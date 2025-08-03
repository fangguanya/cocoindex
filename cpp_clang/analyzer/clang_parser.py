"""
Clang Parser Module - 性能优化版 - 修复版
"""

import os
import json
import time
import subprocess
import platform
import shlex
import traceback
import threading
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
                 global_nodes: Dict[str, Any], file_mappings: Dict[str, Any], stats: Dict[str, Any]):
        self.file_path = file_path
        self.success = success
        self.parse_time = parse_time
        self.extraction_time = extraction_time
        self.functions = functions
        self.classes = classes
        self.namespaces = namespaces
        self.global_nodes = global_nodes
        self.file_mappings = file_mappings
        self.stats = stats
    
    @staticmethod
    def empty_result(file_path: str, error_msg: str = "") -> 'SerializableExtractedData':
        return SerializableExtractedData(
            file_path=file_path, success=False, parse_time=0.0, extraction_time=0.0,
            functions={}, classes={}, namespaces={}, global_nodes={}, file_mappings={},
            stats={"error": error_msg}
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
    """Clang解析器 - 支持compile_commands.json和动态编译参数 - 性能优化版 - 修复版"""
    
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
        """将clang-cl参数转换为libclang兼容的参数"""
        self.logger.info("开始clang-cl到libclang参数转换")
        
        # 递归解析响应文件并收集所有参数
        all_args = self._expand_response_files(raw_args, working_directory)
        self.logger.debug(f"响应文件展开后参数数量: {len(all_args)}")
        
        # 转换参数
        converted_args = self._convert_args_to_libclang_format(all_args, working_directory)
        
        # 提取并添加宏定义
        macro_definitions = self._extract_macros_from_forced_includes(all_args, working_directory)
        for macro in macro_definitions:
            macro_arg = f"-D{macro}"
            if macro_arg not in converted_args:
                converted_args.append(macro_arg)
        
        self.logger.info(f"clang-cl转换完成: {len(raw_args)} -> {len(converted_args)} 参数")
        self.logger.info(f"提取宏定义: {len(macro_definitions)} 个")
        
        return converted_args
    
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
        """将clang-cl参数转换为libclang格式 - 修复版"""
        converted = []
        skip_next = False
        
        for i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            
            # 跳过编译器可执行文件、源文件路径和输出文件 - 修复版
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
            
            # 转换强制包含文件 - 修复版：检查文件是否存在
            if arg.startswith('/FI'):
                include_file = arg[3:].strip('"\'') if len(arg) > 3 else (args[i+1].strip('"\'') if i+1 < len(args) else '')
                if include_file:
                    # 检查强制包含文件是否存在
                    if not os.path.isabs(include_file) and working_directory:
                        full_include_path = os.path.join(working_directory, include_file)
                    else:
                        full_include_path = include_file
                    
                    if os.path.exists(full_include_path):
                        converted.extend(['-include', include_file])
                    else:
                        self.logger.warning(f"跳过不存在的强制包含文件: {include_file}")
                    
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
    
    def _validate_include_paths(self, file_path: str, working_directory: str):
        """验证关键的include文件是否能够找到"""
        try:
            # 读取文件内容，检查前几个include语句
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()[:10]  # 只检查前10行
            
            for i, line in enumerate(lines, 1):
                line = line.strip()
                if line.startswith('#include "'):
                    # 提取include路径
                    start_quote = line.find('"') + 1
                    end_quote = line.rfind('"')
                    if start_quote > 0 and end_quote > start_quote:
                        include_path = line[start_quote:end_quote]
                        
                        # 检查相对路径文件是否存在
                        if not os.path.isabs(include_path):
                            full_include_path = os.path.join(working_directory, include_path)
                            if os.path.exists(full_include_path):
                                self.logger.debug(f"✓ Include文件存在: {include_path}")
                            else:
                                self.logger.warning(f"✗ Include文件不存在: {include_path} (完整路径: {full_include_path})")
                                # 尝试查找文件是否在其他位置
                                self._suggest_include_path(include_path, working_directory)
        except Exception as e:
            self.logger.debug(f"验证include路径时出错: {e}")
    
    def _suggest_include_path(self, include_path: str, working_directory: str):
        """建议可能的include路径"""
        filename = os.path.basename(include_path)
        # 在工作目录及其子目录中搜索文件
        for root, dirs, files in os.walk(working_directory):
            if filename in files:
                found_path = os.path.join(root, filename)
                rel_path = os.path.relpath(found_path, working_directory)
                self.logger.info(f"  建议路径: {rel_path}")
                break
    
    def _get_optimal_working_directory(self, directory: str, file_path: str) -> str:
        """获取最优的工作目录"""
        # 直接使用原始目录，构建系统的配置是正确的
        return directory

    @profile_function("ClangParser.parse_file")
    def parse_file(self, file_path: str) -> Optional[ParsedFile]:
        """解析单个文件 - 深度性能分析版"""
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
            
            # 优化clang解析选项 - 移除PARSE_SKIP_FUNCTION_BODIES以保持函数调用关系提取
            with profiler.timer("clang_translation_unit_parse", {'file': file_path, 'args_count': len(args)}):
                # 平衡性能与功能完整性的解析选项
                parse_options = (
                    clang.TranslationUnit.PARSE_INCOMPLETE |
                    clang.TranslationUnit.PARSE_PRECOMPILED_PREAMBLE |
                    clang.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
                )
                
                try:
                    tu = self.index.parse(
                        file_path, 
                        args=args, 
                        options=parse_options
                    )
                except clang.TranslationUnitLoadError as e:
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
            
            if total_time > 1.0:  # 如果单个文件解析超过1秒，记录警告
                self.logger.warning(f"⚠️  文件 {Path(file_path).name} 解析耗时过长: {total_time:.2f}s")

            return ParsedFile(file_path=file_path, success=success, translation_unit=tu, diagnostics=[], parse_time=parse_time)

        except clang.TranslationUnitLoadError as e:
            # 专门处理TranslationUnitLoadError
            self.logger.error(f"解析文件 '{file_path}' 时发生翻译单元加载错误: {e}")
            if "Module.GMESDK.cpp" in file_path:
                self.logger.info(f"跳过已知问题文件: {file_path}")
                # 返回一个失败的解析结果，但不中断整个流程
                return ParsedFile(
                    file_path=file_path,
                    translation_unit=None,
                    success=False,
                    diagnostics=[],
                    parse_time=0.0
                )
            else:
                # 对于其他文件，也返回失败结果而不是None
                return ParsedFile(
                    file_path=file_path,
                    translation_unit=None,
                    success=False,
                    diagnostics=[],
                    parse_time=0.0
                )
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