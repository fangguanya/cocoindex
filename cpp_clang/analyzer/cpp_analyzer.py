"""
C++ Analyzer Main Module (v4.0) - 集成动态include处理和多进程安全版本

集成功能：
1. 动态include处理 - 不预扫描头文件，在translation unit编译过程中根据include指令动态路由
2. 多进程安全和头文件去重处理
3. 原有的高性能并行处理能力
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
from .distributed_file_manager import DistributedFileIdManager
from .validation_engine import ValidationEngine, ValidationLevel
from .data_structures import Function, Class, Namespace, EntityNode, Location
from .performance_profiler import profiler, profile_function, DetailedLogger
from .include_directive_parser import IncludeDirectiveParser
from .shared_header_manager import (
    SharedHeaderManager, ThreadSafeHeaderProcessor, 
    get_shared_header_manager, init_shared_header_manager
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
    """分析配置类 - 支持动态include和多进程安全"""
    def __init__(self, project_root: str, scan_directory: str, 
                 output_path: str = "cpp_analysis_result.json",
                 compile_commands_path: Optional[str] = None,
                 max_files: Optional[int] = None,
                 verbose: bool = False,
                 num_jobs: int = 0,  # 0 表示自动确定
                 include_extensions: Optional[set] = None,
                 exclude_patterns: Optional[set] = None,
                 enable_dynamic_includes: bool = True,
                 enable_multiprocess_safety: bool = True,
                 enable_legacy_header_scan: bool = False):
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
        self.enable_multiprocess_safety = enable_multiprocess_safety
        self.enable_legacy_header_scan = enable_legacy_header_scan


class AnalysisResult:
    """分析结果类 - 支持动态include统计"""
    def __init__(self, success: bool, output_path: Optional[str] = None,
                 statistics: Optional[Dict[str, Any]] = None,
                 files_processed: int = 0, files_parsed: int = 0,
                 parsing_errors: Optional[List[str]] = None,
                 include_statistics: Optional[Dict[str, Any]] = None):
        self.success = success
        self.output_path = output_path
        self.statistics = statistics or {}
        self.files_processed = files_processed
        self.files_parsed = files_parsed
        self.parsing_errors = parsing_errors or []
        self.include_statistics = include_statistics or {}

# 全局变量用于多进程worker
g_parser: Optional[ClangParser] = None
g_extractor: Optional[EntityExtractor] = None
g_project_root: Optional[str] = None
g_file_manager: Optional[DistributedFileIdManager] = None
g_compile_commands: Optional[Dict[str, Any]] = None
g_include_parser: Optional[IncludeDirectiveParser] = None
g_enable_dynamic_includes: bool = True
g_enable_multiprocess_safety: bool = True

def _init_worker(compile_commands: Dict[str, Any], project_root: str, 
                all_files: List[str],  # 用文件列表替代file_id_manager
                enable_dynamic_includes: bool = True,
                enable_multiprocess_safety: bool = True):
    """初始化工作进程 - 支持动态include和多进程安全，修复序列化问题"""
    global g_parser, g_extractor, g_project_root, g_file_manager, g_compile_commands
    global g_include_parser, g_enable_dynamic_includes, g_enable_multiprocess_safety
    
    try:
        # 启用缓存的解析器，关闭详细输出以提升性能
        g_parser = ClangParser(console=None, verbose=False, enable_cache=True)
        
        # 直接接收编译命令，而不是重新加载
        g_compile_commands = compile_commands
        g_parser.compile_commands = compile_commands
        
        # 在worker进程中重新创建文件管理器和实体提取器，避免序列化问题
        g_project_root = project_root
        g_file_manager = DistributedFileIdManager(project_root, all_files)
        g_extractor = EntityExtractor(g_file_manager)
        
        # 设置功能开关
        g_enable_dynamic_includes = enable_dynamic_includes
        g_enable_multiprocess_safety = enable_multiprocess_safety
        
        # 初始化动态include解析器
        if g_enable_dynamic_includes:
            g_include_parser = IncludeDirectiveParser(project_root)
        
        # 初始化多进程安全的头文件管理器
        if g_enable_multiprocess_safety:
            init_shared_header_manager(project_root)
        
        from .logger import get_logger
        logger = get_logger()
        logger.info(f"Worker初始化成功 - 动态include: {g_enable_dynamic_includes}, 多进程安全: {g_enable_multiprocess_safety}")
        
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
    """并行解析和提取单个文件实体的工作函数 - 支持动态include和多进程安全 - 增强版"""
    global g_parser, g_extractor, g_compile_commands, g_include_parser
    global g_enable_dynamic_includes, g_enable_multiprocess_safety
    
    start_time = time.time()
    
    try:
        # 修复logging导入问题
        from .logger import get_logger
        logger = get_logger()
        
        if not g_parser or not g_extractor or not g_compile_commands:
            logger.error(f"Worker未正确初始化，文件: {file_path}")
            return SerializableExtractedData.empty_result(file_path, "Worker not initialized")
        
        # 标记修复已应用（简化版本，去除对外部修复模块的依赖）
        if not hasattr(g_extractor, '_fixes_applied'):
            g_extractor._fixes_applied = True
            logger.debug("已标记分析问题修复状态")

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
        
        # 3. 处理动态发现的头文件（如果启用多进程安全）
        header_processing_results = {}
        if g_enable_multiprocess_safety and discovered_headers:
            try:
                from .shared_header_manager import get_shared_header_manager
                shared_manager = get_shared_header_manager(g_project_root)
                
                # 使用共享管理器处理头文件
                for header_info in discovered_headers:
                    header_path = header_info['file_path']
                    compile_args = header_info['compile_args']
                    header_directory = header_info['directory']
                    
                    # 检查是否应该处理这个头文件
                    if shared_manager.register_header_for_processing(header_path, compile_args, header_directory):
                        try:
                            # 处理头文件
                            header_result = g_parser.parse_file(header_path)
                            if header_result and header_result.success:
                                header_processing_results[header_path] = {
                                    'status': 'success',
                                    'processed_by_current_worker': True
                                }
                                shared_manager.mark_header_processed(header_path, True)
                            else:
                                header_processing_results[header_path] = {
                                    'status': 'failed',
                                    'error': 'Parsing failed'
                                }
                                shared_manager.mark_header_processed(header_path, False)
                        except Exception as header_error:
                            logger.warning(f"处理头文件失败 {header_path}: {header_error}")
                            header_processing_results[header_path] = {
                                'status': 'failed',
                                'error': str(header_error)
                            }
                            shared_manager.mark_header_processed(header_path, False)
                    else:
                        header_processing_results[header_path] = {
                            'status': 'skipped',
                            'reason': 'already_processed_by_other_worker'
                        }
                        
            except Exception as e:
                logger.warning(f"多进程安全头文件处理失败: {e}")
        
        # 4. 提取实体
        extraction_start_time = time.time()
        extracted_data = g_extractor.extract_from_files([parsed_file], None)
        extraction_time = time.time() - extraction_start_time
        
        # 5. 准备可序列化的结果
        stats = {
            "functions": len(extracted_data.get('functions', {})),
            "classes": len(extracted_data.get('classes', {})),
            "dynamic_headers": len(dynamic_headers),
            "dynamic_header_list": dynamic_headers,
            "header_processing_results": header_processing_results
        }
        
        serializable_result = SerializableExtractedData(
            file_path=file_path,
            success=True,
            parse_time=parse_time,
            extraction_time=extraction_time,
            functions={usr: func.__dict__ for usr, func in extracted_data.get('functions', {}).items()},
            classes={usr: cls.__dict__ for usr, cls in extracted_data.get('classes', {}).items()},
            namespaces={usr: ns.__dict__ for usr, ns in extracted_data.get('namespaces', {}).items()},
            global_nodes=extracted_data.get('global_nodes', {}),
            file_mappings=extracted_data.get('file_mappings', {}),
            stats=stats
        )
        return serializable_result

    except Exception as e:
        from .logger import get_logger
        logger = get_logger()
        logger.error(f"处理异常 {file_path}: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return SerializableExtractedData.empty_result(file_path, str(e))

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
    """C++代码分析器 (v4.0) - 集成动态include处理和多进程安全"""
    
    def __init__(self, console: Optional[Console] = None):
        """初始化分析器"""
        self.console = console or Console()
        self.logger = get_logger()
        self.include_parser = None
        self.shared_header_manager = None
    
    @profile_function()
    def analyze(self, config: AnalysisConfig) -> AnalysisResult:
        """
        执行完整的C++代码分析 (v4.0 流程)
        支持动态include处理和多进程安全
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

            # 2. 初始化组件
            self.console.print("\n[bold]2. 初始化分析组件...[/bold]")
            with profiler.timer("init_components"):
                if config.enable_dynamic_includes:
                    self.include_parser = IncludeDirectiveParser(config.project_root)
                
                if config.enable_multiprocess_safety:
                    self.shared_header_manager = get_shared_header_manager(config.project_root)

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
                file_id_manager = DistributedFileIdManager(config.project_root, filtered_files)

            # 6. 并行解析和提取
            self.console.print("\n[bold]6. 开始并行解析和提取实体...[/bold]")
            self.logger.info("6. 开始并行解析和提取实体...")
            
            with profiler.timer("parallel_parsing_and_extraction"):
                num_jobs = config.num_jobs if config.num_jobs > 0 else multiprocessing.cpu_count()
                self.console.print(f"-> 使用 {num_jobs} 个并行进程")
                
                all_results = []
                
                try:
                    with Progress(console=self.console) as progress:
                        task = progress.add_task("[cyan]解析和提取中...", total=len(filtered_files))
                        
                        # 将完整的编译命令和文件列表传递给工作进程，避免序列化问题
                        init_args = (
                            temp_parser.compile_commands, 
                            config.project_root, 
                            filtered_files,  # 传递文件列表而不是manager对象
                            config.enable_dynamic_includes,
                            config.enable_multiprocess_safety
                        )
                        
                        with multiprocessing.Pool(
                            processes=num_jobs,
                            initializer=_init_worker,
                            initargs=init_args
                        ) as pool:
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
                    if config.enable_multiprocess_safety:
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

            # 9. 验证提取的数据
            self.console.print("\n[bold]9. 验证提取的数据...[/bold]")
            self.logger.info("9. 验证提取的数据...")
            
            with profiler.timer("validate_data"):
                validation_engine = ValidationEngine(ValidationLevel.STANDARD)
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
                if config.enable_multiprocess_safety and self.shared_header_manager:
                    shared_stats = self.shared_header_manager.get_processing_statistics()
                    stats.update({
                        "shared_manager_stats": shared_stats,
                        "total_processed_headers": len(self.shared_header_manager.get_processed_headers())
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
                include_statistics=include_stats
            )

        except Exception as e:
            self.logger.error(f"分析过程中发生严重错误: {e}\n{traceback.format_exc()}")
            return self._create_failure_result("Exception", str(e))
        
        finally:
            # 清理资源
            if self.shared_header_manager:
                self.shared_header_manager.cleanup_expired_entries()

    def _determine_analysis_mode(self, config: AnalysisConfig) -> str:
        """确定分析模式描述"""
        modes = []
        if config.enable_dynamic_includes:
            modes.append("动态Include")
        if config.enable_multiprocess_safety:
            modes.append("多进程安全")
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
        self.console.print("\n[bold cyan]📊 性能分析报告[/bold cyan]")
        self.console.print("=" * 60)
        
        # 基本统计
        self.console.print(f"📁 处理文件数: {stats['successful_processed_files']}/{stats['total_files_to_process']}")
        
        if config.enable_dynamic_includes:
            self.console.print(f"🔍 动态发现头文件: {stats['dynamic_headers_discovered']} 个")
        else:
            self.console.print(f"📄 源文件: {stats['total_files_in_compile_commands']}, 头文件: {stats['total_header_files_added']}")
        
        self.console.print(f"🔍 发现实体: 函数 {stats['total_functions']}, 类 {stats['total_classes']}, 命名空间 {stats['total_namespaces']}")
        self.console.print(f"⏱️  总耗时: {total_time:.2f} 秒")
        self.console.print(f"🏗️  分析模式: {stats['analysis_mode']}")
        
        # 性能指标
        files_per_sec = stats['successful_processed_files'] / total_time if total_time > 0 else 0
        self.console.print(f"🚀 处理速度: {files_per_sec:.2f} 文件/秒")
        
        # 多进程安全统计
        if config.enable_multiprocess_safety and 'shared_manager_stats' in stats:
            shared_stats = stats['shared_manager_stats']
            self.console.print(f"\n[bold green]🔒 多进程安全统计:[/bold green]")
            self.console.print(f"  • 共享状态总数: {shared_stats.get('shared_total', 0)}")
            self.console.print(f"  • 共享已处理: {shared_stats.get('shared_processed', 0)}")
            self.console.print(f"  • 本地缓存: {shared_stats.get('local_cache_size', 0)}")
        
        # 动态处理优势
        if config.enable_dynamic_includes:
            self.console.print(f"\n[bold green]🎯 动态处理优势:[/bold green]")
            self.console.print(f"  • 避免预扫描大量不需要的头文件")
            self.console.print(f"  • 根据实际include关系动态发现依赖")
            self.console.print(f"  • 减少内存占用和处理时间")
        
        # 性能评级
        if total_time < 30:
            rating = "[green]🌟 优秀[/green]"
        elif total_time < 120:
            rating = "[yellow]⚡ 良好[/yellow]"
        elif total_time < 300:
            rating = "[orange1]⚠️  一般[/orange1]"
        else:
            rating = "[red]🐌 需要优化[/red]"
        
        self.console.print(f"📈 性能评级: {rating}")
        
        # 输出详细的计时器报告
        self.console.print("\n[bold]⏱️  详细计时分析:[/bold]")
        profiler.print_report()

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
        merged_global_nodes: Dict[str, EntityNode] = {}
        merged_file_mappings: Dict[str, str] = {}

        for result in parallel_results:
            if not result.success:
                continue

            merged_file_mappings.update(result.file_mappings)

            # 合并函数
            for usr, func_dict in result.functions.items():
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
                new_class = Class(**class_dict)
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
                new_ns = Namespace(**ns_dict)
                if usr not in merged_namespaces:
                    merged_namespaces[usr] = new_ns
                else:
                    existing_ns = merged_namespaces[usr]
                    existing_ns.declaration_locations.extend(new_ns.declaration_locations)
                    existing_ns.classes.extend(new_ns.classes)
                    existing_ns.functions.extend(new_ns.functions)

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