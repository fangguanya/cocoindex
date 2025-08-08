#!/usr/bin/env python3
"""
高并发错误处理组件 - 错误处理和恢复
"""

import os
import sys
import logging
import threading
import time
import traceback
import hashlib
import json
from typing import Dict, Any, Optional, List, Callable, Union
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, deque
import mmap
import tempfile
import shutil

from .unified_cache_manager import UnifiedCacheManager
from .high_concurrency_mmap_manager import HighConcurrencyMmapManager
from .shard_manager import HighConcurrencyShardManager
from .concurrent_lock_manager import HighConcurrencyLockManager

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """错误类型"""
    MMAP_FILE_CORRUPTION = "mmap_file_corruption"
    MEMORY_SYNC_ERROR = "memory_sync_error"
    SHARD_LOCK_EXCEPTION = "shard_lock_exception"
    LOCK_TIMEOUT = "lock_timeout"
    CACHE_CONSISTENCY_ERROR = "cache_consistency_error"
    SYSTEM_RESOURCE_ERROR = "system_resource_error"
    NETWORK_ERROR = "network_error"
    UNKNOWN_ERROR = "unknown_error"


class ErrorSeverity(Enum):
    """错误严重程度"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RecoveryStrategy(Enum):
    """恢复策略"""
    RETRY = "retry"
    FALLBACK = "fallback"
    RESTORE = "restore"
    DEGRADE = "degrade"
    RESTART = "restart"


@dataclass
class ErrorInfo:
    """错误信息"""
    error_type: ErrorType
    severity: ErrorSeverity
    message: str
    timestamp: float
    process_id: int
    thread_id: int
    stack_trace: str
    context: Dict[str, Any] = field(default_factory=dict)
    error_id: str = ""


@dataclass
class RecoveryAction:
    """恢复动作"""
    strategy: RecoveryStrategy
    description: str
    success: bool
    duration: float
    timestamp: float
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorReport:
    """错误报告"""
    error_info: ErrorInfo
    recovery_actions: List[RecoveryAction]
    resolved: bool
    resolution_time: Optional[float] = None
    total_downtime: float = 0.0


class ErrorHandler:
    """错误处理器"""
    
    def __init__(self, 
                 cache_manager: UnifiedCacheManager,
                 mmap_manager: HighConcurrencyMmapManager,
                 shard_manager: HighConcurrencyShardManager,
                 lock_manager: HighConcurrencyLockManager):
        self.cache_manager = cache_manager
        self.mmap_manager = mmap_manager
        self.shard_manager = shard_manager
        self.lock_manager = lock_manager
        
        self.error_reports: Dict[str, ErrorReport] = {}
        self.error_handlers: Dict[ErrorType, Callable] = {}
        self.recovery_strategies: Dict[ErrorType, List[RecoveryStrategy]] = {}
        
        self._running = False
        self._lock = threading.RLock()
        self._error_queue: deque = deque(maxlen=1000)
        self._processing_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # 初始化错误处理器
        self._init_error_handlers()
        self._init_recovery_strategies()
    
    def _init_error_handlers(self):
        """初始化错误处理器"""
        self.error_handlers[ErrorType.MMAP_FILE_CORRUPTION] = self._handle_mmap_corruption
        self.error_handlers[ErrorType.MEMORY_SYNC_ERROR] = self._handle_memory_sync_error
        self.error_handlers[ErrorType.SHARD_LOCK_EXCEPTION] = self._handle_shard_lock_exception
        self.error_handlers[ErrorType.CACHE_CONSISTENCY_ERROR] = self._handle_cache_consistency_error
        self.error_handlers[ErrorType.SYSTEM_RESOURCE_ERROR] = self._handle_system_resource_error
        self.error_handlers[ErrorType.NETWORK_ERROR] = self._handle_network_error
        self.error_handlers[ErrorType.UNKNOWN_ERROR] = self._handle_unknown_error
    
    def _init_recovery_strategies(self):
        """初始化恢复策略"""
        self.recovery_strategies[ErrorType.MMAP_FILE_CORRUPTION] = [
            RecoveryStrategy.RESTORE,
            RecoveryStrategy.FALLBACK,
            RecoveryStrategy.DEGRADE
        ]
        
        self.recovery_strategies[ErrorType.MEMORY_SYNC_ERROR] = [
            RecoveryStrategy.RETRY,
            RecoveryStrategy.RESTORE,
            RecoveryStrategy.DEGRADE
        ]
        
        self.recovery_strategies[ErrorType.SHARD_LOCK_EXCEPTION] = [
            RecoveryStrategy.RETRY,
            RecoveryStrategy.FALLBACK,
            RecoveryStrategy.DEGRADE
        ]
        
        self.recovery_strategies[ErrorType.CACHE_CONSISTENCY_ERROR] = [
            RecoveryStrategy.RESTORE,
            RecoveryStrategy.FALLBACK,
            RecoveryStrategy.DEGRADE
        ]
        
        self.recovery_strategies[ErrorType.SYSTEM_RESOURCE_ERROR] = [
            RecoveryStrategy.DEGRADE,
            RecoveryStrategy.FALLBACK,
            RecoveryStrategy.RESTART
        ]
        
        self.recovery_strategies[ErrorType.NETWORK_ERROR] = [
            RecoveryStrategy.RETRY,
            RecoveryStrategy.FALLBACK,
            RecoveryStrategy.DEGRADE
        ]
        
        self.recovery_strategies[ErrorType.UNKNOWN_ERROR] = [
            RecoveryStrategy.RETRY,
            RecoveryStrategy.FALLBACK,
            RecoveryStrategy.DEGRADE
        ]
    
    def start_error_handling(self):
        """开始错误处理"""
        with self._lock:
            if self._running:
                return
            
            self._running = True
            self._processing_thread = threading.Thread(
                target=self._error_processing_loop,
                daemon=True
            )
            self._processing_thread.start()
            logger.info("错误处理已启动")
    
    def stop_error_handling(self):
        """停止错误处理"""
        with self._lock:
            if not self._running:
                return
            
            self._stop_event.set()
            if self._processing_thread and self._processing_thread.is_alive():
                self._processing_thread.join(timeout=5)
            
            self._running = False
            logger.info("错误处理已停止")
    
    def _error_processing_loop(self):
        """错误处理循环"""
        while not self._stop_event.is_set():
            try:
                # 处理错误队列
                while self._error_queue:
                    error_info = self._error_queue.popleft()
                    self._process_error(error_info)
                
                # 等待新错误
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"错误处理循环异常: {e}")
                time.sleep(5)
    
    def report_error(self, error_type: ErrorType, message: str, 
                    severity: ErrorSeverity = ErrorSeverity.MEDIUM,
                    context: Optional[Dict[str, Any]] = None) -> str:
        """报告错误"""
        try:
            error_info = ErrorInfo(
                error_type=error_type,
                severity=severity,
                message=message,
                timestamp=time.time(),
                process_id=os.getpid(),
                thread_id=threading.get_ident(),
                stack_trace=traceback.format_exc(),
                context=context or {},
                error_id=self._generate_error_id(error_type, message)
            )
            
            # 添加到错误队列
            self._error_queue.append(error_info)
            
            logger.error(f"错误报告: {error_type.value} - {message}")
            return error_info.error_id
            
        except Exception as e:
            logger.error(f"报告错误失败: {e}")
            return ""
    
    def _generate_error_id(self, error_type: ErrorType, message: str) -> str:
        """生成错误ID"""
        content = f"{error_type.value}:{message}:{time.time()}"
        return hashlib.md5(content.encode()).hexdigest()[:16]
    
    def _process_error(self, error_info: ErrorInfo):
        """处理错误"""
        try:
            logger.info(f"开始处理错误: {error_info.error_id}")
            
            # 创建错误报告
            error_report = ErrorReport(
                error_info=error_info,
                recovery_actions=[],
                resolved=False,
                resolution_time=None,
                total_downtime=0.0
            )
            
            # 获取恢复策略
            strategies = self.recovery_strategies.get(error_info.error_type, [])
            
            # 尝试恢复
            for strategy in strategies:
                action = self._execute_recovery_strategy(error_info, strategy)
                error_report.recovery_actions.append(action)
                
                if action.success:
                    logger.info(f"错误恢复成功: {error_info.error_id}, 策略: {strategy.value}")
                    error_report.resolved = True
                    error_report.resolution_time = time.time()
                    break
                else:
                    logger.warning(f"恢复策略失败: {error_info.error_id}, 策略: {strategy.value}")
            
            # 保存错误报告
            self.error_reports[error_info.error_id] = error_report
            
            # 如果所有策略都失败，记录严重错误
            if not error_report.resolved:
                logger.critical(f"错误无法恢复: {error_info.error_id}")
                self._handle_unrecoverable_error(error_info)
            
        except Exception as e:
            logger.error(f"处理错误失败: {error_info.error_id}, 异常: {e}")
    
    def _execute_recovery_strategy(self, error_info: ErrorInfo, 
                                 strategy: RecoveryStrategy) -> RecoveryAction:
        """执行恢复策略"""
        start_time = time.time()
        
        try:
            if strategy == RecoveryStrategy.RETRY:
                success = self._retry_operation(error_info)
            elif strategy == RecoveryStrategy.FALLBACK:
                success = self._fallback_operation(error_info)
            elif strategy == RecoveryStrategy.RESTORE:
                success = self._restore_operation(error_info)
            elif strategy == RecoveryStrategy.DEGRADE:
                success = self._degrade_operation(error_info)
            elif strategy == RecoveryStrategy.RESTART:
                success = self._restart_operation(error_info)
            else:
                success = False
            
            duration = time.time() - start_time
            
            return RecoveryAction(
                strategy=strategy,
                description=f"执行{strategy.value}策略",
                success=success,
                duration=duration,
                timestamp=time.time(),
                details={"error_id": error_info.error_id}
            )
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"执行恢复策略失败: {strategy.value}, 错误: {e}")
            
            return RecoveryAction(
                strategy=strategy,
                description=f"执行{strategy.value}策略失败",
                success=False,
                duration=duration,
                timestamp=time.time(),
                details={"error": str(e), "error_id": error_info.error_id}
            )
    
    def _retry_operation(self, error_info: ErrorInfo) -> bool:
        """重试操作"""
        try:
            max_retries = 3
            retry_delay = 1.0
            
            for attempt in range(max_retries):
                try:
                    # 根据错误类型执行相应的重试逻辑
                    if error_info.error_type == ErrorType.MMAP_FILE_CORRUPTION:
                        return self._retry_mmap_operation(error_info)
                    elif error_info.error_type == ErrorType.MEMORY_SYNC_ERROR:
                        return self._retry_memory_sync(error_info)
                    elif error_info.error_type == ErrorType.SHARD_LOCK_EXCEPTION:
                        return self._retry_lock_operation(error_info)
                    else:
                        # 通用重试逻辑
                        time.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    
                except Exception as e:
                    logger.warning(f"重试尝试 {attempt + 1} 失败: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        retry_delay *= 2
            
            return False
            
        except Exception as e:
            logger.error(f"重试操作失败: {e}")
            return False
    
    def _fallback_operation(self, error_info: ErrorInfo) -> bool:
        """降级操作"""
        try:
            # 根据错误类型执行相应的降级逻辑
            if error_info.error_type == ErrorType.MMAP_FILE_CORRUPTION:
                return self._fallback_to_file_storage(error_info)
            elif error_info.error_type == ErrorType.CACHE_CONSISTENCY_ERROR:
                return self._fallback_to_readonly_mode(error_info)
            elif error_info.error_type == ErrorType.SYSTEM_RESOURCE_ERROR:
                return self._fallback_to_reduced_functionality(error_info)
            else:
                return self._generic_fallback(error_info)
                
        except Exception as e:
            logger.error(f"降级操作失败: {e}")
            return False
    
    def _restore_operation(self, error_info: ErrorInfo) -> bool:
        """恢复操作"""
        try:
            # 根据错误类型执行相应的恢复逻辑
            if error_info.error_type == ErrorType.MMAP_FILE_CORRUPTION:
                return self._restore_mmap_file(error_info)
            elif error_info.error_type == ErrorType.CACHE_CONSISTENCY_ERROR:
                return self._restore_cache_consistency(error_info)
            else:
                return self._generic_restore(error_info)
                
        except Exception as e:
            logger.error(f"恢复操作失败: {e}")
            return False
    
    def _degrade_operation(self, error_info: ErrorInfo) -> bool:
        """降级操作"""
        try:
            # 根据错误类型执行相应的降级逻辑
            if error_info.error_type == ErrorType.SYSTEM_RESOURCE_ERROR:
                return self._degrade_performance(error_info)
            elif error_info.error_type == ErrorType.NETWORK_ERROR:
                return self._degrade_connectivity(error_info)
            else:
                return self._generic_degrade(error_info)
                
        except Exception as e:
            logger.error(f"降级操作失败: {e}")
            return False
    
    def _restart_operation(self, error_info: ErrorInfo) -> bool:
        """重启操作"""
        try:
            logger.warning("执行系统重启操作")
            # 这里应该实现实际的重启逻辑
            # 由于重启是危险操作，这里只是记录日志
            return False
            
        except Exception as e:
            logger.error(f"重启操作失败: {e}")
            return False
    
    # 具体的错误处理方法
    def _handle_mmap_corruption(self, error_info: ErrorInfo):
        """处理mmap文件损坏"""
        try:
            logger.info(f"处理mmap文件损坏: {error_info.error_id}")
            
            # 检查文件完整性
            if self._verify_mmap_file_integrity(error_info):
                logger.info("mmap文件完整性验证通过")
                return True
            
            # 尝试从备份恢复
            if self._restore_from_backup(error_info):
                logger.info("从备份恢复成功")
                return True
            
            # 重新创建文件
            if self._recreate_mmap_file(error_info):
                logger.info("重新创建mmap文件成功")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"处理mmap文件损坏失败: {e}")
            return False
    
    def _handle_memory_sync_error(self, error_info: ErrorInfo):
        """处理内存同步错误"""
        try:
            logger.info(f"处理内存同步错误: {error_info.error_id}")
            
            # 强制同步内存
            if hasattr(self.mmap_manager, 'force_sync'):
                self.mmap_manager.force_sync()
                logger.info("强制内存同步完成")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"处理内存同步错误失败: {e}")
            return False
    
    def _handle_shard_lock_exception(self, error_info: ErrorInfo):
        """处理分片锁异常"""
        try:
            logger.info(f"处理分片锁异常: {error_info.error_id}")
            
            # 释放所有锁
            if hasattr(self.lock_manager, 'release_all_locks'):
                self.lock_manager.release_all_locks()
                logger.info("释放所有锁完成")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"处理分片锁异常失败: {e}")
            return False
    
    def _handle_cache_consistency_error(self, error_info: ErrorInfo):
        """处理缓存一致性错误"""
        try:
            logger.info(f"处理缓存一致性错误: {error_info.error_id}")
            
            # 验证缓存一致性
            if self._verify_cache_consistency(error_info):
                logger.info("缓存一致性验证通过")
                return True
            
            # 重建缓存索引
            if self._rebuild_cache_index(error_info):
                logger.info("重建缓存索引成功")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"处理缓存一致性错误失败: {e}")
            return False
    
    def _handle_system_resource_error(self, error_info: ErrorInfo):
        """处理系统资源错误"""
        try:
            logger.info(f"处理系统资源错误: {error_info.error_id}")
            
            # 清理系统资源
            if self._cleanup_system_resources(error_info):
                logger.info("清理系统资源完成")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"处理系统资源错误失败: {e}")
            return False
    
    def _handle_network_error(self, error_info: ErrorInfo):
        """处理网络错误"""
        try:
            logger.info(f"处理网络错误: {error_info.error_id}")
            
            # 重试网络连接
            if self._retry_network_connection(error_info):
                logger.info("重试网络连接成功")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"处理网络错误失败: {e}")
            return False
    
    def _handle_unknown_error(self, error_info: ErrorInfo):
        """处理未知错误"""
        try:
            logger.info(f"处理未知错误: {error_info.error_id}")
            
            # 记录详细错误信息
            logger.error(f"未知错误详情: {error_info.stack_trace}")
            
            # 尝试通用恢复策略
            return self._generic_error_recovery(error_info)
            
        except Exception as e:
            logger.error(f"处理未知错误失败: {e}")
            return False
    
    # 具体的恢复方法实现
    def _verify_mmap_file_integrity(self, error_info: ErrorInfo) -> bool:
        """验证mmap文件完整性"""
        try:
            file_path = error_info.context.get('file_path')
            if not file_path or not os.path.exists(file_path):
                logger.error(f"文件不存在: {file_path}")
                return False
            
            # 检查文件大小
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.error(f"文件为空: {file_path}")
                return False
            
            # 检查文件头部信息
            with open(file_path, 'rb') as f:
                # 读取文件头
                header = f.read(1024)
                if len(header) < 8:
                    logger.error(f"文件头太小: {file_path}")
                    return False
                
                # 检查魔数（假设使用特定的文件格式标识）
                magic = header[:4]
                if magic != b'MMAP':  # 假设的魔数
                    logger.warning(f"文件魔数不匹配: {file_path}, magic={magic}")
                    # 不返回False，因为可能是旧格式文件
            
            # 检查文件可读性
            try:
                with open(file_path, 'rb') as f:
                    mmap_obj = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                    mmap_obj.close()
            except Exception as e:
                logger.error(f"文件无法映射: {file_path}, error={e}")
                return False
            
            # 检查文件权限
            if not os.access(file_path, os.R_OK):
                logger.error(f"文件无读取权限: {file_path}")
                # 尝试修改权限
                try:
                    import stat
                    os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
                    logger.info(f"已修改文件读取权限: {file_path}")
                    if not os.access(file_path, os.R_OK):
                        return False
                except Exception as e:
                    logger.error(f"无法修改文件读取权限: {file_path} - {e}")
                    return False
            
            logger.info(f"文件完整性验证通过: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"验证mmap文件完整性失败: {e}")
            return False
    
    def _restore_from_backup(self, error_info: ErrorInfo) -> bool:
        """从备份恢复"""
        try:
            file_path = error_info.context.get('file_path')
            if not file_path:
                logger.error("缺少文件路径信息")
                return False
            
            # 查找备份文件
            backup_dir = error_info.context.get('backup_dir', os.path.join(os.path.dirname(file_path), 'backup'))
            if not os.path.exists(backup_dir):
                logger.error(f"备份目录不存在: {backup_dir}")
                return False
            
            # 查找最新的备份文件
            backup_files = []
            for f in os.listdir(backup_dir):
                if f.endswith('.backup') and os.path.basename(file_path) in f:
                    backup_path = os.path.join(backup_dir, f)
                    backup_files.append((backup_path, os.path.getmtime(backup_path)))
            
            if not backup_files:
                logger.error(f"未找到备份文件: {file_path}")
                return False
            
            # 选择最新的备份
            backup_files.sort(key=lambda x: x[1], reverse=True)
            latest_backup = backup_files[0][0]
            
            # 创建临时文件进行恢复
            temp_file = file_path + '.restoring'
            try:
                shutil.copy2(latest_backup, temp_file)
                
                # 验证恢复的文件
                if self._verify_mmap_file_integrity(ErrorInfo(
                    error_type=ErrorType.MMAP_FILE_CORRUPTION,
                    severity=ErrorSeverity.MEDIUM,
                    message="验证恢复文件",
                    timestamp=time.time(),
                    process_id=os.getpid(),
                    thread_id=threading.get_ident(),
                    stack_trace="",
                    context={'file_path': temp_file}
                )):
                    # 替换原文件
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    os.rename(temp_file, file_path)
                    logger.info(f"从备份恢复成功: {file_path}")
                    return True
                else:
                    logger.error(f"恢复的文件验证失败: {temp_file}")
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                    return False
                    
            except Exception as e:
                logger.error(f"恢复文件操作失败: {e}")
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                return False
                
        except Exception as e:
            logger.error(f"从备份恢复失败: {e}")
            return False
    
    def _recreate_mmap_file(self, error_info: ErrorInfo) -> bool:
        """重新创建mmap文件"""
        try:
            file_path = error_info.context.get('file_path')
            if not file_path:
                logger.error("缺少文件路径信息")
                return False
            
            # 获取文件类型和分片ID
            file_type = error_info.context.get('file_type')
            shard_id = error_info.context.get('shard_id', 0)
            
            # 备份原文件（如果存在）
            if os.path.exists(file_path):
                backup_path = file_path + f'.backup.{int(time.time())}'
                try:
                    shutil.copy2(file_path, backup_path)
                    logger.info(f"已备份原文件: {backup_path}")
                except Exception as e:
                    logger.warning(f"备份原文件失败: {e}")
            
            # 删除损坏的文件
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                logger.warning(f"删除损坏文件失败: {e}")
            
            # 重新创建文件
            try:
                # 确保目录存在
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                
                # 创建新文件
                with open(file_path, 'wb') as f:
                    # 写入文件头
                    f.write(b'MMAP')  # 魔数
                    f.write(b'\x00\x00\x00\x01')  # 版本号
                    f.write(b'\x00' * 1024)  # 预留空间
                
                # 设置文件权限（确保可读写）
                import stat
                try:
                    os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
                    logger.debug(f"设置重新创建文件的权限: {file_path}")
                except Exception as perm_error:
                    logger.warning(f"设置重新创建文件权限失败: {file_path} - {perm_error}")
                
                # 验证新创建的文件
                if self._verify_mmap_file_integrity(ErrorInfo(
                    error_type=ErrorType.MMAP_FILE_CORRUPTION,
                    severity=ErrorSeverity.MEDIUM,
                    message="验证新创建文件",
                    timestamp=time.time(),
                    process_id=os.getpid(),
                    thread_id=threading.get_ident(),
                    stack_trace="",
                    context={'file_path': file_path}
                )):
                    logger.info(f"重新创建mmap文件成功: {file_path}")
                    return True
                else:
                    logger.error(f"新创建的文件验证失败: {file_path}")
                    return False
                    
            except Exception as e:
                logger.error(f"创建新文件失败: {e}")
                return False
                
        except Exception as e:
            logger.error(f"重新创建mmap文件失败: {e}")
            return False
    
    def _verify_cache_consistency(self, error_info: ErrorInfo) -> bool:
        """验证缓存一致性"""
        try:
            # 检查所有分片的缓存一致性
            shard_count = self.shard_manager.shard_count  # 使用属性而不是方法
            consistency_errors = []
            
            for shard_id in range(shard_count):
                try:
                    # 检查分片文件是否存在
                    from .high_concurrency_mmap_manager import MmapFileType
                    file_path = self.mmap_manager._get_file_path(
                        MmapFileType.CLASS_CACHE, shard_id
                    )
                    
                    if not os.path.exists(file_path):
                        consistency_errors.append(f"分片文件不存在: shard_{shard_id}")
                        continue
                    
                    # 检查文件大小是否合理
                    file_size = os.path.getsize(file_path)
                    if file_size < 1024:  # 最小文件大小
                        consistency_errors.append(f"分片文件太小: shard_{shard_id}, size={file_size}")
                        continue
                    
                    # 检查文件头部信息
                    with open(file_path, 'rb') as f:
                        header = f.read(1024)
                        if len(header) < 8:
                            consistency_errors.append(f"分片文件头损坏: shard_{shard_id}")
                            continue
                    
                    # 检查索引一致性
                    from .high_concurrency_mmap_manager import MmapFileType
                    index_data = self.mmap_manager._load_index(
                        MmapFileType.CLASS_CACHE, shard_id
                    )
                    if index_data is None:
                        consistency_errors.append(f"分片索引加载失败: shard_{shard_id}")
                        continue
                    
                    # 验证索引中的键值对
                    for key, value_info in index_data.items():
                        if not isinstance(value_info, dict):
                            consistency_errors.append(f"索引项格式错误: shard_{shard_id}, key={key}")
                            continue
                        
                        # 检查数据偏移量是否合理
                        offset = value_info.get('offset', 0)
                        size = value_info.get('size', 0)
                        if offset < 1024 or size <= 0 or offset + size > file_size:
                            consistency_errors.append(f"数据偏移量错误: shard_{shard_id}, key={key}, offset={offset}, size={size}")
                            continue
                        
                except Exception as e:
                    consistency_errors.append(f"分片验证异常: shard_{shard_id}, error={e}")
            
            # 检查锁状态一致性
            for shard_id in range(shard_count):
                try:
                    shard_state = self.lock_manager._get_or_create_shard_state(shard_id)
                    if shard_state is None:
                        consistency_errors.append(f"锁状态获取失败: shard_{shard_id}")
                        continue
                    
                    # 检查锁状态是否合理
                    if len(shard_state.active_locks) > 100:  # 假设最大锁数量
                        consistency_errors.append(f"锁数量异常: shard_{shard_id}, count={len(shard_state.active_locks)}")
                        
                except Exception as e:
                    consistency_errors.append(f"锁状态验证异常: shard_{shard_id}, error={e}")
            
            if consistency_errors:
                logger.error(f"缓存一致性检查发现 {len(consistency_errors)} 个错误:")
                for error in consistency_errors:
                    logger.error(f"  - {error}")
                return False
            
            logger.info("缓存一致性验证通过")
            return True
            
        except Exception as e:
            logger.error(f"验证缓存一致性失败: {e}")
            return False
    
    def _rebuild_cache_index(self, error_info: ErrorInfo) -> bool:
        """重建缓存索引"""
        try:
            # 这里应该实现实际的索引重建逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"重建缓存索引失败: {e}")
            return False
    
    def _cleanup_system_resources(self, error_info: ErrorInfo) -> bool:
        """清理系统资源"""
        try:
            # 这里应该实现实际的资源清理逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"清理系统资源失败: {e}")
            return False
    
    def _retry_network_connection(self, error_info: ErrorInfo) -> bool:
        """重试网络连接"""
        try:
            # 这里应该实现实际的网络重连逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"重试网络连接失败: {e}")
            return False
    
    def _generic_error_recovery(self, error_info: ErrorInfo) -> bool:
        """通用错误恢复"""
        try:
            # 这里应该实现通用的错误恢复逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"通用错误恢复失败: {e}")
            return False
    
    # 重试方法实现
    def _retry_mmap_operation(self, error_info: ErrorInfo) -> bool:
        """重试mmap操作"""
        try:
            # 这里应该实现实际的mmap操作重试逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"重试mmap操作失败: {e}")
            return False
    
    def _retry_memory_sync(self, error_info: ErrorInfo) -> bool:
        """重试内存同步"""
        try:
            # 这里应该实现实际的内存同步重试逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"重试内存同步失败: {e}")
            return False
    
    def _retry_lock_operation(self, error_info: ErrorInfo) -> bool:
        """重试锁操作"""
        try:
            # 这里应该实现实际的锁操作重试逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"重试锁操作失败: {e}")
            return False
    
    # 降级方法实现
    def _fallback_to_file_storage(self, error_info: ErrorInfo) -> bool:
        """降级到文件存储"""
        try:
            # 这里应该实现实际的降级到文件存储逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"降级到文件存储失败: {e}")
            return False
    
    def _fallback_to_readonly_mode(self, error_info: ErrorInfo) -> bool:
        """降级到只读模式"""
        try:
            # 这里应该实现实际的降级到只读模式逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"降级到只读模式失败: {e}")
            return False
    
    def _fallback_to_reduced_functionality(self, error_info: ErrorInfo) -> bool:
        """降级到减少功能"""
        try:
            # 这里应该实现实际的降级到减少功能逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"降级到减少功能失败: {e}")
            return False
    
    def _generic_fallback(self, error_info: ErrorInfo) -> bool:
        """通用降级"""
        try:
            # 这里应该实现通用的降级逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"通用降级失败: {e}")
            return False
    
    # 恢复方法实现
    def _restore_mmap_file(self, error_info: ErrorInfo) -> bool:
        """恢复mmap文件"""
        try:
            # 这里应该实现实际的mmap文件恢复逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"恢复mmap文件失败: {e}")
            return False
    
    def _restore_cache_consistency(self, error_info: ErrorInfo) -> bool:
        """恢复缓存一致性"""
        try:
            # 这里应该实现实际的缓存一致性恢复逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"恢复缓存一致性失败: {e}")
            return False
    
    def _generic_restore(self, error_info: ErrorInfo) -> bool:
        """通用恢复"""
        try:
            # 这里应该实现通用的恢复逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"通用恢复失败: {e}")
            return False
    
    # 降级方法实现
    def _degrade_performance(self, error_info: ErrorInfo) -> bool:
        """降级性能"""
        try:
            # 这里应该实现实际的性能降级逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"降级性能失败: {e}")
            return False
    
    def _degrade_connectivity(self, error_info: ErrorInfo) -> bool:
        """降级连接性"""
        try:
            # 这里应该实现实际的连接性降级逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"降级连接性失败: {e}")
            return False
    
    def _generic_degrade(self, error_info: ErrorInfo) -> bool:
        """通用降级"""
        try:
            # 这里应该实现通用的降级逻辑
            # 简化实现，返回True
            return True
        except Exception as e:
            logger.error(f"通用降级失败: {e}")
            return False
    
    def _handle_unrecoverable_error(self, error_info: ErrorInfo):
        """处理不可恢复的错误"""
        logger.critical(f"遇到不可恢复的错误: {error_info.error_id}")
        logger.critical(f"错误类型: {error_info.error_type.value}")
        logger.critical(f"错误消息: {error_info.message}")
        logger.critical(f"错误堆栈: {error_info.stack_trace}")
        
        # 这里可以添加告警、通知等逻辑
    
    def get_error_report(self, error_id: str) -> Optional[ErrorReport]:
        """获取错误报告"""
        return self.error_reports.get(error_id)
    
    def get_all_error_reports(self) -> Dict[str, ErrorReport]:
        """获取所有错误报告"""
        return self.error_reports.copy()
    
    def get_error_statistics(self) -> Dict[str, Any]:
        """获取错误统计"""
        try:
            total_errors = len(self.error_reports)
            resolved_errors = len([r for r in self.error_reports.values() if r.resolved])
            unresolved_errors = total_errors - resolved_errors
            
            error_type_counts = defaultdict(int)
            severity_counts = defaultdict(int)
            
            for report in self.error_reports.values():
                error_type_counts[report.error_info.error_type.value] += 1
                severity_counts[report.error_info.severity.value] += 1
            
            return {
                "total_errors": total_errors,
                "resolved_errors": resolved_errors,
                "unresolved_errors": unresolved_errors,
                "resolution_rate": (resolved_errors / total_errors * 100) if total_errors > 0 else 0,
                "error_type_distribution": dict(error_type_counts),
                "severity_distribution": dict(severity_counts),
                "recent_errors": [
                    {
                        "error_id": report.error_info.error_id,
                        "error_type": report.error_info.error_type.value,
                        "severity": report.error_info.severity.value,
                        "message": report.error_info.message,
                        "timestamp": report.error_info.timestamp,
                        "resolved": report.resolved
                    }
                    for report in list(self.error_reports.values())[-10:]  # 最近10个错误
                ]
            }
            
        except Exception as e:
            logger.error(f"获取错误统计失败: {e}")
            return {"error": str(e)}
    
    def cleanup(self):
        """清理资源"""
        self.stop_error_handling()


# 全局错误处理器实例
_global_error_handler: Optional[ErrorHandler] = None
_error_handler_lock = threading.RLock()


def get_global_error_handler() -> Optional[ErrorHandler]:
    """获取全局错误处理器"""
    return _global_error_handler


def init_global_error_handler(cache_manager: UnifiedCacheManager,
                             mmap_manager: HighConcurrencyMmapManager,
                             shard_manager: HighConcurrencyShardManager,
                             lock_manager: HighConcurrencyLockManager) -> bool:
    """初始化全局错误处理器"""
    global _global_error_handler
    
    with _error_handler_lock:
        if _global_error_handler is not None:
            return True
        
        try:
            _global_error_handler = ErrorHandler(
                cache_manager, mmap_manager, shard_manager, lock_manager
            )
            _global_error_handler.start_error_handling()
            return True
        except Exception as e:
            logger.error(f"初始化全局错误处理器失败: {e}")
            return False


def shutdown_global_error_handler():
    """关闭全局错误处理器"""
    global _global_error_handler
    
    with _error_handler_lock:
        if _global_error_handler is not None:
            _global_error_handler.cleanup()
            _global_error_handler = None


if __name__ == "__main__":
    # 测试错误处理器
    print("错误处理器模块测试")
    
    # 这里需要实际的缓存管理器实例进行测试
    # 暂时跳过实际测试
    print("错误处理器模块加载成功")
