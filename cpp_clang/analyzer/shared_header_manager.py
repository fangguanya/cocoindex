"""
共享头文件管理器 - 解决多进程环境下的头文件去重和线程安全问题
使用文件锁机制实现真正的跨进程状态共享
"""

import os
import time
import threading
import pickle
from pathlib import Path
from typing import Dict, Set, List, Optional, Any
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import hashlib

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
    """共享头文件管理器 - 多进程安全的头文件处理（使用文件锁机制）"""
    
    def __init__(self, project_root: str, cache_dir: Optional[str] = None):
        self.logger = get_logger()
        self.project_root = project_root
        self.cache_dir = cache_dir or os.path.join(project_root, ".header_cache")
        self.process_id = os.getpid()
        self.thread_id = threading.get_ident()
        
        # 确保缓存目录存在
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # 进程内缓存
        self._local_cache: Dict[str, HeaderProcessingInfo] = {}
        self._lock = threading.RLock()
        
        # 共享状态文件路径
        self._shared_state_file = os.path.normpath(os.path.join(self.cache_dir, "shared_headers.pkl"))
        self._lock_file = os.path.normpath(os.path.join(self.cache_dir, "shared_headers.lock"))
        
        # 初始化共享状态
        self._init_shared_state()
    
    def _init_shared_state(self):
        """初始化共享状态"""
        try:
            if os.path.exists(self._shared_state_file):
                # 清理旧的状态文件（如果超过1小时）
                if time.time() - os.path.getmtime(self._shared_state_file) > 3600:
                    os.remove(self._shared_state_file)
                    self.logger.info("清理了过期的头文件状态文件")
        except Exception as e:
            self.logger.warning(f"初始化头文件共享状态时出错: {e}")
    
    def _acquire_file_lock(self, timeout: float = 5.0) -> bool:
        """获取文件锁 - 快速失败避免死锁"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # 使用文件锁机制
                if not os.path.exists(self._lock_file):
                    with open(self._lock_file, 'w') as f:
                        f.write(f"{self.process_id}:{self.thread_id}")
                    return True
                else:
                    # 检查锁文件是否过期（超过5秒就认为死锁）
                    if time.time() - os.path.getmtime(self._lock_file) > 5:
                        try:
                            os.remove(self._lock_file)
                            self.logger.debug("清理了过期的头文件锁文件")
                        except:
                            pass
                        continue
                        
                    # 检查是否是同一个进程持有锁
                    try:
                        with open(self._lock_file, 'r') as f:
                            lock_info = f.read().strip()
                            if lock_info == f"{self.process_id}:{self.thread_id}":
                                return True  # 已经持有锁
                    except:
                        pass
                        
                time.sleep(0.02)  # 更短的等待时间
            except Exception as e:
                self.logger.debug(f"获取头文件锁时出错: {e}")
                time.sleep(0.02)
        
        self.logger.warning(f"获取头文件锁超时 ({timeout}s)")
        return False
    
    def _release_file_lock(self):
        """释放文件锁"""
        try:
            if os.path.exists(self._lock_file):
                os.remove(self._lock_file)
        except Exception:
            pass
    
    def _load_shared_state(self) -> Dict[str, Any]:
        """加载共享状态 - 如果文件不存在返回空状态，如果加载失败则抛异常"""
        if not os.path.exists(self._shared_state_file):
            # 文件不存在是正常情况（首次运行），返回空状态
            self.logger.info(f"头文件共享状态文件不存在，初始化新的共享状态: {self._shared_state_file}")
            return self._create_empty_shared_state()
        
        try:
            # 检查文件大小，如果太小可能是损坏的
            file_size = os.path.getsize(self._shared_state_file)
            if file_size < 10:
                raise RuntimeError(f"头文件共享状态文件损坏（文件大小 {file_size} 字节，小于最小阈值）: {self._shared_state_file}")
            
            with open(self._shared_state_file, 'rb') as f:
                data = pickle.load(f)
                
            # 验证数据结构完整性
            required_keys = {'header_processing_status', 'header_info_cache', 'processing_lock_map', 
                           'path_to_hash_map', 'hash_to_path_map', 'header_stats'}
            if not all(key in data for key in required_keys):
                missing_keys = required_keys - set(data.keys())
                raise RuntimeError(f"头文件共享状态数据结构不完整，缺少必需的键: {missing_keys}")
            
            self.logger.info(f"成功加载头文件共享状态，包含 {len(data.get('header_info_cache', {}))} 个头文件信息")
            return data
            
        except Exception as e:
            # 共享状态加载失败是严重错误，直接抛异常
            error_msg = f"头文件共享状态加载失败，多进程解析功能不可用: {e}"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg) from e
    
    def _create_empty_shared_state(self) -> Dict[str, Any]:
        """创建空的共享状态结构"""
        return {
            'header_processing_status': {},    # path_hash -> 'pending'|'processing'|'processed'|'failed'
            'header_info_cache': {},           # path_hash -> HeaderProcessingInfo数据
            'processing_lock_map': {},         # path_hash -> (process_id, thread_id, timestamp)
            'path_to_hash_map': {},            # normalized_path -> path_hash
            'hash_to_path_map': {},            # path_hash -> normalized_path
            'header_stats': {
                'total_requests': 0,
                'cache_hits': 0,
                'cache_misses': 0,
                'successful_processing': 0,
                'failed_processing': 0,
                'duplicate_processing_prevented': 0,
                'concurrent_conflicts': 0
            },
            'active_processes': {},            # process_id -> timestamp
            'cleanup_timestamp': time.time()
        }
    
    def _save_shared_state(self, state: Dict[str, Any]):
        """保存共享状态"""
        try:
            with open(self._shared_state_file, 'wb') as f:
                pickle.dump(state, f)
        except Exception as e:
            self.logger.warning(f"保存头文件共享状态失败: {e}")
    
    def _generate_path_hash(self, file_path: str, compile_args: List[str]) -> str:
        """生成路径的唯一哈希值（基于路径和编译参数）"""
        normalized_path = str(Path(file_path).resolve()).replace('\\', '/')
        
        # 组合路径和编译参数
        content_to_hash = f"{normalized_path}|{'|'.join(sorted(compile_args))}"
        return hashlib.md5(content_to_hash.encode('utf-8')).hexdigest()
    
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
        path_hash = self._generate_path_hash(file_path, compile_args)
        current_time = time.time()
        
        # 计算文件内容哈希
        file_hash = self._calculate_header_hash(file_path, compile_args)
        
        self.logger.debug(f"注册头文件处理: {Path(file_path).name} (hash: {path_hash})")
        
        # 先检查本地缓存
        with self._lock:
            if path_hash in self._local_cache:
                cached_info = self._local_cache[path_hash]
                if cached_info.hash_value == file_hash and cached_info.is_processed:
                    self.logger.debug(f"头文件已在本地缓存中处理: {Path(file_path).name}")
                    return False
        
        # 获取文件锁并检查共享状态
        if not self._acquire_file_lock():
            self.logger.warning(f"无法获取文件锁，跳过头文件: {Path(file_path).name}")
            return False
        
        try:
            shared_state = self._load_shared_state()
            
            # 更新统计
            shared_state['header_stats']['total_requests'] += 1
            
            # 检查是否已经被其他进程处理
            status = shared_state['header_processing_status'].get(path_hash)
            if status == 'processed':
                cached_info = shared_state['header_info_cache'].get(path_hash)
                if cached_info and cached_info.get('hash_value') == file_hash:
                    shared_state['header_stats']['cache_hits'] += 1
                    self.logger.debug(f"头文件已被其他进程处理: {Path(file_path).name}")
                    
                    # 更新本地缓存
                    processing_info = HeaderProcessingInfo(
                        file_path=normalized_path,
                        compile_args=compile_args,
                        directory=directory,
                        process_id=cached_info.get('process_id', 0),
                        timestamp=cached_info.get('timestamp', current_time),
                        hash_value=file_hash,
                        is_processed=True
                    )
                    with self._lock:
                        self._local_cache[path_hash] = processing_info
                    
                    self._save_shared_state(shared_state)
                    return False
            
            # 如果正在被其他进程处理（时间戳较新且未完成）
            lock_info = shared_state['processing_lock_map'].get(path_hash)
            if lock_info:
                lock_process_id, lock_thread_id, lock_timestamp = lock_info
                
                # 检查锁是否超时（避免死锁）
                if current_time - lock_timestamp > 300:  # 5分钟超时
                    self.logger.warning(f"头文件处理锁超时，强制释放: {Path(file_path).name}")
                    self._force_release_lock_unsafe(shared_state, path_hash)
                else:
                    # 锁仍有效，无法获取
                    if lock_process_id != self.process_id or lock_thread_id != self.thread_id:
                        shared_state['header_stats']['concurrent_conflicts'] += 1
                        self.logger.debug(f"头文件正在被其他进程处理: {lock_process_id}/{lock_thread_id}")
                        self._save_shared_state(shared_state)
                        return False
            
            # 注册当前进程处理这个头文件
            processing_info = HeaderProcessingInfo(
                file_path=normalized_path,
                compile_args=compile_args,
                directory=directory,
                process_id=self.process_id,
                timestamp=current_time,
                hash_value=file_hash,
                is_processed=False
            )
            
            shared_state['processing_lock_map'][path_hash] = (
                self.process_id, self.thread_id, current_time
            )
            shared_state['header_processing_status'][path_hash] = 'processing'
            shared_state['path_to_hash_map'][normalized_path] = path_hash
            shared_state['hash_to_path_map'][path_hash] = normalized_path
            
            # 保存头文件信息
            shared_state['header_info_cache'][path_hash] = {
                'file_path': normalized_path,
                'compile_args': compile_args,
                'directory': directory,
                'process_id': self.process_id,
                'timestamp': current_time,
                'hash_value': file_hash,
                'is_processed': False
            }
            
            self._save_shared_state(shared_state)
            
            # 更新本地缓存
            with self._lock:
                self._local_cache[path_hash] = processing_info
            
            self.logger.debug(f"注册头文件处理: {Path(file_path).name} (进程 {self.process_id})")
            return True
            
        finally:
            self._release_file_lock()
    
    def mark_header_processed(self, file_path: str, success: bool = True):
        """标记头文件处理完成"""
        normalized_path = str(Path(file_path).resolve()).replace('\\', '/')
        
        # 更新本地缓存
        with self._lock:
            for path_hash, cached_info in self._local_cache.items():
                if cached_info.file_path == normalized_path:
                    cached_info.is_processed = success
                    cached_info.timestamp = time.time()
                    break
        
        # 更新共享状态
        if self._acquire_file_lock():
            try:
                shared_state = self._load_shared_state()
                
                # 找到对应的path_hash
                path_hash = shared_state['path_to_hash_map'].get(normalized_path)
                if path_hash and path_hash in shared_state['processing_lock_map']:
                    lock_info = shared_state['processing_lock_map'][path_hash]
                    lock_process_id, lock_thread_id, lock_timestamp = lock_info
                    
                    if lock_process_id == self.process_id:
                        # 更新状态
                        status = 'processed' if success else 'failed'
                        shared_state['header_processing_status'][path_hash] = status
                        
                        # 更新头文件信息
                        if path_hash in shared_state['header_info_cache']:
                            header_info = shared_state['header_info_cache'][path_hash]
                            header_info['is_processed'] = success
                            header_info['timestamp'] = time.time()
                        
                        # 释放处理锁
                        del shared_state['processing_lock_map'][path_hash]
                        
                        # 更新统计
                        if success:
                            shared_state['header_stats']['successful_processing'] += 1
                        else:
                            shared_state['header_stats']['failed_processing'] += 1
                        
                        self._save_shared_state(shared_state)
                        self.logger.debug(f"标记头文件处理完成: {Path(file_path).name} (成功: {success})")
                
            finally:
                self._release_file_lock()
    
    def get_processed_headers(self) -> Set[str]:
        """获取所有已处理的头文件列表"""
        processed_headers = set()
        
        # 从本地缓存获取
        with self._lock:
            for cached_info in self._local_cache.values():
                if cached_info.is_processed:
                    processed_headers.add(cached_info.file_path)
        
        # 从共享状态获取
        if self._acquire_file_lock():
            try:
                shared_state = self._load_shared_state()
                for path_hash, status in shared_state['header_processing_status'].items():
                    if status == 'processed':
                        # 从hash映射回路径
                        normalized_path = shared_state['hash_to_path_map'].get(path_hash)
                        if normalized_path:
                            processed_headers.add(normalized_path)
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
            "current_process_id": self.process_id
        }
        
        # 统计本地缓存
        with self._lock:
            stats["local_processed"] = sum(1 for info in self._local_cache.values() if info.is_processed)
        
        # 统计共享状态
        if self._acquire_file_lock():
            try:
                shared_state = self._load_shared_state()
                stats.update(shared_state['header_stats'])
                stats["shared_total"] = len(shared_state['header_info_cache'])
                
                # 统计各种状态的数量
                for status in shared_state['header_processing_status'].values():
                    if status == 'processed':
                        stats["shared_processed"] += 1
                
            finally:
                self._release_file_lock()
        
        return stats
    
    def _force_release_lock_unsafe(self, shared_state: Dict[str, Any], path_hash: str):
        """强制释放锁（假设已获取文件锁）"""
        try:
            if path_hash in shared_state['processing_lock_map']:
                del shared_state['processing_lock_map'][path_hash]
            
            status = shared_state['header_processing_status'].get(path_hash)
            if status == 'processing':
                shared_state['header_processing_status'][path_hash] = 'failed'
                
        except Exception as e:
            self.logger.debug(f"强制释放锁失败: {e}")
    
    def cleanup_expired_entries(self, max_age_hours: float = 24.0):
        """清理过期的条目"""
        if not self._acquire_file_lock():
            return
        
        try:
            shared_state = self._load_shared_state()
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600
            
            expired_hashes = []
            for path_hash, header_info in shared_state['header_info_cache'].items():
                if current_time - header_info.get('timestamp', 0) > max_age_seconds:
                    expired_hashes.append(path_hash)
            
            for path_hash in expired_hashes:
                # 清理各种映射
                shared_state['header_info_cache'].pop(path_hash, None)
                shared_state['header_processing_status'].pop(path_hash, None)
                shared_state['processing_lock_map'].pop(path_hash, None)
                
                # 清理路径映射
                normalized_path = shared_state['hash_to_path_map'].get(path_hash)
                if normalized_path:
                    shared_state['path_to_hash_map'].pop(normalized_path, None)
                shared_state['hash_to_path_map'].pop(path_hash, None)
            
            if expired_hashes:
                self._save_shared_state(shared_state)
                self.logger.info(f"清理了 {len(expired_hashes)} 个过期的头文件条目")
                
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