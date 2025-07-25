"""
File Scanner Module

Scans specified directories for C++ source files (.cpp, .cc, .cxx) and 
header files (.h, .hpp, .hxx). Supports filtering, exclusion patterns,
and Unreal Engine project structure.
"""

import os
import fnmatch
from pathlib import Path
from typing import List, Set, Dict
from dataclasses import dataclass

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
    }
    
    def __init__(self):
        pass
    
    def scan_directory(self, config) -> ScanResult:
        """扫描目录 - 支持双路径设计
        
        Args:
            config: AnalysisConfig对象，包含project_root和scan_directory
            
        Returns:
            ScanResult: 扫描结果，包含文件列表和映射
        """
        files = []
        
        # 只扫描scan_directory
        for file_path in self._walk_directory(config.scan_directory):
            if self._should_include_file(file_path, config):
                files.append(file_path)
        
        # 但file_mappings基于project_root计算相对路径
        file_mappings = self._generate_file_mappings(files, config.project_root)
        
        return ScanResult(files=files, file_mappings=file_mappings)
    
    def _walk_directory(self, directory: str) -> List[str]:
        """递归遍历目录获取所有文件"""
        files = []
        directory_path = Path(directory)
        
        if not directory_path.exists():
            return files
        
        if directory_path.is_file():
            files.append(str(directory_path.resolve()))
            return files
        
        try:
            for root, dirs, filenames in os.walk(directory):
                # 修改dirs列表以控制递归行为（跳过隐藏目录）
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                for filename in filenames:
                    file_path = Path(root) / filename
                    files.append(str(file_path.resolve()))
        except (OSError, PermissionError) as e:
            from .logger import get_logger
            logger = get_logger()
            logger.warning(f"无法访问目录 {directory}: {e}")
        
        return files
    
    def _should_include_file(self, file_path: str, config) -> bool:
        """判断文件是否应该包含在扫描结果中"""
        path_obj = Path(file_path)
        
        # 检查文件扩展名
        if path_obj.suffix.lower() not in config.include_extensions:
            return False
        
        # 检查排除模式
        exclude_patterns = config.exclude_patterns or self.DEFAULT_EXCLUDE_PATTERNS
        if self._should_exclude_file(file_path, exclude_patterns):
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
    
    def _should_exclude_file(self, file_path: str, patterns: Set[str]) -> bool:
        """检查文件是否应被排除"""
        # 标准化路径分隔符
        normalized_path = file_path.replace('\\', '/')
        
        for pattern in patterns:
            if fnmatch.fnmatch(normalized_path, pattern):
                return True
        
        return False
    
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
    


# UE项目检测和支持函数
def is_unreal_engine_project(directory: str) -> bool:
    """检测目录是否为Unreal Engine项目"""
    directory_path = Path(directory)
    
    # 检查UE项目标识文件
    ue_indicators = [
        "*.uproject",
        "*.uplugin",
        "Engine/Build/Build.version",
        "Source/Runtime/Engine/Engine.Build.cs",
        "Engine/Source/Runtime/Engine/Engine.Build.cs"
    ]
    
    for pattern in ue_indicators:
        if list(directory_path.glob(pattern)):
            return True
        # 也检查父目录
        parent = directory_path.parent
        if list(parent.glob(pattern)):
            return True
    
    return False

def get_unreal_include_paths(project_root: str) -> List[str]:
    """获取Unreal Engine项目的include路径"""
    include_paths = []
    project_path = Path(project_root)
    
    # UE核心include目录模式
    ue_include_patterns = [
        # Engine核心
        "Engine/Source/Runtime/*/Public",
        "Engine/Source/Runtime/*/Classes",
        "Engine/Source/Developer/*/Public", 
        "Engine/Source/Editor/*/Public",
        "Engine/Source/ThirdParty/*/include",
        
        # 插件
        "Engine/Plugins/*/Source/*/Public",
        "Engine/Plugins/*/Source/*/Classes",
        "Plugins/*/Source/*/Public",
        "Plugins/*/Source/*/Classes",
        
        # 项目源码
        "Source/*/Public",
        "Source/*/Classes",
        "Source/*",
        
        # 第三方库
        "ThirdParty/*/include",
        "ThirdParty/*/Include",
    ]
    
    # 搜索include路径
    for pattern in ue_include_patterns:
        for path in project_path.glob(pattern):
            if path.is_dir():
                include_paths.append(str(path))
    
    # 去重并排序
    include_paths = sorted(list(set(include_paths)))
    
    return include_paths 