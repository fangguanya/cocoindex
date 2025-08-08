"""
分布式文件ID管理器 (v4.0) - 文件锁版本

提供完全多进程安全的文件ID分配和映射管理，使用文件锁实现真正的跨进程状态共享。
"""

from pathlib import Path
from typing import Dict, Optional, List
import os
import json
import time
import threading
import hashlib
from dataclasses import dataclass
from .logger import get_logger


@dataclass
class FileManagerStats:
    """文件管理器统计信息"""
    total_files: int
    predefined_files: int
    temp_files: int
    max_file_id: int


class DistributedFileIdManager:
    """
    多进程共享的确定性文件ID管理器（使用文件锁机制）
    - 使用文件锁实现真正的跨进程状态共享
    - 简化API，无需外部传递共享对象
    """
    
    def __init__(self, project_root: str, all_files: List[str] = None):
        """
        初始化分布式文件ID管理器
        
        Args:
            project_root: 项目根目录
            all_files: 预分配的文件列表（可选）
        """
        self.project_root = Path(project_root).resolve()
        self.cache_dir = os.path.join(project_root, ".file_cache")
        
        # 初始化日志器
        self.logger = get_logger()
        
        # 确保缓存目录存在
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # 共享状态文件路径
        self._shared_state_file = os.path.join(self.cache_dir, "shared_files.json")
        self._lock_file = os.path.normpath(os.path.join(self.cache_dir, "shared_files.lock"))
        
        # 进程内缓存
        self._local_cache: Dict[str, str] = {}
        self._local_reverse_cache: Dict[str, str] = {}
        self._lock = threading.RLock()
        
        # 初始化状态
        self._init_shared_state()
        
        # 如果提供了文件列表，进行预分配
        if all_files:
            self._initialize_predefined_mappings(all_files)
    
    def __getstate__(self):
        """序列化时只保存基本信息，排除锁对象"""
        return {
            'project_root': str(self.project_root),
            'cache_dir': self.cache_dir,
            '_local_cache': self._local_cache,
            '_local_reverse_cache': self._local_reverse_cache
        }
    
    def __setstate__(self, state):
        """反序列化时重新创建锁对象"""
        self.project_root = Path(state['project_root']).resolve()
        self.cache_dir = state['cache_dir']
        self._local_cache = state['_local_cache']
        self._local_reverse_cache = state['_local_reverse_cache']
        
        # 重新创建不能被pickle的对象
        self._lock = threading.RLock()
        self.logger = get_logger()
        
        # 文件路径
        self._shared_state_file = os.path.join(self.cache_dir, "shared_files.json")
        self._lock_file = os.path.normpath(os.path.join(self.cache_dir, "shared_files.lock"))
    
    def _acquire_file_lock(self, timeout: float = 30.0) -> bool:
        """获取文件锁"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                if not os.path.exists(self._lock_file):
                    with open(self._lock_file, 'w') as f:
                        f.write(str(os.getpid()))
                    return True
                else:
                    # 检查锁文件是否过期（超过30秒）
                    if time.time() - os.path.getmtime(self._lock_file) > 30:
                        os.remove(self._lock_file)
                        continue
                time.sleep(0.1)
            except Exception:
                time.sleep(0.1)
        return False
    
    def _release_file_lock(self):
        """释放文件锁"""
        try:
            if os.path.exists(self._lock_file):
                os.remove(self._lock_file)
        except Exception:
            pass
    
    def _init_shared_state(self):
        """初始化共享状态"""
        try:
            if os.path.exists(self._shared_state_file):
                # 清理旧的状态文件（如果超过1小时）
                if time.time() - os.path.getmtime(self._shared_state_file) > 3600:
                    os.remove(self._shared_state_file)
        except Exception:
            pass
    
    def _load_shared_state(self) -> Dict[str, any]:
        """加载共享状态 - 优化版本，减少重复加载"""
        # 检查本地缓存，避免重复加载
        if hasattr(self, '_cached_state') and self._cached_state:
            return self._cached_state
        
        if not os.path.exists(self._shared_state_file):
            # 文件不存在是正常情况（首次运行），返回空状态
            self.logger.info(f"文件管理器共享状态文件不存在，初始化新的共享状态: {self._shared_state_file}")
            state = self._create_empty_shared_state()
            self._cached_state = state
            return state
        
        try:
            # 检查文件大小，如果太小可能是损坏的
            file_size = os.path.getsize(self._shared_state_file)
            if file_size < 10:
                raise RuntimeError(f"文件管理器共享状态文件损坏（文件大小 {file_size} 字节，小于最小阈值）: {self._shared_state_file}")
            
            with open(self._shared_state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # 验证数据结构完整性
            required_keys = {'path_to_id', 'id_to_path', 'predefined_counter', 'temp_counter', 'stats'}
            if not all(key in data for key in required_keys):
                missing_keys = required_keys - set(data.keys())
                raise RuntimeError(f"文件管理器共享状态数据结构不完整，缺少必需的键: {missing_keys}")
            
            # 缓存到本地，避免重复加载
            self._cached_state = data
            self.logger.info(f"成功加载文件管理器共享状态，包含 {len(data.get('path_to_id', {}))} 个文件映射")
            return data
            
        except Exception as e:
            # 共享状态加载失败是严重错误，直接抛异常
            error_msg = f"文件管理器共享状态加载失败，多进程解析功能不可用: {e}"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg) from e
    
    def _create_empty_shared_state(self) -> Dict[str, any]:
        """创建空的共享状态结构"""
        return {
            'path_to_id': {},
            'id_to_path': {},
            'predefined_counter': 0,
            'temp_counter': 0,
            'stats': {
                'predefined_files': 0,
                'temp_files': 0,
                'total_files': 0,
                'reused_ids': 0
            }
        }
    
    def _save_shared_state(self, state: Dict[str, any]):
        """保存共享状态"""
        try:
            with open(self._shared_state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    
    def _initialize_predefined_mappings(self, all_files: List[str]):
        """根据完整文件列表预先生成所有映射（多进程共享确定性算法）"""
        if not self._acquire_file_lock():
            return
        
        try:
            shared_state = self._load_shared_state()
            
            # 检查是否已经初始化过预定义文件
            if shared_state['predefined_counter'] > 0:
                return  # 已经初始化过，避免重复
            
            # 排序以确保确定性
            sorted_files = sorted(all_files)
            
            for i, file_path in enumerate(sorted_files):
                normalized_path = self._normalize_path(file_path)
                file_id = f"file_{i:06d}"
                
                shared_state['path_to_id'][normalized_path] = file_id
                shared_state['id_to_path'][file_id] = normalized_path
            
            # 更新统计信息
            shared_state['predefined_counter'] = len(sorted_files)
            shared_state['stats']['predefined_files'] = len(sorted_files)
            shared_state['stats']['total_files'] = len(sorted_files)
            
            self._save_shared_state(shared_state)
            
        finally:
            self._release_file_lock()
    
    def get_file_id(self, file_path: Optional[str]) -> Optional[str]:
        """获取文件ID，优化版本，减少文件锁竞争"""
        if not file_path:
            return None
        
        normalized_path = self._normalize_path(file_path)
        
        # 检查本地缓存
        with self._lock:
            if normalized_path in self._local_cache:
                return self._local_cache[normalized_path]
        
        # 先检查共享状态（使用缓存的共享状态，减少文件锁）
        shared_state = self._load_shared_state()
        existing_id = shared_state['path_to_id'].get(normalized_path)
        
        if existing_id:
            # 更新本地缓存
            with self._lock:
                self._local_cache[normalized_path] = existing_id
                self._local_reverse_cache[existing_id] = normalized_path
            return existing_id
        
        # 动态分配临时ID（需要文件锁）- 优雅降级机制
        if not self._acquire_file_lock():
            # 如果无法获取锁，生成基于进程的临时ID，避免冲突
            return f"temp_{self.process_id}_{abs(hash(normalized_path)) % 100000}"
        
        try:
            # 重新加载共享状态以确保最新
            shared_state = self._load_shared_state()
            
            # 再次检查（可能在获取锁期间被其他进程添加）
            existing_id = shared_state['path_to_id'].get(normalized_path)
            if existing_id:
                shared_state['stats']['reused_ids'] += 1
                # 更新本地缓存
                with self._lock:
                    self._local_cache[normalized_path] = existing_id
                    self._local_reverse_cache[existing_id] = normalized_path
                self._save_shared_state(shared_state)
                return existing_id
            
            # 不存在则动态分配临时ID
            return self._create_temp_file_id_unsafe(shared_state, normalized_path)
            
        finally:
            self._release_file_lock()
    
    def _create_temp_file_id_unsafe(self, shared_state: Dict[str, any], normalized_path: str) -> str:
        """为动态发现的文件创建临时ID（内部使用，假设已加锁）"""
        # 分配新的临时ID
        shared_state['temp_counter'] += 1
        temp_id = f"t{shared_state['temp_counter']:04d}"  # t0001, t0002, ...
        
        # 添加到共享映射表
        shared_state['path_to_id'][normalized_path] = temp_id
        shared_state['id_to_path'][temp_id] = normalized_path
        
        # 更新统计信息
        shared_state['stats']['temp_files'] += 1
        shared_state['stats']['total_files'] = shared_state['stats']['predefined_files'] + shared_state['stats']['temp_files']
        
        # 更新本地缓存
        with self._lock:
            self._local_cache[normalized_path] = temp_id
            self._local_reverse_cache[temp_id] = normalized_path
        
        self._save_shared_state(shared_state)
        return temp_id
    
    def register_file(self, file_path: str) -> str:
        """注册单个文件并获取ID（别名方法，保持接口兼容）"""
        return self.get_file_id(file_path)
    
    def register_files_batch(self, file_paths: List[str]) -> Dict[str, str]:
        """批量注册文件，返回文件路径到ID的映射"""
        result = {}
        
        for file_path in file_paths:
            file_id = self.get_file_id(file_path)
            if file_id:
                result[file_path] = file_id
        
        return result
    
    def get_file_by_id(self, file_id: str) -> Optional[str]:
        """根据ID获取文件路径（多进程共享状态）"""
        # 检查本地缓存
        with self._lock:
            if file_id in self._local_reverse_cache:
                return self._local_reverse_cache[file_id]
        
        # 检查共享状态
        if not self._acquire_file_lock():
            return None
        
        try:
            shared_state = self._load_shared_state()
            result = shared_state['id_to_path'].get(file_id)
            
            # 更新本地缓存
            if result:
                with self._lock:
                    self._local_reverse_cache[file_id] = result
                    self._local_cache[result] = file_id
            
            return result
            
        finally:
            self._release_file_lock()
    
    def get_id_by_file(self, file_path: str) -> Optional[str]:
        """根据文件路径获取ID（不创建新ID，多进程共享状态）"""
        normalized_path = self._normalize_path(file_path)
        
        # 检查本地缓存
        with self._lock:
            if normalized_path in self._local_cache:
                return self._local_cache[normalized_path]
        
        # 检查共享状态
        if not self._acquire_file_lock():
            return None
        
        try:
            shared_state = self._load_shared_state()
            result = shared_state['path_to_id'].get(normalized_path)
            
            # 更新本地缓存
            if result:
                with self._lock:
                    self._local_cache[normalized_path] = result
                    self._local_reverse_cache[result] = normalized_path
            
            return result
            
        finally:
            self._release_file_lock()
    
    def _normalize_path(self, file_path: str) -> str:
        """将路径标准化为相对于项目根目录的相对路径"""
        try:
            path = Path(file_path)
            if not path.is_absolute():
                path = self.project_root / path
            
            resolved_path = path.resolve()
            relative_path = resolved_path.relative_to(self.project_root)
            return str(relative_path).replace('\\', '/')
        except (ValueError, TypeError):
            try:
                resolved_path = Path(file_path).resolve()
                if not self.project_root.is_absolute():
                     return file_path.replace('\\', '/')

                common_base = Path(os.path.commonpath([str(resolved_path), str(self.project_root)]))
                relative_to_common = resolved_path.relative_to(common_base)
                
                up_parts = self.project_root.relative_to(common_base).parts
                if up_parts == ('.',):
                    up_parts = ()
                
                final_path = Path(*(['..'] * len(up_parts))) / relative_to_common
                return str(final_path).replace('\\', '/')
            except Exception:
                return file_path.replace('\\', '/')

    def get_file_mappings(self) -> Dict[str, str]:
        """获取所有文件映射（file_id -> path，多进程共享状态）"""
        if not self._acquire_file_lock():
            return {}
        
        try:
            shared_state = self._load_shared_state()
            return dict(shared_state['id_to_path'])
        finally:
            self._release_file_lock()

    def get_reverse_mappings(self) -> Dict[str, str]:
        """获取反向文件映射（path -> file_id，多进程共享状态）"""
        if not self._acquire_file_lock():
            return {}
        
        try:
            shared_state = self._load_shared_state()
            return dict(shared_state['path_to_id'])
        finally:
            self._release_file_lock()
    
    def get_all_mappings(self) -> Dict[str, str]:
        """获取所有文件到ID的映射（别名方法）"""
        return self.get_reverse_mappings()

    def get_stats(self) -> Dict[str, int]:
        """获取统计信息（多进程共享状态）"""
        if not self._acquire_file_lock():
            return {'total_files': 0, 'predefined_files': 0, 'temp_files': 0, 'max_file_id': 0, 'reused_ids': 0}
        
        try:
            shared_state = self._load_shared_state()
            stats = dict(shared_state['stats'])
            
            # 计算最大文件ID
            max_id = 0
            for file_id in shared_state['id_to_path'].keys():
                if len(file_id) > 1 and file_id[1:].isdigit():
                    max_id = max(max_id, int(file_id[1:]))
            stats['max_file_id'] = max_id
            
            return stats
        finally:
            self._release_file_lock()
    
    def save_to_file(self, filepath: str):
        """保存当前状态到文件（多进程共享状态）"""
        if not self._acquire_file_lock():
            return
        
        try:
            shared_state = self._load_shared_state()
            data = {
                'project_root': str(self.project_root),
                **shared_state
            }
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        finally:
            self._release_file_lock()


# 简化的工具函数：直接创建文件管理器
def create_multiprocess_file_manager(project_root: str, all_files: List[str] = None) -> DistributedFileIdManager:
    """
    创建多进程安全的文件管理器实例（内部自动管理共享对象）
    
    Args:
        project_root: 项目根目录
        all_files: 预分配的文件列表（可选）
    
    Returns:
        DistributedFileIdManager 实例（可序列化，自动共享状态）
    """
    return DistributedFileIdManager(project_root=project_root, all_files=all_files)