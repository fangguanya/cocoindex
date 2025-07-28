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
                args = self._process_compile_args(shlex.split(command, posix=False) if platform.system() == 'Windows' else shlex.split(command))
                file_commands[self._normalize_path(file_path)] = {"args": args, "directory": directory}
        return file_commands

    def _process_compile_args(self, raw_args: List[str]) -> List[str]:
        """处理和清理编译参数 - 性能优化版"""
        processed_args = []
        skip_next = False
        
        # 预编译常用参数映射
        msvc_to_clang = {
            '/EHsc': '-fexceptions',
            '/GR-': '-fno-rtti',
            '/W1': '-Wall',
            '/W2': '-Wall',
            '/W3': '-Wall',
            '/W4': '-Wall'
        }
        
        for i, arg in enumerate(raw_args):
            if skip_next:
                skip_next = False
                continue
            
            arg_clean = arg.strip('"\'')
            if arg_clean.endswith(('.exe', '.c', '.cpp', '.cc', '.cxx', '.o', '.obj')):
                continue

            if arg in ['-o', '-c', '/c'] and i + 1 < len(raw_args):
                skip_next = True
                continue

            if arg.startswith(('-I', '/I')):
                if len(arg) > 2:
                    processed_args.append('-I' + arg[2:])
                elif i + 1 < len(raw_args):
                    processed_args.append('-I' + raw_args[i+1])
                    skip_next = True
            elif arg.startswith(('-D', '/D')):
                if len(arg) > 2:
                    processed_args.append('-D' + arg[2:])
                elif i + 1 < len(raw_args):
                    processed_args.append('-D' + raw_args[i+1])
                    skip_next = True
            elif arg.startswith('/'):
                converted = msvc_to_clang.get(arg)
                if converted:
                    processed_args.append(converted)
            else:
                processed_args.append(arg)
        return processed_args

    def _normalize_path(self, path: str) -> str:
        """规范化路径"""
        return str(Path(path).resolve()).replace('\\', '/')

    def parse_file(self, file_path: str) -> Optional[ParsedFile]:
        """解析单个文件 - 性能优化版"""
        self.logger.debug(f"开始解析文件: {file_path}")

        compile_info = self.compile_commands.get(self._normalize_path(file_path))
        if not compile_info:
            self.logger.warning(f"在 compile_commands.json 中未找到文件 '{file_path}' 的编译命令，跳过。")
            return None

        args = compile_info["args"]
        directory = compile_info["directory"]

        if not Path(file_path).exists():
            self.logger.error(f"文件路径不存在: {file_path}")
            return None
        if not Path(directory).exists():
            self.logger.error(f"工作目录不存在: {directory}")
            return None

        # 检查缓存
        cache_key = f"{file_path}:{hash(tuple(args))}"
        if self._tu_cache:
            cached_tu = self._tu_cache.get(cache_key)
            if cached_tu:
                self.logger.debug(f"使用缓存的TranslationUnit: {file_path}")
                return ParsedFile(
                    file_path=file_path,
                    translation_unit=cached_tu,
                    success=True,
                    diagnostics=[],
                    parse_time=0.0  # 缓存命中，解析时间为0
                )

        try:
            start_time = time.time()
            original_cwd = os.getcwd()
            os.chdir(directory)
            
            tu = self.index.parse(file_path, args=args, options=clang.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD)
            
            os.chdir(original_cwd)
            parse_time = time.time() - start_time

            if not tu:
                self.logger.error(f"Clang 未能为文件 '{file_path}' 创建翻译单元。")
                return None
            
            # 缓存TranslationUnit
            if self._tu_cache:
                self._tu_cache.put(cache_key, tu)
            
            errors = [d for d in tu.diagnostics if d.severity >= Diagnostic.Error]
            if errors:
                self.logger.warning(f"文件 '{file_path}' 解析时出现 {len(errors)} 个错误。")

            return ParsedFile(file_path=file_path, success=True, translation_unit=tu, diagnostics=[], parse_time=parse_time)

        except Exception as e:
            self.logger.error(f"解析文件 '{file_path}' 时发生未知异常: {e}\n{traceback.format_exc()}")
            return None
    
    def clear_cache(self):
        """清空TranslationUnit缓存"""
        if self._tu_cache:
            self._tu_cache.clear()
            self.logger.info("TranslationUnit缓存已清空")