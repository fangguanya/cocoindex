import sys
from pathlib import Path
import json
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from typing import Dict, List, Any, Set

# 将项目根目录添加到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cpp_clang.analyzer.cpp_analyzer import CppAnalyzer, AnalysisConfig

class AdvancedValidator:
    """高级验证器，用于验证C++代码分析器的各种功能"""
    
    def __init__(self, console: Console):
        self.console = console
        self.validation_results = {
            'is_definition_validation': [],
            'template_parameters_validation': [],
            'template_specialization_validation': [],
            'function_calls_validation': [],
            'overload_resolution_validation': [],
            'cross_file_calls_validation': [],
            'template_instantiation_validation': []
        }
    
    def validate_analysis_result(self, result_path: Path) -> bool:
        """验证分析结果的主函数"""
        self.console.print("\n[bold yellow]🔍 开始高级验证分析结果...[/bold yellow]")
        
        with open(result_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 获取各种数据
        functions_map = data.get('functions', {})
        classes_map = data.get('classes', {})
        variables_map = data.get('variables', {})
        
        self.console.print(f"📊 分析数据概览:")
        self.console.print(f"  - 函数数量: {len(functions_map)}")
        self.console.print(f"  - 类数量: {len(classes_map)}")
        self.console.print(f"  - 变量数量: {len(variables_map)}")
        
        all_passed = True
        
        # 执行各项验证
        all_passed &= self.validate_is_definition(functions_map)
        all_passed &= self.validate_template_parameters(functions_map, classes_map)
        all_passed &= self.validate_template_specializations(functions_map, classes_map)
        all_passed &= self.validate_function_calls(functions_map)
        all_passed &= self.validate_overload_resolution(functions_map)
        all_passed &= self.validate_cross_file_calls(functions_map)
        all_passed &= self.validate_template_instantiations(functions_map, classes_map)
        
        # 生成验证报告
        self.generate_validation_report()
        
        return all_passed
    
    def validate_is_definition(self, functions_map: Dict) -> bool:
        """验证1: is_definition字段验证"""
        self.console.print("\n[bold cyan]1️⃣ 验证is_definition字段...[/bold cyan]")
        
        passed = True
        test_cases = []
        
        for usr, func_data in functions_map.items():
            func_name = func_data.get('name', 'unknown')
            is_definition = func_data.get('is_definition', False)
            body = func_data.get('code_content', '')
            file_path = func_data.get('file_path', '')
            
            # 测试用例1: 有定义的函数应该有函数体
            if is_definition and not body:
                test_cases.append({
                    'test': 'definition_has_body',
                    'function': func_name,
                    'usr': usr,
                    'status': 'FAIL',
                    'message': f'函数被标记为定义但函数体为空'
                })
                passed = False
            elif is_definition and body:
                test_cases.append({
                    'test': 'definition_has_body',
                    'function': func_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'定义函数有正确的函数体'
                })
            
            # 测试用例2: 只有声明的函数不应该有函数体
            if not is_definition and body:
                test_cases.append({
                    'test': 'declaration_no_body',
                    'function': func_name,
                    'usr': usr,
                    'status': 'FAIL',
                    'message': f'声明函数不应该有函数体'
                })
                passed = False
            elif not is_definition and not body:
                test_cases.append({
                    'test': 'declaration_no_body',
                    'function': func_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'声明函数正确地没有函数体'
                })
        
        self.validation_results['is_definition_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ is_definition验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ is_definition验证失败[/bold red]")
        
        return passed
    
    def validate_template_parameters(self, functions_map: Dict, classes_map: Dict) -> bool:
        """验证2: 模板参数导出验证"""
        self.console.print("\n[bold cyan]2️⃣ 验证模板参数导出...[/bold cyan]")
        
        passed = True
        test_cases = []
        
        # 检查函数模板参数
        for usr, func_data in functions_map.items():
            func_name = func_data.get('name', 'unknown')
            template_params = func_data.get('template_parameters', [])
            
            # 检查已知的模板函数
            if 'add_values' in func_name:
                if not template_params:
                    test_cases.append({
                        'test': 'template_function_params',
                        'entity': func_name,
                        'usr': usr,
                        'status': 'FAIL',
                        'message': f'模板函数{func_name}缺少模板参数信息'
                    })
                    passed = False
                else:
                    test_cases.append({
                        'test': 'template_function_params',
                        'entity': func_name,
                        'usr': usr,
                        'status': 'PASS',
                        'message': f'模板函数{func_name}有正确的模板参数: {template_params}'
                    })
            
            if 'process_data' in func_name:
                expected_param_count = 3  # T, U, N
                if len(template_params) != expected_param_count:
                    test_cases.append({
                        'test': 'multi_param_template',
                        'entity': func_name,
                        'usr': usr,
                        'status': 'FAIL',
                        'message': f'多参数模板函数{func_name}参数数量不正确，期望{expected_param_count}，实际{len(template_params)}'
                    })
                    passed = False
                else:
                    test_cases.append({
                        'test': 'multi_param_template',
                        'entity': func_name,
                        'usr': usr,
                        'status': 'PASS',
                        'message': f'多参数模板函数{func_name}参数数量正确: {len(template_params)}'
                    })
        
        # 检查类模板参数
        for usr, class_data in classes_map.items():
            class_name = class_data.get('name', 'unknown')
            template_params = class_data.get('template_parameters', [])
            
            if 'DataProcessor' in class_name:
                if not template_params:
                    test_cases.append({
                        'test': 'template_class_params',
                        'entity': class_name,
                        'usr': usr,
                        'status': 'FAIL',
                        'message': f'模板类{class_name}缺少模板参数信息'
                    })
                    passed = False
                else:
                    test_cases.append({
                        'test': 'template_class_params',
                        'entity': class_name,
                        'usr': usr,
                        'status': 'PASS',
                        'message': f'模板类{class_name}有正确的模板参数: {template_params}'
                    })
        
        self.validation_results['template_parameters_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ 模板参数验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ 模板参数验证失败[/bold red]")
        
        return passed
    
    def validate_template_specializations(self, functions_map: Dict, classes_map: Dict) -> bool:
        """验证3: 模板特化信息验证"""
        self.console.print("\n[bold cyan]3️⃣ 验证模板特化信息...[/bold cyan]")
        
        passed = True
        test_cases = []
        
        # 检查函数模板特化
        specialization_found = False
        for usr, func_data in functions_map.items():
            func_name = func_data.get('name', 'unknown')
            template_args = func_data.get('template_args', [])
            is_specialization = func_data.get('is_template_specialization', False)
            
            # 查找add_values的特化版本
            if 'add_values' in func_name and (is_specialization or template_args):
                specialization_found = True
                test_cases.append({
                    'test': 'function_specialization',
                    'entity': func_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'找到函数模板特化: {func_name}, 模板参数: {template_args}'
                })
        
        if not specialization_found:
            test_cases.append({
                'test': 'function_specialization',
                'entity': 'add_values',
                'usr': 'N/A',
                'status': 'FAIL',
                'message': '未找到add_values函数的模板特化'
            })
            passed = False
        
        # 检查类模板特化
        class_specialization_found = False
        for usr, class_data in classes_map.items():
            class_name = class_data.get('name', 'unknown')
            template_args = class_data.get('template_args', [])
            is_specialization = class_data.get('is_template_specialization', False)
            
            if 'DataProcessor' in class_name and (is_specialization or template_args):
                class_specialization_found = True
                test_cases.append({
                    'test': 'class_specialization',
                    'entity': class_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'找到类模板特化: {class_name}, 模板参数: {template_args}'
                })
        
        if not class_specialization_found:
            test_cases.append({
                'test': 'class_specialization',
                'entity': 'DataProcessor',
                'usr': 'N/A',
                'status': 'WARN',
                'message': '未找到DataProcessor类的模板特化信息'
            })
        
        self.validation_results['template_specialization_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ 模板特化验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ 模板特化验证失败[/bold red]")
        
        return passed
    
    def validate_function_calls(self, functions_map: Dict) -> bool:
        """验证4: 函数调用关系验证"""
        self.console.print("\n[bold cyan]4️⃣ 验证函数调用关系...[/bold cyan]")
        
        passed = True
        test_cases = []
        
        # 统计调用关系
        total_callto = 0
        total_callby = 0
        functions_with_calls = 0
        
        for usr, func_data in functions_map.items():
            func_name = func_data.get('name', 'unknown')
            callto = func_data.get('callto', [])
            callby = func_data.get('callby', [])
            
            if callto:
                total_callto += len(callto)
                functions_with_calls += 1
                test_cases.append({
                    'test': 'callto_relations',
                    'entity': func_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'函数{func_name}调用了{len(callto)}个其他函数'
                })
            
            if callby:
                total_callby += len(callby)
        
        # 验证特定的调用关系
        main_function = None
        for usr, func_data in functions_map.items():
            if func_data.get('name') == 'main':
                main_function = func_data
                break
        
        if main_function:
            main_callto = main_function.get('callto', [])
            if len(main_callto) > 10:  # main函数应该调用很多函数
                test_cases.append({
                    'test': 'main_function_calls',
                    'entity': 'main',
                    'usr': main_function.get('usr', ''),
                    'status': 'PASS',
                    'message': f'main函数调用了{len(main_callto)}个函数，符合预期'
                })
            else:
                test_cases.append({
                    'test': 'main_function_calls',
                    'entity': 'main',
                    'usr': main_function.get('usr', ''),
                    'status': 'FAIL',
                    'message': f'main函数只调用了{len(main_callto)}个函数，可能遗漏了调用关系'
                })
                passed = False
        
        self.console.print(f"📈 调用关系统计:")
        self.console.print(f"  - 总调用关系(callto): {total_callto}")
        self.console.print(f"  - 总被调用关系(callby): {total_callby}")
        self.console.print(f"  - 有调用关系的函数: {functions_with_calls}")
        
        self.validation_results['function_calls_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ 函数调用关系验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ 函数调用关系验证失败[/bold red]")
        
        return passed
    
    def validate_overload_resolution(self, functions_map: Dict) -> bool:
        """验证5: 函数重载解析验证"""
        self.console.print("\n[bold cyan]5️⃣ 验证函数重载解析...[/bold cyan]")
        
        passed = True
        test_cases = []
        
        # 查找重载函数
        overload_groups = {}
        for usr, func_data in functions_map.items():
            func_name = func_data.get('name', 'unknown')
            if func_name not in overload_groups:
                overload_groups[func_name] = []
            overload_groups[func_name].append(func_data)
        
        # 检查已知的重载函数
        overloaded_function_found = False
        for func_name, funcs in overload_groups.items():
            if 'overloaded_function' in func_name and len(funcs) > 1:
                overloaded_function_found = True
                test_cases.append({
                    'test': 'function_overloads',
                    'entity': func_name,
                    'usr': 'multiple',
                    'status': 'PASS',
                    'message': f'找到重载函数{func_name}，共{len(funcs)}个重载版本'
                })
                
                # 验证每个重载版本的参数类型不同
                param_signatures = set()
                for func in funcs:
                    params = func.get('parameters', [])
                    signature = tuple(param.get('type', '') for param in params)
                    param_signatures.add(signature)
                
                if len(param_signatures) == len(funcs):
                    test_cases.append({
                        'test': 'overload_signatures',
                        'entity': func_name,
                        'usr': 'multiple',
                        'status': 'PASS',
                        'message': f'重载函数{func_name}的参数签名都不同'
                    })
                else:
                    test_cases.append({
                        'test': 'overload_signatures',
                        'entity': func_name,
                        'usr': 'multiple',
                        'status': 'FAIL',
                        'message': f'重载函数{func_name}存在相同的参数签名'
                    })
                    passed = False
        
        if not overloaded_function_found:
            test_cases.append({
                'test': 'function_overloads',
                'entity': 'overloaded_function',
                'usr': 'N/A',
                'status': 'FAIL',
                'message': '未找到overloaded_function的重载版本'
            })
            passed = False
        
        self.validation_results['overload_resolution_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ 函数重载解析验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ 函数重载解析验证失败[/bold red]")
        
        return passed
    
    def validate_cross_file_calls(self, functions_map: Dict) -> bool:
        """验证6: 跨文件调用关系验证"""
        self.console.print("\n[bold cyan]6️⃣ 验证跨文件调用关系...[/bold cyan]")
        
        passed = True
        test_cases = []
        
        # 统计跨文件调用
        cross_file_calls = 0
        file_groups = {}
        
        # 按文件分组函数
        for usr, func_data in functions_map.items():
            file_path = func_data.get('file_path', '')
            if file_path not in file_groups:
                file_groups[file_path] = []
            file_groups[file_path].append(func_data)
        
        # 检查跨文件调用
        for usr, func_data in functions_map.items():
            func_name = func_data.get('name', 'unknown')
            func_file = func_data.get('file_path', '')
            callto = func_data.get('callto', [])
            
            for call in callto:
                called_usr = call.get('usr', '')
                # 查找被调用函数的文件
                for called_usr_key, called_func_data in functions_map.items():
                    if called_usr_key == called_usr:
                        called_file = called_func_data.get('file_path', '')
                        if func_file != called_file and func_file and called_file:
                            cross_file_calls += 1
                            test_cases.append({
                                'test': 'cross_file_call',
                                'entity': f'{func_name} -> {called_func_data.get("name", "unknown")}',
                                'usr': usr,
                                'status': 'PASS',
                                'message': f'跨文件调用: {Path(func_file).name} -> {Path(called_file).name}'
                            })
                        break
        
        self.console.print(f"📁 跨文件调用统计: {cross_file_calls}个跨文件调用")
        
        if cross_file_calls > 0:
            test_cases.append({
                'test': 'cross_file_calls_exist',
                'entity': 'overall',
                'usr': 'N/A',
                'status': 'PASS',
                'message': f'发现{cross_file_calls}个跨文件调用关系'
            })
        else:
            test_cases.append({
                'test': 'cross_file_calls_exist',
                'entity': 'overall',
                'usr': 'N/A',
                'status': 'FAIL',
                'message': '未发现跨文件调用关系'
            })
            passed = False
        
        self.validation_results['cross_file_calls_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ 跨文件调用关系验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ 跨文件调用关系验证失败[/bold red]")
        
        return passed
    
    def validate_template_instantiations(self, functions_map: Dict, classes_map: Dict) -> bool:
        """验证7: 模板实例化验证"""
        self.console.print("\n[bold cyan]7️⃣ 验证模板实例化...[/bold cyan]")
        
        passed = True
        test_cases = []
        
        # 查找模板实例化
        template_instances = 0
        
        for usr, func_data in functions_map.items():
            func_name = func_data.get('name', 'unknown')
            template_args = func_data.get('template_args', [])
            
            if template_args:
                template_instances += 1
                test_cases.append({
                    'test': 'template_instantiation',
                    'entity': func_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'函数模板实例化: {func_name} with {template_args}'
                })
        
        for usr, class_data in classes_map.items():
            class_name = class_data.get('name', 'unknown')
            template_args = class_data.get('template_args', [])
            
            if template_args:
                template_instances += 1
                test_cases.append({
                    'test': 'template_instantiation',
                    'entity': class_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'类模板实例化: {class_name} with {template_args}'
                })
        
        self.console.print(f"🔧 模板实例化统计: {template_instances}个模板实例")
        
        if template_instances > 0:
            test_cases.append({
                'test': 'template_instances_exist',
                'entity': 'overall',
                'usr': 'N/A',
                'status': 'PASS',
                'message': f'发现{template_instances}个模板实例化'
            })
        else:
            test_cases.append({
                'test': 'template_instances_exist',
                'entity': 'overall',
                'usr': 'N/A',
                'status': 'WARN',
                'message': '未发现模板实例化，可能需要检查模板分析'
            })
        
        self.validation_results['template_instantiation_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ 模板实例化验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ 模板实例化验证失败[/bold red]")
        
        return passed
    
    def generate_validation_report(self):
        """生成详细的验证报告"""
        self.console.print("\n[bold magenta]📋 详细验证报告[/bold magenta]")
        
        for validation_type, test_cases in self.validation_results.items():
            if not test_cases:
                continue
                
            table = Table(title=f"{validation_type.replace('_', ' ').title()}")
            table.add_column("测试", style="cyan")
            table.add_column("实体", style="yellow")
            table.add_column("状态", style="bold")
            table.add_column("消息", style="white")
            
            for case in test_cases:
                status_color = "green" if case['status'] == 'PASS' else "red" if case['status'] == 'FAIL' else "yellow"
                table.add_row(
                    case['test'],
                    case['entity'][:30] + "..." if len(case['entity']) > 30 else case['entity'],
                    f"[{status_color}]{case['status']}[/{status_color}]",
                    case['message']
                )
            
            self.console.print(table)


def main():
    """对 validation_project 运行分析并保存结果"""
    console = Console()
    validation_project_dir = Path(__file__).parent / "validation_project"
    output_file = validation_project_dir / "new_analysis_result.json"
    
    # 动态创建正确的 compile_commands.json，包含新的advanced_templates.cpp
    project_root = Path(__file__).parent.parent.parent.resolve()
    temp_compile_commands_path = validation_project_dir / "temp_compile_commands.json"
    
    correct_commands = [
        {
            "directory": str(project_root),
            "command": f"clang++ -I{validation_project_dir / 'include'} -c {validation_project_dir / 'src/base.cpp'} -o {validation_project_dir / 'src/base.o'}",
            "file": str(validation_project_dir / "src/base.cpp")
        },
        # {
        #     "directory": str(project_root),
        #     "command": f"clang++ -I{validation_project_dir / 'include'} -c {validation_project_dir / 'src/derived.cpp'} -o {validation_project_dir / 'src/derived.o'}",
        #     "file": str(validation_project_dir / "src/derived.cpp")
        # },
        # {
        #     "directory": str(project_root),
        #     "command": f"clang++ -I{validation_project_dir / 'include'} -c {validation_project_dir / 'src/utils.cpp'} -o {validation_project_dir / 'src/utils.o'}",
        #     "file": str(validation_project_dir / "src/utils.cpp")
        # },
        # {
        #     "directory": str(project_root),
        #     "command": f"clang++ -I{validation_project_dir / 'include'} -c {validation_project_dir / 'src/templates.cpp'} -o {validation_project_dir / 'src/templates.o'}",
        #     "file": str(validation_project_dir / "src/templates.cpp")
        # },
        # {
        #     "directory": str(project_root),
        #     "command": f"clang++ -I{validation_project_dir / 'include'} -c {validation_project_dir / 'src/complex.cpp'} -o {validation_project_dir / 'src/complex.o'}",
        #     "file": str(validation_project_dir / "src/complex.cpp")
        # },
        # {
        #     "directory": str(project_root),
        #     "command": f"clang++ -I{validation_project_dir / 'include'} -c {validation_project_dir / 'src/advanced_templates.cpp'} -o {validation_project_dir / 'src/advanced_templates.o'}",
        #     "file": str(validation_project_dir / "src/advanced_templates.cpp")
        # },
        # {
        #     "directory": str(project_root),
        #     "command": f"clang++ -I{validation_project_dir / 'include'} -c {validation_project_dir / 'src/main.cpp'} -o {validation_project_dir / 'src/main.o'}",
        #     "file": str(validation_project_dir / "src/main.cpp")
        # }
    ]
    
    with open(temp_compile_commands_path, 'w') as f:
        json.dump(correct_commands, f, indent=2)

    console.print(Panel.fit(
        f"[bold blue]🚀 开始分析 validation_project[/bold blue]\n"
        f"📁 项目目录: {validation_project_dir}\n"
        f"⚙️  编译命令: {temp_compile_commands_path} (动态生成)\n"
        f"📄 输出文件: {output_file}",
        title="分析配置"
    ))

    config = AnalysisConfig(
        project_root=str(validation_project_dir),
        scan_directory=str(validation_project_dir),
        compile_commands_path=str(temp_compile_commands_path),
        output_path=str(output_file),
        verbose=True,  # 启用详细输出以便调试
        num_jobs=4      # 使用4个进程
    )

    analyzer = CppAnalyzer(console=console)
    result = analyzer.analyze(config)

    if result.success:
        console.print(f"[bold green]✅ 分析成功完成！[/bold green]")
        console.print(f"结果已保存到: {result.output_path}")
        
        # 使用高级验证器
        validator = AdvancedValidator(console)
        validation_passed = validator.validate_analysis_result(result.output_path)
        
        if validation_passed:
            console.print(Panel.fit(
                "[bold green]🎉 所有验证测试通过！\n"
                "C++代码分析器功能正常工作。[/bold green]",
                title="验证结果"
            ))
        else:
            console.print(Panel.fit(
                "[bold red]⚠️  部分验证测试失败！\n"
                "请检查分析器的实现。[/bold red]",
                title="验证结果"
            ))
    else:
        console.print(f"[bold red]❌ 分析失败。[/bold red]")
        if result.statistics:
            console.print(f"失败阶段: {result.statistics.get('stage')}")
            console.print(f"原因: {result.statistics.get('reason')}")

if __name__ == '__main__':
    # 确保在Windows上多进程正常工作
    import platform
    if platform.system() == 'Windows':
        import multiprocessing
        multiprocessing.freeze_support()
    
    main()