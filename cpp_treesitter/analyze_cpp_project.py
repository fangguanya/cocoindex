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
- CLI参数化配置

使用方法：
python analyze_cpp_project.py -p <project_path> [-o <output_dir>] [--verbose] [-j <jobs>]

功能：
- 使用tree-sitter解析C++代码
- 生成详细的JSON分析结果（兼容json_format.md）
- 增加函数体文本内容提取
- 完整的USR ID系统和调用关系分析
"""

import time
import argparse
import sys
from pathlib import Path

from analyzer.logger import Logger
from analyzer.cpp_analyzer import CppAnalyzer

# ============================================================================
# CLI参数解析
# ============================================================================

def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='C++ Tree-sitter Analyzer v2.4 - 增强版代码分析器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  %(prog)s -p ./my_cpp_project                    # 分析项目到默认输出目录
  %(prog)s -p /path/to/ue5/project -o ./results  # 分析UE5项目到指定目录
  %(prog)s -p ./src --verbose -j 4               # 详细输出，使用4个并行作业
  %(prog)s -p ./game_engine --log ./my.log       # 指定日志文件
        """
    )
    
    # 必需参数
    parser.add_argument(
        '-p', '--project', 
        default='D:/c7_i9_EngineDev/Client',
        help='C++项目的根目录路径（必需）'
    )
    
    # 可选参数
    parser.add_argument(
        '-o', '--output',
        default='analysis_results',
        help='分析结果的输出目录（默认: analysis_results）'
    )
    
    parser.add_argument(
        '-j', '--jobs',
        type=int,
        default=1,
        help='并行作业数量（默认: 1，暂未实现并行处理）'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='启用详细日志输出'
    )
    
    parser.add_argument(
        '--log',
        help='指定日志文件路径（默认: 自动生成带时间戳的文件名）'
    )
    
    parser.add_argument(
        '--include-extensions',
        nargs='+',
        default=['.cpp', '.cc', '.cxx', '.c++', '.h', '.hpp', '.hxx', '.h++'],
        help='要包含的文件扩展名列表（默认: 常见C++扩展名）'
    )
    
    parser.add_argument(
        '--exclude-patterns',
        nargs='+',
        default=[
            '*/Intermediate/*',      # UE中间文件
            '*/Binaries/*',          # UE二进制文件
            '*/DerivedDataCache/*',  # UE缓存
            '*/Saved/*',             # UE保存文件
            '*/.vs/*',               # Visual Studio
            '*/.vscode/*',           # VS Code
            '*/.git/*',              # Git目录
            '*/node_modules/*',      # Node.js
            '*/__pycache__/*',       # Python缓存
            '*/CMakeFiles/*',        # CMake生成文件
            '*/build/*',             # 通用构建目录
            '*/dist/*',              # 发布目录
            '*/obj/*',               # 目标文件目录
            '*/Debug/*',             # Debug输出
            '*/Release/*',           # Release输出
            '*/x64/*',               # x64输出
            '*/Win32/*',             # Win32输出
            "*/.*",
            "*/DerivedDataCache/*",
            "*/Build/*",
            "*/Content/*",
            "*.luac",
            "*/Engine/Source/Programs/*",
            "*/ThirdParty/*",
            "*/Client_WwiseProject/*",],
        help='要排除的文件模式列表（例如: */build/* */test/*）'
    )
    
    parser.add_argument(
        '--version',
        action='version',
        version='C++ Tree-sitter Analyzer v2.4'
    )
    
    return parser.parse_args()

def validate_arguments(args):
    """验证命令行参数"""
    project_path = Path(args.project)
    
    # 验证项目路径
    if not project_path.exists():
        print(f"错误: 项目路径 '{project_path}' 不存在。", file=sys.stderr)
        return False
    
    if not project_path.is_dir():
        print(f"错误: 项目路径 '{project_path}' 不是一个目录。", file=sys.stderr)
        return False
    
    # 验证并行作业数
    if args.jobs < 1:
        print(f"错误: 并行作业数必须大于0，当前值: {args.jobs}", file=sys.stderr)
        return False
    
    if args.jobs > 1:
        print(f"警告: 并行处理功能暂未实现，将使用单线程处理。")
    
    # 验证输出目录
    output_path = Path(args.output)
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"错误: 无法创建输出目录 '{output_path}': {e}", file=sys.stderr)
        return False
    
    return True

# ============================================================================

def main():
    """主函数，用于启动C++项目分析"""
    
    # 解析命令行参数
    args = parse_arguments()
    
    # 验证参数
    if not validate_arguments(args):
        return 1
    
    # 生成日志文件名
    if args.log:
        log_path = args.log
    else:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        log_path = f"cpp_analyzer_{timestamp}.log"

    # 初始化日志记录器
    Logger.setup_logger(log_file=log_path, verbose=args.verbose)
    logger = Logger.get_logger()

    logger.info("=" * 80)
    logger.info("======     C++ Tree-sitter Analyzer v2.4     ======")
    logger.info("======     增强版：USR ID + 两阶段解析      ======")
    logger.info("=" * 80)
    logger.info(f"项目路径: {args.project}")
    logger.info(f"输出目录: {args.output}")
    logger.info(f"并行作业: {args.jobs}")
    logger.info(f"详细输出: {args.verbose}")
    logger.info(f"日志文件: {log_path}")
    logger.info(f"包含扩展: {args.include_extensions}")
    if args.exclude_patterns:
        logger.info(f"排除模式: {args.exclude_patterns}")
    
    start_time = time.time()

    project_path = Path(args.project)
    
    print(f"开始分析项目: {project_path}")
    print(f"输出目录: {args.output}")
    if args.verbose:
        print(f"详细日志: {log_path}")

    try:
        # 1. 初始化分析器
        logger.info(f"正在初始化分析器，项目路径: {project_path}")
        print("📝 正在初始化分析器...")
        analyzer = CppAnalyzer(str(project_path))
        logger.info("分析器初始化完成")
        print("✅ 分析器初始化完成")

        # 2. 执行完整的两阶段分析并导出结果
        logger.info("开始执行完整分析...")
        print("🔍 正在执行两阶段分析...")
        logger.info("调用 analyze_and_export 方法...")
        project = analyzer.analyze()
        
        # 调试：检查分析后的调用关系状态
        debug_stats = analyzer.repo.get_statistics()
        print(f"🔍 分析后调用关系调试:")
        print(f"   get_statistics(): {debug_stats['call_relationships']}")
        print(f"   call_relationships['calls_to']: {len(analyzer.repo.call_relationships['calls_to'])}")
        
        # 检查函数实体中的调用关系
        functions = analyzer.repo.get_nodes_by_type('function')
        functions_with_calls = sum(1 for f in functions if hasattr(f, 'calls_to') and f.calls_to)
        total_calls = sum(len(f.calls_to) for f in functions if hasattr(f, 'calls_to') and f.calls_to)
        print(f"   函数实体中有调用的函数数: {functions_with_calls}")
        print(f"   函数实体中调用总数: {total_calls}")
        
        # 导出结果
        analyzer.export_results(args.output)
        logger.info("analyze_and_export 方法执行完成")

        logger.info("=" * 80)
        logger.info("分析完成！")
        logger.info(f"输出目录: {args.output}")
        logger.info("生成的文件:")
        logger.info("  - cpp_treesitter_analysis_result.json (主分析结果)")
        logger.info("  - nodes.json (全局节点映射)")
        logger.info("  - analysis_summary.json (分析摘要)")
        logger.info("=" * 80)

        print("=" * 80)
        print("✅ 分析完成！")
        print(f"📁 输出目录: {args.output}")
        print("📄 生成的文件:")
        print("   - cpp_treesitter_analysis_result.json (主分析结果)")
        print("   - nodes.json (全局节点映射)")
        print("   - analysis_summary.json (分析摘要)")
        
        # 显示分析统计
        stats = analyzer.repo.get_statistics()
        print("📊 分析统计:")
        print(f"   - 总实体数: {stats['total_entities']}")
        print(f"   - 函数: {stats['by_type'].get('function', 0)}")
        print(f"   - 类: {stats['by_type'].get('class', 0)}")
        print(f"   - 命名空间: {stats['by_type'].get('namespace', 0)}")
        print(f"   - 调用关系: {stats['call_relationships']}")
        print(f"   - 文件数: {stats['files_analyzed']}")
        print("=" * 80)

        return 0

    except Exception as e:
        logger.error(f"分析过程中发生严重错误: {e}")
        print(f"❌ 发生严重错误: {e}")
        print(f"💡 请检查日志文件 '{log_path}' 获取详细信息。")
        return 1

    finally:
        end_time = time.time()
        elapsed_time = end_time - start_time
        logger.info(f"总分析时间: {elapsed_time:.2f} 秒")
        logger.info("分析流程结束。")
        print(f"⏱️  总分析时间: {elapsed_time:.2f} 秒")

if __name__ == "__main__":
    exit(main()) 