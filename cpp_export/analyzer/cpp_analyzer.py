"""
C++ Analyzer Main Module

Orchestrates the complete C++ code analysis process by coordinating
file scanning, clang parsing, entity extraction, and JSON export.
Supports dual-path design for project_root and scan_directory.
"""

import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass, field

from rich.console import Console
from rich.progress import track, Progress

from .logger import get_logger
from .file_scanner import FileScanner, ScanResult
from .clang_parser import ClangParser, ParsedFile
from .entity_extractor import EntityExtractor
from .json_exporter import JsonExporter
from .complexity_analyzer import ComplexityAnalyzer

@dataclass
class AnalysisConfig:
    """分析配置类 - 支持双路径设计"""
    project_root: str  # 项目根目录，用于include搜索和文件ID映射
    scan_directory: str  # 实际扫描目录，可以是project_root的子目录
    output_path: str = "analysis_result.json"
    compile_commands_path: Optional[str] = None  # compile_commands.json 的具体路径，如果为None则使用project_root下的
    clang_working_directory: Optional[str] = None  # clang执行的工作目录，如果为None则使用project_root/Engine
    include_extensions: Set[str] = field(default_factory=lambda: {'.h', '.hpp', '.cpp', '.cc', '.cxx', '.c'})
    exclude_patterns: Set[str] = field(default_factory=lambda: {
        '*/Intermediate/*',  # UE中间文件
        '*/Binaries/*',      # UE二进制文件
        '*/.vs/*',           # Visual Studio
        '*/.git/*',          # Git目录
        '*/DerivedDataCache/*',  # UE缓存
        '*/Saved/*',         # UE保存文件
    })
    use_compile_commands: bool = True  # 是否使用compile_commands.json
    generate_compile_commands: bool = True  # 是否自动生成compile_commands.json
    max_files: Optional[int] = None
    verbose: bool = False

@dataclass
class AnalysisResult:
    """分析结果"""
    success: bool
    extracted_entities: Dict[str, Any]
    file_mappings: Dict[str, str]
    parsed_files: List[ParsedFile]
    config: AnalysisConfig
    statistics: Dict[str, Any]
    json_data: Optional[Dict[str, Any]] = None

class CppAnalyzer:
    """C++代码分析器 - 支持双路径设计和compile_commands.json"""
    
    def __init__(self, console: Optional[Console] = None):
        """初始化分析器"""
        self.console = console or Console()
        self.file_scanner = FileScanner()
        self.clang_parser = ClangParser(console=self.console)
        self.entity_extractor = EntityExtractor()
        self.json_exporter = JsonExporter()
        self.complexity_analyzer = ComplexityAnalyzer()
    
    def analyze(self, project_root: str, scan_directory: Optional[str] = None, 
                compile_commands_path: Optional[str] = None, 
                clang_working_directory: Optional[str] = None, **kwargs) -> AnalysisResult:
        """分析C++项目 - 新的双路径接口
        
        Args:
            project_root: 项目根目录，用于include搜索和路径映射
            scan_directory: 扫描目录，默认与project_root相同
            compile_commands_path: compile_commands.json的具体路径，默认为project_root下的
            clang_working_directory: clang执行的工作目录，默认为project_root/Engine
            **kwargs: 其他配置参数
            
        Returns:
            AnalysisResult: 完整的分析结果
        """
        if scan_directory is None:
            scan_directory = project_root
            
        config = AnalysisConfig(
            project_root=project_root,
            scan_directory=scan_directory,
            compile_commands_path=compile_commands_path,
            clang_working_directory=clang_working_directory,
            **kwargs
        )
        
        return self._analyze_with_config(config)
    
    def analyze_directory(self, root_path: str, **kwargs) -> AnalysisResult:
        """分析目录 - 向后兼容接口"""
        return self.analyze(project_root=root_path, scan_directory=root_path, **kwargs)
    
    def _analyze_with_config(self, config: AnalysisConfig) -> AnalysisResult:
        """使用配置进行分析"""
        logger = get_logger()
        start_time = time.time()
        
        # 记录到日志文件
        logger.info("开始分析C++项目")
        logger.info(f"项目根目录: {config.project_root}")
        logger.info(f"扫描目录: {config.scan_directory}")
        
        try:
            # 1. 初始化解析器
            self._initialize_parser(config)
            
            # 2. 扫描文件
            scan_result = self._scan_files(config)
            if not scan_result.files:
                return self._create_empty_result(config, "未找到匹配的C++文件")
            
            if config.verbose:
                self.console.print(f"找到 {len(scan_result.files)} 个C++文件")
            
            # 3. 解析文件
            parsed_files = self._parse_files(scan_result.files, config)
            
            # 3.5. 收集诊断信息到JsonExporter
            self._collect_diagnostics(parsed_files)
            
            # 4. 提取实体
            extracted_entities = self._extract_entities(parsed_files, scan_result.file_mappings, config)
            
            # 4.5. 分析复杂度
            complexity_metrics = self._analyze_complexity(parsed_files)
            
            # 5. 导出JSON
            json_data = self._export_json(extracted_entities, config, scan_result.file_mappings, complexity_metrics)
            
            # 6. 生成统计信息
            statistics = self._generate_statistics(parsed_files, extracted_entities, start_time)
            
            if config.verbose:
                self._display_results(statistics)
            
            return AnalysisResult(
                success=True,
                extracted_entities=extracted_entities,
                file_mappings=scan_result.file_mappings,
                parsed_files=parsed_files,
                config=config,
                statistics=statistics,
                json_data=json_data
            )
            
        except Exception as e:
            error_msg = f"分析过程中发生错误: {str(e)}"
            self.console.print(f"[red]{error_msg}[/red]")
            
            return AnalysisResult(
                success=False,
                extracted_entities={},
                file_mappings={},
                parsed_files=[],
                config=config,
                statistics={"error": error_msg, "analysis_time": time.time() - start_time}
            )
    
    def _initialize_parser(self, config: AnalysisConfig):
        """初始化解析器 - 支持指定的compile_commands.json路径和工作目录"""
        if config.use_compile_commands:
            # 确定 compile_commands.json 的路径
            if config.compile_commands_path:
                compile_commands_path = config.compile_commands_path
            else:
                compile_commands_path = str(Path(config.project_root) / "compile_commands.json")
            
            # 确定 clang 的工作目录
            if config.clang_working_directory:
                clang_working_dir = config.clang_working_directory
            else:
                clang_working_dir = str(Path(config.project_root) / "Engine")
            
            # 设置 clang 的工作目录
            self.clang_parser.set_working_directory(clang_working_dir)
            
            # 尝试加载或生成compile_commands.json
            if config.generate_compile_commands:
                # 如果指定了具体路径，先检查是否存在
                if Path(compile_commands_path).exists():
                    self.clang_parser.load_compile_commands(compile_commands_path)
                else:
                    # 尝试生成到指定位置
                    self.clang_parser.ensure_compile_commands(config.project_root, compile_commands_path)
            else:
                # 直接加载指定的文件
                if Path(compile_commands_path).exists():
                    self.clang_parser.load_compile_commands(compile_commands_path)
                else:
                    from .logger import get_logger
                    logger = get_logger()
                    logger.warning(f"指定的 compile_commands.json 文件不存在: {compile_commands_path}")

    
    def _scan_files(self, config: AnalysisConfig) -> ScanResult:
        """扫描文件"""
        return self.file_scanner.scan_directory(config)
    
    def _parse_files(self, file_paths: List[str], config: AnalysisConfig) -> List[ParsedFile]:
        """解析文件"""
        if config.max_files:
            file_paths = file_paths[:config.max_files]
        
        with Progress(console=self.console) as progress:
            task = progress.add_task("解析C++文件...", total=len(file_paths))
            parsed_files = self.clang_parser.parse_files(file_paths, progress, task)
        
        return parsed_files
    
    def _collect_diagnostics(self, parsed_files: List[ParsedFile]):
        """收集诊断信息到JsonExporter"""
        all_diagnostics = []
        for parsed_file in parsed_files:
            if parsed_file.translation_unit and parsed_file.translation_unit.diagnostics:
                all_diagnostics.extend(parsed_file.translation_unit.diagnostics)
        
        if all_diagnostics:
            self.json_exporter.add_diagnostics(all_diagnostics)
    
    def _extract_entities(self, parsed_files: List[ParsedFile], file_mappings: Dict[str, str], config: AnalysisConfig) -> Dict[str, Any]:
        """提取实体"""
        return self.entity_extractor.extract_from_files(parsed_files, file_mappings, config)
    
    def _analyze_complexity(self, parsed_files: List[ParsedFile]) -> Dict[str, Any]:
        """分析代码复杂度"""
        return self.complexity_analyzer.analyze_parsed_files(parsed_files)
    
    def _export_json(self, extracted_entities: Dict[str, Any], config: AnalysisConfig, file_mappings: Dict[str, str], complexity_metrics: Dict[str, Any]) -> Dict[str, Any]:
        """导出JSON"""
        json_data = self.json_exporter.export_to_json(extracted_entities, config, file_mappings, complexity_metrics)
        
        # 保存到文件
        import json
        with open(config.output_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        
        return json_data
    
    def _generate_statistics(self, parsed_files: List[ParsedFile], extracted_entities: Dict[str, Any], start_time: float) -> Dict[str, Any]:
        """生成统计信息"""
        total_time = time.time() - start_time
        successful_files = sum(1 for f in parsed_files if f.success)
        
        return {
            "total_files": len(parsed_files),
            "successful_files": successful_files,
            "failed_files": len(parsed_files) - successful_files,
            "total_functions": len(extracted_entities.get('functions', {})),
            "total_classes": len(extracted_entities.get('classes', {})),
            "total_namespaces": len(extracted_entities.get('namespaces', {})),
            "analysis_time": total_time,
            "avg_parse_time": sum(f.parse_time for f in parsed_files) / len(parsed_files) if parsed_files else 0
        }
    
    def _display_results(self, statistics: Dict[str, Any]):
        """显示分析结果"""
        self.console.print("\n[bold green]分析完成![/bold green]")
        self.console.print(f"总文件数: {statistics['total_files']}")
        self.console.print(f"成功解析: {statistics['successful_files']}")
        self.console.print(f"解析失败: {statistics['failed_files']}")
        self.console.print(f"提取函数: {statistics['total_functions']}")
        self.console.print(f"提取类: {statistics['total_classes']}")
        self.console.print(f"提取命名空间: {statistics['total_namespaces']}")
        self.console.print(f"总用时: {statistics['analysis_time']:.2f}秒")
    
    def _create_empty_result(self, config: AnalysisConfig, reason: str) -> AnalysisResult:
        """创建空结果"""
        self.console.print(f"[yellow]{reason}[/yellow]")
        
        return AnalysisResult(
            success=False,
            extracted_entities={},
            file_mappings={},
            parsed_files=[],
            config=config,
            statistics={"reason": reason}
        ) 