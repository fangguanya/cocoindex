from dotenv import load_dotenv
from psycopg_pool import ConnectionPool
from pgvector.psycopg import register_vector
from typing import Any
import cocoindex
import os
from numpy.typing import NDArray
import numpy as np
import threading
import time


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
        message = "📊 进度监控已启动..."
        print(message)
        import logging
        logger = logging.getLogger(__name__)
        logger.info(message)
        
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
                                cur.execute(f"SELECT COUNT(*) FROM {self.table_name};")
                                total_count = cur.fetchone()
                                if total_count:
                                    total_count = total_count[0]
                                else:
                                    total_count = 0
                                
                                # 检查不同文件数
                                cur.execute(f"SELECT COUNT(DISTINCT filename) FROM {self.table_name};")
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
                                    message = f"📈 处理进度: {total_count} 个代码块 | {file_count} 个文件 | 速度: {speed:.1f} 块/秒 | 运行时间: {elapsed:.1f}秒"
                                    #print(message)  # 保持控制台输出
                                    import logging
                                    logger = logging.getLogger(__name__)
                                    logger.info(message)  # 同时记录到日志文件
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


@cocoindex.op.function()
def extract_extension(filename: str) -> str:
    """Extract the extension of a filename."""
    return os.path.splitext(filename)[1]


@cocoindex.op.function()
def track_file_progress(filename: str) -> str:
    """跟踪文件处理进度"""
    import logging
    logger = logging.getLogger(__name__)
    with stats_lock:
        processing_stats["files_processed"] += 1
        processing_stats["current_file"] = filename
        message = f"📄 正在处理文件 #{processing_stats['files_processed']}: {os.path.basename(filename)}"
        print(message)  # 保持控制台输出
        logger.info(message)  # 同时记录到日志文件
    return filename


@cocoindex.op.function()
def track_chunk_progress(text: str) -> str:
    """跟踪代码块处理进度"""
    import logging
    logger = logging.getLogger(__name__)
    with stats_lock:
        processing_stats["chunks_processed"] += 1
        if processing_stats["chunks_processed"] % 10 == 0:  # 每10个块显示一次
            message = f"✂️  已分割 {processing_stats['chunks_processed']} 个代码块"
            #print(message)  # 保持控制台输出
            logger.info(message)  # 同时记录到日志文件
    return text


@cocoindex.op.function()
def track_embedding_progress(embedding: NDArray[np.float32]) -> NDArray[np.float32]:
    """跟踪嵌入生成进度"""
    import logging
    logger = logging.getLogger(__name__)
    with stats_lock:
        processing_stats["embeddings_created"] += 1
        if processing_stats["embeddings_created"] % 10 == 0:  # 每10个嵌入显示一次
            message = f"🤖 已生成 {processing_stats['embeddings_created']} 个向量嵌入"
            #print(message)  # 保持控制台输出
            logger.info(message)  # 同时记录到日志文件
    return embedding


def reset_progress_stats():
    """重置进度统计"""
    with stats_lock:
        processing_stats["files_processed"] = 0
        processing_stats["chunks_processed"] = 0
        processing_stats["embeddings_created"] = 0
        processing_stats["current_file"] = ""


@cocoindex.transform_flow()
def code_to_embedding(
    text: cocoindex.DataSlice[str],
) -> cocoindex.DataSlice[NDArray[np.float32]]:
    """
    Embed the text using a SentenceTransformer model.
    """
    # You can also switch to Voyage embedding model:
    #    return text.transform(
    #        cocoindex.functions.EmbedText(
    #            api_type=cocoindex.LlmApiType.VOYAGE,
    #            model="voyage-code-3",
    #        )
    # 使用本地GPU上的Qwen3-Embedding-4B模型生成嵌入
    return text.transform(
        cocoindex.functions.SentenceTransformerEmbed(
            model="sentence-transformers/all-MiniLM-L6-v2"
        )
        # cocoindex.functions.SentenceTransformerEmbed(
        #     model="Qwen/Qwen3-Embedding-4B"
        # )
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
    #included_patterns=["*.py", "*.rs", "*.toml", "*.md", "*.mdx"],
    #included_patterns=["*.cpp", "*.h", "*.hpp", "*.c", "*.lua"],
    data_scope["files"] = flow_builder.add_source(
        cocoindex.sources.LocalFile(
            path=path,
            included_patterns=included_patterns,
            excluded_patterns=["**/.*", "target", "**/node_modules", 
                "**/Binaries", "**/DerivedDataCache", "**/Intermediate", "**/Saved", "**/Build", "**/Content", "*.luac",
                "**/Engine/Source/Programs", "**/ThirdParty"],
        )
    )
    print("📋 处理的文件路径: ", path)
    print("🔍 包含的文件类型: ", included_patterns)
    
    print("📊 开始处理文件并收集数据...")
    code_embeddings = data_scope.add_collector()

    with data_scope["files"].row() as file:
        # 跟踪文件处理进度
        file["tracked_filename"] = file["filename"].transform(track_file_progress)
        
        file["extension"] = file["filename"].transform(extract_extension)
        file["chunks"] = file["content"].transform(
            cocoindex.functions.SplitRecursively(),
            language=file["extension"],
            chunk_size=1000,
            min_chunk_size=300,
            chunk_overlap=300,
        )
        
        with file["chunks"].row() as chunk:
            # 跟踪代码块处理进度
            chunk["tracked_text"] = chunk["text"].transform(track_chunk_progress)
            
            # 生成嵌入并跟踪进度
            chunk["embedding"] = chunk["text"].call(code_to_embedding)
            chunk["tracked_embedding"] = chunk["embedding"].transform(track_embedding_progress)
            
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


def search(pool: ConnectionPool, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    # Get the table name, for the export target in the code_embedding_flow above.
    table_name = cocoindex.utils.get_target_default_name(
        code_embedding_flow, "code_embeddings"
    )
    # Evaluate the transform flow defined above with the input query, to get the embedding.
    query_vector = code_to_embedding.eval(query)
    # Run the query and get the results.
    with pool.connection() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT filename, code, embedding <=> %s AS distance, start, "end"
                FROM {table_name} ORDER BY distance LIMIT %s
            """,
                (query_vector, top_k),
            )
            return [
                {
                    "filename": row[0],
                    "code": row[1],
                    "score": 1.0 - row[2],
                    "start": row[3],
                    "end": row[4],
                }
                for row in cur.fetchall()
            ]


def _main() -> None:
    # 重置进度统计
    reset_progress_stats()
    
    # Make sure the flow is built and up-to-date.
    print("🚀 开始更新代码嵌入流程...")
    print("📝 正在调用 code_embedding_flow.update()...")
    
    # 启动进度监控
    db_url = os.getenv("COCOINDEX_DATABASE_URL")
    if db_url:
        monitor = ProgressMonitor(db_url)
        monitor.start()
    else:
        monitor = None
        print("⚠️  无法启动进度监控：缺少数据库URL")
    
    try:
        print("⏳ 正在处理文件，这可能需要一些时间...")
        
        stats = code_embedding_flow.update()
        
        # 停止进度监控
        if monitor:
            monitor.stop()
            
        print("✅ 流程更新成功！")
        print("📊 更新完成，统计信息: ", stats)
    except Exception as e:
        # 停止进度监控
        if monitor:
            monitor.stop()
        print(f"❌ 流程更新失败: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 显示数据库中的数据统计
    print("🔍 正在检查数据库中的数据...")
    try:
        test_pool = ConnectionPool(os.getenv("COCOINDEX_DATABASE_URL"))
        with test_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM CodeEmbedding__code_embeddings;")
                count = cur.fetchone()[0]
                print(f"✅ 数据库中共有 {count} 条代码块记录")
                
                cur.execute("SELECT COUNT(DISTINCT filename) FROM CodeEmbedding__code_embeddings;")
                file_count = cur.fetchone()[0]
                print(f"📁 处理了 {file_count} 个不同的文件")
        test_pool.close()
    except Exception as e:
        print(f"⚠️  无法检查数据库统计: {e}")
    
    print("🔄 正在初始化数据库连接池...")

    # Initialize the database connection pool.
    try:
        pool = ConnectionPool(os.getenv("COCOINDEX_DATABASE_URL"))
        print("✅ 数据库连接池初始化成功")
    except Exception as e:
        print(f"❌ 数据库连接池初始化失败: {e}")
        return
    
    print("🎯 进入交互查询模式...")
    print("💡 提示：输入查询词来搜索代码，直接回车退出")
    print("🔍 示例查询：'class', 'function', 'vector', 'include'")
    
    # Run queries in a loop to demonstrate the query capabilities.
    while True:
        try:
            query = input("\n🔎 请输入搜索查询 (直接回车退出): ")
            if query == "":
                break
            print(f"🔍 正在搜索: '{query}'...")
            # Run the query function with the database connection pool and the query.
            results = search(pool, query)
            print(f"\n✅ 找到 {len(results)} 个相关结果:")
            for i, result in enumerate(results, 1):
                print(f"\n📄 结果 {i}: [相似度: {result['score']:.3f}]")
                print(f"📂 文件: {result['filename']}")
                print(f"📍 位置: 第{result['start']['line']}-{result['end']['line']}行")
                print(f"💬 代码片段:")
                print(f"    {result['code'][:200]}..." if len(result['code']) > 200 else f"    {result['code']}")
                print("─" * 50)
            print()
        except Exception as e:
            print(f"❌ 查询出错: {e}")
            import traceback
            traceback.print_exc()
            break
    
    print("👋 感谢使用代码搜索工具！")


if __name__ == "__main__":
    load_dotenv()
    
    # 设置SiliconFlow API密钥
    os.environ["OPENAI_API_KEY"] = "sk-goxqcxvegxcivakvpaaafzfwogskiuhdqbmbaxgonsmqxtep"
    
    # 配置日志级别为DEBUG以显示详细进度
    import logging
    
    # 设置日志格式
    log_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    
    # 创建文件处理器
    file_handler = logging.FileHandler("code_embedding.log", encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(log_formatter)
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_formatter)
    
    # 配置根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    #root_logger.addHandler(console_handler)
    
    # 启用cocoindex的详细日志
    cocoindex_logger = logging.getLogger('cocoindex')
    cocoindex_logger.setLevel(logging.DEBUG)
    
    print("📝 日志输出已配置：")
    print(f"   - 控制台输出：INFO级别及以上")
    print(f"   - 文件输出：code_embedding.log (INFO级别及以上)")
    print(f"   - CocoIndex详细日志：DEBUG级别")
    
    cocoindex.init()
    _main()
