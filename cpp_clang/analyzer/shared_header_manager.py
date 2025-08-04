"""
共享头文件管理器 - 解决多进程环境下的头文件去重和线程安全问题
"""

import os
import time
import threading
import multiprocessing
from pathlib import Path
from typing import Dict, Set, List, Optional, Any
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import hashlib
import pickle

from .logger import get_logger
from .performance_profiler import profiler


@dataclass
class HeaderProcessingInfo:
    """头文件处理信息"""
    file_path: str
    compile_args: List[str]
    directory: str
    process_id: int
    timestamp: float
    hash_value: str
    is_processed: bool = False


class SharedHeaderManager:
    """共享头文件管理器 - 多进程安全的头文件处理"""
    
    def __init__(self, project_root: str, cache_dir: Optional[str] = None):
        self.logger = get_logger()
        self.project_root = project_root
        self.cache_dir = cache_dir or os.path.join(project_root, ".header_cache")
        
        # 确保缓存目录存在
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # 进程内缓存
        self._local_cache: Dict[str, HeaderProcessingInfo] = {}
        self._lock = threading.RLock()
        
        # 共享状态文件路径
        self._shared_state_file = os.path.join(self.cache_dir, "shared_headers.pkl")
        self._lock_file = os.path.join(self.cache_dir, "shared_headers.lock")
        
        # 初始化共享状态
        self._init_shared_state()
    
    def _init_shared_state(self):
        """初始化共享状态"""
        try:
            if os.path.exists(self._shared_state_file):
                # 清理旧的状态文件（如果超过1小时）
                if time.time() - os.path.getmtime(self._shared_state_file) > 3600:
                    os.remove(self._shared_state_file)
                    self.logger.info("清理了过期的共享状态文件")
        except Exception as e:
            self.logger.warning(f"初始化共享状态时出错: {e}")
    
    def _acquire_file_lock(self, timeout: float = 30.0) -> bool:
        """获取文件锁"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # 使用文件锁机制
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
    
    def _load_shared_state(self) -> Dict[str, HeaderProcessingInfo]:
        """加载共享状态"""
        try:
            if os.path.exists(self._shared_state_file):
                with open(self._shared_state_file, 'rb') as f:
                    return pickle.load(f)
        except Exception as e:
            self.logger.debug(f"加载共享状态失败: {e}")
        return {}
    
    def _save_shared_state(self, state: Dict[str, HeaderProcessingInfo]):
        """保存共享状态"""
        try:
            with open(self._shared_state_file, 'wb') as f:
                pickle.dump(state, f)
        except Exception as e:
            self.logger.warning(f"保存共享状态失败: {e}")
    
    def _calculate_header_hash(self, file_path: str, compile_args: List[str]) -> str:
        """计算头文件的哈希值（基于文件内容和编译参数）"""
        try:
            # 读取文件内容
            with open(file_path, 'rb') as f:
                file_content = f.read()
            
            # 组合文件内容和编译参数
            content_to_hash = file_content + '|'.join(sorted(compile_args)).encode('utf-8')
            
            # 计算SHA256哈希
            return hashlib.sha256(content_to_hash).hexdigest()
            
        except Exception as e:
            self.logger.warning(f"计算文件哈希失败 {file_path}: {e}")
            # 如果无法读取文件，使用文件路径和时间戳
            fallback_content = f"{file_path}|{os.path.getmtime(file_path) if os.path.exists(file_path) else 0}"
            return hashlib.sha256(fallback_content.encode('utf-8')).hexdigest()
    
    def register_header_for_processing(self, file_path: str, compile_args: List[str], 
                                     directory: str) -> bool:
        """
        注册头文件进行处理
        返回True表示当前进程应该处理这个头文件
        返回False表示其他进程已经在处理或已经处理完成
        """
        normalized_path = str(Path(file_path).resolve()).replace('\\', '/')
        current_process_id = os.getpid()
        current_time = time.time()
        
        # 计算文件哈希
        file_hash = self._calculate_header_hash(file_path, compile_args)
        
        # 先检查本地缓存
        with self._lock:
            if normalized_path in self._local_cache:
                cached_info = self._local_cache[normalized_path]
                if cached_info.hash_value == file_hash and cached_info.is_processed:
                    self.logger.debug(f"头文件已在本地缓存中处理: {Path(file_path).name}")
                    return False
        
        # 获取文件锁并检查共享状态
        if not self._acquire_file_lock():
            self.logger.warning(f"无法获取文件锁，跳过头文件: {Path(file_path).name}")
            return False
        
        try:
            shared_state = self._load_shared_state()
            
            # 检查是否已经被其他进程处理
            if normalized_path in shared_state:
                existing_info = shared_state[normalized_path]
                
                # 如果哈希值相同且已处理，跳过
                if existing_info.hash_value == file_hash and existing_info.is_processed:
                    self.logger.debug(f"头文件已被进程 {existing_info.process_id} 处理: {Path(file_path).name}")
                    
                    # 更新本地缓存
                    with self._lock:
                        self._local_cache[normalized_path] = existing_info
                    
                    return False
                
                # 如果正在被其他进程处理（时间戳较新且未完成）
                if (not existing_info.is_processed and 
                    existing_info.process_id != current_process_id and
                    current_time - existing_info.timestamp < 300):  # 5分钟超时
                    self.logger.debug(f"头文件正在被进程 {existing_info.process_id} 处理: {Path(file_path).name}")
                    return False
            
            # 注册当前进程处理这个头文件
            processing_info = HeaderProcessingInfo(
                file_path=normalized_path,
                compile_args=compile_args,
                directory=directory,
                process_id=current_process_id,
                timestamp=current_time,
                hash_value=file_hash,
                is_processed=False
            )
            
            shared_state[normalized_path] = processing_info
            self._save_shared_state(shared_state)
            
            # 更新本地缓存
            with self._lock:
                self._local_cache[normalized_path] = processing_info
            
            self.logger.debug(f"注册头文件处理: {Path(file_path).name} (进程 {current_process_id})")
            return True
            
        finally:
            self._release_file_lock()
    
    def mark_header_processed(self, file_path: str, success: bool = True):
        """标记头文件处理完成"""
        normalized_path = str(Path(file_path).resolve()).replace('\\', '/')
        current_process_id = os.getpid()
        
        # 更新本地缓存
        with self._lock:
            if normalized_path in self._local_cache:
                self._local_cache[normalized_path].is_processed = success
                self._local_cache[normalized_path].timestamp = time.time()
        
        # 更新共享状态
        if self._acquire_file_lock():
            try:
                shared_state = self._load_shared_state()
                
                if normalized_path in shared_state:
                    info = shared_state[normalized_path]
                    if info.process_id == current_process_id:
                        info.is_processed = success
                        info.timestamp = time.time()
                        shared_state[normalized_path] = info
                        self._save_shared_state(shared_state)
                        
                        self.logger.debug(f"标记头文件处理完成: {Path(file_path).name} (成功: {success})")
                
            finally:
                self._release_file_lock()
    
    def get_processed_headers(self) -> Set[str]:
        """获取所有已处理的头文件列表"""
        processed_headers = set()
        
        # 从本地缓存获取
        with self._lock:
            for path, info in self._local_cache.items():
                if info.is_processed:
                    processed_headers.add(path)
        
        # 从共享状态获取
        if self._acquire_file_lock():
            try:
                shared_state = self._load_shared_state()
                for path, info in shared_state.items():
                    if info.is_processed:
                        processed_headers.add(path)
            finally:
                self._release_file_lock()
        
        return processed_headers
    
    def get_processing_statistics(self) -> Dict[str, Any]:
        """获取处理统计信息"""
        stats = {
            "local_cache_size": len(self._local_cache),
            "local_processed": 0,
            "shared_total": 0,
            "shared_processed": 0,
            "current_process_id": os.getpid()
        }
        
        # 统计本地缓存
        with self._lock:
            stats["local_processed"] = sum(1 for info in self._local_cache.values() if info.is_processed)
        
        # 统计共享状态
        if self._acquire_file_lock():
            try:
                shared_state = self._load_shared_state()
                stats["shared_total"] = len(shared_state)
                stats["shared_processed"] = sum(1 for info in shared_state.values() if info.is_processed)
            finally:
                self._release_file_lock()
        
        return stats
    
    def cleanup_expired_entries(self, max_age_hours: float = 24.0):
        """清理过期的条目"""
        if not self._acquire_file_lock():
            return
        
        try:
            shared_state = self._load_shared_state()
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600
            
            expired_keys = []
            for path, info in shared_state.items():
                if current_time - info.timestamp > max_age_seconds:
                    expired_keys.append(path)
            
            for key in expired_keys:
                del shared_state[key]
            
            if expired_keys:
                self._save_shared_state(shared_state)
                self.logger.info(f"清理了 {len(expired_keys)} 个过期的头文件条目")
                
        finally:
            self._release_file_lock()
    
    def clear_all_cache(self):
        """清理所有缓存"""
        # 清理本地缓存
        with self._lock:
            self._local_cache.clear()
        
        # 清理共享状态
        if self._acquire_file_lock():
            try:
                if os.path.exists(self._shared_state_file):
                    os.remove(self._shared_state_file)
                self.logger.info("清理了所有头文件缓存")
            finally:
                self._release_file_lock()


class ThreadSafeHeaderProcessor:
    """线程安全的头文件处理器"""
    
    def __init__(self, shared_manager: SharedHeaderManager, max_workers: int = 4):
        self.shared_manager = shared_manager
        self.max_workers = max_workers
        self.logger = get_logger()
        self._processing_lock = threading.RLock()
        self._active_headers: Set[str] = set()
    
    def process_headers_batch(self, headers_info: List[Dict[str, Any]], 
                            processing_func) -> Dict[str, Any]:
        """批量处理头文件（线程安全）"""
        results = {}
        
        # 过滤出需要处理的头文件
        headers_to_process = []
        for header_info in headers_info:
            file_path = header_info['file_path']
            compile_args = header_info.get('compile_args', [])
            directory = header_info.get('directory', '')
            
            if self.shared_manager.register_header_for_processing(file_path, compile_args, directory):
                headers_to_process.append(header_info)
            else:
                # 头文件已被处理，记录跳过
                results[file_path] = {'status': 'skipped', 'reason': 'already_processed'}
        
        if not headers_to_process:
            return results
        
        self.logger.info(f"批量处理 {len(headers_to_process)} 个头文件（跳过 {len(headers_info) - len(headers_to_process)} 个已处理）")
        
        # 使用线程池并行处理
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_header = {
                executor.submit(self._process_single_header, header_info, processing_func): header_info
                for header_info in headers_to_process
            }
            
            for future in future_to_header:
                header_info = future_to_header[future]
                file_path = header_info['file_path']
                
                try:
                    result = future.result(timeout=60)  # 60秒超时
                    results[file_path] = result
                    self.shared_manager.mark_header_processed(file_path, True)
                    
                except Exception as e:
                    self.logger.error(f"处理头文件失败 {file_path}: {e}")
                    results[file_path] = {'status': 'error', 'error': str(e)}
                    self.shared_manager.mark_header_processed(file_path, False)
        
        return results
    
    def _process_single_header(self, header_info: Dict[str, Any], processing_func) -> Dict[str, Any]:
        """处理单个头文件"""
        file_path = header_info['file_path']
        
        # 检查是否正在被当前线程处理
        with self._processing_lock:
            if file_path in self._active_headers:
                return {'status': 'skipped', 'reason': 'already_processing_in_thread'}
            self._active_headers.add(file_path)
        
        try:
            # 调用实际的处理函数
            result = processing_func(header_info)
            return result
            
        finally:
            # 清理活跃头文件集合
            with self._processing_lock:
                self._active_headers.discard(file_path)


# 全局共享管理器实例（用于多进程环境）
_global_header_manager: Optional[SharedHeaderManager] = None


def get_shared_header_manager(project_root: str) -> SharedHeaderManager:
    """获取全局共享头文件管理器"""
    global _global_header_manager
    
    if _global_header_manager is None:
        _global_header_manager = SharedHeaderManager(project_root)
    
    return _global_header_manager


def init_shared_header_manager(project_root: str, cache_dir: Optional[str] = None):
    """初始化共享头文件管理器（在worker进程中调用）"""
    global _global_header_manager
    _global_header_manager = SharedHeaderManager(project_root, cache_dir)
    return _global_header_manager