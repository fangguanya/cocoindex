#!/usr/bin/env python3
"""
MMap共享缓存适配器 - 将基于mmap的共享缓存处理系统集成到现有项目
替换现有的多进程共享缓存机制，提供更高的性能和更好的并发支持

主要功能：
1. 适配现有的SharedClassCache接口
2. 适配现有的SharedHeaderManager接口
3. 提供统一的mmap缓存管理
4. 保持API兼容性

作者: AI Assistant
日期: 2025年
"""

import os
import time
import hashlib
import threading
import pickle
import json
from pathlib import Path
from typing import Dict, Set, List, Optional, Any, Tuple, Union
from dataclasses import dataclass, asdict
from enum import Enum

# 导入我们的mmap缓存系统
from .mmap_cache.unified_cache_manager import (
    init_global_cache_manager, get_global_cache_manager, CacheType, CacheEventType
)
from .mmap_cache.high_concurrency_mmap_manager import (
    init_global_mmap_manager, get_global_mmap_manager, MmapFileType
)
from .mmap_cache.shard_manager import (
    init_global_shard_manager, get_global_shard_manager
)
from .mmap_cache.concurrent_lock_manager import (
    init_global_lock_manager, get_global_lock_manager
)
from .mmap_cache.unified_monitor import (
    init_global_monitor, get_global_monitor
)

from .logger import get_logger


class CacheDataType(Enum):
    """缓存数据类型"""
    CLASS_RESOLUTION = "class_resolution"
    HEADER_PROCESSING = "header_processing"
    FILE_METADATA = "file_metadata"
    TEMPLATE_SPECIALIZATION = "template_specialization"
    INHERITANCE_RELATION = "inheritance_relation"


@dataclass
class ClassResolutionInfo:
    """类解析信息（兼容现有接口）"""
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


@dataclass
class HeaderProcessingInfo:
    """头文件处理信息（兼容现有接口）"""
    file_path: str
    compile_args: List[str]
    directory: str
    process_id: int
    timestamp: float
    hash_value: str
    is_processed: bool


class MMapSharedCacheAdapter:
    """MMap共享缓存适配器 - 统一管理所有类型的缓存"""
    
    def __init__(self, project_root: str):
        self.logger = get_logger()
        self.project_root = Path(project_root)
        self.process_id = os.getpid()
        self.thread_id = threading.get_ident()
        
        # 初始化mmap缓存系统
        self._init_mmap_system()
        
        # 本地缓存（用于快速访问）
        self._local_cache: Dict[str, Any] = {}
        self._lock = threading.RLock()
        
        # 缓存统计
        self._stats = {
            'hits': 0,
            'misses': 0,
            'writes': 0,
            'errors': 0
        }
    
    def _init_mmap_system(self):
        """初始化mmap缓存系统"""
        try:
            # 初始化所有管理器
            self.cache_manager = init_global_cache_manager(self.project_root)
            self.mmap_manager = init_global_mmap_manager(self.project_root)
            # 强制使用负载均衡路由策略
            from .mmap_cache.shard_manager import ShardRoutingStrategy
            self.shard_manager = init_global_shard_manager(self.project_root, 
                                                         routing_strategy=ShardRoutingStrategy.LOAD_BALANCED)
            self.lock_manager = init_global_lock_manager(self.project_root)
            
            # 初始化监控系统
            init_global_monitor(self.cache_manager, self.mmap_manager, 
                              self.shard_manager, self.lock_manager)
            
            self.logger.info("MMap共享缓存系统初始化完成")
            
        except Exception as e:
            self.logger.error(f"MMap缓存系统初始化失败: {e}")
            raise
    
    def _get_cache_key(self, data_type: CacheDataType, key: str) -> str:
        """生成缓存键"""
        return f"{data_type.value}:{key}"
    
    def _get_shard_id(self, key: str) -> int:
        """获取分片ID"""
        return self.shard_manager.get_shard_id(key)
    
    def _acquire_lock(self, key: str, lock_type: str = "write", timeout: float = 5.0) -> Optional[str]:
        """获取锁"""
        try:
            shard_id = self._get_shard_id(key)
            
            # 转换锁类型
            from .mmap_cache.concurrent_lock_manager import LockType
            if lock_type == "read":
                lock_type_enum = LockType.READ
            elif lock_type == "write":
                lock_type_enum = LockType.WRITE
            elif lock_type == "exclusive":
                lock_type_enum = LockType.EXCLUSIVE
            else:
                lock_type_enum = LockType.WRITE  # 默认使用写锁
            
            # 添加重试机制
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    lock_id = self.lock_manager.acquire_lock(shard_id, lock_type_enum, timeout)
                    if lock_id:
                        return lock_id
                except Exception as e:
                    self.logger.debug(f"锁获取尝试 {attempt + 1} 失败: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(0.1)  # 短暂等待后重试
            
            self.logger.error(f"获取锁失败，已重试 {max_retries} 次")
            return None
            
        except Exception as e:
            self.logger.error(f"获取锁失败: {e}")
            return None
    
    def _release_lock(self, lock_id: str):
        """释放锁"""
        try:
            self.lock_manager.release_lock(lock_id)
        except Exception as e:
            self.logger.debug(f"释放锁失败: {e}")
    
    def _serialize_data(self, data: Any) -> bytes:
        """序列化数据"""
        try:
            if isinstance(data, (dict, list, str, int, float, bool)):
                return json.dumps(data, ensure_ascii=False).encode('utf-8')
            else:
                return pickle.dumps(data)
        except Exception as e:
            self.logger.error(f"序列化数据失败: {e}")
            return pickle.dumps(data)
    
    def _deserialize_data(self, data: bytes) -> Any:
        """反序列化数据"""
        try:
            # 尝试JSON反序列化
            text = data.decode('utf-8')
            return json.loads(text)
        except:
            try:
                # 尝试pickle反序列化
                return pickle.loads(data)
            except Exception as e:
                self.logger.error(f"反序列化数据失败: {e}")
                return None
    
    def get(self, data_type: CacheDataType, key: str) -> Optional[Any]:
        """获取缓存数据"""
        cache_key = self._get_cache_key(data_type, key)
        
        # 先检查本地缓存
        with self._lock:
            if cache_key in self._local_cache:
                self._stats['hits'] += 1
                return self._local_cache[cache_key]
        
        try:
            # 从mmap缓存获取
            shard_id = self._get_shard_id(cache_key)
            data = self.mmap_manager.read_data(MmapFileType.CLASS_CACHE, shard_id, cache_key)
            
            if data:
                # 反序列化数据
                result = self._deserialize_data(data)
                
                # 更新本地缓存
                with self._lock:
                    self._local_cache[cache_key] = result
                
                self._stats['hits'] += 1
                return result
            else:
                self._stats['misses'] += 1
                return None
                
        except Exception as e:
            self.logger.error(f"获取缓存数据失败: {e}")
            self._stats['errors'] += 1
            return None
    
    def set(self, data_type: CacheDataType, key: str, value: Any, 
            lock_type: str = "write", timeout: float = 5.0) -> bool:
        """设置缓存数据"""
        cache_key = self._get_cache_key(data_type, key)
        lock_id = None
        
        try:
            # 获取锁
            lock_id = self._acquire_lock(cache_key, lock_type, timeout)
            if not lock_id:
                self.logger.debug(f"无法获取锁: {cache_key}")
                # 优雅降级：允许操作继续，但不使用缓存
                return True
            
            # 序列化数据
            serialized_data = self._serialize_data(value)
            if not serialized_data:
                self.logger.error(f"数据序列化失败: {cache_key}")
                return False
            
            # 写入mmap缓存
            shard_id = self._get_shard_id(cache_key)
            success = self.mmap_manager.write_data(MmapFileType.CLASS_CACHE, shard_id, cache_key, serialized_data)
            
            if success:
                # 更新本地缓存
                with self._lock:
                    self._local_cache[cache_key] = value
                
                self._stats['writes'] += 1
                return True
            else:
                self.logger.error(f"写入mmap缓存失败: {cache_key}")
                return False
                
        except Exception as e:
            self.logger.error(f"设置缓存数据失败: {cache_key} - {e}")
            self._stats['errors'] += 1
            return False
        finally:
            # 确保释放锁
            if lock_id:
                try:
                    self._release_lock(lock_id)
                except Exception as e:
                    self.logger.error(f"释放锁失败: {lock_id} - {e}")
    
    def exists(self, data_type: CacheDataType, key: str) -> bool:
        """检查缓存数据是否存在"""
        cache_key = self._get_cache_key(data_type, key)
        
        # 先检查本地缓存
        with self._lock:
            if cache_key in self._local_cache:
                return True
        
        try:
            # 检查mmap缓存
            shard_id = self._get_shard_id(cache_key)
            data = self.mmap_manager.read_data(MmapFileType.CLASS_CACHE, shard_id, cache_key)
            return data is not None
            
        except Exception as e:
            self.logger.error(f"检查缓存数据失败: {e}")
            return False
    
    def delete(self, data_type: CacheDataType, key: str) -> bool:
        """删除缓存数据"""
        cache_key = self._get_cache_key(data_type, key)
        lock_id = None
        
        try:
            # 获取锁
            lock_id = self._acquire_lock(cache_key, "write", 5.0)
            if not lock_id:
                self.logger.debug(f"无法获取删除锁: {cache_key}")
                # 优雅降级：允许操作继续
                return True
            
            # 从mmap缓存删除
            shard_id = self._get_shard_id(cache_key)
            success = self.mmap_manager.delete_data(MmapFileType.CLASS_CACHE, shard_id, cache_key)
            
            if success:
                # 从本地缓存删除
                with self._lock:
                    self._local_cache.pop(cache_key, None)
                
                return True
            else:
                self.logger.error(f"从mmap缓存删除失败: {cache_key}")
                return False
                
        except Exception as e:
            self.logger.error(f"删除缓存数据失败: {cache_key} - {e}")
            return False
        finally:
            # 确保释放锁
            if lock_id:
                try:
                    self._release_lock(lock_id)
                except Exception as e:
                    self.logger.error(f"释放删除锁失败: {lock_id} - {e}")
    
    def clear_local_cache(self):
        """清理本地缓存"""
        with self._lock:
            self._local_cache.clear()
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        mmap_stats = self.mmap_manager.get_statistics()
        shard_stats = self.shard_manager.get_statistics()
        lock_stats = self.lock_manager.get_statistics()
        
        return {
            'local_cache_size': len(self._local_cache),
            'cache_stats': self._stats.copy(),
            'mmap_stats': mmap_stats,
            'shard_stats': shard_stats,
            'lock_stats': lock_stats
        }


class MMapSharedClassCache:
    """基于MMap的共享类缓存（兼容现有SharedClassCache接口）"""
    
    def __init__(self, project_root: str, cache_dir: Optional[str] = None):
        self.logger = get_logger()
        self.project_root = project_root
        self.cache_dir = cache_dir
        self.process_id = os.getpid()
        self.thread_id = threading.get_ident()
        
        # 初始化mmap适配器
        self._adapter = MMapSharedCacheAdapter(project_root)
        
        # 处理栈（用于循环检测）
        self._processing_stack = set()
    
    def _generate_class_hash(self, usr: str, qualified_name: str = "") -> str:
        """生成类哈希"""
        content = f"{usr}:{qualified_name}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def is_class_resolved(self, usr: str, qualified_name: str = "") -> bool:
        """检查类是否已解析"""
        class_hash = self._generate_class_hash(usr, qualified_name)
        return self._adapter.exists(CacheDataType.CLASS_RESOLUTION, class_hash)
    
    def is_class_being_resolved(self, usr: str, qualified_name: str = "") -> bool:
        """检查类是否正在解析中"""
        class_hash = self._generate_class_hash(usr, qualified_name)
        key = f"resolving:{class_hash}"
        return self._adapter.exists(CacheDataType.CLASS_RESOLUTION, key)
    
    def try_acquire_class_resolution_lock(self, usr: str, qualified_name: str = "") -> bool:
        """尝试获取类解析锁"""
        class_hash = self._generate_class_hash(usr, qualified_name)
        
        # 检查是否已在处理栈中
        if class_hash in self._processing_stack:
            return False
        
        # 检查是否已被其他进程锁定
        resolving_key = f"resolving:{class_hash}"
        if self._adapter.exists(CacheDataType.CLASS_RESOLUTION, resolving_key):
            return False
        
        # 尝试设置解析中状态
        lock_info = {
            'process_id': self.process_id,
            'thread_id': self.thread_id,
            'timestamp': time.time()
        }
        
        success = self._adapter.set(CacheDataType.CLASS_RESOLUTION, resolving_key, lock_info, "write", 1.0)
        if success:
            self._processing_stack.add(class_hash)
            return True
        
        return False
    
    def mark_class_resolved(self, usr: str, qualified_name: str, class_data: Dict[str, Any],
                          parent_classes: Set[str] = None, child_classes: Set[str] = None,
                          is_template: bool = False, template_specializations: Set[str] = None,
                          dependencies: Set[str] = None):
        """标记类已解析"""
        class_hash = self._generate_class_hash(usr, qualified_name)
        
        # 创建解析信息
        resolution_info = ClassResolutionInfo(
            class_name=class_data.get('name', ''),
            class_usr=usr,
            class_hash=class_hash,
            resolution_status='resolved',
            process_id=self.process_id,
            thread_id=self.thread_id,
            timestamp=time.time(),
            resolved_class_data=class_data,
            dependencies=dependencies or set(),
            parent_classes=parent_classes or set(),
            child_classes=child_classes or set(),
            is_template=is_template,
            template_specializations=template_specializations or set(),
            inheritance_processed=False
        )
        
        # 保存解析信息
        self._adapter.set(CacheDataType.CLASS_RESOLUTION, class_hash, asdict(resolution_info))
        
        # 清理解析中状态
        resolving_key = f"resolving:{class_hash}"
        self._adapter.delete(CacheDataType.CLASS_RESOLUTION, resolving_key)
        
        # 从处理栈移除
        self._processing_stack.discard(class_hash)
    
    def update_inheritance_mapping(self, parent_usr: str, parent_name: str, 
                                 child_usr: str, child_name: str):
        """更新继承关系映射（兼容原有接口）"""
        try:
            parent_hash = self._generate_class_hash(parent_usr, parent_name)
            child_hash = self._generate_class_hash(child_usr, child_name)
            
            if not parent_hash or not child_hash:
                return
            
            # 创建继承关系数据
            inheritance_data = {
                'parent_usr': parent_usr,
                'parent_name': parent_name,
                'parent_hash': parent_hash,
                'child_usr': child_usr,
                'child_name': child_name,
                'child_hash': child_hash,
                'timestamp': time.time(),
                'process_id': self.process_id
            }
            
            # 保存继承关系
            relationship_key = f"{child_hash}:{parent_hash}"
            self._adapter.set(CacheDataType.INHERITANCE_RELATION, relationship_key, inheritance_data)
            
            self.logger.debug(f"更新继承关系: {child_name} -> {parent_name}")
            
        except Exception as e:
            self.logger.debug(f"更新继承关系映射时出错: {e}")
    
    def mark_class_failed(self, usr: str, qualified_name: str, error_message: str = ""):
        """标记类解析失败"""
        class_hash = self._generate_class_hash(usr, qualified_name)
        
        # 创建失败信息
        failure_info = {
            'status': 'failed',
            'error_message': error_message,
            'process_id': self.process_id,
            'thread_id': self.thread_id,
            'timestamp': time.time()
        }
        
        # 保存失败信息
        self._adapter.set(CacheDataType.CLASS_RESOLUTION, class_hash, failure_info)
        
        # 清理解析中状态
        resolving_key = f"resolving:{class_hash}"
        self._adapter.delete(CacheDataType.CLASS_RESOLUTION, resolving_key)
        
        # 从处理栈移除
        self._processing_stack.discard(class_hash)
    
    def get_resolved_class(self, usr: str, qualified_name: str = "") -> Optional[Dict[str, Any]]:
        """获取已解析的类数据"""
        class_hash = self._generate_class_hash(usr, qualified_name)
        resolution_info = self._adapter.get(CacheDataType.CLASS_RESOLUTION, class_hash)
        
        if resolution_info and resolution_info.get('resolution_status') == 'resolved':
            return resolution_info.get('resolved_class_data')
        
        return None
    
    def get_cache_statistics(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        return self._adapter.get_statistics()


class MMapSharedHeaderManager:
    """基于MMap的共享头文件管理器（兼容现有SharedHeaderManager接口）"""
    
    def __init__(self, project_root: str, cache_dir: Optional[str] = None):
        self.logger = get_logger()
        self.project_root = project_root
        self.cache_dir = cache_dir
        self.process_id = os.getpid()
        self.thread_id = threading.get_ident()
        
        # 初始化mmap适配器
        self._adapter = MMapSharedCacheAdapter(project_root)
    
    def _generate_path_hash(self, file_path: str, compile_args: List[str]) -> str:
        """生成路径哈希"""
        content = f"{file_path}:{':'.join(compile_args)}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def register_header_for_processing(self, file_path: str, compile_args: List[str], 
                                     directory: str) -> bool:
        """注册头文件进行处理"""
        path_hash = self._generate_path_hash(file_path, compile_args)
        
        # 检查是否已处理
        if self._adapter.exists(CacheDataType.HEADER_PROCESSING, path_hash):
            return False
        
        # 创建处理信息
        processing_info = HeaderProcessingInfo(
            file_path=file_path,
            compile_args=compile_args,
            directory=directory,
            process_id=self.process_id,
            timestamp=time.time(),
            hash_value=path_hash,
            is_processed=False
        )
        
        # 尝试设置处理中状态
        return self._adapter.set(CacheDataType.HEADER_PROCESSING, path_hash, asdict(processing_info), "write", 1.0)
    
    def mark_header_processed(self, file_path: str, compile_args: List[str] = None, success: bool = True):
        """标记头文件已处理"""
        # 使用完整的路径和编译参数生成hash
        if compile_args is None:
            compile_args = []
        path_hash = self._generate_path_hash(file_path, compile_args)
        
        # 获取现有的处理信息或创建新的
        existing_info = self._adapter.get(CacheDataType.HEADER_PROCESSING, path_hash)
        if existing_info:
            processing_info = existing_info
            processing_info['is_processed'] = success
            processing_info['success'] = success
            processing_info['process_id'] = self.process_id
            processing_info['timestamp'] = time.time()
        else:
            processing_info = {
                'file_path': file_path,
                'compile_args': compile_args,
                'directory': str(Path(file_path).parent),
                'process_id': self.process_id,
                'timestamp': time.time(),
                'hash_value': path_hash,
                'is_processed': success,
                'success': success
            }
        
        self._adapter.set(CacheDataType.HEADER_PROCESSING, path_hash, processing_info)
    
    def get_processed_headers(self) -> Set[str]:
        """获取已处理的头文件列表"""
        processed_headers = set()
        
        try:
            # 扫描所有分片获取已处理的头文件
            shard_count = self._adapter.shard_manager.shard_count  # Using attribute instead of method
            for shard_id in range(shard_count):
                # 获取该分片的所有键
                from .mmap_cache.high_concurrency_mmap_manager import MmapFileType
                shard_keys = self._adapter.mmap_manager.get_all_keys(
                    MmapFileType.HEADER_CACHE, shard_id
                )
                
                for key in shard_keys:
                    if key.startswith(f"{CacheDataType.HEADER_PROCESSING.value}:"):
                        # 读取处理信息
                        data = self._adapter.mmap_manager.read_data(
                            MmapFileType.HEADER_CACHE, shard_id, key
                        )
                        if data:
                            try:
                                processing_info = self._adapter._deserialize_data(data)
                                if processing_info and processing_info.get('is_processed', False):
                                    processed_headers.add(processing_info.get('file_path', ''))
                            except Exception as e:
                                self.logger.debug(f"解析头文件处理信息失败: {e}")
                                
        except Exception as e:
            self.logger.error(f"获取已处理头文件列表失败: {e}")
        
        return processed_headers
    
    def cleanup_expired_entries(self, max_age_hours: float = 24.0):
        """清理过期的头文件处理记录"""
        try:
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600
            
            # 扫描所有分片清理过期记录
            shard_count = self._adapter.shard_manager.shard_count
            cleaned_count = 0
            
            for shard_id in range(shard_count):
                try:
                    # 获取该分片的所有键
                    from .mmap_cache.high_concurrency_mmap_manager import MmapFileType
                    shard_keys = self._adapter.mmap_manager.get_all_keys(
                        MmapFileType.HEADER_CACHE, shard_id
                    )
                    
                    for key in shard_keys:
                        if key.startswith(f"{CacheDataType.HEADER_PROCESSING.value}:"):
                            # 读取处理信息
                            data = self._adapter.mmap_manager.read_data(
                                MmapFileType.HEADER_CACHE, shard_id, key
                            )
                            if data:
                                try:
                                    processing_info = self._adapter._deserialize_data(data)
                                    if processing_info:
                                        timestamp = processing_info.get('timestamp', 0)
                                        if current_time - timestamp > max_age_seconds:
                                            # 删除过期记录
                                            self._adapter.mmap_manager.delete_data(
                                                MmapFileType.HEADER_CACHE, shard_id, key
                                            )
                                            cleaned_count += 1
                                except Exception as e:
                                    self.logger.debug(f"清理头文件处理信息失败: {e}")
                                    
                except Exception as e:
                    self.logger.debug(f"清理分片 {shard_id} 失败: {e}")
            
            if cleaned_count > 0:
                self.logger.info(f"清理了 {cleaned_count} 个过期的头文件处理记录")
                
        except Exception as e:
            self.logger.error(f"清理过期头文件记录失败: {e}")
    
    def get_processing_statistics(self) -> Dict[str, Any]:
        """获取头文件处理统计信息"""
        try:
            stats = {
                'total_processed': 0,
                'total_failed': 0,
                'total_skipped': 0,
                'processing_times': []
            }
            
            # 扫描所有分片获取统计信息
            shard_count = self._adapter.shard_manager.shard_count
            for shard_id in range(shard_count):
                try:
                    from .mmap_cache.high_concurrency_mmap_manager import MmapFileType
                    shard_keys = self._adapter.mmap_manager.get_all_keys(
                        MmapFileType.HEADER_CACHE, shard_id
                    )
                    
                    for key in shard_keys:
                        if key.startswith(f"{CacheDataType.HEADER_PROCESSING.value}:"):
                            # 修复：直接使用MmapFileType枚举
                            from .mmap_cache.high_concurrency_mmap_manager import MmapFileType
                            data = self._adapter.mmap_manager.read_data(
                                MmapFileType.HEADER_CACHE, shard_id, key
                            )
                            if data:
                                try:
                                    processing_info = self._adapter._deserialize_data(data)
                                    if processing_info:
                                        if processing_info.get('is_processed', False):
                                            stats['total_processed'] += 1
                                        else:
                                            stats['total_failed'] += 1
                                        
                                        # 记录处理时间
                                        if 'processing_time' in processing_info:
                                            stats['processing_times'].append(processing_info['processing_time'])
                                except Exception as e:
                                    self.logger.debug(f"解析头文件统计信息失败: {e}")
                                    
                except Exception as e:
                    self.logger.debug(f"获取分片 {shard_id} 统计失败: {e}")
            
            # 计算平均处理时间
            if stats['processing_times']:
                stats['avg_processing_time'] = sum(stats['processing_times']) / len(stats['processing_times'])
            else:
                stats['avg_processing_time'] = 0.0
            
            return stats
            
        except Exception as e:
            self.logger.error(f"获取头文件处理统计失败: {e}")
            return {
                'total_processed': 0,
                'total_failed': 0,
                'total_skipped': 0,
                'avg_processing_time': 0.0,
                'processing_times': []
            }


# 全局实例
_global_mmap_adapter: Optional[MMapSharedCacheAdapter] = None
_global_class_cache: Optional[MMapSharedClassCache] = None
_global_header_manager: Optional[MMapSharedHeaderManager] = None
_global_lock = threading.RLock()


def get_global_mmap_adapter(project_root: str) -> MMapSharedCacheAdapter:
    """获取全局MMap适配器"""
    global _global_mmap_adapter
    
    with _global_lock:
        if _global_mmap_adapter is None:
            _global_mmap_adapter = MMapSharedCacheAdapter(project_root)
        
        return _global_mmap_adapter


def get_global_class_cache(project_root: str) -> MMapSharedClassCache:
    """获取全局类缓存"""
    global _global_class_cache
    
    with _global_lock:
        if _global_class_cache is None:
            _global_class_cache = MMapSharedClassCache(project_root)
        
        return _global_class_cache


def get_global_header_manager(project_root: str) -> MMapSharedHeaderManager:
    """获取全局头文件管理器"""
    global _global_header_manager
    
    with _global_lock:
        if _global_header_manager is None:
            _global_header_manager = MMapSharedHeaderManager(project_root)
        
        return _global_header_manager


# 兼容性函数 - 替换现有的全局函数
def get_shared_class_cache(project_root: str) -> MMapSharedClassCache:
    """兼容现有接口的类缓存获取函数"""
    return get_global_class_cache(project_root)


def get_shared_header_manager(project_root: str) -> MMapSharedHeaderManager:
    """兼容现有接口的头文件管理器获取函数"""
    return get_global_header_manager(project_root)


def init_shared_class_cache(project_root: str):
    """初始化共享类缓存"""
    get_global_class_cache(project_root)


def init_shared_header_manager(project_root: str, cache_dir: Optional[str] = None):
    """初始化共享头文件管理器"""
    get_global_header_manager(project_root)
