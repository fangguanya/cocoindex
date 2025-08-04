"""
分布式文件ID管理器 (v2.4)

提供多进程安全的文件ID分配和映射管理，确保在并行处理环境下的一致性。
"""

from pathlib import Path
from typing import Dict, Optional, List
import os
from dataclasses import dataclass

from .logger import get_logger


@dataclass
class FileManagerStats:
    """文件管理器统计信息"""
    total_files: int
    max_file_id: int


class DistributedFileIdManager:
    """
    确定性文件ID管理器 (v2.5)
    - 移除多进程共享状态，改为每个进程独立、确定地生成ID。
    - 依赖于一个预先确定的、排序好的完整文件列表来保证一致性。
    """
    
    def __init__(self, project_root: str, all_files: List[str], shared_counter=None, shared_lock=None):
        # 不在__init__中创建logger，避免序列化问题
        self.project_root = Path(project_root).resolve()
        
        self._path_to_id: Dict[str, str] = {}
        self._id_to_path: Dict[str, str] = {}
        
        # 多进程共享的临时计数器和锁
        self._shared_counter = shared_counter
        self._shared_lock = shared_lock
        
        # 本地计数器作为备份
        self._temp_file_counter = 0
        
        self._initialize_mappings(all_files)
    
    def _get_logger(self):
        """延迟获取logger，避免序列化问题"""
        if not hasattr(self, '_logger'):
            self._logger = get_logger()
        return self._logger
    

    


    def _initialize_mappings(self, all_files: List[str]):
        """根据完整文件列表预先生成所有映射"""
        # 排序以确保确定性
        sorted_files = sorted(list(set(all_files)))
        for i, file_path in enumerate(sorted_files):
            # 使用相对路径进行存储
            relative_path = self._normalize_path(file_path)
            # 使用 'f' 前缀表示这是预先确定的文件, 扩展到4位数字
            file_id = f"f{i+1:04d}"
            self._path_to_id[relative_path] = file_id
            self._id_to_path[file_id] = relative_path
        self._get_logger().debug(f"确定性文件管理器初始化完成，共 {len(self._path_to_id)} 个文件。")

    def get_file_id(self, file_path: Optional[str]) -> Optional[str]:
        """获取文件ID，如果不存在则动态分配临时ID（多进程安全）"""
        if not file_path:
            return None
        
        normalized_path = self._normalize_path(file_path)
        
        # 首先检查预定义的映射
        file_id = self._path_to_id.get(normalized_path)
        if file_id:
            return file_id
        
        # 如果不存在，需要动态分配临时ID（多进程安全）
        if self._shared_lock:
            # 多进程环境：使用共享锁
            with self._shared_lock:
                # 双重检查锁定模式
                file_id = self._path_to_id.get(normalized_path)
                if not file_id:
                    file_id = self._create_temp_file_id_unsafe(normalized_path)
                    self._get_logger().debug(f"动态分配临时文件ID: {normalized_path} -> {file_id}")
        else:
            # 单进程环境：直接分配
            file_id = self._path_to_id.get(normalized_path)
            if not file_id:
                file_id = self._create_temp_file_id_unsafe(normalized_path)
                self._get_logger().debug(f"动态分配临时文件ID: {normalized_path} -> {file_id}")
        
        return file_id
    
    def _create_temp_file_id_unsafe(self, normalized_path: str) -> str:
        """为动态发现的文件创建临时ID（内部使用，假设已加锁）"""
        # 使用多进程安全的计数器分配
        temp_counter = self._get_next_temp_counter()
        temp_id = f"t{temp_counter:04d}"  # t0001, t0002, ...
        
        # 添加到映射表
        self._path_to_id[normalized_path] = temp_id
        self._id_to_path[temp_id] = normalized_path
        
        return temp_id
    
    def _get_next_temp_counter(self) -> int:
        """获取下一个临时计数器值（多进程安全）"""
        if self._shared_counter and self._shared_lock:
            # 使用多进程共享计数器
            with self._shared_lock:
                self._shared_counter.value += 1
                return self._shared_counter.value
        else:
            # 回退到进程内计数器
            self._temp_file_counter += 1
            return self._temp_file_counter
    


    def _normalize_path(self, file_path: str) -> str:
        """将路径标准化为相对于项目根目录的相对路径"""
        try:
            path = Path(file_path)
            if not path.is_absolute():
                path = self.project_root / path
            
            resolved_path = path.resolve()
            relative_path = resolved_path.relative_to(self.project_root)
            return str(relative_path).replace('\\', '/')
        except (ValueError, TypeError):
            try:
                resolved_path = Path(file_path).resolve()
                if not self.project_root.is_absolute():
                     return file_path.replace('\\', '/')

                common_base = Path(os.path.commonpath([str(resolved_path), str(self.project_root)]))
                relative_to_common = resolved_path.relative_to(common_base)
                
                up_parts = self.project_root.relative_to(common_base).parts
                if up_parts == ('.',):
                    up_parts = ()
                
                final_path = Path(*(['..'] * len(up_parts))) / relative_to_common
                return str(final_path).replace('\\', '/')
            except Exception:
                return file_path.replace('\\', '/')

    def get_file_mappings(self) -> Dict[str, str]:
        """获取所有文件映射（file_id -> path）"""
        return self._id_to_path.copy()

    def get_reverse_mappings(self) -> Dict[str, str]:
        """获取反向文件映射（path -> file_id）"""
        return self._path_to_id.copy()

    def get_stats(self) -> Dict[str, int]:
        """获取统计信息"""
        with self._mapping_lock:
            predefined_files = len([k for k in self._id_to_path.keys() if k.startswith('f')])
            temp_files = len([k for k in self._id_to_path.keys() if k.startswith('t')])
            return {
                "total_files": len(self._path_to_id),
                "predefined_files": predefined_files,
                "temp_files": temp_files,
            }