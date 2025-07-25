"""
JSON Exporter Module

Exports extracted C++ entities to JSON format following the specification
defined in json_format.md. Handles file mappings, status bitmasks, 
function signatures, and all other format requirements.
"""

import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from .logger import get_logger

class JsonExporter:
    """JSON导出器 - 支持格式版本2.3和完整诊断信息"""
    
    FORMAT_VERSION = "2.3"
    
    def __init__(self):
        self.project_info = {}
        self.diagnostics_summary = {
            "total_diagnostics": 0,
            "errors": 0,
            "warnings": 0,
            "notes": 0,
            "ignored": 0,
            "by_category": {},
            "by_file": {},
            "most_common_issues": []
        }
    
    def export_to_json(self, 
                      extracted_entities: Dict[str, Any], 
                      config, 
                      file_mappings: Dict[str, str],
                      complexity_metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """导出实体数据为JSON格式"""
        
        # 构建项目信息
        project_info = self._build_project_info(config)
        
        # 构建主要JSON结构 - 严格按照json_format.md规范
        json_data = {
            "version": self.FORMAT_VERSION,  # 规范要求version，不是format_version
            "language": "cpp",
            "timestamp": datetime.now().isoformat(),
            "analyzer_version": "1.0.0",
            
            # 文件映射（格式版本2.3新增）
            "file_mappings": file_mappings,
            
            # 项目调用图 - 包含project_info和所有调用图信息
            "project_call_graph": {
                "project_info": project_info,  # project_info应该在project_call_graph内部
                "modules": {
                    "main": {
                        "functions": extracted_entities.get('functions', {}),
                        "global_variables": self._extract_global_variables(extracted_entities),
                        "call_relationships": self._analyze_call_relationships(extracted_entities)
                    }
                },
                # 全局调用图和反向调用图应该在project_call_graph内部
                "global_call_graph": self._build_global_call_graph(extracted_entities),
                "reverse_call_graph": self._build_reverse_call_graph(extracted_entities)
            },
            
            # OOP分析
            "oop_analysis": {
                "classes": extracted_entities.get('classes', {}),
                "inheritance_graph": self._build_inheritance_graph(extracted_entities),
                "inheritance_relationships": self._analyze_inheritance_relationships(extracted_entities),
                "composition_relationships": self._analyze_composition_relationships(extracted_entities),
                "aggregation_relationships": self._analyze_aggregation_relationships(extracted_entities),
                "method_resolution_orders": self._build_method_resolution_orders(extracted_entities)
            },
            
            # C++特有分析
            "cpp_analysis": {
                "namespaces": extracted_entities.get('namespaces', {}),
                "templates": self._analyze_templates(extracted_entities),
                # 按照规范将宏/包含/条件编译收进preprocessor
                "preprocessor": {
                    "macros": self._extract_macro_definitions(config, extracted_entities),
                    "includes": extracted_entities.get('includes', {}),
                    "conditional_compilation": self._analyze_conditional_compilation(extracted_entities),
                    "include_graph": self._build_include_graph(extracted_entities)
                },
                "forward_declarations": extracted_entities.get('forward_declarations', {}),
                "using_declarations": self._extract_using_declarations(extracted_entities),
                "typedefs": extracted_entities.get('typedefs', {}),
                "enums": self._extract_enums(extracted_entities),
                "unions": self._extract_unions(extracted_entities),
                "friend_relationships": self._analyze_friend_relationships(extracted_entities),
                "operator_overloads": self._analyze_operator_overloads(extracted_entities),
                "lambda_expressions": self._analyze_lambda_expressions(extracted_entities),
                "concepts": self._analyze_concepts(extracted_entities),
                "modules": self._analyze_modules(extracted_entities),
                "coroutines": self._analyze_coroutines(extracted_entities)
            },
            
            # 摘要信息
            "summary": self._build_summary(extracted_entities, file_mappings, complexity_metrics),
            
            # 诊断摘要
            "diagnostics_summary": self.diagnostics_summary
        }
        
        return json_data
    
    def export_to_string(self, 
                        extracted_entities: Dict[str, Any], 
                        config, 
                        file_mappings: Dict[str, str]) -> str:
        """导出为JSON字符串"""
        json_data = self.export_to_json(extracted_entities, config, file_mappings)
        return json.dumps(json_data, indent=2, ensure_ascii=False)
    
    def _build_project_info(self, config) -> Dict[str, Any]:
        """构建项目信息"""
        project_root = getattr(config, 'project_root', '')
        scan_directory = getattr(config, 'scan_directory', '')
        
        return {
            "name": Path(project_root).name if project_root else "Unknown",
            "root_path": project_root,
            "scan_path": scan_directory,
            "version": "1.0.0",
            "description": f"C++ Analysis of {scan_directory}",
            "language": "C++",
            "analysis_timestamp": datetime.now().isoformat(),
            "analyzer_config": {
                "use_compile_commands": getattr(config, 'use_compile_commands', True),
                "generate_compile_commands": getattr(config, 'generate_compile_commands', True),
                "include_extensions": list(getattr(config, 'include_extensions', [])),
                "exclude_patterns": list(getattr(config, 'exclude_patterns', [])),
                "max_files": getattr(config, 'max_files', None),
                "verbose": getattr(config, 'verbose', False)
            }
        }
    
    def _build_summary(self, extracted_entities: Dict[str, Any], file_mappings: Dict[str, str], complexity_metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """构建摘要信息"""
        functions = extracted_entities.get('functions', {})
        classes = extracted_entities.get('classes', {})
        namespaces = extracted_entities.get('namespaces', {})
        
        # 基本统计
        basic_statistics = {
            "total_files": len(file_mappings),
            "total_functions": len(functions),
            "total_classes": len(classes),
            "total_namespaces": len(namespaces)
        }
        
        # C++特有统计
        cpp_statistics = self._calculate_cpp_statistics(functions, classes, namespaces)
                
        # 诊断信息摘要
        diagnostics_summary = self._build_diagnostics_summary()
        
        return {
            "timestamp": datetime.now().isoformat(),
            "basic_statistics": basic_statistics,
            "cpp_statistics": cpp_statistics,
            "complexity_metrics": complexity_metrics,
            "diagnostics_summary": diagnostics_summary,
            "analysis_metadata": {
                "total_analysis_time": "N/A",  # TODO: 从config获取
                "memory_usage": self._get_memory_usage(),
                "libclang_version": self._get_libclang_version(),
                "analyzer_version": "1.0.0"
            }
        }
    
    def _calculate_cpp_statistics(self, functions: Dict, classes: Dict, namespaces: Dict) -> Dict[str, Any]:
        """计算C++特有统计信息"""
        stats = {
            # 函数统计
            "total_functions": len(functions),
            "template_functions": 0,
            "virtual_functions": 0,
            "pure_virtual_functions": 0,
            "static_functions": 0,
            "const_functions": 0,
            "inline_functions": 0,
            "constexpr_functions": 0,
            "operator_overloads": 0,
            "constructors": 0,
            "destructors": 0,
            "copy_constructors": 0,
            "move_constructors": 0,
            
            # 类统计
            "total_classes": len(classes),
            "template_classes": 0,
            "abstract_classes": 0,
            "final_classes": 0,
            "polymorphic_classes": 0,
            "trivial_classes": 0,
            "pod_classes": 0,
            "unions": 0,
            
            # 命名空间统计
            "total_namespaces": len(namespaces),
            "anonymous_namespaces": 0,
            "inline_namespaces": 0,
            
            # 模板统计
            "total_templates": 0,
            "template_specializations": 0,
            "template_instantiations": 0,
            
            # 继承统计
            "inheritance_relationships": 0,
            "multiple_inheritance": 0,
            "virtual_inheritance": 0,
            "diamond_inheritance": 0
        }
        
        # 分析函数状态位掩码
        for func_data in functions.values():
            flags = func_data.get('function_status_flags', 0)
            
            if flags & (1 << 0):  # FUNC_IS_TEMPLATE
                stats["template_functions"] += 1
            if flags & (1 << 2):  # FUNC_IS_VIRTUAL
                stats["virtual_functions"] += 1
            if flags & (1 << 3):  # FUNC_IS_PURE_VIRTUAL
                stats["pure_virtual_functions"] += 1
            if flags & (1 << 6):  # FUNC_IS_STATIC
                stats["static_functions"] += 1
            if flags & (1 << 7):  # FUNC_IS_CONST
                stats["const_functions"] += 1
            if flags & (1 << 9):  # FUNC_IS_INLINE
                stats["inline_functions"] += 1
            if flags & (1 << 10): # FUNC_IS_CONSTEXPR
                stats["constexpr_functions"] += 1
            if flags & (1 << 11): # FUNC_IS_OPERATOR_OVERLOAD
                stats["operator_overloads"] += 1
            if flags & (1 << 12): # FUNC_IS_CONSTRUCTOR
                stats["constructors"] += 1
            if flags & (1 << 13): # FUNC_IS_DESTRUCTOR
                stats["destructors"] += 1
            if flags & (1 << 14): # FUNC_IS_COPY_CONSTRUCTOR
                stats["copy_constructors"] += 1
            if flags & (1 << 15): # FUNC_IS_MOVE_CONSTRUCTOR
                stats["move_constructors"] += 1
        
        # 分析类状态位掩码
        for class_data in classes.values():
            flags = class_data.get('class_status_flags', 0)
            
            if flags & (1 << 0):  # CLASS_IS_TEMPLATE
                stats["template_classes"] += 1
            if flags & (1 << 2):  # CLASS_IS_ABSTRACT
                stats["abstract_classes"] += 1
            if flags & (1 << 3):  # CLASS_IS_FINAL
                stats["final_classes"] += 1
            if flags & (1 << 4):  # CLASS_IS_POLYMORPHIC
                stats["polymorphic_classes"] += 1
            if flags & (1 << 7):  # CLASS_IS_TRIVIAL
                stats["trivial_classes"] += 1
            if flags & (1 << 6):  # CLASS_IS_POD
                stats["pod_classes"] += 1
            if flags & (1 << 15): # CLASS_IS_UNION
                stats["unions"] += 1
        
        return stats
    
    def _build_diagnostics_summary(self) -> Dict[str, Any]:
        """构建诊断信息摘要"""
        # TODO: 从解析过程中收集诊断信息
        return {
            "total_diagnostics": 0,
            "errors": 0,
            "warnings": 0,
            "notes": 0,
            "ignored": 0,
            "by_category": {},
            "by_file": {},
            "most_common_issues": []
        }
    
    def add_diagnostics(self, diagnostics: List[Any]):
        """添加诊断信息用于统计"""
        logger = get_logger()
        try:
            for diag in diagnostics:
                severity = getattr(diag, 'severity', 'unknown')
                category = getattr(diag, 'category_name', 'general')
                location = getattr(diag, 'location', None)
                
                # 更新计数
                if severity not in self.diagnostics_summary:
                    self.diagnostics_summary[severity] = 0
                self.diagnostics_summary[severity] += 1
                
                # 按类别统计
                if 'by_category' not in self.diagnostics_summary:
                    self.diagnostics_summary['by_category'] = {}
                if category not in self.diagnostics_summary['by_category']:
                    self.diagnostics_summary['by_category'][category] = 0
                self.diagnostics_summary['by_category'][category] += 1
                
                # 按文件统计
                if location and hasattr(location, 'file'):
                    filename = location.file.name if location.file else 'unknown'
                    if 'by_file' not in self.diagnostics_summary:
                        self.diagnostics_summary['by_file'] = {}
                    if filename not in self.diagnostics_summary['by_file']:
                        self.diagnostics_summary['by_file'][filename] = 0
                    self.diagnostics_summary['by_file'][filename] += 1
        
        except Exception as e:
            logger.error(f"添加诊断信息失败: {e}")
    
    def _extract_global_variables(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """提取全局变量信息"""
        global_variables = {}
        
        # 从函数中提取全局变量引用
        functions = extracted_entities.get('functions', {})
        for func_key, func_data in functions.items():
            # 这里可以分析函数中使用的全局变量
            # 目前先返回空字典，后续可以通过AST分析实现
            pass
        
        # 从类中提取静态成员变量
        classes = extracted_entities.get('classes', {})
        for class_key, class_data in classes.items():
            # 这里可以分析类的静态成员变量
            # 目前先返回空字典，后续可以通过AST分析实现
            pass
            
        return global_variables
    
    def _analyze_call_relationships(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """分析调用关系"""
        call_relationships = {}
        
        functions = extracted_entities.get('functions', {})
        
        # 基于函数签名分析可能的调用关系
        for caller_key, caller_data in functions.items():
            caller_name = caller_data.get('name', '')
            calls = []
            
            # 通过函数名模式匹配可能的调用关系
            for callee_key, callee_data in functions.items():
                if caller_key != callee_key:
                    callee_name = callee_data.get('name', '')
                    
                    # 简单的调用关系推断（基于命名模式）
                    if (callee_name and caller_name and 
                        (callee_name.lower() in caller_name.lower() or
                         any(keyword in caller_name.lower() for keyword in ['call', 'invoke', 'execute']))):
                        calls.append({
                            'target_function': callee_key,
                            'call_type': 'direct',  # 假设为直接调用
                            'location': caller_data.get('definition_line', 0)
                        })
            
            if calls:
                call_relationships[caller_key] = calls
        
        return call_relationships
    
    def _build_global_call_graph(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """构建全局调用图"""
        global_call_graph = {
            'nodes': {},
            'edges': [],
            'statistics': {
                'total_functions': 0,
                'total_calls': 0,
                'max_depth': 0,
                'cyclic_calls': 0
            }
        }
        
        functions = extracted_entities.get('functions', {})
        
        # 构建节点
        for func_key, func_data in functions.items():
            global_call_graph['nodes'][func_key] = {
                'name': func_data.get('name', ''),
                'qualified_name': func_data.get('qualified_name', ''),
                'file_id': func_data.get('definition_file_id', ''),
                'line': func_data.get('definition_line', 0),
                'in_degree': 0,  # 被调用次数
                'out_degree': 0  # 调用其他函数次数
            }
        
        # 构建边（基于调用关系）
        call_relationships = self._analyze_call_relationships(extracted_entities)
        edge_id = 0
        
        for caller_key, calls in call_relationships.items():
            for call in calls:
                target_key = call['target_function']
                
                # 添加边
                global_call_graph['edges'].append({
                    'id': edge_id,
                    'source': caller_key,
                    'target': target_key,
                    'call_type': call.get('call_type', 'direct'),
                    'weight': 1  # 可以根据调用频率调整
                })
                
                # 更新度数
                if caller_key in global_call_graph['nodes']:
                    global_call_graph['nodes'][caller_key]['out_degree'] += 1
                if target_key in global_call_graph['nodes']:
                    global_call_graph['nodes'][target_key]['in_degree'] += 1
                
                edge_id += 1
        
        # 更新统计信息
        global_call_graph['statistics']['total_functions'] = len(functions)
        global_call_graph['statistics']['total_calls'] = len(global_call_graph['edges'])
        
        return global_call_graph
    
    def _build_reverse_call_graph(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """构建反向调用图"""
        reverse_call_graph = {}
        
        # 基于调用关系构建反向索引
        call_relationships = self._analyze_call_relationships(extracted_entities)
        
        # 初始化所有函数
        functions = extracted_entities.get('functions', {})
        for func_key in functions.keys():
            reverse_call_graph[func_key] = []
        
        # 构建反向调用关系
        for caller_key, calls in call_relationships.items():
            for call in calls:
                target_key = call['target_function']
                if target_key in reverse_call_graph:
                    reverse_call_graph[target_key].append({
                        'caller_function': caller_key,
                        'call_type': call.get('call_type', 'direct'),
                        'location': call.get('location', 0)
                    })
        
        return reverse_call_graph

    def _build_inheritance_graph(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """构建继承图"""
        inheritance_graph = {
            'nodes': {},
            'edges': [],
            'root_classes': [],
            'leaf_classes': []
        }
        
        classes = extracted_entities.get('classes', {})
        
        # 构建节点
        for class_key, class_data in classes.items():
            inheritance_graph['nodes'][class_key] = {
                'name': class_data.get('name', ''),
                'qualified_name': class_data.get('qualified_name', ''),
                'kind': class_data.get('kind', 'class'),
                'parent_count': 0,
                'child_count': 0
            }
        
        # 构建边（基于继承关系）
        edge_id = 0
        for class_key, class_data in classes.items():
            inheritance_list = class_data.get('inheritance_list', [])
            for inheritance in inheritance_list:
                base_class = inheritance.get('base_class', '')
                if base_class:
                    # 查找基类对应的key
                    base_key = None
                    for potential_base_key, potential_base_data in classes.items():
                        if (potential_base_data.get('name', '') == base_class or
                            potential_base_data.get('qualified_name', '') == base_class):
                            base_key = potential_base_key
                            break
                    
                    if base_key:
                        inheritance_graph['edges'].append({
                            'id': edge_id,
                            'base_class': base_key,
                            'derived_class': class_key,
                            'access_specifier': inheritance.get('access_specifier', 'private'),
                            'is_virtual': inheritance.get('is_virtual', False)
                        })
                        
                        # 更新计数
                        inheritance_graph['nodes'][class_key]['parent_count'] += 1
                        inheritance_graph['nodes'][base_key]['child_count'] += 1
                        
                        edge_id += 1
        
        # 确定根类和叶类
        for class_key, node_data in inheritance_graph['nodes'].items():
            if node_data['parent_count'] == 0:
                inheritance_graph['root_classes'].append(class_key)
            if node_data['child_count'] == 0:
                inheritance_graph['leaf_classes'].append(class_key)
        
        # 添加diamond检测
        inheritance_graph['diamond_inheritance'] = self._detect_diamond_inheritance(inheritance_graph)
        
        return inheritance_graph

    def _analyze_inheritance_relationships(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """分析继承关系"""
        relationships = {}
        classes = extracted_entities.get('classes', {})
        
        for class_key, class_data in classes.items():
            inheritance_list = class_data.get('inheritance_list', [])
            if inheritance_list:
                relationships[class_key] = {
                    'base_classes': inheritance_list,
                    'inheritance_depth': len(inheritance_list),  # 简化计算
                    'multiple_inheritance': len(inheritance_list) > 1,
                    'virtual_inheritance': any(i.get('is_virtual', False) for i in inheritance_list)
                }
        
        return relationships

    def _analyze_composition_relationships(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """分析组合关系"""
        # 组合关系需要更复杂的AST分析，这里先返回简化版本
        composition_relationships = {}
        classes = extracted_entities.get('classes', {})
        
        for class_key, class_data in classes.items():
            # 基于类名推断可能的组合关系
            class_name = class_data.get('name', '')
            compositions = []
            
            for other_key, other_data in classes.items():
                if class_key != other_key:
                    other_name = other_data.get('name', '')
                    # 简单的组合关系推断（基于命名模式）
                    if (other_name and class_name and 
                        (other_name in class_name or class_name.endswith(other_name))):
                        compositions.append({
                            'component_class': other_key,
                            'relationship_type': 'composition',
                            'multiplicity': '1'
                        })
            
            if compositions:
                composition_relationships[class_key] = compositions
        
        return composition_relationships

    def _analyze_aggregation_relationships(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """分析聚合关系"""
        # 聚合关系需要更复杂的AST分析，这里先返回简化版本
        aggregation_relationships = {}
        classes = extracted_entities.get('classes', {})
        
        for class_key, class_data in classes.items():
            # 基于类名推断可能的聚合关系
            class_name = class_data.get('name', '').lower()
            aggregations = []
            
            # 查找可能包含集合关系的类
            for other_key, other_data in classes.items():
                if class_key != other_key:
                    other_name = other_data.get('name', '').lower()
                    
                    # 简单的聚合关系推断
                    if (any(keyword in class_name for keyword in ['manager', 'container', 'collection', 'group']) and
                        other_name and 'item' in other_name):
                        aggregations.append({
                            'aggregate_class': other_key,
                            'relationship_type': 'aggregation',
                            'multiplicity': '0..*'
                        })
            
            if aggregations:
                aggregation_relationships[class_key] = aggregations
        
        return aggregation_relationships

    def _build_method_resolution_orders(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """构建方法解析顺序"""
        method_resolution_orders = {}
        classes = extracted_entities.get('classes', {})
        
        for class_key, class_data in classes.items():
            # 简化的方法解析顺序计算
            mro = [class_key]  # 从自身开始
            
            # 添加直接基类
            inheritance_list = class_data.get('inheritance_list', [])
            for inheritance in inheritance_list:
                base_class = inheritance.get('base_class', '')
                if base_class:
                    # 查找基类对应的key
                    for potential_base_key, potential_base_data in classes.items():
                        if (potential_base_data.get('name', '') == base_class or
                            potential_base_data.get('qualified_name', '') == base_class):
                            if potential_base_key not in mro:
                                mro.append(potential_base_key)
                            break
            
            if len(mro) > 1:  # 只有继承类才需要MRO
                method_resolution_orders[class_key] = {
                    'order': mro,
                    'is_diamond_inheritance': False,  # 简化检测
                    'conflicts': []  # 方法冲突检测
                }
        
        return method_resolution_orders

    def _analyze_templates(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """分析模板信息"""
        templates = {
            'class_templates': {},
            'function_templates': {},
            'variable_templates': {},
            'alias_templates': {},
            'specializations': {},
            'instantiations': {},
            'statistics': {
                'total_templates': 0,
                'total_specializations': 0,
                'total_instantiations': 0,
                'template_depth_distribution': {}
            }
        }
        
        functions = extracted_entities.get('functions', {})
        classes = extracted_entities.get('classes', {})
        
        # 分析函数模板
        for func_key, func_data in functions.items():
            template_params = func_data.get('template_parameters', [])
            if template_params:
                templates['function_templates'][func_key] = {
                    'name': func_data.get('name', ''),
                    'qualified_name': func_data.get('qualified_name', ''),
                    'template_parameters': template_params,
                    'parameter_count': len(template_params),
                    'is_specialization': bool(func_data.get('function_status_flags', 0) & (1 << 1)),
                    'definition_file_id': func_data.get('definition_file_id', ''),
                    'definition_line': func_data.get('definition_line', 0)
                }
        
        # 分析类模板
        for class_key, class_data in classes.items():
            template_params = class_data.get('template_parameters', [])
            if template_params:
                templates['class_templates'][class_key] = {
                    'name': class_data.get('name', ''),
                    'qualified_name': class_data.get('qualified_name', ''),
                    'kind': class_data.get('kind', 'class'),
                    'template_parameters': template_params,
                    'parameter_count': len(template_params),
                    'is_specialization': bool(class_data.get('class_status_flags', 0) & (1 << 1)),
                    'definition_file_id': class_data.get('definition_file_id', ''),
                    'definition_line': class_data.get('definition_line', 0)
                }
        
        # 统计信息
        templates['statistics']['total_templates'] = (
            len(templates['class_templates']) + 
            len(templates['function_templates'])
        )
        
        return templates

    def _extract_using_declarations(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """提取using声明信息"""
        using_declarations = {
            'using_directives': {},     # using namespace
            'using_declarations': {},   # using declaration
            'type_aliases': {},         # using alias = type
            'statistics': {
                'total_using_directives': 0,
                'total_using_declarations': 0,
                'total_type_aliases': 0
            }
        }
        
        namespaces = extracted_entities.get('namespaces', {})
        
        # 从命名空间中提取using声明
        for ns_key, ns_data in namespaces.items():
            using_decls = ns_data.get('using_declarations', [])
            
            for using_decl in using_decls:
                if using_decl.startswith('namespace '):
                    # using namespace directive
                    namespace_name = using_decl[10:].strip()
                    directive_key = f"{ns_key}_{namespace_name}"
                    using_declarations['using_directives'][directive_key] = {
                        'source_namespace': ns_data.get('qualified_name', ''),
                        'target_namespace': namespace_name,
                        'file_id': ns_data.get('definition_file_id', ''),
                        'line': ns_data.get('definition_line', 0)
                    }
                elif '=' in using_decl:
                    # type alias
                    alias_parts = using_decl.split('=', 1)
                    alias_name = alias_parts[0].strip()
                    target_type = alias_parts[1].strip()
                    alias_key = f"{ns_key}_{alias_name}"
                    using_declarations['type_aliases'][alias_key] = {
                        'alias_name': alias_name,
                        'target_type': target_type,
                        'source_namespace': ns_data.get('qualified_name', ''),
                        'file_id': ns_data.get('definition_file_id', ''),
                        'line': ns_data.get('definition_line', 0)
                    }
                else:
                    # using declaration
                    decl_key = f"{ns_key}_{using_decl}"
                    using_declarations['using_declarations'][decl_key] = {
                        'declaration': using_decl,
                        'source_namespace': ns_data.get('qualified_name', ''),
                        'file_id': ns_data.get('definition_file_id', ''),
                        'line': ns_data.get('definition_line', 0)
                    }
        
        # 更新统计信息
        using_declarations['statistics']['total_using_directives'] = len(using_declarations['using_directives'])
        using_declarations['statistics']['total_using_declarations'] = len(using_declarations['using_declarations'])
        using_declarations['statistics']['total_type_aliases'] = len(using_declarations['type_aliases'])
        
        return using_declarations

    def _extract_enums(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """提取枚举信息"""
        enums = {
            'scoped_enums': {},      # enum class
            'unscoped_enums': {},    # enum
            'enum_values': {},       # 枚举值
            'statistics': {
                'total_enums': 0,
                'total_scoped_enums': 0,
                'total_unscoped_enums': 0,
                'total_enum_values': 0,
                'average_values_per_enum': 0.0
            }
        }
        
        # 从类中查找嵌套枚举
        classes = extracted_entities.get('classes', {})
        for class_key, class_data in classes.items():
            nested_types = class_data.get('nested_types', [])
            for nested_type in nested_types:
                if nested_type.startswith('enum '):
                    enum_name = nested_type[5:].strip()
                    enum_key = f"{class_key}_{enum_name}"
                    
                    # 确定枚举类型（简化判断）
                    if 'class' in nested_type or 'struct' in nested_type:
                        enums['scoped_enums'][enum_key] = {
                            'name': enum_name,
                            'qualified_name': f"{class_data.get('qualified_name', '')}::{enum_name}",
                            'underlying_type': 'int',  # 默认类型
                            'parent_class': class_key,
                            'file_id': class_data.get('definition_file_id', ''),
                            'line': class_data.get('definition_line', 0),
                            'values': []
                        }
                    else:
                        enums['unscoped_enums'][enum_key] = {
                            'name': enum_name,
                            'qualified_name': f"{class_data.get('qualified_name', '')}::{enum_name}",
                            'underlying_type': 'int',  # 默认类型
                            'parent_class': class_key,
                            'file_id': class_data.get('definition_file_id', ''),
                            'line': class_data.get('definition_line', 0),
                            'values': []
                        }
        
        # 更新统计信息
        total_enums = len(enums['scoped_enums']) + len(enums['unscoped_enums'])
        enums['statistics']['total_enums'] = total_enums
        enums['statistics']['total_scoped_enums'] = len(enums['scoped_enums'])
        enums['statistics']['total_unscoped_enums'] = len(enums['unscoped_enums'])
        
        return enums

    def _extract_unions(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """提取联合体信息"""
        unions = {
            'anonymous_unions': {},
            'named_unions': {},
            'statistics': {
                'total_unions': 0,
                'total_anonymous_unions': 0,
                'total_named_unions': 0,
                'average_members_per_union': 0.0
            }
        }
        
        classes = extracted_entities.get('classes', {})
        
        # 查找联合体类型的类
        for class_key, class_data in classes.items():
            if class_data.get('kind') == 'union':
                class_name = class_data.get('name', '')
                
                union_info = {
                    'name': class_name,
                    'qualified_name': class_data.get('qualified_name', ''),
                    'file_id': class_data.get('definition_file_id', ''),
                    'line': class_data.get('definition_line', 0),
                    'size_in_bytes': class_data.get('size_in_bytes', 0),
                    'alignment': class_data.get('alignment', 0),
                    'members': [],
                    'nested_types': class_data.get('nested_types', []),
                    'access_specifier': class_data.get('access_specifier', 'public')
                }
                
                if class_name:
                    unions['named_unions'][class_key] = union_info
                else:
                    unions['anonymous_unions'][class_key] = union_info
        
        # 从类中查找嵌套联合体
        for class_key, class_data in classes.items():
            if class_data.get('kind') != 'union':
                nested_types = class_data.get('nested_types', [])
                for nested_type in nested_types:
                    if nested_type.startswith('union '):
                        union_name = nested_type[6:].strip()
                        union_key = f"{class_key}_{union_name}"
                        
                        union_info = {
                            'name': union_name,
                            'qualified_name': f"{class_data.get('qualified_name', '')}::{union_name}",
                            'parent_class': class_key,
                            'file_id': class_data.get('definition_file_id', ''),
                            'line': class_data.get('definition_line', 0),
                            'size_in_bytes': 0,
                            'alignment': 0,
                            'members': [],
                            'nested_types': [],
                            'access_specifier': 'public'
                        }
                        
                        if union_name:
                            unions['named_unions'][union_key] = union_info
                        else:
                            unions['anonymous_unions'][union_key] = union_info
        
        # 更新统计信息
        total_unions = len(unions['named_unions']) + len(unions['anonymous_unions'])
        unions['statistics']['total_unions'] = total_unions
        unions['statistics']['total_named_unions'] = len(unions['named_unions'])
        unions['statistics']['total_anonymous_unions'] = len(unions['anonymous_unions'])
        
        return unions

    def validate_json_format(self, json_data: Dict[str, Any]) -> tuple[bool, List[str]]:
        """验证JSON格式是否符合规范"""
        errors = []
        
        # 检查必需字段
        required_fields = [
            "format_version", "language", "timestamp", "file_mappings",
            "project_info", "project_call_graph", "oop_analysis", 
            "cpp_analysis", "summary"
        ]
        
        for field in required_fields:
            if field not in json_data:
                errors.append(f"Missing required field: {field}")
        
        # 检查格式版本
        if json_data.get("format_version") != self.FORMAT_VERSION:
            errors.append(f"Invalid format version. Expected {self.FORMAT_VERSION}, got {json_data.get('format_version')}")
        
        # 检查语言
        if json_data.get("language") != "C++":
            errors.append(f"Invalid language. Expected C++, got {json_data.get('language')}")
        
        # 检查文件映射格式
        file_mappings = json_data.get("file_mappings", {})
        for file_id, file_path in file_mappings.items():
            if not file_id.startswith("f") or not file_id[1:].isdigit():
                errors.append(f"Invalid file ID format: {file_id}")
            if not isinstance(file_path, str):
                errors.append(f"Invalid file path type for {file_id}: {type(file_path)}")
        
        # 检查项目信息
        project_info = json_data.get("project_info", {})
        required_project_fields = ["name", "root_path", "version", "language"]
        for field in required_project_fields:
            if field not in project_info:
                errors.append(f"Missing required project_info field: {field}")
        
        # 检查函数格式
        functions = json_data.get("project_call_graph", {}).get("functions", {})
        for func_key, func_data in functions.items():
            if not isinstance(func_data, dict):
                errors.append(f"Invalid function data type for {func_key}: {type(func_data)}")
                continue
            
            required_func_fields = ["name", "qualified_name", "signature", "return_type"]
            for field in required_func_fields:
                if field not in func_data:
                    errors.append(f"Missing required function field {field} in {func_key}")
        
        # 检查类格式
        classes = json_data.get("oop_analysis", {}).get("classes", {})
        for class_key, class_data in classes.items():
            if not isinstance(class_data, dict):
                errors.append(f"Invalid class data type for {class_key}: {type(class_data)}")
                continue
            
            required_class_fields = ["name", "qualified_name", "kind"]
            for field in required_class_fields:
                if field not in class_data:
                    errors.append(f"Missing required class field {field} in {class_key}")
        
        return len(errors) == 0, errors
    
    def _extract_macro_definitions(self, config, extracted_entities: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """提取编译时宏定义"""
        macros = {}
        try:
            # 从config中获取编译参数
            if hasattr(config, 'compile_args') and config.compile_args:
                for arg in config.compile_args:
                    if arg.startswith('-D') or arg.startswith('/D'):
                        # 解析宏定义
                        macro_def = arg[2:]  # 去掉-D或/D前缀
                        if '=' in macro_def:
                            name, value = macro_def.split('=', 1)
                            macros[name] = {
                                "name": name,
                                "value": value,
                                "source": "command_line"
                            }
                        else:
                            macros[macro_def] = {
                                "name": macro_def,
                                "value": "1",  # 默认值
                                "source": "command_line"
                            }
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取宏定义失败: {e}")
        
        # 合并AST中的宏定义
        if extracted_entities:
            ast_macros = extracted_entities.get('ast_macros', {})
            for macro_name, macro_info in ast_macros.items():
                # 如果命令行宏和AST宏重复，优先使用命令行宏
                if macro_name not in macros:
                    macros[macro_name] = macro_info
                else:
                    # 如果存在重复，添加AST信息作为补充
                    existing_macro = macros[macro_name]
                    existing_macro['ast_definition'] = {
                        'line': macro_info.get('line', 0),
                        'file_id': macro_info.get('file_id', ''),
                        'value': macro_info.get('value', '')
                    }
        
        return macros
    
    def _analyze_friend_relationships(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """分析友元关系"""
        friend_relationships = {}
        
        try:
            classes = extracted_entities.get('classes', {})
            
            for class_key, class_data in classes.items():
                # 从类数据中提取友元声明
                friend_declarations = class_data.get('friend_declarations', [])
                
                if friend_declarations:
                    friend_relationships[class_key] = {
                        "class_name": class_data.get('name', ''),
                        "friends": []
                    }
                    
                    for friend_decl in friend_declarations:
                        friend_info = {
                            "name": friend_decl.get('name', ''),
                            "type": friend_decl.get('type', 'unknown'),  # class, function
                            "qualified_name": friend_decl.get('qualified_name', '')
                        }
                        friend_relationships[class_key]["friends"].append(friend_info)
        
        except Exception as e:
            logger = get_logger()
            logger.error(f"分析友元关系失败: {e}")
        
        return friend_relationships
    
    def _analyze_operator_overloads(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """分析操作符重载"""
        operator_overloads = {}
        
        try:
            functions = extracted_entities.get('functions', {})
            
            for func_key, func_data in functions.items():
                func_name = func_data.get('name', '')
                
                # 检查是否为操作符重载
                if func_name.startswith('operator'):
                    operator_name = func_name[8:].strip()  # 去掉'operator'前缀
                    
                    if operator_name not in operator_overloads:
                        operator_overloads[operator_name] = {
                            "operator": operator_name,
                            "overloads": []
                        }
                    
                    overload_info = {
                        "function_key": func_key,
                        "name": func_data.get('name', ''),
                        "qualified_name": func_data.get('qualified_name', ''),
                        "signature": func_data.get('signature', ''),
                        "return_type": func_data.get('return_type', ''),
                        "parameters": func_data.get('parameters', []),
                        "is_member": func_data.get('parent_class') is not None,
                        "is_friend": "friend" in func_data.get('modifiers', []),
                        "access_specifier": func_data.get('access_specifier', 'public')
                    }
                    
                    operator_overloads[operator_name]["overloads"].append(overload_info)
        
        except Exception as e:
            logger = get_logger()
            logger.error(f"分析操作符重载失败: {e}")
        
        return operator_overloads
    
    def _analyze_lambda_expressions(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """分析lambda表达式"""
        lambda_expressions = {}
        lambda_count = 0
        
        try:
            # Lambda表达式通常在函数体内，需要从AST中提取
            # 这里提供基础框架，实际需要在entity_extractor中收集lambda信息
            
            functions = extracted_entities.get('functions', {})
            
            for func_key, func_data in functions.items():
                # 检查函数是否包含lambda表达式信息
                lambdas = func_data.get('lambda_expressions', [])
                
                for lambda_info in lambdas:
                    lambda_count += 1
                    lambda_key = f"lambda_{lambda_count}"
                    
                    lambda_expressions[lambda_key] = {
                        "parent_function": func_key,
                        "capture_list": lambda_info.get('capture_list', []),
                        "parameters": lambda_info.get('parameters', []),
                        "return_type": lambda_info.get('return_type', 'auto'),
                        "is_mutable": lambda_info.get('is_mutable', False),
                        "location": lambda_info.get('location', {}),
                        "body_info": {
                            "has_return": lambda_info.get('has_return', False),
                            "complexity": lambda_info.get('complexity', 1)
                        }
                    }
        
        except Exception as e:
            logger = get_logger()
            logger.error(f"分析lambda表达式失败: {e}")
        
        return lambda_expressions
    
    def _analyze_concepts(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """分析概念(C++20)"""
        concepts = {}
        
        try:
            # Concepts在entity_extractor中需要专门收集
            concept_entities = extracted_entities.get('concepts', {})
            
            for concept_key, concept_data in concept_entities.items():
                concepts[concept_key] = {
                    "name": concept_data.get('name', ''),
                    "qualified_name": concept_data.get('qualified_name', ''),
                    "template_parameters": concept_data.get('template_parameters', []),
                    "requires_expression": concept_data.get('requires_expression', ''),
                    "constraints": concept_data.get('constraints', []),
                    "location": concept_data.get('location', {}),
                    "documentation": concept_data.get('documentation', '')
                }
        
        except Exception as e:
            logger = get_logger()
            logger.error(f"分析概念失败: {e}")
        
        return concepts
    
    def _analyze_modules(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """分析模块(C++20)"""
        modules = {}
        
        try:
            # 模块信息需要从编译单元中收集
            module_entities = extracted_entities.get('modules', {})
            
            for module_key, module_data in module_entities.items():
                modules[module_key] = {
                    "name": module_data.get('name', ''),
                    "is_interface": module_data.get('is_interface', False),
                    "is_implementation": module_data.get('is_implementation', False),
                    "exports": module_data.get('exports', []),
                    "imports": module_data.get('imports', []),
                    "partitions": module_data.get('partitions', []),
                    "location": module_data.get('location', {})
                }
        
        except Exception as e:
            logger = get_logger()
            logger.error(f"分析模块失败: {e}")
        
        return modules
    
    def _analyze_coroutines(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """分析协程(C++20)"""
        coroutines = {}
        
        try:
            functions = extracted_entities.get('functions', {})
            
            for func_key, func_data in functions.items():
                # 检查是否为协程函数
                if func_data.get('is_coroutine', False):
                    coroutines[func_key] = {
                        "function_name": func_data.get('name', ''),
                        "qualified_name": func_data.get('qualified_name', ''),
                        "coroutine_type": func_data.get('coroutine_type', 'unknown'),  # generator, task, etc.
                        "return_type": func_data.get('return_type', ''),
                        "promise_type": func_data.get('promise_type', ''),
                        "has_co_await": func_data.get('has_co_await', False),
                        "has_co_yield": func_data.get('has_co_yield', False),
                        "has_co_return": func_data.get('has_co_return', False),
                        "location": func_data.get('location', {})
                    }
        
        except Exception as e:
            logger = get_logger()
            logger.error(f"分析协程失败: {e}")
        
        return coroutines
    
    def _get_memory_usage(self) -> str:
        """获取内存使用统计"""
        try:
            import psutil
            import os
            
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            
            # 转换为MB
            rss_mb = memory_info.rss / (1024 * 1024)
            vms_mb = memory_info.vms / (1024 * 1024)
            
            return f"RSS: {rss_mb:.1f}MB, VMS: {vms_mb:.1f}MB"
        
        except ImportError:
            return "psutil not available"
        except Exception as e:
            return f"Error: {e}"
    
    def _get_libclang_version(self) -> str:
        """获取libclang版本信息"""
        try:
            import clang.cindex as clang
            # 尝试获取版本信息
            version_info = clang.conf.get_cindex_library()
            if version_info:
                return str(version_info)
            
            # 备用方法：尝试通过其他方式获取版本
            try:
                # 某些版本的libclang有version属性
                if hasattr(clang, 'version'):
                    return f"libclang {getattr(clang, 'version', 'unknown')}"
                elif hasattr(clang, '__version__'):
                    return f"libclang {getattr(clang, '__version__', 'unknown')}"
                else:
                    return "libclang (version unknown)"
            except:
                return "libclang (version detection failed)"
                
        except ImportError:
            return "N/A (libclang not available)"
        except Exception as e:
            return f"N/A (error: {str(e)})"
    
    def _analyze_conditional_compilation(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """分析条件编译指令"""
        # TODO: 实现条件编译分析（#ifdef, #ifndef, #if等）
        return {
            "ifdef_blocks": {},
            "ifndef_blocks": {},
            "if_blocks": {},
            "conditional_includes": {}
        }
    
    def _build_include_graph(self, extracted_entities: Dict[str, Any]) -> Dict[str, Any]:
        """构建include依赖图"""
        include_graph = {
            "nodes": [],
            "edges": [],
            "cycles": []
        }
        
        try:
            includes = extracted_entities.get('includes', {})
            
            # 构建节点和边
            for file_id, include_list in includes.items():
                include_graph["nodes"].append(file_id)
                
                for include_info in include_list:
                    # 简单的include关系边
                    edge = {
                        "from": file_id,
                        "to": include_info.get('file', ''),
                        "type": include_info.get('type', 'local'),
                        "line": include_info.get('line', 0)
                    }
                    include_graph["edges"].append(edge)
            
            # TODO: 检测循环include依赖
            
        except Exception as e:
            logger = get_logger()
            logger.error(f"构建include图失败: {e}")
        
        return include_graph
    
    def _detect_diamond_inheritance(self, inheritance_graph: Dict[str, Any]) -> List[Dict[str, Any]]:
        """检测diamond继承模式"""
        diamonds = []
        
        try:
            # 构建邻接表
            adj_list = {}
            for edge in inheritance_graph['edges']:
                base = edge['base_class']
                derived = edge['derived_class']
                
                if base not in adj_list:
                    adj_list[base] = []
                adj_list[base].append(derived)
            
            # 检测diamond模式：A -> B, A -> C, B -> D, C -> D
            nodes = inheritance_graph['nodes'].keys()
            
            for a in nodes:
                if a not in adj_list:
                    continue
                    
                direct_children = adj_list[a]
                if len(direct_children) < 2:
                    continue
                
                # 检查是否有共同的孙子类
                for i, b in enumerate(direct_children):
                    for c in direct_children[i+1:]:
                        if b in adj_list and c in adj_list:
                            b_children = set(adj_list[b])
                            c_children = set(adj_list[c])
                            common_children = b_children & c_children
                            
                            for d in common_children:
                                diamonds.append({
                                    'top_class': a,
                                    'middle_classes': [b, c],
                                    'bottom_class': d,
                                    'pattern': f"{a} -> {b}, {a} -> {c}, {b} -> {d}, {c} -> {d}"
                                })
        
        except Exception as e:
            logger = get_logger()
            logger.error(f"Diamond检测失败: {e}")
        
        return diamonds 