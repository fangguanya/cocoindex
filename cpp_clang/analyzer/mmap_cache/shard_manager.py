#!/usr/bin/env python3
"""
高并发分片管理器 - 支持192进程的分片路由和负载均衡
"""

import os
import sys
import time
import threading
import hashlib
import json
from typing import Dict, Any, Optional, List, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum
import logging

# 添加父目录到路径以导入logger
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from logger import get_logger


class ShardStatus(Enum):
    """分片状态枚举"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    OVERLOADED = "overloaded"
    FAILED = "failed"


@dataclass
class ShardInfo:
    """分片信息"""
    shard_id: int
    status: ShardStatus
    created_time: float
    last_access_time: float
    access_count: int
    data_size: int
    lock_count: int
    error_count: int
    load_factor: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'shard_id': self.shard_id,
            'status': self.status.value,
            'created_time': self.created_time,
            'last_access_time': self.last_access_time,
            'access_count': self.access_count,
            'data_size': self.data_size,
            'lock_count': self.lock_count,
            'error_count': self.error_count,
            'load_factor': self.load_factor
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ShardInfo':
        """从字典创建"""
        return cls(
            shard_id=data['shard_id'],
            status=ShardStatus(data['status']),
            created_time=data['created_time'],
            last_access_time=data['last_access_time'],
            access_count=data['access_count'],
            data_size=data['data_size'],
            lock_count=data['lock_count'],
            error_count=data['error_count'],
            load_factor=data.get('load_factor', 0.0)
        )


class ShardRoutingStrategy(Enum):
    """分片路由策略"""
    HASH_BASED = "hash_based"
    ROUND_ROBIN = "round_robin"
    LOAD_BALANCED = "load_balanced"
    CONSISTENT_HASH = "consistent_hash"


class HighConcurrencyShardManager:
    """高并发分片管理器"""
    
    def __init__(self, 
                 project_root: str,
                 cache_dir: Optional[str] = None,
                 shard_count: int = 64,
                 max_shards: int = 256,
                 routing_strategy: ShardRoutingStrategy = ShardRoutingStrategy.HASH_BASED):
        self.logger = get_logger()
        self.project_root = project_root
        self.cache_dir = cache_dir or os.path.join(project_root, ".mmap_cache")
        self.shard_count = shard_count
        self.max_shards = max_shards
        self.routing_strategy = routing_strategy
        
        # 确保缓存目录存在
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # 分片信息管理
        self.shards: Dict[int, ShardInfo] = {}
        self.active_shards: Set[int] = set()
        self.overloaded_shards: Set[int] = set()
        self.failed_shards: Set[int] = set()
        
        # 路由相关
        self.current_round_robin_index = 0
        self.consistent_hash_ring: Dict[int, int] = {}
        
        # 线程安全
        self._lock = threading.RLock()
        self._shard_locks: Dict[int, threading.RLock] = {}
        
        # 性能统计
        self.stats = {
            'total_requests': 0,
            'successful_routes': 0,
            'failed_routes': 0,
            'shard_creations': 0,
            'shard_failures': 0,
            'load_balancing_events': 0,
            'average_load_factor': 0.0
        }
        
        # 配置参数
        self.load_threshold = 0.8  # 负载阈值
        self.error_threshold = 10  # 错误阈值
        self.rebalance_interval = 60  # 重平衡间隔（秒）
        self.last_rebalance_time = time.time()
        
        # 初始化分片
        self._initialize_shards()
        
        # 优化：默认使用负载均衡策略以更好分散数据
        if routing_strategy == ShardRoutingStrategy.HASH_BASED:
            self.routing_strategy = ShardRoutingStrategy.LOAD_BALANCED
            self.logger.info("优化：将哈希路由策略更改为负载均衡以更好分散数据")
        
        self.logger.info(f"高并发分片管理器初始化完成，分片数: {self.shard_count}, 路由策略: {self.routing_strategy.value}")
    
    def _initialize_shards(self):
        """初始化分片"""
        for i in range(self.shard_count):
            shard_info = ShardInfo(
                shard_id=i,
                status=ShardStatus.ACTIVE,
                created_time=time.time(),
                last_access_time=time.time(),
                access_count=0,
                data_size=0,
                lock_count=0,
                error_count=0,
                load_factor=0.0
            )
            self.shards[i] = shard_info
            self.active_shards.add(i)
            self._shard_locks[i] = threading.RLock()
        
        # 初始化一致性哈希环
        self._build_consistent_hash_ring()
    
    def _build_consistent_hash_ring(self):
        """构建一致性哈希环"""
        self.consistent_hash_ring.clear()
        virtual_nodes = 3  # 每个分片的虚拟节点数
        
        for shard_id in self.active_shards:
            for i in range(virtual_nodes):
                virtual_key = f"shard_{shard_id}_vnode_{i}"
                hash_value = self._calculate_hash(virtual_key)
                self.consistent_hash_ring[hash_value] = shard_id
    
    def _calculate_hash(self, key: str) -> int:
        """计算哈希值"""
        return int(hashlib.md5(key.encode('utf-8')).hexdigest(), 16)
    
    def get_shard_id(self, key: str, cache_type: str = "") -> int:
        """获取分片ID"""
        with self._lock:
            self.stats['total_requests'] += 1
            
            try:
                if self.routing_strategy == ShardRoutingStrategy.HASH_BASED:
                    shard_id = self._hash_based_routing(key, cache_type)
                elif self.routing_strategy == ShardRoutingStrategy.ROUND_ROBIN:
                    shard_id = self._round_robin_routing()
                elif self.routing_strategy == ShardRoutingStrategy.LOAD_BALANCED:
                    shard_id = self._load_balanced_routing()
                elif self.routing_strategy == ShardRoutingStrategy.CONSISTENT_HASH:
                    shard_id = self._consistent_hash_routing(key)
                else:
                    shard_id = self._hash_based_routing(key, cache_type)
                
                # 更新分片访问统计
                if shard_id in self.shards:
                    shard_info = self.shards[shard_id]
                    shard_info.last_access_time = time.time()
                    shard_info.access_count += 1
                    shard_info.load_factor = self._calculate_load_factor(shard_info)
                    
                    # 检查是否需要负载均衡
                    if shard_info.load_factor > self.load_threshold:
                        self._mark_shard_overloaded(shard_id)
                
                self.stats['successful_routes'] += 1
                return shard_id
                
            except Exception as e:
                self.stats['failed_routes'] += 1
                self.logger.error(f"分片路由失败: {key} - {e}")
                # 返回默认分片
                return 0
    
    def _hash_based_routing(self, key: str, cache_type: str) -> int:
        """基于哈希的路由 - 修复版本"""
        combined_key = f"{cache_type}:{key}"
        hash_value = self._calculate_hash(combined_key)
        
        # 防止除零错误
        if not self.active_shards:
            return 0
        
        # 修复：使用总分片数进行哈希，然后检查是否为活跃分片
        base_shard_id = hash_value % self.shard_count
        
        # 如果计算出的分片是活跃的，直接使用
        if base_shard_id in self.active_shards:
            self.logger.debug(f"分片路由: key='{key}', cache_type='{cache_type}', hash={hash_value}, base_shard={base_shard_id}, final_shard={base_shard_id}")
            return base_shard_id
        
        # 如果基础分片不活跃，寻找最近的活跃分片
        active_list = sorted(self.active_shards)
        
        # 找到最近的活跃分片
        for shard_id in active_list:
            if shard_id >= base_shard_id:
                self.logger.debug(f"分片路由: key='{key}', cache_type='{cache_type}', hash={hash_value}, base_shard={base_shard_id}, fallback_shard={shard_id}")
                return shard_id
        
        # 如果找不到，使用第一个活跃分片
        fallback_shard = active_list[0] if active_list else 0
        self.logger.debug(f"分片路由: key='{key}', cache_type='{cache_type}', hash={hash_value}, base_shard={base_shard_id}, fallback_shard={fallback_shard}")
        return fallback_shard
    
    def _round_robin_routing(self) -> int:
        """轮询路由"""
        if not self.active_shards:
            return 0
        
        active_list = list(self.active_shards)
        
        # 防止除零错误
        if not active_list:
            return 0
            
        shard_id = active_list[self.current_round_robin_index % len(active_list)]
        self.current_round_robin_index += 1
        return shard_id
    
    def _load_balanced_routing(self) -> int:
        """负载均衡路由"""
        if not self.active_shards:
            return 0
        
        # 选择负载最低的分片
        try:
            best_shard = min(self.active_shards, 
                            key=lambda sid: self.shards[sid].load_factor)
            return best_shard
        except (ValueError, KeyError):
            # 如果出现错误，返回默认分片
            return 0
    
    def _consistent_hash_routing(self, key: str) -> int:
        """一致性哈希路由 - 优化版本"""
        if not self.consistent_hash_ring:
            return 0
        
        hash_value = self._calculate_hash(key)
        sorted_hashes = sorted(self.consistent_hash_ring.keys())
        
        # 防止除零错误
        if not sorted_hashes:
            return 0
        
        # 使用二分查找优化性能
        import bisect
        pos = bisect.bisect_left(sorted_hashes, hash_value)
        
        if pos < len(sorted_hashes):
            return self.consistent_hash_ring[sorted_hashes[pos]]
        
        # 如果没有找到，返回第一个节点（环形结构）
        return self.consistent_hash_ring[sorted_hashes[0]]
    
    def _calculate_load_factor(self, shard_info: ShardInfo) -> float:
        """计算负载因子"""
        # 基于访问频率、数据大小和锁竞争计算负载
        access_factor = min(shard_info.access_count / 1000, 1.0)
        size_factor = min(shard_info.data_size / (1024 * 1024 * 100), 1.0)  # 100MB基准
        lock_factor = min(shard_info.lock_count / 100, 1.0)
        error_factor = min(shard_info.error_count / self.error_threshold, 1.0)
        
        # 加权平均
        load_factor = (access_factor * 0.3 + 
                      size_factor * 0.3 + 
                      lock_factor * 0.2 + 
                      error_factor * 0.2)
        
        return min(load_factor, 1.0)
    
    def _mark_shard_overloaded(self, shard_id: int):
        """标记分片过载"""
        if shard_id in self.active_shards:
            self.active_shards.remove(shard_id)
            self.overloaded_shards.add(shard_id)
            self.shards[shard_id].status = ShardStatus.OVERLOADED
            self.stats['load_balancing_events'] += 1
            self.logger.warning(f"分片过载: {shard_id}")
    
    def _mark_shard_failed(self, shard_id: int):
        """标记分片失败"""
        if shard_id in self.active_shards:
            self.active_shards.remove(shard_id)
        if shard_id in self.overloaded_shards:
            self.overloaded_shards.remove(shard_id)
        
        self.failed_shards.add(shard_id)
        self.shards[shard_id].status = ShardStatus.FAILED
        self.stats['shard_failures'] += 1
        self.logger.error(f"分片失败: {shard_id}")
    
    def create_shard(self) -> Optional[int]:
        """创建新分片"""
        with self._lock:
            if len(self.shards) >= self.max_shards:
                self.logger.error(f"已达到最大分片数: {self.max_shards}")
                return None
            
            shard_id = len(self.shards)
            shard_info = ShardInfo(
                shard_id=shard_id,
                status=ShardStatus.ACTIVE,
                created_time=time.time(),
                last_access_time=time.time(),
                access_count=0,
                data_size=0,
                lock_count=0,
                error_count=0,
                load_factor=0.0
            )
            
            self.shards[shard_id] = shard_info
            self.active_shards.add(shard_id)
            self._shard_locks[shard_id] = threading.RLock()
            
            # 重建一致性哈希环
            self._build_consistent_hash_ring()
            
            self.stats['shard_creations'] += 1
            self.logger.info(f"创建新分片: {shard_id}")
            return shard_id
    
    def get_shard_lock(self, shard_id: int) -> threading.RLock:
        """获取分片锁"""
        if shard_id not in self._shard_locks:
            with self._lock:
                if shard_id not in self._shard_locks:
                    self._shard_locks[shard_id] = threading.RLock()
        return self._shard_locks[shard_id]
    
    def update_shard_stats(self, shard_id: int, 
                          data_size: Optional[int] = None,
                          lock_count: Optional[int] = None,
                          error_count: Optional[int] = None):
        """更新分片统计信息"""
        if shard_id not in self.shards:
            return
        
        with self._lock:
            shard_info = self.shards[shard_id]
            
            if data_size is not None:
                shard_info.data_size = data_size
            if lock_count is not None:
                shard_info.lock_count = lock_count
            if error_count is not None:
                shard_info.error_count = error_count
                if error_count >= self.error_threshold:
                    self._mark_shard_failed(shard_id)
            
            shard_info.load_factor = self._calculate_load_factor(shard_info)
    
    def rebalance_shards(self) -> bool:
        """重平衡分片"""
        current_time = time.time()
        if current_time - self.last_rebalance_time < self.rebalance_interval:
            return False
        
        with self._lock:
            self.last_rebalance_time = current_time
            
            # 计算平均负载
            if self.active_shards:
                total_load = sum(self.shards[sid].load_factor for sid in self.active_shards)
                self.stats['average_load_factor'] = total_load / len(self.active_shards)
            
            # 检查过载分片是否可以恢复
            recovered_shards = []
            for shard_id in self.overloaded_shards:
                shard_info = self.shards[shard_id]
                if shard_info.load_factor < self.load_threshold * 0.5:  # 负载降低到阈值的一半
                    recovered_shards.append(shard_id)
            
            for shard_id in recovered_shards:
                self.overloaded_shards.remove(shard_id)
                self.active_shards.add(shard_id)
                self.shards[shard_id].status = ShardStatus.ACTIVE
                self.logger.info(f"分片恢复: {shard_id}")
            
            # 重建一致性哈希环
            if recovered_shards:
                self._build_consistent_hash_ring()
            
            self.logger.info(f"分片重平衡完成，活跃分片: {len(self.active_shards)}")
            return True
    
    def get_shard_info(self, shard_id: int) -> Optional[ShardInfo]:
        """获取分片信息"""
        return self.shards.get(shard_id)
    
    def get_all_shard_info(self) -> Dict[int, ShardInfo]:
        """获取所有分片信息"""
        return self.shards.copy()
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            stats = self.stats.copy()
            stats.update({
                'total_shards': len(self.shards),
                'active_shards': len(self.active_shards),
                'overloaded_shards': len(self.overloaded_shards),
                'failed_shards': len(self.failed_shards),
                'shard_distribution': {
                    'active': list(self.active_shards),
                    'overloaded': list(self.overloaded_shards),
                    'failed': list(self.failed_shards)
                }
            })
            return stats
    
    def get_status(self) -> Dict[str, Any]:
        """获取状态信息（兼容性方法）"""
        return self.get_statistics()
    
    def cleanup(self) -> None:
        """清理资源"""
        try:
            # 保存状态
            state_file = os.path.join(self.cache_dir, "shard_manager_state.json")
            self.save_state(state_file)
            
            # 清理分片锁
            self._shard_locks.clear()
            
            # 清理分片信息
            self.shards.clear()
            self.active_shards.clear()
            self.overloaded_shards.clear()
            self.failed_shards.clear()
            
            self.logger.info("高并发分片管理器清理完成")
            
        except Exception as e:
            self.logger.error(f"清理分片管理器失败: {e}")
    
    def save_state(self, file_path: str) -> bool:
        """保存状态到文件"""
        try:
            state = {
                'shards': {str(sid): info.to_dict() for sid, info in self.shards.items()},
                'active_shards': list(self.active_shards),
                'overloaded_shards': list(self.overloaded_shards),
                'failed_shards': list(self.failed_shards),
                'stats': self.stats,
                'current_round_robin_index': self.current_round_robin_index,
                'last_rebalance_time': self.last_rebalance_time
            }
            
            with open(file_path, 'w') as f:
                json.dump(state, f, indent=2)
            
            return True
            
        except Exception as e:
            self.logger.error(f"保存状态失败: {e}")
            return False
    
    def load_state(self, file_path: str) -> bool:
        """从文件加载状态"""
        try:
            if not os.path.exists(file_path):
                return False
            
            with open(file_path, 'r') as f:
                state = json.load(f)
            
            # 恢复分片信息
            self.shards.clear()
            for sid_str, info_dict in state['shards'].items():
                sid = int(sid_str)
                self.shards[sid] = ShardInfo.from_dict(info_dict)
                self._shard_locks[sid] = threading.RLock()
            
            # 恢复集合
            self.active_shards = set(state['active_shards'])
            self.overloaded_shards = set(state['overloaded_shards'])
            self.failed_shards = set(state['failed_shards'])
            
            # 恢复其他状态
            self.stats = state['stats']
            self.current_round_robin_index = state['current_round_robin_index']
            self.last_rebalance_time = state['last_rebalance_time']
            
            # 重建一致性哈希环
            self._build_consistent_hash_ring()
            
            return True
            
        except Exception as e:
            self.logger.error(f"加载状态失败: {e}")
            return False


# 全局分片管理器实例
_global_shard_manager: Optional[HighConcurrencyShardManager] = None
_global_shard_manager_lock = threading.RLock()


def get_global_shard_manager(project_root: str) -> HighConcurrencyShardManager:
    """获取全局分片管理器"""
    global _global_shard_manager
    
    if _global_shard_manager is None:
        with _global_shard_manager_lock:
            if _global_shard_manager is None:
                _global_shard_manager = HighConcurrencyShardManager(project_root)
    
    return _global_shard_manager


def init_global_shard_manager(project_root: str, 
                            shard_count: int = 64,
                            routing_strategy: ShardRoutingStrategy = ShardRoutingStrategy.HASH_BASED) -> HighConcurrencyShardManager:
    """初始化全局分片管理器"""
    global _global_shard_manager
    
    with _global_shard_manager_lock:
        if _global_shard_manager is not None:
            _global_shard_manager.cleanup()
        
        _global_shard_manager = HighConcurrencyShardManager(
            project_root=project_root,
            shard_count=shard_count,
            routing_strategy=routing_strategy
        )
    
    return _global_shard_manager


def shutdown_global_shard_manager() -> None:
    """关闭全局分片管理器"""
    global _global_shard_manager
    
    with _global_shard_manager_lock:
        if _global_shard_manager is not None:
            _global_shard_manager.cleanup()
            _global_shard_manager = None
