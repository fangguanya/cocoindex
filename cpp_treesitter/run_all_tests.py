#!/usr/bin/env python3
"""
综合测试脚本，运行所有C++分析测试用例并验证结果
"""

import os
import sys
import json
import subprocess
import logging
from pathlib import Path

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TestRunner:
    def __init__(self):
        self.base_dir = Path(__file__).parent
        self.analyzer_script = self.base_dir / "analyze_cpp_project.py"
        self.test_cases = [
            {
                "name": "简单示例测试",
                "project_path": "test_demo",
                "output_path": "test_demo/analysis_results",
                "expected_features": {
                    "min_functions": 8,
                    "min_classes": 3,
                    "min_inheritance": 3,
                    "min_calls": 7
                }
            },
            {
                "name": "丰富示例测试",
                "project_path": "rich_demo",
                "output_path": "rich_demo/analysis_results",
                "expected_features": {
                    "min_functions": 80,
                    "min_classes": 15,
                    "min_inheritance": 10,
                    "min_calls": 50,
                    "has_templates": True,
                    "has_multiple_inheritance": True,
                    "min_files": 8
                }
            }
        ]
        
    def run_analyzer(self, project_path: str, output_path: str) -> bool:
        """运行C++分析器"""
        try:
            cmd = [
                sys.executable, str(self.analyzer_script),
                "-p", project_path,
                "-o", output_path,
                "--verbose"
            ]
            
            logger.info(f"运行命令: {' '.join(cmd)}")
            result = subprocess.run(cmd, cwd=self.base_dir, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"分析成功完成: {project_path}")
                return True
            else:
                logger.error(f"分析失败: {project_path}")
                logger.error(f"错误输出: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"运行分析器时出错: {e}")
            return False
    
    def load_analysis_results(self, output_path: str) -> dict:
        """加载分析结果文件"""
        results = {}
        output_dir = self.base_dir / output_path
        
        # 加载主要结果文件
        main_result_file = output_dir / "cpp_treesitter_analysis_result.json"
        if main_result_file.exists():
            with open(main_result_file, 'r', encoding='utf-8') as f:
                results['main'] = json.load(f)
        
        # 加载节点文件
        nodes_file = output_dir / "nodes.json"
        if nodes_file.exists():
            with open(nodes_file, 'r', encoding='utf-8') as f:
                results['nodes'] = json.load(f)
        
        # 加载文件映射
        file_mappings_file = output_dir / "file_mappings.json"
        if file_mappings_file.exists():
            with open(file_mappings_file, 'r', encoding='utf-8') as f:
                results['file_mappings'] = json.load(f)
        
        return results
    
    def verify_relative_paths(self, results: dict, test_name: str) -> bool:
        """验证相对路径"""
        logger.info(f"验证相对路径 - {test_name}")
        
        if 'file_mappings' not in results:
            logger.error("缺少文件映射")
            return False
        
        file_mappings_data = results['file_mappings']
        # 检查文件映射格式
        if isinstance(file_mappings_data, dict) and 'file_mappings' in file_mappings_data:
            file_mappings = file_mappings_data['file_mappings']
        else:
            file_mappings = file_mappings_data
        
        relative_path_count = 0
        
        for file_id, file_path in file_mappings.items():
            if isinstance(file_path, str) and not os.path.isabs(file_path):
                relative_path_count += 1
                logger.info(f"  ✓ 相对路径: {file_path}")
            elif isinstance(file_path, str):
                logger.warning(f"  ✗ 绝对路径: {file_path}")
            else:
                logger.warning(f"  ✗ 无效路径格式: {file_path}")
        
        success = relative_path_count == len(file_mappings)
        logger.info(f"相对路径验证: {'通过' if success else '失败'} ({relative_path_count}/{len(file_mappings)})")
        return success
    
    def verify_function_exports(self, results: dict, test_name: str) -> bool:
        """验证函数导出（location和code_content）"""
        logger.info(f"验证函数导出 - {test_name}")
        
        # 检查主结果文件中的函数
        main_data = results.get('main', {})
        entities = main_data.get('entities', {})
        main_functions = entities.get('functions', {})
        
        functions_with_location = 0
        functions_with_content = 0
        definition_functions = 0
        declaration_functions = 0
        invalid_definitions = []
        invalid_declarations = []
        
        # main_functions 是一个字典，键是函数ID，值是函数信息
        for func_id, func_info in main_functions.items():
            is_definition = func_info.get('is_definition', False)
            has_location = 'location' in func_info
            has_content = 'code_content' in func_info
            code_content = func_info.get('code_content', '')
            func_name = func_info.get('name', 'Unknown')
            
            if has_location:
                functions_with_location += 1
            if has_content:
                functions_with_content += 1
            
            if is_definition:
                definition_functions += 1
                # 验证规则1：函数定义必须有函数体内容字段（即使内容为空，如= default函数）
                if not has_content:
                    invalid_definitions.append({
                        'name': func_name,
                        'id': func_id,
                        'issue': 'definition_without_content_field',
                        'has_content': has_content
                    })
                # 验证规则2：函数定义必须有location信息
                if not has_location:
                    invalid_definitions.append({
                        'name': func_name,
                        'id': func_id,
                        'issue': 'definition_without_location',
                        'has_location': has_location
                    })
                
                # 特殊检查：如果函数体为空但有location，可能是= default或= delete函数，这是合法的
                if has_content and not code_content.strip() and has_location:
                    # 这是正常情况，如 virtual ~Base() = default; 
                    pass
            else:
                declaration_functions += 1
                # 验证规则2：函数声明不应该有location和code_content
                if has_location:
                    invalid_declarations.append({
                        'name': func_name,
                        'id': func_id,
                        'issue': 'declaration_with_location'
                    })
                if has_content and code_content.strip():
                    invalid_declarations.append({
                        'name': func_name,
                        'id': func_id,
                        'issue': 'declaration_with_body'
                    })
        
        logger.info(f"  主结果文件: {functions_with_location}个函数有location, {functions_with_content}个有code_content")
        logger.info(f"  函数类型统计: {definition_functions}个定义, {declaration_functions}个声明")
        
        # 检查nodes.json中的函数
        nodes_functions = 0
        nodes_with_location = 0
        nodes_with_content = 0
        nodes_definition_functions = 0
        nodes_declaration_functions = 0
        nodes_invalid_definitions = []
        nodes_invalid_declarations = []
        
        if 'nodes' in results:
            nodes_data = results['nodes']
            # 检查nodes.json格式
            if isinstance(nodes_data, dict) and 'entities' in nodes_data:
                entities = nodes_data['entities']
                for node_id, node_info in entities.items():
                    if node_info.get('type') == 'function':
                        nodes_functions += 1
                        node_data = node_info.get('data', {})
                        is_definition = node_data.get('is_definition', False)
                        has_location = 'location' in node_data
                        has_content = 'code_content' in node_data
                        code_content = node_data.get('code_content', '')
                        func_name = node_data.get('name', 'Unknown')
                        
                        if has_location:
                            nodes_with_location += 1
                        if has_content:
                            nodes_with_content += 1
                        
                        if is_definition:
                            nodes_definition_functions += 1
                            # 验证函数定义的规则
                            if not has_content:
                                nodes_invalid_definitions.append({
                                    'name': func_name,
                                    'id': node_id,
                                    'issue': 'definition_without_content_field'
                                })
                            if not has_location:
                                nodes_invalid_definitions.append({
                                    'name': func_name,
                                    'id': node_id,
                                    'issue': 'definition_without_location'
                                })
                            # 空函数体但有location是合法的（= default/= delete函数）
                        else:
                            nodes_declaration_functions += 1
                            # 验证函数声明的规则
                            if has_location:
                                nodes_invalid_declarations.append({
                                    'name': func_name,
                                    'id': node_id,
                                    'issue': 'declaration_with_location'
                                })
                            if has_content and code_content.strip():
                                nodes_invalid_declarations.append({
                                    'name': func_name,
                                    'id': node_id,
                                    'issue': 'declaration_with_body'
                                })
            elif isinstance(nodes_data, list):
                # 旧格式，作为列表处理
                for node in nodes_data:
                    if isinstance(node, dict) and node.get('type') == 'function':
                        nodes_functions += 1
                        if 'location' in node:
                            nodes_with_location += 1
                        if 'code_content' in node:
                            nodes_with_content += 1
        
        logger.info(f"  节点文件: {nodes_with_location}/{nodes_functions}个函数有location, {nodes_with_content}/{nodes_functions}个有code_content")
        logger.info(f"  节点函数类型: {nodes_definition_functions}个定义, {nodes_declaration_functions}个声明")
        
        # 报告验证问题
        if invalid_definitions:
            logger.warning(f"  发现{len(invalid_definitions)}个有问题的函数定义:")
            for issue in invalid_definitions[:3]:  # 只显示前3个
                logger.warning(f"    {issue['name']}: {issue['issue']}")
        
        if invalid_declarations:
            logger.warning(f"  发现{len(invalid_declarations)}个有问题的函数声明:")
            for issue in invalid_declarations[:3]:  # 只显示前3个
                logger.warning(f"    {issue['name']}: {issue['issue']}")
        
        if nodes_invalid_definitions:
            logger.warning(f"  节点文件中发现{len(nodes_invalid_definitions)}个有问题的函数定义")
        
        if nodes_invalid_declarations:
            logger.warning(f"  节点文件中发现{len(nodes_invalid_declarations)}个有问题的函数声明")
        
        # 检查是否所有函数定义都有location和code_content
        basic_success = (functions_with_location > 0 and functions_with_content > 0 and 
                        nodes_with_location > 0 and nodes_with_content > 0)
        
        # 函数体验证成功条件：
        # 1. 基本功能正常
        # 2. 没有无效的函数定义（定义必须有body和location）
        # 3. 没有无效的函数声明（声明不应该有location和非空body）
        validation_success = (basic_success and 
                             len(invalid_definitions) == 0 and 
                             len(invalid_declarations) == 0 and
                             len(nodes_invalid_definitions) == 0 and 
                             len(nodes_invalid_declarations) == 0)
        
        logger.info(f"函数导出验证: {'通过' if validation_success else '失败'}")
        if not validation_success:
            if not basic_success:
                logger.error("  基本功能验证失败")
            if invalid_definitions or nodes_invalid_definitions:
                logger.error("  函数定义验证失败：存在没有函数体或location的定义")
            if invalid_declarations or nodes_invalid_declarations:
                logger.error("  函数声明验证失败：存在带有location或函数体的声明")
        
        return validation_success
    
    def verify_inheritance_relationships(self, results: dict, test_name: str, min_expected: int = 3) -> bool:
        """验证继承关系"""
        logger.info(f"验证继承关系 - {test_name}")
        
        main_data = results.get('main', {})
        entities = main_data.get('entities', {})
        inheritance_relations = entities.get('inheritance_relations', [])
        inheritance_count = len(inheritance_relations)
        
        # 统计继承关系：按派生类分组计算基类数量
        derived_classes = {}
        for rel in inheritance_relations:
            derived = rel.get('derived_class_name', 'Unknown')
            base = rel.get('base_class_name', 'Unknown')
            if derived not in derived_classes:
                derived_classes[derived] = []
            derived_classes[derived].append(base)
        
        single_inheritance = [derived for derived, bases in derived_classes.items() if len(bases) == 1]
        multiple_inheritance = [derived for derived, bases in derived_classes.items() if len(bases) > 1]
        
        logger.info(f"  总继承关系: {inheritance_count}")
        logger.info(f"  单继承类: {len(single_inheritance)}")
        logger.info(f"  多重继承类: {len(multiple_inheritance)}")
        
        # 显示前5个继承关系
        for i, (derived, bases) in enumerate(list(derived_classes.items())[:5]):
            logger.info(f"    {derived} <- {', '.join(bases)}")
        
        success = inheritance_count >= min_expected
        logger.info(f"继承关系验证: {'通过' if success else '失败'} ({inheritance_count}>={min_expected})")
        return success
    
    def verify_call_relationships(self, results: dict, test_name: str, min_expected: int = 5) -> bool:
        """验证调用关系"""
        logger.info(f"验证调用关系 - {test_name}")
        
        main_data = results.get('main', {})
        entities = main_data.get('entities', {})
        call_relations = entities.get('call_relations', [])
        call_count = len(call_relations)
        
        # 统计不同类型的调用
        recursive_calls = [rel for rel in call_relations if rel.get('caller_usr') == rel.get('callee_usr')]
        
        # 修复跨类调用统计
        cross_class_calls = 0
        for rel in call_relations:
            caller_class = rel.get('caller_class_name')
            callee_class = rel.get('callee_class_name')
            # 确保二者都存在，且不相等
            if caller_class and callee_class and caller_class != callee_class:
                cross_class_calls += 1

        unknown_calls = [
            rel for rel in call_relations 
            if rel.get('caller_function_name', 'Unknown') == 'Unknown' or 
               rel.get('callee_function_name', 'Unknown') == 'Unknown'
        ]
        
        logger.info(f"  总调用关系: {call_count}")
        logger.info(f"  递归调用: {len(recursive_calls)}")
        logger.info(f"  跨类调用: {cross_class_calls}")
        if unknown_calls:
            logger.warning(f"  发现 {len(unknown_calls)} 个未知的调用关系！")
        
        # 显示前5个调用关系
        for rel in call_relations[:5]:
            caller = rel.get('caller_function_name', rel.get('caller_name', 'Unknown'))
            callee = rel.get('callee_function_name', rel.get('callee_name', 'Unknown'))
            logger.info(f"    {caller} -> {callee}")
        
        success = call_count >= min_expected and len(unknown_calls) == 0
        logger.info(f"调用关系验证: {'通过' if success else '失败'} ({call_count}>={min_expected}, {len(unknown_calls)} 个未知)")
        if not success and len(unknown_calls) > 0:
            logger.error("  失败原因：存在未解析的调用关系 (Unknown -> Unknown)")
        return success
    
    def verify_template_features(self, results: dict, test_name: str) -> bool:
        """验证模板功能"""
        logger.info(f"验证模板功能 - {test_name}")
        
        # 在主结果中查找模板相关内容
        main_result = results.get('main', {})
        
        # 检查类中是否有模板类
        entities = main_result.get('entities', {})
        classes = entities.get('classes', {})
        template_classes = []
        for class_id, class_info in classes.items():
            class_name = class_info.get('name', '')
            if '<' in class_name and '>' in class_name:
                template_classes.append(class_name)
        
        # 检查函数中是否有模板函数
        functions = entities.get('functions', {})
        template_functions = []
        for func_id, func_info in functions.items():
            func_name = func_info.get('name', '')
            func_signature = func_info.get('signature', '')
            # 检查函数是否使用了模板类型（如参数类型包含T, K, V等）
            parameters = func_info.get('parameters', [])
            has_template_params = any(
                param.get('type', '') in ['T', 'K', 'V', 'Predicate'] or 
                '<' in param.get('type', '') for param in parameters
            )
            return_type = func_info.get('return_type', '')
            has_template_return = return_type in ['T', 'K', 'V'] or '<' in return_type
            
            if has_template_params or has_template_return or 'templa_' in func_id:
                template_functions.append(func_name)
        
        logger.info(f"  模板类: {len(template_classes)}")
        for cls in template_classes[:3]:
            logger.info(f"    {cls}")
        
        logger.info(f"  模板函数: {len(template_functions)}")
        for func in template_functions[:3]:
            logger.info(f"    {func}")
        
        success = len(template_classes) > 0 or len(template_functions) > 0
        logger.info(f"模板功能验证: {'通过' if success else '失败'}")
        return success
    
    def verify_template_specialization(self, results: dict, test_name: str) -> bool:
        """验证模板特化信息"""
        logger.info(f"验证模板特化 - {test_name}")
        main_data = results.get('main', {})
        entities = main_data.get('entities', {})
        
        specialized_classes = 0
        specialized_functions = 0

        # 检查类特化
        if 'classes' in entities:
            for class_info in entities['classes'].values():
                if class_info.get('is_template_specialization'):
                    specialized_classes += 1
                    logger.info(f"  ✓ 发现类特化: {class_info.get('qualified_name', 'Unknown')}")

        # 检查函数特化
        if 'functions' in entities:
            for func_info in entities['functions'].values():
                if func_info.get('is_template_specialization'):
                    specialized_functions += 1
                    logger.info(f"  ✓ 发现函数特化: {func_info.get('signature', 'Unknown')}")

        logger.info(f"  统计: {specialized_classes} 个类特化, {specialized_functions} 个函数特化")
        
        # 在我们的 rich_demo 中，我们期望至少有2个类特化(包括偏特化)和1个函数特化
        expected_class_specializations = 2
        expected_function_specializations = 1

        success = (specialized_classes >= expected_class_specializations and 
                   specialized_functions >= expected_function_specializations)
        
        logger.info(f"模板特化验证: {'通过' if success else '失败'}")
        if not success:
            logger.error(f"  失败原因：预期的类特化数量不足: 发现 {specialized_classes}, 期望 {expected_class_specializations}")
            logger.error(f"  失败原因：预期的函数特化数量不足: 发现 {specialized_functions}, 期望 {expected_function_specializations}")
        return success

    def verify_multiple_inheritance(self, results: dict, test_name: str) -> bool:
        """验证多重继承"""
        logger.info(f"验证多重继承 - {test_name}")
        
        main_data = results.get('main', {})
        entities = main_data.get('entities', {})
        inheritance_relations = entities.get('inheritance_relations', [])
        # 统计继承关系：按派生类分组计算基类数量
        derived_classes = {}
        for rel in inheritance_relations:
            derived = rel.get('derived_class_name', 'Unknown')
            base = rel.get('base_class_name', 'Unknown')
            if derived not in derived_classes:
                derived_classes[derived] = []
            derived_classes[derived].append(base)
        
        multiple_inheritance_classes = [derived for derived, bases in derived_classes.items() if len(bases) > 1]
        
        logger.info(f"  多重继承类: {len(multiple_inheritance_classes)}")
        
        for derived in multiple_inheritance_classes:
            bases = derived_classes[derived]
            logger.info(f"    {derived} <- {', '.join(bases)}")
        
        success = len(multiple_inheritance_classes) > 0
        logger.info(f"多重继承验证: {'通过' if success else '失败'}")
        return success
    
    def run_test_case(self, test_case: dict) -> bool:
        """运行单个测试用例"""
        logger.info(f"\n{'='*60}")
        logger.info(f"运行测试用例: {test_case['name']}")
        logger.info(f"{'='*60}")
        
        # 运行分析器
        if not self.run_analyzer(test_case['project_path'], test_case['output_path']):
            return False
        
        # 加载结果
        results = self.load_analysis_results(test_case['output_path'])
        if not results:
            logger.error("无法加载分析结果")
            return False
        
        # 运行验证
        verifications = []
        
        # 基础验证
        verifications.append(self.verify_relative_paths(results, test_case['name']))
        verifications.append(self.verify_function_exports(results, test_case['name']))
        
        expected = test_case['expected_features']
        verifications.append(self.verify_inheritance_relationships(
            results, test_case['name'], expected.get('min_inheritance', 3)))
        verifications.append(self.verify_call_relationships(
            results, test_case['name'], expected.get('min_calls', 5)))
        
        # 高级功能验证（仅对丰富示例）
        if expected.get('has_templates', False):
            verifications.append(self.verify_template_features(results, test_case['name']))
        
        if expected.get('has_multiple_inheritance', False):
            verifications.append(self.verify_multiple_inheritance(results, test_case['name']))
        
        # 统计验证结果
        passed = sum(verifications)
        total = len(verifications)
        success = passed == total
        
        logger.info(f"\n测试用例 '{test_case['name']}' 结果: {'通过' if success else '失败'} ({passed}/{total})")
        return success
    
    def run_all_tests(self):
        """运行所有测试用例"""
        logger.info("开始运行所有C++分析测试用例")
        
        total_tests = len(self.test_cases)
        passed_tests = 0
        
        for test_case in self.test_cases:
            if self.run_test_case(test_case):
                passed_tests += 1
        
        # 最终汇总
        logger.info(f"\n{'='*60}")
        logger.info(f"测试汇总")
        logger.info(f"{'='*60}")
        logger.info(f"总测试用例: {total_tests}")
        logger.info(f"通过: {passed_tests}")
        logger.info(f"失败: {total_tests - passed_tests}")
        logger.info(f"成功率: {passed_tests/total_tests*100:.1f}%")
        
        if passed_tests == total_tests:
            logger.info("🎉 所有测试用例都通过了！")
            return True
        else:
            logger.error("❌ 部分测试用例失败")
            return False

def main():
    """主函数"""
    runner = TestRunner()
    success = runner.run_all_tests()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main() 