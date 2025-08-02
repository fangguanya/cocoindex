"""
C++ Analyzer Main Module (v2.3) - 修复版本

修复了头文件编译命令创建的问题，正确处理command字段而不是args字段
"""

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
        if not g_parser or not g_extractor or not g_compile_commands:
            import logging
            logging.error(f"Worker not initialized properly for file {file_path}")
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
                logging.error(f"No compile info found for {file_path}")
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
        logging.error(f"EXCEPTION in _parse_and_extract_worker for {file_path}: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
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
        """扫描头文件并为其创建编译命令 - 修复版本"""
        header_files = []
        header_compile_commands = {}
        include_dirs = set()

        # 1. 从编译命令中提取include目录 - 修复：正确处理command字段
        for cmd_info in compile_commands.values():
            directory = Path(cmd_info['directory'])
            
            # 关键修复：正确处理command字段而不是args字段
            args = cmd_info.get('args', [])
            if not args and 'command' in cmd_info:
                # 如果没有args字段，从command字段解析参数
                try:
                    # 使用shlex.split来正确解析命令行，处理引号和转义
                    command_parts = shlex.split(cmd_info['command'])
                    args = command_parts[1:]  # 跳过编译器路径
                except Exception as e:
                    self.logger.warning(f"解析编译命令失败: {e}")
                    continue
            
            for arg in args:
                # 跳过响应文件引用（关键修复）
                if arg.startswith('@'):
                    continue
                
                if arg.startswith('-I'):
                    path_str = arg[2:].strip()
                    if not path_str: 
                        continue
                    
                    include_path = Path(path_str)
                    if not include_path.is_absolute():
                        include_path = (directory / include_path).resolve()
                    else:
                        include_path = include_path.resolve()
                    
                    # 确保只添加项目内的头文件目录
                    try:
                        include_path.relative_to(project_root)
                        include_dirs.add(str(include_path))
                    except ValueError:
                        continue

        # 2. 直接扫描目标目录中的头文件（重要修复）
        scan_path = Path(scan_directory)
        if scan_path.exists():
            try:
                for p in scan_path.rglob('*'):
                    if p.is_file() and p.suffix in ['.h', '.hpp']:
                        header_path = str(p.resolve())
                        header_files.append(header_path)
                        self.logger.info(f"发现头文件: {header_path}")
            except Exception as e:
                self.logger.warning(f"扫描目标目录 '{scan_directory}' 时出错: {e}")

        # 3. 扫描include目录中的头文件（作为补充）
        for inc_dir in include_dirs:
            try:
                for p in Path(inc_dir).rglob('*'):
                    if p.is_file() and p.suffix in ['.h', '.hpp']:
                        header_path = str(p.resolve())
                        # 只包含scan_directory下的头文件
                        try:
                            Path(header_path).relative_to(scan_directory)
                            if header_path not in header_files:  # 避免重复
                                header_files.append(header_path)
                        except ValueError:
                            continue
            except Exception as e:
                self.logger.warning(f"扫描头文件目录 '{inc_dir}' 时出错: {e}")

        # 4. 为头文件创建编译命令 - 修复：正确处理command字段
        # 选择一个代表性的源文件作为模板
        template_source = None
        template_cmd_info = None
        
        # 优先选择scan_directory下的源文件作为模板
        for src_file in source_files:
            try:
                Path(src_file).relative_to(scan_directory)
                template_source = src_file
                template_cmd_info = compile_commands[src_file]
                break
            except ValueError:
                continue
        
        # 如果没有找到scan_directory下的源文件，使用第一个源文件作为模板
        if not template_source and source_files:
            template_source = source_files[0]
            template_cmd_info = compile_commands[template_source]

        if template_cmd_info:
            # 获取模板的编译参数
            template_args = template_cmd_info.get('args', [])
            if not template_args and 'command' in template_cmd_info:
                try:
                    command_parts = shlex.split(template_cmd_info['command'])
                    template_args = command_parts[1:]  # 跳过编译器路径
                except Exception as e:
                    self.logger.warning(f"解析模板编译命令失败: {e}")
                    template_args = []
            
            # 为每个头文件创建编译命令
            for header_file in header_files:
                normalized_header_path = str(Path(header_file).resolve()).replace('\\', '/')
                
                # 复制模板的编译参数，但移除一些不适用于头文件的参数
                header_args = []
                skip_next = False
                
                i = 0
                while i < len(template_args):
                    arg = template_args[i]
                    
                    # 跳过响应文件引用（关键修复）
                    if arg.startswith('@'):
                        i += 1
                        continue
                    
                    # 检查并跳过-Xclang -x -Xclang c++序列
                    if (arg == '-Xclang' and i + 3 < len(template_args) and 
                        template_args[i+1:i+4] == ['-x', '-Xclang', 'c++']):
                        # 跳过整个-Xclang -x -Xclang c++序列
                        print(f"跳过-Xclang序列: {template_args[i:i+4]}")
                        i += 4
                        continue
                    elif (arg == '-Xclang' and i + 3 < len(template_args) and 
                          template_args[i+1:i+4] == ['-x', '-Xclang', '"c++"']):
                        # 跳过整个-Xclang -x -Xclang "c++"序列（带引号版本）
                        print(f"跳过-Xclang序列（带引号）: {template_args[i:i+4]}")
                        i += 4
                        continue
                    
                    # 跳过PCH相关参数（可能导致解析失败）
                    if arg in ['-include'] and i + 1 < len(template_args):
                        next_arg = template_args[i + 1]
                        if 'PCH.' in next_arg or 'Definitions.h' in next_arg:
                            i += 2  # 跳过-include和下一个参数
                            continue
                    
                    # 跳过输出相关参数
                    if arg in ['-o', '-c', '/c', '/Fo'] and i + 1 < len(template_args):
                        i += 2  # 跳过参数和值
                        continue
                    
                    # 跳过源文件特定的参数
                    if arg.endswith(('.cpp', '.cc', '.cxx', '.c')):
                        i += 1
                        continue
                    
                    # 跳过有问题的参数格式
                    if arg in ['"c++"', 'c++']:
                        # 检查是否是-x c++序列的一部分
                        if i >= 1 and template_args[i-1] in ['-x']:
                            # 这是-x c++序列，跳过
                            i += 1
                            continue
                        else:
                            # 这是一个独立的c++参数，也跳过
                            i += 1
                            continue
                    
                    # 跳过可能有问题的UE特定参数
                    if 'PCH.' in arg or 'Definitions.h' in arg:
                        i += 1
                        continue
                    
                    header_args.append(arg)
                    i += 1
                
                # 确保包含必要的include路径
                if not any(arg.startswith('-I') for arg in header_args):
                    # 从现有的include目录中添加基本路径，避免硬编码
                    if include_dirs:
                        # 使用已发现的include目录
                        for inc_dir in sorted(include_dirs)[:4]:  # 限制数量避免过多参数
                            header_args.append(f'-I{inc_dir}')
                    else:
                        # 如果没有发现include目录，添加相对于项目根目录的基本路径
                        project_path = Path(project_root)
                        basic_includes = [
                            project_path / "Source",
                            project_path / "Public", 
                            project_path / "Private"
                        ]
                        for inc_path in basic_includes:
                            if inc_path.exists():
                                header_args.append(f'-I{inc_path}')
                
                # 添加头文件特定的参数（避免重复）
                additional_args = [
                    '-x', 'c++',  # 指定为C++文件
                    '-DPLATFORM_WINDOWS=1',
                    '-D_WIN64=1'
                ]
                
                # 只添加不存在的参数，避免重复
                for arg in additional_args:
                    if arg not in header_args:
                        header_args.append(arg)
                
                header_compile_commands[normalized_header_path] = {
                    "args": header_args,
                    "directory": template_cmd_info['directory']
                }

        self.console.print(f"-> 发现头文件: {len(header_files)} 个")
        self.console.print(f"-> 创建头文件编译命令: {len(header_compile_commands)} 个")
        
        return header_files, header_compile_commands

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