#!/usr/bin/env python3
"""
C++ 分析器深度性能测试 v2.0
测试第二轮优化后的性能改进效果
"""

import sys
import os
import time
import json
import subprocess
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzer.cpp_analyzer import CppAnalyzer, AnalysisConfig
from analyzer.performance_profiler import profiler, DetailedLogger
from rich.console import Console
from rich.table import Table

def run_performance_test():
    """运行深度性能测试"""
    console = Console()
    console.print("[bold green]🚀 C++ 分析器深度性能测试 v2.0[/bold green]")
    console.print("=" * 60)
    
    # 测试配置
    test_project = Path(__file__).parent / "validation_project"
    compile_commands = test_project / "compile_commands.json"
    output_file = "performance_test_v2_result.json"
    
    if not compile_commands.exists():
        console.print(f"[red]错误: 找不到 {compile_commands}[/red]")
        return False
    
    # 清理之前的性能数据
    profiler.clear()
    
    # 创建分析器配置
    config = AnalysisConfig(
        project_root=str(test_project),
        scan_directory=str(test_project),
        output_path=output_file,
        compile_commands_path=str(compile_commands),
        max_files=None,  # 处理所有文件
        verbose=True,
        num_jobs=4  # 使用4个进程
    )
    
    console.print(f"📁 测试项目: {test_project}")
    console.print(f"📋 编译命令: {compile_commands}")
    console.print(f"📊 输出文件: {output_file}")
    console.print(f"🔧 并行进程: {config.num_jobs}")
    
    # 执行分析
    console.print("\n[bold]开始深度性能测试...[/bold]")
    
    analyzer = CppAnalyzer(console)
    
    # 记录开始时间
    start_time = time.time()
    
    try:
        result = analyzer.analyze(config)
        end_time = time.time()
        
        if result.success:
            console.print(f"\n[green]✅ 分析成功完成![/green]")
            
            # 详细性能统计
            total_time = end_time - start_time
            stats = result.statistics
            
            # 创建性能报告表格
            table = Table(title="深度性能测试结果 v2.0")
            table.add_column("指标", style="cyan")
            table.add_column("数值", style="magenta")
            table.add_column("单位", style="green")
            
            table.add_row("总处理时间", f"{total_time:.2f}", "秒")
            table.add_row("成功处理文件", str(stats.get('successful_processed_files', 0)), "个")
            table.add_row("总文件数", str(stats.get('total_files_to_process', 0)), "个")
            table.add_row("发现函数", str(stats.get('total_functions', 0)), "个")
            table.add_row("发现类", str(stats.get('total_classes', 0)), "个")
            table.add_row("发现命名空间", str(stats.get('total_namespaces', 0)), "个")
            
            # 计算性能指标
            files_per_sec = stats.get('successful_processed_files', 0) / total_time if total_time > 0 else 0
            table.add_row("处理速度", f"{files_per_sec:.2f}", "文件/秒")
            
            console.print(table)
            
            # 性能评估
            console.print("\n[bold]🎯 性能评估:[/bold]")
            if total_time < 10:
                console.print("[green]🌟 性能优秀! 已达到秒级处理目标![/green]")
            elif total_time < 30:
                console.print("[yellow]⚡ 性能良好，接近目标[/yellow]")
            elif total_time < 60:
                console.print("[orange1]⚠️  性能一般，需要进一步优化[/orange1]")
            else:
                console.print("[red]🐌 性能不佳，需要深度优化[/red]")
            
            # 与之前的性能对比
            console.print("\n[bold]📈 性能改进分析:[/bold]")
            baseline_time = 600  # 假设基线是10分钟
            improvement = (baseline_time - total_time) / baseline_time * 100
            speedup = baseline_time / total_time if total_time > 0 else 0
            
            console.print(f"相比基线 ({baseline_time}s) 改进: {improvement:.1f}%")
            console.print(f"加速倍数: {speedup:.2f}x")
            
            # 输出详细的性能分析报告
            console.print("\n[bold]🔍 详细性能分析:[/bold]")
            profiler.print_report()
            
            # 保存性能测试结果
            performance_report = {
                "test_version": "v2.0",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_time_seconds": total_time,
                "files_processed": stats.get('successful_processed_files', 0),
                "files_per_second": files_per_sec,
                "entities_found": {
                    "functions": stats.get('total_functions', 0),
                    "classes": stats.get('total_classes', 0),
                    "namespaces": stats.get('total_namespaces', 0)
                },
                "performance_rating": "excellent" if total_time < 10 else "good" if total_time < 30 else "fair" if total_time < 60 else "poor",
                "improvement_vs_baseline": improvement,
                "speedup_factor": speedup,
                "profiler_data": profiler.get_stats()
            }
            
            report_file = "performance_test_v2_report.json"
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(performance_report, f, indent=2, ensure_ascii=False)
            
            console.print(f"\n📋 性能报告已保存到: {report_file}")
            
            return True
            
        else:
            console.print(f"[red]❌ 分析失败: {result.parsing_errors}[/red]")
            return False
            
    except Exception as e:
        console.print(f"[red]💥 测试过程中发生异常: {e}[/red]")
        import traceback
        console.print(f"[red]{traceback.format_exc()}[/red]")
        return False

def main():
    """主函数"""
    console = Console()
    
    console.print("[bold blue]C++ 分析器深度性能测试 v2.0[/bold blue]")
    console.print("测试第二轮优化后的性能改进效果\n")
    
    success = run_performance_test()
    
    if success:
        console.print("\n[green]🎉 性能测试完成![/green]")
        return 0
    else:
        console.print("\n[red]❌ 性能测试失败![/red]")
        return 1

if __name__ == "__main__":
    sys.exit(main())