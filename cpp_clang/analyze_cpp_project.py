#!/usr/bin/env python3
"""
C++ 项目分析脚本

使用方法：
1. 修改下面的配置参数
2. 运行：python analyze_cpp_project.py

功能：
- 分别指定项目根目录和 compile_commands.json 路径
- 设置 clang 工作目录为根目录/Engine
- 生成详细的 JSON 分析结果
"""

import sys
import time
import argparse
import platform
import multiprocessing
from pathlib import Path

# 将项目根目录添加到 sys.path
# 这使得我们可以使用绝对路径导入，如 from cpp_clang.analyzer import ...
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from cpp_clang.analyzer.cpp_analyzer import CppAnalyzer, AnalysisConfig

def main():
    """主函数 - 执行 C++ 项目分析"""
    # ========== 参数解析 ==========
    parser = argparse.ArgumentParser(description="C++ 项目分析工具")
    parser.add_argument("--project_root", default="N:/c7_enginedev/Engine/Source", help="项目根目录")
    parser.add_argument("--scan_directory", default="N:/c7_enginedev/Client", help="要分析的代码目录 (仅用于过滤)")
    #parser.add_argument("--scan_directory", default="N:/c7_enginedev/Client/Plugins/KGCharacter/Source/KGCharacter")
    parser.add_argument("--compile_commands_path", default="L:/ai-cocoindex-clangcpp/cpp_clang/compile_commands.json", help="compile_commands.json 文件的路径")
    #parser.add_argument("--compile_commands_path", default="N:/c7_enginedev/compile_commands.json", help="compile_commands.json 文件的路径")
    parser.add_argument("--output_file", default="cpp_analysis_result.json", help="输出 JSON 文件路径")
    parser.add_argument("--verbose", action="store_true", help="显示详细输出")
    parser.add_argument("--max_files", type=int, default=None, help="限制处理的文件数量，None 表示不限制")
    parser.add_argument("-j", "--jobs", type=int, default=2, help="并行处理任务数 (0 表示使用所有CPU核心)")
    parser.add_argument("--strict_validation", action="store_true", help="启用严格验证模式（报告所有警告，包括外部函数相关）")
    #parser.add_argument("-j", "--jobs", type=int, default=0, help="并行处理任务数 (0 表示使用所有CPU核心)")
    args = parser.parse_args()
    
    console = Console()
    console.print("[bold green]C++ 项目分析工具[/bold green]")
    console.print("正在启动分析...")
    
    # ========== 配置参数 ==========
    
    PROJECT_ROOT = args.project_root
    SCAN_DIRECTORY = args.scan_directory
    COMPILE_COMMANDS_PATH = args.compile_commands_path
    OUTPUT_FILE = args.output_file
    VERBOSE = args.verbose
    MAX_FILES = args.max_files
    NUM_JOBS = args.jobs

    # ========== 配置结束 ==========
    
    console.print("\n[bold blue]分析配置:[/bold blue]")
    console.print(f"项目根目录: {PROJECT_ROOT}")
    console.print(f"扫描目录 (参考): {SCAN_DIRECTORY}")
    console.print(f"编译命令文件: {COMPILE_COMMANDS_PATH}")
    console.print(f"输出文件: {OUTPUT_FILE}")
    console.print(f"并行任务数: {NUM_JOBS if NUM_JOBS > 0 else '自动 (所有核心)'}")
    
    if not Path(PROJECT_ROOT).exists():
        console.print(f"[red]警告: 项目根目录不存在: {PROJECT_ROOT}[/red]")
    if not Path(COMPILE_COMMANDS_PATH).exists():
        console.print(f"[red]错误: compile_commands.json 不存在: {COMPILE_COMMANDS_PATH}[/red]")
        return 1
    
    try:
        console.print("\n[bold blue]初始化分析器...[/bold blue]")
        analyzer = CppAnalyzer(console=console)
        
        config = AnalysisConfig(
            project_root=PROJECT_ROOT,
            scan_directory=SCAN_DIRECTORY,
            compile_commands_path=COMPILE_COMMANDS_PATH,
            output_path=OUTPUT_FILE,
            verbose=VERBOSE,
            max_files=MAX_FILES,
            num_jobs=NUM_JOBS,
            strict_validation=args.strict_validation
        )

        console.print("\n[bold blue]开始分析...[/bold blue]")
        result = analyzer.analyze(config)
        
        console.print(f"\n{'='*60}")
        if result.success and result.output_path:
            console.print("[bold green]✓ 分析完成！[/bold green]")
            
            stats = result.statistics
            console.print(f"\n[bold blue]分析统计:[/bold blue]")
            console.print(f"源文件数 (from compile_commands): {stats.get('total_files_in_compile_commands', 0)}")
            console.print(f"成功处理: {stats.get('successful_processed_files', 0)} / {stats.get('total_files_to_process', 0)}")
            console.print(f"提取函数: {stats.get('total_functions', 0)}")
            console.print(f"提取类: {stats.get('total_classes', 0)}")
            console.print(f"提取命名空间: {stats.get('total_namespaces', 0)}")
            console.print(f"分析用时: {stats.get('analysis_time_sec', 0):.2f}秒")
            
            if Path(result.output_path).exists():
                file_size = Path(result.output_path).stat().st_size
                console.print(f"\n[green]✓ 结果已保存到: {result.output_path}[/green]")
                console.print(f"文件大小: {file_size:,} 字节")
            else:
                console.print(f"[yellow]! 输出文件未生成[/yellow]")
        else:
            console.print("[bold red]✗ 分析失败[/bold red]")
            stats = result.statistics
            if 'stage' in stats and 'reason' in stats:
                console.print(f"失败阶段: {stats['stage']}")
                console.print(f"原因: {stats['reason']}")
    
    except KeyboardInterrupt:
        console.print("\n[yellow]分析被用户中断[/yellow]")
        return 1
    
    except Exception as e:
        console.print(f"\n[red]分析过程中发生错误: {str(e)}[/red]")
        import traceback
        console.print(traceback.format_exc())
        return 1
    
    console.print(f"\n{'='*60}")
    console.print("[bold green]程序执行完成[/bold green]")
    return 0

if __name__ == "__main__":
    """程序入口"""
    # 确保在Windows上多进程正常工作
    if platform.system() == 'Windows':
        multiprocessing.freeze_support()
    
    exit_code = main()
    sys.exit(exit_code)