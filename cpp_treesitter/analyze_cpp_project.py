#!/usr/bin/env python3
"""
Tree-sitter版本的C++项目分析脚本

使用tree-sitter替代clang进行C++代码分析，按照json_format.md规格输出结果。
相比clang版本，增加了函数体文本提取功能。

重大改进：
- 全局USR ID系统，确保跨文件唯一性
- 两阶段解析：声明收集 + 定义处理
- 完整的函数调用关系分析
- 类方法详细解析
- 函数体代码内容提取
- 双JSON输出：主分析结果 + 全局nodes映射

使用方法：
1. 修改下面的配置参数
2. 运行：python analyze_cpp_project.py

功能：
- 使用tree-sitter解析C++代码
- 生成详细的JSON分析结果（兼容json_format.md）
- 增加函数体文本内容提取
- 完整的USR ID系统和调用关系分析
"""

import time
from pathlib import Path

from analyzer.logger import Logger
from analyzer.cpp_analyzer import CppAnalyzer

# ============================================================================
# 配置参数 - 根据需要修改这些参数
# ============================================================================

# C++项目的根目录路径
PROJECT_PATH = "test_code"  # 修改为您的项目路径

# 分析结果的输出目录
OUTPUT_DIR = "analysis_results"

# 日志文件路径（None表示自动生成带时间戳的文件名）
LOG_PATH = None

# 是否启用详细日志输出
VERBOSE = True

# ============================================================================

def main():
    """主函数，用于启动C++项目分析"""
    
    # 生成默认日志文件名
    if LOG_PATH is None:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        log_path = f"cpp_analyzer_{timestamp}.log"
    else:
        log_path = LOG_PATH

    # 初始化日志记录器
    Logger.setup_logger(log_file=log_path, verbose=VERBOSE)
    logger = Logger.get_logger()

    logger.info("=" * 80)
    logger.info("======     C++ Tree-sitter Analyzer v2.4     ======")
    logger.info("======     增强版：USR ID + 两阶段解析      ======")
    logger.info("=" * 80)
    
    start_time = time.time()

    project_path = Path(PROJECT_PATH)
    if not project_path.is_dir():
        logger.error(f"错误: 项目路径 '{project_path}' 不存在或不是一个目录。")
        print(f"错误: 项目路径 '{project_path}' 不存在或不是一个目录。")
        print("请修改 analyze_cpp_project.py 文件中的 PROJECT_PATH 变量。")
        return 1

    try:
        # 1. 初始化分析器
        logger.info(f"正在初始化分析器，项目路径: {project_path}")
        print(f"开始分析项目: {project_path}")
        analyzer = CppAnalyzer(str(project_path))

        # 2. 执行完整的两阶段分析并导出结果
        logger.info("开始执行完整分析...")
        print("正在执行两阶段分析...")
        project = analyzer.analyze_and_export(OUTPUT_DIR)

        logger.info("=" * 80)
        logger.info("分析完成！")
        logger.info(f"输出目录: {OUTPUT_DIR}")
        logger.info("生成的文件:")
        logger.info("  - cpp_treesitter_analysis_result.json (主分析结果)")
        logger.info("  - nodes.json (全局节点映射)")
        logger.info("  - analysis_summary.json (分析摘要)")
        logger.info("=" * 80)

        print("=" * 80)
        print("分析完成！")
        print(f"输出目录: {OUTPUT_DIR}")
        print("生成的文件:")
        print("  - cpp_treesitter_analysis_result.json (主分析结果)")
        print("  - nodes.json (全局节点映射)")
        print("  - analysis_summary.json (分析摘要)")
        print("=" * 80)

        return 0

    except Exception as e:
        logger.error(f"分析过程中发生严重错误: {e}", exc_info=True)
        print(f"发生严重错误: {e}")
        print(f"请检查日志文件 '{log_path}' 获取详细信息。")
        return 1

    finally:
        end_time = time.time()
        logger.info(f"总分析时间: {end_time - start_time:.2f} 秒")
        logger.info("分析流程结束。")
        print(f"总分析时间: {end_time - start_time:.2f} 秒")

if __name__ == "__main__":
    exit(main()) 