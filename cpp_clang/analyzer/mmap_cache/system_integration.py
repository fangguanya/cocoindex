#!/usr/bin/env python3
"""
系统集成组件 - 多缓存集成到现有系统
"""

import os
import sys
import logging
import threading
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import json

from .unified_cache_manager import (
    UnifiedCacheManager, CacheType, CacheEventType, CacheEvent, CacheInfo
)
from .high_concurrency_mmap_manager import (
    HighConcurrencyMmapManager, MmapFileType, MmapAccessMode
)
from .shard_manager import (
    HighConcurrencyShardManager, ShardRoutingStrategy
)
from .concurrent_lock_manager import (
    HighConcurrencyLockManager, LockType
)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)





@dataclass
class CacheConfig:
    """缓存配置"""
    cache_name: str
    cache_type: CacheType
    file_type: MmapFileType
    shard_count: int = 64
    routing_strategy: ShardRoutingStrategy = ShardRoutingStrategy.HASH_BASED
    enable_compression: bool = False
    enable_encryption: bool = False
    max_file_size: int = 1024 * 1024 * 1024  # 1GB
    backup_enabled: bool = True
    backup_interval: int = 3600  # 1小时


@dataclass
class SystemConfig:
    """系统配置"""
    base_dir: str = "./cache"
    temp_dir: str = "./temp"
    log_dir: str = "./logs"
    max_processes: int = 192
    enable_monitoring: bool = True
    enable_health_check: bool = True
    health_check_interval: int = 60  # 60秒
    caches: Dict[str, CacheConfig] = field(default_factory=dict)


class SystemIntegrator:
    """系统集成器"""
    
    def __init__(self, config: SystemConfig):
        self.config = config
        self.cache_manager: Optional[UnifiedCacheManager] = None
        self.mmap_manager: Optional[HighConcurrencyMmapManager] = None
        self.shard_manager: Optional[HighConcurrencyShardManager] = None
        self.lock_manager: Optional[HighConcurrencyLockManager] = None
        self._initialized = False
        self._lock = threading.RLock()
        self._health_check_thread: Optional[threading.Thread] = None
        self._stop_health_check = threading.Event()
        
        # 创建必要的目录
        self._create_directories()
    
    def _create_directories(self):
        """创建必要的目录"""
        directories = [self.config.base_dir, self.config.temp_dir, self.config.log_dir]
        for directory in directories:
            os.makedirs(directory, exist_ok=True)
    
    def initialize(self) -> bool:
        """初始化系统集成器"""
        with self._lock:
            if self._initialized:
                return True
            
            try:
                logger.info("开始初始化系统集成器...")
                
                # 初始化全局管理器
                self._init_global_managers()
                
                # 初始化缓存管理器
                self._init_cache_manager()
                
                # 初始化mmap管理器
                self._init_mmap_manager()
                
                # 初始化分片管理器
                self._init_shard_manager()
                
                # 初始化锁管理器
                self._init_lock_manager()
                
                # 注册缓存
                self._register_caches()
                
                # 启动健康检查
                if self.config.enable_health_check:
                    self._start_health_check()
                
                self._initialized = True
                logger.info("系统集成器初始化完成")
                return True
                
            except Exception as e:
                logger.error(f"系统集成器初始化失败: {e}")
                self.cleanup()
                return False
    
    def _init_global_managers(self):
        """初始化全局管理器"""
        from .unified_cache_manager import init_global_cache_manager
        from .high_concurrency_mmap_manager import init_global_mmap_manager
        from .shard_manager import init_global_shard_manager
        from .concurrent_lock_manager import init_global_lock_manager
        
        # 初始化全局缓存管理器
        init_global_cache_manager(self.config.base_dir)
        
        # 初始化全局mmap管理器
        init_global_mmap_manager(self.config.base_dir)
        
        # 初始化全局分片管理器
        init_global_shard_manager(
            project_root=self.config.base_dir,
            shard_count=64
        )
        
        # 初始化全局锁管理器
        init_global_lock_manager(
            project_root=self.config.base_dir,
            default_timeout=30.0,
            enable_deadlock_detection=True
        )
    
    def _init_cache_manager(self):
        """初始化缓存管理器"""
        from .unified_cache_manager import get_global_cache_manager
        self.cache_manager = get_global_cache_manager(self.config.base_dir)
        
        # 设置事件处理器
        self.cache_manager.subscribe_event(
            CacheEventType.CACHE_UPDATED,
            self._on_cache_created
        )
        self.cache_manager.subscribe_event(
            CacheEventType.CACHE_CLEARED,
            self._on_cache_deleted
        )
        self.cache_manager.subscribe_event(
            CacheEventType.CACHE_UPDATED,
            self._on_cache_error
        )
    
    def _init_mmap_manager(self):
        """初始化mmap管理器"""
        from .high_concurrency_mmap_manager import get_global_mmap_manager
        self.mmap_manager = get_global_mmap_manager(self.config.base_dir)
    
    def _init_shard_manager(self):
        """初始化分片管理器"""
        from .shard_manager import get_global_shard_manager
        self.shard_manager = get_global_shard_manager(self.config.base_dir)
    
    def _init_lock_manager(self):
        """初始化锁管理器"""
        from .concurrent_lock_manager import get_global_lock_manager
        self.lock_manager = get_global_lock_manager(self.config.base_dir)
    
    def _register_caches(self):
        """注册缓存"""
        for cache_name, cache_config in self.config.caches.items():
            try:
                self.register_cache(cache_name, cache_config)
            except Exception as e:
                logger.error(f"注册缓存 {cache_name} 失败: {e}")
    
    def register_cache(self, cache_name: str, config: CacheConfig) -> bool:
        """注册缓存"""
        if not self._initialized:
            raise RuntimeError("系统集成器未初始化")
        
        try:
            # 创建缓存
            cache_info = CacheInfo(
                cache_type=config.cache_type,
                instance=None  # 暂时设为None，后续会设置
            )
            
            success = self.cache_manager.register_cache(config.cache_type, None)
            if success:
                logger.info(f"成功注册缓存: {cache_name}")
                return True
            else:
                logger.error(f"注册缓存失败: {cache_name}")
                return False
                
        except Exception as e:
            logger.error(f"注册缓存异常: {cache_name}, 错误: {e}")
            return False
    
    def get_cache(self, cache_name: str):
        """获取缓存实例"""
        if not self._initialized:
            raise RuntimeError("系统集成器未初始化")
        
        return self.cache_manager.get_cache(cache_name)
    
    def list_caches(self) -> List[str]:
        """列出所有缓存"""
        if not self._initialized:
            return []
        
        return self.cache_manager.list_caches()
    
    def delete_cache(self, cache_name: str) -> bool:
        """删除缓存"""
        if not self._initialized:
            return False
        
        return self.cache_manager.delete_cache(cache_name)
    
    def get_cache_info(self, cache_name: str) -> Optional[CacheInfo]:
        """获取缓存信息"""
        if not self._initialized:
            return None
        
        return self.cache_manager.get_cache_info(cache_name)
    
    def get_system_status(self) -> Dict[str, Any]:
        """获取系统状态"""
        if not self._initialized:
            return {"status": "not_initialized"}
        
        try:
            status = {
                "status": "running",
                "initialized": self._initialized,
                "cache_count": len(self.list_caches()),
                "caches": {},
                "shard_status": {},
                "lock_status": {},
                "mmap_status": {}
            }
            
            # 缓存状态
            for cache_name in self.list_caches():
                cache_info = self.get_cache_info(cache_name)
                if cache_info:
                    status["caches"][cache_name] = {
                        "type": cache_info.cache_type.value,
                        "file_type": cache_info.file_type.value,
                        "shard_count": cache_info.shard_count,
                        "routing_strategy": cache_info.routing_strategy.value
                    }
            
            # 分片状态
            if self.shard_manager:
                shard_stats = self.shard_manager.get_statistics()
                status["shard_status"] = {
                    "total_shards": shard_stats.get("total_shards", 0),
                    "active_shards": shard_stats.get("active_shards", 0),
                    "load_balanced": shard_stats.get("load_balanced", True)
                }
            
            # 锁状态
            if self.lock_manager:
                lock_stats = self.lock_manager.get_statistics()
                status["lock_status"] = {
                    "total_locks": lock_stats.get("total_locks", 0),
                    "active_locks": lock_stats.get("active_locks", 0),
                    "deadlock_count": lock_stats.get("deadlock_count", 0)
                }
            
            # mmap状态
            if self.mmap_manager:
                mmap_stats = self.mmap_manager.get_statistics()
                status["mmap_status"] = {
                    "total_files": mmap_stats.get("total_files", 0),
                    "total_size": mmap_stats.get("total_size", 0),
                    "memory_usage": mmap_stats.get("memory_usage", 0)
                }
            
            return status
            
        except Exception as e:
            logger.error(f"获取系统状态失败: {e}")
            return {"status": "error", "error": str(e)}
    
    def _on_cache_created(self, event: CacheEvent):
        """缓存创建事件处理"""
        logger.info(f"缓存创建事件: {event.cache_name}")
    
    def _on_cache_deleted(self, event: CacheEvent):
        """缓存删除事件处理"""
        logger.info(f"缓存删除事件: {event.cache_name}")
    
    def _on_cache_error(self, event: CacheEvent):
        """缓存错误事件处理"""
        logger.error(f"缓存错误事件: {event.cache_name}, 错误: {event.data}")
    
    def _start_health_check(self):
        """启动健康检查"""
        self._health_check_thread = threading.Thread(
            target=self._health_check_loop,
            daemon=True
        )
        self._health_check_thread.start()
    
    def _health_check_loop(self):
        """健康检查循环"""
        while not self._stop_health_check.is_set():
            try:
                self._perform_health_check()
                time.sleep(self.config.health_check_interval)
            except Exception as e:
                logger.error(f"健康检查异常: {e}")
                time.sleep(10)  # 异常时等待10秒
    
    def _perform_health_check(self):
        """执行健康检查"""
        try:
            # 检查缓存管理器
            if self.cache_manager:
                cache_count = len(self.list_caches())
                logger.debug(f"健康检查: 缓存数量 {cache_count}")
            
            # 检查分片管理器
            if self.shard_manager:
                shard_stats = self.shard_manager.get_statistics()
                active_shards = shard_stats.get("active_shards", 0)
                total_shards = shard_stats.get("total_shards", 0)
                if total_shards > 0 and active_shards < total_shards * 0.9:
                    logger.warning("健康检查: 分片活跃度低于90%")
            
            # 检查锁管理器
            if self.lock_manager:
                lock_stats = self.lock_manager.get_statistics()
                deadlock_count = lock_stats.get("deadlock_count", 0)
                if deadlock_count > 0:
                    logger.warning(f"健康检查: 检测到 {deadlock_count} 个死锁")
            
            # 检查mmap管理器
            if self.mmap_manager:
                mmap_stats = self.mmap_manager.get_statistics()
                memory_usage = mmap_stats.get("memory_usage", 0)
                if memory_usage > 1024 * 1024 * 1024:  # 1GB
                    logger.warning("健康检查: 内存使用超过1GB")
                    
        except Exception as e:
            logger.error(f"健康检查失败: {e}")
    
    def cleanup(self):
        """清理资源"""
        with self._lock:
            if not self._initialized:
                return
            
            logger.info("开始清理系统集成器...")
            
            # 停止健康检查
            if self._health_check_thread:
                self._stop_health_check.set()
                if self._health_check_thread.is_alive():
                    self._health_check_thread.join(timeout=5)
            
            # 清理全局管理器
            try:
                from .unified_cache_manager import shutdown_global_cache_manager
                from .high_concurrency_mmap_manager import shutdown_global_mmap_manager
                from .shard_manager import shutdown_global_shard_manager
                from .concurrent_lock_manager import shutdown_global_lock_manager
                
                shutdown_global_cache_manager()
                shutdown_global_mmap_manager()
                shutdown_global_shard_manager()
                shutdown_global_lock_manager()
                
            except Exception as e:
                logger.error(f"清理全局管理器失败: {e}")
            
            self._initialized = False
            logger.info("系统集成器清理完成")
    
    def __enter__(self):
        """上下文管理器入口"""
        if not self.initialize():
            raise RuntimeError("系统集成器初始化失败")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.cleanup()


# 全局系统集成器实例
_global_integrator: Optional[SystemIntegrator] = None
_integrator_lock = threading.RLock()


def get_global_integrator() -> Optional[SystemIntegrator]:
    """获取全局系统集成器"""
    return _global_integrator


def init_global_integrator(config: SystemConfig) -> bool:
    """初始化全局系统集成器"""
    global _global_integrator
    
    with _integrator_lock:
        if _global_integrator is not None:
            return True
        
        try:
            _global_integrator = SystemIntegrator(config)
            return _global_integrator.initialize()
        except Exception as e:
            logger.error(f"初始化全局系统集成器失败: {e}")
            return False


def shutdown_global_integrator():
    """关闭全局系统集成器"""
    global _global_integrator
    
    with _integrator_lock:
        if _global_integrator is not None:
            _global_integrator.cleanup()
            _global_integrator = None


# 默认配置
def create_default_config() -> SystemConfig:
    """创建默认配置"""
    config = SystemConfig()
    
    # 添加默认缓存配置
    config.caches["class_cache"] = CacheConfig(
        cache_name="class_cache",
        cache_type=CacheType.CLASS_CACHE,
        file_type=MmapFileType.CLASS_CACHE,
        shard_count=64,
        routing_strategy=ShardRoutingStrategy.HASH_BASED
    )
    
    config.caches["header_cache"] = CacheConfig(
        cache_name="header_cache",
        cache_type=CacheType.HEADER_CACHE,
        file_type=MmapFileType.HEADER_CACHE,
        shard_count=64,
        routing_strategy=ShardRoutingStrategy.HASH_BASED
    )
    
    config.caches["file_cache"] = CacheConfig(
        cache_name="file_cache",
        cache_type=CacheType.FILE_CACHE,
        file_type=MmapFileType.FILE_CACHE,
        shard_count=64,
        routing_strategy=ShardRoutingStrategy.HASH_BASED
    )
    
    return config


if __name__ == "__main__":
    # 测试系统集成器
    config = create_default_config()
    
    with SystemIntegrator(config) as integrator:
        print("系统集成器初始化成功")
        
        # 获取系统状态
        status = integrator.get_system_status()
        print(f"系统状态: {json.dumps(status, indent=2, ensure_ascii=False)}")
        
        # 列出缓存
        caches = integrator.list_caches()
        print(f"缓存列表: {caches}")
        
        print("系统集成器测试完成")
