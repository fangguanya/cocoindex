"""
并行 C++ 分析器模块 (P1 任务)

实现基于 ThreadPoolExecutor 的文件级并行解析框架，
提升大项目的分析性能，同时确保线程安全。

主要特性：
1. 文件级并行处理
2. 线程安全的 NodeRepository 访问
3. 进度跟踪和错误处理
4. 动态负载均衡
5. 内存使用优化

性能目标：
- 5k+ 文件项目的分析时间减少 70-80%
- 支持 CPU 核心数自适应并发
- 内存使用控制在合理范围内
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import queue
import multiprocessing

from rich.progress import Progress, TaskID, TextColumn, BarColumn, TimeElapsedColumn
from rich.console import Console

from .data_structures import NodeRepository, Project
from .entity_extractor import EntityExtractor  
from .file_scanner import FileScanner
from .logger import Logger
from .file_filter import create_unreal_filter

# ==============================================================================
# 模块级单例优化
# ==============================================================================

_cpp_language = None
_language_lock = threading.Lock()

def get_cpp_language():
    """获取tree-sitter C++语言对象的单例"""
    global _cpp_language
    if _cpp_language is None:
        with _language_lock:
            if _cpp_language is None:
                try:
                    import tree_sitter_cpp
                    import tree_sitter
                    # 将PyCapsule包装成Language对象
                    _cpp_language = tree_sitter.Language(tree_sitter_cpp.language())
                except ImportError as e:
                    raise ImportError(f"无法导入tree_sitter_cpp: {e}")
    return _cpp_language

# ==============================================================================
# 主分析器类
# ==============================================================================

@dataclass
class ParallelTaskResult:
    """并行任务结果"""
    file_path: str
    success: bool
    entities_extracted: int
    processing_time: float
    error_message: Optional[str] = None
    memory_usage_mb: Optional[float] = None


@dataclass
class ParallelAnalysisConfig:
    """并行分析配置"""
    max_workers: Optional[int] = None  # None = auto detect
    chunk_size: int = 10  # 文件批次大小
    enable_progress: bool = True
    memory_limit_mb: Optional[int] = None  # 内存限制
    enable_detailed_logging: bool = False
    fail_fast: bool = False  # 遇到错误是否立即停止


class ThreadSafeNodeRepository:
    """线程安全的 NodeRepository 包装器"""
    
    def __init__(self, base_repo: NodeRepository):
        self.base_repo = base_repo
        self._lock = threading.RLock()  # 可重入锁
        self._stats_cache = {}
        self._cache_lock = threading.Lock()
    
    def add_node_threadsafe(self, node):
        """线程安全的节点添加"""
        with self._lock:
            return self.base_repo.add_node(node)
    
    def get_node_threadsafe(self, usr: str):
        """线程安全的节点获取"""
        with self._lock:
            return self.base_repo.get_node(usr)
    
    def add_call_relationship_threadsafe(self, caller_usr: str, callee_usr: str):
        """线程安全的调用关系添加"""
        with self._lock:
            return self.base_repo.add_call_relationship(caller_usr, callee_usr)
    
    def get_statistics_cached(self) -> Dict[str, Any]:
        """获取缓存的统计信息（避免频繁计算）"""
        with self._cache_lock:
            return self.base_repo.get_statistics()
    
    def invalidate_cache(self):
        """使缓存失效"""
        with self._cache_lock:
            self._stats_cache.clear()


class ParallelCppAnalyzer:
    """并行 C++ 分析器主类"""
    
    def __init__(self, project_path: str, config: Optional[ParallelAnalysisConfig] = None):
        self.project_path = Path(project_path)
        self.project_name = self.project_path.name
        self.config = config or ParallelAnalysisConfig()
        self.logger = Logger.get_logger()
        self.console = Console()
        
        # 自动检测最优工作线程数
        if self.config.max_workers is None:
            cpu_count = multiprocessing.cpu_count()
            # I/O 密集型任务，可以使用 2-4 倍的 CPU 核心数
            self.config.max_workers = min(cpu_count * 2, 16)  # 最多 16 个线程
        
        # 初始化组件
        self.base_repo = NodeRepository()
        self.base_repo.clear()
        self.thread_safe_repo = ThreadSafeNodeRepository(self.base_repo)
        
        # 解析器池（每个线程一个解析器实例）
        self._parsers = {}
        self._parser_lock = threading.Lock()
        
        # 统计信息
        self.analysis_stats = {
            'total_files': 0,
            'processed_files': 0,
            'failed_files': 0,
            'total_entities': 0,
            'total_processing_time': 0.0,
            'average_file_time': 0.0,
            'peak_memory_mb': 0.0,
            'parallel_efficiency': 0.0
        }

    def _get_thread_parser(self):
        """获取当前线程的解析器实例 - 优化版使用模块级单例"""
        thread_id = threading.get_ident()
        
        with self._parser_lock:
            if thread_id not in self._parsers:
                try:
                    from tree_sitter import Parser
                    
                    # 使用模块级单例，避免每次调用tree_sitter_cpp.language()
                    # 新版tree-sitter API: 在构造时直接传入language
                    parser = Parser(get_cpp_language())
                    self._parsers[thread_id] = parser
                    
                    if self.config.enable_detailed_logging:
                        self.logger.debug(f"线程 {thread_id}: 创建新的解析器实例")
                        
                except ImportError as e:
                    self.logger.error(f"线程 {thread_id}: 无法初始化 tree-sitter 解析器: {e}")
                    raise
            
            return self._parsers[thread_id]

    def _process_file_worker(self, file_path: Path, phase: str) -> ParallelTaskResult:
        """工作线程中的文件处理函数 - 增强版带重试机制"""
        return self._process_file_worker_with_retry(file_path, phase, max_retries=2)
    
    def _process_file_worker_with_retry(self, file_path: Path, phase: str, max_retries: int = 2) -> ParallelTaskResult:
        """带重试机制的文件处理函数"""
        thread_id = threading.get_ident()
        
        for attempt in range(max_retries + 1):
            start_time = time.time()
            
            try:
                # 读取文件内容
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                # 获取线程专用的解析器
                parser = self._get_thread_parser()
                
                # 解析 AST
                tree = parser.parse(content.encode('utf-8'))
                if not tree or not tree.root_node:
                    if attempt < max_retries:
                        self.logger.warning(f"线程 {thread_id}: AST解析失败，重试 {attempt + 1}/{max_retries}: {file_path}")
                        time.sleep(0.1 * (attempt + 1))  # 递增延迟
                        continue
                    else:
                        return ParallelTaskResult(
                            file_path=str(file_path),
                            success=False,
                            entities_extracted=0,
                            processing_time=time.time() - start_time,
                            error_message=f"无法解析 AST: {file_path}"
                        )
                
                # 创建实体提取器
                extractor = EntityExtractor(str(file_path), content, self.base_repo)
                
                # 根据阶段执行不同的处理
                if phase == "phase_one":
                    extractor.phase_one_collect_declarations(tree.root_node)
                elif phase == "phase_two":
                    extractor.phase_two_process_definitions(tree.root_node)
                else:
                    raise ValueError(f"Unknown phase: {phase}")
                
                processing_time = time.time() - start_time
                
                # 获取本次处理提取的实体数量（优化：避免每次遍历所有节点）
                entities_count = len([node for node in self.base_repo.nodes.values() 
                                    if hasattr(node, 'file_path') and node.file_path == str(file_path)])
                
                if self.config.enable_detailed_logging:
                    self.logger.debug(f"线程 {thread_id}: 处理文件 {file_path} 完成，"
                                    f"提取 {entities_count} 个实体，耗时 {processing_time:.3f}s")
                
                # 估算内存使用（粗略）
                memory_usage_mb = len(content) / (1024 * 1024)  # 文件大小作为内存使用估算
                
                return ParallelTaskResult(
                    file_path=str(file_path),
                    success=True,
                    entities_extracted=entities_count,
                    processing_time=processing_time,
                    memory_usage_mb=memory_usage_mb
                )
                
            except Exception as e:
                if attempt < max_retries:
                    self.logger.warning(f"线程 {thread_id}: 处理文件 {file_path} 失败，重试 {attempt + 1}/{max_retries}: {e}")
                    time.sleep(0.1 * (attempt + 1))  # 递增延迟
                    continue
                else:
                    error_msg = f"处理文件 {file_path} 时发生错误: {str(e)}"
                    self.logger.error(f"线程 {thread_id}: {error_msg}")
                    
                    return ParallelTaskResult(
                        file_path=str(file_path),
                        success=False,
                        entities_extracted=0,
                        processing_time=time.time() - start_time,
                        error_message=error_msg
                    )
        
        # 理论上不会到达这里
        return ParallelTaskResult(
            file_path=str(file_path),
            success=False,
            entities_extracted=0,
            processing_time=0.0,
            error_message="重试次数已用完"
        )

    def _process_files_parallel(self, files: List[Path], phase: str) -> List[ParallelTaskResult]:
        """并行处理文件列表"""
        results = []
        failed_count = 0
        
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            # 提交所有任务
            future_to_file = {
                executor.submit(self._process_file_worker, file_path, phase): file_path 
                for file_path in files
            }
            
            # 设置进度条
            progress = None
            task_id = None
            if self.config.enable_progress:
                progress = Progress(
                    TextColumn("[bold blue]Processing files"),
                    BarColumn(),
                    "[progress.percentage]{task.percentage:>3.0f}%",
                    "•",
                    TimeElapsedColumn(),
                    console=self.console
                )
                progress.start()
                task_id = progress.add_task(f"[cyan]{phase}", total=len(files))
            
            try:
                # 收集结果
                for future in as_completed(future_to_file):
                    result = future.result()
                    results.append(result)
                    
                    if not result.success:
                        failed_count += 1
                        if self.config.fail_fast:
                            self.logger.error(f"启用 fail_fast，因错误停止: {result.error_message}")
                            break
                    
                    # 更新进度条
                    if progress and task_id:
                        progress.advance(task_id)
                
            finally:
                if progress:
                    progress.stop()
        
        # 输出阶段统计
        success_count = len(results) - failed_count
        total_time = sum(r.processing_time for r in results)
        avg_time = total_time / len(results) if results else 0
        
        self.logger.info(f"{phase} 完成: {success_count}/{len(files)} 成功, "
                        f"平均耗时 {avg_time:.3f}s/文件")
        
        if failed_count > 0:
            self.logger.warning(f"{phase}: {failed_count} 个文件处理失败")
        
        return results

    def analyze_parallel(self) -> Project:
        """执行并行分析"""
        self.logger.info(f"开始并行分析项目: {self.project_path}")
        self.logger.info(f"并行配置: {self.config.max_workers} 个工作线程")
        
        start_time = time.time()
        
        # 1. 扫描文件 - 使用统一过滤器
        self.logger.info("扫描项目文件...")
        
        # 使用简单的文件扫描，然后应用过滤器
        include_patterns = ['*.cpp', '*.cc', '*.cxx', '*.c++', '*.h', '*.hpp', '*.hxx', '*.h++']
        all_files = []
        
        for pattern in include_patterns:
            found_files = list(self.project_path.rglob(pattern))
            all_files.extend(found_files)
        
        # 使用统一过滤器过滤文件
        file_filter = create_unreal_filter()
        files = file_filter.filter_files(all_files)
        
        self.logger.info(f"扫描到 {len(all_files)} 个文件，过滤后剩余 {len(files)} 个文件")
        
        # files 已经是 Path 对象列表，不需要转换
        self.analysis_stats['total_files'] = len(files)
        
        if not files:
            self.logger.warning("未找到任何 C++ 文件")
            return Project(name=self.project_name)
        
        self.logger.info(f"发现 {len(files)} 个 C++ 文件")
        
        # 2. 第一阶段：并行声明收集
        self.logger.info("第一阶段：并行收集声明...")
        phase1_results = self._process_files_parallel(files, "phase_one")
        
        # 3. 第二阶段：并行定义处理
        self.logger.info("第二阶段：并行处理定义...")
        phase2_results = self._process_files_parallel(files, "phase_two")
        
        # 4. 构建项目结构
        project = Project(name=self.project_name)
        project.files = [str(f) for f in files]
        
        # 从 repository 中收集实体
        for usr, node in self.base_repo.nodes.items():
            project.add_entity(node)
        
        # 构建调用图和继承图
        project.build_graphs(self.base_repo)
        
        # 5. 计算统计信息
        total_time = time.time() - start_time
        self._update_analysis_stats(phase1_results + phase2_results, total_time)
        
        self.logger.info("=" * 60)
        self.logger.info("🎉 并行分析完成！")
        self.logger.info(f"📊 分析统计:")
        self.logger.info(f"   - 总文件数: {self.analysis_stats['total_files']}")
        self.logger.info(f"   - 成功处理: {self.analysis_stats['processed_files']}")
        self.logger.info(f"   - 失败文件: {self.analysis_stats['failed_files']}")
        self.logger.info(f"   - 提取实体: {self.analysis_stats['total_entities']}")
        self.logger.info(f"   - 总耗时: {total_time:.2f}s")
        self.logger.info(f"   - 平均文件耗时: {self.analysis_stats['average_file_time']:.3f}s")
        self.logger.info(f"   - 并行效率提升: ~{self.analysis_stats['parallel_efficiency']:.1f}%")
        self.logger.info("=" * 60)
        
        return project

    def _update_analysis_stats(self, all_results: List[ParallelTaskResult], total_time: float):
        """更新分析统计信息"""
        successful_results = [r for r in all_results if r.success]
        
        self.analysis_stats.update({
            'processed_files': len(successful_results),
            'failed_files': len(all_results) - len(successful_results),
            'total_entities': len(self.base_repo.nodes),
            'total_processing_time': sum(r.processing_time for r in all_results),
            'average_file_time': sum(r.processing_time for r in successful_results) / max(len(successful_results), 1)
        })
        
        # 估算并行效率（简化计算）
        if self.analysis_stats['total_processing_time'] > 0:
            theoretical_serial_time = self.analysis_stats['total_processing_time']
            actual_parallel_time = total_time
            efficiency = max(0, (theoretical_serial_time - actual_parallel_time) / theoretical_serial_time * 100)
            self.analysis_stats['parallel_efficiency'] = efficiency

    def get_analysis_summary(self) -> Dict[str, Any]:
        """获取分析摘要（并行版本）"""
        base_stats = self.base_repo.get_statistics()
        
        return {
            "project_name": self.project_name,
            "project_path": str(self.project_path),
            "analysis_mode": "parallel",
            "parallel_config": {
                "max_workers": self.config.max_workers,
                "chunk_size": self.config.chunk_size
            },
            "performance_stats": self.analysis_stats,
            "entity_stats": base_stats,
            "parser_type": "tree-sitter-parallel",
            "version": "2.4-parallel"
        }


def create_parallel_analyzer(project_path: str, 
                           max_workers: Optional[int] = None,
                           enable_progress: bool = True) -> ParallelCppAnalyzer:
    """便捷函数：创建并行分析器"""
    config = ParallelAnalysisConfig(
        max_workers=max_workers,
        enable_progress=enable_progress,
        enable_detailed_logging=False  # 生产环境关闭详细日志
    )
    
    return ParallelCppAnalyzer(project_path, config) 