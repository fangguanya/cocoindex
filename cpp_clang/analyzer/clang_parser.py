"""
Clang Parser Module - 性能优化版

Core C++ parsing functionality using libclang. Handles AST generation,
symbol resolution, and extraction of language constructs like functions,
classes, namespaces, and templates.

性能优化：
- 移除@dataclass装饰器以提升对象创建性能
- 添加TranslationUnit缓存机制
- 优化编译参数处理
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
    """Clang解析器 - 支持compile_commands.json和动态编译参数 - 性能优化版"""
    
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
        """
        解析响应文件(.rsp)并返回参数列表
        
        Args:
            rsp_path: 响应文件路径（可能是相对路径）
            working_directory: 工作目录，用于解析相对路径
            
        Returns:
            解析后的参数列表
        """
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
    
    

    def _process_compile_args(self, raw_args: List[str], working_directory: str = None) -> List[str]:
        """处理和清理编译参数，包括响应文件展开 - 修复版"""
        
        def process_args_recursive(args_list: List[str]) -> List[str]:
            """递归处理参数列表，包括响应文件展开"""
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
                    # 强制包含文件
                    if len(arg) > 3:
                        processed.append('-include')
                        processed.append(arg[3:].strip('"\''))
                    elif i + 1 < len(args_list):
                        processed.append('-include')
                        processed.append(args_list[i+1].strip('"\''))
                        skip_next = True
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
        
        # 参考UE官方ClangToolChain.cs，添加必要的Clang编译参数
        # 这些参数解决了UE项目中的各种编译问题
        
        # 添加UE官方使用的关键Clang参数
        ue_clang_args = [
            # 解决constexpr函数和优化相关问题 (参考ClangToolChain.cs:655)
            '-fno-delete-null-pointer-checks',
            
            # 解决constexpr函数的严格检查问题
            # 将constexpr相关的错误降级为警告，然后禁用这些警告
            '-Wno-invalid-constexpr',
            '-Wno-constexpr-not-const',
            
            # 诊断格式设置，便于IDE识别错误 (参考ClangToolChain.cs:586)
            '-fdiagnostics-format=msvc',
            '-fdiagnostics-absolute-paths',
            
            # FP语义设置，确保精确的浮点运算 (参考ClangToolChain.cs:617)
            '-ffp-contract=off',
            
            # 警告设置 (参考ClangToolChain.cs:564)
            '-Wall',
        ]
        
        # 检查并添加缺失的UE Clang参数
        for arg in ue_clang_args:
            if arg not in processed_args:
                processed_args.append(arg)
        
        # 移除硬编码的宏定义，让项目的Definitions.h来处理
        # UE官方通过CompileEnvironment.Definitions来管理宏定义，而不是硬编码
        
        
        
        return processed_args

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
        """
        获取最优的工作目录
        
        对于UE项目，应该使用compile_commands.json中指定的原始工作目录，
        因为UnrealBuildTool已经正确配置了相对路径。
        
        Args:
            directory: compile_commands.json中指定的目录
            file_path: 要编译的文件路径
            
        Returns:
            最优的工作目录路径
        """
        # 直接使用原始目录，UnrealBuildTool的配置是正确的
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
                original_cwd = os.getcwd()
                
                # 智能处理UE项目的工作目录
                working_directory = self._get_optimal_working_directory(directory, file_path)
                
                # 确保工作目录存在且切换成功
                if not os.path.exists(working_directory):
                    self.logger.error(f"工作目录不存在: {working_directory}")
                    return None
                
                self.logger.debug(f"切换工作目录: {original_cwd} -> {working_directory}")
                os.chdir(working_directory)
            
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
                    if is_problematic_file:
                        # 对于已知的问题文件，尝试更简化的解析选项
                        self.logger.warning(f"文件 {file_path} 解析失败，尝试简化解析选项: {e}")
                        try:
                            simplified_parse_options = clang.TranslationUnit.PARSE_INCOMPLETE
                            tu = self.index.parse(
                                file_path,
                                args=args[:10],  # 只使用前10个参数，避免复杂参数导致的问题
                                options=simplified_parse_options
                            )
                            self.logger.info(f"使用简化选项成功解析文件: {file_path}")
                        except Exception as e2:
                            self.logger.error(f"即使使用简化选项也无法解析文件 {file_path}: {e2}")
                            # 创建一个空的解析结果，标记为失败但不中断整个流程
                            os.chdir(original_cwd)
                            return ParsedFile(
                                file_path=file_path,
                                translation_unit=None,
                                success=False,
                                diagnostics=[],
                                parse_time=time.perf_counter() - start_time
                            )
                    else:
                        # 对于其他文件，重新抛出异常
                        raise
            
            with profiler.timer("clang_parse_cleanup"):
                os.chdir(original_cwd)
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

            return ParsedFile(file_path=file_path, success=True, translation_unit=tu, diagnostics=[], parse_time=parse_time)

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
            # 确保工作目录被恢复
            try:
                os.chdir(original_cwd)
            except:
                pass
            return ParsedFile(
                file_path=file_path,
                translation_unit=None,
                success=False,
                diagnostics=[],
                parse_time=0.0
            )
    
    def clear_cache(self):
        """清空TranslationUnit缓存"""
        if self._tu_cache:
            self._tu_cache.clear()
            self.logger.info("TranslationUnit缓存已清空")