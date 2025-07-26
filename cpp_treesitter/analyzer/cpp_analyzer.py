"""
Tree-sitter C++ Analyzer Main Module

基于tree-sitter的C++代码分析器主协调器，集成了以下增强功能：
1. USR ID生成系统
2. 全局节点注册表
3. 增强版实体提取器
4. 双JSON输出（主分析结果 + 全局nodes映射）
5. 完整的json_format.md规范支持

支持函数体文本提取功能和调用关系分析。
"""

import time
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime

from rich.console import Console
from rich.progress import track, Progress

# 使用本地的组件
from .file_scanner import FileScanner, ScanResult
from .json_exporter import JsonExporter
from .entity_extractor import EntityExtractor
from .call_relationship_analyzer import CallRelationshipAnalyzer

from pathlib import Path
from typing import List, Dict, Any
from tree_sitter import Parser, Language
import os

from .logger import Logger
from .data_structures import Project, NodeRepository, Function, Class, Namespace

# 尝试加载tree-sitter语言库
try:
    CPP_LANGUAGE = Language(os.path.join(os.path.dirname(__file__), '..', '..', 'grammars', 'languages.so'), 'cpp')
except Exception as e:
    # 如果无法加载预编译的语言库，尝试从源码编译
    Logger.get_logger().warning(f"无法加载预编译的C++语言库: {e}")
    try:
        import tree_sitter_cpp
        CPP_LANGUAGE = tree_sitter_cpp.language()
    except ImportError:
        Logger.get_logger().error("请安装 tree-sitter-cpp: pip install tree-sitter-cpp")
        raise


class CppAnalyzer:
    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.project_name = self.project_path.name
        self.logger = Logger.get_logger()
        
        # 初始化tree-sitter解析器
        self.parser = Parser()
        self.parser.set_language(CPP_LANGUAGE)
        
        # 初始化全局组件
        self.repo = NodeRepository()
        self.repo.clear()  # 确保从一个干净的状态开始
        self.call_analyzer = CallRelationshipAnalyzer(self.repo)
        
        # 初始化项目结构
        self.project = Project(name=self.project_name)
        
        # 文件扫描设置
        self.include_patterns = ['*.cpp', '*.cc', '*.cxx', '*.c++', '*.h', '*.hpp', '*.hxx', '*.h++']
        self.exclude_patterns = ['*/build/*', '*/dist/*', '*/.git/*', '*/node_modules/*']

    def analyze(self) -> Project:
        """
        执行C++项目的完整两阶段分析.
        
        第一阶段: 收集所有实体的声明
        第二阶段: 处理定义并建立调用关系
        
        Returns:
            Project: 分析完成的项目对象
        """
        self.logger.info("=" * 60)
        self.logger.info(f"开始分析C++项目: {self.project_name}")
        self.logger.info(f"项目路径: {self.project_path}")
        self.logger.info("=" * 60)
        
        start_time = time.time()
        
        try:
            # 扫描C++文件
            cpp_files = self._scan_cpp_files()
            if not cpp_files:
                self.logger.warning("未找到任何C++源文件")
                return self.project
            
            self.logger.info(f"发现 {len(cpp_files)} 个C++文件")
            
            # 第一阶段：收集声明
            self._phase_one_collect_declarations(cpp_files)
            
            # 第二阶段：处理定义和调用关系
            self._phase_two_process_definitions(cpp_files)
            
            # 构建最终项目结构
            self._build_project_structure()
            
            # 统计信息
            stats = self.repo.get_statistics()
            self.logger.info("=" * 60)
            self.logger.info("分析完成!")
            self.logger.info(f"总耗时: {time.time() - start_time:.2f} 秒")
            self.logger.info(f"总实体: {stats['total_entities']}")
            self.logger.info(f"  - 函数: {stats['by_type'].get('function', 0)}")
            self.logger.info(f"  - 类: {stats['by_type'].get('class', 0)}")
            self.logger.info(f"  - 命名空间: {stats['by_type'].get('namespace', 0)}")
            self.logger.info(f"调用关系: {stats['call_relationships']}")
            self.logger.info("=" * 60)
            
            return self.project
            
        except Exception as e:
            self.logger.error(f"分析过程中发生错误: {e}", exc_info=True)
            raise

    def _scan_cpp_files(self) -> List[Path]:
        """扫描C++源文件"""
        cpp_files = []
        
        for pattern in self.include_patterns:
            files = list(self.project_path.rglob(pattern))
            # 过滤排除的文件
            for file_path in files:
                if not any(excluded in str(file_path) for excluded in self.exclude_patterns):
                    cpp_files.append(file_path)
        
        # 去重和排序
        cpp_files = sorted(list(set(cpp_files)))
        
        # 记录文件到项目
        for file_path in cpp_files:
            self.project.add_file(str(file_path))
        
        return cpp_files

    def _phase_one_collect_declarations(self, cpp_files: List[Path]):
        """第一阶段：收集所有声明"""
        self.logger.info(f"第一阶段: 从 {len(cpp_files)} 个文件中收集声明...")
        
        successful_files = 0
        failed_files = 0
        
        with Progress() as progress:
            task = progress.add_task("收集声明", total=len(cpp_files))
            
            for file_path in cpp_files:
                try:
                    self._process_file_phase_one(file_path)
                    successful_files += 1
                except Exception as e:
                    self.logger.error(f"第一阶段处理文件失败 {file_path}: {e}")
                    failed_files += 1
                
                progress.advance(task)
        
        stats = self.repo.get_statistics()
        self.logger.info(f"第一阶段完成: 成功 {successful_files} 个文件，失败 {failed_files} 个文件")
        self.logger.info(f"收集到 {stats['total_entities']} 个实体声明")

    def _phase_two_process_definitions(self, cpp_files: List[Path]):
        """第二阶段：处理定义和调用关系"""
        self.logger.info(f"第二阶段: 处理定义和分析调用关系...")
        
        successful_files = 0
        failed_files = 0
        
        with Progress() as progress:
            task = progress.add_task("处理定义", total=len(cpp_files))
            
            for file_path in cpp_files:
                try:
                    self._process_file_phase_two(file_path)
                    successful_files += 1
                except Exception as e:
                    self.logger.error(f"第二阶段处理文件失败 {file_path}: {e}")
                    failed_files += 1
                
                progress.advance(task)
        
        stats = self.repo.get_statistics()
        self.logger.info(f"第二阶段完成: 成功 {successful_files} 个文件，失败 {failed_files} 个文件")
        self.logger.info(f"建立了 {stats['call_relationships']} 个调用关系")

    def _process_file_phase_one(self, file_path: Path):
        """第一阶段处理单个文件"""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # 解析AST
        tree = self.parser.parse(content.encode('utf-8'))
        if not tree or not tree.root_node:
            self.logger.warning(f"无法解析文件: {file_path}")
            return
        
        # 创建实体提取器并执行第一阶段
        extractor = EntityExtractor(str(file_path), content, self.repo)
        extractor.phase_one_collect_declarations(tree.root_node)

    def _process_file_phase_two(self, file_path: Path):
        """第二阶段处理单个文件"""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # 解析AST
        tree = self.parser.parse(content.encode('utf-8'))
        if not tree or not tree.root_node:
            return
        
        # 创建实体提取器并执行第二阶段
        extractor = EntityExtractor(str(file_path), content, self.repo)
        extractor.phase_two_process_definitions(tree.root_node)

    def _build_project_structure(self):
        """从NodeRepository中的节点信息构建最终的Project结构"""
        self.logger.info("构建项目结构...")
        
        # 从repo中收集所有实体的USR
        for usr, node in self.repo.nodes.items():
            self.project.add_entity(node)
        
        # 构建调用图和继承图
        self.project.build_graphs(self.repo)
        
        self.logger.info("项目结构构建完成")

    def get_analysis_summary(self) -> Dict[str, Any]:
        """获取分析摘要信息"""
        stats = self.repo.get_statistics()
        
        return {
            "project_name": self.project_name,
            "project_path": str(self.project_path),
            "analysis_timestamp": datetime.now().isoformat(),
            "total_files": len(self.project.files),
            "total_entities": stats['total_entities'],
            "entities_by_type": stats['by_type'],
            "call_relationships": stats['call_relationships'],
            "files_analyzed": stats['files_analyzed'],
            "parser_type": "tree-sitter",
            "version": "2.4"
        }

    def export_results(self, output_dir: str):
        """导出分析结果"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        self.logger.info(f"导出分析结果到: {output_path}")
        
        # 导出主分析结果
        exporter = JsonExporter()
        main_output = output_path / "cpp_treesitter_analysis_result.json"
        exporter.export_analysis_result(self.project, self.repo, str(main_output))
        
        # 导出全局nodes映射
        nodes_output = output_path / "nodes.json"
        exporter.export_nodes_json(self.repo, str(nodes_output))
        
        # 导出分析摘要
        summary_output = output_path / "analysis_summary.json"
        with open(summary_output, 'w', encoding='utf-8') as f:
            json.dump(self.get_analysis_summary(), f, indent=2, ensure_ascii=False)
        
        self.logger.info(f"导出完成:")
        self.logger.info(f"  - 主分析结果: {main_output}")
        self.logger.info(f"  - 全局节点映射: {nodes_output}")
        self.logger.info(f"  - 分析摘要: {summary_output}")

    def analyze_and_export(self, output_dir: str = "analysis_results") -> Project:
        """分析项目并导出结果的便捷方法"""
        project = self.analyze()
        self.export_results(output_dir)
        return project 