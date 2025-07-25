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
from typing import Dict, List, Optional, Any
import argparse
import uuid
from numpy.typing import NDArray
import numpy as np

# 添加python目录到路径，以便导入cocoindex
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

import cocoindex
from cocoindex import flow, lib, setting
from cocoindex.cli import (
    _load_user_app, _get_app_ref_from_specifier
)

# 导入FastMCP
from fastmcp import FastMCP

# 添加数据库相关导入
from psycopg_pool import ConnectionPool
from pgvector.psycopg import register_vector
from psycopg import sql

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
    
    def __init__(self, db_url: str, table_name: str = "CodeEmbedding__code_embeddings"):
        self.db_url = db_url
        self.table_name = table_name
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
            while self.running:
                try:
                    # 连接数据库检查记录数
                    with ConnectionPool(self.db_url, open=True) as pool:
                        with pool.connection() as conn:
                            with conn.cursor() as cur:
                                # 检查总记录数
                                try:
                                    cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(self.table_name)))
                                    total_count = cur.fetchone()
                                    if total_count:
                                        total_count = total_count[0]
                                    else:
                                        total_count = 0
                                    
                                    # 检查不同文件数
                                    cur.execute(sql.SQL("SELECT COUNT(DISTINCT filename) FROM {}").format(sql.Identifier(self.table_name)))
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
                                except Exception as inner_e:
                                    if "does not exist" not in str(inner_e):
                                        print(f"⚠️  查询错误: {inner_e}")
                    
                except Exception as e:
                    # 如果表还不存在，静默等待
                    if "does not exist" not in str(e):
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
    data_scope["files"] = flow_builder.add_source(
        cocoindex.sources.LocalFile(
            path=path,
            included_patterns=included_patterns,
            excluded_patterns=["**/.*", "target", "**/node_modules", "**/Binaries", "**/DerivedDataCache", "**/Intermediate", "**/Saved", "**/Build", "**/Content"],
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
    code_embeddings.export(
        "code_embeddings",
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


class ImprovedCodeSearch:
    """改进的代码搜索引擎"""
    
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.table_name = "codeembedding__code_embeddings"
        
        # 初始化CocoIndex
        settings = setting.Settings.from_env()
        lib.init(settings)
    
    def exact_search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """精确匹配搜索"""
        results = []
        try:
            import psycopg
            with psycopg.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    search_query = sql.SQL("""
                        SELECT filename, code, start, "end", 'exact' as match_type
                        FROM {} 
                        WHERE code ILIKE %s
                        ORDER BY LENGTH(code) ASC
                        LIMIT %s
                    """).format(sql.Identifier(self.table_name))
                    
                    cur.execute(search_query, (f'%{query}%', limit))
                    rows = cur.fetchall()
                    
                    for row in rows:
                        results.append({
                            "filename": row[0],
                            "code": row[1],
                            "start": row[2],
                            "end": row[3],
                            "match_type": row[4],
                            "score": 1.0,  # 精确匹配给满分
                        })
        except Exception as e:
            print(f"精确搜索错误: {e}")
        
        return results
    
    def fuzzy_search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """模糊匹配搜索（分解查询词）"""
        results = []
        
        # 将查询分解为单词（处理驼峰命名）
        words = re.findall(r'[A-Z][a-z]*|[a-z]+|\d+', query)
        
        if len(words) > 1:
            try:
                import psycopg
                with psycopg.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        # 构建模糊搜索条件
                        conditions = []
                        params = []
                        for word in words:
                            conditions.append("code ILIKE %s")
                            params.append(f'%{word}%')
                        
                        search_query = sql.SQL("""
                            SELECT filename, code, start, "end", 'fuzzy' as match_type
                            FROM {} 
                            WHERE {}
                            ORDER BY LENGTH(code) ASC
                            LIMIT %s
                        """).format(
                            sql.Identifier(self.table_name),
                            sql.SQL(' AND ').join(sql.SQL(cond) for cond in conditions)
                        )
                        
                        params.append(limit)
                        cur.execute(search_query, params)
                        rows = cur.fetchall()
                        
                        for row in rows:
                            # 计算匹配度
                            code = row[1].lower()
                            matched_words = sum(1 for word in words if word.lower() in code)
                            score = matched_words / len(words) * 0.8  # 模糊匹配给80%权重
                            
                            results.append({
                                "filename": row[0],
                                "code": row[1],
                                "start": row[2],
                                "end": row[3],
                                "match_type": row[4],
                                "score": score,
                            })
            except Exception as e:
                print(f"模糊搜索错误: {e}")
        
        return results
    
    def semantic_search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """语义搜索"""
        results = []
        try:
            import psycopg
            from pgvector.psycopg import register_vector
            with psycopg.connect(self.db_url) as conn:
                register_vector(conn)
                with conn.cursor() as cur:
                    # 生成查询向量
                    query_vector = code_to_embedding.eval(query)
                    
                    # 执行向量搜索
                    search_query = sql.SQL("""
                        SELECT filename, code, embedding <=> %s AS distance, start, "end"
                        FROM {} 
                        ORDER BY distance 
                        LIMIT %s
                    """).format(sql.Identifier(self.table_name))
                    
                    cur.execute(search_query, (query_vector, limit))
                    rows = cur.fetchall()
                    
                    for row in rows:
                        similarity = 1.0 - row[2]
                        # 只有相似度够高的才认为是有效结果
                        if similarity > 0.2:  # 降低阈值以获得更多结果
                            results.append({
                                "filename": row[0],
                                "code": row[1],
                                "start": row[3],
                                "end": row[4],
                                "match_type": "semantic",
                                "score": similarity * 0.6,  # 语义搜索权重较低
                            })
        except Exception as e:
            print(f"语义搜索错误: {e}")
        
        return results
    
    def hybrid_search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """混合搜索：结合精确、模糊和语义搜索"""
        # 执行三种搜索
        exact_results = self.exact_search(query, limit)
        fuzzy_results = self.fuzzy_search(query, limit)
        semantic_results = self.semantic_search(query, limit)
        
        # 合并结果，去重
        all_results = []
        seen = set()
        
        # 优先级：精确 > 模糊 > 语义
        for results_list in [exact_results, fuzzy_results, semantic_results]:
            for result in results_list:
                # 使用文件名+位置作为唯一标识，转换dict为字符串
                start_str = str(result["start"])
                end_str = str(result["end"])
                key = (result["filename"], start_str, end_str)
                if key not in seen:
                    seen.add(key)
                    all_results.append(result)
        
        # 按分数排序
        all_results.sort(key=lambda x: x["score"], reverse=True)
        
        return all_results[:limit]


class CocoIndexMcpServer:
    """MCP Server for CocoIndex code analysis using FastMCP."""
    
    def __init__(self, flow_name: str = "gmzz", host: str = "127.0.0.1", port: int = 2010):
        self.flow_name = flow_name
        self.host = host
        self.port = port
        self.logger = logging.getLogger(__name__)
        self._initialized = False
        self.db_pool: Optional[ConnectionPool] = None
        self.search_engine: Optional[ImprovedCodeSearch] = None
        
        self.mcp_server = FastMCP(            
            name="CocoIndex"
        )
        self._setup_tools()
    
    async def initialize(self):
        """初始化MCP服务器"""
        if self._initialized:
            return
            
        self.logger.info("🚀 开始初始化 CocoIndex MCP 服务器...")
            
        try:
            # 初始化CocoIndex
            settings = setting.Settings.from_env()
            lib.init(settings)
            
            # 初始化数据库连接池
            db_url = os.getenv("COCOINDEX_DATABASE_URL")
            if db_url:
                self.db_pool = ConnectionPool(db_url, open=True)
                self.search_engine = ImprovedCodeSearch(db_url)
                self.logger.info("Database connection pool and search engine initialized")
            else:
                self.logger.warning("COCOINDEX_DATABASE_URL not set, database queries will not work")
            
            # 确保flows可用
            try:
                flow.ensure_all_flows_built()
                self.logger.info(f"MCP Server initialized with flow: {self.flow_name}")
            except Exception as e:
                self.logger.warning(f"Could not build flows: {e}")
            
            # 自动初始化代码嵌入流程
            await self._auto_initialize_flow()
            
            self._initialized = True
            self.logger.info("🎉 CocoIndex MCP 服务器初始化完成！")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize MCP server: {e}")
            raise

    async def _auto_initialize_flow(self):
        """自动初始化代码嵌入流程"""
        try:
            self.logger.info("🚀 开始自动初始化代码嵌入流程...")
            
            # 重置进度统计
            reset_progress_stats()
            
            # 启动进度监控
            db_url = os.getenv("COCOINDEX_DATABASE_URL")
            monitor = None
            if db_url:
                monitor = ProgressMonitor(db_url, "codeembedding__code_embeddings")
                monitor.start()
                self.logger.info("📊 进度监控已启动")
            
            # 检查是否已经有数据
            if self.db_pool:
                try:
                    with self.db_pool.connection() as conn:
                        with conn.cursor() as cur:
                            table_name = "codeembedding__code_embeddings"
                            cur.execute(
                                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                                (table_name,)
                            )
                            table_exists = cur.fetchone()
                            
                            if table_exists and table_exists[0]:
                                cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table_name)))
                                count_result = cur.fetchone()
                                existing_count = count_result[0] if count_result else 0
                                
                                if existing_count > 0:
                                    self.logger.info(f"📁 数据库中已有 {existing_count} 条记录，跳过重新索引")
                                    if monitor:
                                        monitor.stop()
                                    return
                except Exception as e:
                    self.logger.debug(f"检查现有数据时出错: {e}")
            
            self.logger.info("⏳ 正在处理文件，这可能需要一些时间...")
            
            try:
                # 更新流程
                stats = code_embedding_flow.update()
                
                # 停止进度监控
                if monitor:
                    monitor.stop()
                
                self.logger.info("✅ 代码嵌入流程初始化成功！")
                self.logger.info(f"📊 处理统计: {stats}")
                
                # 显示最终统计
                if self.db_pool:
                    with self.db_pool.connection() as conn:
                        with conn.cursor() as cur:
                            table_name = "codeembedding__code_embeddings"
                            cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table_name)))
                            total_count = cur.fetchone()
                            count = total_count[0] if total_count else 0
                            
                            cur.execute(sql.SQL("SELECT COUNT(DISTINCT filename) FROM {}").format(sql.Identifier(table_name)))
                            file_count_result = cur.fetchone()
                            file_count = file_count_result[0] if file_count_result else 0
                            
                            self.logger.info(f"✅ 数据库中共有 {count} 条代码块记录")
                            self.logger.info(f"📁 处理了 {file_count} 个不同的文件")
                
            except Exception as e:
                if monitor:
                    monitor.stop()
                self.logger.error(f"❌ 代码嵌入流程初始化失败: {e}")
                # 不抛出异常，让服务器继续运行
                
        except Exception as e:
            self.logger.error(f"自动初始化流程失败: {e}")
            # 不抛出异常，让服务器继续运行

    def _setup_tools(self):
        """设置MCP工具"""
        
        @self.mcp_server.tool()
        async def test_connection() -> Dict[str, Any]:
            """测试CocoIndex连接和服务器状态"""
            # 生成请求ID并记录请求
            request_id = str(uuid.uuid4())[:8]
            self.logger.info(f"🔧 [请求 {request_id}] 工具: test_connection")
            self.logger.info(f"📥 [请求 {request_id}] 参数: 无")
            
            start_time = time.time()
            
            try:
                self.logger.info(f"⚙️  [处理 {request_id}] 正在执行工具: test_connection")
                
                status = {
                    "cocoindex_initialized": self._initialized,
                    "database_connected": bool(self.db_pool),
                    "search_engine_ready": bool(self.search_engine),
                    "flow_name": self.flow_name
                }
                
                # 测试数据库连接
                if self.db_pool:
                    try:
                        with self.db_pool.connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("SELECT 1")
                                result = cur.fetchone()
                                status["database_test"] = "SUCCESS" if result else "FAILED"
                    except Exception as e:
                        status["database_test"] = f"ERROR: {str(e)}"
                else:
                    status["database_test"] = "NO_POOL"
                
                # 检查可用的flows
                try:
                    current_flows = list(flow.flow_names())
                    status["available_flows"] = current_flows
                except Exception as e:
                    status["available_flows"] = f"ERROR: {str(e)}"
                
                # 记录成功响应
                execution_time = time.time() - start_time
                self.logger.info(f"✅ [响应 {request_id}] 工具: test_connection - 执行成功")
                self.logger.info(f"⏱️  [响应 {request_id}] 执行耗时: {execution_time:.3f}秒")
                
                result_str = str(status)
                if len(result_str) > 500:
                    result_preview = result_str[:500] + "... (结果被截断)"
                else:
                    result_preview = result_str
                self.logger.info(f"📤 [响应 {request_id}] 返回结果: {result_preview}")
                
                return status
                
            except Exception as e:
                # 记录错误响应
                execution_time = time.time() - start_time
                self.logger.error(f"❌ [响应 {request_id}] 工具: test_connection - 执行失败")
                self.logger.error(f"⏱️  [响应 {request_id}] 执行耗时: {execution_time:.3f}秒")
                self.logger.error(f"🚨 [响应 {request_id}] 错误信息: {str(e)}")
                raise e

        @self.mcp_server.tool()
        async def search_code(
            query: str, 
            search_type: str = "hybrid",
            flow_name: Optional[str] = None,
            top_k: int = 5
        ) -> List[Dict[str, Any]]:
            """改进的代码搜索，支持精确、模糊、语义和混合搜索"""
            # 生成请求ID并记录请求
            request_id = str(uuid.uuid4())[:8]
            self.logger.info(f"🔧 [请求 {request_id}] 工具: search_code")
            self.logger.info(f"📥 [请求 {request_id}] 参数: query='{query}', search_type='{search_type}', flow_name={flow_name}, top_k={top_k}")
            
            start_time = time.time()
            
            try:
                self.logger.info(f"⚙️  [处理 {request_id}] 正在执行工具: search_code")
                
                if not self.search_engine:
                    error_result = [{"error": "Search engine not available. Please check database connection."}]
                    
                    # 记录错误响应
                    execution_time = time.time() - start_time
                    self.logger.error(f"❌ [响应 {request_id}] 工具: search_code - 搜索引擎不可用")
                    self.logger.error(f"⏱️  [响应 {request_id}] 执行耗时: {execution_time:.3f}秒")
                    self.logger.error(f"📤 [响应 {request_id}] 返回结果: {error_result}")
                    
                    return error_result
                
                # 使用改进的搜索引擎
                if search_type == "exact":
                    results = self.search_engine.exact_search(query, top_k)
                elif search_type == "fuzzy":
                    results = self.search_engine.fuzzy_search(query, top_k)
                elif search_type == "semantic":
                    results = self.search_engine.semantic_search(query, top_k)
                else:  # hybrid (default)
                    results = self.search_engine.hybrid_search(query, top_k)
                
                # 格式化结果，添加match_type字段
                formatted_results = []
                for result in results:
                    formatted_results.append({
                        "filename": result["filename"],
                        "code": result["code"],
                        "score": result["score"],
                        "match_type": result["match_type"],
                        "start": result["start"],
                        "end": result["end"],
                    })
                
                # 记录成功响应
                execution_time = time.time() - start_time
                self.logger.info(f"✅ [响应 {request_id}] 工具: search_code - 执行成功")
                self.logger.info(f"⏱️  [响应 {request_id}] 执行耗时: {execution_time:.3f}秒")
                self.logger.info(f"📤 [响应 {request_id}] 搜索到 {len(formatted_results)} 个结果")
                
                return formatted_results
                        
            except Exception as e:
                # 记录错误响应
                execution_time = time.time() - start_time
                self.logger.error(f"❌ [响应 {request_id}] 工具: search_code - 执行失败")
                self.logger.error(f"⏱️  [响应 {request_id}] 执行耗时: {execution_time:.3f}秒")
                self.logger.error(f"🚨 [响应 {request_id}] 错误信息: {str(e)}")
                
                error_result = [{"error": f"Search error: {str(e)}"}]
                return error_result

        @self.mcp_server.tool()
        async def get_database_stats() -> Dict[str, Any]:
            """获取数据库统计信息"""
            # 生成请求ID并记录请求
            request_id = str(uuid.uuid4())[:8]
            self.logger.info(f"🔧 [请求 {request_id}] 工具: get_database_stats")
            self.logger.info(f"📥 [请求 {request_id}] 参数: 无")
            
            start_time = time.time()
            
            try:
                self.logger.info(f"⚙️  [处理 {request_id}] 正在执行工具: get_database_stats")
                
                if not self.db_pool:
                    error_result = {"error": "Database not available"}
                    
                    # 记录错误响应
                    execution_time = time.time() - start_time
                    self.logger.error(f"❌ [响应 {request_id}] 工具: get_database_stats - 数据库不可用")
                    self.logger.error(f"⏱️  [响应 {request_id}] 执行耗时: {execution_time:.3f}秒")
                    self.logger.error(f"📤 [响应 {request_id}] 返回结果: {error_result}")
                    
                    return error_result
                
                with self.db_pool.connection() as conn:
                    with conn.cursor() as cur:
                        # 获取正确的表名
                        table_name = "codeembedding__code_embeddings"
                        
                        try:
                            cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table_name)))
                            total_count = cur.fetchone()
                            count = total_count[0] if total_count else 0
                            
                            cur.execute(sql.SQL("SELECT COUNT(DISTINCT filename) FROM {}").format(sql.Identifier(table_name)))
                            file_count_result = cur.fetchone()
                            file_count = file_count_result[0] if file_count_result else 0
                            
                            result = {
                                "table_name": table_name,
                                "total_records": count,
                                "unique_files": file_count,
                                "table_exists": True
                            }
                            
                            # 记录成功响应
                            execution_time = time.time() - start_time
                            self.logger.info(f"✅ [响应 {request_id}] 工具: get_database_stats - 执行成功")
                            self.logger.info(f"⏱️  [响应 {request_id}] 执行耗时: {execution_time:.3f}秒")
                            self.logger.info(f"📤 [响应 {request_id}] 数据库统计: {count} 条记录, {file_count} 个文件")
                            
                            return result
                            
                        except Exception as e:
                            if "does not exist" in str(e):
                                result = {
                                    "table_name": table_name,
                                    "table_exists": False,
                                    "message": "表不存在，请先运行初始化流程"
                                }
                                
                                # 记录警告响应
                                execution_time = time.time() - start_time
                                self.logger.warning(f"⚠️  [响应 {request_id}] 工具: get_database_stats - 表不存在")
                                self.logger.warning(f"⏱️  [响应 {request_id}] 执行耗时: {execution_time:.3f}秒")
                                self.logger.warning(f"📤 [响应 {request_id}] 返回结果: {result}")
                                
                                return result
                            else:
                                raise e
                                
            except Exception as e:
                # 记录错误响应
                execution_time = time.time() - start_time
                self.logger.error(f"❌ [响应 {request_id}] 工具: get_database_stats - 执行失败")
                self.logger.error(f"⏱️  [响应 {request_id}] 执行耗时: {execution_time:.3f}秒")
                self.logger.error(f"🚨 [响应 {request_id}] 错误信息: {str(e)}")
                
                error_result = {"error": f"Database query failed: {str(e)}"}
                return error_result

        @self.mcp_server.tool()
        async def cli_list_flows(app_target: Optional[str] = None) -> Dict[str, Any]:
            """列出所有flows"""
            # 生成请求ID并记录请求
            request_id = str(uuid.uuid4())[:8]
            self.logger.info(f"🔧 [请求 {request_id}] 工具: cli_list_flows")
            self.logger.info(f"📥 [请求 {request_id}] 参数: app_target={app_target}")
            
            start_time = time.time()
            
            try:
                self.logger.info(f"⚙️  [处理 {request_id}] 正在执行工具: cli_list_flows")
                
                from cocoindex.setup import flow_names_with_setup
                
                persisted_flow_names = flow_names_with_setup()
                result = {"persisted_flows": persisted_flow_names}
                
                if app_target:
                    app_ref = _get_app_ref_from_specifier(app_target)
                    _load_user_app(app_ref)
                    
                    current_flow_names = list(flow.flow_names())
                    result["current_flows"] = current_flow_names
                    result["app_target"] = [str(app_ref)]
                    
                    persisted_set = set(persisted_flow_names)
                    missing_setup = [name for name in current_flow_names if name not in persisted_set]
                    result["missing_setup"] = missing_setup
                
                # 记录成功响应
                execution_time = time.time() - start_time
                self.logger.info(f"✅ [响应 {request_id}] 工具: cli_list_flows - 执行成功")
                self.logger.info(f"⏱️  [响应 {request_id}] 执行耗时: {execution_time:.3f}秒")
                
                result_str = str(result)
                if len(result_str) > 500:
                    result_preview = result_str[:500] + "... (结果被截断)"
                else:
                    result_preview = result_str
                self.logger.info(f"📤 [响应 {request_id}] 返回结果: {result_preview}")
                
                return result
                
            except Exception as e:
                # 记录错误响应
                execution_time = time.time() - start_time
                self.logger.error(f"❌ [响应 {request_id}] 工具: cli_list_flows - 执行失败")
                self.logger.error(f"⏱️  [响应 {request_id}] 执行耗时: {execution_time:.3f}秒")
                self.logger.error(f"🚨 [响应 {request_id}] 错误信息: {str(e)}")
                
                error_result = {"error": str(e)}
                return error_result

        # 设置资源
        @self.mcp_server.resource("cocoindex://flows")
        async def get_flows():
            """获取可用的CocoIndex flows"""
            try:
                current_flows = list(flow.flow_names())
                return {"flows": current_flows}
            except Exception as e:
                return {"error": str(e), "flows": []}

        @self.mcp_server.resource("cocoindex://schema")
        async def get_schema():
            """获取流程架构信息"""
            return {"message": "Schema information would be here"}

    async def test_connection(self) -> Dict[str, Any]:
        """测试CocoIndex连接和服务器状态（用于测试模式）"""
        status = {
            "cocoindex_initialized": self._initialized,
            "database_connected": bool(self.db_pool),
            "flow_name": self.flow_name
        }
        
        # 测试数据库连接
        if self.db_pool:
            try:
                with self.db_pool.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                        result = cur.fetchone()
                        status["database_test"] = "SUCCESS" if result else "FAILED"
            except Exception as e:
                status["database_test"] = f"ERROR: {str(e)}"
        else:
            status["database_test"] = "NO_POOL"
        
        # 检查可用的flows
        try:
            current_flows = list(flow.flow_names())
            status["available_flows"] = current_flows
        except Exception as e:
            status["available_flows"] = f"ERROR: {str(e)}"
        
        return status

    def run_stdio(self):
        """运行stdio传输"""
        self.logger.info("🔌 启动 MCP 服务器（stdio 传输）")
        self.mcp_server.run('stdio')

    def run_sse(self):
        """运行Streamable HTTP传输"""
        self.logger.info(f"🚀 启动 MCP Streamable HTTP 服务器: http://{self.host}:{self.port}")
        self.logger.info(f"📡 消息端点: http://{self.host}:{self.port}/message")
        self.logger.info(f"💚 健康检查: http://{self.host}:{self.port}/health")
        self.logger.info(f"📋 传输方式: streamable-http (FastMCP)")
        self.logger.info("🎯 MCP 服务器准备就绪，等待客户端请求...")
        self.mcp_server.run(transport="sse", host=self.host, port=self.port)

def main_sync():
    """同步主入口函数，用于FastMCP运行"""
    parser = argparse.ArgumentParser(description="CocoIndex MCP Server")
    parser.add_argument("--flow", default="gmzz", help="CocoIndex flow name")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="sse", 
                       help="Transport type (stdio or sse)")
    parser.add_argument("--host", default="0.0.0.0", help="Server host")
    parser.add_argument("--port", type=int, default=2010, help="Server port")
    parser.add_argument("--test", action="store_true", help="Run test mode")
    
    args = parser.parse_args()
    
    # 设置日志
    log_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # 创建文件处理器
    file_handler = logging.FileHandler("cocoindex_mcp.log", encoding='utf-8')
    file_handler.setLevel(getattr(logging, args.log_level.upper()))
    file_handler.setFormatter(log_formatter)
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, args.log_level.upper()))
    console_handler.setFormatter(log_formatter)
    
    # 配置根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, args.log_level.upper()))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # 设置环境变量
    if not os.environ.get("COCOINDEX_DATABASE_URL"):
        os.environ["COCOINDEX_DATABASE_URL"] = "postgresql://cocoindex:cocoindex@localhost/cocoindex"
    
    try:
        # 创建MCP服务器
        server = CocoIndexMcpServer(
            flow_name=args.flow,
            host=args.host,
            port=args.port
        )
        
        if args.test:
            # 测试模式需要异步初始化
            async def test_async():
                await server.initialize()
                status = await server.test_connection()
                print("🔍 CocoIndex MCP Server Test Results:")
                print(json.dumps(status, indent=2))
            
            asyncio.run(test_async())
        elif args.transport == "sse":
            # 先同步初始化CocoIndex部分
            # 然后运行FastMCP服务器（它会处理自己的事件循环）
            async def init_async():
                await server.initialize()
            
            asyncio.run(init_async())
            
            # 现在运行FastMCP服务器（同步方式）
            server.run_sse()
        else:
            # stdio模式
            async def init_and_run():
                await server.initialize()
                server.run_stdio()
            
            asyncio.run(init_and_run())
            
    except KeyboardInterrupt:
        logging.info("🛑 服务器被用户停止")
    except Exception as e:
        logging.error(f"❌ 服务器错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main_sync() 