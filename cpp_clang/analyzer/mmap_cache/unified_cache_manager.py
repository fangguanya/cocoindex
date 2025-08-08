#!/usr/bin/env python3
"""
统一缓存管理器 - 多缓存类型支持和缓存间解耦机制
"""

import os
import time
import threading
import hashlib
from typing import Dict, Any, Optional, Set, List, Callable
from dataclasses import dataclass, field
from enum import Enum
import logging

# 修复导入问题
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import get_logger


class CacheType(Enum):
    """缓存类型枚举"""
    CLASS_CACHE = "class_cache"
    HEADER_CACHE = "header_cache"
    FILE_CACHE = "file_cache"


class CacheEventType(Enum):
    """缓存事件类型枚举"""
    CLASS_RESOLVED = "class_resolved"
    HEADER_PROCESSED = "header_processed"
    FILE_MAPPED = "file_mapped"
    TEMPLATE_RESOLVED = "template_resolved"
    CACHE_UPDATED = "cache_updated"
    CACHE_CLEARED = "cache_cleared"


@dataclass
class CacheEvent:
    """缓存事件"""
    event_type: CacheEventType
    cache_type: CacheType
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    source_process: int = field(default_factory=lambda: os.getpid())


@dataclass
class CacheInfo:
    """缓存信息"""
    cache_type: CacheType
    instance: Any
    dependencies: Set[CacheType] = field(default_factory=set)
    is_active: bool = True
    created_time: float = field(default_factory=time.time)
    last_access_time: float = field(default_factory=time.time)
    access_count: int = 0


class CacheEventBus:
    """缓存事件总线"""
    
    def __init__(self):
        self.logger = get_logger()
        self.subscribers: Dict[CacheEventType, List[Callable]] = {}
        self.event_history: List[CacheEvent] = []
        self.max_history_size = 1000
        self._lock = threading.RLock()
    
    def subscribe(self, event_type: CacheEventType, callback: Callable[[CacheEvent], None]):
        """订阅缓存事件"""
        with self._lock:
            if event_type not in self.subscribers:
                self.subscribers[event_type] = []
            self.subscribers[event_type].append(callback)
            self.logger.debug(f"订阅事件: {event_type.value}")
    
    def unsubscribe(self, event_type: CacheEventType, callback: Callable[[CacheEvent], None]):
        """取消订阅缓存事件"""
        with self._lock:
            if event_type in self.subscribers:
                try:
                    self.subscribers[event_type].remove(callback)
                    self.logger.debug(f"取消订阅事件: {event_type.value}")
                except ValueError:
                    pass
    
    def publish(self, event: CacheEvent):
        """发布缓存事件"""
        with self._lock:
            # 记录事件历史
            self.event_history.append(event)
            if len(self.event_history) > self.max_history_size:
                self.event_history.pop(0)
            
            # 通知订阅者
            if event.event_type in self.subscribers:
                for callback in self.subscribers[event.event_type]:
                    try:
                        callback(event)
                    except Exception as e:
                        self.logger.error(f"事件回调执行失败: {e}")
            
            self.logger.debug(f"发布事件: {event.event_type.value} -> {event.cache_type.value}")
    
    def get_event_history(self, event_type: Optional[CacheEventType] = None, 
                         limit: int = 100) -> List[CacheEvent]:
        """获取事件历史"""
        with self._lock:
            if event_type:
                filtered_events = [e for e in self.event_history if e.event_type == event_type]
            else:
                filtered_events = self.event_history
            
            return filtered_events[-limit:]


class UnifiedCacheManager:
    """统一缓存管理器"""
    
    def __init__(self, project_root: str):
        self.logger = get_logger()
        self.project_root = project_root
        
        # 缓存实例注册表
        self.caches: Dict[CacheType, CacheInfo] = {}
        
        # 依赖关系图
        self.dependencies: Dict[CacheType, Set[CacheType]] = {}
        
        # 事件总线
        self.event_bus = CacheEventBus()
        
        # 线程安全
        self._lock = threading.RLock()
        
        # 性能统计
        self.stats = {
            'total_requests': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'dependency_syncs': 0,
            'events_published': 0
        }
        
        self.logger.info("统一缓存管理器初始化完成")
    
    def register_cache(self, cache_type: CacheType, cache_instance: Any, 
                      dependencies: Optional[Set[CacheType]] = None) -> bool:
        """注册缓存实例"""
        with self._lock:
            if cache_type in self.caches:
                self.logger.warning(f"缓存类型 {cache_type.value} 已存在，将被覆盖")
            
            cache_info = CacheInfo(
                cache_type=cache_type,
                instance=cache_instance,
                dependencies=dependencies or set()
            )
            
            self.caches[cache_type] = cache_info
            self.dependencies[cache_type] = dependencies or set()
            
            # 发布注册事件
            event = CacheEvent(
                event_type=CacheEventType.CACHE_UPDATED,
                cache_type=cache_type,
                data={'action': 'registered', 'dependencies': list(dependencies or [])}
            )
            self.event_bus.publish(event)
            
            self.logger.info(f"注册缓存: {cache_type.value} (依赖: {[d.value for d in dependencies or []]})")
            return True
    
    def unregister_cache(self, cache_type: CacheType) -> bool:
        """注销缓存实例"""
        with self._lock:
            if cache_type not in self.caches:
                return False
            
            cache_info = self.caches[cache_type]
            cache_info.is_active = False
            
            # 发布注销事件
            event = CacheEvent(
                event_type=CacheEventType.CACHE_UPDATED,
                cache_type=cache_type,
                data={'action': 'unregistered'}
            )
            self.event_bus.publish(event)
            
            self.logger.info(f"注销缓存: {cache_type.value}")
            return True
    
    def get_cache(self, cache_type: CacheType) -> Optional[Any]:
        """获取缓存实例"""
        with self._lock:
            self.stats['total_requests'] += 1
            
            if cache_type not in self.caches:
                self.stats['cache_misses'] += 1
                self.logger.warning(f"缓存类型不存在: {cache_type.value}")
                return None
            
            cache_info = self.caches[cache_type]
            if not cache_info.is_active:
                self.stats['cache_misses'] += 1
                self.logger.warning(f"缓存类型已停用: {cache_type.value}")
                return None
            
            # 更新访问统计
            cache_info.last_access_time = time.time()
            cache_info.access_count += 1
            self.stats['cache_hits'] += 1
            
            return cache_info.instance
    
    def get_cache_info(self, cache_type: CacheType) -> Optional[CacheInfo]:
        """获取缓存信息"""
        with self._lock:
            return self.caches.get(cache_type)
    
    def get_all_caches(self) -> Dict[CacheType, Any]:
        """获取所有活跃的缓存实例"""
        with self._lock:
            return {
                cache_type: cache_info.instance
                for cache_type, cache_info in self.caches.items()
                if cache_info.is_active
            }
    
    def list_caches(self) -> List[Dict[str, Any]]:
        """列出所有缓存信息"""
        with self._lock:
            cache_list = []
            for cache_type, cache_info in self.caches.items():
                cache_list.append({
                    'type': cache_type.value,
                    'is_active': cache_info.is_active,
                    'created_time': cache_info.created_time,
                    'last_access_time': cache_info.last_access_time,
                    'access_count': cache_info.access_count,
                    'dependencies': [dep.value for dep in cache_info.dependencies]
                })
            return cache_list
    
    def sync_dependencies(self, cache_type: CacheType) -> bool:
        """同步依赖缓存的数据"""
        with self._lock:
            if cache_type not in self.dependencies:
                return False
            
            dependencies = self.dependencies[cache_type]
            if not dependencies:
                return True
            
            self.stats['dependency_syncs'] += 1
            self.logger.debug(f"同步依赖: {cache_type.value} -> {[d.value for d in dependencies]}")
            
            # 这里可以实现具体的依赖同步逻辑
            # 例如：当类缓存更新时，通知头文件缓存更新相关状态
            
            # 发布依赖同步事件
            event = CacheEvent(
                event_type=CacheEventType.CACHE_UPDATED,
                cache_type=cache_type,
                data={'action': 'dependency_sync', 'dependencies': [d.value for d in dependencies]}
            )
            self.event_bus.publish(event)
            
            return True
    
    def get_dependencies(self, cache_type: CacheType) -> Set[CacheType]:
        """获取缓存依赖"""
        with self._lock:
            return self.dependencies.get(cache_type, set()).copy()
    
    def add_dependency(self, cache_type: CacheType, dependency: CacheType) -> bool:
        """添加缓存依赖"""
        with self._lock:
            if cache_type not in self.dependencies:
                self.dependencies[cache_type] = set()
            
            self.dependencies[cache_type].add(dependency)
            
            # 更新缓存信息
            if cache_type in self.caches:
                self.caches[cache_type].dependencies.add(dependency)
            
            self.logger.debug(f"添加依赖: {cache_type.value} -> {dependency.value}")
            return True
    
    def remove_dependency(self, cache_type: CacheType, dependency: CacheType) -> bool:
        """移除缓存依赖"""
        with self._lock:
            if cache_type in self.dependencies:
                self.dependencies[cache_type].discard(dependency)
            
            if cache_type in self.caches:
                self.caches[cache_type].dependencies.discard(dependency)
            
            self.logger.debug(f"移除依赖: {cache_type.value} -> {dependency.value}")
            return True
    
    def publish_event(self, event_type: CacheEventType, cache_type: CacheType, 
                     data: Dict[str, Any]) -> None:
        """发布缓存事件"""
        event = CacheEvent(
            event_type=event_type,
            cache_type=cache_type,
            data=data
        )
        self.event_bus.publish(event)
        self.stats['events_published'] += 1
    
    def subscribe_event(self, event_type: CacheEventType, 
                       callback: Callable[[CacheEvent], None]) -> None:
        """订阅缓存事件"""
        self.event_bus.subscribe(event_type, callback)
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            stats = self.stats.copy()
            
            # 添加缓存实例统计
            stats['active_caches'] = len([c for c in self.caches.values() if c.is_active])
            stats['total_caches'] = len(self.caches)
            
            # 添加缓存访问统计
            cache_access_stats = {}
            for cache_type, cache_info in self.caches.items():
                cache_access_stats[cache_type.value] = {
                    'access_count': cache_info.access_count,
                    'last_access': cache_info.last_access_time,
                    'is_active': cache_info.is_active
                }
            stats['cache_access_stats'] = cache_access_stats
            
            return stats
    
    def cleanup_inactive_caches(self, max_idle_time: float = 3600) -> int:
        """清理非活跃缓存"""
        with self._lock:
            current_time = time.time()
            cleaned_count = 0
            
            for cache_type, cache_info in list(self.caches.items()):
                if (not cache_info.is_active and 
                    current_time - cache_info.last_access_time > max_idle_time):
                    
                    del self.caches[cache_type]
                    if cache_type in self.dependencies:
                        del self.dependencies[cache_type]
                    
                    cleaned_count += 1
                    self.logger.info(f"清理非活跃缓存: {cache_type.value}")
            
            return cleaned_count
    
    def shutdown(self) -> None:
        """关闭缓存管理器"""
        with self._lock:
            # 发布关闭事件
            for cache_type in self.caches:
                event = CacheEvent(
                    event_type=CacheEventType.CACHE_UPDATED,
                    cache_type=cache_type,
                    data={'action': 'shutdown'}
                )
                self.event_bus.publish(event)
            
            # 清理所有缓存
            self.caches.clear()
            self.dependencies.clear()
            
            self.logger.info("统一缓存管理器已关闭")


# 全局缓存管理器实例
_global_cache_manager: Optional[UnifiedCacheManager] = None
_global_cache_manager_lock = threading.Lock()


def get_global_cache_manager(project_root: str) -> UnifiedCacheManager:
    """获取全局缓存管理器实例"""
    global _global_cache_manager
    
    if _global_cache_manager is None:
        with _global_cache_manager_lock:
            if _global_cache_manager is None:
                _global_cache_manager = UnifiedCacheManager(project_root)
    
    return _global_cache_manager


def init_global_cache_manager(project_root: str) -> UnifiedCacheManager:
    """初始化全局缓存管理器"""
    global _global_cache_manager
    
    with _global_cache_manager_lock:
        if _global_cache_manager is not None:
            _global_cache_manager.shutdown()
        
        _global_cache_manager = UnifiedCacheManager(project_root)
        return _global_cache_manager


def shutdown_global_cache_manager() -> None:
    """关闭全局缓存管理器"""
    global _global_cache_manager
    
    with _global_cache_manager_lock:
        if _global_cache_manager is not None:
            _global_cache_manager.shutdown()
            _global_cache_manager = None
