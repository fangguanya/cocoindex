"""
统一的共享类缓存管理器 - 处理所有类型（泛型+普通）的多进程安全解析
使用文件锁机制实现真正的跨进程状态共享

解决问题：
1. 当前只有泛型类型有专门的共享缓存，普通类型（如基类B）在多进程下可能被重复解析
2. A继承B，C也继承B的情况下，B可能被多个进程重复解析
3. 需要统一的缓存架构来处理所有类型

统一解决方案：
- 扩展SharedTemplateCache为通用的SharedClassCache
- 支持普通类型和泛型类型
- 基于USR的唯一标识
- 使用文件锁实现真正的多进程安全

作者: AI Assistant
日期: 2025年
"""

import os
import time
import hashlib
import threading
import pickle
from pathlib import Path
from typing import Dict, Set, List, Optional, Any, Tuple
from dataclasses import dataclass

from .logger import get_logger


@dataclass
class ClassResolutionInfo:
    """类解析信息（统一处理泛型和普通类型）"""
    class_name: str
    class_usr: str
    class_hash: str
    resolution_status: str  # 'pending', 'resolving', 'resolved', 'failed'
    process_id: int
    thread_id: int
    timestamp: float
    resolved_class_data: Dict[str, Any]
    dependencies: Set[str]
    parent_classes: Set[str]
    child_classes: Set[str]
    is_template: bool
    template_specializations: Set[str]
    inheritance_processed: bool


class SharedClassCache:
    """统一的多进程共享类缓存管理器（使用文件锁机制）"""
    
    def __init__(self, project_root: str, cache_dir: Optional[str] = None):
        self.logger = get_logger()
        self.project_root = project_root
        self.cache_dir = cache_dir or os.path.normpath(os.path.join(project_root, ".class_cache"))
        self.process_id = os.getpid()
        self.thread_id = threading.get_ident()
        
        # 确保缓存目录存在
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # 移除本地缓存，只使用全局共享存储
        # self._local_cache: Dict[str, ClassResolutionInfo] = {}
        self._lock = threading.RLock()
        
        # 添加循环检测机制
        self._processing_stack = set()  # 当前正在处理的类型
        
        # 共享状态文件路径
        self._shared_state_file = os.path.normpath(os.path.join(self.cache_dir, "shared_classes.pkl"))
        self._lock_file = os.path.normpath(os.path.join(self.cache_dir, "shared_classes.lock"))
        
        # 初始化共享状态
        self._init_shared_state()
    

    def _init_shared_state(self):
        """初始化共享状态"""
        try:
            if os.path.exists(self._shared_state_file):
                # 清理旧的状态文件（如果超过1小时）
                if time.time() - os.path.getmtime(self._shared_state_file) > 3600:
                    os.remove(self._shared_state_file)
                    self.logger.info("清理了过期的类缓存状态文件")
        except Exception as e:
            self.logger.warning(f"初始化类缓存共享状态时出错: {e}")
    
    def _acquire_file_lock(self, timeout: float = 2.0) -> bool:
        """获取文件锁 - 快速失败机制，避免长时间阻塞"""
        start_time = time.time()
        max_attempts = 20  # 最多尝试20次
        attempt = 0
        
        while time.time() - start_time < timeout and attempt < max_attempts:
            attempt += 1
            try:
                # 原子性创建锁文件
                if not os.path.exists(self._lock_file):
                    try:
                        # 使用排他模式创建文件，如果文件已存在会失败
                        with open(self._lock_file, 'x') as f:
                            f.write(f"{self.process_id}:{self.thread_id}:{time.time()}")
                        return True
                    except FileExistsError:
                        # 文件已存在，继续下一轮尝试
                        pass
                else:
                    # 检查锁文件是否过期（超过5秒就认为死锁）
                    try:
                        lock_age = time.time() - os.path.getmtime(self._lock_file)
                        if lock_age > 5.0:
                            try:
                                os.remove(self._lock_file)
                                self.logger.debug(f"清理了过期的锁文件 (age: {lock_age:.1f}s)")
                                continue  # 立即重试
                            except (OSError, FileNotFoundError):
                                pass
                    except (OSError, FileNotFoundError):
                        # 文件可能已被其他进程删除，继续
                        continue
                        
                    # 检查是否是同一个进程持有锁
                    try:
                        with open(self._lock_file, 'r') as f:
                            lock_info = f.read().strip()
                            parts = lock_info.split(':')
                            if len(parts) >= 2 and parts[0] == str(self.process_id) and parts[1] == str(self.thread_id):
                                return True  # 已经持有锁
                    except (OSError, FileNotFoundError):
                        # 文件可能被删除，继续重试
                        continue
                        
                # 指数退避等待策略
                wait_time = min(0.001 * (2 ** min(attempt - 1, 6)), 0.1)  # 最多等待100ms
                time.sleep(wait_time)
                
            except Exception as e:
                self.logger.debug(f"获取锁时出错 (attempt {attempt}): {e}")
                time.sleep(0.001)
        
        self.logger.debug(f"获取文件锁失败 ({timeout}s, {attempt} attempts)")
        return False
    
    def _release_file_lock(self):
        """释放文件锁"""
        try:
            if os.path.exists(self._lock_file):
                os.remove(self._lock_file)
        except Exception:
            pass
    
    def _load_shared_state(self) -> Dict[str, Any]:
        """加载共享状态 - 优化版本，减少重复加载"""
        # 检查本地缓存，避免重复加载
        if hasattr(self, '_cached_state') and self._cached_state:
            return self._cached_state
        
        if not os.path.exists(self._shared_state_file):
            # 文件不存在是正常情况（首次运行），返回空状态
            self.logger.info(f"共享状态文件不存在，初始化新的共享状态: {self._shared_state_file}")
            state = self._create_empty_shared_state()
            self._cached_state = state
            return state
        
        try:
            # 检查文件大小，如果太小可能是损坏的
            file_size = os.path.getsize(self._shared_state_file)
            if file_size < 10:
                self.logger.warning(f"共享状态文件损坏（文件大小 {file_size} 字节），删除并重新初始化")
                os.remove(self._shared_state_file)
                state = self._create_empty_shared_state()
                self._cached_state = state
                return state
            
            with open(self._shared_state_file, 'rb') as f:
                data = pickle.load(f)
                
            # 验证数据结构完整性
            required_keys = {'class_resolution_status', 'resolved_classes', 'processing_lock_map', 
                           'usr_to_hash_map', 'name_to_hash_map', 'parent_child_mapping', 'child_parent_mapping'}
            if not all(key in data for key in required_keys):
                missing_keys = required_keys - set(data.keys())
                self.logger.warning(f"共享状态数据结构不完整，缺少必需的键: {missing_keys}，重新初始化")
                os.remove(self._shared_state_file)
                state = self._create_empty_shared_state()
                self._cached_state = state
                return state
            
            # 缓存到本地，避免重复加载
            self._cached_state = data
            self.logger.info(f"成功加载共享状态，包含 {len(data.get('resolved_classes', {}))} 个已解析类")
            return data
            
        except (EOFError, pickle.UnpicklingError, RuntimeError) as e:
            # pickle文件损坏，删除并重新初始化
            self.logger.warning(f"共享状态文件损坏，删除并重新初始化: {e}")
            try:
                if os.path.exists(self._shared_state_file):
                    os.remove(self._shared_state_file)
            except Exception as cleanup_error:
                self.logger.warning(f"清理损坏文件失败: {cleanup_error}")
            
            state = self._create_empty_shared_state()
            self._cached_state = state
            return state
            
        except Exception as e:
            # 其他未知错误，记录但继续使用空状态
            self.logger.error(f"加载共享状态时发生未知错误: {e}")
            state = self._create_empty_shared_state()
            self._cached_state = state
            return state
    
    def _create_empty_shared_state(self) -> Dict[str, Any]:
        """创建空的共享状态结构"""
        return {
            'class_resolution_status': {},  # class_hash -> status
            'resolved_classes': {},         # class_hash -> class_data
            'processing_lock_map': {},      # class_hash -> (process_id, thread_id, timestamp)
            'usr_to_hash_map': {},          # usr -> class_hash
            'name_to_hash_map': {},         # qualified_name -> set of class_hashes
            'parent_child_mapping': {},     # parent_hash -> list of child_hashes
            'child_parent_mapping': {},     # child_hash -> list of parent_hashes
            'template_specializations': {}, # base_template_hash -> set of specialization_hashes
            'template_dependencies': {},    # template_hash -> set of dependent_hashes
            'cache_stats': {
                'total_requests': 0,
                'cache_hits': 0,
                'cache_misses': 0,
                'duplicate_resolutions_prevented': 0,
                'concurrent_resolution_conflicts': 0,
                'successful_resolutions': 0,
                'failed_resolutions': 0,
                'template_resolutions': 0,
                'normal_class_resolutions': 0,
                'inheritance_conflicts_resolved': 0
            },
            'active_processes': {},         # process_id -> timestamp
            'cleanup_timestamp': time.time()
        }
    
    def _save_shared_state(self, state: Dict[str, Any]):
        """保存共享状态"""
        try:
            with open(self._shared_state_file, 'wb') as f:
                pickle.dump(state, f)
        except Exception as e:
            self.logger.warning(f"保存类缓存共享状态失败: {e}")
    
    def _generate_class_hash(self, usr: str, qualified_name: str = "") -> str:
        """生成类的唯一哈希值（基于USR，因为USR是全局唯一的）"""
        # 主要基于USR，但也考虑qualified_name作为备选
        primary_key = usr if usr else qualified_name
        if not primary_key:
            return ""
        
        # 规范化键值
        normalized_key = primary_key.strip().replace(' ', '').replace('\t', '')
        return hashlib.md5(normalized_key.encode('utf-8')).hexdigest()
    
    def is_class_resolved(self, usr: str, qualified_name: str = "") -> bool:
        """检查类是否已解析（优化版本，使用本地缓存）"""
        class_hash = self._generate_class_hash(usr, qualified_name)
        if not class_hash:
            return False
        
        # 循环检测：如果正在处理这个类型，直接返回True避免无限循环
        if class_hash in self._processing_stack:
            self.logger.debug(f"检测到循环依赖，返回已解析: {qualified_name}")
            return True
        
        # 先检查本地缓存
        if hasattr(self, '_local_resolved_cache') and class_hash in self._local_resolved_cache:
            return self._local_resolved_cache[class_hash]
        
        #self.logger.debug(f"检查类解析状态: {qualified_name} (hash: {class_hash})")
        
        # 检查共享状态 - 优雅降级机制
        if not self._acquire_file_lock():
            self.logger.debug(f"无法获取文件锁，跳过共享缓存检查: {qualified_name}")
            # 优雅降级：无法获取锁时，假设未解析，避免阻塞
            return False
        
        try:
            shared_state = self._load_shared_state()
            
            # 更新统计
            shared_state['cache_stats']['total_requests'] += 1
            
            status = shared_state['class_resolution_status'].get(class_hash)
            resolved = False
            
            if status == 'resolved':
                shared_state['cache_stats']['cache_hits'] += 1
                self.logger.debug("✓ 共享缓存命中")
                resolved = True
            elif status in ['pending', 'resolving']:
                shared_state['cache_stats']['duplicate_resolutions_prevented'] += 1
                self.logger.debug("✓ 正在被其他进程解析")
                resolved = True
            else:
                shared_state['cache_stats']['cache_misses'] += 1
            
            # 缓存到本地
            if not hasattr(self, '_local_resolved_cache'):
                self._local_resolved_cache = {}
            self._local_resolved_cache[class_hash] = resolved
            
            self._save_shared_state(shared_state)
            return resolved
            
        finally:
            self._release_file_lock()
    
    def is_class_being_resolved(self, usr: str, qualified_name: str = "") -> bool:
        """检查类是否正在被其他进程解析（只使用全局共享存储）"""
        class_hash = self._generate_class_hash(usr, qualified_name)
        if not class_hash:
            return False
        
        #self.logger.debug(f"检查类解析状态: {qualified_name} (hash: {class_hash})")
        
        # 检查共享状态 - 优雅降级机制
        if not self._acquire_file_lock():
            self.logger.debug(f"无法获取文件锁，跳过解析状态检查: {qualified_name}")
            # 优雅降级：无法获取锁时，假设未在解析
            return False
        
        try:
            shared_state = self._load_shared_state()
            
            status = shared_state['class_resolution_status'].get(class_hash)
            if status in ['pending', 'resolving']:
                self.logger.debug(f"共享缓存显示正在解析: {qualified_name}, 状态: {status}")
                return True
            
            return False
            
        finally:
            self._release_file_lock()
    
    def try_acquire_class_resolution_lock(self, usr: str, qualified_name: str = "") -> bool:
        """尝试获取类解析锁（防止重复解析，带循环检测）"""
        class_hash = self._generate_class_hash(usr, qualified_name)
        if not class_hash:
            return False
        
        # 循环检测：如果正在处理这个类型，拒绝获取锁
        if class_hash in self._processing_stack:
            self.logger.debug(f"检测到循环依赖，拒绝获取解析锁: {qualified_name}")
            return False
        
        # 标记开始处理这个类型
        self._processing_stack.add(class_hash)
        
        current_time = time.time()
        
        if not self._acquire_file_lock():
            self.logger.debug(f"无法获取文件锁，拒绝解析: {qualified_name}")
            # 优雅降级：从处理栈中移除，避免后续问题
            self._processing_stack.discard(class_hash)
            return False
        
        try:
            shared_state = self._load_shared_state()
            
            # 检查是否已被锁定
            lock_info = shared_state['processing_lock_map'].get(class_hash)
            if lock_info:
                lock_process_id, lock_thread_id, lock_timestamp = lock_info
                
                # 检查锁是否超时（避免死锁）
                if current_time - lock_timestamp > 300:  # 5分钟超时
                    self.logger.warning(f"类解析锁超时，强制释放: {usr} ({qualified_name})")
                    self._force_release_lock_unsafe(shared_state, class_hash)
                else:
                    # 锁仍有效，无法获取
                    if lock_process_id != self.process_id or lock_thread_id != self.thread_id:
                        shared_state['cache_stats']['concurrent_resolution_conflicts'] += 1
                        self.logger.debug(f"锁被其他进程持有: {lock_process_id}/{lock_thread_id}")
                        self._save_shared_state(shared_state)
                        return False
            
            # 设置锁和USR映射
            shared_state['processing_lock_map'][class_hash] = (
                self.process_id, self.thread_id, current_time
            )
            shared_state['class_resolution_status'][class_hash] = 'resolving'
            
            # 建立USR映射
            if usr:
                shared_state['usr_to_hash_map'][usr] = class_hash
            if qualified_name:
                if qualified_name not in shared_state['name_to_hash_map']:
                    shared_state['name_to_hash_map'][qualified_name] = set()
                shared_state['name_to_hash_map'][qualified_name].add(class_hash)
            
            self._save_shared_state(shared_state)
            #self.logger.debug(f"获取类解析锁成功: {usr} ({qualified_name})")
            return True
            
        finally:
            self._release_file_lock()
    
    def mark_class_resolved(self, usr: str, qualified_name: str, class_data: Dict[str, Any],
                          parent_classes: Set[str] = None, child_classes: Set[str] = None,
                          is_template: bool = False, template_specializations: Set[str] = None,
                          dependencies: Set[str] = None):
        """标记类为已解析状态（清理循环检测）"""
        class_hash = self._generate_class_hash(usr, qualified_name)
        if not class_hash:
            return
        
        # 清理循环检测标记
        self._processing_stack.discard(class_hash)
        
        current_time = time.time()
        
        resolution_info = ClassResolutionInfo(
            class_name=qualified_name,
            class_usr=usr,
            class_hash=class_hash,
            resolution_status='resolved',
            process_id=self.process_id,
            thread_id=self.thread_id,
            timestamp=current_time,
            resolved_class_data=class_data,
            dependencies=dependencies or set(),
            parent_classes=parent_classes or set(),
            child_classes=child_classes or set(),
            is_template=is_template,
            template_specializations=template_specializations or set(),
            inheritance_processed=bool(parent_classes or child_classes)
        )
        
        # 更新共享缓存
        if not self._acquire_file_lock():
            self.logger.debug(f"无法获取文件锁，跳过共享缓存更新: {qualified_name}")
            # 优雅降级：清理本地状态
            self._processing_stack.discard(class_hash)
            return
        
        try:
            shared_state = self._load_shared_state()
            
            # 准备共享数据
            shared_data = {
                'class_name': qualified_name,
                'class_usr': usr,
                'class_data': class_data,
                'parent_classes': list(parent_classes or []),
                'child_classes': list(child_classes or []),
                'is_template': is_template,
                'template_specializations': list(template_specializations or []),
                'dependencies': list(dependencies or []),
                'timestamp': current_time,
                'process_id': self.process_id,
                'inheritance_processed': bool(parent_classes or child_classes)
            }
            
            # 基本状态更新
            shared_state['class_resolution_status'][class_hash] = 'resolved'
            shared_state['resolved_classes'][class_hash] = shared_data
            
            # 释放处理锁
            if class_hash in shared_state['processing_lock_map']:
                del shared_state['processing_lock_map'][class_hash]
            
            # 更新继承关系
            self._update_inheritance_relations_unsafe(shared_state, class_hash, parent_classes)
            
            # 更新模板关系
            self._update_template_relations_unsafe(shared_state, class_hash, template_specializations, is_template)
            
            # 更新统计
            shared_state['cache_stats']['successful_resolutions'] += 1
            if is_template:
                shared_state['cache_stats']['template_resolutions'] += 1
            else:
                shared_state['cache_stats']['normal_class_resolutions'] += 1
                
            self._save_shared_state(shared_state)
            
        finally:
            self._release_file_lock()
        
        #self.logger.debug(f"类已标记为解析完成: {usr} ({qualified_name})")
    
    def _update_inheritance_relations_unsafe(self, shared_state: Dict[str, Any], class_hash: str, parent_classes):
        """更新继承关系（假设已获取锁）"""
        if not parent_classes:
            return
            
        for parent_usr in parent_classes:
            parent_hash = self._generate_class_hash(parent_usr)
            if parent_hash:
                # 更新父->子映射
                if parent_hash not in shared_state['parent_child_mapping']:
                    shared_state['parent_child_mapping'][parent_hash] = []
                
                current_children = shared_state['parent_child_mapping'][parent_hash]
                if class_hash not in current_children:
                    current_children.append(class_hash)
                
                # 更新子->父映射
                if class_hash not in shared_state['child_parent_mapping']:
                    shared_state['child_parent_mapping'][class_hash] = []
                
                current_parents = shared_state['child_parent_mapping'][class_hash]
                if parent_hash not in current_parents:
                    current_parents.append(parent_hash)
    
    def _update_template_relations_unsafe(self, shared_state: Dict[str, Any], class_hash: str, template_specializations, is_template: bool):
        """更新模板关系（假设已获取锁）"""
        if not is_template or not template_specializations:
            return
            
        for spec_usr in template_specializations:
            spec_hash = self._generate_class_hash(spec_usr)
            if spec_hash:
                if class_hash not in shared_state['template_specializations']:
                    shared_state['template_specializations'][class_hash] = set()
                shared_state['template_specializations'][class_hash].add(spec_hash)
    
    def mark_class_failed(self, usr: str, qualified_name: str, error_message: str = ""):
        """标记类解析失败（清理循环检测）"""
        class_hash = self._generate_class_hash(usr, qualified_name)
        if not class_hash:
            return
        
        # 清理循环检测标记
        self._processing_stack.discard(class_hash)
        
        if not self._acquire_file_lock():
            self.logger.debug(f"无法获取文件锁，跳过标记失败: {qualified_name}")
            # 优雅降级：清理本地状态
            self._processing_stack.discard(class_hash)
            return
        
        try:
            shared_state = self._load_shared_state()
            shared_state['class_resolution_status'][class_hash] = 'failed'
            
            # 释放锁
            if class_hash in shared_state['processing_lock_map']:
                del shared_state['processing_lock_map'][class_hash]
            
            shared_state['cache_stats']['failed_resolutions'] += 1
            self._save_shared_state(shared_state)
            
        finally:
            self._release_file_lock()
        
        self.logger.debug(f"类标记为解析失败: {usr} ({qualified_name}) - {error_message}")
    
    def get_resolved_class(self, usr: str, qualified_name: str = "") -> Optional[Dict[str, Any]]:
        """获取已解析的类信息"""
        class_hash = self._generate_class_hash(usr, qualified_name)
        if not class_hash:
            return None
        
        # 检查共享缓存
        if not self._acquire_file_lock():
            return None
        
        try:
            shared_state = self._load_shared_state()
            if class_hash in shared_state['resolved_classes']:
                result = shared_state['resolved_classes'][class_hash]['class_data']
                return result
        finally:
            self._release_file_lock()
        
        return None
    
    def update_inheritance_mapping(self, parent_usr: str, parent_name: str, 
                                 child_usr: str, child_name: str):
        """更新继承关系映射"""
        try:
            parent_hash = self._generate_class_hash(parent_usr, parent_name)
            child_hash = self._generate_class_hash(child_usr, child_name)
            
            if not parent_hash or not child_hash:
                return
            
            if not self._acquire_file_lock():
                self.logger.debug(f"无法获取文件锁，跳过继承关系更新: {child_name} -> {parent_name}")
                # 优雅降级：允许程序继续，但不更新共享状态
                return
            
            try:
                shared_state = self._load_shared_state()
                
                # 更新父->子映射
                if parent_hash not in shared_state['parent_child_mapping']:
                    shared_state['parent_child_mapping'][parent_hash] = []
                
                if child_hash not in shared_state['parent_child_mapping'][parent_hash]:
                    shared_state['parent_child_mapping'][parent_hash].append(child_hash)
                
                # 更新子->父映射
                if child_hash not in shared_state['child_parent_mapping']:
                    shared_state['child_parent_mapping'][child_hash] = []
                
                if parent_hash not in shared_state['child_parent_mapping'][child_hash]:
                    shared_state['child_parent_mapping'][child_hash].append(parent_hash)
                
                # 更新统计
                shared_state['cache_stats']['inheritance_relationships_tracked'] = shared_state['cache_stats'].get('inheritance_relationships_tracked', 0) + 1
                
                self._save_shared_state(shared_state)
                self.logger.debug(f"更新继承关系: {child_name} -> {parent_name}")
                
            finally:
                self._release_file_lock()
                
        except Exception as e:
            self.logger.debug(f"更新继承关系映射时出错: {e}")
    
    def get_cache_statistics(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        stats = {
            'shared_cache_size': 0,
            'active_processes': 0,
            'processing_locks': 0,
            'inheritance_relationships': 0,
            'template_relationships': 0
        }
        
        # 获取共享统计
        if self._acquire_file_lock():
            try:
                shared_state = self._load_shared_state()
                stats.update(shared_state['cache_stats'])
                stats['shared_cache_size'] = len(shared_state['resolved_classes'])
                stats['active_processes'] = len(shared_state['active_processes'])
                stats['processing_locks'] = len(shared_state['processing_lock_map'])
                stats['inheritance_relationships'] = len(shared_state['parent_child_mapping'])
                stats['template_relationships'] = len(shared_state['template_specializations'])
            finally:
                self._release_file_lock()
        
        return stats
    
    def _force_release_lock_unsafe(self, shared_state: Dict[str, Any], class_hash: str):
        """强制释放锁（假设已获取文件锁）"""
        try:
            if class_hash in shared_state['processing_lock_map']:
                del shared_state['processing_lock_map'][class_hash]
            
            status = shared_state['class_resolution_status'].get(class_hash)
            if status == 'resolving':
                shared_state['class_resolution_status'][class_hash] = 'failed'
                
        except Exception as e:
            self.logger.debug(f"强制释放锁失败: {e}")
    
    def cleanup_expired_entries(self, max_age_hours: float = 24.0):
        """清理过期的条目"""
        if not self._acquire_file_lock():
            return
        
        try:
            shared_state = self._load_shared_state()
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600
            
            expired_hashes = []
            for class_hash, class_data in shared_state['resolved_classes'].items():
                if current_time - class_data.get('timestamp', 0) > max_age_seconds:
                    expired_hashes.append(class_hash)
            
            for class_hash in expired_hashes:
                # 清理各种映射
                shared_state['resolved_classes'].pop(class_hash, None)
                shared_state['class_resolution_status'].pop(class_hash, None)
                shared_state['processing_lock_map'].pop(class_hash, None)
                
                # 清理继承关系
                shared_state['parent_child_mapping'].pop(class_hash, None)
                shared_state['child_parent_mapping'].pop(class_hash, None)
                shared_state['template_specializations'].pop(class_hash, None)
            
            if expired_hashes:
                self._save_shared_state(shared_state)
                self.logger.info(f"清理了 {len(expired_hashes)} 个过期的类缓存条目")
                
        finally:
            self._release_file_lock()


# 全局共享缓存管理器实例
_global_class_cache: Optional[SharedClassCache] = None


def get_shared_class_cache(project_root: str) -> SharedClassCache:
    """获取全局共享类缓存管理器"""
    global _global_class_cache
    
    if _global_class_cache is None:
        _global_class_cache = SharedClassCache(project_root)
    
    return _global_class_cache


def init_shared_class_cache(project_root: str):
    """初始化全局共享类缓存管理器"""
    global _global_class_cache
    _global_class_cache = SharedClassCache(project_root)
    return _global_class_cache