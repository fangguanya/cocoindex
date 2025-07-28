"""
Tree-sitter C++ Analyzer Main Module

基于tree-sitter的C++代码分析器主协调器，集成了以下增强功能：
1. USR ID生成系统
2. 全局节点注册表
3. 增强版实体提取器
4. 双JSON输出（主分析结果 + 全局nodes映射）
5. 完整的json_format.md规范支持
6. 文件ID映射系统 (v2.3新增)

支持函数体文本提取功能和调用关系分析。
"""

import os
import time
import json
import gc
import hashlib
import psutil
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from contextlib import contextmanager

from rich.console import Console
from rich.progress import track, Progress

# 使用本地的组件
from .file_scanner import FileScanner, ScanResult
from .json_exporter import JsonExporter
from .entity_extractor import EntityExtractor
from .call_relationship_analyzer import CallRelationshipAnalyzer
from .file_manager import FileManager, get_file_manager
from .template_analyzer import TemplateAnalyzer
from .file_filter import UnifiedFileFilter, create_unreal_filter

from pathlib import Path
from typing import List, Dict, Any
import tree_sitter
from tree_sitter import Parser, Language
import os

from .logger import Logger
from .data_structures import Project, NodeRepository, Function, Class, Namespace

# 尝试加载tree-sitter语言库
try:
    import tree_sitter_cpp
    CPP_LANGUAGE = tree_sitter.Language(tree_sitter_cpp.language())
except Exception as e:
    # 如果无法加载预编译的语言库，尝试从源码编译
    Logger.get_logger().warning(f"无法加载预编译的C++语言库: {e}")
    try:
        import tree_sitter_cpp
        import tree_sitter
        # 将PyCapsule包装成Language对象
        CPP_LANGUAGE = tree_sitter.Language(tree_sitter_cpp.language())
    except ImportError:
        Logger.get_logger().error("请安装 tree-sitter-cpp: pip install tree-sitter-cpp")
        raise


class PerformanceMonitor:
    """性能监控器，用于跟踪分析过程的性能指标"""
    
    def __init__(self):
        self.timers = {}
        self.memory_tracker = {}
        self.process = psutil.Process()
        
    @contextmanager
    def timer(self, name: str):
        """性能计时器上下文管理器"""
        start_time = time.time()
        start_memory = self.get_memory_usage()
        
        try:
            yield
        finally:
            elapsed = time.time() - start_time
            end_memory = self.get_memory_usage()
            memory_delta = end_memory - start_memory
            
            self.timers[name] = {
                'duration': elapsed,
                'start_memory_mb': start_memory,
                'end_memory_mb': end_memory,
                'memory_delta_mb': memory_delta
            }
            
            from .logger import Logger
            logger = Logger.get_logger()
            logger.info(f"⏱️  {name}: {elapsed:.2f}s, 内存: {end_memory:.1f}MB (+{memory_delta:+.1f}MB)")
    
    def get_memory_usage(self) -> float:
        """获取当前内存使用量(MB)"""
        return self.process.memory_info().rss / 1024 / 1024
    
    def log_memory_checkpoint(self, name: str):
        """记录内存检查点"""
        memory_mb = self.get_memory_usage()
        self.memory_tracker[name] = memory_mb
        
        from .logger import Logger
        logger = Logger.get_logger()
        logger.info(f"📊 内存检查点 {name}: {memory_mb:.1f}MB")
    
    def force_gc(self, description: str = ""):
        """强制垃圾回收并记录效果"""
        before_mb = self.get_memory_usage()
        gc.collect()
        after_mb = self.get_memory_usage()
        freed_mb = before_mb - after_mb
        
        from .logger import Logger
        logger = Logger.get_logger()
        if freed_mb > 1.0:  # 只有回收超过1MB才记录
            logger.info(f"🗑️  垃圾回收{description}: 释放 {freed_mb:.1f}MB 内存")
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """获取性能摘要"""
        total_time = sum(timer['duration'] for timer in self.timers.values())
        peak_memory = max(timer['end_memory_mb'] for timer in self.timers.values()) if self.timers else 0
        
        return {
            'total_analysis_time': total_time,
            'peak_memory_mb': peak_memory,
            'stage_timings': {name: timer['duration'] for name, timer in self.timers.items()},
            'memory_checkpoints': self.memory_tracker
        }


class CppAnalyzer:
    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.project_name = self.project_path.name
        self.logger = Logger.get_logger()
        
        # 初始化性能监控器
        self.performance_monitor = PerformanceMonitor()
        
        # 初始化tree-sitter解析器
        # 新版tree-sitter API: 在构造时直接传入language
        self.parser = Parser(CPP_LANGUAGE)
        
        # 初始化全局组件
        self.repo = NodeRepository()
        self.repo.clear()  # 确保从一个干净的状态开始
        self.call_analyzer = CallRelationshipAnalyzer(self.repo)
        
        # 初始化文件管理器 - 传入project_root
        self.file_manager = get_file_manager(str(self.project_path))
        self.file_manager.clear()  # 重置文件管理器
        # 重新设置project_root（因为clear()不会重置它）
        self.file_manager.project_root = self.project_path
        
        # 初始化模板分析器
        self.template_analyzer = TemplateAnalyzer(self.repo)
        
        # 初始化项目结构
        self.project = Project(name=self.project_name)
        
        # 文件扫描设置
        self.include_patterns = ['*.cpp', '*.cc', '*.cxx', '*.c++', '*.h', '*.hpp', '*.hxx', '*.h++']
        
        # 使用高效的统一文件过滤器
        self.file_filter = create_unreal_filter()

        # 新增：AST缓存机制
        self.ast_cache: Dict[str, Tuple[Any, str]] = {}  # file_path -> (tree, content)
        self.cache_hits = 0
        self.cache_misses = 0

        # 在分析过程中保存这些数据
        self.file_contents: Dict[str, bytes] = {}  # 保存文件内容用于验证
        self.parsed_trees: Dict[str, Any] = {}    # 保存解析的AST用于验证

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
            self.logger.info("开始扫描C++源文件...")
            cpp_files = self._scan_cpp_files()
            self.logger.info("文件扫描完成")
            
            if not cpp_files:
                self.logger.warning("未找到任何C++源文件")
                return self.project
            
            self.logger.info(f"发现 {len(cpp_files)} 个C++文件")
            
            # 注册所有文件到文件管理器
            self._register_files(cpp_files)
            
            # 第一阶段：收集声明
            self._phase_one_collect_declarations(cpp_files)
            
            # 第二阶段：处理定义和调用关系
            self._phase_two_process_definitions(cpp_files)
            
            # 构建最终项目结构
            self._build_project_structure()
            
            # 统计信息
            stats = self.repo.get_statistics()
            file_stats = self.file_manager.get_statistics()
            
            self.logger.info("=" * 60)
            self.logger.info("分析完成!")
            self.logger.info(f"总耗时: {time.time() - start_time:.2f} 秒")
            self.logger.info(f"总实体: {stats['total_entities']}")
            self.logger.info(f"  - 函数: {stats['by_type'].get('function', 0)}")
            self.logger.info(f"  - 类: {stats['by_type'].get('class', 0)}")
            self.logger.info(f"  - 命名空间: {stats['by_type'].get('namespace', 0)}")
            self.logger.info(f"调用关系: {stats['call_relationships']}")
            self.logger.info(f"文件映射: {file_stats['total_files']} 个文件")
            
            # 新增：AST缓存性能统计
            total_cache_operations = self.cache_hits + self.cache_misses
            if total_cache_operations > 0:
                cache_hit_rate = (self.cache_hits / total_cache_operations) * 100
                self.logger.info(f"AST缓存性能: 命中率 {cache_hit_rate:.1f}% ({self.cache_hits}/{total_cache_operations})")
                estimated_time_saved = self.cache_hits * 0.1  # 假设每次解析节省100ms
                self.logger.info(f"预估节省时间: {estimated_time_saved:.2f} 秒")
            
            self.logger.info("=" * 60)
            
            # 添加质量保证验证
            self.logger.info("🎯 开始质量保证验证...")
            from .quality_assurance import QualityAssuranceReporter
            
            qa_reporter = QualityAssuranceReporter()
            qa_report = qa_reporter.generate_comprehensive_report(
                self.repo, 
                self.file_contents,
                self.parsed_trees
            )
            
            # 打印质量报告摘要
            qa_reporter.print_summary(qa_report)
            
            # 导出质量保证报告
            analysis_output_path = os.path.join(self.project_path, "analysis_results")
            os.makedirs(analysis_output_path, exist_ok=True)
            qa_report_path = os.path.join(analysis_output_path, "quality_assurance_report.json")
            qa_reporter.export_report(qa_report, qa_report_path)
            
            self.logger.info(f"📊 总体质量评分: {qa_report.overall_quality_score}/100")
            self.logger.info(f"🎯 质量保证报告已导出到: {qa_report_path}")
            
            return self.project
            
        except Exception as e:
            self.logger.error(f"分析过程中发生错误: {e}")
            raise

    def _scan_cpp_files(self) -> List[Path]:
        """扫描C++源文件"""
        cpp_files = []
        
        self.logger.info(f"扫描项目路径: {self.project_path}")
        self.logger.info(f"包含模式: {self.include_patterns}")
        self.logger.info(f"排除模式: {self.file_filter.raw_patterns}")
        
        for i, pattern in enumerate(self.include_patterns):
            self.logger.info(f"正在扫描模式 {i+1}/{len(self.include_patterns)}: {pattern}")
            try:
                files = list(self.project_path.rglob(pattern))
                self.logger.info(f"模式 {pattern} 找到 {len(files)} 个文件")
                
                # 使用统一过滤器过滤文件
                before_filter_count = len(files)
                filtered_files = self.file_filter.filter_files(files)
                cpp_files.extend(filtered_files)
                filtered_count = before_filter_count - len(filtered_files)
                
                if filtered_count > 0:
                    self.logger.info(f"模式 {pattern} 过滤掉 {filtered_count} 个文件")
                    
            except Exception as e:
                self.logger.error(f"扫描模式 {pattern} 时出错: {e}")
        
        self.logger.info(f"扫描完成，初步找到 {len(cpp_files)} 个文件")
        
        # 去重和排序
        original_count = len(cpp_files)
        cpp_files = sorted(list(set(cpp_files)))
        deduplicated_count = len(cpp_files)
        
        if original_count != deduplicated_count:
            self.logger.info(f"去重: {original_count} -> {deduplicated_count} 个文件")
        
        # 记录文件到项目
        self.logger.info("将文件添加到项目结构...")
        for i, file_path in enumerate(cpp_files):
            if i % 1000 == 0 and i > 0:  # 每1000个文件输出一次进度
                self.logger.info(f"已添加 {i}/{len(cpp_files)} 个文件到项目")
            self.project.add_file(str(file_path))
        
        self.logger.info(f"文件扫描和注册完成，共 {len(cpp_files)} 个文件")
        
        # 输出过滤统计信息
        filter_stats = self.file_filter.get_statistics()
        self.logger.info(f"过滤统计: 检查了 {filter_stats['total_checks']} 个文件，排除了 {filter_stats['excluded_count']} 个")
        self.logger.info(f"排除率: {filter_stats['exclusion_rate']:.1f}%")
        
        return cpp_files

    def _register_files(self, cpp_files: List[Path]):
        """注册所有文件到文件管理器"""
        self.logger.info("注册文件到文件管理器...")
        
        file_paths = [str(file_path) for file_path in cpp_files]
        mappings = self.file_manager.register_files(file_paths)
        
        self.logger.info(f"注册了 {len(mappings)} 个文件，生成文件ID映射")
        
        # 验证映射完整性
        if not self.file_manager.validate_mappings():
            self.logger.warning("文件映射验证失败！")

    def _phase_one_collect_declarations(self, cpp_files: List[Path]):
        """第一阶段：收集所有声明"""
        self.logger.info(f"第一阶段: 从 {len(cpp_files)} 个文件中收集声明...")
        
        successful_files = 0
        failed_files = 0
        
        with Progress() as progress:
            task = progress.add_task("收集声明", total=len(cpp_files))
            
            for i, file_path in enumerate(cpp_files):
                try:
                    self.logger.info(f"Phase 1: Processing declarations from {file_path}")
                    self._process_file_phase_one(file_path)
                    successful_files += 1
                except Exception as e:
                    self.logger.error(f"第一阶段处理文件失败 {file_path}: {e}")
                    failed_files += 1
                
                progress.advance(task)
                
                # 每处理100个文件输出一次进度到日志
                if (i + 1) % 100 == 0:
                    self.logger.info(f"第一阶段进度: {i + 1}/{len(cpp_files)} 个文件已处理")
        
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
            
            for i, file_path in enumerate(cpp_files):
                try:
                    self.logger.info(f"Phase 2: Processing definitions and call relationships from {file_path}")
                    self._process_file_phase_two(file_path)
                    successful_files += 1
                except Exception as e:
                    self.logger.error(f"第二阶段处理文件失败 {file_path}: {e}")
                    failed_files += 1
                
                progress.advance(task)
                
                # 每处理50个文件输出一次进度到日志（第二阶段更频繁）
                if (i + 1) % 50 == 0:
                    self.logger.info(f"第二阶段进度: {i + 1}/{len(cpp_files)} 个文件已处理")
        
        # 新增：第二轮调用关系解析，处理待解析的调用
        self.logger.info("开始第二轮调用关系解析...")
        pending_stats = self.call_analyzer.resolve_pending_calls()
        
        stats = self.repo.get_statistics()
        self.logger.info(f"第二阶段完成: 成功 {successful_files} 个文件，失败 {failed_files} 个文件")
        self.logger.info(f"建立了 {stats['call_relationships']} 个调用关系")
        self.logger.info(f"第二轮解析: 新解析 {pending_stats['resolved']} 个调用，剩余 {pending_stats['still_pending']} 个待解析")

    def _process_file_phase_one(self, file_path: Path):
        """第一阶段处理单个文件"""
        # 获取文件ID
        file_id = self.file_manager.get_file_id(str(file_path))
        if not file_id:
            self.logger.warning(f"文件 {file_path} 未注册到文件管理器")
            file_id = self.file_manager.get_or_create_file_id(str(file_path))
        
        # 检查缓存
        file_path_str = str(file_path)
        if file_path_str in self.ast_cache:
            self.cache_hits += 1
            tree, content = self.ast_cache[file_path_str]
        else:
            self.cache_misses += 1
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            tree = self.parser.parse(content.encode('utf-8'))
            self.ast_cache[file_path_str] = (tree, content)
        
        if not tree or not tree.root_node:
            self.logger.warning(f"无法解析文件: {file_path}")
            return

        # 保存文件内容和解析树用于质量保证验证
        self.file_contents[file_path_str] = content.encode('utf-8')
        self.parsed_trees[file_path_str] = tree.root_node

        # 创建实体提取器并执行第一阶段
        extractor = EntityExtractor(str(file_path), content, self.repo, file_id)
        extractor.phase_one_collect_declarations(tree.root_node)

    def _process_file_phase_two(self, file_path: Path):
        """第二阶段处理单个文件"""
        # 获取文件ID
        file_id = self.file_manager.get_file_id(str(file_path))
        if not file_id:
            self.logger.warning(f"文件 {file_path} 未注册到文件管理器")
            file_id = self.file_manager.get_or_create_file_id(str(file_path))
        
        # 检查缓存
        file_path_str = str(file_path)
        if file_path_str in self.ast_cache:
            self.cache_hits += 1
            tree, content = self.ast_cache[file_path_str]
        else:
            self.cache_misses += 1
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            tree = self.parser.parse(content.encode('utf-8'))
            self.ast_cache[file_path_str] = (tree, content)
        
        if not tree or not tree.root_node:
            return
        
        # 创建实体提取器并执行第二阶段
        extractor = EntityExtractor(str(file_path), content, self.repo, file_id)
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
        file_stats = self.file_manager.get_statistics()
        
        return {
            "project_name": self.project_name,
            "project_path": str(self.project_path),
            "analysis_timestamp": datetime.now().isoformat(),
            "total_files": len(self.project.files),
            "total_entities": stats['total_entities'],
            "entities_by_type": stats['by_type'],
            "call_relationships": stats['call_relationships'],
            "files_analyzed": stats['files_analyzed'],
            "file_mappings": file_stats,
            "parser_type": "tree-sitter",
            "version": "2.3"
        }

    def export_results(self, output_dir: str):
        """导出分析结果"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        self.logger.info("=" * 60)
        self.logger.info("🚀 开始导出分析结果")
        self.logger.info(f"📁 导出目录: {output_path}")
        self.logger.info("=" * 60)
        
        # 统计信息
        stats = self.repo.get_statistics()
        total_entities = stats['total_entities']
        total_files = len(self.project.files)
        
        self.logger.info(f"📊 准备导出数据:")
        self.logger.info(f"   - 总实体数: {total_entities}")
        self.logger.info(f"   - 总文件数: {total_files}")
        self.logger.info(f"   - 调用关系: {stats['call_relationships']}")
        
        # 1. 导出主分析结果
        self.logger.info("📝 [1/4] 正在导出主分析结果...")
        exporter = JsonExporter(self.file_manager)
        main_output = output_path / "cpp_treesitter_analysis_result.json"
        
        import time
        start_time = time.time()
        exporter.export_analysis_result(self.project, self.repo, str(main_output))
        main_export_time = time.time() - start_time
        
        file_size_mb = main_output.stat().st_size / (1024 * 1024)
        self.logger.info(f"✅ 主分析结果导出完成: {file_size_mb:.2f}MB, 耗时: {main_export_time:.2f}秒")
        
        # 2. 导出全局nodes映射
        self.logger.info("🔗 [2/4] 正在导出全局nodes映射...")
        nodes_output = output_path / "nodes.json"
        
        start_time = time.time()
        exporter.export_nodes_json(self.repo, str(nodes_output))
        nodes_export_time = time.time() - start_time
        
        file_size_mb = nodes_output.stat().st_size / (1024 * 1024)
        self.logger.info(f"✅ 全局nodes映射导出完成: {file_size_mb:.2f}MB, 耗时: {nodes_export_time:.2f}秒")
        
        # 3. 导出文件映射
        self.logger.info("📄 [3/4] 正在导出文件映射...")
        file_mapping_output = output_path / "file_mappings.json"
        
        start_time = time.time()
        with open(file_mapping_output, 'w', encoding='utf-8') as f:
            json.dump(self.file_manager.export_mapping_json(), f, indent=2, ensure_ascii=False)
        file_mapping_time = time.time() - start_time
        
        file_size_mb = file_mapping_output.stat().st_size / (1024 * 1024)
        self.logger.info(f"✅ 文件映射导出完成: {file_size_mb:.2f}MB, 耗时: {file_mapping_time:.2f}秒")
        
        # 4. 导出分析摘要
        self.logger.info("📋 [4/4] 正在导出分析摘要...")
        summary_output = output_path / "analysis_summary.json"
        
        start_time = time.time()
        with open(summary_output, 'w', encoding='utf-8') as f:
            json.dump(self.get_analysis_summary(), f, indent=2, ensure_ascii=False)
        summary_time = time.time() - start_time
        
        file_size_mb = summary_output.stat().st_size / (1024 * 1024)
        self.logger.info(f"✅ 分析摘要导出完成: {file_size_mb:.2f}MB, 耗时: {summary_time:.2f}秒")
        
        # 总结
        total_export_time = main_export_time + nodes_export_time + file_mapping_time + summary_time
        total_size_mb = sum(f.stat().st_size for f in [main_output, nodes_output, file_mapping_output, summary_output]) / (1024 * 1024)
        
        self.logger.info("=" * 60)
        self.logger.info("🎉 导出完成!")
        self.logger.info(f"📊 导出统计:")
        self.logger.info(f"   - 总导出时间: {total_export_time:.2f}秒")
        self.logger.info(f"   - 总文件大小: {total_size_mb:.2f}MB")
        self.logger.info(f"   - 平均导出速度: {total_entities / total_export_time:.0f} 实体/秒")
        
        self.logger.info(f"导出完成:")
        self.logger.info(f"  - 主分析结果: {main_output}")
        self.logger.info(f"  - 全局节点映射: {nodes_output}")
        self.logger.info(f"  - 文件映射: {file_mapping_output}")
        self.logger.info(f"  - 分析摘要: {summary_output}")

    def analyze_and_export(self, output_dir: str = "analysis_results") -> Project:
        """分析项目并导出结果的便捷方法，集成性能监控"""
        overall_start = time.time()
        self.performance_monitor.log_memory_checkpoint("分析开始")
        
        try:
            # 执行分析
            with self.performance_monitor.timer("项目分析"):
                project = self.analyze()
            
            # 强制垃圾回收
            self.performance_monitor.force_gc("分析完成后")
            self.performance_monitor.log_memory_checkpoint("分析完成")
            
            # 导出结果
            with self.performance_monitor.timer("结果导出"):
                self.export_results(output_dir)
            
            # 最终垃圾回收
            self.performance_monitor.force_gc("导出完成后")
            self.performance_monitor.log_memory_checkpoint("导出完成")
            
            # 输出性能摘要
            total_time = time.time() - overall_start
            self.logger.info("=" * 60)
            self.logger.info("📊 性能分析摘要:")
            
            summary = self.performance_monitor.get_performance_summary()
            self.logger.info(f"   - 总耗时: {total_time:.2f}秒")
            self.logger.info(f"   - 峰值内存: {summary['peak_memory_mb']:.1f}MB")
            
            for stage, duration in summary['stage_timings'].items():
                percentage = (duration / total_time) * 100
                self.logger.info(f"   - {stage}: {duration:.2f}秒 ({percentage:.1f}%)")
            
            # 缓存性能统计
            cache_stats = self.get_cache_statistics()
            if cache_stats['total_operations'] > 0:
                self.logger.info(f"   - AST缓存命中率: {cache_stats['hit_rate']:.1f}%")
                saved_time = cache_stats['cache_hits'] * 0.1  # 假设每次缓存命中节省0.1秒
                self.logger.info(f"   - 预估节省时间: {saved_time:.2f}秒")
            
            self.logger.info("=" * 60)
            
            return project
            
        except Exception as e:
            self.logger.error(f"分析过程出错: {e}")
            raise 

    def clear_ast_cache(self):
        """清理AST缓存以释放内存"""
        cache_size = len(self.ast_cache)
        self.ast_cache.clear()
        self.logger.info(f"已清理AST缓存，释放了 {cache_size} 个缓存项")

    def get_cache_statistics(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        total_operations = self.cache_hits + self.cache_misses
        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "total_operations": total_operations,
            "hit_rate": (self.cache_hits / total_operations * 100) if total_operations > 0 else 0,
            "estimated_time_saved": self.cache_hits * 0.1  # 假设每次命中节省0.1秒
        }

    def _get_file_hash(self, file_path: Path) -> str:
        """计算文件的MD5哈希值，用于缓存键"""
        import hashlib
        try:
            with open(file_path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return str(file_path.stat().st_mtime)  # 降级到修改时间
    
    def _get_cached_ast(self, file_path: Path) -> Optional[Any]:
        """从缓存获取AST，基于文件哈希检查是否有效"""
        file_hash = self._get_file_hash(file_path)
        cache_key = str(file_path)
        
        if cache_key in self.ast_cache:
            cached_tree, cached_hash = self.ast_cache[cache_key]
            if cached_hash == file_hash:
                self.cache_hits += 1
                return cached_tree
            else:
                # 文件已更改，移除旧缓存
                del self.ast_cache[cache_key]
        
        self.cache_misses += 1
        return None
    
    def _cache_ast(self, file_path: Path, tree: Any):
        """缓存AST，同时存储文件哈希"""
        file_hash = self._get_file_hash(file_path)
        cache_key = str(file_path)
        self.ast_cache[cache_key] = (tree, file_hash)
        
        # 限制缓存大小，避免内存溢出
        if len(self.ast_cache) > 1000:  # 最多缓存1000个文件
            # 移除最旧的缓存项
            oldest_key = next(iter(self.ast_cache))
            del self.ast_cache[oldest_key] 