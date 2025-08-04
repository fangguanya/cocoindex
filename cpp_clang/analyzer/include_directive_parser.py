"""
Include Directive Parser - 动态include指令解析器
根据translation unit编译过程中的#include指令动态路由到真正需要的头文件
"""

import os
import re
import time
from pathlib import Path
from typing import List, Dict, Set, Any, Optional, Tuple
from dataclasses import dataclass

import clang.cindex as clang
from .logger import get_logger
from .performance_profiler import profiler, profile_function


@dataclass
class IncludeDirective:
    """Include指令信息"""
    file_path: str
    include_path: str
    line_number: int
    is_system_include: bool  # <> vs ""
    resolved_path: Optional[str] = None
    is_processed: bool = False


@dataclass
class IncludeResolutionResult:
    """Include解析结果"""
    success: bool
    resolved_path: Optional[str] = None
    error_message: Optional[str] = None
    include_dirs_searched: List[str] = None


class IncludeDirectiveParser:
    """动态include指令解析器"""
    
    def __init__(self, project_root: str):
        self.logger = get_logger()
        self.project_root = project_root
        self._include_cache: Dict[str, str] = {}  # include_path -> resolved_path
        self._file_include_map: Dict[str, List[IncludeDirective]] = {}  # file_path -> includes
        
    def extract_include_directives_from_tu(self, translation_unit: clang.TranslationUnit) -> List[IncludeDirective]:
        """从translation unit中提取所有include指令"""
        directives = []
        
        # 遍历translation unit的所有节点，查找INCLUSION_DIRECTIVE
        for cursor in translation_unit.cursor.walk_preorder():
            if cursor.kind == clang.CursorKind.INCLUSION_DIRECTIVE:
                directive = self._parse_inclusion_directive(cursor)
                if directive:
                    directives.append(directive)
        
        return directives
    
    def _parse_inclusion_directive(self, cursor: clang.Cursor) -> Optional[IncludeDirective]:
        """解析单个inclusion directive cursor"""
        try:
            # 获取include的文件信息
            included_file = cursor.get_included_file()
            if not included_file:
                return None
            
            file_path = cursor.location.file.name if cursor.location.file else ""
            include_path = included_file.name
            line_number = cursor.location.line
            
            # 判断是否是系统include（通过检查原始文本）
            is_system_include = self._is_system_include(cursor, file_path, line_number)
            
            return IncludeDirective(
                file_path=file_path,
                include_path=include_path,
                line_number=line_number,
                is_system_include=is_system_include,
                resolved_path=include_path
            )
            
        except Exception as e:
            self.logger.debug(f"解析include指令时出错: {e}")
            return None
    
    def _is_system_include(self, cursor: clang.Cursor, file_path: str, line_number: int) -> bool:
        """判断是否是系统include（<> vs ""）"""
        try:
            # 读取源文件的对应行来判断include类型
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                if 0 <= line_number - 1 < len(lines):
                    line = lines[line_number - 1].strip()
                    # 检查是否包含 < 和 >
                    return '<' in line and '>' in line
        except Exception:
            pass
        return False
    
    def extract_include_directives_from_source(self, file_path: str) -> List[IncludeDirective]:
        """从源文件中直接解析include指令（不依赖clang）"""
        directives = []
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            include_pattern = re.compile(r'^\s*#\s*include\s*([<"])([^>"]+)[>"]')
            
            for line_num, line in enumerate(lines, 1):
                match = include_pattern.match(line.strip())
                if match:
                    bracket_type = match.group(1)
                    include_path = match.group(2)
                    is_system_include = bracket_type == '<'
                    
                    directive = IncludeDirective(
                        file_path=file_path,
                        include_path=include_path,
                        line_number=line_num,
                        is_system_include=is_system_include
                    )
                    directives.append(directive)
                    
        except Exception as e:
            self.logger.warning(f"解析文件 {file_path} 的include指令时出错: {e}")
        
        return directives
    
    def resolve_include_directive(self, directive: IncludeDirective, 
                                include_dirs: List[str]) -> IncludeResolutionResult:
        """解析include指令到实际文件路径"""
        
        # 检查缓存
        cache_key = f"{directive.include_path}:{':'.join(include_dirs)}"
        if cache_key in self._include_cache:
            return IncludeResolutionResult(
                success=True,
                resolved_path=self._include_cache[cache_key]
            )
        
        searched_dirs = []
        
        # 如果是相对include（用""），先在当前文件目录查找
        if not directive.is_system_include:
            current_dir = os.path.dirname(directive.file_path)
            candidate_path = os.path.join(current_dir, directive.include_path)
            searched_dirs.append(current_dir)
            
            if os.path.exists(candidate_path):
                resolved_path = str(Path(candidate_path).resolve())
                self._include_cache[cache_key] = resolved_path
                return IncludeResolutionResult(
                    success=True,
                    resolved_path=resolved_path,
                    include_dirs_searched=searched_dirs
                )
        
        # 在include目录中查找
        for include_dir in include_dirs:
            candidate_path = os.path.join(include_dir, directive.include_path)
            searched_dirs.append(include_dir)
            
            if os.path.exists(candidate_path):
                resolved_path = str(Path(candidate_path).resolve())
                self._include_cache[cache_key] = resolved_path
                return IncludeResolutionResult(
                    success=True,
                    resolved_path=resolved_path,
                    include_dirs_searched=searched_dirs
                )
        
        # 未找到文件
        return IncludeResolutionResult(
            success=False,
            error_message=f"无法找到include文件: {directive.include_path}",
            include_dirs_searched=searched_dirs
        )
    
    def get_required_headers_for_file(self, file_path: str, 
                                    include_dirs: List[str]) -> List[str]:
        """获取文件所需的所有头文件（递归解析include）"""
        required_headers = []
        processed_files = set()
        
        def process_file_recursive(current_file: str, depth: int = 0):
            if depth > 50:  # 防止无限递归
                self.logger.warning(f"Include递归深度过深，停止处理: {current_file}")
                return
            
            if current_file in processed_files:
                return
            
            processed_files.add(current_file)
            
            # 解析当前文件的include指令
            directives = self.extract_include_directives_from_source(current_file)
            
            for directive in directives:
                resolution = self.resolve_include_directive(directive, include_dirs)
                
                if resolution.success and resolution.resolved_path:
                    # 只处理项目内的头文件
                    if self._is_project_header(resolution.resolved_path):
                        if resolution.resolved_path not in required_headers:
                            required_headers.append(resolution.resolved_path)
                        
                        # 递归处理include的头文件
                        process_file_recursive(resolution.resolved_path, depth + 1)
                else:
                    self.logger.debug(f"无法解析include: {directive.include_path} in {current_file}")
        
        process_file_recursive(file_path)
        return required_headers
    
    def _is_project_header(self, file_path: str) -> bool:
        """判断是否是项目内的头文件"""
        try:
            resolved_path = Path(file_path).resolve()
            project_path = Path(self.project_root).resolve()
            
            # 检查文件是否在项目目录内
            try:
                resolved_path.relative_to(project_path)
                return True
            except ValueError:
                return False
                
        except Exception:
            return False
    
    def create_dynamic_compile_commands(self, source_file: str, 
                                      base_compile_info: Dict[str, Any],
                                      include_dirs: List[str]) -> Dict[str, Dict[str, Any]]:
        """为源文件及其依赖的头文件动态创建编译命令"""
        compile_commands = {}
        
        # 获取源文件需要的所有头文件
        required_headers = self.get_required_headers_for_file(source_file, include_dirs)
        
        self.logger.info(f"文件 {source_file} 需要 {len(required_headers)} 个头文件")
        
        # 为每个头文件创建编译命令
        for header_file in required_headers:
            header_compile_info = self._create_header_compile_command(
                header_file, base_compile_info, include_dirs
            )
            
            normalized_path = str(Path(header_file).resolve()).replace('\\', '/')
            compile_commands[normalized_path] = header_compile_info
        
        return compile_commands
    
    def _create_header_compile_command(self, header_file: str, 
                                     base_compile_info: Dict[str, Any],
                                     include_dirs: List[str]) -> Dict[str, Any]:
        """为头文件创建编译命令"""
        base_args = base_compile_info.get("args", [])
        
        # 创建头文件专用的编译参数
        header_args = []
        
        # 保留基本的编译参数
        skip_next = False
        for i, arg in enumerate(base_args):
            if skip_next:
                skip_next = False
                continue
            
            # 跳过源文件相关参数
            if (arg.endswith(('.cpp', '.cc', '.cxx', '.c')) or 
                arg in ['-o', '-c'] or
                arg.startswith('-o')):
                if arg in ['-o', '-c'] and i + 1 < len(base_args):
                    skip_next = True
                continue
            
            header_args.append(arg)
        
        # 添加所有include目录
        for inc_dir in include_dirs:
            include_arg = f'-I{inc_dir}'
            if include_arg not in header_args:
                header_args.append(include_arg)
        
        # 添加头文件特定参数
        header_specific_args = [
            '-x', 'c++-header',
            '-Wno-pragma-once-outside-header',
            '-Wno-include-next-outside-header'
        ]
        
        for arg in header_specific_args:
            if arg not in header_args:
                header_args.append(arg)
        
        return {
            "args": header_args,
            "directory": base_compile_info.get("directory", self.project_root)
        }
    
    def analyze_include_dependencies(self, files: List[str], 
                                   include_dirs: List[str]) -> Dict[str, List[str]]:
        """分析文件的include依赖关系"""
        dependencies = {}
        
        for file_path in files:
            try:
                required_headers = self.get_required_headers_for_file(file_path, include_dirs)
                dependencies[file_path] = required_headers
                self.logger.debug(f"文件 {file_path} 依赖 {len(required_headers)} 个头文件")
            except Exception as e:
                self.logger.warning(f"分析文件 {file_path} 的依赖时出错: {e}")
                dependencies[file_path] = []
        
        return dependencies
    
    def get_include_statistics(self) -> Dict[str, Any]:
        """获取include解析统计信息"""
        return {
            "cached_includes": len(self._include_cache),
            "processed_files": len(self._file_include_map),
            "cache_hit_ratio": self._calculate_cache_hit_ratio()
        }
    
    def _calculate_cache_hit_ratio(self) -> float:
        """计算缓存命中率"""
        # 这里可以添加更详细的缓存统计逻辑
        return 0.0
    
    def clear_cache(self):
        """清空缓存"""
        self._include_cache.clear()
        self._file_include_map.clear()
        self.logger.info("Include解析缓存已清空")