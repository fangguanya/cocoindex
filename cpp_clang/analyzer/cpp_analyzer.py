"""
C++ Analyzer Main Module (v2.3)

Orchestrates the complete C++ code analysis process by coordinating
file scanning, clang parsing, entity extraction (v2.3), and JSON export (v2.3).
"""

import time
import traceback
import multiprocessing
import platform
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from rich.console import Console
from rich.progress import Progress

from .logger import get_logger
from .file_scanner import FileScanner, ScanResult
from .clang_parser import ClangParser, ParsedFile
from .entity_extractor import EntityExtractor
from .json_exporter import JsonExporter

# Windows平台需要设置multiprocessing启动方法
if platform.system() == 'Windows':
    multiprocessing.set_start_method('spawn', force=True)


@dataclass
class AnalysisConfig:
    """分析配置类"""
    project_root: str
    scan_directory: str
    output_path: str = "cpp_analysis_result.json"
    compile_commands_path: Optional[str] = None
    max_files: Optional[int] = None
    verbose: bool = False
    num_jobs: int = 0  # 0 表示自动确定
    include_extensions: set = field(default_factory=lambda: {'.h', '.hpp', '.cpp', '.cc', '.cxx', '.c'})
    exclude_patterns: set = field(default_factory=set)


@dataclass
class AnalysisResult:
    """分析结果类"""
    success: bool
    output_path: Optional[str] = None
    statistics: Dict[str, Any] = field(default_factory=dict)

# 全局 ClangParser 实例，用于多进程初始化
g_parser: Optional[ClangParser] = None

def _init_worker(compile_commands_path: str):
    """初始化工作进程"""
    global g_parser
    try:
        print(f"DEBUG: Initializing worker process with compile_commands: {compile_commands_path}")
        
        # 不传递Console对象到子进程，避免多进程冲突，关闭详细输出
        g_parser = ClangParser(console=None, verbose=False)
        print(f"DEBUG: Created ClangParser instance")
        
        g_parser.load_compile_commands(compile_commands_path)
        print(f"DEBUG: Loaded compile commands, found {len(g_parser.compile_commands)} entries")
        
        print(f"DEBUG: Worker initialization completed successfully")
        
    except Exception as e:
        print(f"ERROR: Failed to initialize worker: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        g_parser = None

def _parse_worker(file_path: str) -> Optional[ParsedFile]:
    """并行解析单个文件的工作函数"""
    global g_parser
    
    # 添加详细的调试信息
    try:
        if not g_parser:
            print(f"ERROR: g_parser is None for file {file_path}")
            return None
            
        print(f"DEBUG: Attempting to parse {file_path}")
        
        # 检查compile_commands是否已加载
        if not hasattr(g_parser, 'compile_commands') or not g_parser.compile_commands:
            print(f"ERROR: compile_commands not loaded in worker for {file_path}")
            return None
            
        # 检查文件是否在compile_commands中
        if file_path not in g_parser.compile_commands:
            print(f"WARNING: {file_path} not found in compile_commands")
            return None
            
        print(f"DEBUG: Found compile commands for {file_path}, calling parse_file...")
        result = g_parser.parse_file(file_path)
        
        if result:
            print(f"SUCCESS: Parsed {file_path}")
        else:
            print(f"FAILED: parse_file returned None for {file_path}")
            
        return result
        
    except Exception as e:
        print(f"EXCEPTION in _parse_worker for {file_path}: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return None

class CppAnalyzer:
    """C++代码分析器 (v2.3)"""
    
    def __init__(self, console: Optional[Console] = None):
        """初始化分析器"""
        self.console = console or Console()
        self.logger = get_logger()
    
    def analyze(self, config: AnalysisConfig) -> AnalysisResult:
        """
        执行完整的C++代码分析 (v2.3 流程)
        """
        start_time = time.time()
        self.console.print("[bold green]--- 开始 C++ 项目分析 (v2.3) ---[/bold green]")
        self.logger.info("开始 C++ 项目分析 (v2.3)")
        
        try:
            # 1. 加载 compile_commands.json 作为分析的权威来源
            self.console.print("\n[bold]1. 加载 compile_commands.json...[/bold]")
            self.logger.info("1. 加载 compile_commands.json...")
            
            if not config.compile_commands_path or not Path(config.compile_commands_path).exists():
                msg = f"必须提供有效的 compile_commands.json 路径。"
                self.logger.error(msg)
                return self._create_failure_result("Configuration", msg)
            
            # 临时解析器，仅用于获取文件列表
            temp_parser = ClangParser(verbose=config.verbose)
            temp_parser.load_compile_commands(config.compile_commands_path)
            files_to_parse = list(temp_parser.compile_commands.keys())

            if not files_to_parse:
                return self._create_failure_result("Parsing", "compile_commands.json 中未找到任何文件记录。")
            
            self.console.print(f"-> 找到 {len(files_to_parse)} 个待分析文件。")
            self.logger.info(f"找到 {len(files_to_parse)} 个待分析文件。")

            # 1.5. 使用 FileScanner 过滤文件列表
            self.console.print("\n[bold]1.5. 应用文件过滤规则...[/bold]")
            self.logger.info("1.5. 应用文件过滤规则...")
            file_scanner = FileScanner()
            filtered_files = file_scanner.filter_files_from_list(files_to_parse, config.scan_directory)
            
            if config.max_files is not None and config.max_files > 0:
                filtered_files = filtered_files[:config.max_files]

            self.console.print(f"-> 过滤后剩余 {len(filtered_files)} 个文件待解析。")
            self.logger.info(f"过滤后剩余 {len(filtered_files)} 个文件待解析。")

            # 2. 并行解析文件
            self.console.print("\n[bold]2. 开始并行解析文件...[/bold]")
            self.logger.info("2. 开始并行解析文件...")
            
            parsed_files: List[ParsedFile] = []
            num_jobs = config.num_jobs if config.num_jobs > 0 else multiprocessing.cpu_count()
            
            self.console.print(f"-> 使用 {num_jobs} 个并行进程")
            self.logger.info(f"使用 {num_jobs} 个并行进程")
            
            try:
                with Progress(console=self.console) as progress:
                    task = progress.add_task("[cyan]解析中...", total=len(filtered_files))
                    
                    # 使用 multiprocessing.Pool 实现并行处理
                    # initializer 用于为每个工作进程设置全局解析器实例
                    with multiprocessing.Pool(
                        processes=num_jobs, 
                        initializer=_init_worker, 
                        initargs=(config.compile_commands_path,)
                    ) as pool:
                        # 使用 imap_unordered 以便在任务完成时立即获得结果
                        try:
                            for result in pool.imap_unordered(_parse_worker, filtered_files):
                                if result:
                                    parsed_files.append(result)
                                progress.update(task, advance=1)
                        except KeyboardInterrupt:
                            self.console.print("\n[yellow]用户中断，正在终止进程池...[/yellow]")
                            pool.terminate()
                            pool.join()
                            raise
                        except Exception as e:
                            self.console.print(f"\n[red]并行处理过程中发生错误: {e}[/red]")
                            pool.terminate()
                            pool.join()
                            raise

            except KeyboardInterrupt:
                return self._create_failure_result("Parsing", "用户中断了分析过程")
            except Exception as e:
                return self._create_failure_result("Parsing", f"并行解析失败: {str(e)}")

            self.console.print(f"-> 成功解析 {len(parsed_files)} / {len(filtered_files)} 个文件。")
            self.logger.info(f"成功解析 {len(parsed_files)} / {len(filtered_files)} 个文件。")
            
            # 检查是否有足够的解析结果
            if not parsed_files:
                return self._create_failure_result("Parsing", "没有成功解析任何文件")
            
            success_rate = len(parsed_files) / len(filtered_files)
            if success_rate < 0.1:  # 如果成功率低于10%，可能存在严重问题
                self.logger.warning(f"解析成功率较低: {success_rate:.1%}")
                self.console.print(f"[yellow]警告: 解析成功率较低 ({success_rate:.1%})，请检查编译配置[/yellow]")

            # 3. 提取实体
            self.console.print("\n[bold]3. 提取代码实体 (函数、类等)...[/bold]")
            self.logger.info("3. 提取代码实体...")
            extractor = EntityExtractor(config.project_root)
            extracted_data = extractor.extract_from_files(parsed_files, config)
            self.console.print(f"-> 提取完成。")

            # 4. 导出为 JSON
            self.console.print("\n[bold]4. 导出为 JSON...[/bold]")
            self.logger.info("4. 导出为 JSON...")
            exporter = JsonExporter()
            export_success = exporter.export(extracted_data, config.output_path)
            
            if not export_success:
                return self._create_failure_result("Export", "JSON导出失败")

            end_time = time.time()
            
            # 准备统计数据
            stats = {
                "total_files_in_compile_commands": len(files_to_parse),
                "total_parsed_files": len(filtered_files),
                "successful_parsed_files": len(parsed_files),
                "total_functions": extracted_data.get("total_functions", 0),
                "total_classes": extracted_data.get("total_classes", 0),
                "analysis_time_sec": end_time - start_time,
            }
            
            self.logger.info("C++ 项目分析成功完成。")
            return AnalysisResult(success=True, output_path=config.output_path, statistics=stats)

        except Exception as e:
            self.logger.error(f"分析过程中发生严重错误: {e}\n{traceback.format_exc()}")
            return self._create_failure_result("Exception", str(e))

    def _create_failure_result(self, stage: str, reason: str) -> AnalysisResult:
        """创建一个表示失败的分析结果"""
        return AnalysisResult(success=False, statistics={"stage": stage, "reason": reason})
    