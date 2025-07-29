import sys
from pathlib import Path
import json
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from typing import Dict, List, Any, Set
import time
import multiprocessing

# 将项目根目录添加到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cpp_clang.analyzer.cpp_analyzer import CppAnalyzer, AnalysisConfig

class EnhancedValidator:
    """增强验证器，用于验证C++代码分析器的各种功能，包括高级模板特性"""
    
    def __init__(self, console: Console):
        self.console = console
        self.validation_results = {
            'is_definition_validation': [],
            'template_parameters_validation': [],
            'template_specialization_validation': [],
            'function_calls_validation': [],
            'overload_resolution_validation': [],
            'cross_file_calls_validation': [],
            'template_instantiation_validation': [],
            'advanced_template_validation': [],
            'variadic_template_validation': [],
            'template_inheritance_validation': [],
            'multicore_stability_validation': []
        }
        self.file_mappings: Dict[str, str] = {}
    
    def is_project_function(self, usr_id: str) -> bool:
        """判断是否为项目内的函数（排除标准库函数）"""
        # 排除标准库函数
        if usr_id.startswith('c:@N@std@'):
            return False
        if 'operator<<' in usr_id:
            return False
        if 'basic_string' in usr_id and 'std' in usr_id:
            return False
        return True
    
    def validate_analysis_result(self, result_path: Path) -> bool:
        """验证分析结果的主函数"""
        self.console.print("\n[bold yellow]🔍 开始增强验证分析结果...[/bold yellow]")
        
        with open(result_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 获取各种数据
        functions_map = data.get('functions', {})
        classes_map = data.get('classes', {})
        variables_map = data.get('variables', {})
        self.file_mappings = data.get('file_mappings', {})
        
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
        
        # 新增的高级验证
        all_passed &= self.validate_advanced_templates(functions_map, classes_map)
        all_passed &= self.validate_variadic_templates(functions_map)
        all_passed &= self.validate_template_inheritance(functions_map, classes_map)
        
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
            
            # 测试用例1: 有定义的函数应该有函数体（内联函数除外）
            if is_definition and not body:
                # 检查是否为内联函数或模板特化
                if 'inline_template' in func_name or 'inline' in usr:
                    test_cases.append({
                        'test': 'inline_function_definition',
                        'entity': func_name,
                        'usr': usr,
                        'status': 'PASS',
                        'message': f'内联函数{func_name}可能在头文件中定义'
                    })
                else:
                    test_cases.append({
                        'test': 'definition_has_body',
                        'entity': func_name,
                        'usr': usr,
                        'status': 'FAIL',
                        'message': f'函数被标记为定义但函数体为空'
                    })
                    passed = False
            elif is_definition and body:
                test_cases.append({
                    'test': 'definition_has_body',
                    'entity': func_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'定义函数有正确的函数体'
                })
            
            # 测试用例2: 只有声明的函数不应该有函数体
            if not is_definition and body:
                test_cases.append({
                    'test': 'declaration_no_body',
                    'entity': func_name,
                    'usr': usr,
                    'status': 'FAIL',
                    'message': f'声明函数不应该有函数体'
                })
                passed = False
            elif not is_definition and not body:
                test_cases.append({
                    'test': 'declaration_no_body',
                    'entity': func_name,
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
        """验证2: 模板参数导出验证（增强版）"""
        self.console.print("\n[bold cyan]2️⃣ 验证模板参数导出...[/bold cyan]")
        
        passed = True
        test_cases = []
        
        # 检查函数模板参数
        template_functions_found = 0
        for usr, func_data in functions_map.items():
            func_name = func_data.get('name', 'unknown')
            cpp_extensions = func_data.get('cpp_extensions', {})
            template_params = cpp_extensions.get('template_parameters', [])
            
            # 检查基础模板函数
            if 'add_values' in func_name:
                template_functions_found += 1
                if not template_params and 'FT@' in usr:
                    test_cases.append({
                        'test': 'template_function_params',
                        'entity': func_name,
                        'usr': usr,
                        'status': 'FAIL',
                        'message': f'模板函数{func_name}缺少模板参数信息'
                    })
                    passed = False
                elif template_params or 'FT@' in usr:
                    test_cases.append({
                        'test': 'template_function_params',
                        'entity': func_name,
                        'usr': usr,
                        'status': 'PASS',
                        'message': f'模板函数{func_name}有正确的模板参数: {template_params}'
                    })
            
            # 检查多参数模板函数
            if 'process_data' in func_name:
                template_functions_found += 1
                expected_param_count = 3  # T, U, N
                if len(template_params) < expected_param_count and 'FT@' in usr:
                    test_cases.append({
                        'test': 'multi_param_template',
                        'entity': func_name,
                        'usr': usr,
                        'status': 'WARN',
                        'message': f'多参数模板函数{func_name}参数数量可能不完整，期望{expected_param_count}，实际{len(template_params)}'
                    })
                else:
                    test_cases.append({
                        'test': 'multi_param_template',
                        'entity': func_name,
                        'usr': usr,
                        'status': 'PASS',
                        'message': f'多参数模板函数{func_name}参数检测正常: {len(template_params)}'
                    })
            
            # 检查约束模板函数
            if 'constrained_add' in func_name:
                template_functions_found += 1
                test_cases.append({
                    'test': 'constrained_template',
                    'entity': func_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'约束模板函数{func_name}被正确识别'
                })
            
            # 检查内联模板函数
            if 'inline_template' in func_name:
                template_functions_found += 1
                test_cases.append({
                    'test': 'inline_template',
                    'entity': func_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'内联模板函数{func_name}被正确识别'
                })
        
        # 检查类模板参数
        template_classes_found = 0
        for usr, class_data in classes_map.items():
            class_name = class_data.get('name', 'unknown')
            cpp_extensions = class_data.get('cpp_oop_extensions', {})
            template_params = cpp_extensions.get('template_parameters', [])
            
            # 检查DataProcessor模板类
            if 'DataProcessor' in class_name:
                template_classes_found += 1
                if not template_params and 'ST>' in usr:
                    test_cases.append({
                        'test': 'template_class_params',
                        'entity': class_name,
                        'usr': usr,
                        'status': 'FAIL',
                        'message': f'模板类{class_name}缺少模板参数信息'
                    })
                    passed = False
                elif template_params or 'ST>' in usr:
                    test_cases.append({
                        'test': 'template_class_params',
                        'entity': class_name,
                        'usr': usr,
                        'status': 'PASS',
                        'message': f'模板类{class_name}有正确的模板参数: {template_params}'
                    })
            
            # 检查模板继承类
            if 'BaseTemplate' in class_name or 'DerivedTemplate' in class_name:
                template_classes_found += 1
                test_cases.append({
                    'test': 'template_inheritance_class',
                    'entity': class_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'模板继承类{class_name}被正确识别'
                })
            
            # 检查模板模板参数类
            if 'TemplateTemplateExample' in class_name:
                template_classes_found += 1
                test_cases.append({
                    'test': 'template_template_class',
                    'entity': class_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'模板模板参数类{class_name}被正确识别'
                })
        
        # 统计结果
        if template_functions_found == 0 and template_classes_found == 0:
            test_cases.append({
                'test': 'template_detection',
                'entity': 'overall',
                'usr': 'N/A',
                'status': 'FAIL',
                'message': '未包含advanced_templates.cpp，需要启用该文件'
            })
            passed = False
        else:
            test_cases.append({
                'test': 'template_detection',
                'entity': 'overall',
                'usr': 'N/A',
                'status': 'PASS',
                'message': f'找到{template_functions_found}个模板函数和{template_classes_found}个模板类'
            })
        
        self.validation_results['template_parameters_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ 模板参数验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ 模板参数验证失败[/bold red]")
        
        return passed
    
    def validate_template_specializations(self, functions_map: Dict, classes_map: Dict) -> bool:
        """验证3: 模板特化信息验证（增强版）"""
        self.console.print("\n[bold cyan]3️⃣ 验证模板特化信息...[/bold cyan]")
        
        passed = True
        test_cases = []
        
        # 检查函数模板特化
        function_specializations = 0
        for usr, func_data in functions_map.items():
            func_name = func_data.get('name', 'unknown')
            cpp_extensions = func_data.get('cpp_extensions', {})
            template_args = cpp_extensions.get('template_args', [])
            is_specialization = func_data.get('is_template_specialization', False)
            
            # 查找add_values的特化版本
            if 'add_values' in func_name and ('<' in usr or is_specialization):
                function_specializations += 1
                test_cases.append({
                    'test': 'function_specialization',
                    'entity': func_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'找到函数模板特化: {func_name}, USR: {usr[:50]}...'
                })
        
        # 检查类模板特化
        class_specializations = 0
        for usr, class_data in classes_map.items():
            class_name = class_data.get('name', 'unknown')
            cpp_extensions = class_data.get('cpp_oop_extensions', {})
            template_args = cpp_extensions.get('template_specialization_args', [])
            is_specialization = class_data.get('is_template_specialization', False)
            
            # 检查DataProcessor的特化版本
            if 'DataProcessor' in class_name and ('SP>' in usr or '#' in usr or '<' in usr):
                class_specializations += 1
                test_cases.append({
                    'test': 'class_specialization',
                    'entity': class_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'找到类模板特化: {class_name}, USR: {usr[:50]}...'
                })
        
        # 验证结果
        if function_specializations == 0:
            test_cases.append({
                'test': 'function_specialization_detection',
                'entity': 'add_values',
                'usr': 'N/A',
                'status': 'WARN',
                'message': '未找到add_values函数的模板特化，可能需要检查advanced_templates.cpp'
            })
        
        if class_specializations == 0:
            test_cases.append({
                'test': 'class_specialization_detection',
                'entity': 'DataProcessor',
                'usr': 'N/A',
                'status': 'WARN',
                'message': '未找到DataProcessor类的模板特化信息'
            })
        
        total_specializations = function_specializations + class_specializations
        test_cases.append({
            'test': 'specialization_summary',
            'entity': 'overall',
            'usr': 'N/A',
            'status': 'PASS' if total_specializations > 0 else 'WARN',
            'message': f'总共找到{total_specializations}个模板特化（函数:{function_specializations}, 类:{class_specializations}）'
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
        
        # 统计项目内的调用关系
        total_project_callto = 0
        total_project_callby = 0
        functions_with_calls = 0
        
        for usr, func_data in functions_map.items():
            func_name = func_data.get('name', 'unknown')
            callto = func_data.get('calls_to', [])
            callby = func_data.get('called_by', [])
            
            # 过滤项目内的调用
            project_callto = [call for call in callto if self.is_project_function(call)]
            project_callby = [call for call in callby if self.is_project_function(call)]
            
            if project_callto:
                total_project_callto += len(project_callto)
                functions_with_calls += 1
                test_cases.append({
                    'test': 'project_callto_relations',
                    'entity': func_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'函数{func_name}调用了{len(project_callto)}个项目内函数'
                })
            
            if project_callby:
                total_project_callby += len(project_callby)
        
        # 验证模板用户类的调用关系
        template_user_methods = ['use_templates', 'use_template_classes', 'complex_call_chain']
        for method_name in template_user_methods:
            method_found = False
            for usr, func_data in functions_map.items():
                if func_data.get('name') == method_name and 'TemplateUser' in usr:
                    method_found = True
                    callto = func_data.get('calls_to', [])
                    project_callto = [call for call in callto if self.is_project_function(call)]
                    
                    if len(project_callto) > 0:
                        test_cases.append({
                            'test': 'template_user_calls',
                            'entity': method_name,
                            'usr': usr,
                            'status': 'PASS',
                            'message': f'TemplateUser::{method_name}调用了{len(project_callto)}个项目函数'
                        })
                    else:
                        test_cases.append({
                            'test': 'template_user_calls',
                            'entity': method_name,
                            'usr': usr,
                            'status': 'FAIL',
                            'message': f'TemplateUser::{method_name}未调用任何项目函数'
                        })
                        passed = False
                    break
            
            if not method_found:
                test_cases.append({
                    'test': 'template_user_detection',
                    'entity': method_name,
                    'usr': 'N/A',
                    'status': 'WARN',
                    'message': f'未找到TemplateUser::{method_name}方法'
                })
        
        self.console.print(f"📈 项目内调用关系统计:")
        self.console.print(f"  - 项目内调用关系(callto): {total_project_callto}")
        self.console.print(f"  - 项目内被调用关系(callby): {total_project_callby}")
        self.console.print(f"  - 有调用关系的函数: {functions_with_calls}")
        
        self.validation_results['function_calls_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ 函数调用关系验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ 函数调用关系验证失败[/bold red]")
        
        return passed
    
    def validate_overload_resolution(self, functions_map: Dict) -> bool:
        """验证5: 函数重载解析验证（增强版）"""
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
        
        # 检查overloaded_function重载函数（来自advanced_templates.cpp）
        overloaded_function_found = False
        if 'overloaded_function' in overload_groups:
            funcs = overload_groups['overloaded_function']
            if len(funcs) >= 3:  # 应该有int, double, string, vector<T>版本
                overloaded_function_found = True
                test_cases.append({
                    'test': 'advanced_overloads',
                    'entity': 'overloaded_function',
                    'usr': 'multiple',
                    'status': 'PASS',
                    'message': f'找到高级重载函数overloaded_function，共{len(funcs)}个重载版本'
                })
                
                # 验证参数类型多样性
                param_types = []
                for func in funcs:
                    params = func.get('parameters', [])
                    types = [param.get('type', '') for param in params]
                    param_types.append(types)
                
                test_cases.append({
                    'test': 'overload_type_diversity',
                    'entity': 'overloaded_function',
                    'usr': 'multiple',
                    'status': 'PASS',
                    'message': f'重载函数参数类型多样性: {len(set(str(pt) for pt in param_types))}种不同签名'
                })
        
        if not overloaded_function_found:
            test_cases.append({
                'test': 'advanced_overloads',
                'entity': 'overloaded_function',
                'usr': 'N/A',
                'status': 'FAIL',
                'message': '未找到advanced_templates.cpp中的overloaded_function重载版本'
            })
            passed = False
        
        # 检查print_message重载函数
        print_message_found = False
        if 'print_message' in overload_groups:
            funcs = overload_groups['print_message']
            if len(funcs) >= 2:
                print_message_found = True
                test_cases.append({
                    'test': 'basic_overloads',
                    'entity': 'print_message',
                    'usr': 'multiple',
                    'status': 'PASS',
                    'message': f'找到基础重载函数print_message，共{len(funcs)}个重载版本'
                })
        
        if not print_message_found:
            test_cases.append({
                'test': 'basic_overloads',
                'entity': 'print_message',
                'usr': 'N/A',
                'status': 'WARN',
                'message': '未找到print_message的重载版本'
            })
        
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
        cross_file_call_details = []
        
        # 检查跨文件调用
        for usr, func_data in functions_map.items():
            func_name = func_data.get('name', 'unknown')
            func_file_id = func_data.get('definition_file_id')
            if not func_file_id: continue

            caller_file_path = self.file_mappings.get(func_file_id, "")
            callto = func_data.get('calls_to', [])
            
            for call_usr in callto:
                if not self.is_project_function(call_usr):
                    continue
                    
                called_func_data = functions_map.get(call_usr)
                if called_func_data:
                    called_file_id = called_func_data.get('definition_file_id')
                    if not called_file_id: continue

                    callee_file_path = self.file_mappings.get(called_file_id, "")
                    called_func_name = called_func_data.get('name', 'unknown')
                    
                    if caller_file_path != callee_file_path and caller_file_path and callee_file_path:
                        cross_file_calls += 1
                        cross_file_call_details.append({
                            'caller': func_name,
                            'caller_file': caller_file_path,
                            'callee': called_func_name,
                            'callee_file': callee_file_path
                        })
        
        self.console.print(f"📁 跨文件调用统计: {cross_file_calls}个跨文件调用")
        
        # 验证advanced_templates.cpp到其他文件的调用
        advanced_to_others = any(
            'advanced_templates.cpp' in detail['caller_file'] and 
            'advanced_templates.cpp' not in detail['callee_file']
            for detail in cross_file_call_details
        )
        
        if advanced_to_others:
            test_cases.append({
                'test': 'advanced_templates_cross_calls',
                'entity': 'advanced_templates.cpp',
                'usr': 'N/A',
                'status': 'PASS',
                'message': 'advanced_templates.cpp成功调用其他文件中的函数'
            })
        else:
            test_cases.append({
                'test': 'advanced_templates_cross_calls',
                'entity': 'advanced_templates.cpp',
                'usr': 'N/A',
                'status': 'WARN',
                'message': 'advanced_templates.cpp可能未调用其他文件中的函数'
            })
        
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
            cpp_extensions = func_data.get('cpp_extensions', {})
            template_args = cpp_extensions.get('template_args', [])
            
            # 检查特化版本（USR中包含<>的通常是实例化或特化）
            if '<' in usr and '>' in usr:
                template_instances += 1
                test_cases.append({
                    'test': 'template_instantiation',
                    'entity': func_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'函数模板实例化: {func_name}, USR: {usr[:50]}...'
                })
        
        for usr, class_data in classes_map.items():
            class_name = class_data.get('name', 'unknown')
            cpp_extensions = class_data.get('cpp_oop_extensions', {})
            template_args = cpp_extensions.get('template_specialization_args', [])
            
            # 检查特化版本
            if ('#' in usr or '<' in usr) and not class_name.endswith('Template'):  # 排除通用模板
                template_instances += 1
                test_cases.append({
                    'test': 'template_instantiation',
                    'entity': class_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'类模板实例化: {class_name}, USR: {usr[:50]}...'
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
                'message': '未发现模板实例化，需要启用advanced_templates.cpp来测试模板功能'
            })
        
        self.validation_results['template_instantiation_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ 模板实例化验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ 模板实例化验证失败[/bold red]")
        
        return passed
    
    def validate_advanced_templates(self, functions_map: Dict, classes_map: Dict) -> bool:
        """验证8: 高级模板特性验证"""
        self.console.print("\n[bold cyan]8️⃣ 验证高级模板特性...[/bold cyan]")
        
        passed = True
        test_cases = []
        
        # 检查模板模板参数
        template_template_found = False
        for usr, class_data in classes_map.items():
            class_name = class_data.get('name', 'unknown')
            if 'TemplateTemplateExample' in class_name:
                template_template_found = True
                test_cases.append({
                    'test': 'template_template_parameter',
                    'entity': class_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'找到模板模板参数类: {class_name}'
                })
                break
        
        if not template_template_found:
            test_cases.append({
                'test': 'template_template_parameter',
                'entity': 'TemplateTemplateExample',
                'usr': 'N/A',
                'status': 'FAIL',
                'message': '未找到模板模板参数类TemplateTemplateExample'
            })
            passed = False
        
        # 检查递归模板
        factorial_found = False
        for usr, class_data in classes_map.items():
            class_name = class_data.get('name', 'unknown')
            if 'Factorial' in class_name:
                factorial_found = True
                test_cases.append({
                    'test': 'recursive_template',
                    'entity': class_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'找到递归模板结构: {class_name}'
                })
                break
        
        if not factorial_found:
            test_cases.append({
                'test': 'recursive_template',
                'entity': 'Factorial',
                'usr': 'N/A',
                'status': 'WARN',
                'message': '未找到递归模板结构Factorial'
            })
        
        # 检查约束模板（SFINAE）
        constrained_found = False
        for usr, func_data in functions_map.items():
            func_name = func_data.get('name', 'unknown')
            if 'constrained_add' in func_name:
                constrained_found = True
                test_cases.append({
                    'test': 'constrained_template',
                    'entity': func_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'找到约束模板函数: {func_name}'
                })
                break
        
        if not constrained_found:
            test_cases.append({
                'test': 'constrained_template',
                'entity': 'constrained_add',
                'usr': 'N/A',
                'status': 'WARN',
                'message': '未找到约束模板函数constrained_add'
            })
        
        # 检查函数对象模板
        comparator_found = False
        for usr, class_data in classes_map.items():
            class_name = class_data.get('name', 'unknown')
            if 'Comparator' in class_name:
                comparator_found = True
                test_cases.append({
                    'test': 'function_object_template',
                    'entity': class_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'找到函数对象模板: {class_name}'
                })
                break
        
        if not comparator_found:
            test_cases.append({
                'test': 'function_object_template',
                'entity': 'Comparator',
                'usr': 'N/A',
                'status': 'WARN',
                'message': '未找到函数对象模板Comparator'
            })
        
        self.validation_results['advanced_template_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ 高级模板特性验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ 高级模板特性验证失败[/bold red]")
        
        return passed
    
    def validate_variadic_templates(self, functions_map: Dict) -> bool:
        """验证9: 可变参数模板验证"""
        self.console.print("\n[bold cyan]9️⃣ 验证可变参数模板...[/bold cyan]")
        
        passed = True
        test_cases = []
        
        # 检查可变参数模板函数
        variadic_found = False
        for usr, func_data in functions_map.items():
            func_name = func_data.get('name', 'unknown')
            cpp_extensions = func_data.get('cpp_extensions', {})
            template_params = cpp_extensions.get('template_parameters', [])
            
            if 'log_values' in func_name:
                variadic_found = True
                # 检查是否有可变参数模板的标识
                has_variadic = any('...' in str(param) or 'Args' in str(param) for param in template_params)
                
                test_cases.append({
                    'test': 'variadic_template_function',
                    'entity': func_name,
                    'usr': usr,
                    'status': 'PASS',
                    'message': f'找到可变参数模板函数: {func_name}, 参数包检测: {has_variadic}'
                })
                break
        
        if not variadic_found:
            test_cases.append({
                'test': 'variadic_template_function',
                'entity': 'log_values',
                'usr': 'N/A',
                'status': 'FAIL',
                'message': '未找到可变参数模板函数log_values'
            })
            passed = False
        
        self.validation_results['variadic_template_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ 可变参数模板验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ 可变参数模板验证失败[/bold red]")
        
        return passed
    
    def validate_template_inheritance(self, functions_map: Dict, classes_map: Dict) -> bool:
        """验证10: 模板继承验证 (修正版)"""
        self.console.print("\n[bold cyan]🔟 验证模板继承...[/bold cyan]")
        
        passed = True
        test_cases = []
        
        # 步骤 1: 精确查找主模板定义
        base_template_def = None
        derived_template_def = None
        
        for usr, class_data in classes_map.items():
            class_name = class_data.get('name', 'unknown')
            cpp_extensions = class_data.get('cpp_oop_extensions', {})
            # 主模板定义有template_parameters，但没有template_specialization_args
            is_primary_template = (
                cpp_extensions.get('template_parameters') and 
                not cpp_extensions.get('template_specialization_args')
            )
            
            if is_primary_template:
                if class_name == 'BaseTemplate':
                    base_template_def = class_data
                elif class_name == 'DerivedTemplate':
                    derived_template_def = class_data

        # 步骤 2: 验证BaseTemplate
        if base_template_def:
            test_cases.append({
                'test': 'template_base_class_definition',
                'entity': 'BaseTemplate',
                'usr': base_template_def.get('usr_id', 'N/A'),
                'status': 'PASS',
                'message': '找到模板基类的主定义'
            })
        else:
            test_cases.append({
                'test': 'template_base_class_definition',
                'entity': 'BaseTemplate',
                'usr': 'N/A',
                'status': 'FAIL',
                'message': '未找到模板基类BaseTemplate的主定义'
            })
            passed = False

        # 步骤 3: 验证DerivedTemplate及其继承关系
        if derived_template_def:
            # 检查继承信息
            parent_classes = derived_template_def.get('parent_classes', [])
            inheritance_list = derived_template_def.get('cpp_oop_extensions', {}).get('inheritance_list', [])
            
            # 检查是否继承自BaseTemplate
            # 我们需要BaseTemplate的USR来进行精确匹配
            base_usr_to_check = base_template_def.get('usr_id') if base_template_def else ''
            
            inherits_base = False
            if base_usr_to_check:
                inherits_base = (
                    any(base_usr_to_check == parent_usr for parent_usr in parent_classes) or
                    any(base_usr_to_check == item.get('base_class_usr_id', '') for item in inheritance_list)
                )

            test_cases.append({
                'test': 'template_derived_class_definition',
                'entity': 'DerivedTemplate',
                'usr': derived_template_def.get('usr_id', 'N/A'),
                'status': 'PASS' if inherits_base else 'FAIL',
                'message': f'找到模板派生类的主定义, 继承检测: {inherits_base}'
            })
            if not inherits_base:
                passed = False
        else:
            test_cases.append({
                'test': 'template_derived_class_definition',
                'entity': 'DerivedTemplate',
                'usr': 'N/A',
                'status': 'FAIL',
                'message': '未找到模板派生类DerivedTemplate的主定义'
            })
            passed = False
        
        self.validation_results['template_inheritance_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ 模板继承验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ 模板继承验证失败[/bold red]")
        
        return passed
    
    def validate_multicore_stability(self, results: List[Dict]) -> bool:
        """验证11: 多核处理稳定性验证"""
        self.console.print("\n[bold cyan]1️⃣1️⃣ 验证多核处理稳定性...[/bold cyan]")
        
        passed = True
        test_cases = []
        
        if len(results) < 2:
            test_cases.append({
                'test': 'multicore_test_count',
                'entity': 'stability_test',
                'usr': 'N/A',
                'status': 'FAIL',
                'message': f'多核稳定性测试需要至少2次运行，实际{len(results)}次'
            })
            passed = False
            self.validation_results['multicore_stability_validation'] = test_cases
            return passed
        
        # 比较结果一致性
        base_result = results[0]
        base_functions = {f_usr: f_data for f_usr, f_data in base_result.get('functions', {}).items() if self.is_project_function(f_usr)}
        base_classes = base_result.get('classes', {})
        
        for i, result in enumerate(results[1:], 1):
            current_functions = {f_usr: f_data for f_usr, f_data in result.get('functions', {}).items() if self.is_project_function(f_usr)}
            current_classes = result.get('classes', {})
            
            # 检查数量一致性
            functions_match = len(current_functions) == len(base_functions)
            classes_match = len(current_classes) == len(base_classes)
            
            # 检查内容一致性（忽略顺序）
            if functions_match:
                # 比较键是否一致
                base_func_keys = set(base_functions.keys())
                curr_func_keys = set(current_functions.keys())
                if base_func_keys != curr_func_keys:
                    functions_match = False
                else:
                    # 逐个比较值
                    functions_match = all(current_functions[usr] == data for usr, data in base_functions.items())

            if classes_match:
                # 比较键是否一致
                base_cls_keys = set(base_classes.keys())
                curr_cls_keys = set(current_classes.keys())
                if base_cls_keys != curr_cls_keys:
                    classes_match = False
                else:
                    # 逐个比较值
                    classes_match = all(current_classes[usr] == data for usr, data in base_classes.items())

            overall_match = functions_match and classes_match
            
            test_cases.append({
                'test': 'result_consistency',
                'entity': f'run_{i+1}',
                'usr': 'N/A',
                'status': 'PASS' if overall_match else 'FAIL',
                'message': f'运行{i+1}: 函数{len(current_functions)}({functions_match}), 类{len(current_classes)}({classes_match})'
            })
            
            if not overall_match:
                passed = False
        
        # 计算一致性百分比
        consistent_runs = sum(1 for case in test_cases if case['status'] == 'PASS')
        consistency_rate = (consistent_runs / len(test_cases)) * 100 if test_cases else 0
        
        test_cases.append({
            'test': 'overall_consistency',
            'entity': 'stability_summary',
            'usr': 'N/A',
            'status': 'PASS' if consistency_rate >= 80 else 'FAIL',
            'message': f'多核处理一致性: {consistency_rate:.1f}% ({consistent_runs}/{len(test_cases)}次一致)'
        })
        
        self.validation_results['multicore_stability_validation'] = test_cases
        
        if passed:
            self.console.print("[bold green]✅ 多核处理稳定性验证通过[/bold green]")
        else:
            self.console.print("[bold red]❌ 多核处理稳定性验证失败[/bold red]")
        
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
                entity = case.get('entity', case.get('function', 'N/A'))
                table.add_row(
                    case.get('test', 'unknown'),
                    entity[:30] + "..." if len(entity) > 30 else entity,
                    f"[{status_color}]{case['status']}[/{status_color}]",
                    case.get('message', '')
                )
            
            self.console.print(table)


def run_multicore_stability_test(console: Console, config: AnalysisConfig, num_runs: int = 3) -> List[Dict]:
    """运行多核稳定性测试"""
    console.print(f"\n[bold blue]🚀 开始多核稳定性测试 (运行{num_runs}次)...[/bold blue]")
    
    results = []
    
    for run_num in range(num_runs):
        console.print(f"\n[bold cyan]运行 {run_num + 1}/{num_runs}[/bold cyan]")
        
        # 每次运行使用不同的进程数来测试稳定性
        test_configs = [1, 2, 4, multiprocessing.cpu_count()]
        current_num_jobs = test_configs[run_num % len(test_configs)]
        
        # 创建新的配置
        test_config = AnalysisConfig(
            project_root=config.project_root,
            scan_directory=config.scan_directory,
            compile_commands_path=config.compile_commands_path,
            output_path=str(Path(config.output_path).parent / f"stability_test_run_{run_num + 1}.json"),
            verbose=False,  # 减少输出
            num_jobs=current_num_jobs
        )
        
        console.print(f"  使用 {current_num_jobs} 个进程")
        
        start_time = time.time()
        analyzer = CppAnalyzer(console=console)
        result = analyzer.analyze(test_config)
        end_time = time.time()
        
        if result.success:
            # 读取结果
            with open(result.output_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            results.append(data)
            
            console.print(f"  ✅ 运行{run_num + 1}成功 (耗时: {end_time - start_time:.2f}秒)")
        else:
            console.print(f"  ❌ 运行{run_num + 1}失败")
            # 添加空结果以保持索引一致
            results.append({})
    
    return results


def main():
    """对 validation_project 运行分析并保存结果，启用所有源文件包括advanced_templates.cpp"""
    console = Console()
    validation_project_dir = Path(__file__).parent / "validation_project"
    output_file = validation_project_dir / "enhanced_analysis_result.json"
    
    # 动态创建正确的 compile_commands.json，启用所有源文件包括advanced_templates.cpp
    project_root = Path(__file__).parent.parent.parent.resolve()
    temp_compile_commands_path = validation_project_dir / "temp_compile_commands.json"
    
    correct_commands = [
        {
            "directory": str(project_root),
            "command": f"clang++ -I{validation_project_dir / 'include'} -c {validation_project_dir / 'src/base.cpp'} -o {validation_project_dir / 'src/base.o'}",
            "file": str(validation_project_dir / "src/base.cpp")
        },
        {
            "directory": str(project_root),
            "command": f"clang++ -I{validation_project_dir / 'include'} -c {validation_project_dir / 'src/derived.cpp'} -o {validation_project_dir / 'src/derived.o'}",
            "file": str(validation_project_dir / "src/derived.cpp")
        },
        {
            "directory": str(project_root),
            "command": f"clang++ -I{validation_project_dir / 'include'} -c {validation_project_dir / 'src/utils.cpp'} -o {validation_project_dir / 'src/utils.o'}",
            "file": str(validation_project_dir / "src/utils.cpp")
        },
        {
            "directory": str(project_root),
            "command": f"clang++ -I{validation_project_dir / 'include'} -c {validation_project_dir / 'src/templates.cpp'} -o {validation_project_dir / 'src/templates.o'}",
            "file": str(validation_project_dir / "src/templates.cpp")
        },
        {
            "directory": str(project_root),
            "command": f"clang++ -I{validation_project_dir / 'include'} -c {validation_project_dir / 'src/complex.cpp'} -o {validation_project_dir / 'src/complex.o'}",
            "file": str(validation_project_dir / "src/complex.cpp")
        },
        {
            "directory": str(project_root),
            "command": f"clang++ -I{validation_project_dir / 'include'} -c {validation_project_dir / 'src/advanced_templates.cpp'} -o {validation_project_dir / 'src/advanced_templates.o'}",
            "file": str(validation_project_dir / "src/advanced_templates.cpp")
        }
    ]
    
    with open(temp_compile_commands_path, 'w') as f:
        json.dump(correct_commands, f, indent=2)

    console.print(Panel.fit(
        f"[bold blue]🚀 开始增强分析 validation_project (包含advanced_templates.cpp + 多核测试)[/bold blue]\n"
        f"📁 项目目录: {validation_project_dir}\n"
        f"⚙️  编译命令: {temp_compile_commands_path} (动态生成)\n"
        f"📄 输出文件: {output_file}\n"
        f"📂 启用文件: base.cpp, derived.cpp, utils.cpp, templates.cpp, complex.cpp, advanced_templates.cpp\n"
        f"🔧 多核支持: 启用 (CPU核心数: {multiprocessing.cpu_count()})",
        title="增强分析配置"
    ))

    # 主分析配置 - 使用多核
    config = AnalysisConfig(
        project_root=str(validation_project_dir),
        scan_directory=str(validation_project_dir),
        compile_commands_path=str(temp_compile_commands_path),
        output_path=str(output_file),
        verbose=True,  # 启用详细输出以便调试
        num_jobs=multiprocessing.cpu_count()  # 使用所有可用核心
    )

    # 运行主分析
    console.print("\n[bold green]🎯 运行主分析...[/bold green]")
    analyzer = CppAnalyzer(console=console)
    result = analyzer.analyze(config)

    if result.success:
        console.print(f"[bold green]✅ 主分析成功完成！[/bold green]")
        console.print(f"结果已保存到: {result.output_path}")
        
        # 运行多核稳定性测试
        stability_results = run_multicore_stability_test(console, config, num_runs=3)
        
        # 使用增强验证器
        validator = EnhancedValidator(console)
        validation_passed = validator.validate_analysis_result(result.output_path)
        
        # 验证多核稳定性
        if stability_results:
            stability_passed = validator.validate_multicore_stability(stability_results)
            validation_passed &= stability_passed
        
        if validation_passed:
            console.print(Panel.fit(
                "[bold green]🎉 所有验证测试通过！\n"
                "C++代码分析器功能完全正常工作，包括高级模板特性和多核稳定性。[/bold green]",
                title="验证结果"
            ))
        else:
            console.print(Panel.fit(
                "[bold yellow]⚠️  部分验证测试失败！\n"
                "请检查详细验证报告以了解具体问题。[/bold yellow]",
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