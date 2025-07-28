"""
类型推断引擎 - 增强版

该模块提供C++代码的高级类型推断功能，支持：
- STL容器和算法的类型推断
- 用户自定义类型的成员访问推断  
- 模板实例化和特化推断
- 重载决议和函数调用推断
- 上下文相关的类型推断
"""

from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass
from .data_structures import NodeRepository, Function, Class, Variable
from .logger import Logger
import re


@dataclass
class TypeInfo:
    """类型信息数据类，包含类型名称和置信度"""
    type_name: str
    confidence: float = 1.0
    
    def __post_init__(self):
        """确保置信度在有效范围内"""
        if self.confidence < 0.0:
            self.confidence = 0.0
        elif self.confidence > 1.0:
            self.confidence = 1.0


class STLTypeDatabase:
    """STL类型数据库"""
    
    def __init__(self):
        self.containers = {
            'std::vector': {
                'template_params': ['T', 'Allocator'],
                'methods': {
                    'size': {'return_type': 'size_t', 'params': []},
                    'empty': {'return_type': 'bool', 'params': []},
                    'at': {'return_type': 'T&', 'params': ['size_t']},
                    'operator[]': {'return_type': 'T&', 'params': ['size_t']},
                    'push_back': {'return_type': 'void', 'params': ['const T&']},
                    'pop_back': {'return_type': 'void', 'params': []},
                    'begin': {'return_type': 'iterator', 'params': []},
                    'end': {'return_type': 'iterator', 'params': []},
                    'clear': {'return_type': 'void', 'params': []},
                    'resize': {'return_type': 'void', 'params': ['size_t']},
                    'reserve': {'return_type': 'void', 'params': ['size_t']},
                    'capacity': {'return_type': 'size_t', 'params': []}
                },
                'typedefs': {
                    'value_type': 'T',
                    'reference': 'T&',
                    'const_reference': 'const T&',
                    'iterator': 'std::vector<T>::iterator',
                    'const_iterator': 'std::vector<T>::const_iterator',
                    'size_type': 'size_t'
                }
            },
            'std::string': {
                'template_params': [],
                'methods': {
                    'size': {'return_type': 'size_t', 'params': []},
                    'length': {'return_type': 'size_t', 'params': []},
                    'empty': {'return_type': 'bool', 'params': []},
                    'at': {'return_type': 'char&', 'params': ['size_t']},
                    'operator[]': {'return_type': 'char&', 'params': ['size_t']},
                    'substr': {'return_type': 'std::string', 'params': ['size_t', 'size_t']},
                    'find': {'return_type': 'size_t', 'params': ['const std::string&']},
                    'replace': {'return_type': 'std::string&', 'params': ['size_t', 'size_t', 'const std::string&']},
                    'append': {'return_type': 'std::string&', 'params': ['const std::string&']},
                    'insert': {'return_type': 'std::string&', 'params': ['size_t', 'const std::string&']},
                    'erase': {'return_type': 'std::string&', 'params': ['size_t', 'size_t']},
                    'clear': {'return_type': 'void', 'params': []},
                    'c_str': {'return_type': 'const char*', 'params': []}
                }
            },
            'std::map': {
                'template_params': ['Key', 'T', 'Compare', 'Allocator'],
                'methods': {
                    'size': {'return_type': 'size_t', 'params': []},
                    'empty': {'return_type': 'bool', 'params': []},
                    'operator[]': {'return_type': 'T&', 'params': ['const Key&']},
                    'at': {'return_type': 'T&', 'params': ['const Key&']},
                    'find': {'return_type': 'iterator', 'params': ['const Key&']},
                    'insert': {'return_type': 'std::pair<iterator,bool>', 'params': ['const std::pair<const Key,T>&']},
                    'erase': {'return_type': 'size_t', 'params': ['const Key&']},
                    'clear': {'return_type': 'void', 'params': []},
                    'begin': {'return_type': 'iterator', 'params': []},
                    'end': {'return_type': 'iterator', 'params': []}
                },
                'typedefs': {
                    'key_type': 'Key',
                    'mapped_type': 'T',
                    'value_type': 'std::pair<const Key, T>',
                    'iterator': 'std::map<Key,T>::iterator',
                    'const_iterator': 'std::map<Key,T>::const_iterator'
                }
            },
            'std::set': {
                'template_params': ['T', 'Compare', 'Allocator'],
                'methods': {
                    'size': {'return_type': 'size_t', 'params': []},
                    'empty': {'return_type': 'bool', 'params': []},
                    'find': {'return_type': 'iterator', 'params': ['const T&']},
                    'insert': {'return_type': 'std::pair<iterator,bool>', 'params': ['const T&']},
                    'erase': {'return_type': 'size_t', 'params': ['const T&']},
                    'clear': {'return_type': 'void', 'params': []},
                    'begin': {'return_type': 'iterator', 'params': []},
                    'end': {'return_type': 'iterator', 'params': []}
                }
            },
            'std::list': {
                'template_params': ['T', 'Allocator'],
                'methods': {
                    'size': {'return_type': 'size_t', 'params': []},
                    'empty': {'return_type': 'bool', 'params': []},
                    'front': {'return_type': 'T&', 'params': []},
                    'back': {'return_type': 'T&', 'params': []},
                    'push_front': {'return_type': 'void', 'params': ['const T&']},
                    'push_back': {'return_type': 'void', 'params': ['const T&']},
                    'pop_front': {'return_type': 'void', 'params': []},
                    'pop_back': {'return_type': 'void', 'params': []},
                    'begin': {'return_type': 'iterator', 'params': []},
                    'end': {'return_type': 'iterator', 'params': []}
                }
            }
        }
        
        self.algorithms = {
            'std::find': {'return_type': 'Iterator', 'params': ['Iterator', 'Iterator', 'const T&']},
            'std::find_if': {'return_type': 'Iterator', 'params': ['Iterator', 'Iterator', 'Predicate']},
            'std::sort': {'return_type': 'void', 'params': ['Iterator', 'Iterator']},
            'std::transform': {'return_type': 'OutputIterator', 'params': ['InputIterator', 'InputIterator', 'OutputIterator', 'UnaryOperation']},
            'std::for_each': {'return_type': 'UnaryFunction', 'params': ['Iterator', 'Iterator', 'UnaryFunction']},
            'std::count': {'return_type': 'typename iterator_traits<Iterator>::difference_type', 'params': ['Iterator', 'Iterator', 'const T&']},
            'std::copy': {'return_type': 'OutputIterator', 'params': ['InputIterator', 'InputIterator', 'OutputIterator']}
        }
        
        self.smart_pointers = {
            'std::unique_ptr': {
                'template_params': ['T', 'Deleter'],
                'methods': {
                    'get': {'return_type': 'T*', 'params': []},
                    'operator*': {'return_type': 'T&', 'params': []},
                    'operator->': {'return_type': 'T*', 'params': []},
                    'reset': {'return_type': 'void', 'params': ['T*']},
                    'release': {'return_type': 'T*', 'params': []},
                    'operator bool': {'return_type': 'bool', 'params': []}
                }
            },
            'std::shared_ptr': {
                'template_params': ['T'],
                'methods': {
                    'get': {'return_type': 'T*', 'params': []},
                    'operator*': {'return_type': 'T&', 'params': []},
                    'operator->': {'return_type': 'T*', 'params': []},
                    'reset': {'return_type': 'void', 'params': ['T*']},
                    'use_count': {'return_type': 'long', 'params': []},
                    'operator bool': {'return_type': 'bool', 'params': []}
                }
            }
        }

    def get_container_info(self, container_type: str) -> Optional[Dict[str, Any]]:
        """获取容器类型信息"""
        return self.containers.get(container_type)
    
    def get_method_return_type(self, container_type: str, method_name: str, template_args: List[str] = None) -> Optional[str]:
        """获取容器方法的返回类型"""
        container_info = self.containers.get(container_type)
        if not container_info:
            return None
            
        method_info = container_info['methods'].get(method_name)
        if not method_info:
            return None
            
        return_type = method_info['return_type']
        
        # 处理模板参数替换
        if template_args and container_info.get('template_params'):
            for i, param in enumerate(container_info['template_params']):
                if i < len(template_args):
                    return_type = return_type.replace(param, template_args[i])
                    
        return return_type


class EnhancedTypeInferenceEngine:
    """增强版类型推断引擎"""
    
    def __init__(self, repo: NodeRepository):
        self.repo = repo
        self.logger = Logger.get_logger()
        self.stl_db = STLTypeDatabase()
        
        # 类型推断缓存
        self.type_cache: Dict[str, str] = {}  # expression -> type
        self.variable_types: Dict[str, str] = {}  # variable_name -> type
        self.function_signatures: Dict[str, Dict[str, str]] = {}  # function_usr -> {param_name: type}
        
        # 上下文信息
        self.current_function_usr: Optional[str] = None
        self.current_class_usr: Optional[str] = None
        self.current_namespace: List[str] = []
        
        # 文件内容（用于源码分析）
        self.file_content: Optional[bytes] = None
        
    def set_file_content(self, content: bytes):
        """设置当前文件内容"""
        self.file_content = content
        
    def set_context(self, function_usr: str = None, class_usr: str = None, namespace: List[str] = None):
        """设置推断上下文"""
        self.current_function_usr = function_usr
        self.current_class_usr = class_usr
        self.current_namespace = namespace or []
        
    def analyze_function_variables(self, function_node, function_usr: str):
        """分析函数内的变量类型（从AST节点）"""
        self.current_function_usr = function_usr
        self.function_signatures[function_usr] = {}
        
        # 分析函数参数
        parameters_node = function_node.child_by_field_name('parameters')
        if parameters_node:
            self._analyze_parameter_list(parameters_node, function_usr)
            
        # 分析函数体内的变量声明
        body_node = function_node.child_by_field_name('body')
        if body_node:
            self._analyze_variable_declarations(body_node, function_usr)
    
    def _analyze_parameter_list(self, parameters_node, function_usr: str):
        """分析函数参数列表"""
        for param_node in parameters_node.children:
            if param_node.type == 'parameter_declaration':
                param_type = self._extract_type_from_declaration(param_node)
                param_name = self._extract_identifier_from_declaration(param_node)
                
                if param_type and param_name:
                    self.function_signatures[function_usr][param_name] = param_type
                    self.variable_types[f"{function_usr}::{param_name}"] = param_type
    
    def _analyze_variable_declarations(self, node, function_usr: str):
        """递归分析变量声明"""
        if node.type == 'declaration':
            var_type = self._extract_type_from_declaration(node)
            
            # 查找所有变量名
            for child in node.children:
                if child.type == 'init_declarator':
                    var_name = self._extract_identifier_from_declarator(child)
                    if var_type and var_name:
                        self.variable_types[f"{function_usr}::{var_name}"] = var_type
                        
        # 递归分析子节点
        for child in node.children:
            self._analyze_variable_declarations(child, function_usr)
    
    def _extract_type_from_declaration(self, decl_node) -> Optional[str]:
        """从声明节点提取类型"""
        if not self.file_content:
            return None
            
        # 查找类型说明符
        for child in decl_node.children:
            if child.type in ['type_identifier', 'primitive_type', 'qualified_identifier']:
                return self._get_text_from_node(child)
            elif child.type == 'template_type':
                return self._get_text_from_node(child)
                
        return None
    
    def _extract_identifier_from_declaration(self, decl_node) -> Optional[str]:
        """从声明节点提取标识符"""
        for child in decl_node.children:
            if child.type == 'identifier':
                return self._get_text_from_node(child)
            elif child.type == 'init_declarator':
                return self._extract_identifier_from_declarator(child)
        return None
    
    def _extract_identifier_from_declarator(self, declarator_node) -> Optional[str]:
        """从声明器节点提取标识符"""
        for child in declarator_node.children:
            if child.type == 'identifier':
                return self._get_text_from_node(child)
        return None
    
    def _get_text_from_node(self, node) -> str:
        """从AST节点获取文本内容"""
        if self.file_content and node:
            return self.file_content[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')
        return ""
    
    def infer_expression_type(self, expression: str, context_function_usr: str = None) -> Optional[str]:
        """推断表达式的类型 - 增强版"""
        # 检查缓存
        cache_key = f"{expression}::{context_function_usr or 'global'}"
        if cache_key in self.type_cache:
            return self.type_cache[cache_key]
        
        inferred_type = self._infer_expression_type_impl(expression, context_function_usr)
        
        # 缓存结果
        if inferred_type:
            self.type_cache[cache_key] = inferred_type
            
        return inferred_type
    
    def _infer_expression_type_impl(self, expression: str, context_function_usr: str = None) -> Optional[str]:
        """表达式类型推断的具体实现"""
        expression = expression.strip()
        
        # 1. 字面量类型推断
        literal_type = self._infer_literal_type(expression)
        if literal_type:
            return literal_type
        
        # 2. 变量类型推断
        if self._is_simple_identifier(expression):
            return self._infer_variable_type(expression, context_function_usr)
        
        # 3. 成员访问推断 (obj.member 或 obj->member)
        member_access_type = self._infer_member_access_type(expression, context_function_usr)
        if member_access_type:
            return member_access_type
        
        # 4. 函数调用推断
        if '(' in expression and ')' in expression:
            return self._infer_function_call_type(expression, context_function_usr)
        
        # 5. 数组/容器访问推断 (obj[index])
        if '[' in expression and ']' in expression:
            return self._infer_subscript_type(expression, context_function_usr)
        
        # 6. 运算符表达式推断
        return self._infer_operator_expression_type(expression, context_function_usr)
    
    def _infer_literal_type(self, expression: str) -> Optional[str]:
        """推断字面量类型"""
        # 字符串字面量
        if (expression.startswith('"') and expression.endswith('"')) or \
           (expression.startswith("'") and expression.endswith("'")):
            if expression.startswith('"'):
                return "const char*" if len(expression) > 3 else "char"
            else:
                return "char"
        
        # 数字字面量
        if expression.isdigit():
            return "int"
        
        # 浮点数字面量
        if re.match(r'^\d+\.\d+[fF]?$', expression):
            return "float" if expression.endswith('f') or expression.endswith('F') else "double"
        
        # 布尔字面量
        if expression in ['true', 'false']:
            return "bool"
        
        # nullptr字面量
        if expression == 'nullptr':
            return "std::nullptr_t"
        
        return None
    
    def _is_simple_identifier(self, expression: str) -> bool:
        """检查是否是简单标识符"""
        return re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', expression) is not None
    
    def _infer_variable_type(self, var_name: str, context_function_usr: str = None) -> Optional[str]:
        """推断变量类型"""
        with self.repo._lock.read_lock():
            # 1. 检查函数参数和局部变量
            if context_function_usr:
                full_var_name = f"{context_function_usr}::{var_name}"
                if full_var_name in self.variable_types:
                    return self.variable_types[full_var_name]
            
            # 2. 检查类成员变量
            if self.current_class_usr:
                class_entity = self.repo.get_node(self.current_class_usr)
                if isinstance(class_entity, Class):
                    for field_usr in class_entity.fields:
                        field_entity = self.repo.get_node(field_usr)
                        if isinstance(field_entity, Variable) and field_entity.name == var_name:
                            return field_entity.var_type
            
            # 3. 检查全局变量
            global_vars = self.repo.find_by_qualified_name(var_name, 'variable')
            if global_vars:
                return global_vars[0].var_type
            
            return None
    

    
    def _infer_member_access_type(self, expression: str, context_function_usr: str = None) -> Optional[str]:
        """推断成员访问表达式的类型"""
        # 处理 obj.member 访问
        if '.' in expression and '->' not in expression:
            parts = expression.split('.', 1)
            if len(parts) == 2:
                obj_expr, member_name = parts
                obj_type = self.infer_expression_type(obj_expr.strip(), context_function_usr)
                if obj_type:
                    return self._get_member_type(obj_type, member_name.strip())
        
        # 处理 obj->member 访问
        if '->' in expression:
            parts = expression.split('->', 1)
            if len(parts) == 2:
                obj_expr, member_name = parts
                obj_type = self.infer_expression_type(obj_expr.strip(), context_function_usr)
                if obj_type:
                    # 对于指针类型，需要解引用
                    if obj_type.endswith('*'):
                        base_type = obj_type[:-1].strip()
                        return self._get_member_type(base_type, member_name.strip())
                    # 对于智能指针
                    elif 'ptr' in obj_type.lower():
                        base_type = self._extract_template_arg(obj_type, 0)
                        if base_type:
                            return self._get_member_type(base_type, member_name.strip())
        
        return None
    
    def _get_member_type(self, class_type: str, member_name: str) -> Optional[str]:
        """获取类成员的类型"""
        # 1. 检查STL容器
        stl_method_type = self._get_stl_method_type(class_type, member_name)
        if stl_method_type:
            return stl_method_type
        
        # 2. 检查用户定义类型
        with self.repo._lock.read_lock():
            class_entities = self.repo.find_by_qualified_name(class_type, 'class')
            if class_entities:
                class_entity = class_entities[0]
                
                # 检查方法
                for method_usr in class_entity.methods:
                    method_entity = self.repo.get_node(method_usr)
                    if isinstance(method_entity, Function) and method_entity.name == member_name:
                        return method_entity.return_type
                
                # 检查字段
                for field_usr in class_entity.fields:
                    field_entity = self.repo.get_node(field_usr)
                    if isinstance(field_entity, Variable) and field_entity.name == member_name:
                        return field_entity.var_type
        
        return None
    
    def _get_stl_method_type(self, container_type: str, method_name: str) -> Optional[str]:
        """获取STL容器方法的返回类型"""
        # 提取模板参数
        template_args = self._extract_all_template_args(container_type)
        
        # 标准化容器类型名
        base_container_type = self._normalize_stl_type(container_type)
        
        return self.stl_db.get_method_return_type(base_container_type, method_name, template_args)
    
    def _normalize_stl_type(self, type_name: str) -> str:
        """标准化STL类型名"""
        # 移除模板参数，只保留基础类型名
        if '<' in type_name:
            return type_name.split('<')[0].strip()
        return type_name.strip()
    
    def _extract_template_arg(self, template_type: str, index: int) -> Optional[str]:
        """提取模板参数"""
        template_args = self._extract_all_template_args(template_type)
        if template_args and index < len(template_args):
            return template_args[index]
        return None
    
    def _extract_all_template_args(self, template_type: str) -> List[str]:
        """提取所有模板参数"""
        if '<' not in template_type or '>' not in template_type:
            return []
        
        start = template_type.find('<')
        end = template_type.rfind('>')
        if start == -1 or end == -1 or start >= end:
            return []
        
        args_str = template_type[start+1:end].strip()
        if not args_str:
            return []
        
        # 简单分割（需要处理嵌套模板的情况）
        args = []
        current_arg = ""
        depth = 0
        
        for char in args_str:
            if char == '<':
                depth += 1
                current_arg += char
            elif char == '>':
                depth -= 1
                current_arg += char
            elif char == ',' and depth == 0:
                args.append(current_arg.strip())
                current_arg = ""
            else:
                current_arg += char
        
        if current_arg.strip():
            args.append(current_arg.strip())
        
        return args
    
    def _infer_function_call_type(self, expression: str, context_function_usr: str = None) -> Optional[str]:
        """推断函数调用的返回类型"""
        # 提取函数名和参数
        paren_pos = expression.find('(')
        if paren_pos == -1:
            return None
        
        func_expr = expression[:paren_pos].strip()
        
        # 处理成员函数调用
        if '.' in func_expr or '->' in func_expr:
            return self._infer_member_access_type(func_expr, context_function_usr)
        
        # 处理普通函数调用
        with self.repo._lock.read_lock():
            func_entities = self.repo.find_by_qualified_name(func_expr, 'function')
            if func_entities:
                # 优先选择定义而不是声明
                for func in func_entities:
                    if func.is_definition and func.return_type:
                        return func.return_type
                # 如果没有定义，使用第一个声明
                if func_entities[0].return_type:
                    return func_entities[0].return_type
        
        return None
    
    def _infer_subscript_type(self, expression: str, context_function_usr: str = None) -> Optional[str]:
        """推断数组/容器下标访问的类型"""
        bracket_pos = expression.find('[')
        if bracket_pos == -1:
            return None
        
        container_expr = expression[:bracket_pos].strip()
        container_type = self.infer_expression_type(container_expr, context_function_usr)
        
        if not container_type:
            return None
        
        # 处理数组类型
        if container_type.endswith('[]') or '[' in container_type:
            # 移除数组标记，返回元素类型
            return container_type.replace('[]', '').split('[')[0].strip()
        
        # 处理指针类型
        if container_type.endswith('*'):
            return container_type[:-1].strip()
        
        # 处理STL容器
        if container_type.startswith('std::'):
            element_type = self._extract_template_arg(container_type, 0)
            if element_type:
                return element_type
        
        return None
    
    def _infer_operator_expression_type(self, expression: str, context_function_usr: str = None) -> Optional[str]:
        """推断运算符表达式的类型"""
        # 算术运算符
        for op in ['+', '-', '*', '/', '%']:
            if op in expression:
                parts = expression.split(op, 1)
                if len(parts) == 2:
                    left_type = self.infer_expression_type(parts[0].strip(), context_function_usr)
                    right_type = self.infer_expression_type(parts[1].strip(), context_function_usr)
                    return self._resolve_arithmetic_type(left_type, right_type, op)
        
        # 比较运算符
        for op in ['==', '!=', '<', '>', '<=', '>=']:
            if op in expression:
                return "bool"
        
        # 逻辑运算符
        for op in ['&&', '||']:
            if op in expression:
                return "bool"
        
        return None
    
    def _resolve_arithmetic_type(self, left_type: Optional[str], right_type: Optional[str], operator: str) -> Optional[str]:
        """解析算术运算的结果类型"""
        if not left_type or not right_type:
            return None
        
        # C++类型提升规则的简化版本
        type_hierarchy = ['bool', 'char', 'short', 'int', 'long', 'float', 'double']
        
        left_rank = type_hierarchy.index(left_type) if left_type in type_hierarchy else -1
        right_rank = type_hierarchy.index(right_type) if right_type in type_hierarchy else -1
        
        if left_rank >= 0 and right_rank >= 0:
            # 返回更高级的类型
            return type_hierarchy[max(left_rank, right_rank)]
        
        # 如果是相同的用户定义类型，假设有重载运算符
        if left_type == right_type:
            return left_type
        
        return None
    
    def resolve_overloaded_function(self, function_name: str, argument_types: List[str], context_namespace: str = "") -> Optional[str]:
        """重载决议 - 增强版"""
        # 查找所有同名函数
        possible_names = [
            function_name,
            f"{context_namespace}::{function_name}" if context_namespace else function_name
        ]
        
        candidates = []
        with self.repo._lock.read_lock():
            for name in possible_names:
                functions = self.repo.find_by_qualified_name(name, 'function')
                candidates.extend(functions)
        
        if not candidates:
            return None
        
        # 重载决议算法
        best_match = None
        best_score = -1
        
        for candidate in candidates:
            score = self._calculate_match_score(candidate, argument_types)
            if score > best_score:
                best_score = score
                best_match = candidate
        
        return best_match.usr if best_match else None
    
    def _calculate_match_score(self, function: Function, argument_types: List[str]) -> int:
        """计算函数匹配分数"""
        if not function.parameters:
            return 0 if not argument_types else -1
        
        if len(function.parameters) != len(argument_types):
            return -1  # 参数数量不匹配
        
        score = 0
        for i, param in enumerate(function.parameters):
            param_type = param.get('type', '')
            arg_type = argument_types[i]
            
            if param_type == arg_type:
                score += 100  # 完全匹配
            elif self._is_convertible(arg_type, param_type):
                score += 50   # 可转换
            else:
                return -1     # 不兼容
        
        return score
    
    def _is_convertible(self, from_type: str, to_type: str) -> bool:
        """检查类型是否可转换"""
        # 简化的类型转换规则
        conversions = {
            'int': ['long', 'float', 'double'],
            'float': ['double'],
            'char': ['int', 'long'],
            'bool': ['int', 'long']
        }
        
        return to_type in conversions.get(from_type, [])
    
    def clear_cache(self):
        """清理类型推断缓存"""
        self.type_cache.clear()
        self.variable_types.clear()
        self.function_signatures.clear()


# 保持向后兼容性的别名
TypeInferenceEngine = EnhancedTypeInferenceEngine 