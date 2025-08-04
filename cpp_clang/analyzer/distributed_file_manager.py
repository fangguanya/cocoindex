"""
分布式文件ID管理器 (v3.0) - 多进程安全版本

提供完全多进程安全的文件ID分配和映射管理，解决序列化问题。
基于 multiprocessing.Manager 实现真正的进程间共享状态。
"""

from pathlib import Path
from typing import Dict, Optional, List
import os
import multiprocessing
import json
import platform
from dataclasses import dataclass

# Windows平台multiprocessing设置
if platform.system() == 'Windows':
    try:
        if multiprocessing.get_start_method(allow_none=True) is None:
            multiprocessing.set_start_method('spawn', force=False)
    except RuntimeError:
        pass


@dataclass
class FileManagerStats:
    """文件管理器统计信息"""
    total_files: int
    predefined_files: int
    temp_files: int
    max_file_id: int


class DistributedFileIdManager:
    """
    多进程共享对象的确定性文件ID管理器 (v5.0)
    - 内部自动管理多进程共享状态
    - 使用类级别的单例Manager确保所有实例共享同一个状态
    - 简化API，无需外部传递共享对象
    """
    
    # 类级别的共享Manager和对象（所有实例共享）
    _class_manager = None
    _class_shared_objects = None
    _class_lock = None
    
    @classmethod
    def _ensure_class_manager(cls):
        """确保类级别的Manager已初始化（单例模式）"""
        if cls._class_manager is None:
            try:
                cls._class_manager = multiprocessing.Manager()
                cls._class_shared_objects = {
                    'path_to_id': cls._class_manager.dict(),
                    'id_to_path': cls._class_manager.dict(),
                    'predefined_counter': cls._class_manager.Value('i', 0),
                    'temp_counter': cls._class_manager.Value('i', 0),
                    'stats': cls._class_manager.dict()
                }
                cls._class_lock = cls._class_manager.Lock()
                
                # 初始化统计信息
                with cls._class_lock:
                    if 'initialized' not in cls._class_shared_objects['stats']:
                        cls._class_shared_objects['stats']['predefined_files'] = 0
                        cls._class_shared_objects['stats']['temp_files'] = 0
                        cls._class_shared_objects['stats']['total_files'] = 0
                        cls._class_shared_objects['stats']['reused_ids'] = 0
                        cls._class_shared_objects['stats']['initialized'] = True
                        
            except Exception as e:
                # 如果在子进程中无法创建Manager，使用本地状态
                print(f"警告: 无法初始化Manager (可能在子进程中): {e}")
                cls._class_manager = None
                cls._class_shared_objects = None
                cls._class_lock = None
    
    def __init__(self, project_root: str, all_files: List[str] = None):
        """
        初始化分布式文件ID管理器
        
        Args:
            project_root: 项目根目录
            all_files: 预分配的文件列表（可选）
        """
        # 确保类级别的Manager已初始化
        self._ensure_class_manager()
        
        self.project_root = Path(project_root).resolve()
        
        # 使用类级别的共享对象或本地状态（如果在子进程中）
        if self._class_shared_objects is not None:
            self._shared_path_to_id = self._class_shared_objects['path_to_id']
            self._shared_id_to_path = self._class_shared_objects['id_to_path']
            self._shared_predefined_counter = self._class_shared_objects['predefined_counter']
            self._shared_temp_counter = self._class_shared_objects['temp_counter']
            self._shared_lock = self._class_lock
            self._shared_stats = self._class_shared_objects['stats']
        else:
            raise RuntimeError("DistributedFileIdManager :找不到共享数据！")
        
        # 如果提供了文件列表，进行预分配（确保只初始化一次）
        if all_files and self._shared_lock is not None:
            self._initialize_predefined_mappings(all_files)
    
    def _safe_lock_operation(self, operation_func, fallback_func=None):
        """安全执行需要锁的操作"""
        if self._shared_lock is not None:
            with self._shared_lock:
                return operation_func()
        elif fallback_func:
            return fallback_func()
        else:
            return None
    
    def _initialize_predefined_mappings(self, all_files: List[str]):
        """根据完整文件列表预先生成所有映射（多进程共享确定性算法）"""
        def init_operation():
            # 检查是否已经初始化过预定义文件
            if self._shared_predefined_counter.value > 0:
                return  # 已经初始化过，避免重复
            
            # 排序以确保确定性
            sorted_files = sorted(all_files)
            
            for i, file_path in enumerate(sorted_files):
                normalized_path = self._normalize_path(file_path)
                file_id = f"file_{i:06d}"
                
                self._shared_path_to_id[normalized_path] = file_id
                self._shared_id_to_path[file_id] = normalized_path
            
            # 更新共享统计信息
            self._shared_predefined_counter.value = len(sorted_files)
            self._shared_stats['predefined_files'] = len(sorted_files)
            self._shared_stats['total_files'] = len(sorted_files)
        
        if self._shared_lock is not None:
            with self._shared_lock:
                init_operation()
    
    def get_file_id(self, file_path: Optional[str]) -> Optional[str]:
        """获取文件ID，如果不存在则动态分配临时ID（多进程共享状态）"""
        if not file_path:
            return None
        
        normalized_path = self._normalize_path(file_path)
        
        # 检查共享映射表（包含预定义和临时文件）
        if self._shared_lock is not None:
            with self._shared_lock:
                existing_id = self._shared_path_to_id.get(normalized_path)
                if existing_id:
                    self._shared_stats['reused_ids'] = self._shared_stats.get('reused_ids', 0) + 1
                    return existing_id
                
                # 不存在则动态分配临时ID
                return self._create_temp_file_id_unsafe(normalized_path)
        else:
            # 在子进程中，使用本地模式
            existing_id = self._shared_path_to_id.get(normalized_path)
            if existing_id:
                return existing_id
            
            # 生成简单的文件ID
            return f"file_{abs(hash(normalized_path)) % 1000000}"
    
    def register_file(self, file_path: str) -> str:
        """注册单个文件并获取ID（别名方法，保持接口兼容）"""
        return self.get_file_id(file_path)
    
    def register_files_batch(self, file_paths: List[str]) -> Dict[str, str]:
        """批量注册文件，返回文件路径到ID的映射"""
        result = {}
        
        for file_path in file_paths:
            # 使用标准的get_file_id方法，它已经处理了所有的锁和映射逻辑
            file_id = self.get_file_id(file_path)
            if file_id:
                result[file_path] = file_id
        
        return result
    
    def _create_temp_file_id_unsafe(self, normalized_path: str) -> str:
        """为动态发现的文件创建临时ID（内部使用，假设已加锁）"""
        if self._shared_temp_counter is not None:
            # 分配新的临时ID
            self._shared_temp_counter.value += 1
            temp_id = f"t{self._shared_temp_counter.value:04d}"  # t0001, t0002, ...
            
            # 添加到共享映射表
            self._shared_path_to_id[normalized_path] = temp_id
            self._shared_id_to_path[temp_id] = normalized_path
            
            # 更新统计信息
            self._shared_stats['temp_files'] = self._shared_stats.get('temp_files', 0) + 1
            self._shared_stats['total_files'] = self._shared_stats.get('predefined_files', 0) + self._shared_stats.get('temp_files', 0)
            
            return temp_id
        else:
            # 在子进程中生成简单的临时ID
            temp_id = f"temp_{abs(hash(normalized_path)) % 1000000}"
            self._shared_path_to_id[normalized_path] = temp_id
            self._shared_id_to_path[temp_id] = normalized_path
            return temp_id
    
    def get_file_by_id(self, file_id: str) -> Optional[str]:
        """根据ID获取文件路径（多进程共享状态）"""
        if self._shared_lock is not None:
            with self._shared_lock:
                return self._shared_id_to_path.get(file_id)
        else:
            return self._shared_id_to_path.get(file_id)
    
    def get_id_by_file(self, file_path: str) -> Optional[str]:
        """根据文件路径获取ID（不创建新ID，多进程共享状态）"""
        normalized_path = self._normalize_path(file_path)
        if self._shared_lock is not None:
            with self._shared_lock:
                return self._shared_path_to_id.get(normalized_path)
        else:
            return self._shared_path_to_id.get(normalized_path)
    
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
        """获取所有文件映射（file_id -> path，多进程共享状态）"""
        if self._shared_lock is not None:
            with self._shared_lock:
                return dict(self._shared_id_to_path)
        else:
            return dict(self._shared_id_to_path)

    def get_reverse_mappings(self) -> Dict[str, str]:
        """获取反向文件映射（path -> file_id，多进程共享状态）"""
        if self._shared_lock is not None:
            with self._shared_lock:
                return dict(self._shared_path_to_id)
        else:
            return dict(self._shared_path_to_id)
    
    def get_all_mappings(self) -> Dict[str, str]:
        """获取所有文件到ID的映射（别名方法）"""
        return self.get_reverse_mappings()

    def get_stats(self) -> Dict[str, int]:
        """获取统计信息（多进程共享状态）"""
        if self._shared_lock is not None:
            with self._shared_lock:
                stats = dict(self._shared_stats)
                
                # 计算最大文件ID
                max_id = 0
                for file_id in self._shared_id_to_path.keys():
                    if len(file_id) > 1 and file_id[1:].isdigit():
                        max_id = max(max_id, int(file_id[1:]))
                stats['max_file_id'] = max_id
                
                # 移除内部标记
                stats.pop('initialized', None)
                return stats
        else:
            stats = dict(self._shared_stats)
            stats['max_file_id'] = 0
            return stats
    
    def remove_file(self, file_path: str) -> bool:
        """移除文件映射（多进程共享状态）"""
        normalized_path = self._normalize_path(file_path)
        
        with self._shared_lock:
            if normalized_path in self._shared_path_to_id:
                file_id = self._shared_path_to_id[normalized_path]
                del self._shared_path_to_id[normalized_path]
                del self._shared_id_to_path[file_id]
                
                # 更新统计信息
                if file_id.startswith('f'):
                    self._shared_stats['predefined_files'] -= 1
                elif file_id.startswith('t'):
                    self._shared_stats['temp_files'] -= 1
                
                self._shared_stats['total_files'] = (
                    self._shared_stats['predefined_files'] + self._shared_stats['temp_files']
                )
                
                return True
            return False
    
    def save_to_file(self, filepath: str):
        """保存当前状态到文件（多进程共享状态）"""
        if self._shared_lock is not None:
            with self._shared_lock:
                data = {
                    'project_root': str(self.project_root),
                    'shared_path_to_id': dict(self._shared_path_to_id),
                    'shared_id_to_path': dict(self._shared_id_to_path),
                    'shared_predefined_counter': self._shared_predefined_counter.value,
                    'shared_temp_counter': self._shared_temp_counter.value,
                    'shared_stats': dict(self._shared_stats)
                }
        else:
            data = {
                'project_root': str(self.project_root),
                'shared_path_to_id': dict(self._shared_path_to_id),
                'shared_id_to_path': dict(self._shared_id_to_path),
                'shared_predefined_counter': 0,
                'shared_temp_counter': 0,
                'shared_stats': dict(self._shared_stats)
            }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def load_from_file(self, filepath: str):
        """从文件加载状态（多进程共享状态）"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if self._shared_lock is not None:
            with self._shared_lock:
                # 清空现有数据
                self._shared_path_to_id.clear()
                self._shared_id_to_path.clear()
                
                # 加载数据
                self._shared_path_to_id.update(data.get('shared_path_to_id', {}))
                self._shared_id_to_path.update(data.get('shared_id_to_path', {}))
                self._shared_predefined_counter.value = data.get('shared_predefined_counter', 0)
                self._shared_temp_counter.value = data.get('shared_temp_counter', 0)
                self._shared_stats.clear()
                self._shared_stats.update(data.get('shared_stats', {}))
        else:
            # 在子进程中，直接更新本地状态
            self._shared_path_to_id.clear()
            self._shared_id_to_path.clear()
            self._shared_path_to_id.update(data.get('shared_path_to_id', {}))
            self._shared_id_to_path.update(data.get('shared_id_to_path', {}))
            self._shared_stats.clear()
            self._shared_stats.update(data.get('shared_stats', {}))
    
    def __getstate__(self):
        """序列化时只保存项目根目录，共享对象通过类级别管理"""
        return {
            'project_root': str(self.project_root)
        }
    
    def __setstate__(self, state):
        """反序列化时恢复状态并重新连接到类级别的共享对象"""
        self.project_root = Path(state['project_root']).resolve()
        
        # 在子进程中，直接引用已存在的类级别共享对象，不要创建新的Manager
        # 如果类管理器不存在，说明我们在子进程中，应该等待主进程设置
        if self._class_manager is not None:
            self._shared_path_to_id = self._class_shared_objects['path_to_id']
            self._shared_id_to_path = self._class_shared_objects['id_to_path']
            self._shared_predefined_counter = self._class_shared_objects['predefined_counter']
            self._shared_temp_counter = self._class_shared_objects['temp_counter']
            self._shared_lock = self._class_lock
            self._shared_stats = self._class_shared_objects['stats']
        else:
            # 如果在子进程中且Manager未初始化，使用本地状态（只读模式）
            self._shared_path_to_id = {}
            self._shared_id_to_path = {}
            self._shared_predefined_counter = None
            self._shared_temp_counter = None
            self._shared_lock = None
            self._shared_stats = {}


# 简化的工具函数：直接创建文件管理器
def create_multiprocess_file_manager(project_root: str, all_files: List[str] = None) -> DistributedFileIdManager:
    """
    创建多进程安全的文件管理器实例（内部自动管理共享对象）
    
    Args:
        project_root: 项目根目录
        all_files: 预分配的文件列表（可选）
    
    Returns:
        DistributedFileIdManager 实例（可序列化，自动共享状态）
    """
    return DistributedFileIdManager(project_root=project_root, all_files=all_files)
