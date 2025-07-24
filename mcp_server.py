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
from typing import Dict, List, Optional, Any
from datetime import datetime
import argparse
from numpy.typing import NDArray
import numpy as np

# 添加python目录到路径，以便导入cocoindex
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

import cocoindex
from cocoindex import flow, lib, setting
from cocoindex.cli import (
    _load_user_app, _get_app_ref_from_specifier, _parse_app_flow_specifier,
    _flow_by_name, _setup_flows, _run_server as cli_run_server
)

# 导入FastMCP
try:
    from fastmcp import FastMCP
    FASTMCP_AVAILABLE = True
except ImportError:
    FASTMCP_AVAILABLE = False

# 添加数据库相关导入
try:
    from psycopg_pool import ConnectionPool
    from pgvector.psycopg import register_vector
    from psycopg import sql
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False


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
                    pool = ConnectionPool(self.db_url)
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
                    pool.close()
                    
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
    with stats_lock:
        processing_stats["chunks_processed"] += 1
        if processing_stats["chunks_processed"] % 1000 == 0:  # 每10个块显示一次
            print(f"✂️  已分割 {processing_stats['chunks_processed']} 个代码块")
    return text


def track_embedding_progress(embedding: NDArray[np.float32]) -> NDArray[np.float32]:
    """跟踪嵌入生成进度"""
    with stats_lock:
        processing_stats["embeddings_created"] += 1
        if processing_stats["embeddings_created"] % 1000 == 0:  # 每10个嵌入显示一次
            print(f"🤖 已生成 {processing_stats['embeddings_created']} 个向量嵌入")
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
    data_scope["files"] = flow_builder.add_source(
        cocoindex.sources.LocalFile(
            path="D:/c7_i9_EngineDev/Client",
            included_patterns=["*.cpp", "*.h", "*.hpp", "*.c"],
            excluded_patterns=["**/.*", "target", "**/node_modules", "**/Binaries", "**/DerivedDataCache", "**/Intermediate", "**/Saved", "**/Build", "**/Content"],
        )
    )
    
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


class CocoIndexMcpServer:
    """MCP Server for CocoIndex code analysis using FastMCP."""
    
    def __init__(self, flow_name: str = "gmzz", host: str = "127.0.0.1", port: int = 2010):
        self.flow_name = flow_name
        self.host = host
        self.port = port
        self.logger = logging.getLogger(__name__)
        self._initialized = False
        self.db_pool: Optional[ConnectionPool] = None
        
        # 初始化FastMCP服务器（修复弃用警告）
        if FASTMCP_AVAILABLE:
            self.mcp_server = FastMCP(            
                name="CocoIndex"
            )
            self._setup_tools()
        else:
            self.logger.error("FastMCP not available. Install with: pip install fastmcp")
            raise ImportError("FastMCP is required")
    
    async def initialize(self):
        """初始化MCP服务器"""
        if self._initialized:
            return
            
        try:
            # 初始化CocoIndex
            settings = setting.Settings.from_env()
            lib.init(settings)
            
            # 初始化数据库连接池
            if POSTGRES_AVAILABLE:
                db_url = os.getenv("COCOINDEX_DATABASE_URL")
                if db_url:
                    self.db_pool = ConnectionPool(db_url)
                    self.logger.info("Database connection pool initialized")
                else:
                    self.logger.warning("COCOINDEX_DATABASE_URL not set, database queries will not work")
            else:
                self.logger.warning("psycopg_pool not available, install with: pip install psycopg[pool] pgvector")
            
            # 确保flows可用
            try:
                flow.ensure_all_flows_built()
                self.logger.info(f"MCP Server initialized with flow: {self.flow_name}")
            except Exception as e:
                self.logger.warning(f"Could not build flows: {e}")
            
            # 自动初始化代码嵌入流程
            await self._auto_initialize_flow()
            
            self._initialized = True
            
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
            self.logger.info("📋 处理的文件路径: D:/c7_i9_EngineDev/Client")
            self.logger.info("🔍 包含的文件类型: *.cpp, *.h, *.hpp, *.c")
            
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
            status = {
                "cocoindex_initialized": self._initialized,
                "postgres_available": POSTGRES_AVAILABLE,
                "fastmcp_available": FASTMCP_AVAILABLE,
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

        @self.mcp_server.tool()
        async def search_code(
            query: str, 
            flow_name: Optional[str] = None,
            top_k: int = 5
        ) -> List[Dict[str, Any]]:
            """使用CocoIndex进行语义代码搜索"""
            if not self.db_pool:
                return [{"error": "Database not available. Set COCOINDEX_DATABASE_URL and install psycopg[pool] pgvector."}]
            
            try:
                # 获取当前可用的flows
                current_flows = list(flow.flow_names())
                
                if not current_flows:
                    return [{"error": "No flows available. Please run a CocoIndex flow first."}]
                
                # 确定要搜索的flow
                if flow_name and flow_name in current_flows:
                    target_flow = flow.flow_by_name(flow_name)
                else:
                    # 寻找CodeEmbedding flow或使用第一个可用的
                    target_flow = None
                    for fname in current_flows:
                        if "embedding" in fname.lower() or "code" in fname.lower():
                            target_flow = flow.flow_by_name(fname)
                            break
                    
                    if not target_flow and current_flows:
                        target_flow = flow.flow_by_name(current_flows[0])
                
                if not target_flow:
                    return [{"error": "No suitable flow found for code search"}]
                
                # 获取表名（使用实际的表名）
                table_name = "codeembedding__code_embeddings"
                
                # 使用transform flow获取查询向量
                try:
                    query_vector = code_to_embedding.eval(query)
                except Exception as e:
                    return [{"error": f"Failed to generate query embedding: {str(e)}"}]
                
                # 执行向量相似度搜索
                with self.db_pool.connection() as conn:
                    register_vector(conn)
                    with conn.cursor() as cur:
                        # 检查表是否存在
                        cur.execute(
                            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                            (table_name,)
                        )
                        
                        table_exists = cur.fetchone()
                        if not table_exists or not table_exists[0]:
                            return [{"error": f"Table '{table_name}' does not exist. Please run indexing first."}]
                        
                        # 执行向量搜索（使用余弦相似度）
                        search_query = sql.SQL("""
                            SELECT filename, code, embedding <=> %s AS distance, start, "end"
                            FROM {} 
                            ORDER BY distance 
                            LIMIT %s
                        """).format(sql.Identifier(table_name))
                        
                        cur.execute(search_query, (query_vector, top_k))
                        
                        results = []
                        rows = cur.fetchall()
                        for row in rows:
                            results.append({
                                "filename": row[0],
                                "code": row[1],
                                "score": 1.0 - row[2],  # 转换distance为similarity score
                                "start": row[3],
                                "end": row[4],
                            })
                        
                        return results
                        
            except Exception as e:
                self.logger.error(f"Error in semantic search: {e}")
                return [{"error": f"Search error: {str(e)}"}]

        @self.mcp_server.tool()
        async def get_database_stats() -> Dict[str, Any]:
            """获取数据库统计信息"""
            if not self.db_pool:
                return {"error": "Database not available"}
            
            try:
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
                            
                            return {
                                "table_name": table_name,
                                "total_records": count,
                                "unique_files": file_count,
                                "table_exists": True
                            }
                        except Exception as e:
                            if "does not exist" in str(e):
                                return {
                                    "table_name": table_name,
                                    "table_exists": False,
                                    "message": "表不存在，请先运行初始化流程"
                                }
                            else:
                                raise e
            except Exception as e:
                return {"error": f"Database query failed: {str(e)}"}

        @self.mcp_server.tool()
        async def cli_list_flows(app_target: Optional[str] = None) -> Dict[str, Any]:
            """列出所有flows"""
            try:
                from cocoindex.setup import flow_names_with_setup
                
                persisted_flow_names = flow_names_with_setup()
                result = {"persisted_flows": persisted_flow_names}
                
                if app_target:
                    app_ref = _get_app_ref_from_specifier(app_target)
                    _load_user_app(app_ref)
                    
                    current_flow_names = list(flow.flow_names())
                    result["current_flows"] = current_flow_names
                    result["app_target"] = str(app_ref)
                    
                    persisted_set = set(persisted_flow_names)
                    missing_setup = [name for name in current_flow_names if name not in persisted_set]
                    result["missing_setup"] = missing_setup
                
                return result
            except Exception as e:
                self.logger.error(f"Error listing flows: {e}")
                return {"error": str(e)}

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
            "postgres_available": POSTGRES_AVAILABLE,
            "fastmcp_available": FASTMCP_AVAILABLE,
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
        self.logger.info("Starting MCP server with stdio transport")
        self.mcp_server.run('stdio')

    def run_sse(self):
        """运行Streamable HTTP传输"""
        self.logger.info(f"🚀 Starting MCP Streamable HTTP server at http://{self.host}:{self.port}")
        self.logger.info(f"📡 Message endpoint: http://{self.host}:{self.port}/message")
        self.logger.info(f"💚 Health check: http://{self.host}:{self.port}/health")
        self.logger.info(f"📋 Transport: streamable-http (FastMCP)")
        self.mcp_server.run(transport="sse", host=self.host, port=self.port)

def main_sync():
    """同步主入口函数，用于FastMCP运行"""
    parser = argparse.ArgumentParser(description="CocoIndex MCP Server")
    parser.add_argument("--flow", default="gmzz", help="CocoIndex flow name")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio", 
                       help="Transport type (stdio or sse)")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=2010, help="Server port")
    parser.add_argument("--test", action="store_true", help="Run test mode")
    
    args = parser.parse_args()
    
    # 设置日志
    log_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    
    # 创建文件处理器
    file_handler = logging.FileHandler("log.txt", encoding='utf-8')
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
        logging.info("Server stopped by user")
    except Exception as e:
        logging.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main_sync() 