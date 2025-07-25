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
from pathlib import Path

# 添加 analyzer 模块到路径
sys.path.insert(0, str(Path(__file__).parent))

from rich.console import Console
from analyzer.cpp_analyzer import CppAnalyzer

def main():
    """主函数 - 执行 C++ 项目分析"""
    console = Console()
    console.print("[bold green]C++ 项目分析工具[/bold green]")
    console.print("正在启动分析...")
    
    # ========== 配置参数 ==========
    # 请根据实际项目修改以下路径
    
    # 项目根目录（用于 include 搜索和路径映射）
    PROJECT_ROOT = "D:/c7_i9_EngineDev/Engine"
    
    # 要分析的代码目录
    SCAN_DIRECTORY = "D:/c7_i9_EngineDev/Engine/Source"
    
    # compile_commands.json 文件的具体路径
    COMPILE_COMMANDS_PATH = "E:/mcp/codebase_index/cocoindex/cpp_export/compile_commands.json"
    
    # clang 执行的工作目录（通常是 Engine 目录）
    CLANG_WORKING_DIR = "D:/c7_i9_EngineDev/Engine"
    
    # 输出 JSON 文件路径
    OUTPUT_FILE = "cpp_analysis_result.json"
    
    # 其他选项
    VERBOSE = True  # 是否显示详细输出
    MAX_FILES = None  # 限制处理的文件数量，None 表示不限制
    
    # ========== 配置结束 ==========
    
    console.print("\n[bold blue]分析配置:[/bold blue]")
    console.print(f"项目根目录: {PROJECT_ROOT}")
    console.print(f"扫描目录: {SCAN_DIRECTORY}")
    console.print(f"编译命令文件: {COMPILE_COMMANDS_PATH}")
    console.print(f"clang工作目录: {CLANG_WORKING_DIR}")
    console.print(f"输出文件: {OUTPUT_FILE}")
    
    # 检查路径是否存在
    paths_to_check = [
        ("项目根目录", PROJECT_ROOT),
        ("扫描目录", SCAN_DIRECTORY),
    ]
    
    for name, path in paths_to_check:
        if not Path(path).exists():
            console.print(f"[red]警告: {name} 不存在: {path}[/red]")
            console.print("[yellow]请修改脚本中的路径配置[/yellow]")
            console.print("[yellow]如果这是演示运行，程序会继续执行[/yellow]")
    
    if Path(COMPILE_COMMANDS_PATH).exists():
        console.print(f"[green]✓ 找到编译命令文件[/green]")
    else:
        console.print(f"[yellow]! 编译命令文件不存在，将尝试自动生成[/yellow]")
    
    try:
        # 创建分析器
        console.print("\n[bold blue]初始化分析器...[/bold blue]")
        analyzer = CppAnalyzer(console=console)
        
        # 执行分析
        console.print("\n[bold blue]开始分析...[/bold blue]")
        start_time = time.time()
        
        result = analyzer.analyze(
            project_root=PROJECT_ROOT,
            scan_directory=SCAN_DIRECTORY,
            compile_commands_path=COMPILE_COMMANDS_PATH,
            clang_working_directory=CLANG_WORKING_DIR,
            output_path=OUTPUT_FILE,
            verbose=VERBOSE,
            max_files=MAX_FILES,
            use_compile_commands=True,
            generate_compile_commands=True
        )
        
        analysis_time = time.time() - start_time
        
        # 显示结果
        console.print(f"\n{'='*60}")
        if result.success:
            console.print("[bold green]✓ 分析完成！[/bold green]")
            
            # 显示统计信息
            stats = result.statistics
            console.print(f"\n[bold blue]分析统计:[/bold blue]")
            console.print(f"总文件数: {stats.get('total_files', 0)}")
            console.print(f"成功解析: {stats.get('successful_files', 0)}")
            console.print(f"解析失败: {stats.get('failed_files', 0)}")
            console.print(f"提取函数: {stats.get('total_functions', 0)}")
            console.print(f"提取类: {stats.get('total_classes', 0)}")
            console.print(f"提取命名空间: {stats.get('total_namespaces', 0)}")
            console.print(f"分析用时: {analysis_time:.2f}秒")
            
            # 检查输出文件
            if Path(OUTPUT_FILE).exists():
                file_size = Path(OUTPUT_FILE).stat().st_size
                console.print(f"\n[green]✓ 结果已保存到: {OUTPUT_FILE}[/green]")
                console.print(f"文件大小: {file_size:,} 字节")
            else:
                console.print(f"[yellow]! 输出文件未生成[/yellow]")
        else:
            console.print("[bold red]✗ 分析失败[/bold red]")
            if 'error' in result.statistics:
                console.print(f"错误: {result.statistics['error']}")
            if 'reason' in result.statistics:
                console.print(f"原因: {result.statistics['reason']}")
    
    except KeyboardInterrupt:
        console.print("\n[yellow]分析被用户中断[/yellow]")
        return 1
    
    except Exception as e:
        console.print(f"\n[red]分析过程中发生错误: {str(e)}[/red]")
        return 1
    
    console.print(f"\n{'='*60}")
    console.print("[bold green]程序执行完成[/bold green]")
    return 0

if __name__ == "__main__":
    """程序入口"""
    exit_code = main()
    sys.exit(exit_code) 