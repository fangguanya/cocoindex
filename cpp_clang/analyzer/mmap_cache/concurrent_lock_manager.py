#!/usr/bin/env python3
"""
高并发锁管理器 - 分片级别的读写锁和死锁检测
"""

import os
import sys
import time
import threading
import uuid
from typing import Dict, Any, Optional, List, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import logging

# 添加父目录到路径以导入logger
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from logger import get_logger


class LockType(Enum):
    """锁类型枚举"""
    READ = "read"
    WRITE = "write"
    EXCLUSIVE = "exclusive"


class LockStatus(Enum):
    """锁状态枚举"""
    ACQUIRED = "acquired"
    WAITING = "waiting"
    TIMEOUT = "timeout"
    RELEASED = "released"


@dataclass
class LockRequest:
    """锁请求"""
    lock_id: str
    process_id: int
    thread_id: int
    shard_id: int
    lock_type: LockType
    request_time: float
    timeout: float
    status: LockStatus = LockStatus.WAITING
    acquire_time: Optional[float] = None
    release_time: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'lock_id': self.lock_id,
            'process_id': self.process_id,
            'thread_id': self.thread_id,
            'shard_id': self.shard_id,
            'lock_type': self.lock_type.value,
            'request_time': self.request_time,
            'timeout': self.timeout,
            'status': self.status.value,
            'acquire_time': self.acquire_time,
            'release_time': self.release_time
        }


@dataclass
class ShardLockState:
    """分片锁状态"""
    shard_id: int
    write_lock_owner: Optional[str] = None
    write_lock_time: Optional[float] = None
    read_lock_owners: Set[str] = field(default_factory=set)
    waiting_requests: List[LockRequest] = field(default_factory=list)
    lock_count: int = 0
    last_activity: float = field(default_factory=time.time)


class DeadlockDetector:
    """死锁检测器"""
    
    def __init__(self):
        self.logger = get_logger()
        self.detection_interval = 5.0  # 检测间隔（秒）
        self.last_detection_time = time.time()
    
    def detect_deadlock(self, shard_locks: Dict[int, ShardLockState], 
                       current_request: LockRequest) -> List[List[str]]:
        """检测死锁"""
        current_time = time.time()
        if current_time - self.last_detection_time < self.detection_interval:
            return []
        
        self.last_detection_time = current_time
        
        # 构建资源分配图
        resource_graph = self._build_resource_graph(shard_locks, current_request)
        
        # 检测循环
        cycles = self._find_cycles(resource_graph)
        
        if cycles:
            self.logger.warning(f"检测到死锁: {cycles}")
        
        return cycles
    
    def _build_resource_graph(self, shard_locks: Dict[int, ShardLockState], 
                            current_request: LockRequest) -> Dict[str, Set[str]]:
        """构建资源分配图"""
        graph = {}
        
        # 添加当前请求
        current_id = current_request.lock_id
        
        for shard_id, lock_state in shard_locks.items():
            # 写锁持有者
            if lock_state.write_lock_owner:
                if lock_state.write_lock_owner not in graph:
                    graph[lock_state.write_lock_owner] = set()
                
                # 当前请求等待写锁
                if current_request.shard_id == shard_id and current_request.lock_type in [LockType.WRITE, LockType.EXCLUSIVE]:
                    if current_id not in graph:
                        graph[current_id] = set()
                    graph[current_id].add(lock_state.write_lock_owner)
                
                # 读锁持有者等待写锁
                for read_owner in lock_state.read_lock_owners:
                    if read_owner not in graph:
                        graph[read_owner] = set()
                    graph[read_owner].add(lock_state.write_lock_owner)
            
            # 读锁持有者
            for read_owner in lock_state.read_lock_owners:
                if read_owner not in graph:
                    graph[read_owner] = set()
                
                # 当前请求等待读锁
                if current_request.shard_id == shard_id and current_request.lock_type == LockType.READ:
                    if current_id not in graph:
                        graph[current_id] = set()
                    graph[current_id].add(read_owner)
        
        return graph
    
    def _find_cycles(self, graph: Dict[str, Set[str]]) -> List[List[str]]:
        """查找循环"""
        cycles = []
        visited = set()
        rec_stack = set()
        
        def dfs(node: str, path: List[str]):
            if node in rec_stack:
                # 找到循环
                cycle_start = path.index(node)
                cycle = path[cycle_start:] + [node]
                cycles.append(cycle)
                return
            
            if node in visited:
                return
            
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            
            for neighbor in graph.get(node, set()):
                dfs(neighbor, path)
            
            path.pop()
            rec_stack.remove(node)
        
        for node in graph:
            if node not in visited:
                dfs(node, [])
        
        return cycles


class HighConcurrencyLockManager:
    """高并发锁管理器"""
    
    def __init__(self, 
                 project_root: str,
                 default_timeout: float = 30.0,
                 max_waiting_requests: int = 1000,
                 enable_deadlock_detection: bool = True):
        self.logger = get_logger()
        self.project_root = project_root
        self.default_timeout = min(default_timeout, 5.0)  # 限制最大超时时间为5秒
        self.max_waiting_requests = max_waiting_requests
        self.enable_deadlock_detection = enable_deadlock_detection
        
        # 分片锁状态
        self.shard_locks: Dict[int, ShardLockState] = {}
        
        # 全局锁管理
        self._global_lock = threading.RLock()
        self._shard_locks: Dict[int, threading.RLock] = {}
        
        # 死锁检测器
        self.deadlock_detector = DeadlockDetector() if enable_deadlock_detection else None
        
        # 性能统计
        self.stats = {
            'total_requests': 0,
            'successful_acquires': 0,
            'timeout_acquires': 0,
            'fast_failures': 0,  # 快速失败次数
            'deadlock_detections': 0,
            'lock_releases': 0,
            'average_wait_time': 0.0,
            'max_wait_time': 0.0
        }
        
        # 配置参数
        self.process_id = os.getpid()
        self.thread_id = threading.get_ident()
        
        self.logger.info(f"高并发锁管理器初始化完成，默认超时: {default_timeout}s")
    
    def _get_shard_lock(self, shard_id: int) -> threading.RLock:
        """获取分片锁"""
        if shard_id not in self._shard_locks:
            with self._global_lock:
                if shard_id not in self._shard_locks:
                    self._shard_locks[shard_id] = threading.RLock()
        return self._shard_locks[shard_id]
    
    def _get_or_create_shard_state(self, shard_id: int) -> ShardLockState:
        """获取或创建分片状态"""
        if shard_id not in self.shard_locks:
            with self._global_lock:
                if shard_id not in self.shard_locks:
                    self.shard_locks[shard_id] = ShardLockState(shard_id=shard_id)
        return self.shard_locks[shard_id]
    
    def acquire_lock(self, shard_id: int, lock_type: LockType, 
                    timeout: Optional[float] = None) -> Optional[str]:
        """获取锁"""
        if timeout is None:
            timeout = self.default_timeout
        
        lock_id = str(uuid.uuid4())
        request = LockRequest(
            lock_id=lock_id,
            process_id=self.process_id,
            thread_id=self.thread_id,
            shard_id=shard_id,
            lock_type=lock_type,
            request_time=time.time(),
            timeout=timeout
        )
        
        self.stats['total_requests'] += 1
        start_time = time.time()
        
        try:
            shard_lock = self._get_shard_lock(shard_id)
            
            with shard_lock:
                shard_state = self._get_or_create_shard_state(shard_id)
                
                # 检查是否可以立即获取锁
                if self._can_acquire_lock_immediately(shard_state, lock_type):
                    return self._acquire_lock_immediately(shard_state, request)
                
                # 优化：如果无法立即获取锁，根据锁类型决定策略
                if lock_type == LockType.READ:
                    # 读锁：快速失败，避免等待
                    self.stats['fast_failures'] += 1
                    self.logger.debug(f"读锁快速失败: {shard_id}")
                    return None
                else:
                    # 写锁：尝试短暂等待
                    return self._try_wait_for_lock(shard_state, request, min(timeout, 1.0))
                
        except Exception as e:
            self.logger.error(f"获取锁失败: {shard_id} - {e}")
            return None
    
    def _can_acquire_lock_immediately(self, shard_state: ShardLockState, 
                                    lock_type: LockType) -> bool:
        """检查是否可以立即获取锁"""
        current_time = time.time()
        
        # 清理超时的锁
        self._cleanup_expired_locks(shard_state, current_time)
        
        if lock_type == LockType.READ:
            # 读锁：没有写锁时可以获取
            return shard_state.write_lock_owner is None
        elif lock_type == LockType.WRITE:
            # 写锁：没有其他锁时可以获取
            return (shard_state.write_lock_owner is None and 
                   len(shard_state.read_lock_owners) == 0)
        elif lock_type == LockType.EXCLUSIVE:
            # 独占锁：没有其他锁时可以获取
            return (shard_state.write_lock_owner is None and 
                   len(shard_state.read_lock_owners) == 0)
        
        return False
    
    def _acquire_lock_immediately(self, shard_state: ShardLockState, 
                                request: LockRequest) -> str:
        """立即获取锁"""
        current_time = time.time()
        
        if request.lock_type == LockType.READ:
            shard_state.read_lock_owners.add(request.lock_id)
        elif request.lock_type in [LockType.WRITE, LockType.EXCLUSIVE]:
            shard_state.write_lock_owner = request.lock_id
            shard_state.write_lock_time = current_time
        
        request.status = LockStatus.ACQUIRED
        request.acquire_time = current_time
        shard_state.last_activity = current_time
        
        self.stats['successful_acquires'] += 1
        self.logger.debug(f"立即获取锁: {request.lock_id} ({request.lock_type.value})")
        
        return request.lock_id
    
    def _try_wait_for_lock(self, shard_state: ShardLockState, request: LockRequest, timeout: float) -> Optional[str]:
        """尝试等待锁 - 简化版本"""
        start_time = time.time()
        check_interval = 0.01  # 10ms检查间隔
        
        while time.time() - start_time < timeout:
            # 检查是否可以获取锁
            if self._can_acquire_lock_immediately(shard_state, request.lock_type):
                return self._acquire_lock_immediately(shard_state, request)
            
            # 短暂等待
            time.sleep(check_interval)
            check_interval = min(check_interval * 1.1, 0.05)  # 逐渐增加等待时间
        
        # 超时
        self.stats['timeout_acquires'] += 1
        self.logger.debug(f"等待锁超时: {request.lock_id} ({request.lock_type.value})")
        return None
    
    def _wait_for_lock(self, shard_state: ShardLockState, 
                      request: LockRequest, start_time: float) -> Optional[str]:
        """等待锁"""
        current_time = time.time()
        end_time = current_time + request.timeout
        
        # 添加最大重试次数，防止无限循环
        max_retries = int(request.timeout * 1000)  # 每1ms一次重试
        retry_count = 0
        
        while current_time < end_time and retry_count < max_retries:
            # 检查是否可以获取锁
            if self._can_acquire_lock_immediately(shard_state, request.lock_type):
                # 从等待队列中移除
                if request in shard_state.waiting_requests:
                    shard_state.waiting_requests.remove(request)
                
                # 获取锁
                return self._acquire_lock_immediately(shard_state, request)
            
            # 检查是否超时
            if current_time >= end_time:
                request.status = LockStatus.TIMEOUT
                if request in shard_state.waiting_requests:
                    shard_state.waiting_requests.remove(request)
                self.stats['timeout_acquires'] += 1
                self.logger.warning(f"锁获取超时: {request.lock_id}")
                return None
            
            # 等待一段时间后重试
            time.sleep(0.001)  # 1ms
            current_time = time.time()
            retry_count += 1
        
        # 如果达到最大重试次数，返回超时
        request.status = LockStatus.TIMEOUT
        if request in shard_state.waiting_requests:
            shard_state.waiting_requests.remove(request)
        self.stats['timeout_acquires'] += 1
        self.logger.warning(f"锁获取达到最大重试次数: {request.lock_id}")
        return None
    
    def _cleanup_expired_locks(self, shard_state: ShardLockState, current_time: float):
        """清理过期的锁"""
        # 清理过期的写锁
        if (shard_state.write_lock_owner and 
            shard_state.write_lock_time and 
            current_time - shard_state.write_lock_time > self.default_timeout):
            
            self.logger.warning(f"清理过期写锁: {shard_state.write_lock_owner}")
            shard_state.write_lock_owner = None
            shard_state.write_lock_time = None
    
    def release_lock(self, lock_id: str) -> bool:
        """释放锁"""
        try:
            with self._global_lock:
                # 查找锁所在的分片
                target_shard_id = None
                target_shard_state = None
                
                for shard_id, shard_state in self.shard_locks.items():
                    if (shard_state.write_lock_owner == lock_id or 
                        lock_id in shard_state.read_lock_owners):
                        target_shard_id = shard_id
                        target_shard_state = shard_state
                        break
                
                if not target_shard_state:
                    self.logger.debug(f"未找到锁: {lock_id}")
                    return True  # 返回True，因为锁已经不存在
                
                # 释放锁
                if target_shard_state.write_lock_owner == lock_id:
                    target_shard_state.write_lock_owner = None
                    target_shard_state.write_lock_time = None
                elif lock_id in target_shard_state.read_lock_owners:
                    target_shard_state.read_lock_owners.remove(lock_id)
                
                target_shard_state.last_activity = time.time()
                self.stats['lock_releases'] += 1
                
                self.logger.debug(f"释放锁: {lock_id}")
                return True
                
        except Exception as e:
            self.logger.error(f"释放锁失败: {lock_id} - {e}")
            return False
    
    def get_lock_info(self, shard_id: int) -> Optional[Dict[str, Any]]:
        """获取锁信息"""
        if shard_id not in self.shard_locks:
            return None
        
        shard_state = self.shard_locks[shard_id]
        return {
            'shard_id': shard_id,
            'write_lock_owner': shard_state.write_lock_owner,
            'write_lock_time': shard_state.write_lock_time,
            'read_lock_owners': list(shard_state.read_lock_owners),
            'waiting_requests': len(shard_state.waiting_requests),
            'lock_count': shard_state.lock_count,
            'last_activity': shard_state.last_activity
        }
    
    def get_all_lock_info(self) -> Dict[int, Dict[str, Any]]:
        """获取所有锁信息"""
        return {shard_id: self.get_lock_info(shard_id) 
                for shard_id in self.shard_locks.keys()}
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = self.stats.copy()
        
        # 计算平均等待时间
        if stats['total_requests'] > 0:
            stats['average_wait_time'] = stats.get('total_wait_time', 0) / stats['total_requests']
        
        # 添加当前锁状态
        stats['active_locks'] = sum(
            1 for state in self.shard_locks.values()
            if state.write_lock_owner or state.read_lock_owners
        )
        stats['waiting_requests'] = sum(
            len(state.waiting_requests) for state in self.shard_locks.values()
        )
        
        return stats
    
    def get_status(self) -> Dict[str, Any]:
        """获取状态信息（兼容性方法）"""
        return self.get_statistics()
    
    def cleanup_expired_locks(self) -> int:
        """清理所有过期的锁"""
        current_time = time.time()
        cleaned_count = 0
        
        with self._global_lock:
            for shard_state in self.shard_locks.values():
                original_count = (1 if shard_state.write_lock_owner else 0) + len(shard_state.read_lock_owners)
                self._cleanup_expired_locks(shard_state, current_time)
                new_count = (1 if shard_state.write_lock_owner else 0) + len(shard_state.read_lock_owners)
                cleaned_count += original_count - new_count
        
        if cleaned_count > 0:
            self.logger.info(f"清理了 {cleaned_count} 个过期锁")
        
        return cleaned_count
    
    def force_release_all_locks(self) -> int:
        """强制释放所有锁"""
        released_count = 0
        
        with self._global_lock:
            for shard_state in self.shard_locks.values():
                if shard_state.write_lock_owner:
                    shard_state.write_lock_owner = None
                    shard_state.write_lock_time = None
                    released_count += 1
                
                if shard_state.read_lock_owners:
                    released_count += len(shard_state.read_lock_owners)
                    shard_state.read_lock_owners.clear()
                
                shard_state.waiting_requests.clear()
        
        self.logger.warning(f"强制释放了 {released_count} 个锁")
        return released_count


# 全局锁管理器实例
_global_lock_manager: Optional[HighConcurrencyLockManager] = None
_global_lock_manager_lock = threading.RLock()


def get_global_lock_manager(project_root: str) -> HighConcurrencyLockManager:
    """获取全局锁管理器"""
    global _global_lock_manager
    
    if _global_lock_manager is None:
        with _global_lock_manager_lock:
            if _global_lock_manager is None:
                _global_lock_manager = HighConcurrencyLockManager(project_root)
    
    return _global_lock_manager


def init_global_lock_manager(project_root: str, 
                           default_timeout: float = 30.0,
                           enable_deadlock_detection: bool = True) -> HighConcurrencyLockManager:
    """初始化全局锁管理器"""
    global _global_lock_manager
    
    with _global_lock_manager_lock:
        if _global_lock_manager is not None:
            _global_lock_manager.force_release_all_locks()
        
        _global_lock_manager = HighConcurrencyLockManager(
            project_root=project_root,
            default_timeout=default_timeout,
            enable_deadlock_detection=enable_deadlock_detection
        )
    
    return _global_lock_manager


def shutdown_global_lock_manager() -> None:
    """关闭全局锁管理器"""
    global _global_lock_manager
    
    with _global_lock_manager_lock:
        if _global_lock_manager is not None:
            _global_lock_manager.force_release_all_locks()
            _global_lock_manager = None
