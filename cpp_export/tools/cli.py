"""
Command Line Interface for C++ Code Analyzer

Provides a CLI interface for analyzing C++ projects with support for:
- Dual-path design (project_root + scan_directory)
- compile_commands.json integration
- Unreal Engine project support
- Rich output and progress reporting
"""

import click
from pathlib import Path
from typing import Optional
import sys

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from ..analyzer.cpp_analyzer import CppAnalyzer, AnalysisConfig

console = Console()

@click.group()
@click.version_option()
def main():
    """C++ Code Analyzer - 分析C++项目并导出实体关系到JSON"""
    pass

@click.command()
@click.option('--project-root',
              required=True,
              type=click.Path(exists=True, file_okay=False, dir_okay=True),
              help='项目根目录，用于include搜索和路径映射')
@click.option('--scan-directory',
              type=click.Path(exists=True, file_okay=False, dir_okay=True),
              help='扫描目录，默认与project-root相同')
@click.option('--output', '-o',
              default='analysis_result.json',
              help='输出JSON文件路径')
@click.option('--no-compile-db',
              is_flag=True,
              help='不使用compile_commands.json')
@click.option('--no-generate-db',
              is_flag=True,
              help='不自动生成compile_commands.json')
@click.option('--include-ext',
              multiple=True,
              default=['.h', '.hpp', '.cpp', '.cc', '.cxx', '.c'],
              help='包含的文件扩展名')
@click.option('--exclude',
              multiple=True,
              help='排除的文件模式')
@click.option('--max-files',
              type=int,
              help='最大处理文件数')
@click.option('--verbose', '-v',
              is_flag=True,
              help='详细输出')
@click.option('--quiet', '-q',
              is_flag=True,
              help='静默模式')
def analyze(project_root: str, scan_directory: Optional[str], output: str, 
           no_compile_db: bool, no_generate_db: bool, include_ext: tuple, 
           exclude: tuple, max_files: Optional[int], verbose: bool, quiet: bool):
    """分析C++项目"""
    
    if quiet:
        console = Console(file=sys.stderr, quiet=True)
    else:
        console = Console()
    
    # 显示分析配置
    if not quiet:
        console.print(Panel.fit(
            "[bold blue]C++ Code Analyzer[/bold blue]\n"
            f"项目根目录: {project_root}\n"
            f"扫描目录: {scan_directory or project_root}\n"
            f"输出文件: {output}",
            title="分析配置"
        ))
    
    try:
        # 创建分析器
        analyzer = CppAnalyzer(console=console)
        
        # 执行分析
        result = analyzer.analyze(
            project_root=project_root,
            scan_directory=scan_directory,
            output_path=output,
            include_extensions=set(include_ext),
            exclude_patterns=set(exclude) if exclude else None,
            use_compile_commands=not no_compile_db,
            generate_compile_commands=not no_generate_db,
            max_files=max_files,
            verbose=verbose
        )
        
        # 显示结果
        if not quiet:
            _display_analysis_result(result, console)
        
        # 退出码
        sys.exit(0 if result.success else 1)
        
    except KeyboardInterrupt:
        console.print("\n[yellow]分析被用户中断[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]分析失败: {str(e)}[/red]")
        if verbose:
            import traceback
            console.print(traceback.format_exc())
        sys.exit(1)

@click.command(hidden=True)
@click.argument('directory', 
                type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.option('--output', '-o',
              default='analysis_result.json',
              help='输出JSON文件路径')
@click.option('--include-ext',
              multiple=True,
              default=['.h', '.hpp', '.cpp', '.cc', '.cxx', '.c'],
              help='包含的文件扩展名')
@click.option('--exclude',
              multiple=True,
              help='排除的文件模式')
@click.option('--max-files',
              type=int,
              help='最大处理文件数')
@click.option('--verbose', '-v',
              is_flag=True,
              help='详细输出')
@click.option('--quiet', '-q',
              is_flag=True,
              help='静默模式')
def analyze_directory(directory: str, output: str, include_ext: tuple, 
                     exclude: tuple, max_files: Optional[int], verbose: bool, quiet: bool):
    """分析目录 - 向后兼容命令（隐藏）"""
    
    if quiet:
        console = Console(file=sys.stderr, quiet=True)
    else:
        console = Console()
    
    try:
        # 创建分析器
        analyzer = CppAnalyzer(console=console)
        
        # 执行分析（使用旧接口）
        result = analyzer.analyze_directory(
            root_path=directory,
            output_path=output,
            include_extensions=set(include_ext),
            exclude_patterns=set(exclude) if exclude else None,
            max_files=max_files,
            verbose=verbose
        )
        
        # 显示结果
        if not quiet:
            _display_analysis_result(result, console)
        
        # 退出码
        sys.exit(0 if result.success else 1)
        
    except KeyboardInterrupt:
        console.print("\n[yellow]分析被用户中断[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]分析失败: {str(e)}[/red]")
        if verbose:
            import traceback
            console.print(traceback.format_exc())
        sys.exit(1)

def _display_analysis_result(result, console: Console):
    """显示分析结果"""
    
    if result.success:
        # 成功结果表格
        table = Table(title="分析结果", show_header=True, header_style="bold green")
        table.add_column("项目", style="cyan")
        table.add_column("数值", justify="right", style="green")
        
        stats = result.statistics
        table.add_row("总文件数", str(stats.get('total_files', 0)))
        table.add_row("成功解析", str(stats.get('successful_files', 0)))
        table.add_row("解析失败", str(stats.get('failed_files', 0)))
        table.add_row("提取函数", str(stats.get('total_functions', 0)))
        table.add_row("提取类", str(stats.get('total_classes', 0)))
        table.add_row("提取命名空间", str(stats.get('total_namespaces', 0)))
        table.add_row("分析用时", f"{stats.get('analysis_time', 0):.2f}秒")
        
        console.print(table)
        
        # 输出文件信息
        if result.config.output_path:
            output_path = Path(result.config.output_path)
            if output_path.exists():
                file_size = output_path.stat().st_size
                console.print(f"\n[green]✓[/green] 结果已保存到: {output_path}")
                console.print(f"文件大小: {file_size:,} 字节")
    
    else:
        # 失败结果
        console.print("[red]✗ 分析失败[/red]")
        if 'error' in result.statistics:
            console.print(f"错误: {result.statistics['error']}")
        if 'reason' in result.statistics:
            console.print(f"原因: {result.statistics['reason']}")

@click.command()
@click.argument('project_root',
                type=click.Path(exists=True, file_okay=False, dir_okay=True))
def detect_project(project_root: str):
    """检测项目类型和配置"""
    
    console.print(Panel.fit(
        f"[bold blue]项目检测[/bold blue]\n项目路径: {project_root}",
        title="C++ Project Detector"
    ))
    
    project_path = Path(project_root)
    
    # 检测项目类型
    is_ue = _detect_unreal_engine(project_path)
    is_cmake = _detect_cmake(project_path)
    compile_commands_exists = (project_path / "compile_commands.json").exists()
    
    # 显示检测结果
    table = Table(title="检测结果", show_header=True, header_style="bold cyan")
    table.add_column("项目特征", style="cyan")
    table.add_column("状态", justify="center")
    
    table.add_row("Unreal Engine项目", "✓" if is_ue else "✗")
    table.add_row("CMake项目", "✓" if is_cmake else "✗") 
    table.add_row("compile_commands.json", "✓" if compile_commands_exists else "✗")
    
    console.print(table)
    
    # 显示建议
    suggestions = []
    
    if is_ue:
        suggestions.append("检测到UE项目，建议使用 --project-root 指向引擎根目录")
        suggestions.append("使用 --scan-directory 指定要分析的具体模块")
    
    if is_cmake and not compile_commands_exists:
        suggestions.append("CMake项目建议先生成compile_commands.json")
        suggestions.append("运行: cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON")
    
    if not compile_commands_exists:
        suggestions.append("推荐使用 --no-generate-db 禁用自动生成编译数据库")
    
    if suggestions:
        console.print("\n[bold yellow]建议:[/bold yellow]")
        for suggestion in suggestions:
            console.print(f"• {suggestion}")

def _detect_unreal_engine(project_path: Path) -> bool:
    """检测Unreal Engine项目"""
    indicators = [
        "*.uproject",
        "Source",
        "Config/DefaultEngine.ini",
        "Plugins",
        "Engine/Build/Build.version"
    ]
    
    for indicator in indicators:
        if indicator.startswith("*"):
            if list(project_path.glob(indicator)):
                return True
        elif (project_path / indicator).exists():
            return True
    
    return False

def _detect_cmake(project_path: Path) -> bool:
    """检测CMake项目"""
    cmake_files = ["CMakeLists.txt", "cmake"]
    return any((project_path / f).exists() for f in cmake_files)

@click.command()
def version():
    """显示版本信息"""
    console.print(Panel.fit(
        "[bold blue]C++ Code Analyzer[/bold blue]\n"
        "版本: 1.0.0\n"
        "支持: C++17, Unreal Engine, CMake\n"
        "依赖: libclang, Rich",
        title="版本信息"
    ))

# 注册命令
main.add_command(analyze)
main.add_command(analyze_directory)
main.add_command(detect_project)
main.add_command(version)

if __name__ == '__main__':
    main() 