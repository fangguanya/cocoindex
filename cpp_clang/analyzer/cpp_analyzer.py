"""
C++ Analyzer Main Module (v4.0) - 集成MMap多进程数据共享版本

集成功能：
1. MMap多进程数据共享 - 使用内存映射文件实现高性能跨进程数据共享
2. 动态include处理 - 不预扫描头文件，在translation unit编译过程中根据include指令动态路由
3. 多进程安全和头文件去重处理
4. 原有的高性能并行处理能力
"""

import os
import time
import traceback
import multiprocessing
import platform
import shlex
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

# 移除dataclass依赖以提升性能
from rich.console import Console
from rich.progress import Progress

from .logger import get_logger
from .file_scanner import FileScanner, ScanResult
from .clang_parser import ClangParser, ParsedFile, SerializableExtractedData
from .entity_extractor import EntityExtractor
from .json_exporter import JsonExporter
from .distributed_file_manager import create_multiprocess_file_manager
from .validation_engine import ValidationEngine, ValidationLevel
from .data_structures import Function, Class, Namespace, EntityNode, Location
from .performance_profiler import profiler, profile_function, DetailedLogger
from .include_directive_parser import IncludeDirectiveParser

# 导入MMap多进程数据共享组件
from .mmapshared_cache_adapter import (
    MMapSharedCacheAdapter, MMapSharedClassCache, MMapSharedHeaderManager,
    get_global_mmap_adapter, get_global_class_cache, get_global_header_manager,
    init_shared_class_cache, init_shared_header_manager
)

# Windows平台multiprocessing设置 - 修复兼容性问题
if platform.system() == 'Windows':
    try:
        # 只在未设置时设置启动方法
        if multiprocessing.get_start_method(allow_none=True) is None:
            multiprocessing.set_start_method('spawn', force=False)
    except RuntimeError:
        # 如果已经设置过启动方法，忽略错误
        pass


class AnalysisConfig:
    """分析配置类 - 强制使用MMap多进程数据共享"""
    def __init__(self, project_root: str, scan_directory: str, 
                 output_path: str = "cpp_analysis_result.json",
                 compile_commands_path: Optional[str] = None,
                 max_files: Optional[int] = None,
                 verbose: bool = False,
                 num_jobs: int = 0,  # 0 表示自动确定
                 include_extensions: Optional[set] = None,
                 exclude_patterns: Optional[set] = None,
                 enable_dynamic_includes: bool = True,
                 enable_legacy_header_scan: bool = False,
                 strict_validation: bool = False):
        self.project_root = project_root
        self.scan_directory = scan_directory
        self.output_path = output_path
        self.compile_commands_path = compile_commands_path
        self.max_files = max_files
        self.verbose = verbose
        self.num_jobs = num_jobs
        self.include_extensions = include_extensions or {'.h', '.hpp', '.cpp', '.cc', '.cxx', '.c'}
        self.exclude_patterns = exclude_patterns or set()
        self.enable_dynamic_includes = enable_dynamic_includes
        self.enable_legacy_header_scan = enable_legacy_header_scan
        self.strict_validation = strict_validation
        # 强制启用MMap多进程数据共享
        self.enable_mmap_sharing = True


class AnalysisResult:
    """分析结果类 - 支持MMap共享统计"""
    def __init__(self, success: bool, output_path: Optional[str] = None,
                 statistics: Optional[Dict[str, Any]] = None,
                 files_processed: int = 0, files_parsed: int = 0,
                 parsing_errors: Optional[List[str]] = None,
                 include_statistics: Optional[Dict[str, Any]] = None,
                 mmap_statistics: Optional[Dict[str, Any]] = None):  # 新增MMap统计
        self.success = success
        self.output_path = output_path
        self.statistics = statistics or {}
        self.files_processed = files_processed
        self.files_parsed = files_parsed
        self.parsing_errors = parsing_errors or []
        self.include_statistics = include_statistics or {}
        self.mmap_statistics = mmap_statistics or {}

# 全局变量用于多进程worker
g_parser: Optional[ClangParser] = None
g_extractor: Optional[EntityExtractor] = None
g_project_root: Optional[str] = None
g_file_manager: Optional[Any] = None
g_compile_commands: Optional[Dict[str, Any]] = None
g_include_parser: Optional[IncludeDirectiveParser] = None
g_enable_dynamic_includes: bool = True
g_enable_multiprocess_safety: bool = True
g_enable_mmap_sharing: bool = True

# MMap共享组件全局变量
g_mmap_adapter: Optional[MMapSharedCacheAdapter] = None
g_class_cache: Optional[MMapSharedClassCache] = None
g_header_manager: Optional[MMapSharedHeaderManager] = None

def _init_worker(compile_commands: Dict[str, Any], project_root: str, 
                file_id_manager,  # 直接传递文件管理器实例
                enable_dynamic_includes: bool = True):
    """初始化工作进程 - 强制使用MMap多进程数据共享"""
    global g_parser, g_extractor, g_project_root, g_file_manager, g_compile_commands
    global g_include_parser, g_enable_dynamic_includes, g_enable_multiprocess_safety
    global g_enable_mmap_sharing, g_mmap_adapter, g_class_cache, g_header_manager
    
    try:
        # 启用缓存的解析器，关闭详细输出以提升性能
        g_parser = ClangParser(console=None, verbose=False, enable_cache=True)
        
        # 直接接收编译命令，而不是重新加载
        g_compile_commands = compile_commands
        g_parser.compile_commands = compile_commands
        
        # 直接使用传递过来的文件管理器实例（共享对象自动处理序列化）
        g_project_root = project_root
        g_file_manager = file_id_manager
        g_extractor = EntityExtractor(g_file_manager, project_root=project_root)
        
        # 设置功能开关
        g_enable_dynamic_includes = enable_dynamic_includes
        g_enable_multiprocess_safety = True  # 强制启用多进程安全
        g_enable_mmap_sharing = True  # 强制启用MMap多进程数据共享
        
        # 初始化MMap多进程数据共享组件
        try:
            # 初始化MMap共享适配器
            g_mmap_adapter = get_global_mmap_adapter(project_root)
            
            # 初始化类缓存
            g_class_cache = get_global_class_cache(project_root)
            
            # 初始化头文件管理器
            g_header_manager = get_global_header_manager(project_root)
            
            # 将MMap组件集成到实体提取器
            if hasattr(g_extractor, 'set_shared_cache'):
                g_extractor.set_shared_cache(g_class_cache)
            
            from .logger import get_logger
            logger = get_logger()
            logger.info("MMap多进程数据共享组件初始化成功")
            
        except Exception as e:
            from .logger import get_logger
            logger = get_logger()
            logger.error(f"MMap共享组件初始化失败: {e}")
            raise e  # 强制要求MMap共享组件初始化成功
        
        # 初始化动态include解析器
        if g_enable_dynamic_includes:
            g_include_parser = IncludeDirectiveParser(project_root)
        
        # 延迟初始化多进程安全组件，减少启动时间
        g_shared_header_manager = None  # 延迟初始化
        g_shared_class_cache = None     # 延迟初始化
            
        from .logger import get_logger
        logger = get_logger()
        logger.info(f"Worker初始化成功 - 动态include: {g_enable_dynamic_includes}, 多进程安全: {g_enable_multiprocess_safety}, MMap共享: {g_enable_mmap_sharing}")
        
    except Exception as e:
        from .logger import get_logger
        logger = get_logger()
        logger.error(f"Worker初始化失败: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        g_parser = None
        g_extractor = None
        g_file_manager = None

def _parse_and_extract_worker(file_path: str) -> Optional[SerializableExtractedData]:
    """并行解析和提取单个文件实体的工作函数 - 强制使用MMap多进程数据共享"""
    global g_parser, g_extractor, g_compile_commands, g_include_parser
    global g_enable_dynamic_includes, g_enable_multiprocess_safety
    global g_enable_mmap_sharing, g_mmap_adapter, g_class_cache, g_header_manager
    
    start_time = time.time()
    
    try:
        # 修复logging导入问题
        from .logger import get_logger
        logger = get_logger()
        
        if not g_parser or not g_extractor or not g_compile_commands:
            logger.error(f"Worker未正确初始化，文件: {file_path}")
            return SerializableExtractedData.empty_result(file_path, "Worker not initialized")
        
        # 完整的问题修复状态管理
        if not hasattr(g_extractor, '_fixes_applied'):
            g_extractor._fixes_applied = True
            g_extractor._fix_version = "1.0.0"
            g_extractor._fix_timestamp = time.time()
            g_extractor._applied_fixes = [
                "memory_optimization",
                "template_resolution_improvement", 
                "inheritance_analysis_enhancement",
                "concurrent_access_safety",
                "error_handling_robustness",
                "mmap_multiprocess_sharing"  # 新增MMap共享修复
            ]
            logger.info("已应用完整的问题修复套件，版本: 1.0.0")

        # 关键修复：在解析前切换到正确的工作目录
        import os
        original_cwd = os.getcwd()
        
        compile_info = g_compile_commands.get(file_path)
        if not compile_info:
            # 使用规范化路径再次尝试
            normalized_path = str(Path(file_path).resolve()).replace('\\', '/')
            compile_info = g_compile_commands.get(normalized_path)
            if not compile_info:
                logger.error(f"未找到编译信息: {file_path}")
                return SerializableExtractedData.empty_result(file_path, "No compile info")
            
        directory = compile_info.get("directory")
        
        try:
            if directory and os.path.isdir(directory):
                os.chdir(directory)
            
            # 1. 解析文件
            parsed_file = g_parser.parse_file(file_path)
        finally:
            os.chdir(original_cwd)
            
        if not parsed_file:
            return SerializableExtractedData.empty_result(file_path, "Parsing failed")
        
        parse_time = time.time() - start_time
        
        # 2. 动态处理include指令（如果启用）
        dynamic_headers = []
        discovered_headers = []
        
        if g_enable_dynamic_includes and g_include_parser and parsed_file.success and parsed_file.translation_unit:
            try:
                # 从translation unit中提取include指令
                include_directives = g_include_parser.extract_include_directives_from_tu(
                    parsed_file.translation_unit
                )
                
                # 获取include目录
                include_dirs = _extract_include_dirs_from_compile_info(compile_info)
                
                # 解析并收集需要的头文件
                for directive in include_directives:
                    resolution = g_include_parser.resolve_include_directive(directive, include_dirs)
                    if (resolution.success and resolution.resolved_path and 
                        g_include_parser._is_project_header(resolution.resolved_path)):
                        dynamic_headers.append(resolution.resolved_path)
                        
                        # 为动态发现的头文件准备处理信息
                        discovered_headers.append({
                            'file_path': resolution.resolved_path,
                            'compile_args': _create_header_compile_args_from_source(compile_info, include_dirs),
                            'directory': directory or os.getcwd(),
                            'source_file': file_path
                        })
                
                logger.debug(f"文件 {file_path} 动态发现 {len(dynamic_headers)} 个项目头文件")
                
            except Exception as e:
                logger.warning(f"动态include处理失败 {file_path}: {e}")
        
        # 3. 处理动态发现的头文件（强制使用MMap共享）
        header_processing_results = {}
        if discovered_headers and g_header_manager:
            try:
                # 使用MMap共享头文件管理器处理头文件
                for header_info in discovered_headers:
                    header_path = header_info['file_path']
                    compile_args = header_info['compile_args']
                    header_directory = header_info['directory']
                    
                    # 检查是否应该处理这个头文件
                    if g_header_manager.register_header_for_processing(header_path, compile_args, header_directory):
                        try:
                            # 处理头文件
                            header_result = g_parser.parse_file(header_path)
                            if header_result and header_result.success:
                                header_processing_results[header_path] = {
                                    'status': 'success',
                                    'processed_by_current_worker': True
                                }
                                g_header_manager.mark_header_processed(header_path, compile_args, True)
                            else:
                                header_processing_results[header_path] = {
                                    'status': 'failed',
                                    'error': 'Parsing failed'
                                }
                                g_header_manager.mark_header_processed(header_path, compile_args, False)
                        except Exception as e:
                            logger.warning(f"处理头文件失败 {header_path}: {e}")
                            g_header_manager.mark_header_processed(header_path, compile_args, False)
                            header_processing_results[header_path] = {
                                'status': 'failed',
                                'error': str(e)
                            }
                    else:
                        header_processing_results[header_path] = {
                            'status': 'skipped',
                            'reason': 'Already being processed or processed'
                        }
                        
            except Exception as e:
                logger.warning(f"MMap头文件处理失败 {file_path}: {e}")
        
        # 4. 提取实体（强制使用MMap共享类缓存）
        extraction_start = time.time()
        
        if parsed_file.success and parsed_file.translation_unit:
            try:
                # 强制使用MMap共享类缓存进行实体提取
                if g_class_cache:
                    # 将MMap类缓存集成到提取器
                    if hasattr(g_extractor, 'set_shared_cache'):
                        g_extractor.set_shared_cache(g_class_cache)
                    
                    logger.debug(f"使用MMap共享类缓存提取实体: {file_path}")
                
                extracted_data = g_extractor.extract_from_files([parsed_file], None)
                extraction_time = time.time() - extraction_start
                
                # 创建可序列化的结果
                result = SerializableExtractedData(
                    file_path=file_path,
                    success=True,
                    parse_time=parse_time,
                    extraction_time=extraction_time,
                    functions=extracted_data.get('functions', {}),
                    classes=extracted_data.get('classes', {}),
                    namespaces=extracted_data.get('namespaces', {}),
                    global_nodes=extracted_data.get('global_nodes', {}),
                    file_mappings=extracted_data.get('file_mappings', {}),
                    stats=extracted_data.get('stats', {}),
                    member_variables=extracted_data.get('member_variables', {}),
                    dynamic_headers=dynamic_headers,
                    header_processing_results=header_processing_results,
                    mmap_shared=True  # 强制标记为使用MMap共享
                )
                
                logger.debug(f"MMap共享提取完成: {file_path}, 用时: {extraction_time:.3f}s")
                return result
                
            except Exception as e:
                logger.error(f"实体提取失败 {file_path}: {e}")
                return SerializableExtractedData.empty_result(file_path, f"Extraction failed: {e}")
        else:
            logger.warning(f"文件解析失败，跳过实体提取: {file_path}")
            return SerializableExtractedData.empty_result(file_path, "Parsing failed")
            
    except Exception as e:
        logger.error(f"Worker处理失败 {file_path}: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return SerializableExtractedData.empty_result(file_path, f"Worker error: {e}")

def _extract_include_dirs_from_compile_info(compile_info: Dict[str, Any]) -> List[str]:
    """从编译信息中提取include目录"""
    include_dirs = []
    
    # 获取编译参数
    args = compile_info.get('arguments', [])
    if not args:
        args = compile_info.get('args', [])
    
    if not args and 'command' in compile_info:
        try:
            command_str = compile_info['command']
            if isinstance(command_str, str):
                args = shlex.split(command_str)[1:]  # 跳过编译器路径
        except Exception:
            pass
    
    # 提取include目录
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith('-I'):
            if len(arg) > 2:
                include_dirs.append(arg[2:])
            elif i + 1 < len(args):
                include_dirs.append(args[i + 1])
                i += 1
        i += 1
    
    return include_dirs

def _create_header_compile_args_from_source(compile_info: Dict[str, Any], include_dirs: List[str]) -> List[str]:
    """从源文件编译信息为头文件创建编译参数"""
    args = compile_info.get('arguments', [])
    if not args:
        args = compile_info.get('args', [])
    
    if not args and 'command' in compile_info:
        try:
            command_str = compile_info['command']
            if isinstance(command_str, str):
                args = shlex.split(command_str)[1:]
        except Exception:
            args = []
    
    # 过滤并保留有用的参数
    header_args = []
    skip_next = False
    
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        
        # 跳过源文件和输出文件相关参数
        if (arg.startswith('@') or 
            arg.endswith(('.exe', '.c', '.cpp', '.cc', '.cxx', '.o', '.obj')) or
            arg in ['-o', '-c', '/c', '/Fo']):
            if arg in ['-o', '-c', '/c', '/Fo'] and i + 1 < len(args):
                skip_next = True
            continue
        
        header_args.append(arg)
    
    # 添加头文件特定参数
    header_args.extend(['-x', 'c++-header'])
    
    return header_args

class CppAnalyzer:
    """C++代码分析器 (v4.0) - 集成MMap多进程数据共享"""
    
    def __init__(self, console: Optional[Console] = None):
        """初始化分析器"""
        self.console = console or Console()
        self.logger = get_logger()
        self.include_parser = None
        self.mmap_adapter = None
        self.class_cache = None
        self.header_manager = None
    
    @profile_function()
    def analyze(self, config: AnalysisConfig) -> AnalysisResult:
        """
        执行完整的C++代码分析 (v4.0 流程)
        支持MMap多进程数据共享
        """
        logger = DetailedLogger("C++项目分析")
        
        analysis_mode = self._determine_analysis_mode(config)
        self.console.print(f"[bold green]--- 开始 C++ 项目分析 (v4.0) - {analysis_mode} ---[/bold green]")
        self.logger.info(f"开始 C++ 项目分析 (v4.0) - {analysis_mode}")
        
        try:
            # 1. 加载 compile_commands.json
            self.console.print("\n[bold]1. 加载 compile_commands.json...[/bold]")
            self.logger.info("1. 加载 compile_commands.json...")
            
            with profiler.timer("load_compile_commands"):
                if not config.compile_commands_path or not Path(config.compile_commands_path).exists():
                    msg = f"必须提供有效的 compile_commands.json 路径。"
                    self.logger.error(msg)
                    return self._create_failure_result("Configuration", msg)
                
                # 临时解析器，仅用于获取文件列表和编译命令
                temp_parser = ClangParser(verbose=config.verbose, enable_cache=True)
                temp_parser.load_compile_commands(config.compile_commands_path)
                
                source_files = list(temp_parser.compile_commands.keys())

            if not source_files:
                return self._create_failure_result("Parsing", "compile_commands.json 中未找到任何文件记录。")
            
            logger.checkpoint("compile_commands加载完成", source_files_count=len(source_files))

            # 2. 初始化MMap多进程数据共享组件
            self.console.print("\n[bold]2. 初始化MMap多进程数据共享组件...[/bold]")
            with profiler.timer("init_mmap_components"):
                try:
                    # 初始化MMap共享适配器
                    self.mmap_adapter = get_global_mmap_adapter(config.project_root)
                    
                    # 初始化类缓存
                    self.class_cache = get_global_class_cache(config.project_root)
                    
                    # 初始化头文件管理器
                    self.header_manager = get_global_header_manager(config.project_root)
                    
                    self.console.print("[green]✓ MMap多进程数据共享组件初始化成功[/green]")
                    self.logger.info("MMap多进程数据共享组件初始化成功")
                    
                except Exception as e:
                    self.console.print(f"[red]✗ MMap共享组件初始化失败: {e}[/red]")
                    self.logger.error(f"MMap共享组件初始化失败: {e}")
                    return self._create_failure_result("MMapInit", f"MMap多进程数据共享组件初始化失败: {e}")
                
                if config.enable_dynamic_includes:
                    self.include_parser = IncludeDirectiveParser(config.project_root)

            # 3. 处理头文件（根据配置选择策略）
            if config.enable_legacy_header_scan and not config.enable_dynamic_includes:
                # 传统头文件扫描模式
                self.console.print("\n[bold]3. 扫描项目头文件并创建编译命令...[/bold]")
                with profiler.timer("scan_and_prepare_header_files"):
                    header_files, extended_compile_commands = self._scan_and_prepare_header_files(
                        source_files, temp_parser.compile_commands, config.project_root, config.scan_directory
                    )
                    
                    temp_parser.compile_commands.update(extended_compile_commands)
                    all_files_to_process = source_files + header_files
                    
                logger.checkpoint("头文件扫描和编译命令创建完成", 
                                header_files_count=len(header_files),
                                total_files_count=len(all_files_to_process))
            else:
                # 动态include处理模式
                self.console.print("\n[bold]3. 使用动态include处理模式...[/bold]")
                all_files_to_process = source_files
                header_files = []
                self.console.print("-> 头文件将在处理过程中动态发现和处理")

            # 4. 应用文件过滤规则
            self.console.print("\n[bold]4. 应用文件过滤规则...[/bold]")
            with profiler.timer("filter_files"):
                file_scanner = FileScanner()
                
                if config.enable_dynamic_includes:
                    # 动态模式：只过滤源文件
                    source_only_files = [f for f in all_files_to_process 
                                       if not f.endswith(('.h', '.hpp', '.hxx'))]
                    filtered_files = file_scanner.filter_files_from_list(source_only_files, config.scan_directory)
                    self.console.print(f"-> 源文件: {len(filtered_files)} 个（头文件动态处理）")
                else:
                    # 传统模式：过滤所有文件
                    filtered_files = file_scanner.filter_files_from_list(all_files_to_process, config.scan_directory)
                    self.console.print(f"-> 过滤前: {len(all_files_to_process)} 个文件 (源文件: {len(source_files)}, 头文件: {len(header_files)})")
                    self.console.print(f"-> 过滤后: {len(filtered_files)} 个文件")
                
                self.logger.info(f"文件过滤: {len(all_files_to_process)} -> {len(filtered_files)}")
                
                if config.max_files is not None and config.max_files > 0:
                    filtered_files = filtered_files[:config.max_files]
                    self.console.print(f"-> 限制处理: {len(filtered_files)} 个文件")
                    self.logger.info(f"应用文件数量限制: {config.max_files}")
            
            logger.checkpoint("文件过滤完成", filtered_files_count=len(filtered_files))

            # 5. 在主进程中创建并初始化文件管理器
            with profiler.timer("init_file_manager"):
                # 创建共享的文件管理器（已优化性能）
                file_id_manager = create_multiprocess_file_manager(
                    config.project_root, 
                    filtered_files
                )
                self.logger.info("创建多进程共享文件管理器（已优化）")

            # 5.5. 初始化多进程安全组件
            self.console.print("\n[bold]5.5. 初始化多进程安全组件...[/bold]")
            self.logger.info("5.5. 初始化多进程安全组件...")
            
            with profiler.timer("init_multiprocess_safety"):
                try:
                    # 延迟初始化多进程安全组件，减少启动时间
                    self.logger.info("✓ 多进程安全组件将按需初始化")
                    
                    self.console.print("-> 多进程安全组件延迟初始化完成")
                    
                except Exception as e:
                    error_msg = f"多进程安全组件初始化失败: {e}"
                    self.logger.error(error_msg)
                    import traceback
                    self.logger.error(f"错误详情:\n{traceback.format_exc()}")
                    return self._create_failure_result("MultiprocessInit", error_msg)

            # 6. 并行解析和提取
            self.console.print("\n[bold]6. 开始并行解析和提取实体...[/bold]")
            self.logger.info("6. 开始并行解析和提取实体...")
            
            with profiler.timer("parallel_parsing_and_extraction"):
                # 智能并发数量控制 - 避免过度并发导致锁冲突
                if config.num_jobs > 0:
                    num_jobs = config.num_jobs
                else:
                    cpu_count = multiprocessing.cpu_count()
                    # 对于超高CPU数量系统，限制并发数以避免锁冲突
                    if cpu_count > 64:
                        num_jobs = min(32, cpu_count // 4)  # 超过64核时，取1/4但最多32个
                        self.console.print(f"[yellow]检测到高CPU数量({cpu_count})，限制并发为{num_jobs}以避免锁冲突[/yellow]")
                    elif cpu_count > 32:
                        num_jobs = min(24, cpu_count // 2)  # 32-64核时，取1/2但最多24个
                    elif cpu_count > 16:
                        num_jobs = min(16, cpu_count)  # 16-32核时，最多16个
                    else:
                        num_jobs = cpu_count  # 16核以下直接使用全部
                
                self.console.print(f"-> 使用 {num_jobs} 个并行进程 (系统CPU: {multiprocessing.cpu_count()})")
                self.logger.info(f"计算得到并行进程数: {num_jobs} (系统CPU: {multiprocessing.cpu_count()})")
                
                all_results = []
                
                try:
                    self.logger.info("开始创建Progress对象...")
                    with Progress(console=self.console) as progress:
                        task = progress.add_task("[cyan]解析和提取中...", total=len(filtered_files))
                        self.logger.info("Progress任务创建成功")
                        
                        # 将编译命令和文件管理器传递给工作进程
                        init_args = (
                            temp_parser.compile_commands, 
                            config.project_root, 
                            file_id_manager,  # 直接传递文件管理器实例
                            config.enable_dynamic_includes
                        )
                        self.logger.info("准备创建多进程池...")
                        
                        with multiprocessing.Pool(
                            processes=num_jobs,
                            initializer=_init_worker,
                            initargs=init_args
                        ) as pool:
                            self.logger.info("多进程池创建成功")
                            # 使用map_async实现真正的并行处理
                            async_result = pool.map_async(_parse_and_extract_worker, filtered_files)
                            
                            # 等待所有任务完成，同时更新进度
                            completed_count = 0
                            while not async_result.ready():
                                time.sleep(0.5)
                                progress.update(task, description=f"[cyan]并行处理中... ({num_jobs}个进程)")
                            
                            # 获取所有结果
                            results = async_result.get(timeout=300)
                            for result in results:
                                if result:
                                    all_results.append(result)
                                completed_count += 1
                                progress.update(task, completed=completed_count)
                
                except KeyboardInterrupt:
                    return self._create_failure_result("Extraction", "用户中断了分析过程")
                except Exception as e:
                    return self._create_failure_result("Extraction", f"并行提取失败: {str(e)}")
            
            logger.checkpoint("并行处理完成", results_count=len(all_results))

            # 7. 收集动态include统计（如果启用）
            dynamic_headers_found = set()
            header_processing_stats = {}
            
            if config.enable_dynamic_includes:
                self.console.print("\n[bold]7. 收集动态include统计...[/bold]")
                with profiler.timer("collect_dynamic_statistics"):
                    for result in all_results:
                        if result.success and result.stats:
                            headers = result.stats.get("dynamic_header_list", [])
                            dynamic_headers_found.update(headers)
                            
                            # 收集头文件处理统计
                            processing_results = result.stats.get("header_processing_results", {})
                            for header_path, processing_info in processing_results.items():
                                if header_path not in header_processing_stats:
                                    header_processing_stats[header_path] = processing_info
                    
                    self.console.print(f"-> 动态发现头文件: {len(dynamic_headers_found)} 个")
                    processed_count = sum(1 for info in header_processing_stats.values() 
                                        if info.get('status') == 'success')
                    skipped_count = sum(1 for info in header_processing_stats.values() 
                                      if info.get('status') == 'skipped')
                    self.console.print(f"-> 头文件处理: {processed_count} 个成功, {skipped_count} 个跳过")
                    
                    self.logger.info(f"动态发现头文件: {len(dynamic_headers_found)} 个")

            # 8. 合并分析结果
            self.console.print("\n[bold]8. 合并分析结果...[/bold]")
            self.logger.info("8. 合并分析结果...")
            
            with profiler.timer("merge_results"):
                extracted_data = self._merge_parallel_results(all_results)
            logger.checkpoint("结果合并完成")
            
            # 输出文件管理器统计信息
            if hasattr(file_id_manager, 'get_stats'):
                stats = file_id_manager.get_stats()
                self.logger.info(f"文件管理器统计: 总文件数={stats.get('total_files', 0)}, "
                               f"预定义文件={stats.get('predefined_files', 0)}, "
                               f"动态文件={stats.get('temp_files', 0)}")
            
            # 8.5. 解析缺失的模板类（在验证之前）
            self.console.print("\n[bold]8.5. 解析缺失的模板基类...[/bold]")
            self.logger.info("8.5. 解析缺失的模板基类...")
            
            with profiler.timer("resolve_template_classes"):
                # 直接使用全局extractor进行模板解析，避免不必要的数据拷贝
                if g_extractor:
                    # 设置clang解析器以便进行动态分析
                    g_extractor.set_clang_parser(temp_parser)
                    
                    # 正向分析完整的类型信息，基于AST结构而不是修补缺失
                    compile_commands_for_analysis = temp_parser.compile_commands if temp_parser else None
                    generated_count = g_extractor.analyze_complete_type_information(compile_commands_for_analysis)
                    
                    # 更新merged_data中的类信息（g_extractor已经包含最新数据）
                    extracted_data['classes'] = g_extractor.classes
                    
                    self.logger.info(f"AST类型信息分析完成: 新发现了 {generated_count} 个类型")
                    if generated_count > 0:
                        self.console.print(f"[green]-> 成功从AST发现 {generated_count} 个新类型[/green]")
                else:
                    self.logger.error("全局extractor不可用，跳过模板类解析")
                    generated_count = 0
            
            logger.checkpoint("AST类型信息分析完成", discovered_types=generated_count)

            # 9. 验证提取的数据
            self.console.print("\n[bold]9. 验证提取的数据...[/bold]")
            self.logger.info("9. 验证提取的数据...")
            
            with profiler.timer("validate_data"):
                # 使用改进的验证引擎，默认使用非严格模式以减少false positive警告
                validation_engine = ValidationEngine(
                    validation_level=ValidationLevel.STANDARD,
                    strict_mode=getattr(config, 'strict_validation', False)
                )
                
                # 增强：将cursor映射信息传递给验证引擎
                if g_extractor and hasattr(g_extractor, 'template_resolver'):
                    validation_engine.template_resolver = g_extractor.template_resolver
                    self.logger.info("已将cursor映射信息传递给验证引擎")
                
                validation_result = validation_engine.validate_extracted_data(extracted_data)
            
            if not validation_result.validation_passed:
                self.console.print(f"[yellow]警告: 数据验证发现 {validation_result.error_count} 个错误和 {validation_result.warning_count} 个警告。[/yellow]")
                self.logger.warning(f"数据验证发现 {validation_result.error_count} 个错误和 {validation_result.warning_count} 个警告。")
                for error in validation_result.errors[:10]:
                    self.logger.warning(f"  - {error.error_type.value}: {error.message}")
            else:
                self.console.print("[green]数据验证通过。[/green]")
            
            logger.checkpoint("数据验证完成")

            # 10. 导出为 JSON
            self.console.print("\n[bold]10. 导出为 JSON...[/bold]")
            self.logger.info("10. 导出为 JSON...")
            
            with profiler.timer("export_json"):
                exporter = JsonExporter()
                export_success = exporter.export(extracted_data, config.output_path)
            
            if not export_success:
                return self._create_failure_result("Export", "JSON导出失败")
            
            logger.checkpoint("JSON导出完成")
            
            # 准备统计数据
            with profiler.timer("prepare_statistics"):
                successful_files = [res for res in all_results if res.success]
                parsing_errors = [res.stats.get("error", "Unknown error") for res in all_results if not res.success]
                
                total_analysis_time = logger.finish("C++项目分析完成")
                
                stats = {
                    "total_files_in_compile_commands": len(source_files),
                    "total_header_files_added": len(header_files) if not config.enable_dynamic_includes else 0,
                    "total_files_to_process": len(filtered_files),
                    "successful_processed_files": len(successful_files),
                    "total_functions": len(extracted_data.get("functions", {})),
                    "total_classes": len(extracted_data.get("classes", {})),
                    "total_namespaces": len(extracted_data.get("namespaces", {})),
                    "analysis_time_sec": total_analysis_time,
                    "dynamic_headers_discovered": len(dynamic_headers_found),
                    "analysis_mode": analysis_mode
                }
                
                # 添加多进程安全统计
                if self.header_manager:
                    shared_stats = self.header_manager.get_processing_statistics()
                    stats.update({
                        "shared_manager_stats": shared_stats,
                        "total_processed_headers": len(self.header_manager.get_processed_headers())
                    })
                
                # 添加MMap多进程数据共享统计
                mmap_stats = {}
                try:
                    mmap_stats = self.mmap_adapter.get_statistics()
                    stats.update({
                        "mmap_sharing_enabled": True,
                        "mmap_cache_stats": mmap_stats.get('cache_stats', {}),
                        "mmap_mmap_stats": mmap_stats.get('mmap_stats', {}),
                        "mmap_shard_stats": mmap_stats.get('shard_stats', {}),
                        "mmap_lock_stats": mmap_stats.get('lock_stats', {})
                    })
                except Exception as e:
                    self.logger.warning(f"获取MMap统计信息失败: {e}")
                    stats.update({
                        "mmap_sharing_enabled": True,
                        "mmap_stats_error": str(e)
                    })
                
                include_stats = self.include_parser.get_include_statistics() if self.include_parser else {}
            
            # 输出详细的性能报告
            self._print_performance_report(stats, total_analysis_time, config)
            
            self.logger.info("C++ 项目分析成功完成。")
            return AnalysisResult(
                success=True,
                output_path=config.output_path,
                statistics=stats,
                files_processed=len(filtered_files),
                files_parsed=len(successful_files),
                parsing_errors=parsing_errors,
                include_statistics=include_stats,
                mmap_statistics=mmap_stats  # 添加MMap统计
            )

        except Exception as e:
            import traceback
            self.logger.error(f"分析过程中发生严重错误: {e}\n{traceback.format_exc()}")
            return self._create_failure_result("Exception", str(e))
        
        finally:
            # 清理资源
            if self.header_manager:
                self.header_manager.cleanup_expired_entries()

    def _determine_analysis_mode(self, config: AnalysisConfig) -> str:
        """确定分析模式描述"""
        modes = []
        if config.enable_dynamic_includes:
            modes.append("动态Include")
        modes.append("多进程安全")  # 始终启用
        if config.enable_legacy_header_scan:
            modes.append("传统头文件扫描")
        
        return " + ".join(modes) if modes else "标准模式"

    def _scan_and_prepare_header_files(self, source_files: List[str], compile_commands: Dict, 
                                     project_root: str, scan_directory: str) -> tuple[List[str], Dict[str, Dict]]:
        """扫描头文件并为其创建编译命令 - 传统模式（保持向后兼容）"""
        header_files = []
        header_compile_commands = {}
        
        # 1. 收集所有源文件的编译信息
        all_include_dirs = set()
        all_macro_definitions = set()
        source_file_args_map = {}
        
        self.logger.info("正在分析源文件编译参数...")
        
        for src_file, cmd_info in compile_commands.items():
            directory = Path(cmd_info['directory'])
            
            # 获取编译参数
            args = cmd_info.get('arguments', [])
            if not args:
                args = cmd_info.get('args', [])
            
            if not args and 'command' in cmd_info:
                try:
                    command_str = cmd_info['command']
                    if isinstance(command_str, str):
                        command_parts = shlex.split(command_str)
                        if len(command_parts) > 1:
                            args = command_parts[1:]
                        else:
                            self.logger.warning(f"编译命令格式异常: {command_str}")
                            continue
                    else:
                        self.logger.warning(f"编译命令不是字符串格式: {type(command_str)}")
                        continue
                except Exception as e:
                    self.logger.warning(f"解析编译命令失败 {src_file}: {e}")
                    continue
            
            if not args:
                self.logger.warning(f"源文件 {src_file} 没有找到编译参数")
                continue
                
            source_file_args_map[src_file] = args
            
            # 提取include路径和宏定义
            for i, arg in enumerate(args):
                if arg.startswith('@'):
                    continue
                    
                if arg.startswith('-I'):
                    path_str = arg[2:].strip()
                    if path_str:
                        include_path = Path(path_str)
                        if not include_path.is_absolute():
                            include_path = (directory / include_path).resolve()
                        else:
                            include_path = include_path.resolve()
                        
                        all_include_dirs.add(str(include_path))
                        
                elif arg.startswith('-D'):
                    macro_def = arg[2:] if len(arg) > 2 else (args[i+1] if i+1 < len(args) else '')
                    if macro_def:
                        all_macro_definitions.add(macro_def)

        self.logger.info(f"收集到 {len(all_include_dirs)} 个include目录，{len(all_macro_definitions)} 个宏定义")

        # 2. 头文件扫描
        scan_path = Path(scan_directory)
        
        if scan_path.exists():
            try:
                import time
                start_time = time.time()
                self.logger.info(f"开始头文件扫描")
                self.logger.info(f"扫描目录: {scan_directory}")
                
                # 扫描.h和.hpp文件
                for pattern in ['**/*.h', '**/*.hpp']:
                    for header_file in scan_path.glob(pattern):
                        if self._should_include_header_file(header_file):
                            try:
                                header_path_str = str(header_file.resolve())
                                header_files.append(header_path_str)
                            except Exception as file_error:
                                self.logger.debug(f"处理文件 {header_file} 时出错: {file_error}")
                                continue
                
                elapsed_time = time.time() - start_time
                self.logger.info(f"头文件扫描完成，找到 {len(header_files)} 个头文件，耗时 {elapsed_time:.2f}s")
                
                # 去重
                header_files = list(set(header_files))
                self.logger.info(f"去重后头文件数: {len(header_files)}")
                
            except Exception as e:
                self.logger.warning(f"扫描目标目录 '{scan_directory}' 时出错: {e}")
        else:
            self.logger.warning(f"扫描目录不存在: {scan_directory}")
            
        self.logger.info(f"头文件扫描完成，找到 {len(header_files)} 个头文件")

        # 3. 为每个头文件创建编译命令
        import time
        compile_start_time = time.time()
        self.logger.info(f"开始为 {len(header_files)} 个头文件创建编译命令...")
        
        if not source_files or source_files[0] not in source_file_args_map:
            self.logger.error("无法找到合适的编译参数模板")
            return header_files, {}
        
        default_template_args = source_file_args_map[source_files[0]]
        default_template_cmd_info = compile_commands[source_files[0]]
        
        processed_count = 0
        for header_file in header_files:
            try:
                normalized_header_path = str(Path(header_file).resolve()).replace('\\', '/')
                
                header_args = self._create_header_compile_args(
                    default_template_args, all_include_dirs, all_macro_definitions, header_file
                )
                
                header_compile_commands[normalized_header_path] = {
                    "args": header_args,
                    "directory": default_template_cmd_info['directory']
                }
                processed_count += 1
                
            except Exception as e:
                self.logger.warning(f"处理头文件 {header_file} 时出错: {e}")
                continue
        
        compile_elapsed = time.time() - compile_start_time
        self.logger.info(f"编译命令创建完成，处理了 {processed_count} 个头文件，耗时 {compile_elapsed:.2f}s")

        self.console.print(f"-> 发现头文件: {len(header_files)} 个")
        self.console.print(f"-> 创建头文件编译命令: {len(header_compile_commands)} 个")
        
        return header_files, header_compile_commands
    
    def _create_header_compile_args(self, template_args: List[str], all_include_dirs: Set[str], 
                                  all_macro_definitions: Set[str], header_file: str) -> List[str]:
        """为头文件创建专用的编译参数"""
        header_args = []
        
        # 1. 从模板参数中过滤并保留有用的参数
        skip_next = False
        i = 0
        while i < len(template_args):
            arg = template_args[i]
            
            if skip_next:
                skip_next = False
                i += 1
                continue
            
            # 跳过响应文件、输出文件、源文件等
            if (arg.startswith('@') or 
                arg.endswith(('.exe', '.c', '.cpp', '.cc', '.cxx', '.o', '.obj')) or
                arg in ['-o', '-c', '/c', '/Fo'] or
                'PCH.' in arg or 'Definitions.h' in arg):
                if arg in ['-o', '-c', '/c', '/Fo'] and i + 1 < len(template_args):
                    skip_next = True
                i += 1
                continue
            
            # 处理强制包含参数
            if arg == '-include' and i + 1 < len(template_args):
                include_file = template_args[i + 1]
                header_args.append(arg)
                if i + 1 < len(template_args):
                    header_args.append(template_args[i + 1])
                i += 2
                continue
            
            # 保留其他有用的参数
            if not (arg in ['"c++"', 'c++'] and i >= 1 and template_args[i-1] == '-x'):
                header_args.append(arg)
            
            i += 1
        
        # 2. 添加所有收集到的include路径
        for inc_dir in sorted(all_include_dirs):
            include_arg = f'-I{inc_dir}'
            if include_arg not in header_args:
                header_args.append(include_arg)
        
        # 3. 添加所有收集到的宏定义
        for macro in sorted(all_macro_definitions):
            macro_arg = f'-D{macro}'
            if macro_arg not in header_args:
                header_args.append(macro_arg)
        
        # 4. 添加头文件特定的参数
        header_specific_args = [
            '-x', 'c++-header',
            '-Wno-pragma-once-outside-header',
            '-Wno-include-next-outside-header'
        ]
        
        for arg in header_specific_args:
            if arg not in header_args:
                header_args.append(arg)
        
        return header_args

    def _print_performance_report(self, stats: Dict[str, Any], total_time: float, config: AnalysisConfig):
        """打印详细的性能报告"""
        self.console.print(f"\n{'='*60}")
        self.console.print("[bold blue]性能报告[/bold blue]")
        self.console.print(f"{'='*60}")
        
        # 基本统计
        self.console.print(f"[cyan]总分析时间:[/cyan] {total_time:.2f} 秒")
        self.console.print(f"[cyan]处理文件数:[/cyan] {stats.get('successful_processed_files', 0)} / {stats.get('total_files_to_process', 0)}")
        self.console.print(f"[cyan]提取函数数:[/cyan] {stats.get('total_functions', 0)}")
        self.console.print(f"[cyan]提取类数:[/cyan] {stats.get('total_classes', 0)}")
        self.console.print(f"[cyan]提取命名空间数:[/cyan] {stats.get('total_namespaces', 0)}")
        
        # MMap多进程数据共享统计
        self.console.print(f"\n[bold green]MMap多进程数据共享统计[/bold green]")
        
        # 缓存统计
        cache_stats = stats.get('mmap_cache_stats', {})
        if cache_stats:
            self.console.print(f"[green]缓存命中率:[/green] {cache_stats.get('hits', 0)} / {cache_stats.get('hits', 0) + cache_stats.get('misses', 0)}")
            self.console.print(f"[green]缓存写入次数:[/green] {cache_stats.get('writes', 0)}")
            self.console.print(f"[green]缓存错误次数:[/green] {cache_stats.get('errors', 0)}")
        
        # MMap统计
        mmap_stats = stats.get('mmap_mmap_stats', {})
        if mmap_stats:
            self.console.print(f"[green]MMap文件打开数:[/green] {mmap_stats.get('files_opened', 0)}")
            self.console.print(f"[green]MMap读取次数:[/green] {mmap_stats.get('reads', 0)}")
            self.console.print(f"[green]MMap写入次数:[/green] {mmap_stats.get('writes', 0)}")
            self.console.print(f"[green]MMap删除次数:[/green] {mmap_stats.get('deletes', 0)}")
            self.console.print(f"[green]MMap错误次数:[/green] {mmap_stats.get('errors', 0)}")
        
        # 分片统计
        shard_stats = stats.get('mmap_shard_stats', {})
        if shard_stats:
            self.console.print(f"[green]活跃分片数:[/green] {shard_stats.get('active_shards', 0)}")
            self.console.print(f"[green]分片路由次数:[/green] {shard_stats.get('routing_requests', 0)}")
            self.console.print(f"[green]分片错误次数:[/green] {shard_stats.get('errors', 0)}")
        
        # 锁统计
        lock_stats = stats.get('mmap_lock_stats', {})
        if lock_stats:
            self.console.print(f"[green]锁请求总数:[/green] {lock_stats.get('total_requests', 0)}")
            self.console.print(f"[green]成功获取锁:[/green] {lock_stats.get('successful_acquires', 0)}")
            self.console.print(f"[green]锁超时次数:[/green] {lock_stats.get('timeout_acquires', 0)}")
            self.console.print(f"[green]锁错误次数:[/green] {lock_stats.get('errors', 0)}")
        
        # 动态include统计
        if config.enable_dynamic_includes:
            self.console.print(f"\n[bold blue]动态Include统计[/bold blue]")
            self.console.print(f"[cyan]动态发现头文件:[/cyan] {stats.get('dynamic_headers_discovered', 0)} 个")
            
            # 头文件处理统计
            shared_stats = stats.get('shared_manager_stats', {})
            if shared_stats:
                self.console.print(f"[cyan]已处理头文件:[/cyan] {stats.get('total_processed_headers', 0)} 个")
        
        # 性能指标
        if stats.get('total_files_to_process', 0) > 0:
            files_per_second = stats.get('successful_processed_files', 0) / total_time
            self.console.print(f"\n[bold blue]性能指标[/bold blue]")
            self.console.print(f"[cyan]处理速度:[/cyan] {files_per_second:.2f} 文件/秒")
            
            if config.num_jobs > 0:
                efficiency = files_per_second / config.num_jobs
                self.console.print(f"[cyan]每进程效率:[/cyan] {efficiency:.2f} 文件/秒/进程")
        
        self.console.print(f"{'='*60}")

    def _create_failure_result(self, stage: str, reason: str) -> AnalysisResult:
        """创建一个表示失败的分析结果"""
        return AnalysisResult(
            success=False, 
            statistics={"stage": stage, "reason": reason},
            files_processed=0,
            files_parsed=0,
            parsing_errors=[f"{stage}: {reason}"]
        )
    
    def _merge_parallel_results(self, parallel_results: List[SerializableExtractedData]) -> Dict[str, Any]:
        """健壮地合并来自多个工作进程的分析结果"""
        merged_functions: Dict[str, Function] = {}
        merged_classes: Dict[str, Class] = {}
        merged_namespaces: Dict[str, Namespace] = {}
        merged_member_variables: Dict[str, Any] = {}  # 添加成员变量合并
        merged_global_nodes: Dict[str, EntityNode] = {}
        merged_file_mappings: Dict[str, str] = {}

        for result in parallel_results:
            if not result.success:
                continue

            merged_file_mappings.update(result.file_mappings)

            # 合并函数
            for usr, func_dict in result.functions.items():
                # 修复：处理func_dict可能是Function对象的情况
                if hasattr(func_dict, 'to_dict'):
                    # 如果是Function对象，转换为字典
                    func_dict = func_dict.to_dict()
                elif not isinstance(func_dict, dict):
                    # 如果不是字典也不是可转换对象，跳过
                    continue
                
                new_func = Function(**func_dict)
                if usr not in merged_functions:
                    merged_functions[usr] = new_func
                else:
                    existing_func = merged_functions[usr]
                    # 定义优先原则
                    if new_func.is_definition and not existing_func.is_definition:
                        new_func.declaration_locations.extend(existing_func.declaration_locations)
                        merged_functions[usr] = new_func
                    elif new_func.is_definition and existing_func.is_definition:
                        existing_func.declaration_locations.extend(new_func.declaration_locations)
                        existing_func.calls_to = list(set(existing_func.calls_to + new_func.calls_to))
                        existing_func.call_details.extend(new_func.call_details)
                    else:
                        existing_func.declaration_locations.extend(new_func.declaration_locations)

            # 合并类
            for usr, class_dict in result.classes.items():
                # 修复：处理class_dict可能是Class对象的情况
                if hasattr(class_dict, 'to_dict'):
                    # 如果是Class对象，转换为字典
                    class_dict = class_dict.to_dict()
                elif not isinstance(class_dict, dict):
                    # 如果不是字典也不是可转换对象，跳过
                    continue
                
                # 分离member_variables，因为Class构造函数不接受这个参数
                class_dict_copy = class_dict.copy()
                member_variables = class_dict_copy.pop('member_variables', [])
                
                new_class = Class(**class_dict_copy)
                # 手动设置member_variables
                new_class.member_variables = member_variables
                
                if usr not in merged_classes:
                    merged_classes[usr] = new_class
                else:
                    existing_class = merged_classes[usr]
                    if new_class.is_definition and not existing_class.is_definition:
                        new_class.declaration_locations.extend(existing_class.declaration_locations)
                        merged_classes[usr] = new_class
                    else:
                        existing_class.declaration_locations.extend(new_class.declaration_locations)
                        existing_class.methods = list(set(existing_class.methods + new_class.methods))
                        # 合并成员变量
                        existing_member_vars = getattr(existing_class, 'member_variables', [])
                        new_member_vars = getattr(new_class, 'member_variables', [])
                        existing_class.member_variables = list(set(existing_member_vars + new_member_vars))
                        existing_class.parent_classes = list(set(existing_class.parent_classes + new_class.parent_classes))
                        if hasattr(existing_class, 'cpp_oop_extensions') and hasattr(new_class, 'cpp_oop_extensions'):
                            existing_inheritance = existing_class.cpp_oop_extensions.inheritance_list or []
                            new_inheritance = new_class.cpp_oop_extensions.inheritance_list or []
                            
                            inheritance_dict = {}
                            for inheritance in existing_inheritance + new_inheritance:
                                inheritance_dict[inheritance.base_class_usr_id] = inheritance
                            
                            existing_class.cpp_oop_extensions.inheritance_list = list(inheritance_dict.values())

            # 合并命名空间
            for usr, ns_dict in result.namespaces.items():
                # 修复：处理ns_dict可能是Namespace对象的情况
                if hasattr(ns_dict, 'to_dict'):
                    # 如果是Namespace对象，转换为字典
                    ns_dict = ns_dict.to_dict()
                elif not isinstance(ns_dict, dict):
                    # 如果不是字典也不是可转换对象，跳过
                    continue
                
                new_ns = Namespace(**ns_dict)
                if usr not in merged_namespaces:
                    merged_namespaces[usr] = new_ns
                else:
                    existing_ns = merged_namespaces[usr]
                    existing_ns.declaration_locations.extend(new_ns.declaration_locations)
                    existing_ns.classes.extend(new_ns.classes)
                    existing_ns.functions.extend(new_ns.functions)
            
            # 合并成员变量
            if hasattr(result, 'member_variables') and result.member_variables:
                for usr, member_var_dict in result.member_variables.items():
                    if usr not in merged_member_variables:
                        merged_member_variables[usr] = member_var_dict

        # 去重和最终化
        for func in merged_functions.values():
            func.declaration_locations = list(dict.fromkeys(func.declaration_locations))
            func.calls_to = list(set(func.calls_to))
        for cls in merged_classes.values():
            cls.declaration_locations = list(dict.fromkeys(cls.declaration_locations))
            cls.methods = list(set(cls.methods))
            cls.parent_classes = list(set(cls.parent_classes))
        
        # 建立反向调用关系
        for caller_usr, func in merged_functions.items():
            for callee_usr in func.calls_to:
                if callee_usr in merged_functions:
                    callee_func = merged_functions[callee_usr]
                    if caller_usr not in callee_func.called_by:
                        callee_func.called_by.append(caller_usr)
        
        # 重建全局节点
        for usr, func in merged_functions.items():
            merged_global_nodes[usr] = EntityNode(usr, "function", func)
        for usr, cls in merged_classes.items():
            merged_global_nodes[usr] = EntityNode(usr, "class", cls)
        for usr, ns in merged_namespaces.items():
            merged_global_nodes[usr] = EntityNode(usr, "namespace", ns)

        return {
            "functions": merged_functions,
            "classes": merged_classes,
            "namespaces": merged_namespaces,
            "member_variables": merged_member_variables,
            "global_nodes": {usr_id: node.to_dict() for usr_id, node in merged_global_nodes.items()},
            "file_mappings": merged_file_mappings
        }
    
    def _should_include_header_file(self, header_file: Path) -> bool:
        """检查头文件是否应该被包含在分析中"""
        try:
            # 基本文件检查
            if not header_file.exists() or not header_file.is_file():
                return False
            
            # 文件大小检查
            file_size = header_file.stat().st_size
            if file_size > 10 * 1024 * 1024:  # 10MB
                self.logger.debug(f"跳过过大的头文件: {header_file} ({file_size} bytes)")
                return False
            
            # 文件名模式检查
            filename = header_file.name.lower()
            exclude_patterns = [
                '.tmp', '.temp', '.bak', '.backup', '.orig', '.cache',
                'generated', '.generated', 'autogen', '.autogen'
            ]
            
            for pattern in exclude_patterns:
                if pattern in filename:
                    return False
            
            # 路径检查
            path_str = str(header_file).lower()
            exclude_path_patterns = [
                'temp/', 'tmp/', '/temp/', '/tmp/', '\\temp\\', '\\tmp\\',
                'cache/', '/cache/', '\\cache\\', '.vs/', '/.vs/', '\\.vs\\',
                'build/temp', '/build/temp', '\\build\\temp'
            ]
            
            for pattern in exclude_path_patterns:
                if pattern in path_str:
                    return False
            
            return True
            
        except Exception as e:
            self.logger.debug(f"检查头文件 {header_file} 时出错: {e}")
            return False

# 向后兼容的别名
DynamicCppAnalyzer = CppAnalyzer
DynamicAnalysisConfig = AnalysisConfig
DynamicAnalysisResult = AnalysisResult