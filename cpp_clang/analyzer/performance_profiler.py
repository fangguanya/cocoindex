"""
性能分析器 - 深度性能分析和热点定位

用于定位C++代码分析器中的性能瓶颈，提供详细的时间分析和热点报告。
"""

import time
import functools
import threading
import cProfile
import pstats
import io
import logging
from typing import Dict, List, Any, Optional
from contextlib import contextmanager
from collections import defaultdict, deque

logger = logging.getLogger(__name__)

class PerformanceProfiler:
    """性能分析器 - 提供详细的性能分析功能"""
    
    def __init__(self):
        self._timings: Dict[str, List[float]] = defaultdict(list)
        self._call_counts: Dict[str, int] = defaultdict(int)
        self._active_timers: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._enabled = True
        self._detailed_logs: List[Dict[str, Any]] = []
        
    def enable(self):
        """启用性能分析"""
        self._enabled = True
        
    def disable(self):
        """禁用性能分析"""
        self._enabled = False
        
    @contextmanager
    def timer(self, name: str, details: Optional[Dict[str, Any]] = None):
        """计时器上下文管理器"""
        if not self._enabled:
            yield
            return
            
        start_time = time.perf_counter()
        thread_id = threading.get_ident()
        timer_key = f"{name}_{thread_id}"
        
        try:
            with self._lock:
                self._active_timers[timer_key] = start_time
                self._call_counts[name] += 1
            yield
        finally:
            end_time = time.perf_counter()
            duration = end_time - start_time
            
            with self._lock:
                self._timings[name].append(duration)
                if timer_key in self._active_timers:
                    del self._active_timers[timer_key]
                
                # 记录详细日志
                log_entry = {
                    'name': name,
                    'duration': duration,
                    'start_time': start_time,
                    'end_time': end_time,
                    'thread_id': thread_id,
                    'details': details or {}
                }
                self._detailed_logs.append(log_entry)
    
    def time_function(self, name: Optional[str] = None):
        """函数装饰器，用于自动计时"""
        def decorator(func):
            func_name = name or f"{func.__module__}.{func.__name__}"
            
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                if not self._enabled:
                    return func(*args, **kwargs)
                    
                with self.timer(func_name):
                    return func(*args, **kwargs)
            return wrapper
        return decorator
    
    def get_stats(self) -> Dict[str, Any]:
        """获取性能统计信息"""
        with self._lock:
            stats = {}
            for name, times in self._timings.items():
                if times:
                    stats[name] = {
                        'count': len(times),
                        'total_time': sum(times),
                        'avg_time': sum(times) / len(times),
                        'min_time': min(times),
                        'max_time': max(times),
                        'call_count': self._call_counts[name]
                    }
            return stats
    
    def get_hotspots(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """获取性能热点"""
        stats = self.get_stats()
        hotspots = []
        
        for name, data in stats.items():
            hotspots.append({
                'name': name,
                'total_time': data['total_time'],
                'avg_time': data['avg_time'],
                'count': data['count'],
                'percentage': 0  # 将在后面计算
            })
        
        # 按总时间排序
        hotspots.sort(key=lambda x: x['total_time'], reverse=True)
        
        # 计算百分比
        total_time = sum(h['total_time'] for h in hotspots)
        if total_time > 0:
            for hotspot in hotspots:
                hotspot['percentage'] = (hotspot['total_time'] / total_time) * 100
        
        return hotspots[:top_n]
    
    def print_report(self, show_details: bool = False):
        """打印性能报告"""
        logger.info("\n" + "="*60)
        logger.info("🔍 性能分析报告")
        logger.info("="*60)
        
        hotspots = self.get_hotspots(15)
        if hotspots:
            logger.info("\n📊 性能热点 (按总耗时排序):")
            logger.info("-" * 60)
            logger.info(f"{'函数名':<40} {'总耗时':<10} {'平均':<8} {'调用次数':<8} {'占比':<6}")
            logger.info("-" * 60)
            
            for hotspot in hotspots:
                logger.info(f"{hotspot['name']:<40} "
                      f"{hotspot['total_time']:<10.3f} "
                      f"{hotspot['avg_time']:<8.4f} "
                      f"{hotspot['count']:<8} "
                      f"{hotspot['percentage']:<6.1f}%")
        
        if show_details and self._detailed_logs:
            logger.info(f"\n📝 详细日志 (最近20条):")
            logger.info("-" * 80)
            recent_logs = self._detailed_logs[-20:]
            for log in recent_logs:
                logger.info(f"[{log['start_time']:.3f}] {log['name']}: {log['duration']:.4f}s")
                if log['details']:
                    for key, value in log['details'].items():
                        logger.info(f"  {key}: {value}")
    
    def save_profile(self, filename: str):
        """保存性能分析结果到文件"""
        import json
        
        data = {
            'stats': self.get_stats(),
            'hotspots': self.get_hotspots(50),
            'detailed_logs': self._detailed_logs[-1000:],  # 保存最近1000条
            'timestamp': time.time()
        }
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"性能分析结果已保存到: {filename}")
    
    def clear(self):
        """清空所有统计数据"""
        with self._lock:
            self._timings.clear()
            self._call_counts.clear()
            self._active_timers.clear()
            self._detailed_logs.clear()
    
    def reset(self):
        """重置所有性能数据 (clear的别名)"""
        self.clear()
    
    def get_summary(self) -> Dict[str, Any]:
        """获取性能分析摘要"""
        return self.get_stats()

# 全局性能分析器实例
profiler = PerformanceProfiler()

def profile_function(name: Optional[str] = None):
    """装饰器：自动分析函数性能"""
    return profiler.time_function(name)

@contextmanager
def profile_block(name: str, **details):
    """上下文管理器：分析代码块性能"""
    with profiler.timer(name, details):
        yield

class DetailedLogger:
    """详细日志记录器"""
    
    def __init__(self, name: str):
        self.name = name
        self.start_time = time.perf_counter()
        self.last_checkpoint = self.start_time
        
    def checkpoint(self, message: str, **details):
        """记录检查点"""
        current_time = time.perf_counter()
        elapsed = current_time - self.last_checkpoint
        total_elapsed = current_time - self.start_time
        
        logger.info(f"[{self.name}] {message}")
        logger.info(f"  ⏱️  步骤耗时: {elapsed:.4f}s, 总耗时: {total_elapsed:.4f}s")
        
        if details:
            for key, value in details.items():
                logger.info(f"  📊 {key}: {value}")
        
        self.last_checkpoint = current_time
        return elapsed, total_elapsed
    
    def finish(self, message: str = "完成"):
        """完成并记录总耗时"""
        total_time = time.perf_counter() - self.start_time
        logger.info(f"[{self.name}] {message}")
        logger.info(f"  🎯 总耗时: {total_time:.4f}s")
        return total_time

def create_cprofile_decorator(filename: str):
    """创建cProfile装饰器"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            pr = cProfile.Profile()
            pr.enable()
            try:
                result = func(*args, **kwargs)
            finally:
                pr.disable()
                
                # 保存详细的profile结果
                pr.dump_stats(filename)
                
                # 打印简要统计
                s = io.StringIO()
                ps = pstats.Stats(pr, stream=s)
                ps.sort_stats('cumulative').print_stats(20)
                logger.info(f"\n📊 cProfile 分析结果 (top 20):\n{s.getvalue()}")
                
            return result
        return wrapper
    return decorator