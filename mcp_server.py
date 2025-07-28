#!/usr/bin/env python3
"""
MCP (Model Context Protocol) Server for CocoIndex Code Analysis

This server provides semantic search and analysis capabilities for codebases,
designed to work with AI coding assistants and IDEs.

Supports both stdio and SSE transports following MCP specification.
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time
import re
import platform
from typing import Dict, List, Optional, Any, Union
import argparse
import uuid
from numpy.typing import NDArray
import numpy as np
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import psycopg_pool
from psycopg import sql
from functools import lru_cache
from sentence_transformers import SentenceTransformer
from fastmcp import FastMCP

# 在Windows上设置正确的事件循环策略，解决 psycopg 异步连接问题
if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 添加python目录到路径，以便导入cocoindex
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

import cocoindex
from cocoindex import flow, lib, setting, setup
from cocoindex.cli import (
    _load_user_app, _get_app_ref_from_specifier
)

# 添加数据库相关导入
from psycopg_pool import ConnectionPool
from pgvector.psycopg import register_vector
from psycopg import sql, errors as psycopg_errors

# 性能监控类
class PerformanceMonitor:
    """一个用于性能监控和日志记录的上下文管理器（异步兼容）"""
    def __init__(self, name: str, logger: logging.Logger, request_id: str, log_memory: bool = False):
        self.name = name
        self.logger = logger
        self.request_id = request_id
        self.start_time = None
        self.checkpoints = []
        self.last_checkpoint_time = None
        self.memory_usages = []
        self.log_memory = log_memory
        
    async def __aenter__(self):
        """异步进入上下文，记录开始时间"""
        self.start_time = time.perf_counter()
        self.logger.debug(f"⏱️ [{self.request_id}] 开始执行: {self.name}")
        if self.log_memory:
            await self.memory_checkpoint("初始化")
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步退出上下文，记录总耗时和详细步骤"""
        if self.start_time is None:
            return
            
        total_time = time.perf_counter() - self.start_time
        self.logger.debug(f"✅ [{self.request_id}] 完成执行: {self.name} (总耗时: {total_time:.6f}秒)")

        if self.checkpoints:
            # 性能统计分析
            self.logger.debug(f"📊 [{self.request_id}] 性能分析详情:")
            for i, (name, duration) in enumerate(self.checkpoints):
                percentage = (duration / total_time * 100) if total_time > 0 else 0
                self.logger.debug(f"    {i+1:2d}. {name}: {duration:.6f}秒 ({percentage:.1f}%)")
            
            # 性能优化建议
            if self.checkpoints:
                max_step = max(self.checkpoints, key=lambda x: x[1])
                min_step = min(self.checkpoints, key=lambda x: x[1]) 
                avg_step = sum(duration for _, duration in self.checkpoints) / len(self.checkpoints) if len(self.checkpoints) > 0 else 0
                
                self.logger.debug(f"📈 [{self.request_id}] 性能概览:")
                self.logger.debug(f"    🐌 最慢步骤: {max_step[0]} ({max_step[1]:.6f}秒)")
                self.logger.debug(f"    ⚡ 最快步骤: {min_step[0]} ({min_step[1]:.6f}秒)")
                self.logger.debug(f"    📊 平均耗时: {avg_step:.6f}秒")
                
                # 效率比计算
                if min_step[1] > 0:
                    efficiency_ratio = max_step[1] / min_step[1]
                    self.logger.debug(f"    ⚡ 效率比: {efficiency_ratio:.1f}x (最慢/最快)")
                else:
                    self.logger.debug(f"    ⚡ 效率比: 无法计算（最快步骤耗时为0）")
                
                # 相邻步骤对比
                for i in range(1, len(self.checkpoints)):
                    current_step = self.checkpoints[i]
                    prev_step = self.checkpoints[i-1]
                    if prev_step[1] > 0:
                        change_ratio = current_step[1] / prev_step[1]
                        change_desc = "加速" if change_ratio < 1 else "减速"
                        self.logger.debug(f"    🔄 与上步比: {current_step[0]} {change_desc} {change_ratio:.1f}x")

    def __enter__(self):
        """同步进入上下文，记录开始时间"""
        self.start_time = time.perf_counter()
        self.logger.debug(f"⏱️ [{self.request_id}] 开始执行 (同步): {self.name}")
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """同步退出上下文，记录总耗S时"""
        if self.start_time is None:
            return
        total_time = time.perf_counter() - self.start_time
        self.logger.debug(f"✅ [{self.request_id}] 完成执行 (同步): {self.name} (总耗it: {total_time:.6f}秒)")

    async def checkpoint(self, name: str, extra_info: str = ""):
        """添加性能检查点 - 增强版"""
        if self.start_time is not None:
            current_time = time.time()
            
            # 计算与上一个检查点的时间差
            if self.checkpoints:
                last_checkpoint_total = sum(prev_time for _, prev_time in self.checkpoints)
                duration = current_time - self.start_time - last_checkpoint_total
            else:
                duration = current_time - self.start_time
            
            # 计算与上一个检查点的间隔
            step_duration = current_time - self.last_checkpoint_time if self.last_checkpoint_time else duration
            self.last_checkpoint_time = current_time
            
            self.checkpoints.append((name, duration))
            
            # 基本DEBUG日志
            self.logger.debug(f"🔍 [{self.request_id}] {name}: {duration:.6f}秒")
            
            # 详细DEBUG日志
            if extra_info:
                self.logger.debug(f"💡 [{self.request_id}] {name} - {extra_info}")
            
            # 实时性能指标
            total_elapsed = current_time - self.start_time
            checkpoint_percentage = (duration / total_elapsed) * 100 if total_elapsed > 0 else 0
            
            self.logger.debug(f"📏 [{self.request_id}] {name} - 实时指标:")
            self.logger.debug(f"    ⏱️  步骤耗时: {step_duration:.6f}秒")
            self.logger.debug(f"    📊 累计占比: {checkpoint_percentage:.2f}%")
            self.logger.debug(f"    🕐 总经过时间: {total_elapsed:.6f}秒")
    
    async def memory_checkpoint(self, name: str, memory_info: Optional[dict] = None):
        """记录内存检查点（异步）"""
        try:
            import psutil
            process = psutil.Process(os.getpid())
            mem_info = process.memory_info()
            extra_info = f"RSS: {mem_info.rss / 1024**2:.2f} MB, VMS: {mem_info.vms / 1024**2:.2f} MB"
            if memory_info:
                extra_info += f", 额外信息: {memory_info}"
            await self.checkpoint(name, extra_info)
        except ImportError:
            await self.checkpoint(name, "内存监控不可用（需要psutil）")
        except Exception as e:
            await self.checkpoint(name, f"内存监控失败: {e}")


class QueryCache:
    """一个带TTL（生存时间）的简单线程安全LRU查询缓存"""
    def __init__(self, maxsize: int = 128, ttl: int = 300):
        self._cache = {}
        self._order = []
        self._lock = threading.Lock()
        self.maxsize = maxsize
        self.ttl = ttl

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                value, timestamp = self._cache[key]
                if time.time() - timestamp < self.ttl:
                    # 更新访问顺序
                    self._order.remove(key)
                    self._order.append(key)
                    return value
                else:
                    # TTL过期，删除
                    self._remove_entry(key)
        return None

    def set(self, key: str, value: Any):
        with self._lock:
            if key in self._cache:
                self._order.remove(key)
            elif len(self._cache) >= self.maxsize:
                # 缓存已满，移除最旧的
                oldest_key = self._order.pop(0)
                self._remove_entry(oldest_key)
            
            self._cache[key] = (value, time.time())
            self._order.append(key)

    def _remove_entry(self, key: str):
        if key in self._cache:
            del self._cache[key]
            if key in self._order:
                self._order.remove(key)

# 全局共享的SentenceTransformer模型，避免重复初始化
_shared_sentence_transformer = None
_transformer_lock = threading.Lock()

def get_shared_sentence_transformer():
    """获取共享的SentenceTransformer模型，避免重复初始化"""
    global _shared_sentence_transformer
    
    if _shared_sentence_transformer is None:
        with _transformer_lock:
            if _shared_sentence_transformer is None:  # 双重检查锁定
                try:
                    import sentence_transformers
                    print("🤖 正在初始化共享的SentenceTransformer模型...")
                    _shared_sentence_transformer = sentence_transformers.SentenceTransformer(
                        "sentence-transformers/all-MiniLM-L6-v2"
                    )
                    print("✅ SentenceTransformer模型初始化完成")
                except ImportError as e:
                    raise ImportError(
                        "sentence_transformers is required. Install it with: pip install sentence-transformers"
                    ) from e
    
    return _shared_sentence_transformer

@lru_cache(maxsize=1024)
def get_cached_embedding(query: str) -> List[float]:
    """获取缓存的向量嵌入，使用共享模型避免重复初始化"""
    model = get_shared_sentence_transformer()
    result = model.encode(query, convert_to_numpy=True, show_progress_bar=False)
    return result.tolist() # 确保返回列表

# 全局变量用于跟踪进度
processing_stats = {
    "files_processed": 0,
    "chunks_processed": 0,
    "embeddings_created": 0,
    "current_file": ""
}
stats_lock = threading.Lock()

class ProgressMonitor:
    """监控cocoindex处理进度的类"""
    
    def __init__(self, db_url: str, table_name: str):
        self.db_url = db_url
        self.table_name = table_name
        self.actual_table_name = None  # 缓存实际找到的表名
        self.running = False
        self.thread = None
        self.start_time = None
        self.last_count = 0
        
    def start(self):
        """开始监控进度"""
        self.running = True
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._monitor, daemon=True)
        self.thread.start()
        print("📊 进度监控已启动...")
        
    def stop(self):
        """停止监控进度"""
        self.running = False
        if self.thread:
            self.thread.join()
             
    def _monitor(self):
        """监控进程的主循环"""
        try:
            # 初始化时查找实际表名
            if not self.actual_table_name:
                found_table_name = find_actual_table_name(self.db_url, self.table_name)
                if found_table_name:
                    self.actual_table_name = found_table_name
                    print(f"📊 监控器找到实际表名: {self.actual_table_name}")
                else:
                    self.actual_table_name = self.table_name
                    print(f"📊 监控器使用配置表名: {self.table_name}")
            
            while self.running:
                try:
                    # 使用优化的连接池配置连接数据库检查记录数
                    with ConnectionPool(
                        self.db_url, 
                        open=True,
                        max_size=20,  # 监控器使用较小的连接池
                        min_size=2,
                        timeout=30.0
                    ) as pool:
                        with pool.connection() as conn:
                            with conn.cursor() as cur:
                                # 检查总记录数
                                try:
                                    cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(self.actual_table_name)))
                                    total_count = cur.fetchone()
                                    if total_count:
                                        total_count = total_count[0]
                                    else:
                                        total_count = 0
                                    
                                    # 检查不同文件数
                                    cur.execute(sql.SQL("SELECT COUNT(DISTINCT filename) FROM {}").format(sql.Identifier(self.actual_table_name)))
                                    file_count = cur.fetchone()
                                    if file_count:
                                        file_count = file_count[0]
                                    else:
                                        file_count = 0
                                    
                                    # 计算处理速度
                                    elapsed = time.time() - (self.start_time or time.time())
                                    new_records = total_count - self.last_count
                                    
                                    if new_records > 0:
                                        speed = new_records / elapsed if elapsed > 0 else 0
                                        print(f"📈 处理进度: {total_count} 个代码块 | {file_count} 个文件 | "
                                              f"速度: {speed:.1f} 块/秒 | 运行时间: {elapsed:.1f}秒")
                                        self.last_count = total_count
                                        self.start_time = time.time()  # 重置计时器
                                except psycopg_errors.UndefinedTable:
                                    # 表尚不存在，静默等待（这是正常情况）
                                    pass
                                except Exception as inner_e:
                                    import traceback
                                    error_str = str(inner_e).lower()
                                    if 'pool timed out' in error_str or 'connection' in error_str:
                                        print(f"⚠️  数据库连接超时，正在重试...")
                                    else:
                                        print(f"⚠️  查询错误: {inner_e}")
                    
                except Exception as e:
                    # 处理不同类型的错误
                    error_str = str(e).lower()
                    if "does not exist" in error_str or "undefined" in error_str:
                        # 表不存在，静默等待
                        pass
                    elif "pool timed out" in error_str or "connection" in error_str:
                        print(f"⚠️  监控连接超时: {e}")
                    elif "couldn't get a connection" in error_str:
                        print(f"⚠️  监控器无法获取数据库连接，连接池可能已满")
                    else:
                        print(f"⚠️  监控错误: {e}")
                
                # 每5秒检查一次
                time.sleep(5)
                
        except Exception as e:
            print(f"❌ 进度监控异常: {e}")


def reset_progress_stats():
    """重置进度统计"""
    with stats_lock:
        processing_stats["files_processed"] = 0
        processing_stats["chunks_processed"] = 0
        processing_stats["embeddings_created"] = 0
        processing_stats["current_file"] = ""


def parse_cocoindex_stats(stats_str: str) -> Dict[str, int]:
    """解析cocoindex返回的统计字符串"""
    result = {
        "total_files": 0,
        "failed_files": 0,
        "no_change_files": 0,
        "success_files": 0,
        "failed_rate": 0.0
    }
    
    try:
        # 解析类似 "files: 86505 source rows FAILED; 7274 source rows NO CHANGE" 的字符串
        stats_lower = str(stats_str).lower()
        
        # 提取失败数量
        import re
        failed_match = re.search(r'(\d+)\s+source\s+rows\s+failed', stats_lower)
        if failed_match:
            result["failed_files"] = int(failed_match.group(1))
        
        # 提取无变化数量
        no_change_match = re.search(r'(\d+)\s+source\s+rows\s+no\s+change', stats_lower)
        if no_change_match:
            result["no_change_files"] = int(no_change_match.group(1))
        
        # 计算总文件数和成功数
        result["total_files"] = result["failed_files"] + result["no_change_files"]
        result["success_files"] = result["no_change_files"]  # NO CHANGE通常表示已成功处理过
        
        # 计算失败率
        if result["total_files"] > 0:
            result["failed_rate"] = result["failed_files"] / result["total_files"]
        
        return result
    except Exception as e:
        print(f"❌ 解析统计信息失败: {e}")
        return result


def get_connection_pool_stats(pool) -> Dict[str, Any]:
    """获取连接池统计信息"""
    try:
        if hasattr(pool, '_pool'):
            return {
                "pool_size": getattr(pool._pool, 'size', 'unknown'),
                "available_connections": getattr(pool._pool, '_nconns_open', 'unknown'),
                "waiting_requests": getattr(pool._pool, '_nconns_waiting', 'unknown'),
                "max_size": getattr(pool, '_max_size', 'unknown'),
                "min_size": getattr(pool, '_min_size', 'unknown')
            }
    except Exception:
        pass
    return {"status": "stats_unavailable"}


def find_actual_table_name(db_url: str, expected_table_name: str) -> Optional[str]:
    """动态查找实际的数据库表名，因为cocoindex可能添加前缀"""
    try:
        import psycopg
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                # 首先检查原始表名
                cur.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_name = %s",
                    (expected_table_name,)
                )
                if cur.fetchone():
                    return expected_table_name
                
                # 如果不存在，查找包含期望表名的所有表
                cur.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_name LIKE %s",
                    (f'%{expected_table_name}%',)
                )
                tables = cur.fetchall()
                
                if tables:
                    # 返回第一个匹配的表名
                    actual_table = tables[0][0]
                    print(f"🔍 找到实际表名: {actual_table} (期望: {expected_table_name})")
                    return actual_table
                
                return None
    except Exception as e:
        print(f"❌ 查找表名时出错: {e}")
        return None


def get_all_cocoindex_tables(db_url: str) -> List[str]:
    """获取所有cocoindex相关的表"""
    try:
        import psycopg
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_name LIKE '%embedding%' OR table_name LIKE '%cocoindex%'"
                )
                tables = [row[0] for row in cur.fetchall()]
                return tables
    except Exception as e:
        print(f"❌ 获取表列表时出错: {e}")
        return []


def extract_extension(filename: str) -> str:
    """Extract the extension of a filename."""
    return os.path.splitext(filename)[1]


def track_file_progress(filename: str) -> str:
    """跟踪文件处理进度"""
    with stats_lock:
        processing_stats["files_processed"] += 1
        processing_stats["current_file"] = filename
        print(f"📄 正在处理文件 #{processing_stats['files_processed']}: {os.path.basename(filename)}")
    return filename


def track_chunk_progress(text: str) -> str:
    """跟踪代码块处理进度"""
    # with stats_lock:
    #     processing_stats["chunks_processed"] += 1
    #     if processing_stats["chunks_processed"] % 1000 == 0:  # 每10个块显示一次
    #         print(f"✂️  已分割 {processing_stats['chunks_processed']} 个代码块")
    return text


def track_embedding_progress(embedding: NDArray[np.float32]) -> NDArray[np.float32]:
    """跟踪嵌入生成进度"""
    # with stats_lock:
    #     processing_stats["embeddings_created"] += 1
    #     if processing_stats["embeddings_created"] % 1000 == 0:  # 每10个嵌入显示一次
    #         print(f"🤖 已生成 {processing_stats['embeddings_created']} 个向量嵌入")
    return embedding


# 定义嵌入转换流程（从examples/main.py复制）
@cocoindex.transform_flow()
def code_to_embedding(
    text: cocoindex.DataSlice[str],
) -> cocoindex.DataSlice[Any]:
    """
    将文本嵌入为向量，用于语义搜索
    """
    return text.transform(
        cocoindex.functions.SentenceTransformerEmbed(
            model="sentence-transformers/all-MiniLM-L6-v2"
        )
    )


@cocoindex.flow_def(name="CodeEmbedding")
def code_embedding_flow(
    flow_builder: cocoindex.FlowBuilder, data_scope: cocoindex.DataScope
) -> None:
    """
    Define an example flow that embeds files into a vector database with progress tracking.
    """
    print("🔍 正在扫描源文件...")
    path = "D:/c7_i9_EngineDev/Client"
    included_patterns = ["*.cpp", "*.h", "*.hpp", "*.c"]
    excluded_patterns = ["**/.*", "target", "**/node_modules", "**/Binaries", "**/DerivedDataCache", "**/Intermediate", "**/Saved", "**/Build", "**/Content"]
    
    print("=" * 60)
    print("🔧 Flow配置调试信息:")
    print(f"📂 扫描路径: {path}")
    print(f"📄 包含文件类型: {included_patterns}")
    print(f"🚫 排除模式: {excluded_patterns}")
    print(f"🏷️  Flow名称: CodeEmbedding")
    print("=" * 60)
    
    data_scope["files"] = flow_builder.add_source(
        cocoindex.sources.LocalFile(
            path=path,
            included_patterns=included_patterns,
            excluded_patterns=excluded_patterns,
        )
    )
    print("📋 处理的文件路径: ", path)
    print("🔍 包含的文件类型: ", included_patterns)
    
    print("📊 开始处理文件并收集数据...")
    code_embeddings = data_scope.add_collector()

    with data_scope["files"].row() as file:
        # 简化的文件处理，不使用transform
        file["extension"] = file["filename"].transform(
            cocoindex.functions.SplitRecursively(),
            language=".cpp",  # 固定使用cpp语言处理
            chunk_size=1000,
            min_chunk_size=300,
            chunk_overlap=300,
        )
        
        file["chunks"] = file["content"].transform(
            cocoindex.functions.SplitRecursively(),
            language=".cpp",
            chunk_size=1000,
            min_chunk_size=300,
            chunk_overlap=300,
        )
        
        with file["chunks"].row() as chunk:
            # 生成嵌入
            chunk["embedding"] = chunk["text"].call(code_to_embedding)
            
            code_embeddings.collect(
                filename=file["filename"],
                location=chunk["location"],
                code=chunk["text"],
                embedding=chunk["embedding"],
                start=chunk["start"],
                end=chunk["end"],
            )

    print("💾 正在导出到PostgreSQL数据库...")
    
    # 从环境变量获取表名，与服务器配置保持一致
    table_name = os.environ.get("COCOINDEX_DATABASE_TABLE", "c7_client_code_embeddings")
    print(f"✅ 将导出到表: {table_name}")

    code_embeddings.export(
        table_name,
        cocoindex.targets.Postgres(),
        primary_key_fields=["filename", "location"],
        vector_indexes=[
            cocoindex.VectorIndexDef(
                field_name="embedding",
                metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY,
            )
        ],
    )
    print("✅ 流程定义完成！")


def extract_line_number(position_obj) -> int:
    """从位置对象中安全地提取行号"""
    if isinstance(position_obj, dict):
        return position_obj.get('line', 1)  # 默认返回第1行
    elif isinstance(position_obj, (int, float)):
        return int(position_obj)
    else:
        return 1  # 默认值


class ImprovedCodeSearch:
    """改进的代码搜索引擎，优化了性能"""
    
    def __init__(self, db_pool: psycopg_pool.AsyncConnectionPool, table_name: str, logger: Optional[logging.Logger] = None, flow_name: Optional[str] = None):
        """
        初始化搜索引擎。

        :param db_pool: 一个 `psycopg_pool.AsyncConnectionPool` 实例。
        :param table_name: 主表名（不带前缀）。
        :param logger: 日志记录器实例。
        :param flow_name: 工作流名称，用于确定表名前缀。
        """
        self.db_pool = db_pool
        self.table_name = table_name
        self.flow_name = flow_name
        self.logger = logger or logging.getLogger(__name__)
        self.query_cache = QueryCache(maxsize=128, ttl=300)
        
        # 根据是否提供 flow_name 来确定实际表名
        # CocoIndex 使用格式: {flow_name.lower()}__{table_name}
        if self.flow_name:
            # 特殊处理：如果是 "gmzz" 但实际表是 "codeembedding__" 开头的，则使用 "codeembedding"
            if self.flow_name.lower() == "gmzz":
                self.actual_table_name = f"codeembedding__{self.table_name}"
            else:
                self.actual_table_name = f"{self.flow_name.lower()}__{self.table_name}"
        else:
            self.actual_table_name = self.table_name
            
        self.schema_name = None  # CocoIndex 不使用 PostgreSQL schema 分离
        self.logger.info(f"搜索引擎已配置，将使用表: {self.actual_table_name}")

    async def initialize_table_name(self):
        """验证表名是否存在并可访问"""
        self.logger.info(f"验证表名: {self.actual_table_name}")

        # 连接数据库验证表是否存在
        try:
            async with self.db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    # 直接使用表名，不需要 schema 分离
                    identifier = sql.Identifier(self.actual_table_name)
                    await cur.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(identifier))
            self.logger.info(f"✅ 成功验证表 '{self.actual_table_name}' 存在。")
        except Exception:
            self.logger.warning(
                f"⚠️ 表 '{self.actual_table_name}' 可能不存在或无法访问。将在第一次查询时处理此问题。",
                exc_info=False
            )
    
    async def _execute_query(self, query: sql.Composable, params: Optional[dict], monitor: PerformanceMonitor, fetch: str = "all"):
        """
        执行数据库查询并进行性能分析。

        :param query: 要执行的SQL查询（psycopg.sql对象）。
        :param params: 查询参数。
        :param monitor: PerformanceMonitor 实例。
        :param fetch: 'all', 'one', or 'none'
        :return: 查询结果或None。
        """
        results = []
        await monitor.checkpoint("开始数据库查询")
        try:
            # 使用 `async with` 来自动处理连接的获取和释放
            async with self.db_pool.connection() as conn:
                await monitor.checkpoint("数据库连接已获取")
                async with conn.cursor() as cur:
                    start_query_time = time.perf_counter()
                    await cur.execute(query, params) # type: ignore
                    query_duration = time.perf_counter() - start_query_time
                    self.logger.debug(f"⚡️ 数据库原生查询耗时: {query_duration:.6f}秒")
                    
                    if fetch == "all":
                        results = await cur.fetchall()
                    elif fetch == "one":
                        results = await cur.fetchone()
                    # 如果 fetch == 'none'，不获取结果

            await monitor.checkpoint("数据库查询完成")
            return results
        except Exception as e:
            self.logger.error(f"数据库查询失败: {e}", exc_info=True)
            await monitor.checkpoint(f"数据库查询失败: {e}")
            return None # 或者可以重新抛出异常
    
    async def exact_search(self, query: str, limit: int = 5, request_id: Optional[str] = None) -> List[dict]:
        """执行精确匹配搜索"""
        request_id = request_id or str(uuid.uuid4())[:8]
        self.logger.debug(f"🎯 [{request_id}] 开始精确搜索: query='{query}'")
        async with PerformanceMonitor("精确搜索", self.logger, request_id) as monitor:
            # 动态构建表标识符
            sql_query = sql.SQL("""
                SELECT filename, code, score, start, "end"
                FROM (
                    SELECT
                        filename,
                        code,
                        ts_rank_cd(to_tsvector('simple', code), plainto_tsquery('simple', %(query)s)) as score,
                        start,
                        "end"
                    FROM {table}
                    WHERE to_tsvector('simple', code) @@ plainto_tsquery('simple', %(query)s)
                ) as ranked_results
                WHERE score > 0
                ORDER BY score DESC
                LIMIT %(limit)s;
            """).format(table=sql.Identifier(self.actual_table_name))
            
            params = {"query": query, "limit": limit}
            
            results = await self._execute_query(sql_query, params, monitor, fetch="all")
            
            if results is None: return []

            formatted_results = [
                {"filename": row[0], "code": row[1], "score": row[2], 
                 "start_line": extract_line_number(row[3]), "end_line": extract_line_number(row[4])}
                for row in results
            ]
            self.logger.debug(f"🎯 [{request_id}] 精确搜索最终结果: {len(formatted_results)} 个精确匹配项")
            return formatted_results
    
    async def fuzzy_search(self, query: str, limit: int = 5, request_id: Optional[str] = None) -> List[dict]:
        """执行模糊匹配搜索 (ILIKE with trigram)"""
        request_id = request_id or str(uuid.uuid4())[:8]
        self.logger.debug(f"🎯 [{request_id}] 开始模糊搜索: query='{query}'")
        async with PerformanceMonitor("模糊搜索", self.logger, request_id) as monitor:
            # 动态构建表标识符
            sql_query = sql.SQL("""
                SELECT filename, code, similarity(code, %(query)s) as score, start, "end"
                FROM {table}
                WHERE code %% %(query)s
                ORDER BY score DESC
                LIMIT %(limit)s;
            """).format(table=sql.Identifier(self.actual_table_name))
            
            params = {"query": query, "limit": limit}

            results = await self._execute_query(sql_query, params, monitor, fetch="all")

            if results is None: return []

            formatted_results = [
                {"filename": row[0], "code": row[1], "score": row[2], 
                 "start_line": extract_line_number(row[3]), "end_line": extract_line_number(row[4])}
                for row in results
            ]
            self.logger.debug(f"🎯 [{request_id}] 模糊搜索最终结果: {len(formatted_results)} 个模糊匹配项")
            return formatted_results
    
    async def semantic_search(self, query: str, limit: int = 5, request_id: Optional[str] = None) -> List[dict]:
        """执行语义（向量）搜索"""
        request_id = request_id or str(uuid.uuid4())[:8]
        self.logger.debug(f"🎯 [{request_id}] 开始语义搜索: query='{query}'")
        async with PerformanceMonitor("语义搜索", self.logger, request_id) as monitor:
            await monitor.checkpoint("开始向量嵌入计算")
            start_embed_time = time.perf_counter()
            try:
                # 这个函数是同步的，但在后台是线程安全的
                query_embedding = get_cached_embedding(query)
                api_call_duration = time.perf_counter() - start_embed_time
                await monitor.checkpoint(f"向量嵌入计算完成，耗时: {api_call_duration:.6f}s")
                
                self.logger.debug(f"💡 [{request_id}] 向量嵌入计算 - 查询长度: {len(query)}, 向量维度: {len(query_embedding)}, API调用耗时: {api_call_duration:.6f}秒")
            except Exception as e:
                self.logger.error(f"❌ [{request_id}] 向量嵌入计算失败: {e}", exc_info=True)
                await monitor.checkpoint("向量嵌入计算失败")
                return []

            # 动态构建表标识符
            sql_query = sql.SQL("""
                SELECT filename, code, (1 - (embedding <=> %(embedding)s)) as score, start, "end"
                FROM {table}
                ORDER BY score DESC
                LIMIT %(limit)s;
            """).format(table=sql.Identifier(self.actual_table_name))

            params = {"embedding": str(query_embedding), "limit": limit}
            
            results = await self._execute_query(sql_query, params, monitor, fetch="all")

            if results is None: return []

            formatted_results = [
                {"filename": row[0], "code": row[1], "score": row[2], 
                 "start_line": extract_line_number(row[3]), "end_line": extract_line_number(row[4])}
                for row in results
            ]
            self.logger.debug(f"🎯 [{request_id}] 语义搜索最终结果: {len(formatted_results)} 个语义匹配项")
            return formatted_results
    
    async def hybrid_search(self, query: str, search_type: str, top_k: int = 5) -> List[dict]:
        hybrid_start_time = time.perf_counter()
        request_id = str(uuid.uuid4())[:8]
        self.logger.info(f"⚡️ [{request_id}] 开始混合搜索: query='{query}', type='{search_type}', k={top_k}")
        
        cache_key = f"{search_type}:{query}:{top_k}:{self.actual_table_name}"
        cached_result = self.query_cache.get(cache_key)
        if cached_result is not None:
            cache_time = time.perf_counter() - hybrid_start_time
            self.logger.info(f"✅ [{request_id}] 从缓存中返回结果 (缓存命中耗时: {cache_time:.3f}秒)")
            return cached_result

        async with PerformanceMonitor(f"混合搜索 ({search_type})", self.logger, request_id) as monitor:
            exact_results, fuzzy_results, semantic_results = [], [], []

            if search_type in ["advanced", "all"]:
                await monitor.checkpoint("开始并行搜索")
                tasks = [
                    self.exact_search(query, top_k, request_id),
                    self.fuzzy_search(query, top_k, request_id),
                    self.semantic_search(query, top_k, request_id)
                ]
                results_from_gather = await asyncio.gather(*tasks, return_exceptions=True)
                await monitor.checkpoint("并行搜索完成")

                # 安全地解包结果并记录错误
                if isinstance(results_from_gather[0], BaseException):
                    self.logger.error(f"❌ [{request_id}] 并行精确搜索失败: {results_from_gather[0]}")
                    exact_results = []
                else:
                    exact_results = results_from_gather[0]

                if isinstance(results_from_gather[1], BaseException):
                    self.logger.error(f"❌ [{request_id}] 并行模糊搜索失败: {results_from_gather[1]}")
                    fuzzy_results = []
                else:
                    fuzzy_results = results_from_gather[1]
                
                if isinstance(results_from_gather[2], BaseException):
                    self.logger.error(f"❌ [{request_id}] 并行语义搜索失败: {results_from_gather[2]}")
                    semantic_results = []
                else:
                    semantic_results = results_from_gather[2]
            else:
                if search_type == "exact":
                    exact_results = await self.exact_search(query, top_k, request_id)
                elif search_type == "fuzzy":
                    fuzzy_results = await self.fuzzy_search(query, top_k, request_id)
                elif search_type == "semantic":
                    semantic_results = await self.semantic_search(query, top_k, request_id)

            await monitor.checkpoint("开始结果去重和排序")
            
            all_results = []
            seen = set()
            
            # 优先级: 精确 > 模糊 > 语义
            search_results_list = [
                (exact_results, 'exact'), 
                (fuzzy_results, 'fuzzy'), 
                (semantic_results, 'semantic')
            ]

            for results_list, match_type in search_results_list:
                for result in results_list:
                    # 使用 文件名 + 起始行 作为唯一标识进行去重
                    # 现在start_line已经是简单的整数，可以安全地用作哈希key
                    key = (result.get("filename"), result.get("start_line"))
                    if key not in seen:
                        seen.add(key)
                        # 为结果添加 match_type 以便追溯
                        result_with_type = result.copy()
                        result_with_type['match_type'] = match_type
                        all_results.append(result_with_type)

            # 按分数降序排序
            all_results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            final_results = all_results[:top_k]

            await monitor.checkpoint(f"结果处理完成, 生成 {len(final_results)} 个最终结果")

            # 计算混合搜索总耗时
            hybrid_total_time = time.perf_counter() - hybrid_start_time
            self.logger.info(f"✅ [{request_id}] 混合搜索完成，返回 {len(final_results)} 个结果 (混合搜索耗时: {hybrid_total_time:.3f}秒)")
            self.query_cache.set(cache_key, final_results)
            return final_results
        
    async def advanced_search(self, query: str, top_k: int = 5) -> List[dict]:
        """
        智能高级搜索。根据查询类型决定最佳搜索策略。
        """
        # 简单规则：如果查询看起来像一个精确的标识符，优先精确搜索。
        # 否则，并行运行所有搜索并合并结果。
        # 这是一个可以未来扩展的策略引擎。
        return await self.hybrid_search(query, "all", top_k)


class CocoIndexMcpServer:
    def __init__(
        self,
        flow_name: str,
        host: str = "0.0.0.0",
        port: int = 2010,
        db_url: Optional[str] = None,
        table_name: str = "c7_client_code_embeddings",
        log_level: str = "INFO",
    ):
        self.flow_name = flow_name
        self.db_url = db_url or os.environ.get("DATABASE_URL")
        if not self.db_url:
            raise ValueError("数据库URL未提供，请设置DATABASE_URL环境变量或通过参数传递")
            
        self.table_name = table_name
        self.log_level = log_level
        self.host = host
        self.port = port
        self.db_pool: Optional[psycopg_pool.AsyncConnectionPool] = None
        self.search_engine: Optional[ImprovedCodeSearch] = None

        # 配置日志
        log_format = '%(asctime)s - %(name)s.%(funcName)s - %(levelname)s - %(message)s'
        logging.basicConfig(level=self.log_level.upper(), format=log_format)
        self.logger = logging.getLogger(__name__)

        # 创建FastMCP实例
        self.mcp = FastMCP(name="CocoIndex MCP Server")
        self._setup_tools()

    def _setup_tools(self):
        """设置MCP工具"""
        @self.mcp.tool()
        async def search_code(
            query: str,
            search_type: str = "advanced",
            flow_name: str = "",
            top_k: int = 10,
        ) -> List[dict]:
            """根据自然语言查询在代码库中进行高级混合搜索"""
            request_start_time = time.perf_counter()
            request_id = str(uuid.uuid4())[:8]
            self.logger.info(
                f"📥 [{request_id}] 收到搜索请求: query='{query}', type='{search_type}', k={top_k}"
            )

            if not self.search_engine:
                self.logger.error(f"❌ [{request_id}] 搜索引擎未初始化")
                return [{"error": "Search engine is not initialized."}]

            # 检查是否需要切换flow
            if flow_name and len(flow_name) > 1 and self.flow_name != flow_name:
                self.logger.warning(f"[{request_id}] 请求的 flow_name '{flow_name}' 与服务器初始化的 '{self.flow_name}' 不同。此功能暂不支持动态切换。")
                # 当前设计为每个服务器实例服务一个flow，未来可以扩展
            
            try:
                async with PerformanceMonitor(f"Tool search_code", self.logger, request_id) as monitor:
                    if search_type == "advanced":
                        results = await self.search_engine.advanced_search(query, top_k)
                    else:
                        results = await self.search_engine.hybrid_search(query, search_type, top_k)
                    
                    # 计算总体请求时间
                    total_request_time = time.perf_counter() - request_start_time
                    self.logger.info(f"📤 [{request_id}] 响应搜索请求，返回 {len(results)} 个结果 (总耗时: {total_request_time:.3f}秒)")
                    return results
            except Exception as e:
                total_request_time = time.perf_counter() - request_start_time
                self.logger.error(f"❌ [{request_id}] 执行搜索时发生意外错误: {e} (总耗时: {total_request_time:.3f}秒)", exc_info=True)
                return [{"error": f"An unexpected error occurred: {e}"}]

    async def initialize(self):
        """异步初始化服务器资源"""
        self.logger.info("🚀 服务器正在初始化...")
        # 1. 初始化数据库连接池
        self.logger.info(f"🔌 正在连接数据库: {self.db_url}")
        if not self.db_url:
            raise ValueError("数据库URL不能为空")
            
        try:
            self.db_pool = psycopg_pool.AsyncConnectionPool(self.db_url, min_size=5, max_size=20, open=True)  # type: ignore
            # 预热连接池
            if self.db_pool:
                async with self.db_pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT 1;")
                        self.logger.info("✅ 数据库连接池初始化成功")
        except Exception as e:
            self.logger.error(f"❌ 数据库连接失败: {e}", exc_info=True)
            # 连接失败是致命错误，直接退出
            raise

        # 2. 初始化代码搜索引擎
        if self.db_pool:
            self.search_engine = ImprovedCodeSearch(
                db_pool=self.db_pool,
                table_name=self.table_name,
                logger=self.logger,
                flow_name=self.flow_name,
            )
            await self.search_engine.initialize_table_name()
            self.logger.info(f"✅ 代码搜索引擎已准备就绪 (操作表: {self.search_engine.actual_table_name})")
        else:
            self.logger.error("❌ 数据库连接池未初始化，无法创建搜索引擎。")

        # 3. 优化模糊搜索（确保在 search_engine 初始化之后）
        try:
            await self._ensure_fuzzy_search_index()
        except Exception as e:
            self.logger.error(f"⚠️ 模糊搜索索引优化失败: {e}", exc_info=True)

        # 4. 预加载语义搜索模型，避免首次请求延迟
        try:
            self.logger.info("⏳ 正在预热语义搜索模型 (首次启动可能需要下载)...")
            loop = asyncio.get_event_loop()
            # 在线程池中执行同步的加载函数，避免阻塞事件循环
            async with PerformanceMonitor("预加载SentenceTransformer", self.logger, "startup") as monitor:
                await loop.run_in_executor(
                    None,  # 使用默认的 ThreadPoolExecutor
                    get_shared_sentence_transformer
                )
        except Exception as e:
            self.logger.error(f"❌ 预加载 SentenceTransformer 模型失败: {e}", exc_info=True)
            # 这不是致命错误，服务器可以继续运行，但首次语义搜索会很慢

        self.logger.info("🎉 服务器初始化完成，随时可以接收请求")

    async def cleanup(self):
        """清理资源，在服务器关闭时调用"""
        self.logger.info("👋 服务器正在关闭，清理资源...")
        if self.db_pool:
            await self.db_pool.close()
            self.logger.info("✅ 数据库连接池已关闭")

    async def _ensure_fuzzy_search_index(self):
        """检查并创建用于模糊搜索的pg_trgm GIN索引"""
        if self.search_engine and self.search_engine.actual_table_name and self.db_pool:
            self.logger.info("🔧 正在检查并创建模糊搜索优化索引 (pg_trgm)...")
            table_to_index = self.search_engine.actual_table_name

            async with self.db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    # 1. 启用 pg_trgm 扩展
                    await cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
                    self.logger.info("✅ pg_trgm 扩展已启用")

                    # 2. 检查表是否存在
                    await cur.execute(
                        "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                        (table_to_index,)
                    )
                    table_exists = await cur.fetchone()
                    
                    if not table_exists:
                        self.logger.warning(f"⚠️ 表 '{table_to_index}' 不存在，跳过创建索引")
                        return

                    # 3. 在 code 列上创建 GIN 索引
                    # 清理表名中的特殊字符以生成有效的索引名
                    clean_table_name = table_to_index.replace('__', '_').replace('-', '_')
                    index_name = f"idx_gin_code_trgm_{clean_table_name}"
                    
                    table_identifier = sql.Identifier(table_to_index)

                    try:
                        await cur.execute(sql.SQL("""
                            CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} USING gin (code gin_trgm_ops);
                        """).format(
                            index_name=sql.Identifier(index_name),
                            table_name=table_identifier
                        ))
                        self.logger.info(f"✅ 在 '{table_to_index}' 表上成功创建或验证了 GIN 索引 '{index_name}'")
                    except Exception as index_error:
                        # 如果索引创建失败，记录警告但不中断服务器启动
                        self.logger.warning(f"⚠️ 在 '{table_to_index}' 上创建 GIN 索引失败: {index_error}")
                        self.logger.info("📝 模糊搜索功能仍可使用，但性能可能较慢")
        else:
            self.logger.warning("⚠️ 未能获取到有效的表名或数据库连接池，跳过模糊搜索索引的创建")

    async def run(self, transport: str = "stdio"):
        """运行服务器"""
        await self.initialize()
        self.logger.info(f"🚀 启动 FastMCP 服务器: {transport} 模式")
        if transport == "sse":
            await self.mcp.run_sse_async(port=self.port, host=self.host)
        else:
            await self.mcp.run_stdio_async()

def main():
    """同步主入口函数，用于FastMCP运行"""
    parser = argparse.ArgumentParser(description="CocoIndex MCP Server")
    parser.add_argument("--flow-name", default="gmzz", help="要服务的Flow的名称")
    parser.add_argument("--host", default="0.0.0.0", help="服务器主机地址")
    parser.add_argument("--port", type=int, default=2010, help="服务器端口")
    parser.add_argument("--db-url", default="postgresql://cocoindex:cocoindex@localhost/cocoindex", help="数据库连接URL")
    parser.add_argument("--table-name", default="c7_client_code_embeddings", help="代码嵌入表名")
    parser.add_argument("--transport", default="sse", choices=["sse", "stdio"], help="MCP传输方式")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志记录级别"
    )

    args = parser.parse_args()

    server = CocoIndexMcpServer(
        flow_name=args.flow_name,
        host=args.host,
        port=args.port,
        db_url=args.db_url,
        table_name=args.table_name,
        log_level=args.log_level,
    )

    try:
        asyncio.run(server.run(transport=args.transport))
    except KeyboardInterrupt:
        print("\n👋 服务器已停止")
    finally:
        asyncio.run(server.cleanup())

if __name__ == "__main__":
    main() 