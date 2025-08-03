"""
C++ Analyzer Main Module (v2.3) - 修复版本

修复了头文件编译命令创建的问题，正确处理command字段而不是args字段
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

# Windows平台需要设置multiprocessing启动方法
if platform.system() == 'Windows':
    multiprocessing.set_start_method('spawn', force=True)


class AnalysisConfig:
    """分析配置类 - 性能优化版"""
    def __init__(self, project_root: str, scan_directory: str, 
                 output_path: str = "cpp_analysis_result.json",
                 compile_commands_path: Optional[str] = None,
                 max_files: Optional[int] = None,
                 verbose: bool = False,
                 num_jobs: int = 0,  # 0 表示自动确定
                 include_extensions: Optional[set] = None,
                 exclude_patterns: Optional[set] = None):
        self.project_root = project_root
        self.scan_directory = scan_directory
        self.output_path = output_path
        self.compile_commands_path = compile_commands_path
        self.max_files = max_files
        self.verbose = verbose
        self.num_jobs = num_jobs
        self.include_extensions = include_extensions or {'.h', '.hpp', '.cpp', '.cc', '.cxx', '.c'}
        self.exclude_patterns = exclude_patterns or set()


class AnalysisResult:
    """分析结果类 - 性能优化版"""
    def __init__(self, success: bool, output_path: Optional[str] = None,
                 statistics: Optional[Dict[str, Any]] = None,
                 files_processed: int = 0, files_parsed: int = 0,
                 parsing_errors: Optional[List[str]] = None):
        self.success = success
        self.output_path = output_path
        self.statistics = statistics or {}
        self.files_processed = files_processed
        self.files_parsed = files_parsed
        self.parsing_errors = parsing_errors or []

# 全局变量用于多进程worker
g_parser: Optional[ClangParser] = None
g_extractor: Optional[EntityExtractor] = None
g_project_root: Optional[str] = None

g_file_manager: Optional[DistributedFileIdManager] = None
g_compile_commands: Optional[Dict[str, Any]] = None

def _init_worker(compile_commands: Dict[str, Any], project_root: str, file_id_manager: DistributedFileIdManager):
    """初始化工作进程 - 性能优化版"""
    global g_parser, g_extractor, g_project_root, g_file_manager, g_compile_commands
    try:
        # 启用缓存的解析器，关闭详细输出以提升性能
        g_parser = ClangParser(console=None, verbose=False, enable_cache=True)
        
        # 直接接收编译命令，而不是重新加载
        g_compile_commands = compile_commands
        g_parser.compile_commands = compile_commands
        
        # 初始化文件管理器和实体提取器
        g_project_root = project_root
        g_file_manager = file_id_manager
        g_extractor = EntityExtractor(g_file_manager)
        
        from .logger import get_logger
        logger = get_logger()
        logger.info(f"Worker initialized successfully with cache enabled")
        
    except Exception as e:
        from .logger import get_logger
        logger = get_logger()
        logger.error(f"Failed to initialize worker: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        g_parser = None
        g_extractor = None
        g_file_manager = None

def _parse_and_extract_worker(file_path: str) -> Optional[SerializableExtractedData]:
    """并行解析和提取单个文件实体的工作函数"""
    global g_parser, g_extractor, g_compile_commands
    
    start_time = time.time()
    
    try:
        # 修复logging导入问题
        from .logger import get_logger
        logger = get_logger()
        
        if not g_parser or not g_extractor or not g_compile_commands:
            logger.error(f"Worker not initialized properly for file {file_path}")
            return SerializableExtractedData.empty_result(file_path, "Worker not initialized")

        # 关键修复：在解析前切换到正确的工作目录
        import os
        original_cwd = os.getcwd()
        
        compile_info = g_compile_commands.get(file_path)
        if not compile_info:
            # 使用规范化路径再次尝试
            normalized_path = str(Path(file_path).resolve()).replace('\\', '/')
            compile_info = g_compile_commands.get(normalized_path)
            if not compile_info:
                logger.error(f"No compile info found for {file_path}")
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
        
        # 2. 提取实体
        extraction_start_time = time.time()
        extracted_data = g_extractor.extract_from_files([parsed_file], None)
        extraction_time = time.time() - extraction_start_time
        
        # 3. 准备可序列化的结果
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
            stats={
                "functions": len(extracted_data.get('functions', {})),
                "classes": len(extracted_data.get('classes', {}))
            }
        )
        return serializable_result

    except Exception as e:
        from .logger import get_logger
        logger = get_logger()
        logger.error(f"EXCEPTION in _parse_and_extract_worker for {file_path}: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return SerializableExtractedData.empty_result(file_path, str(e))

class CppAnalyzer:
    """C++代码分析器 (v2.3) - 修复版本"""
    
    def __init__(self, console: Optional[Console] = None):
        """初始化分析器"""
        self.console = console or Console()
        self.logger = get_logger()
    
    @profile_function("CppAnalyzer.analyze")
    def analyze(self, config: AnalysisConfig) -> AnalysisResult:
        """
        执行完整的C++代码分析 (v2.3 流程)
        """
        logger = DetailedLogger("C++项目分析")
        self.console.print("[bold green]--- 开始 C++ 项目分析 (v2.3) ---[/bold green]")
        self.logger.info("开始 C++ 项目分析 (v2.3)")
        
        try:
            # 1. 加载 compile_commands.json 作为分析的权威来源
            self.console.print("\n[bold]1. 加载 compile_commands.json...[/bold]")
            self.logger.info("1. 加载 compile_commands.json...")
            
            with profiler.timer("load_compile_commands"):
                if not config.compile_commands_path or not Path(config.compile_commands_path).exists():
                    msg = f"必须提供有效的 compile_commands.json 路径。"
                    self.logger.error(msg)
                    return self._create_failure_result("Configuration", msg)
                
                # 临时解析器，仅用于获取文件列表和编译命令 - 启用缓存优化
                temp_parser = ClangParser(verbose=config.verbose, enable_cache=True)
                temp_parser.load_compile_commands(config.compile_commands_path)
                
                # ClangParser现在返回规范化的绝对路径，所以直接使用即可
                source_files = list(temp_parser.compile_commands.keys())

            if not source_files:
                return self._create_failure_result("Parsing", "compile_commands.json 中未找到任何文件记录。")
            
            logger.checkpoint("compile_commands加载完成", source_files_count=len(source_files))

            # 1.5. 扫描所有相关的头文件并为其创建编译命令
            self.console.print("\n[bold]1.5. 扫描项目头文件并创建编译命令...[/bold]")
            with profiler.timer("scan_and_prepare_header_files"):
                header_files, extended_compile_commands = self._scan_and_prepare_header_files(
                    source_files, temp_parser.compile_commands, config.project_root, config.scan_directory
                )
                
                # 更新解析器的编译命令以包含头文件
                temp_parser.compile_commands.update(extended_compile_commands)
                all_files_to_process = source_files + header_files
                
            logger.checkpoint("头文件扫描和编译命令创建完成", 
                            header_files_count=len(header_files),
                            total_files_count=len(all_files_to_process))

            # 1.6. 应用文件过滤规则
            self.console.print("\n[bold]1.6. 应用文件过滤规则...[/bold]")
            with profiler.timer("filter_files"):
                # 使用FileScanner进行专业的文件过滤
                file_scanner = FileScanner()
                filtered_files = file_scanner.filter_files_from_list(all_files_to_process, config.scan_directory)
                
                self.console.print(f"-> 过滤前: {len(all_files_to_process)} 个文件 (源文件: {len(source_files)}, 头文件: {len(header_files)})")
                self.console.print(f"-> 过滤后: {len(filtered_files)} 个文件")
                self.logger.info(f"文件过滤: {len(all_files_to_process)} -> {len(filtered_files)}")
                
                if config.max_files is not None and config.max_files > 0:
                    filtered_files = filtered_files[:config.max_files]
                    self.console.print(f"-> 限制处理: {len(filtered_files)} 个文件")
                    self.logger.info(f"应用文件数量限制: {config.max_files}")
            
            logger.checkpoint("文件过滤完成", filtered_files_count=len(filtered_files))

            # 1.7. 在主进程中创建并初始化确定性的文件管理器
            with profiler.timer("init_file_manager"):
                file_id_manager = DistributedFileIdManager(config.project_root, all_files_to_process)

            # 2. 并行解析和提取
            self.console.print("\n[bold]2. 开始并行解析和提取实体...[/bold]")
            self.logger.info("2. 开始并行解析和提取实体...")
            
            with profiler.timer("parallel_parsing_and_extraction"):
                num_jobs = config.num_jobs if config.num_jobs > 0 else multiprocessing.cpu_count()
                self.console.print(f"-> 使用 {num_jobs} 个并行进程")
                
                all_results = []
                
                try:
                    with Progress(console=self.console) as progress:
                        task = progress.add_task("[cyan]解析和提取中...", total=len(filtered_files))
                        
                        # 将完整的编译命令和文件管理器传递给工作进程
                        init_args = (temp_parser.compile_commands, config.project_root, file_id_manager)
                        
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
                                time.sleep(0.5)  # 增加等待时间，减少CPU占用
                                # 更新进度条显示
                                progress.update(task, description=f"[cyan]并行处理中... ({num_jobs}个进程)")
                            
                            # 获取所有结果
                            results = async_result.get(timeout=300)  # 5分钟超时
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

            # 3. 合并并行处理的结果
            self.console.print("\n[bold]3. 合并分析结果...[/bold]")
            self.logger.info("3. 合并分析结果...")
            
            with profiler.timer("merge_results"):
                extracted_data = self._merge_parallel_results(all_results)
            logger.checkpoint("结果合并完成")

            # 4. 验证提取的数据
            self.console.print("\n[bold]4. 验证提取的数据...[/bold]")
            self.logger.info("4. 验证提取的数据...")
            
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

            # 5. 导出为 JSON
            self.console.print("\n[bold]5. 导出为 JSON...[/bold]")
            self.logger.info("5. 导出为 JSON...")
            
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
                    "total_header_files_added": len(header_files),
                    "total_files_to_process": len(filtered_files),
                    "successful_processed_files": len(successful_files),
                    "total_functions": len(extracted_data.get("functions", {})),
                    "total_classes": len(extracted_data.get("classes", {})),
                    "total_namespaces": len(extracted_data.get("namespaces", {})),
                    "analysis_time_sec": total_analysis_time,
                }
            
            # 输出详细的性能报告
            self._print_performance_report(stats, total_analysis_time)
            
            self.logger.info("C++ 项目分析成功完成。")
            return AnalysisResult(
                success=True,
                output_path=config.output_path,
                statistics=stats,
                files_processed=len(filtered_files),
                files_parsed=len(successful_files),
                parsing_errors=parsing_errors
            )

        except Exception as e:
            self.logger.error(f"分析过程中发生严重错误: {e}\n{traceback.format_exc()}")
            return self._create_failure_result("Exception", str(e))

    def _scan_and_prepare_header_files(self, source_files: List[str], compile_commands: Dict, 
                                     project_root: str, scan_directory: str) -> tuple[List[str], Dict[str, Dict]]:
        """扫描头文件并为其创建编译命令 - 智能继承版本"""
        header_files = []
        header_compile_commands = {}
        
        # 1. 收集所有源文件的编译信息
        all_include_dirs = set()
        all_macro_definitions = set()
        source_file_args_map = {}  # 源文件路径 -> 编译参数映射
        
        self.logger.info("正在分析源文件编译参数...")
        
        for src_file, cmd_info in compile_commands.items():
            directory = Path(cmd_info['directory'])
            
            # 获取编译参数 - 修复版本
            args = cmd_info.get('arguments', [])  # 首先尝试arguments字段
            if not args:
                args = cmd_info.get('args', [])  # 然后尝试args字段
            
            if not args and 'command' in cmd_info:
                try:
                    # 处理command字段，需要正确解析shell命令
                    command_str = cmd_info['command']
                    if isinstance(command_str, str):
                        # 使用shlex正确分割命令行
                        command_parts = shlex.split(command_str)
                        if len(command_parts) > 1:
                            args = command_parts[1:]  # 跳过编译器路径
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
                if arg.startswith('@'):  # 跳过响应文件引用
                    continue
                    
                if arg.startswith('-I'):
                    path_str = arg[2:].strip()
                    if path_str:
                        include_path = Path(path_str)
                        if not include_path.is_absolute():
                            include_path = (directory / include_path).resolve()
                        else:
                            include_path = include_path.resolve()
                        
                        # 添加所有include路径，不限制项目内
                        all_include_dirs.add(str(include_path))
                        
                elif arg.startswith('-D'):
                    macro_def = arg[2:] if len(arg) > 2 else (args[i+1] if i+1 < len(args) else '')
                    if macro_def:
                        all_macro_definitions.add(macro_def)

        self.logger.info(f"收集到 {len(all_include_dirs)} 个include目录，{len(all_macro_definitions)} 个宏定义")

        # 2. 快速头文件扫描 - 极速版本
        scan_path = Path(scan_directory)
        max_header_files = 50    # 大幅减少到50个文件
        max_scan_time = 10       # 最大扫描时间10秒
        
        if scan_path.exists():
            try:
                import time
                start_time = time.time()
                self.logger.info(f"开始快速头文件扫描，限制: {max_header_files} 个文件，最大时间: {max_scan_time}s")
                self.logger.info(f"扫描目录: {scan_directory}")
                
                # 优先扫描特定目录，避免全目录递归
                priority_dirs = ['Source', 'Public', 'Private', 'Classes', 'include', 'Inc']
                
                # 先快速检查优先目录
                for priority_dir in priority_dirs:
                    if len(header_files) >= max_header_files:
                        break
                    if time.time() - start_time > max_scan_time:
                        self.logger.warning(f"头文件扫描达到时间限制 ({max_scan_time}s)")
                        break
                        
                    priority_path = scan_path / priority_dir
                    if priority_path.exists():
                        self.logger.info(f"扫描优先目录: {priority_path}")
                        try:
                            # 只扫描前两层，避免深度递归
                            for header_file in priority_path.glob('*.h'):
                                if len(header_files) >= max_header_files:
                                    break
                                header_files.append(str(header_file.resolve()))
                                self.logger.debug(f"找到头文件: {header_file.name}")
                            
                            # 扫描一层子目录
                            for subdir in priority_path.iterdir():
                                if len(header_files) >= max_header_files:
                                    break
                                if time.time() - start_time > max_scan_time:
                                    break
                                if subdir.is_dir():
                                    for header_file in subdir.glob('*.h'):
                                        if len(header_files) >= max_header_files:
                                            break
                                        header_files.append(str(header_file.resolve()))
                                        self.logger.debug(f"找到头文件: {subdir.name}/{header_file.name}")
                        except Exception as e:
                            self.logger.debug(f"扫描优先目录 {priority_path} 时出错: {e}")
                
                # 如果还没达到限制且时间允许，快速扫描根目录
                if len(header_files) < max_header_files and time.time() - start_time < max_scan_time:
                    self.logger.info("快速扫描根目录...")
                    try:
                        # 只扫描根目录的直接头文件
                        for header_file in scan_path.glob('*.h'):
                            if len(header_files) >= max_header_files:
                                break
                            header_files.append(str(header_file.resolve()))
                            self.logger.debug(f"找到根目录头文件: {header_file.name}")
                    except Exception as e:
                        self.logger.debug(f"扫描根目录时出错: {e}")
                
                elapsed_time = time.time() - start_time
                self.logger.info(f"头文件扫描完成，找到 {len(header_files)} 个头文件，耗时 {elapsed_time:.2f}s")
                
                if elapsed_time >= max_scan_time:
                    self.logger.warning(f"头文件扫描达到时间限制，可能未扫描完所有文件")
                    
            except Exception as e:
                self.logger.warning(f"扫描目标目录 '{scan_directory}' 时出错: {e}")
        else:
            self.logger.warning(f"扫描目录不存在: {scan_directory}")
            
        self.logger.info(f"头文件扫描完成，找到 {len(header_files)} 个头文件")

        # 3. 为每个头文件智能选择最佳的编译参数模板
        import time
        compile_start_time = time.time()
        self.logger.info(f"为 {len(header_files)} 个头文件创建编译命令...")
        
        processed_count = 0
        for header_file in header_files:
            normalized_header_path = str(Path(header_file).resolve()).replace('\\', '/')
            
            # 找到第一个包含该头文件的源文件作为模板
            best_source_file = self._find_first_including_source(header_file, source_files)
            
            if best_source_file and best_source_file in source_file_args_map:
                template_args = source_file_args_map[best_source_file]
                template_cmd_info = compile_commands[best_source_file]
            else:
                # 回退到第一个可用的源文件
                if source_files and source_files[0] in source_file_args_map:
                    template_args = source_file_args_map[source_files[0]]
                    template_cmd_info = compile_commands[source_files[0]]
                else:
                    self.logger.warning(f"无法为头文件 {header_file} 找到合适的编译参数模板")
                    continue
            
            # 创建头文件专用的编译参数
            header_args = self._create_header_compile_args(
                template_args, 
                all_include_dirs, 
                all_macro_definitions,
                header_file
            )
            
            header_compile_commands[normalized_header_path] = {
                "args": header_args,
                "directory": template_cmd_info['directory']
            }
            
            processed_count += 1
            if processed_count % 10 == 0:
                elapsed = time.time() - compile_start_time
                self.logger.info(f"已处理 {processed_count}/{len(header_files)} 个头文件，耗时 {elapsed:.2f}s")
        
        compile_elapsed = time.time() - compile_start_time
        self.logger.info(f"头文件编译命令创建完成，耗时 {compile_elapsed:.2f}s")

        self.console.print(f"-> 发现头文件: {len(header_files)} 个")
        self.console.print(f"-> 创建头文件编译命令: {len(header_compile_commands)} 个")
        
        return header_files, header_compile_commands
    
    def _find_first_including_source(self, header_file: str, source_files: List[str]) -> Optional[str]:
        """找到第一个包含该头文件的源文件（快速版本）"""
        header_path = Path(header_file)
        header_name = header_path.name
        
        # 快速策略1: 查找同名的源文件（如 MyClass.h -> MyClass.cpp）
        header_stem = header_path.stem
        for src_file in source_files:
            src_path = Path(src_file)
            if src_path.stem == header_stem:
                self.logger.debug(f"找到同名源文件 {src_path.name} 作为头文件 {header_name} 的模板")
                return src_file
        
        # 快速策略2: 查找同目录下的源文件
        header_dir = header_path.parent
        for src_file in source_files:
            src_path = Path(src_file)
            if src_path.parent == header_dir:
                self.logger.debug(f"找到同目录源文件 {src_path.name} 作为头文件 {header_name} 的模板")
                return src_file
        
        # 快速策略3: 简单的直接包含检测（只检查前几个源文件的前50行）
        max_sources_to_check = min(5, len(source_files))  # 只检查前5个源文件
        for src_file in source_files[:max_sources_to_check]:
            try:
                with open(src_file, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_num, line in enumerate(f, 1):
                        if line_num > 50:  # 只检查前50行
                            break
                        line = line.strip()
                        if line.startswith('#include') and (f'"{header_name}"' in line or f'<{header_name}>' in line):
                            self.logger.debug(f"头文件 {header_name} 被源文件 {Path(src_file).name} 直接包含")
                            return src_file
            except Exception:
                continue
        
        # 回退策略：返回第一个源文件作为默认模板
        if source_files:
            self.logger.debug(f"使用第一个源文件作为头文件 {header_name} 的模板")
            return source_files[0]
        
        return None
    
    def _find_direct_including_source(self, header_file: str, source_files: List[str]) -> Optional[str]:
        """找到直接包含该头文件的源文件"""
        header_path = Path(header_file)
        header_name = header_path.name
        
        # 生成可能的包含路径变体
        possible_include_patterns = self._generate_include_patterns(header_file)
        
        # 遍历所有源文件，找到第一个包含该头文件的源文件
        for src_file in source_files:
            try:
                with open(src_file, 'r', encoding='utf-8', errors='ignore') as f:
                    # 读取更多行来检查include语句，但设置合理上限
                    for line_num, line in enumerate(f, 1):
                        if line_num > 200:  # 增加检查范围到200行
                            break
                        
                        line = line.strip()
                        if line.startswith('#include'):
                            # 检查所有可能的包含模式
                            for pattern in possible_include_patterns:
                                if pattern in line:
                                    self.logger.debug(f"头文件 {header_name} 被源文件 {Path(src_file).name} 直接包含")
                                    return src_file
                                    
            except Exception as e:
                self.logger.debug(f"检查源文件 {src_file} 时出错: {e}")
                continue
        
        return None
    
    def _find_indirect_including_source(self, header_file: str, source_files: List[str]) -> Optional[str]:
        """找到间接包含该头文件的源文件"""
        header_path = Path(header_file)
        
        # 构建头文件包含图
        include_graph = self._build_include_graph(source_files)
        
        # 在包含图中查找间接包含关系
        for src_file in source_files:
            if self._has_indirect_include(src_file, header_file, include_graph):
                self.logger.debug(f"头文件 {header_path.name} 被源文件 {Path(src_file).name} 间接包含")
                return src_file
        
        return None
    
    def _find_smart_matching_source(self, header_file: str, source_files: List[str]) -> Optional[str]:
        """使用智能匹配策略找到最合适的源文件"""
        header_path = Path(header_file)
        header_stem = header_path.stem  # 不包含扩展名的文件名
        
        # 策略1: 查找同名的源文件（如 MyClass.h -> MyClass.cpp）
        for src_file in source_files:
            src_path = Path(src_file)
            if src_path.stem == header_stem:
                self.logger.debug(f"找到同名源文件 {src_path.name} 作为头文件 {header_path.name} 的模板")
                return src_file
        
        # 策略2: 查找同目录下的源文件
        header_dir = header_path.parent
        for src_file in source_files:
            src_path = Path(src_file)
            if src_path.parent == header_dir:
                self.logger.debug(f"找到同目录源文件 {src_path.name} 作为头文件 {header_path.name} 的模板")
                return src_file
        
        # 策略3: 查找路径相似度最高的源文件
        best_source = None
        best_similarity = 0
        
        for src_file in source_files:
            similarity = self._calculate_path_similarity(header_file, src_file)
            if similarity > best_similarity:
                best_similarity = similarity
                best_source = src_file
        
        if best_source and best_similarity > 0.3:  # 相似度阈值
            self.logger.debug(f"找到路径相似源文件 {Path(best_source).name} 作为头文件 {header_path.name} 的模板（相似度: {best_similarity:.2f}）")
            return best_source
        
        return None
    
    def _generate_include_patterns(self, header_file: str) -> List[str]:
        """生成头文件可能的包含模式"""
        header_path = Path(header_file)
        patterns = []
        
        # 基本文件名模式
        header_name = header_path.name
        patterns.extend([
            f'"{header_name}"',
            f'<{header_name}>',
        ])
        
        # 相对路径模式（从项目根目录开始的不同层级）
        parts = header_path.parts
        for i in range(len(parts)):
            relative_path = '/'.join(parts[i:])
            patterns.extend([
                f'"{relative_path}"',
                f'<{relative_path}>',
            ])
            
            # Windows路径分隔符
            relative_path_win = '\\'.join(parts[i:])
            patterns.extend([
                f'"{relative_path_win}"',
                f'<{relative_path_win}>',
            ])
        
        return list(set(patterns))  # 去重
    
    def _build_include_graph(self, source_files: List[str]) -> Dict[str, Set[str]]:
        """构建文件包含关系图（增强版本，支持头文件和更深层次的分析）"""
        include_graph = {}
        
        # 扩展处理范围，包含更多文件但仍保持合理的性能
        max_files_to_process = min(len(source_files), 200)  # 增加到200个文件
        files_to_process = source_files[:max_files_to_process]
        
        self.logger.debug(f"构建包含图：处理 {len(files_to_process)} 个文件")
        
        # 同时收集所有发现的头文件路径
        discovered_headers = set()
        
        for src_file in files_to_process:
            try:
                includes = set()
                with open(src_file, 'r', encoding='utf-8', errors='ignore') as f:
                    # 增加读取行数，确保捕获更多include语句
                    for line_num, line in enumerate(f, 1):
                        if line_num > 300:  # 增加到300行
                            break
                        
                        line = line.strip()
                        if line.startswith('#include') and not line.startswith('#include_next'):
                            # 提取包含的文件名
                            import re
                            match = re.search(r'#include\s*[<"]([^>"]+)[>"]', line)
                            if match:
                                included_file = match.group(1)
                                includes.add(included_file)
                                
                                # 收集发现的头文件，用于后续处理
                                if included_file.endswith(('.h', '.hpp', '.hxx')):
                                    discovered_headers.add(included_file)
                
                include_graph[src_file] = includes
                
            except Exception as e:
                self.logger.debug(f"构建包含图时处理文件 {src_file} 出错: {e}")
                include_graph[src_file] = set()
        
        # 尝试为发现的头文件也构建包含关系（如果能找到这些头文件的话）
        self._extend_include_graph_with_headers(include_graph, discovered_headers, source_files)
        
        self.logger.debug(f"包含图构建完成：{len(include_graph)} 个文件，发现 {len(discovered_headers)} 个头文件引用")
        
        return include_graph
    
    def _extend_include_graph_with_headers(self, include_graph: Dict[str, Set[str]], 
                                         discovered_headers: Set[str], source_files: List[str]):
        """扩展包含图以包含头文件的包含关系"""
        # 尝试找到实际的头文件路径
        header_file_map = {}
        
        # 从源文件路径推断可能的头文件位置
        for src_file in source_files:
            src_dir = Path(src_file).parent
            
            # 搜索常见的头文件目录
            potential_header_dirs = [
                src_dir,
                src_dir / 'include',
                src_dir / '..' / 'include',
                src_dir / '..' / '..' / 'include',
                src_dir.parent / 'include',
            ]
            
            for header_name in discovered_headers:
                if header_name in header_file_map:
                    continue
                    
                for header_dir in potential_header_dirs:
                    try:
                        if header_dir.exists():
                            # 递归搜索头文件
                            for header_path in header_dir.rglob(header_name):
                                if header_path.is_file():
                                    header_file_map[header_name] = str(header_path.resolve())
                                    break
                    except Exception:
                        continue
                    
                    if header_name in header_file_map:
                        break
        
        # 为找到的头文件构建包含关系
        processed_headers = 0
        max_headers_to_process = 50  # 限制处理的头文件数量以控制性能
        
        for header_name, header_path in header_file_map.items():
            if processed_headers >= max_headers_to_process:
                break
                
            try:
                includes = set()
                with open(header_path, 'r', encoding='utf-8', errors='ignore') as f:
                    # 头文件通常include语句在前面，读取前150行足够
                    for line_num, line in enumerate(f, 1):
                        if line_num > 150:
                            break
                        
                        line = line.strip()
                        if line.startswith('#include') and not line.startswith('#include_next'):
                            import re
                            match = re.search(r'#include\s*[<"]([^>"]+)[>"]', line)
                            if match:
                                included_file = match.group(1)
                                includes.add(included_file)
                
                include_graph[header_path] = includes
                processed_headers += 1
                
            except Exception as e:
                self.logger.debug(f"处理头文件 {header_path} 时出错: {e}")
        
        self.logger.debug(f"扩展包含图：处理了 {processed_headers} 个头文件")
    
    def _has_indirect_include(self, source_file: str, target_header: str, include_graph: Dict[str, Set[str]]) -> bool:
        """检查源文件是否间接包含目标头文件（支持递归检查和循环依赖检测）"""
        target_path = Path(target_header)
        target_name = target_path.name
        
        # 使用深度优先搜索检查间接包含关系
        visited = set()  # 防止循环依赖
        return self._dfs_include_search(source_file, target_header, target_name, include_graph, visited, max_depth=5)
    
    def _dfs_include_search(self, current_file: str, target_header: str, target_name: str, 
                           include_graph: Dict[str, Set[str]], visited: Set[str], max_depth: int) -> bool:
        """深度优先搜索包含关系"""
        if max_depth <= 0 or current_file in visited:
            return False
        
        visited.add(current_file)
        
        # 获取当前文件直接包含的文件
        direct_includes = include_graph.get(current_file, set())
        
        # 检查直接包含的文件中是否有目标头文件
        for included_file in direct_includes:
            if self._is_target_header_match(included_file, target_header, target_name):
                return True
        
        # 递归检查间接包含
        for included_file in direct_includes:
            # 尝试将included_file解析为完整路径，以便在include_graph中查找
            resolved_included_file = self._resolve_include_path(included_file, current_file, include_graph)
            if resolved_included_file and self._dfs_include_search(
                resolved_included_file, target_header, target_name, include_graph, visited.copy(), max_depth - 1
            ):
                return True
        
        return False
    
    def _is_target_header_match(self, included_file: str, target_header: str, target_name: str) -> bool:
        """检查包含的文件是否匹配目标头文件（精确匹配版本）"""
        # 1. 精确文件名匹配 - 但要确保是完全匹配
        if included_file == target_name:
            return True
        
        # 2. 精确路径匹配
        if included_file == target_header:
            return True
        
        # 3. 规范化路径匹配
        try:
            target_path = Path(target_header)
            
            # 如果included_file是相对路径，尝试不同的匹配方式
            if not Path(included_file).is_absolute():
                # 精确文件名匹配
                if target_path.name == included_file:
                    return True
                
                # 检查是否是目标文件的相对路径表示
                if target_header.endswith(included_file):
                    # 确保是路径分隔符边界，而不是部分匹配
                    prefix = target_header[:-len(included_file)]
                    if prefix.endswith('/') or prefix.endswith('\\') or prefix == '':
                        return True
            else:
                # 绝对路径的精确匹配
                try:
                    included_path = Path(included_file).resolve()
                    target_path_resolved = target_path.resolve()
                    if included_path == target_path_resolved:
                        return True
                except Exception:
                    pass
        except Exception:
            pass
        
        return False
    
    def _resolve_include_path(self, included_file: str, current_file: str, include_graph: Dict[str, Set[str]]) -> Optional[str]:
        """尝试将相对包含路径解析为include_graph中的完整路径"""
        # 1. 直接在include_graph中查找
        if included_file in include_graph:
            return included_file
        
        # 2. 查找以included_file结尾的路径
        for file_path in include_graph.keys():
            if file_path.endswith(included_file) or included_file in file_path:
                return file_path
        
        # 3. 基于当前文件目录解析相对路径
        try:
            current_dir = Path(current_file).parent
            potential_path = (current_dir / included_file).resolve()
            potential_path_str = str(potential_path)
            
            # 查找匹配的路径
            for file_path in include_graph.keys():
                if potential_path_str == file_path or file_path.endswith(str(potential_path.name)):
                    return file_path
        except Exception:
            pass
        
        return None
    
    def _calculate_path_similarity(self, path1: str, path2: str) -> float:
        """计算两个路径的相似度"""
        parts1 = Path(path1).parts
        parts2 = Path(path2).parts
        
        # 计算公共路径段的数量
        common_parts = 0
        min_len = min(len(parts1), len(parts2))
        
        for i in range(min_len):
            if parts1[-(i+1)] == parts2[-(i+1)]:  # 从末尾开始比较
                common_parts += 1
            else:
                break
        
        # 相似度 = 公共部分 / 最大长度
        max_len = max(len(parts1), len(parts2))
        return common_parts / max_len if max_len > 0 else 0
    
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
            
            # 特别处理强制包含参数 - 移除有问题的强制包含文件
            if arg == '-include' and i + 1 < len(template_args):
                include_file = template_args[i + 1]
                if self._is_problematic_include_file(include_file):
                    self.logger.info(f"为头文件 {Path(header_file).name} 移除有问题的强制包含文件: {include_file}")
                    # 跳过 -include 和文件名
                    i += 2
                    continue
                else:
                    # 保留这个强制包含
                    header_args.append(arg)
                    if i + 1 < len(template_args):
                        header_args.append(template_args[i + 1])
                    i += 2
                    continue
            
            # 跳过复杂的clang参数序列
            if (arg == '-Xclang' and i + 3 < len(template_args) and 
                template_args[i+1:i+4] in [['-x', '-Xclang', 'c++'], ['-x', '-Xclang', '"c++"']]):
                i += 4
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
        
        # 4. 添加UE引擎核心包含路径
        ue_engine_includes = [
            # 核心运行时模块
            'N:/c7_enginedev/Engine/Source/Runtime/CoreUObject/Public',
            'N:/c7_enginedev/Engine/Source/Runtime/Core/Public',
            'N:/c7_enginedev/Engine/Source/Runtime/Engine/Public',
            'N:/c7_enginedev/Engine/Source/Runtime/Engine/Classes',
            'N:/c7_enginedev/Engine/Source/Runtime/CoreUObject/Classes',
            'N:/c7_enginedev/Engine/Source/Runtime/Core/Classes',
            
            # 编辑器相关模块
            'N:/c7_enginedev/Engine/Source/Editor/UnrealEd/Public',
            'N:/c7_enginedev/Engine/Source/Editor/UnrealEd/Classes',
            'N:/c7_enginedev/Engine/Source/Editor/ComponentVisualizers/Public',
            'N:/c7_enginedev/Engine/Source/Editor/ToolMenus/Public',
            'N:/c7_enginedev/Engine/Source/Editor/EditorStyle/Public',
            'N:/c7_enginedev/Engine/Source/Editor/EditorWidgets/Public',
            
            # 开发者设置和工具
            'N:/c7_enginedev/Engine/Source/Developer/Settings/Public',
            'N:/c7_enginedev/Engine/Source/Developer/DeveloperSettings/Public',
            'N:/c7_enginedev/Engine/Source/Developer/TargetPlatform/Public',
            'N:/c7_enginedev/Engine/Source/Programs/UnrealHeaderTool/Public',
            
            # 其他重要模块
            'N:/c7_enginedev/Engine/Source/Runtime/TraceLog/Public',
            'N:/c7_enginedev/Engine/Source/Runtime/Slate/Public',
            'N:/c7_enginedev/Engine/Source/Runtime/SlateCore/Public',
            'N:/c7_enginedev/Engine/Source/Runtime/ApplicationCore/Public'
        ]
        
        for ue_include in ue_engine_includes:
            if os.path.exists(ue_include):
                include_arg = f'-I{ue_include}'
                if include_arg not in header_args:
                    header_args.append(include_arg)
                    self.logger.debug(f"添加UE引擎包含路径: {ue_include}")
        
        # 5. 添加头文件特定的参数
        header_specific_args = [
            '-x', 'c++-header',  # 指定为C++头文件
            '-Wno-pragma-once-outside-header',
            '-Wno-include-next-outside-header'
        ]
        
        for arg in header_specific_args:
            if arg not in header_args:
                header_args.append(arg)
        
        
        
        self.logger.debug(f"为头文件 {Path(header_file).name} 创建了 {len(header_args)} 个编译参数")
        return header_args
    
    def _is_problematic_include_file(self, include_file: str) -> bool:
        """
        检查是否是已知有问题的强制包含文件 - 修复版本
        """
        import re
        
        # 只检查明确已知有问题的文件模式，避免过于激进的判断
        problematic_patterns = [
            'PCH.GMESDK.h',  # 特定的有问题的PCH文件
        ]
        
        filename = os.path.basename(include_file)
        
        # 精确匹配有问题的文件名，而不是模糊匹配
        for pattern in problematic_patterns:
            if filename == pattern:
                self.logger.debug(f"发现已知有问题的强制包含文件: {filename}")
                return True
        
        # 检查文件是否存在
        try:
            if os.path.isabs(include_file):
                include_path = Path(include_file)
            else:
                include_path = Path(os.getcwd()) / include_file
            
            if not include_path.exists():
                # 文件不存在才认为是有问题的
                self.logger.debug(f"强制包含文件不存在: {include_file}")
                return True
            
            # 对于存在的文件，只有在包含了无法解析的相对路径时才认为有问题
            # 但是要更加谨慎，避免将正常的UE文件标记为问题文件
            with open(include_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(2048)  # 只读取前2KB内容
                
                # 检查是否包含已知会导致解析失败的特定模式
                if 'PCH.GMESDK.h' in content and '../' in content:
                    # 只有当文件同时包含PCH.GMESDK.h引用和相对路径时才认为有问题
                    self.logger.debug(f"发现包含PCH.GMESDK.h和相对路径的强制包含文件: {include_file}")
                    return True
                
        except Exception as e:
            self.logger.debug(f"检查强制包含文件 {include_file} 时出错: {e}")
            # 出错时不再保守地认为有问题，而是允许继续处理
            return False
        
        # 默认认为文件是正常的，不是问题文件
        return False

    def _print_performance_report(self, stats: Dict[str, Any], total_time: float):
        """打印详细的性能报告"""
        self.console.print("\n[bold cyan]📊 性能分析报告[/bold cyan]")
        self.console.print("=" * 60)
        
        # 基本统计
        self.console.print(f"📁 处理文件数: {stats['successful_processed_files']}/{stats['total_files_to_process']}")
        self.console.print(f"📄 源文件: {stats['total_files_in_compile_commands']}, 头文件: {stats['total_header_files_added']}")
        self.console.print(f"🔍 发现实体: 函数 {stats['total_functions']}, 类 {stats['total_classes']}, 命名空间 {stats['total_namespaces']}")
        self.console.print(f"⏱️  总耗时: {total_time:.2f} 秒")
        
        # 性能指标
        files_per_sec = stats['successful_processed_files'] / total_time if total_time > 0 else 0
        self.console.print(f"🚀 处理速度: {files_per_sec:.2f} 文件/秒")
        
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
        
        # 性能建议
        if total_time > 60:
            self.console.print("\n[bold yellow]💡 性能优化建议:[/bold yellow]")
            if stats['total_files_to_process'] > 100:
                self.console.print("  • 考虑使用更多并行进程")
            self.console.print("  • 检查是否有大型头文件导致解析缓慢")
            self.console.print("  • 考虑启用更激进的解析优化选项")

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
                        # 新的是定义，旧的是声明，用新的覆盖旧的，但保留旧的声明位置
                        new_func.declaration_locations.extend(existing_func.declaration_locations)
                        merged_functions[usr] = new_func
                    elif new_func.is_definition and existing_func.is_definition:
                        # 两个都是定义（例如，头文件中的inline函数），合并信息
                        existing_func.declaration_locations.extend(new_func.declaration_locations)
                        existing_func.calls_to = list(set(existing_func.calls_to + new_func.calls_to))
                        # 简单的合并，未来可以优化为更智能的合并策略
                        existing_func.call_details.extend(new_func.call_details)
                    else: # new_func是声明
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
                            # 合并继承信息 - 使用inheritance_list而不是base_classes
                            existing_inheritance = existing_class.cpp_oop_extensions.inheritance_list or []
                            new_inheritance = new_class.cpp_oop_extensions.inheritance_list or []
                            
                            # 基于base_class_usr_id去重合并
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