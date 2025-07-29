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
from typing import List, Set, Dict, Pattern
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
        # 预处理排除模式以提高性能
        self._excluded_dir_names: Set[str] = set()  # 纯目录名，O(1)查找
        self._excluded_extensions: Set[str] = set()  # 文件扩展名
        self._compiled_patterns: List[Pattern] = []  # 复杂模式的正则表达式
        self._process_exclude_patterns()
    
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
        """预处理排除模式，提高匹配效率"""
        for pattern in self.DEFAULT_EXCLUDE_PATTERNS:
            # 处理目录名模式，例如：*/Intermediate/* -> Intermediate
            if pattern.startswith('*/') and pattern.endswith('/*'):
                dir_name = pattern[2:-2]  # 移除 */ 和 /*
                self._excluded_dir_names.add(dir_name)
            # 处理以目录名结尾的模式，例如：*/Intermediate -> Intermediate
            elif pattern.startswith('*/') and not pattern.endswith('/*'):
                dir_name = pattern[2:]  # 移除 */
                self._excluded_dir_names.add(dir_name)
            # 处理文件扩展名模式，例如：*.luac -> .luac
            elif pattern.startswith('*.'):
                ext = pattern[1:]  # 移除 *
                self._excluded_extensions.add(ext)
            # 处理隐藏文件模式：*/.*
            elif pattern == '*/.*':
                # 这个会在快速检查中特殊处理
                pass
            # 其他复杂模式编译为正则表达式
            else:
                try:
                    # 将fnmatch模式转换为正则表达式
                    regex_pattern = fnmatch.translate(pattern)
                    compiled = re.compile(regex_pattern)
                    self._compiled_patterns.append(compiled)
                except re.error:
                    # 如果正则表达式编译失败，跳过该模式
                    pass
    
    def _should_exclude_directory(self, dir_name: str, dir_path: str) -> bool:
        """快速目录排除检查（高性能版本）"""
        # 1. O(1) 检查隐藏目录
        if dir_name.startswith('.'):
            return True
        
        # 2. O(1) 检查预定义的目录名
        if dir_name in self._excluded_dir_names:
            return True
        
        # 3. 检查文件扩展名（虽然这里是目录，但可能有同名情况）
        for ext in self._excluded_extensions:
            if dir_name.endswith(ext):
                return True
        
        # 4. 只有在必要时才进行复杂的正则匹配
        if self._compiled_patterns:
            normalized_path = dir_path.replace('\\', '/')
            for pattern in self._compiled_patterns:
                if pattern.match(normalized_path):
                    return True
        
        return False
    
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
        
        # 检查排除模式 - 使用配置中的排除模式
        if config.exclude_patterns and self._should_exclude_file(file_path, config.exclude_patterns):
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

    def _is_under_directory(self, file_path: Path, directory_path: Path) -> bool:
        """判断文件是否在指定目录下"""
        try:
            # 确保两个路径都是绝对路径并解析符号链接
            abs_file_path = file_path.resolve()
            abs_dir_path = directory_path.resolve()
            
            # 使用更可靠的方法检查路径包含关系
            try:
                # 尝试计算相对路径，如果成功则说明文件在目录下
                abs_file_path.relative_to(abs_dir_path)
                return True
            except ValueError:
                # 如果relative_to抛出ValueError，说明文件不在目录下
                return False
                
        except (OSError, ValueError):
            # 如果路径解析失败，则认为不在目录下
            return False

    def _is_file_excluded(self, file_path: str) -> bool:
        """
        检查单个文件是否应被排除（高性能版本）。
        
        Args:
            file_path: 文件的绝对路径。
            
        Returns:
            bool: 如果文件应被排除，则返回 True。
        """
        path_obj = Path(file_path)
        
        # 1. 检查文件扩展名
        if path_obj.suffix in self._excluded_extensions:
            return True
            
        # 2. 检查隐藏文件（基于文件名）
        if path_obj.name.startswith('.'):
            return True

        # 3. 将路径标准化为Unix风格，以匹配模式
        normalized_path = path_obj.as_posix()
        
        # 4. 检查预编译的正则表达式
        for pattern in self._compiled_patterns:
            if pattern.match(normalized_path):
                return True
        
        return False

    def filter_files_from_list(self, file_list: List[str], scan_directory: str) -> List[str]:
        """
        从现有文件列表中过滤文件。

        Args:
            file_list (List[str]): 从 compile_commands.json 读取的原始文件列表。
            scan_directory (str): 需要分析的目标目录。只有在此目录下的文件才会被包含。

        Returns:
            List[str]: 过滤后的文件列表。
        """
        filtered_files = []
        scan_path = Path(scan_directory).resolve()
        
        from .logger import get_logger
        logger = get_logger()
        logger.info(f"开始过滤文件列表... 原始数量: {len(file_list)}, 目标目录: {scan_directory}")
        
        for file_path_str in file_list:
            file_path = Path(file_path_str).resolve()
            
            # 1. 检查文件是否在指定的扫描目录下
            if not self._is_under_directory(file_path, scan_path):
                continue
                
            # 2. 检查文件是否应被排除
            if self._is_file_excluded(str(file_path)):
                logger.debug(f"排除文件: {file_path_str}")
                continue
            
            # 3. 检查文件扩展名是否为支持的类型
            if file_path.suffix.lower() not in self.ALL_EXTENSIONS:
                logger.debug(f"因扩展名不受支持而跳过: {file_path_str}")
                continue

            filtered_files.append(file_path_str)
            
        logger.info(f"过滤完成。保留文件数: {len(filtered_files)}")
        return filtered_files