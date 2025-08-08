"""
Clang Parser Module - 性能优化版 - 修复版 - 应用增强版修复 - 清理版
"""

import os
import json
import time
import subprocess
import platform
import shlex
import traceback
import threading
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from concurrent.futures import ThreadPoolExecutor

import clang.cindex as clang
from clang.cindex import TranslationUnit, Diagnostic
from rich.console import Console
from rich.progress import Progress, TaskID
from .logger import get_logger
from .performance_profiler import profiler, profile_function, DetailedLogger

def _get_severity_name(severity_int: int) -> str:
    """将 Diagnostic severity 整数值转换为可读字符串"""
    severity_map = {
        0: "Ignored",
        1: "Note", 
        2: "Warning",
        3: "Error",
        4: "Fatal"
    }
    return severity_map.get(severity_int, f"Unknown({severity_int})")

class DiagnosticInfo:
    """诊断信息 - 性能优化版"""
    def __init__(self, severity: str, message: str, file_path: str, line: int, column: int, category: str):
        self.severity = severity
        self.message = message
        self.file_path = file_path
        self.line = line
        self.column = column
        self.category = category

class SerializableDiagnostic:
    """可序列化的诊断信息 - 性能优化版"""
    def __init__(self, spelling: str, severity: int, location_file: str, location_line: int, location_column: int):
        self.spelling = spelling
        self.severity = severity
        self.location_file = location_file
        self.location_line = location_line
        self.location_column = location_column

class SerializableParseResult:
    """可序列化的解析结果，用于多进程传输 - 性能优化版"""
    def __init__(self, file_path: str, success: bool, diagnostics: List[SerializableDiagnostic], parse_time: float):
        self.file_path = file_path
        self.success = success
        self.diagnostics = diagnostics
        self.parse_time = parse_time
    
    @staticmethod
    def from_parsed_file(parsed_file) -> 'SerializableParseResult':
        serializable_diagnostics = []
        if hasattr(parsed_file, 'translation_unit') and parsed_file.translation_unit:
            for diag in parsed_file.translation_unit.diagnostics:
                try:
                    serializable_diagnostics.append(SerializableDiagnostic(
                        spelling=diag.spelling,
                        severity=diag.severity,
                        location_file=str(diag.location.file) if diag.location.file else "",
                        location_line=diag.location.line,
                        location_column=diag.location.column
                    ))
                except:
                    pass
        
        return SerializableParseResult(
            file_path=parsed_file.file_path,
            success=parsed_file.success,
            diagnostics=serializable_diagnostics,
            parse_time=parsed_file.parse_time
        )

class SerializableExtractedData:
    """可序列化的实体提取结果，用于多进程传输 - 性能优化版"""
    def __init__(self, file_path: str, success: bool, parse_time: float, extraction_time: float,
                 functions: Dict[str, Any], classes: Dict[str, Any], namespaces: Dict[str, Any],
                 global_nodes: Dict[str, Any], file_mappings: Dict[str, Any], stats: Dict[str, Any],
                 member_variables: Optional[Dict[str, Any]] = None,
                 dynamic_headers: Optional[List[str]] = None,
                 header_processing_results: Optional[Dict[str, Any]] = None,
                 mmap_shared: bool = False):
        self.file_path = file_path
        self.success = success
        self.parse_time = parse_time
        self.extraction_time = extraction_time
        self.functions = functions
        self.classes = classes
        self.namespaces = namespaces
        self.member_variables = member_variables or {}
        self.global_nodes = global_nodes
        self.file_mappings = file_mappings
        self.stats = stats
        self.dynamic_headers = dynamic_headers or []
        self.header_processing_results = header_processing_results or {}
        self.mmap_shared = mmap_shared
    
    @staticmethod
    def empty_result(file_path: str, error_msg: str = "") -> 'SerializableExtractedData':
        """创建空结果"""
        return SerializableExtractedData(
            file_path=file_path,
            success=False,
            parse_time=0.0,
            extraction_time=0.0,
            functions={},
            classes={},
            namespaces={},
            global_nodes={},
            file_mappings={},
            stats={"error": error_msg},
            member_variables={},
            dynamic_headers=[],
            header_processing_results={},
            mmap_shared=False
        )

class ParsedFile:
    """解析后的文件信息 - 性能优化版"""
    def __init__(self, file_path: str, translation_unit: Any, success: bool, 
                 diagnostics: List[DiagnosticInfo], parse_time: float):
        self.file_path = file_path
        self.translation_unit = translation_unit
        self.success = success
        self.diagnostics = diagnostics
        self.parse_time = parse_time

class TranslationUnitCache:
    """TranslationUnit缓存机制 - 性能优化"""
    def __init__(self, max_size: int = 100):
        self._cache: Dict[str, TranslationUnit] = {}
        self._access_times: Dict[str, float] = {}
        self._max_size = max_size
        self._lock = threading.RLock()
    
    def get(self, key: str) -> Optional[TranslationUnit]:
        with self._lock:
            if key in self._cache:
                self._access_times[key] = time.time()
                return self._cache[key]
            return None
    
    def put(self, key: str, tu: TranslationUnit):
        with self._lock:
            if len(self._cache) >= self._max_size:
                self._evict_oldest()
            self._cache[key] = tu
            self._access_times[key] = time.time()
    
    def _evict_oldest(self):
        if not self._access_times:
            return
        oldest_key = min(self._access_times.keys(), key=lambda k: self._access_times[k])
        del self._cache[oldest_key]
        del self._access_times[oldest_key]
    
    def clear(self):
        with self._lock:
            self._cache.clear()
            self._access_times.clear()

class ClangParser:
    """Clang解析器 - 支持compile_commands.json和动态编译参数 - 性能优化版 - 修复版 - 增强版"""
    
    def __init__(self, console: Optional[Console] = None, verbose: bool = True, enable_cache: bool = True):
        self.console = console or Console()
        self.logger = get_logger()
        self._verbose = verbose
        self.index = None
        self.compile_commands: Dict[str, Dict[str, Any]] = {}
        self._tu_cache = TranslationUnitCache() if enable_cache else None
        self._initialize_index()

    def _initialize_index(self):
        """初始化libclang索引"""
        try:
            self.index = clang.Index.create()
            self.logger.info("libclang索引初始化成功")
        except Exception as e:
            self.logger.error(f"libclang索引初始化失败: {e}")
            raise

    def load_compile_commands(self, compile_commands_path: str):
        """加载compile_commands.json文件"""
        self.logger.info(f"加载 compile_commands.json: {compile_commands_path}")
        with open(compile_commands_path, 'r', encoding='utf-8') as f:
            commands_data = json.load(f)
        self.compile_commands = self._parse_compile_commands(commands_data)
        self.logger.info(f"已加载 compile_commands.json: {len(self.compile_commands)} 文件")

    def _parse_compile_commands(self, commands_data: List[Dict]) -> Dict[str, Dict[str, Any]]:
        """解析compile_commands.json数据"""
        file_commands = {}
        for entry in commands_data:
            file_path = entry.get('file', '')
            if not file_path: continue
            
            directory = entry.get('directory', '')
            command = entry.get('command', '')
            
            if command:
                args = self._process_compile_args(
                    shlex.split(command, posix=False) if platform.system() == 'Windows' else shlex.split(command),
                    directory
                )
                
                # 正确处理相对路径和绝对路径
                if os.path.isabs(file_path):
                    # 绝对路径直接使用
                    normalized_path = self._normalize_path(file_path)
                else:
                    # 相对路径需要结合directory来构造完整路径
                    full_path = os.path.join(directory, file_path)
                    normalized_path = self._normalize_path(full_path)
                
                file_commands[normalized_path] = {"args": args, "directory": directory}
        return file_commands

    def _parse_response_file(self, rsp_path: str, working_directory: str = None) -> List[str]:
        """解析响应文件(.rsp)并返回参数列表"""
        # 处理相对路径：如果rsp_path是相对路径且提供了工作目录，则相对于工作目录解析
        if working_directory and not os.path.isabs(rsp_path):
            full_rsp_path = os.path.join(working_directory, rsp_path)
        else:
            full_rsp_path = rsp_path
        
        if not os.path.exists(full_rsp_path):
            self.logger.warning(f"响应文件不存在: {rsp_path} (完整路径: {full_rsp_path})")
            return []
        
        try:
            with open(full_rsp_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            
            # 简单的参数分割
            if platform.system() == 'Windows':
                args = shlex.split(content, posix=False)
            else:
                args = shlex.split(content)
            
            self.logger.debug(f"成功解析响应文件: {rsp_path} -> {len(args)} 个参数")
            return args
        
        except Exception as e:
            self.logger.error(f"解析响应文件失败 {rsp_path}: {e}")
            return []

    def _extract_file_path_from_args(self, args: List[str]) -> Optional[str]:
        """从编译参数中提取文件路径"""
        # 查找不以-开头的参数，通常是文件路径
        for arg in args:
            if not arg.startswith('-') and (arg.endswith('.h') or arg.endswith('.cpp') or arg.endswith('.cc')):
                return arg
        return None

    def _process_compile_args(self, raw_args: List[str], working_directory: str = None) -> List[str]:
        """处理和清理编译参数，包括响应文件展开"""
        
        # 检测是否为clang-cl命令，如果是则进行参数转换
        is_clang_cl = any('clang-cl' in str(arg) for arg in raw_args)
        if is_clang_cl:
            self.logger.debug("检测到clang-cl命令，启用参数转换")
            return self._convert_clang_cl_to_libclang(raw_args, working_directory)
        
        # 处理标准编译参数
        return self._process_standard_compile_args(raw_args, working_directory)
    
    def _process_standard_compile_args(self, raw_args: List[str], working_directory: str = None) -> List[str]:
        """处理标准编译参数（非clang-cl）"""
        
        # 已移除delayed-template-parsing参数（在C++20后被弃用）
        filtered_args = []
        for arg in raw_args:
            if 'delayed-template-parsing' not in str(arg):
                filtered_args.append(arg)
        raw_args = filtered_args
        
        def process_args_recursive(args_list: List[str]) -> List[str]:
            """递归处理参数列表，包括响应文件展开"""
            # 在递归处理中再次过滤delayed-template-parsing参数
            args_list = [arg for arg in args_list if 'delayed-template-parsing' not in str(arg)]
            processed = []
            skip_next = False
            
            for i, arg in enumerate(args_list):
                if skip_next:
                    skip_next = False
                    continue
                
                # 处理响应文件
                if arg.startswith('@'):
                    rsp_path = arg[1:].strip('"\'')
                    rsp_args = self._parse_response_file(rsp_path, working_directory)
                    # 递归处理响应文件中的参数
                    processed.extend(process_args_recursive(rsp_args))
                    continue
                
                # 跳过文件名和输出文件
                arg_clean = arg.strip('"\'')
                if arg_clean.endswith(('.exe', '.c', '.cpp', '.cc', '.cxx', '.o', '.obj')):
                    continue
                
                # 跳过输出相关参数和PCH相关参数
                if arg in ['-o', '-c', '/c', '/Fo'] and i + 1 < len(args_list):
                    skip_next = True
                    continue
                
                # 跳过MSVC预编译头文件参数
                if arg.startswith('/Yc') or arg.startswith('/Fp'):
                    # /Yc 和 /Fp 参数用于创建和指定预编译头文件
                    # 这些参数对于clang解析AST来说不是必需的，且会被误认为链接器参数
                    continue
                
                # 跳过单独的PCH参数
                if arg in ['/Yc', '/Fp'] and i + 1 < len(args_list):
                    skip_next = True
                    continue
                
                # 转换和保留重要的编译参数
                if arg.startswith(('-I', '/I')):
                    if len(arg) > 2:
                        processed.append('-I' + arg[2:].strip('"\''))
                    elif i + 1 < len(args_list):
                        processed.append('-I' + args_list[i+1].strip('"\''))
                        skip_next = True
                elif arg.startswith(('-D', '/D')):
                    if len(arg) > 2:
                        processed.append('-D' + arg[2:])
                    elif i + 1 < len(args_list):
                        processed.append('-D' + args_list[i+1])
                        skip_next = True
                elif arg.startswith('/FI'):
                    # 强制包含文件 - 检查文件是否存在
                    include_file = ""
                    if len(arg) > 3:
                        include_file = arg[3:].strip('"\'')
                    elif i + 1 < len(args_list):
                        include_file = args_list[i+1].strip('"\'')
                        skip_next = True
                    
                    if include_file:
                        # 检查强制包含文件是否存在
                        if not os.path.isabs(include_file) and working_directory:
                            full_include_path = os.path.join(working_directory, include_file)
                        else:
                            full_include_path = include_file
                        
                        if os.path.exists(full_include_path):
                            processed.append('-include')
                            processed.append(include_file)
                        else:
                            self.logger.warning(f"跳过不存在的强制包含文件: {include_file}")
                elif arg.startswith('/imsvc'):
                    # MSVC系统包含路径
                    if len(arg) > 6:
                        processed.append('-isystem')
                        processed.append(arg[6:].strip('"\''))
                    elif i + 1 < len(args_list):
                        processed.append('-isystem')
                        processed.append(args_list[i+1].strip('"\''))
                        skip_next = True
                elif arg.startswith('/std:'):
                    # C++标准参数，转换为clang格式
                    std_version = arg[5:]  # 去掉 '/std:'
                    processed.append(f'-std={std_version}')
                elif arg == '/EHsc':
                    processed.append('-fexceptions')
                elif arg == '/GR-':
                    processed.append('-fno-rtti')
                elif arg in ['/W1', '/W2', '/W3', '/W4']:
                    processed.append('-Wall')
                elif arg.startswith('/'):
                    # 过滤掉会被clang误认为链接器参数的MSVC编译选项
                    msvc_compile_only = [
                        '/Zc:', '/nologo', '/Oi', '/Gy', '/utf-8', '/wd', '/we', '/Ob', '/Ox', '/Ot', 
                        '/GF', '/errorReport:', '/Z7', '/MD', '/fp:', '/Zo', '/Zp', '/clang:'
                    ]
                    # PCH相关参数需要被过滤掉
                    pch_params = ['/Yc', '/Fp', '/Yu']
                    
                    # 只保留真正重要的MSVC参数，过滤掉会导致clang报错的参数
                    if not any(arg.startswith(prefix) for prefix in msvc_compile_only + pch_params + ['/Fo', '/Fe', '/Fd', '/link', '/LIBPATH']):
                        processed.append(arg)
                else:
                    # 保留其他参数
                    processed.append(arg)
            
            return processed
        
        processed_args = process_args_recursive(raw_args)
        
        # 添加基本的Clang编译参数来解决常见编译问题
        essential_clang_args = [
            # 解决constexpr函数和优化相关问题
            '-fno-delete-null-pointer-checks',
            
            # 解决constexpr函数的严格检查问题
            '-Wno-invalid-constexpr',
            '-Wno-constexpr-not-const',
            
            # 诊断格式设置，便于IDE识别错误
            '-fdiagnostics-format=msvc',
            '-fdiagnostics-absolute-paths',
            
            # FP语义设置，确保精确的浮点运算
            '-ffp-contract=off',
            
            # 警告设置
            '-Wall',
            
            # 添加更多兼容性参数来解决解析问题
            '-fms-compatibility',
            '-fms-extensions',
            '-Wno-microsoft',
            '-Wno-unknown-pragmas',
            '-Wno-unused-value',
            '-Wno-ignored-attributes',
        ]
        
        # 检查并添加缺失的Clang参数
        for arg in essential_clang_args:
            if arg not in processed_args:
                processed_args.append(arg)
        
        # 特殊处理头文件解析
        if '-x' in processed_args and 'c++-header' in processed_args:
            # 为头文件解析添加特殊参数
            header_specific_args = [
                '-Wno-pragma-once-outside-header',  # 允许头文件中的#pragma once
                '-Wno-include-next-outside-header', # 允许头文件中的#include_next
            ]
            
            for arg in header_specific_args:
                if arg not in processed_args:
                    processed_args.append(arg)
        
        return processed_args

    def _convert_clang_cl_to_libclang(self, raw_args: List[str], working_directory: str = None) -> List[str]:
        """将clang-cl参数转换为libclang兼容的参数 - 增强版"""
        self.logger.info("开始clang-cl到libclang参数转换")
        
        # 递归解析响应文件并收集所有参数
        all_args = self._expand_response_files(raw_args, working_directory)
        self.logger.debug(f"响应文件展开后参数数量: {len(all_args)}")
        
        # 转换参数
        converted_args = self._convert_args_to_libclang_format(all_args, working_directory)
        
        # 增强：添加强制包含文件目录到包含路径
        converted_args = self._enhance_include_paths_for_forced_includes(converted_args)
        
        # 提取并添加宏定义
        macro_definitions = self._extract_macros_from_forced_includes(all_args, working_directory)
        for macro in macro_definitions:
            macro_arg = f"-D{macro}"
            if macro_arg not in converted_args:
                converted_args.append(macro_arg)
        
        self.logger.info(f"clang-cl转换完成: {len(raw_args)} -> {len(converted_args)} 参数")
        self.logger.info(f"提取宏定义: {len(macro_definitions)} 个")
        
        return converted_args
    
    def _enhance_include_paths_for_forced_includes(self, args: List[str]) -> List[str]:
        """增强：为强制包含文件添加其目录到包含路径，解决相对路径问题"""
        enhanced_args = args.copy()
        forced_include_dirs = set()
        
        # 查找所有 -include 参数
        i = 0
        while i < len(args):
            if args[i] == '-include' and i + 1 < len(args):
                include_file = args[i + 1]
                if os.path.isabs(include_file) and os.path.exists(include_file):
                    include_dir = os.path.dirname(include_file)
                    forced_include_dirs.add(include_dir)
                    
                    # 分析强制包含文件内容，查找相对路径包含
                    try:
                        with open(include_file, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                        
                        import re
                        # 查找所有 #include "相对路径" 语句
                        relative_includes = re.findall(r'#include\s+"([^"]+)"', content)
                        for rel_include in relative_includes:
                            if not os.path.isabs(rel_include):
                                # 计算相对路径可能的基础目录
                                potential_base = include_dir
                                for _ in range(rel_include.count('../')):
                                    potential_base = os.path.dirname(potential_base)
                                
                                # 检查这个基础目录是否存在相对包含的文件
                                full_rel_path = os.path.join(potential_base, rel_include)
                                if os.path.exists(full_rel_path):
                                    rel_dir = os.path.dirname(full_rel_path)
                                    forced_include_dirs.add(rel_dir)
                                    self.logger.debug(f"发现相对包含文件: {rel_include} -> 添加目录: {rel_dir}")
                    except Exception as e:
                        self.logger.debug(f"分析强制包含文件 {include_file} 时出错: {e}")
                
                i += 2
            else:
                i += 1
        
        # 将发现的目录添加为包含路径
        for include_dir in forced_include_dirs:
            include_arg = f'-I{include_dir}'
            if include_arg not in enhanced_args:
                enhanced_args.append(include_arg)
                self.logger.debug(f"添加强制包含文件目录到包含路径: {include_dir}")
        
        return enhanced_args
    
    def _expand_response_files(self, args: List[str], working_directory: str = None) -> List[str]:
        """递归展开响应文件"""
        expanded_args = []
        
        for arg in args:
            if arg.startswith('@'):
                rsp_path = arg[1:].strip('"\'')
                rsp_args = self._parse_response_file(rsp_path, working_directory)
                # 递归处理响应文件中的参数
                expanded_args.extend(self._expand_response_files(rsp_args, working_directory))
            else:
                expanded_args.append(arg)
        
        return expanded_args
    
    def _convert_args_to_libclang_format(self, args: List[str], working_directory: str = None) -> List[str]:
        """将clang-cl参数转换为libclang格式 - 增强版 - 移除硬编码UE宏定义"""
        converted = []
        skip_next = False
        
        for i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            
            # 跳过编译器可执行文件、源文件路径和输出文件
            if (arg.endswith(('.exe', '.c', '.cpp', '.cc', '.cxx', '.o', '.obj')) or 
                ('clang-cl.exe' in arg) or
                (arg.startswith('N:') and (arg.endswith('.cpp') or arg.endswith('.h'))) or
                (arg.startswith('"N:') and (arg.endswith('.cpp"') or arg.endswith('.h"'))) or
                # 处理响应文件中的绝对路径源文件
                (os.path.isabs(arg.strip('"\'')) and arg.strip('"\'').endswith(('.cpp', '.c', '.cc', '.cxx', '.h')))):
                continue
            
            # 跳过输出相关参数
            if arg in ['-o', '/Fo', '/Fe', '/Fd', '-c', '/c']:
                skip_next = True
                continue
            
            # 跳过依赖文件生成参数
            if arg in ['-MD', '-MMD', '-MF', '/Fd'] or arg.startswith('/clang:-M'):
                if arg in ['-MF'] and i + 1 < len(args):
                    skip_next = True  # -MF需要跳过下一个参数（依赖文件路径）
                continue
            
            # 跳过PCH相关参数
            if arg.startswith(('/Yc', '/Yu', '/Fp')):
                if '=' not in arg and i + 1 < len(args):
                    skip_next = True
                continue
            
            # 转换include路径
            if arg.startswith(('/I', '-I')):
                path = arg[2:].strip('"\'') if len(arg) > 2 else (args[i+1].strip('"\'') if i+1 < len(args) else '')
                if path:
                    converted.append(f'-I{path}')
                    if len(arg) == 2:
                        skip_next = True
                continue
            
            # 转换宏定义
            if arg.startswith(('/D', '-D')):
                macro = arg[2:] if len(arg) > 2 else (args[i+1] if i+1 < len(args) else '')
                if macro:
                    converted.append(f'-D{macro}')
                    if len(arg) == 2:
                        skip_next = True
                continue
            
            # 转换强制包含文件 - 增强版：使用绝对路径
            if arg.startswith('/FI'):
                include_file = arg[3:].strip('"\'') if len(arg) > 3 else (args[i+1].strip('"\'') if i+1 < len(args) else '')
                if include_file:
                    # 检查强制包含文件是否存在
                    if not os.path.isabs(include_file) and working_directory:
                        full_include_path = os.path.join(working_directory, include_file)
                    else:
                        full_include_path = include_file
                    
                    if os.path.exists(full_include_path):
                        # 使用绝对路径来避免相对路径解析问题
                        abs_include_path = os.path.abspath(full_include_path)
                        converted.extend(['-include', abs_include_path])
                        self.logger.debug(f"添加强制包含文件 (绝对路径): {abs_include_path}")
                    else:
                        self.logger.warning(f"跳过不存在的强制包含文件: {include_file} (完整路径: {full_include_path})")
                    
                    if len(arg) == 3:
                        skip_next = True
                continue
            
            # 转换系统包含路径
            if arg.startswith('/imsvc'):
                path = arg[6:].strip('"\'') if len(arg) > 6 else (args[i+1].strip('"\'') if i+1 < len(args) else '')
                if path:
                    converted.extend(['-isystem', path])
                    if len(arg) == 6:
                        skip_next = True
                continue
            
            # 转换C++标准
            if arg.startswith('/std:'):
                std_version = arg[5:]
                converted.append(f'-std={std_version}')
                continue
            
            # 转换其他MSVC参数
            if arg == '/EHsc':
                converted.append('-fexceptions')
            elif arg == '/GR-':
                converted.append('-fno-rtti')
            elif arg in ['/W1', '/W2', '/W3', '/W4']:
                converted.append('-Wall')
            elif arg.startswith('/clang:'):
                # 处理 /clang: 参数，提取其中的clang参数
                clang_arg = arg[7:]  # 去掉 '/clang:'
                if clang_arg:
                    converted.append(clang_arg)
            elif arg.startswith('/'):
                # 跳过其他MSVC特定参数，但保留一些重要的
                important_msvc_args = ['/DWIN32', '/D_WINDOWS', '/D_USRDLL']
                if any(arg.startswith(prefix) for prefix in important_msvc_args):
                    # 转换为 -D 格式
                    if arg.startswith('/D'):
                        converted.append('-D' + arg[2:])
                # 其他 /xxx 参数跳过
                continue
            else:
                # 保留其他参数，但过滤一些明显的编译器参数
                if not any(keyword in arg.lower() for keyword in ['clang-cl', '.exe', '.dll']):
                    converted.append(arg)
        
        # 添加libclang需要的基本参数
        essential_args = [
            '-fms-compatibility',
            '-fms-extensions',
            '-Wno-microsoft',
            '-Wno-unknown-pragmas',
            '-Wno-unused-value',
            '-Wno-ignored-attributes',
            '-fno-delete-null-pointer-checks',
            '-Wno-invalid-constexpr',
            '-Wno-constexpr-not-const'
        ]
        
        for arg in essential_args:
            if arg not in converted:
                converted.append(arg)
        
        return converted
    
    def _extract_macros_from_forced_includes(self, args: List[str], working_directory: str = None) -> List[str]:
        """从强制包含文件中提取宏定义"""
        macros = []
        
        # 查找强制包含的文件
        forced_includes = []
        skip_next = False
        
        for i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            
            if arg.startswith('/FI'):
                if len(arg) > 3:
                    forced_includes.append(arg[3:].strip('"\''))
                elif i + 1 < len(args):
                    forced_includes.append(args[i+1].strip('"\''))
                    skip_next = True
        
        # 解析每个强制包含文件中的宏定义
        for include_file in forced_includes:
            if not os.path.isabs(include_file) and working_directory:
                full_path = os.path.join(working_directory, include_file)
            else:
                full_path = include_file
            
            if include_file.endswith('Definitions.h') and os.path.exists(full_path):
                self.logger.debug(f"解析Definitions.h文件: {full_path}")
                file_macros = self._parse_definitions_file(full_path)
                macros.extend(file_macros)
                self.logger.info(f"从{include_file}提取了{len(file_macros)}个宏定义")
        
        return macros
    
    def _parse_definitions_file(self, file_path: str) -> List[str]:
        """解析Definitions.h文件中的宏定义"""
        macros = []
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            import re
            
            # 匹配 #define 宏定义
            define_pattern = r'#define\s+([A-Z_][A-Z0-9_]*)\s+(.+?)(?=\n|$)'
            matches = re.findall(define_pattern, content, re.MULTILINE)
            
            for name, value in matches:
                # 清理值，移除注释和多余空白
                value = re.sub(r'//.*$', '', value).strip()
                value = re.sub(r'/\*.*?\*/', '', value, flags=re.DOTALL).strip()
                
                if value:
                    macros.append(f"{name}={value}")
                else:
                    macros.append(name)
            
        except Exception as e:
            self.logger.warning(f"解析Definitions.h文件失败: {e}")
        
        return macros

    def _normalize_path(self, path: str) -> str:
        """规范化路径"""
        return str(Path(path).resolve()).replace('\\', '/')
    
    def _validate_include_paths(self, file_path: str, working_directory: str, compile_args: List[str] = None):
        """增强版验证关键的include文件是否能够找到"""
        if compile_args is None:
            compile_args = []
            
        # 提取所有-I参数作为搜索路径
        include_search_paths = [working_directory]
        i = 0
        while i < len(compile_args):
            arg = compile_args[i]
            if arg.startswith('-I'):
                if len(arg) > 2:
                    include_search_paths.append(arg[2:].strip('"\''))
                elif i + 1 < len(compile_args):
                    include_search_paths.append(compile_args[i + 1].strip('"\''))
                    i += 1
            i += 1
        
        try:
            # 读取文件内容，检查更多include语句
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()[:50]  # 检查前50行以获得更全面的验证
            
            include_stats = {
                'found': 0,
                'missing': 0,
                'total': 0,
                'missing_files': []
            }
            
            for i, line in enumerate(lines, 1):
                line = line.strip()
                if line.startswith('#include'):
                    include_stats['total'] += 1
                    
                    # 处理 #include "..." 格式
                    if '"' in line:
                        start_quote = line.find('"') + 1
                        end_quote = line.rfind('"')
                        if start_quote > 0 and end_quote > start_quote:
                            include_path = line[start_quote:end_quote]
                            if self._find_include_file(include_path, include_search_paths):
                                include_stats['found'] += 1
                                self.logger.debug(f"✓ Include文件存在: {include_path}")
                            else:
                                include_stats['missing'] += 1
                                include_stats['missing_files'].append(include_path)
                                self.logger.warning(f"✗ Include文件不存在: {include_path}")
                                # 尝试查找文件是否在其他位置
                                self._suggest_include_path(include_path, working_directory, include_search_paths)
                    
                    # 处理 #include <...> 格式 (系统头文件，通常不需要验证)
                    elif '<' in line and '>' in line:
                        start_bracket = line.find('<') + 1
                        end_bracket = line.rfind('>')
                        if start_bracket > 0 and end_bracket > start_bracket:
                            include_path = line[start_bracket:end_bracket]
                            self.logger.debug(f"系统头文件 (跳过验证): {include_path}")
            
            # 输出验证统计信息
            if include_stats['total'] > 0:
                success_rate = (include_stats['found'] / include_stats['total']) * 100
                self.logger.info(f"Include路径验证结果: {include_stats['found']}/{include_stats['total']} 找到 ({success_rate:.1f}%)")
                
                if include_stats['missing'] > 0:
                    self.logger.warning(f"缺失的头文件: {include_stats['missing_files']}")
                    return False
            
            return True
            
        except Exception as e:
            self.logger.debug(f"验证include路径时出错: {e}")
            return False
    
    def _find_include_file(self, include_path: str, search_paths: List[str]) -> bool:
        """在搜索路径中查找include文件"""
        for search_path in search_paths:
            if not search_path:
                continue
            full_path = os.path.join(search_path, include_path)
            if os.path.exists(full_path):
                return True
        return False
    
    def _suggest_include_path(self, include_path: str, working_directory: str, search_paths: List[str] = None):
        """建议可能的include路径"""
        filename = os.path.basename(include_path)
        
        # 在所有搜索路径中查找文件
        search_dirs = search_paths if search_paths else [working_directory]
        
        found_suggestions = []
        for search_dir in search_dirs:
            if not search_dir or not os.path.exists(search_dir):
                continue
                
            try:
                for root, dirs, files in os.walk(search_dir):
                    if filename in files:
                        found_path = os.path.join(root, filename)
                        rel_path = os.path.relpath(found_path, search_dir)
                        found_suggestions.append((search_dir, rel_path, found_path))
                        
                        # 限制搜索结果数量
                        if len(found_suggestions) >= 3:
                            break
            except Exception as e:
                self.logger.debug(f"搜索目录 {search_dir} 时出错: {e}")
        
        if found_suggestions:
            self.logger.info(f"  可能的位置:")
            for search_dir, rel_path, full_path in found_suggestions:
                self.logger.info(f"    在 {search_dir}: {rel_path}")
        else:
            self.logger.info(f"  未找到文件 {filename}")
            
        return found_suggestions
    

    
    def _get_optimal_working_directory(self, directory: str, file_path: str) -> str:
        """获取最优的工作目录"""
        # 直接使用原始目录，构建系统的配置是正确的
        return directory

    def _fix_include_args(self, args: List[str], working_directory: str) -> List[str]:
        """修复 -include 参数：
        A: 如果文件是普通C++文件，直接强制包含绝对路径
        B: 如果文件内部全是相对路径引用，则解析内部引用并替换为绝对路径的直接包含
        """
        fixed_args = []
        i = 0
        fixed_includes = []
        
        # 导入路径解析器
        try:
            from .path_resolver import PathResolver
            path_resolver = PathResolver(working_directory, self.logger)
        except ImportError:
            self.logger.warning("无法导入路径解析器，使用基本路径处理")
            path_resolver = None
        
        while i < len(args):
            arg = args[i]
            
            if arg == '-include' and i + 1 < len(args):
                include_file = args[i + 1]
                
                # 获取文件的绝对路径
                abs_include_path = self._get_absolute_path(include_file, working_directory)
                
                if abs_include_path and os.path.exists(abs_include_path):
                    # 分析文件内容，判断是否需要特殊处理
                    if self._file_contains_only_relative_includes(abs_include_path):
                        # 情况B: 文件内部全是相对路径引用，解析内部引用
                        internal_includes = self._extract_and_resolve_internal_includes(
                            abs_include_path, working_directory, path_resolver
                        )
                        
                        if internal_includes:
                            # 添加解析后的内部包含文件
                            for internal_file in internal_includes:
                                fixed_args.extend(['-include', internal_file])
                            
                            fixed_includes.append(f"解析内部引用文件: {include_file} -> {len(internal_includes)} 个内部文件")
                            self.logger.info(f"解析内部引用: {include_file} -> {internal_includes}")
                        else:
                            # 如果解析失败，还是包含原文件
                            fixed_args.extend(['-include', abs_include_path])
                            fixed_includes.append(f"内部引用解析失败，保留原文件: {abs_include_path}")
                    else:
                        # 情况A: 普通C++文件，直接强制包含绝对路径
                        fixed_args.extend(['-include', abs_include_path])
                        fixed_includes.append(f"强制包含文件 (绝对路径): {abs_include_path}")
                        self.logger.debug(f"处理强制包含文件: {include_file} -> {abs_include_path}")
                else:
                    # 文件不存在，跳过
                    fixed_includes.append(f"跳过不存在的文件: {include_file}")
                    self.logger.warning(f"跳过不存在的强制包含文件: {include_file}")
                
                i += 2  # 跳过 -include 和文件路径
            elif arg == '-include-pch' and i + 1 < len(args):
                # -include-pch 参数，保持原样
                include_file = args[i + 1]
                
                if os.path.exists(include_file):
                    fixed_args.extend(['-include-pch', include_file])
                    fixed_includes.append(f"保留PCH文件: {include_file}")
                    self.logger.debug(f"保留PCH文件: {include_file}")
                else:
                    fixed_includes.append(f"跳过不存在PCH文件: {include_file}")
                    self.logger.warning(f"跳过不存在的PCH文件: {include_file}")
                
                i += 2  # 跳过 -include-pch 和文件路径
            else:
                fixed_args.append(arg)
                i += 1
        
        if fixed_includes:
            self.logger.info(f"处理了 {len(fixed_includes)} 个强制包含文件:")
            for fixed in fixed_includes:
                self.logger.info(f"  - {fixed}")
        
        return fixed_args
    
    def _get_absolute_path(self, file_path: str, working_directory: str) -> Optional[str]:
        """获取文件的绝对路径"""
        if os.path.isabs(file_path):
            return file_path if os.path.exists(file_path) else None
        
        # 尝试相对于工作目录解析
        candidate_path = os.path.join(working_directory, file_path)
        if os.path.exists(candidate_path):
            return os.path.abspath(candidate_path)
        
        return None
    
    def _file_contains_only_relative_includes(self, file_path: str) -> bool:
        """检查文件是否仅包含相对路径的include语句（没有其他实际代码内容）"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            import re
            
            # 移除注释和空行，获取有效内容
            lines = content.split('\n')
            effective_lines = []
            
            in_block_comment = False
            for line in lines:
                line = line.strip()
                
                # 跳过空行
                if not line:
                    continue
                
                # 处理块注释
                if '/*' in line:
                    in_block_comment = True
                if '*/' in line:
                    in_block_comment = False
                    continue
                if in_block_comment:
                    continue
                
                # 跳过单行注释
                if line.startswith('//'):
                    continue
                
                # 移除行末注释
                if '//' in line:
                    line = line[:line.index('//')].strip()
                    if not line:
                        continue
                
                effective_lines.append(line)
            
            # 如果没有有效内容，返回False
            if not effective_lines:
                return False
            
            # 检查每一行有效内容
            for line in effective_lines:
                # 如果不是 #include 语句，说明有其他代码内容
                if not re.match(r'^\s*#include\s+[<"]([^>"]+)[>"]', line):
                    return False
            
            # 所有有效行都是include语句，现在检查是否都是相对路径
            include_statements = re.findall(r'#include\s+[<"]([^>"]+)[>"]', '\n'.join(effective_lines))
            
            if not include_statements:
                return False
            
            # 检查所有include是否都是相对路径
            for include in include_statements:
                # 相对路径的特征：包含 '../' 或者不是绝对路径（不以/开头，不是系统头文件）
                # 系统头文件通常用<>包围且不包含路径分隔符，用户头文件用""包围
                if not ('../' in include or ('/' in include and not include.startswith('/')) or 
                       (not '/' in include and '"' in effective_lines[0])):  # 简单的头文件名且用引号包围
                    return False
            
            return True
            
        except Exception as e:
            self.logger.debug(f"分析文件内容失败: {file_path}, 错误: {e}")
            return False  # 分析失败时视为普通文件
    
    def _extract_and_resolve_internal_includes(self, file_path: str, working_directory: str, path_resolver) -> List[str]:
        """提取并解析文件内部的包含文件"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            import re
            # 查找所有 #include 语句
            include_statements = re.findall(r'#include\s+[<"]([^>"]+)[>"]', content)
            
            resolved_files = []
            file_dir = os.path.dirname(file_path)
            
            for include in include_statements:
                # 跳过系统头文件
                if include.startswith('<') or not ('../' in include or include.endswith('.h')):
                    continue
                
                # 解析相对路径
                resolved_path = None
                
                if path_resolver:
                    # 使用路径解析器
                    resolved_path = path_resolver._resolve_single_path(include, file_dir)
                else:
                    # 基本路径解析
                    if '../' in include:
                        # 处理相对路径
                        base_dir = file_dir
                        parts = include.split('/')
                        for part in parts:
                            if part == '..':
                                base_dir = os.path.dirname(base_dir)
                            elif part and part != '.':
                                base_dir = os.path.join(base_dir, part)
                        
                        if os.path.exists(base_dir):
                            resolved_path = os.path.abspath(base_dir)
                    else:
                        # 相对于当前文件目录
                        candidate = os.path.join(file_dir, include)
                        if os.path.exists(candidate):
                            resolved_path = os.path.abspath(candidate)
                
                if resolved_path and os.path.exists(resolved_path):
                    resolved_files.append(resolved_path)
                    self.logger.debug(f"解析内部包含: {include} -> {resolved_path}")
            
            return resolved_files
            
        except Exception as e:
            self.logger.error(f"提取内部包含文件失败: {file_path}, 错误: {e}")
            return []
    
    def _fix_xclang_args(self, args: List[str]) -> List[str]:
        """修复 -Xclang 参数格式问题，特别是 -Xclang -x -Xclang "c++" 的错误格式"""
        fixed_args = []
        i = 0
        fixed_count = 0
        
        while i < len(args):
            arg = args[i]
            
            # 检查是否是有问题的 -Xclang -x -Xclang "c++" 序列
            if (arg == '-Xclang' and 
                i + 3 < len(args) and 
                args[i + 1] == '-x' and 
                args[i + 2] == '-Xclang' and 
                args[i + 3] in ['"c++"', "'c++'"]):
                
                # 修复格式：替换为正确的参数
                fixed_args.extend(['-x', 'c++'])
                self.logger.info(f"修复了有问题的 -Xclang 序列: {args[i:i+4]} -> ['-x', 'c++']")
                fixed_count += 1
                i += 4  # 跳过整个序列
                continue
            
            # 检查其他可能的 -Xclang 问题
            elif arg == '-Xclang' and i + 1 < len(args):
                next_arg = args[i + 1]
                
                # 如果下一个参数被引号包围，去掉引号
                if ((next_arg.startswith('"') and next_arg.endswith('"')) or 
                    (next_arg.startswith("'") and next_arg.endswith("'"))):
                    
                    fixed_next_arg = next_arg[1:-1]
                    fixed_args.extend(['-Xclang', fixed_next_arg])
                    self.logger.info(f"修复了引号问题: -Xclang {next_arg} -> -Xclang {fixed_next_arg}")
                    fixed_count += 1
                    i += 2
                    continue
            
            # 保留原始参数
            fixed_args.append(arg)
            i += 1
        
        if fixed_count > 0:
            self.logger.info(f"成功修复了 {fixed_count} 个 -Xclang 参数问题")
        
        return fixed_args
    
    def _create_permissive_args(self, args: List[str]) -> List[str]:
        """创建宽松的解析参数，移除可能导致问题的严格检查"""
        permissive_args = []
        skip_next = False
        
        for i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            
            # 跳过可能导致严格检查的参数
            if arg in ['-Werror', '-pedantic', '-pedantic-errors']:
                continue
            
            # 跳过某些可能有问题的include参数
            if arg == '-include' and i + 1 < len(args):
                include_file = args[i + 1]
                # 只保留存在的include文件
                if os.path.exists(include_file):
                    permissive_args.extend([arg, include_file])
                skip_next = True
                continue
            
            permissive_args.append(arg)
        
        # 添加宽松的编译选项
        permissive_options = [
            '-Wno-error',
            '-Wno-unknown-pragmas',
            '-Wno-ignored-attributes',
            '-Wno-unused-value',
            '-Wno-microsoft',
            '-fms-compatibility',
            '-fms-extensions'
        ]
        
        for option in permissive_options:
            if option not in permissive_args:
                permissive_args.append(option)
        
        return permissive_args
    
    def _create_minimal_args(self, args: List[str], file_path: str) -> List[str]:
        """创建最小化的参数集，只保留最基本的编译参数"""
        minimal_args = []
        skip_next = False
        
        # 只保留最基本的参数
        for i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            
            # 保留include路径
            if arg.startswith('-I'):
                minimal_args.append(arg)
                continue
            
            # 保留基本的宏定义（但跳过复杂的）
            if arg.startswith('-D'):
                macro = arg[2:] if len(arg) > 2 else (args[i+1] if i+1 < len(args) else '')
                if macro and not any(complex_char in macro for complex_char in ['(', ')', '{', '}', '"']):
                    minimal_args.append(arg)
                    if len(arg) == 2:
                        skip_next = True
                continue
            
            # 保留C++标准
            if arg.startswith('-std='):
                minimal_args.append(arg)
                continue
            
            # 跳过所有-include参数
            if arg == '-include':
                skip_next = True
                continue
        
        # 添加基本的兼容性参数
        basic_args = [
            '-fms-compatibility',
            '-fms-extensions',
            '-Wno-microsoft',
            '-Wno-unknown-pragmas'
        ]
        
        # 根据文件类型添加特定参数
        if file_path.endswith('.h') or file_path.endswith('.hpp'):
            basic_args.extend(['-x', 'c++-header'])
        
        for arg in basic_args:
            if arg not in minimal_args:
                minimal_args.append(arg)
        
        return minimal_args

    @profile_function("ClangParser.parse_file")
    def parse_file(self, file_path: str) -> Optional[ParsedFile]:
        """解析单个文件 - 深度性能分析版 - 增强错误处理"""
        logger = DetailedLogger(f"解析文件: {Path(file_path).name}")
        
        with profiler.timer("parse_file_validation", {'file': file_path}):
            # 尝试直接匹配标准化路径
            normalized_file_path = self._normalize_path(file_path)
            compile_info = self.compile_commands.get(normalized_file_path)
            
            # 如果直接匹配失败，尝试其他匹配策略
            if not compile_info:
                # 尝试匹配文件名（用于调试）
                file_name = os.path.basename(file_path)
                matching_keys = [key for key in self.compile_commands.keys() if os.path.basename(key) == file_name]
                
                if matching_keys:
                    self.logger.info(f"文件路径匹配失败，但找到同名文件: {matching_keys}")
                    # 使用第一个匹配的文件
                    compile_info = self.compile_commands[matching_keys[0]]
                    normalized_file_path = matching_keys[0]
                else:
                    self.logger.warning(f"在 compile_commands.json 中未找到文件 '{file_path}' 的编译命令")
                    self.logger.debug(f"已注册的文件路径: {list(self.compile_commands.keys())[:5]}...")
                    return None

            args = compile_info["args"]
            directory = compile_info["directory"]

            if not Path(file_path).exists():
                self.logger.error(f"文件路径不存在: {file_path}")
                return None
            if not Path(directory).exists():
                self.logger.error(f"工作目录不存在: {directory}")
                return None

        logger.checkpoint("验证完成", args_count=len(args), directory=directory)

        # 检查缓存
        with profiler.timer("cache_lookup", {'file': file_path}):
            cache_key = f"{file_path}:{hash(tuple(args))}"
            if self._tu_cache:
                cached_tu = self._tu_cache.get(cache_key)
                if cached_tu:
                    logger.checkpoint("缓存命中", cache_key=cache_key[:50])
                    return ParsedFile(
                        file_path=file_path,
                        translation_unit=cached_tu,
                        success=True,
                        diagnostics=[],
                        parse_time=0.0  # 缓存命中，解析时间为0
                    )

        logger.checkpoint("缓存未命中，开始解析")

        try:
            with profiler.timer("clang_parse_setup"):
                start_time = time.perf_counter()
                # 移除工作目录切换逻辑 - 让clang直接处理路径
                
                # 获取工作目录但不切换
                working_directory = self._get_optimal_working_directory(directory, file_path)
                
                # 验证工作目录存在但不切换
                if not os.path.exists(working_directory):
                    self.logger.error(f"工作目录不存在: {working_directory}")
                    return None
                
                self.logger.debug(f"使用工作目录: {working_directory} (不切换)")
            
            logger.checkpoint("环境设置完成", working_dir=working_directory)
            
            # 预防性修复编译参数 - 在解析之前就修复include问题
            with profiler.timer("fix_include_args", {'file': file_path}):
                # 添加详细的工作目录日志
                current_cwd = os.getcwd()
                self.logger.info(f"=== 工作目录调试信息 ===")
                self.logger.info(f"当前进程工作目录: {current_cwd}")
                self.logger.info(f"编译命令指定目录: {working_directory}")
                self.logger.info(f"目录是否存在: 当前={os.path.exists(current_cwd)}, 编译={os.path.exists(working_directory)}")
                
                fixed_args = self._fix_include_args(args, working_directory)
                if len(fixed_args) != len(args):
                    self.logger.info(f"预处理修复了编译参数: {len(args)} -> {len(fixed_args)}")
                
                # 修复 -Xclang 参数格式问题
                fixed_args = self._fix_xclang_args(fixed_args)
                self.logger.debug(f"Xclang参数修复完成，参数数量: {len(fixed_args)}")
                
            logger.checkpoint("编译参数修复完成", fixed_args_count=len(fixed_args))
            
            # 增强版头文件路径验证
            with profiler.timer("validate_include_paths", {'file': file_path}):
                self.logger.info(f"=== 头文件路径验证 ===")
                validation_result = self._validate_include_paths(file_path, working_directory, fixed_args)
                if not validation_result:
                    self.logger.warning(f"头文件路径验证发现缺失文件，但继续解析: {file_path}")
                else:
                    self.logger.info(f"头文件路径验证通过: {file_path}")
                    
            logger.checkpoint("头文件路径验证完成", validation_passed=validation_result)
            
            # 优化clang解析选项 - 移除PARSE_SKIP_FUNCTION_BODIES以保持函数调用关系提取
            with profiler.timer("clang_translation_unit_parse", {'file': file_path, 'args_count': len(fixed_args)}):
                # 平衡性能与功能完整性的解析选项
                parse_options = (
                    clang.TranslationUnit.PARSE_INCOMPLETE |
                    clang.TranslationUnit.PARSE_PRECOMPILED_PREAMBLE |
                    clang.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
                )
                
                # 调用libclang之前的最终调试信息
                self.logger.info(f"=== libclang调用调试信息 ===")
                self.logger.info(f"解析文件: {file_path}")
                self.logger.info(f"当前工作目录: {os.getcwd()}")
                self.logger.info(f"使用参数数量: {len(fixed_args)}")
                self.logger.info(f"关键include参数:")
                for i, arg in enumerate(fixed_args):
                    if arg in ['-include', '-include-pch'] and i + 1 < len(fixed_args):
                        include_file = fixed_args[i + 1]
                        exists = os.path.exists(include_file)
                        self.logger.info(f"  {arg} {include_file} (存在: {'✅' if exists else '❌'})")
                
                try:
                    tu = self.index.parse(
                        file_path, 
                        args=fixed_args,  # 使用修复后的参数
                        options=parse_options
                    )
                except clang.TranslationUnitLoadError as e:
                    self.logger.error(f"libclang标准解析失败，异常详情: {e}")
                    self.logger.error(f"失败时的工作目录: {os.getcwd()}")
                    self.logger.error(f"失败时的参数: {fixed_args}")
                    raise
            
            with profiler.timer("clang_parse_cleanup"):
                parse_time = time.perf_counter() - start_time

            logger.checkpoint("Clang解析完成", parse_time=f"{parse_time:.4f}s")

            if not tu:
                self.logger.error(f"Clang 未能为文件 '{file_path}' 创建翻译单元。")
                return None
            
            # 缓存TranslationUnit
            with profiler.timer("cache_store"):
                if self._tu_cache:
                    self._tu_cache.put(cache_key, tu)
            
            with profiler.timer("diagnostic_check"):
                errors = [d for d in tu.diagnostics if d.severity >= Diagnostic.Error]
                success = len(errors) == 0
                
                if errors:
                    self.logger.warning(f"文件 '{file_path}' 解析时出现 {len(errors)} 个错误。")
                    for diag in errors:
                        self.logger.error(
                            f"  - {_get_severity_name(diag.severity)}: {diag.spelling}\n"
                            f"    at {diag.location.file}:{diag.location.line}:{diag.location.column}"
                        )

            total_time = logger.finish("解析成功")
            
            #if total_time > 1.0:  # 如果单个文件解析超过1秒，记录警告
            #    self.logger.warning(f"⚠️  文件 {Path(file_path).name} 解析耗时过长: {total_time:.2f}s")

            return ParsedFile(file_path=file_path, success=success, translation_unit=tu, diagnostics=[], parse_time=parse_time)

        except Exception as e:
            self.logger.error(f"解析文件 '{file_path}' 时发生未知异常: {e}\n{traceback.format_exc()}")
            # 工作目录恢复逻辑已移除（不再需要切换工作目录）
            return ParsedFile(
                file_path=file_path,
                translation_unit=None,
                success=False,
                diagnostics=[],
                parse_time=0.0
            )
    
    def cleanup(self):
        """清理资源"""
        # 清理缓存
        self.clear_cache()
    
    def clear_cache(self):
        """清空TranslationUnit缓存"""
        if self._tu_cache:
            self._tu_cache.clear()
            self.logger.info("TranslationUnit缓存已清空")

    def __del__(self):
        """析构函数"""
        try:
            self.cleanup()
        except:
            pass  # 忽略析构时的异常


def create_enhanced_parser(**kwargs) -> ClangParser:
    """
    创建增强版解析器的便利函数
    
    Args:
        **kwargs: 传递给ClangParser的参数
    
    Returns:
        ClangParser实例
    """
    return ClangParser(**kwargs)