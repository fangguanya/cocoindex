"""
File Scanner Module

Scans specified directories for C++ source files (.cpp, .cc, .cxx) and 
header files (.h, .hpp, .hxx). Supports filtering, exclusion patterns,
and Unreal Engine project structure.
"""

import os
import fnmatch
import re
from pathlib import Path
from typing import List, Set, Dict, Pattern, Optional
from dataclasses import dataclass
from .file_filter import UnifiedFileFilter, create_unreal_filter

@dataclass
class ScanResult:
    """文件扫描结果"""
    files: List[str]  # 扫描到的文件列表
    file_mappings: Dict[str, str]  # 文件ID到路径的映射

class FileScanner:
    """文件扫描器 - 支持双路径设计"""
    
    # 支持的C++文件扩展名
    CPP_EXTENSIONS = {'.cpp', '.cc', '.cxx', '.c'}
    HEADER_EXTENSIONS = {'.h', '.hpp', '.hxx', '.hh'}
    ALL_EXTENSIONS = CPP_EXTENSIONS | HEADER_EXTENSIONS
    
    # 精确的默认排除模式
    DEFAULT_EXCLUDE_PATTERNS = {
        '*/Intermediate/*',      # UE中间文件
        '*/Binaries/*',          # UE二进制文件
        '*/DerivedDataCache/*',  # UE缓存
        '*/Saved/*',             # UE保存文件
        '*/.vs/*',               # Visual Studio
        '*/.vscode/*',           # VS Code
        '*/.git/*',              # Git目录
        '*/node_modules/*',      # Node.js
        '*/__pycache__/*',       # Python缓存
        '*/CMakeFiles/*',        # CMake生成文件
        '*/build/*',             # 通用构建目录
        '*/dist/*',              # 发布目录
        '*/obj/*',               # 目标文件目录
        '*/Debug/*',             # Debug输出
        '*/Release/*',           # Release输出
        '*/x64/*',               # x64输出
        '*/Win32/*',             # Win32输出
        "*/.*",
        "*/DerivedDataCache/*",
        "*/Build/*",
        "*/Content/*",
        "*.luac",
        "*/Engine/Source/Programs/*",
        "*/ThirdParty/*",
        "*/Client_WwiseProject/*",
    }
    
    def __init__(self):
        # 使用统一的高效文件过滤器
        self.file_filter = create_unreal_filter()
        
        # 保持向后兼容的属性（已弃用，建议使用file_filter）
        self._excluded_dir_names: Set[str] = set()
        self._excluded_extensions: Set[str] = set()
        self._compiled_patterns: List[Pattern] = []
    
    def scan_directory(self, config) -> ScanResult:
        """扫描目录 - 支持双路径设计
        
        Args:
            config: AnalysisConfig对象，包含project_root和scan_directory
            
        Returns:
            ScanResult: 扫描结果，包含文件列表和映射
        """
        files = []
        
        # 只扫描scan_directory
        all_files = self._walk_directory(config.scan_directory)
        for file_path in all_files:
            if self._should_include_file(file_path, config):
                files.append(file_path)
        
        # 但file_mappings基于project_root计算相对路径
        file_mappings = self._generate_file_mappings(files, config.project_root)
        
        return ScanResult(files=files, file_mappings=file_mappings)
    
    def _process_exclude_patterns(self):
        """已弃用：现在使用UnifiedFileFilter进行过滤"""
        # 保留此方法以保持向后兼容性，但不执行任何操作
        pass
    
    def _should_exclude_directory(self, dir_name: str, dir_path: str) -> bool:
        """使用统一过滤器检查目录是否应该被排除"""
        return self.file_filter.should_exclude(dir_path)
    
    def _walk_directory(self, directory: str) -> List[str]:
        """递归遍历目录获取所有文件（优化版：在遍历期间应用排除模式）"""
        files = []
        directory_path = Path(directory)
        
        if not directory_path.exists():
            return files
        
        if directory_path.is_file():
            files.append(str(directory_path.resolve()))
            return files
        
        # 性能统计
        total_dirs_scanned = 0
        dirs_excluded = 0
        
        try:
            for root, dirs, filenames in os.walk(directory):
                total_dirs_scanned += 1
                
                # 过滤要递归的目录 - 使用高性能的快速检查！
                dirs_to_remove = []
                for dir_name in dirs:
                    dir_path = Path(root) / dir_name
                    full_dir_path = str(dir_path.resolve())
                    
                    # 使用预处理的快速检查方法
                    if self._should_exclude_directory(dir_name, full_dir_path):
                        dirs_to_remove.append(dir_name)
                        dirs_excluded += 1
                        from .logger import get_logger
                        logger = get_logger()
                        logger.debug(f"⚡ 跳过排除目录: {dir_name}")
                        continue
                
                # 从dirs列表中移除被排除的目录，防止os.walk递归进入
                for dir_to_remove in dirs_to_remove:
                    dirs.remove(dir_to_remove)
                
                # 添加当前目录下的文件
                for filename in filenames:
                    file_path = Path(root) / filename
                    files.append(str(file_path.resolve()))
                    
        except (OSError, PermissionError) as e:
            from .logger import get_logger
            logger = get_logger()
            logger.warning(f"无法访问目录 {directory}: {e}")
        
        # 输出性能统计
        from .logger import get_logger
        logger = get_logger()
        logger.info(f"📊 目录扫描统计: 总计扫描 {total_dirs_scanned} 个目录, 排除 {dirs_excluded} 个目录, 找到 {len(files)} 个文件")
        if dirs_excluded > 0:
            exclusion_rate = (dirs_excluded / total_dirs_scanned) * 100
            logger.info(f"🚀 性能优化: 排除率 {exclusion_rate:.1f}%, 大大提升扫描速度！")
        
        return files
    
    def _should_include_file(self, file_path: str, config) -> bool:
        """判断文件是否应该包含在扫描结果中"""
        path_obj = Path(file_path)
        
        # 检查文件扩展名
        if path_obj.suffix.lower() not in config.include_extensions:
            return False
        
        # 检查排除模式 - 优先使用统一过滤器，向后兼容配置中的排除模式
        if hasattr(config, 'exclude_patterns') and config.exclude_patterns:
            if self._should_exclude_file(file_path, config.exclude_patterns):
                return False
        else:
            # 使用统一过滤器
            if self._should_exclude_file(file_path):
                return False
        
        # 检查文件是否存在和可读
        if not path_obj.exists() or not path_obj.is_file():
            return False
        
        try:
            # 尝试读取文件以确保可访问
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                f.read(1)  # 只读取1个字符
            return True
        except (OSError, PermissionError):
            return False
    
    def _should_exclude_file(self, file_path: str, patterns: Optional[Set[str]] = None) -> bool:
        """使用统一过滤器检查文件是否应被排除"""
        # 如果提供了patterns参数，使用传统模式（向后兼容）
        if patterns is not None:
            normalized_path = file_path.replace('\\', '/')
            for pattern in patterns:
                if fnmatch.fnmatch(normalized_path, pattern):
                    return True
            return False
        
        # 否则使用统一过滤器（推荐方式）
        return self.file_filter.should_exclude(file_path)
    
    def _generate_file_mappings(self, files: List[str], project_root: str) -> Dict[str, str]:
        """生成文件ID映射 - 基于project_root"""
        file_mappings = {}
        project_root_path = Path(project_root).resolve()
        
        for i, file_path in enumerate(sorted(files)):
            file_id = f"f{i+1:03d}"  # f001, f002, f003...
            
            # 计算相对于project_root的路径
            try:
                abs_file_path = Path(file_path).resolve()
                rel_path = abs_file_path.relative_to(project_root_path)
                file_mappings[file_id] = str(rel_path).replace('\\', '/')
            except ValueError:
                # 文件不在project_root下，使用绝对路径
                file_mappings[file_id] = str(Path(file_path).resolve()).replace('\\', '/')
        
        return file_mappings

    def _is_under_directory(self, file_path: Path, directory_path: Path) -> bool:
        """判断文件是否在指定目录下"""
        try:
            file_path.relative_to(directory_path)
            return True
        except ValueError:
            return False 
    
    def get_filter_statistics(self) -> Dict:
        """获取文件过滤器的统计信息"""
        return self.file_filter.get_statistics()
    
    def clear_filter_cache(self):
        """清理文件过滤器的缓存"""
        self.file_filter.clear_cache()
    
    def add_exclude_pattern(self, pattern: str):
        """动态添加排除模式"""
        self.file_filter.add_exclude_pattern(pattern)
    
    def remove_exclude_pattern(self, pattern: str):
        """移除排除模式"""
        self.file_filter.remove_exclude_pattern(pattern)