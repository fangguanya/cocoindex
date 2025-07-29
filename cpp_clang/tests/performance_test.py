#!/usr/bin/env python3
"""
性能测试脚本 - 测试优化后的性能改进

比较优化前后的性能指标：
- 解析时间
- 内存使用
- TranslationUnit缓存效果
- 多进程效率
"""

import time
import psutil
import os
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from analyzer.cpp_analyzer import CppAnalyzer, AnalysisConfig
from analyzer.logger import get_logger

class PerformanceMonitor:
    """性能监控器"""
    
    def __init__(self):
        self.start_time = None
        self.start_memory = None
        self.process = psutil.Process()
        
    def start(self):
        """开始监控"""
        self.start_time = time.time()
        self.start_memory = self.process.memory_info().rss / 1024 / 1024  # MB
        
    def stop(self):
        """停止监控并返回结果"""
        end_time = time.time()
        end_memory = self.process.memory_info().rss / 1024 / 1024  # MB
        
        return {
            'duration': end_time - self.start_time,
            'memory_start': self.start_memory,
            'memory_end': end_memory,
            'memory_peak': self.process.memory_info().peak_wss / 1024 / 1024 if hasattr(self.process.memory_info(), 'peak_wss') else end_memory,
            'memory_used': end_memory - self.start_memory
        }

def run_performance_test():
    """运行性能测试"""
    logger = get_logger()
    
    # 测试配置
    project_dir = project_root / "tests" / "validation_project"
    compile_commands_path = project_dir / "temp_compile_commands.json"
    
    # 创建临时编译命令文件
    _create_temp_compile_commands(project_dir, compile_commands_path)
    
    print("🚀 开始性能测试...")
    print("=" * 60)
    
    # 测试不同的配置
    test_configs = [
        {
            'name': '单进程测试',
            'num_jobs': 1,
            'description': '使用1个进程，测试基础性能'
        },
        {
            'name': '多进程测试 (2核)',
            'num_jobs': 2,
            'description': '使用2个进程，测试并行性能'
        },
        {
            'name': '多进程测试 (4核)',
            'num_jobs': 4,
            'description': '使用4个进程，测试最大并行性能'
        }
    ]
    
    results = []
    
    for config in test_configs:
        print(f"\n📊 {config['name']}")
        print(f"   {config['description']}")
        print("-" * 40)
        
        # 运行测试
        monitor = PerformanceMonitor()
        monitor.start()
        
        try:
            analyzer = CppAnalyzer()
            analysis_config = AnalysisConfig(
                project_root=str(project_dir),
                scan_directory=str(project_dir),
                output_path=str(project_dir / f"perf_test_{config['num_jobs']}.json"),
                compile_commands_path=str(compile_commands_path),
                verbose=False,
                num_jobs=config['num_jobs']
            )
            
            result = analyzer.analyze(analysis_config)
            perf_stats = monitor.stop()
            
            if result.success:
                print(f"✅ 测试成功")
                print(f"   解析时间: {perf_stats['duration']:.2f} 秒")
                print(f"   内存使用: {perf_stats['memory_used']:.1f} MB")
                print(f"   峰值内存: {perf_stats['memory_peak']:.1f} MB")
                print(f"   处理文件: {result.files_processed}")
                print(f"   解析成功: {result.files_parsed}")
                print(f"   提取函数: {result.statistics.get('total_functions', 0)}")
                print(f"   提取类: {result.statistics.get('total_classes', 0)}")
                
                results.append({
                    'config': config,
                    'performance': perf_stats,
                    'analysis': result.statistics
                })
            else:
                print(f"❌ 测试失败: {result.parsing_errors}")
                
        except Exception as e:
            perf_stats = monitor.stop()
            print(f"❌ 测试异常: {e}")
            logger.error(f"性能测试异常: {e}")
    
    # 输出性能对比
    print("\n" + "=" * 60)
    print("📈 性能对比结果")
    print("=" * 60)
    
    if len(results) >= 2:
        baseline = results[0]  # 单进程作为基准
        
        print(f"基准测试 ({baseline['config']['name']}):")
        print(f"  解析时间: {baseline['performance']['duration']:.2f} 秒")
        print(f"  内存使用: {baseline['performance']['memory_used']:.1f} MB")
        
        for i, result in enumerate(results[1:], 1):
            speedup = baseline['performance']['duration'] / result['performance']['duration']
            memory_ratio = result['performance']['memory_used'] / baseline['performance']['memory_used']
            
            print(f"\n对比测试 {i} ({result['config']['name']}):")
            print(f"  解析时间: {result['performance']['duration']:.2f} 秒")
            print(f"  加速比: {speedup:.2f}x")
            print(f"  内存使用: {result['performance']['memory_used']:.1f} MB")
            print(f"  内存比率: {memory_ratio:.2f}x")
    
    # 清理临时文件
    try:
        compile_commands_path.unlink()
        for result in results:
            output_file = Path(result['config'].get('output_path', ''))
            if output_file.exists():
                output_file.unlink()
    except:
        pass
    
    print(f"\n🎉 性能测试完成！")
    return results

def _create_temp_compile_commands(project_dir: Path, output_path: Path):
    """创建临时的compile_commands.json文件"""
    import json
    
    # 获取所有源文件
    source_files = []
    for pattern in ['*.cpp', '*.cc', '*.cxx']:
        source_files.extend(project_dir.rglob(pattern))
    
    # 生成编译命令
    compile_commands = []
    for src_file in source_files:
        compile_commands.append({
            "directory": str(project_dir),
            "command": f"clang++ -std=c++17 -I{project_dir}/include {src_file}",
            "file": str(src_file)
        })
    
    # 写入文件
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(compile_commands, f, indent=2)

if __name__ == "__main__":
    run_performance_test()