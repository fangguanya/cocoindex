#!/usr/bin/env python3
"""
mmap_cache模块 - 高并发内存映射缓存系统
"""

from .unified_cache_manager import (
    UnifiedCacheManager,
    CacheType,
    CacheEventType,
    CacheEvent,
    CacheInfo,
    CacheEventBus,
    get_global_cache_manager,
    init_global_cache_manager,
    shutdown_global_cache_manager
)

from .high_concurrency_mmap_manager import (
    HighConcurrencyMmapManager,
    MmapFileType,
    MmapHeader,
    MmapIndexEntry,
    MmapAccessMode,
    get_global_mmap_manager,
    init_global_mmap_manager,
    shutdown_global_mmap_manager
)

from .shard_manager import (
    HighConcurrencyShardManager,
    ShardStatus,
    ShardInfo,
    ShardRoutingStrategy,
    get_global_shard_manager,
    init_global_shard_manager,
    shutdown_global_shard_manager
)

from .concurrent_lock_manager import (
    HighConcurrencyLockManager,
    LockType,
    LockStatus,
    LockRequest,
    ShardLockState,
    DeadlockDetector,
    get_global_lock_manager,
    init_global_lock_manager,
    shutdown_global_lock_manager
)



from .unified_monitor import (
    UnifiedMonitor,
    MetricType,
    LogLevel,
    OptimizationTarget,
    OptimizationLevel,
    MetricData,
    LogEntry,
    HealthStatus,
    PerformanceMetrics,
    OptimizationConfig,
    OptimizationResult,
    get_global_monitor,
    init_global_monitor,
    shutdown_global_monitor
)

from .system_integration import (
    SystemIntegrator,
    SystemConfig,
    CacheConfig,
    create_default_config,
    get_global_integrator,
    init_global_integrator,
    shutdown_global_integrator
)

from .error_handler import (
    ErrorHandler,
    ErrorType,
    ErrorSeverity,
    RecoveryStrategy,
    ErrorInfo,
    RecoveryAction,
    ErrorReport,
    get_global_error_handler,
    init_global_error_handler,
    shutdown_global_error_handler
)

__all__ = [
    # 统一缓存管理器
    'UnifiedCacheManager',
    'CacheType',
    'CacheEventType',
    'CacheEvent',
    'CacheInfo',
    'CacheEventBus',
    'get_global_cache_manager',
    'init_global_cache_manager',
    'shutdown_global_cache_manager',
    
    # 高并发mmap管理器
    'HighConcurrencyMmapManager',
    'MmapFileType',
    'MmapHeader',
    'MmapIndexEntry',
    'MmapAccessMode',
    'get_global_mmap_manager',
    'init_global_mmap_manager',
    'shutdown_global_mmap_manager',
    
    # 高并发分片管理器
    'HighConcurrencyShardManager',
    'ShardStatus',
    'ShardInfo',
    'ShardRoutingStrategy',
    'get_global_shard_manager',
    'init_global_shard_manager',
    'shutdown_global_shard_manager',
    
    # 高并发锁管理器
    'HighConcurrencyLockManager',
    'LockType',
    'LockStatus',
    'LockRequest',
    'ShardLockState',
    'DeadlockDetector',
    'get_global_lock_manager',
    'init_global_lock_manager',
    'shutdown_global_lock_manager',
    

    
    # 统一监控器
    'UnifiedMonitor',
    'MetricType',
    'LogLevel',
    'OptimizationTarget',
    'OptimizationLevel',
    'MetricData',
    'LogEntry',
    'HealthStatus',
    'PerformanceMetrics',
    'OptimizationConfig',
    'OptimizationResult',
    'get_global_monitor',
    'init_global_monitor',
    'shutdown_global_monitor',
    
    # 系统集成
    'SystemIntegrator',
    'SystemConfig',
    'CacheConfig',
    'create_default_config',
    'get_global_integrator',
    'init_global_integrator',
    'shutdown_global_integrator',
    
    # 错误处理器
    'ErrorHandler',
    'ErrorType',
    'ErrorSeverity',
    'RecoveryStrategy',
    'ErrorInfo',
    'RecoveryAction',
    'ErrorReport',
    'get_global_error_handler',
    'init_global_error_handler',
    'shutdown_global_error_handler'
]

__version__ = "1.0.0"
__author__ = "C++ Analyzer Team"
__description__ = "高并发内存映射缓存系统，支持192进程并发访问"
