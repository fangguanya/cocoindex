"""
分布式文件ID管理器 (v2.4)

提供多进程安全的文件ID分配和映射管理，确保在并行处理环境下的一致性。
"""

from pathlib import Path
from typing import Dict, Optional, List
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
    
    def __init__(self, project_root: str, all_files: List[str]):
        self.logger = get_logger()
        self.project_root = Path(project_root).resolve()
        
        self._path_to_id: Dict[str, str] = {}
        self._id_to_path: Dict[str, str] = {}
        
        self._initialize_mappings(all_files)

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
        self.logger.debug(f"确定性文件管理器初始化完成，共 {len(self._path_to_id)} 个文件。")

    def get_file_id(self, file_path: Optional[str]) -> Optional[str]:
        """获取文件ID"""
        if not file_path:
            return None
        
        normalized_path = self._normalize_path(file_path)
        file_id = self._path_to_id.get(normalized_path)
        # if not file_id:
        #     self.logger.warning(f"未找到文件 '{normalized_path}' 的预分配ID。该文件可能未包含在初始文件列表中。")
        return file_id

    def _normalize_path(self, file_path: str) -> str:
        """将路径标准化为相对于项目根目录的相对路径"""
        try:
            path = Path(file_path)
            if not path.is_absolute():
                path = self.project_root / path
            
            # 解析路径以消除 ".." 等
            resolved_path = path.resolve()
            
            # 计算相对于项目根目录的路径
            relative_path = resolved_path.relative_to(self.project_root)
            
            return str(relative_path).replace('\\', '/')
        except (ValueError, TypeError):
            # 如果路径不在项目根目录下，或路径类型错误，则返回原始的、正斜杠格式的路径
            return file_path.replace('\\', '/')

    def get_file_mappings(self) -> Dict[str, str]:
        """获取所有文件映射（file_id -> path）"""
        return self._id_to_path.copy()

    def get_reverse_mappings(self) -> Dict[str, str]:
        """获取反向文件映射（path -> file_id）"""
        return self._path_to_id.copy()