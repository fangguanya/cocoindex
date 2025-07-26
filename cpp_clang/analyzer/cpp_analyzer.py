"""
C++ Analyzer Main Module (v2.3)

Orchestrates the complete C++ code analysis process by coordinating
file scanning, clang parsing, entity extraction (v2.3), and JSON export (v2.3).
"""

import time
import traceback
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


@dataclass
class AnalysisConfig:
    """分析配置类"""
    project_root: str
    scan_directory: str
    output_path: str = "cpp_analysis_result.json"
    compile_commands_path: Optional[str] = None
    max_files: Optional[int] = None
    verbose: bool = False
    include_extensions: set = field(default_factory=lambda: {'.h', '.hpp', '.cpp', '.cc', '.cxx', '.c'})
    exclude_patterns: set = field(default_factory=set)


@dataclass
class AnalysisResult:
    """分析结果"""
    success: bool
    statistics: Dict[str, Any]
    output_path: Optional[str] = None


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
            parser = ClangParser(console=self.console)
            if not config.compile_commands_path or not Path(config.compile_commands_path).exists():
                msg = f"必须提供有效的 compile_commands.json 路径。"
                self.logger.error(msg)
                return self._create_failure_result("Configuration", msg)
            
            parser.load_compile_commands(config.compile_commands_path)
            files_to_parse = list(parser.compile_commands.keys())

            if not files_to_parse:
                return self._create_failure_result("Parsing", "compile_commands.json 中未找到任何文件记录。")
            
            self.console.print(f"-> 找到 {len(files_to_parse)} 个待分析文件。")
            self.logger.info(f"找到 {len(files_to_parse)} 个待分析文件。")

            # 2. Clang解析
            self.console.print("\n[bold]2. 使用 Clang 解析文件...[/bold]")
            parsed_files = self._parse_files(parser, files_to_parse, config)
            successful_parses = [p for p in parsed_files if p.success]
            if not successful_parses:
                 return self._create_failure_result("Parsing", "未能成功解析任何文件。")
            self.console.print(f"-> 成功解析 {len(successful_parses)} / {len(parsed_files)} 个文件。")
            self.logger.info(f"成功解析 {len(successful_parses)} / {len(parsed_files)} 个文件。")
            # 3. 实体提取 (新版)
            self.console.print("\n[bold]3. 提取代码实体 (v2.3)...[/bold]")
            extractor = EntityExtractor(config.project_root)
            extracted_data = extractor.extract_from_files(successful_parses, config)
            self.console.print("-> 实体提取完成。")
            self.logger.info("3. 实体提取完成。")
            # 4. JSON导出 (新版)
            self.console.print("\n[bold]4. 导出为 JSON (v2.3)...[/bold]")
            exporter = JsonExporter()
            export_success = exporter.export(extracted_data, config.output_path)
            if not export_success:
                return self._create_failure_result("Exporting", "JSON 导出过程失败。")
            self.console.print(f"-> 分析结果已保存到: {config.output_path}")
            self.logger.info(f"分析结果已保存到: {config.output_path}")
            # 5. 完成
            statistics = self._generate_statistics(
                total_files=len(files_to_parse),
                parsed_files=parsed_files,
                extracted_data=extracted_data,
                start_time=start_time
            )
            self.console.print("\n[bold green]✓ 分析成功完成！[/bold green]")
            self._display_statistics(statistics)
            self.logger.info("分析成功完成！")
            return AnalysisResult(success=True, statistics=statistics, output_path=config.output_path)
        
        except Exception as e:
            error_msg = f"分析过程中发生严重错误: {e}"
            self.logger.error(error_msg)
            self.logger.error(traceback.format_exc())
            return self._create_failure_result("Unhandled Exception", str(e))

    def _parse_files(self, parser: ClangParser, file_paths: List[str], config: AnalysisConfig) -> List[ParsedFile]:
        """解析文件并显示进度"""
        if config.max_files:
            file_paths = file_paths[:config.max_files]
        
        with Progress(console=self.console) as progress:
            task = progress.add_task("[cyan]解析中...", total=len(file_paths))
            # 使用正确的 parse_files 方法
            parsed_files = parser.parse_files(file_paths, progress, task)
        return parsed_files
    
    def _create_failure_result(self, stage: str, reason: str) -> AnalysisResult:
        """创建失败结果的辅助函数"""
        self.logger.error(f"分析在 [{stage}] 阶段失败: {reason}")
        self.console.print(f"[bold red]✗ 分析失败于 {stage} 阶段: {reason}[/bold red]")
        return AnalysisResult(success=False, statistics={"stage": stage, "reason": reason})

    def _generate_statistics(self, total_files: int, parsed_files: List[ParsedFile], extracted_data: Dict[str, Any], start_time: float) -> Dict[str, Any]:
        """生成最终的统计信息"""
        total_time = time.time() - start_time
        successful_parses = sum(1 for p in parsed_files if p.success)
        return {
            "total_files_in_compile_commands": total_files,
            "total_parsed_files": len(parsed_files),
            "successful_parsed_files": successful_parses,
            "failed_parsed_files": len(parsed_files) - successful_parses,
            "total_functions": len(extracted_data.get('functions', {})),
            "total_classes": len(extracted_data.get('classes', {})),
            "total_namespaces": len(extracted_data.get('namespaces', {})),
            "analysis_time_sec": round(total_time, 2),
        }

    def _display_statistics(self, stats: Dict[str, Any]):
        """在控制台打印统计信息"""
        self.console.print("\n[bold]--- 分析统计 ---[/bold]")
        self.console.print(f"源文件数 (from compile_commands): {stats['total_files_in_compile_commands']}")
        self.console.print(f"成功解析数: {stats['successful_parsed_files']} / {stats['total_parsed_files']}")
        self.console.print(f"提取函数数: {stats['total_functions']}")
        self.console.print(f"提取类/结构体数: {stats['total_classes']}")
        self.console.print(f"分析总用时: {stats['analysis_time_sec']} 秒")
        self.console.print("[bold]------------------[/bold]")
    