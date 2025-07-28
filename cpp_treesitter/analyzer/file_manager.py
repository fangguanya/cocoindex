"""
文件 ID 映射管理器

该模块负责管理文件路径到 ID 的映射，符合 json_format.md v2.3 规范。
主要功能：
1. 生成唯一的文件 ID（f001, f002, ...）
2. 管理文件路径到 ID 的双向映射
3. 支持路径标准化和去重
4. 提供高效的查找接口
5. 线程安全的操作
"""

from pathlib import Path
from typing import Dict, Optional, List
import hashlib
import re
import threading
from threading import RLock


class FileManager:
    """文件 ID 映射管理器"""
    
    def __init__(self, project_root: Optional[str] = None):
        self.file_to_id: Dict[str, str] = {}  # 标准化路径 -> 文件ID
        self.id_to_file: Dict[str, str] = {}  # 文件ID -> 标准化路径
        self._counter = 0
        self._path_cache: Dict[str, str] = {}  # 原始路径 -> 标准化路径的缓存
        self._lock = RLock() # 添加锁
        
        # 新增：项目根目录设置
        self.project_root: Optional[Path] = Path(project_root).resolve() if project_root else None
    
    def get_or_create_file_id(self, file_path: str) -> str:
        """
        获取或创建文件ID
        
        Args:
            file_path: 原始文件路径
            
        Returns:
            文件ID（格式：f001, f002, ...）
        """
        with self._lock: # 使用锁保护共享数据
            # 标准化文件路径
            normalized_path = self._normalize_path(file_path)
            
            # 检查是否已存在
            if normalized_path in self.file_to_id:
                return self.file_to_id[normalized_path]
            
            # 创建新的文件ID
            self._counter += 1
            file_id = f"f{self._counter:03d}"
            
            # 建立双向映射
            self.file_to_id[normalized_path] = file_id
            self.id_to_file[file_id] = normalized_path
            
            # 缓存原始路径映射
            self._path_cache[file_path] = normalized_path
            
            return file_id
    
    def get_file_id(self, file_path: str) -> Optional[str]:
        """
        获取已存在文件的ID（不创建新ID）
        
        Args:
            file_path: 文件路径
            
        Returns:
            文件ID或None
        """
        with self._lock: # 使用锁保护共享数据
            normalized_path = self._normalize_path(file_path)
            return self.file_to_id.get(normalized_path)
    
    def get_file_path(self, file_id: str) -> Optional[str]:
        """
        根据文件ID获取文件路径
        
        Args:
            file_id: 文件ID
            
        Returns:
            标准化的文件路径或None
        """
        with self._lock: # 使用锁保护共享数据
            return self.id_to_file.get(file_id)
    
    def get_file_mappings(self) -> Dict[str, str]:
        """
        获取所有文件映射（用于JSON导出）
        
        Returns:
            字典，key为文件ID，value为文件路径
        """
        with self._lock: # 使用锁保护共享数据
            return self.id_to_file.copy()
    
    def get_reverse_mappings(self) -> Dict[str, str]:
        """
        获取反向文件映射
        
        Returns:
            字典，key为文件路径，value为文件ID
        """
        with self._lock: # 使用锁保护共享数据
            return self.file_to_id.copy()
    
    def register_files(self, file_paths: List[str]) -> Dict[str, str]:
        """
        批量注册文件并生成ID
        
        Args:
            file_paths: 文件路径列表
            
        Returns:
            文件路径到文件ID的映射
        """
        with self._lock: # 使用锁保护共享数据
            result = {}
            for file_path in file_paths:
                file_id = self.get_or_create_file_id(file_path)
                result[file_path] = file_id
            return result
    
    def _normalize_path(self, file_path: str) -> str:
        """
        标准化文件路径 - 如果设置了project_root，则生成相对路径
        
        Args:
            file_path: 原始文件路径
            
        Returns:
            标准化的文件路径（相对于project_root或绝对路径）
        """
        with self._lock: # 使用锁保护共享数据
            # 检查缓存
            if file_path in self._path_cache:
                return self._path_cache[file_path]
            
            try:
                # 使用 pathlib 进行路径标准化
                path_obj = Path(file_path)
                
                # 如果是绝对路径，尝试解析
                if path_obj.is_absolute():
                    try:
                        resolved_path = path_obj.resolve()
                        
                        # 如果设置了project_root，尝试生成相对路径
                        if self.project_root:
                            try:
                                relative_path = resolved_path.relative_to(self.project_root)
                                normalized = str(relative_path)
                            except ValueError:
                                # 文件不在project_root下，使用绝对路径
                                normalized = str(resolved_path)
                        else:
                            normalized = str(resolved_path)
                            
                    except (OSError, RuntimeError):
                        # 如果解析失败，使用原始路径
                        normalized = str(path_obj)
                else:
                    # 相对路径直接标准化
                    normalized = str(path_obj)
                
                # 统一使用正斜杠
                normalized = normalized.replace('\\', '/')
                
                # 移除重复的斜杠
                normalized = re.sub(r'/+', '/', normalized)
                
                # 缓存结果
                self._path_cache[file_path] = normalized
                
                return normalized
                
            except Exception:
                # 如果标准化失败，返回清理后的原始路径
                normalized = file_path.replace('\\', '/').strip()
                self._path_cache[file_path] = normalized
                return normalized
    
    def get_statistics(self) -> Dict[str, int]:
        """
        获取文件管理器统计信息
        
        Returns:
            统计信息字典
        """
        with self._lock: # 使用锁保护共享数据
            return {
                "total_files": len(self.id_to_file),
                "max_file_id": self._counter,
                "cache_size": len(self._path_cache)
            }
    
    def clear(self):
        """清空所有映射"""
        with self._lock: # 使用锁保护共享数据
            self.file_to_id.clear()
            self.id_to_file.clear()
            self._path_cache.clear()
            self._counter = 0
    
    def validate_mappings(self) -> bool:
        """
        验证映射的一致性
        
        Returns:
            如果映射一致则返回True
        """
        with self._lock: # 使用锁保护共享数据
            # 检查双向映射的一致性
            for path, file_id in self.file_to_id.items():
                if self.id_to_file.get(file_id) != path:
                    return False
            
            for file_id, path in self.id_to_file.items():
                if self.file_to_id.get(path) != file_id:
                    return False
            
            return True
    
    def export_mapping_json(self) -> Dict[str, any]:
        """
        导出符合 v2.3 规范的文件映射JSON
        
        Returns:
            符合规范的JSON数据
        """
        with self._lock: # 使用锁保护共享数据
            return {
                "version": "2.3",
                "total_files": len(self.id_to_file),
                "file_mappings": self.get_file_mappings(),
                "mapping_statistics": self.get_statistics()
            }


# 全局文件管理器实例（单例模式）
_global_file_manager: Optional[FileManager] = None


def get_file_manager(project_root: Optional[str] = None) -> FileManager:
    """获取全局文件管理器实例"""
    global _global_file_manager
    if _global_file_manager is None:
        _global_file_manager = FileManager(project_root)
    return _global_file_manager


def reset_file_manager():
    """重置全局文件管理器（主要用于测试）"""
    global _global_file_manager
    _global_file_manager = None 