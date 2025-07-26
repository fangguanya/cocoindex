"""
函数调用关系分析器

该模块负责从tree-sitter AST中识别函数调用关系，
并在NodeRepository中建立双向的calls_to和called_by关系。

主要功能：
1. 识别直接函数调用
2. 识别成员函数调用
3. 识别运算符重载调用
4. 识别构造函数调用
5. 处理虚函数调用
6. 建立双向调用关系
"""

from tree_sitter import Node
from typing import List, Optional, Set, Dict, Tuple
import re

from .logger import Logger
from .data_structures import NodeRepository, Function


class CallRelationshipAnalyzer:
    """函数调用关系分析器"""
    
    def __init__(self, repo: NodeRepository):
        self.repo = repo
        self.logger = Logger.get_logger()
        self.current_namespace_stack: List[str] = []
        self.current_class_stack: List[str] = []
        
    def analyze_function_calls(self, function_node: Node, function_usr_id: str, file_content: bytes):
        """分析函数体中的所有调用"""
        self.file_content = file_content
        calls_to = []
        
        # 获取函数体节点
        body_node = function_node.child_by_field_name('body')
        if not body_node:
            return calls_to
            
        # 查找所有函数调用表达式
        for call_node in self._find_call_expressions(body_node):
            called_usr_id = self._analyze_single_call(call_node, function_usr_id)
            if called_usr_id and called_usr_id not in calls_to:
                calls_to.append(called_usr_id)
                # 建立双向关系
                self.repo.add_call_relationship(function_usr_id, called_usr_id)
        
        return calls_to
    
    def _find_call_expressions(self, node: Node) -> List[Node]:
        """递归查找所有函数调用表达式"""
        call_expressions = []
        
        # 定义需要识别的调用类型
        call_types = {
            'call_expression',           # 普通函数调用: func()
            'field_expression',          # 成员访问可能是函数调用: obj.method
            'subscript_expression',      # 重载operator[]
            'binary_expression',         # 运算符重载
            'unary_expression',          # 一元运算符重载
            'assignment_expression',     # 赋值运算符重载
            'update_expression',         # ++, -- 运算符
            'new_expression',           # new 表达式（构造函数调用）
            'delete_expression',        # delete 表达式（析构函数调用）
        }
        
        def traverse(n: Node):
            if n.type in call_types:
                # 进一步检查是否真的是函数调用
                if self._is_function_call(n):
                    call_expressions.append(n)
            
            # 递归遍历子节点
            for child in n.children:
                traverse(child)
        
        traverse(node)
        return call_expressions
    
    def _is_function_call(self, node: Node) -> bool:
        """判断节点是否真的是函数调用"""
        if node.type == 'call_expression':
            return True
        elif node.type == 'field_expression':
            # 检查是否有后续的调用表达式
            parent = node.parent
            return parent and parent.type == 'call_expression' and parent.child_by_field_name('function') == node
        elif node.type in ['binary_expression', 'unary_expression', 'assignment_expression']:
            # 运算符重载可能是函数调用
            return True
        elif node.type in ['new_expression', 'delete_expression']:
            return True
        
        return False
    
    def _analyze_single_call(self, call_node: Node, caller_usr_id: str) -> Optional[str]:
        """分析单个函数调用，返回被调用函数的USR ID"""
        try:
            if call_node.type == 'call_expression':
                return self._analyze_direct_call(call_node, caller_usr_id)
            elif call_node.type == 'field_expression':
                return self._analyze_member_call(call_node, caller_usr_id)
            elif call_node.type in ['binary_expression', 'unary_expression', 'assignment_expression']:
                return self._analyze_operator_call(call_node, caller_usr_id)
            elif call_node.type == 'new_expression':
                return self._analyze_constructor_call(call_node, caller_usr_id)
            elif call_node.type == 'delete_expression':
                return self._analyze_destructor_call(call_node, caller_usr_id)
        except Exception as e:
            self.logger.warning(f"Error analyzing call in {caller_usr_id}: {e}")
        
        return None
    
    def _analyze_direct_call(self, call_node: Node, caller_usr_id: str) -> Optional[str]:
        """分析直接函数调用"""
        function_node = call_node.child_by_field_name('function')
        if not function_node:
            return None
        
        function_name = self._get_text(function_node)
        
        # 处理命名空间限定的调用
        if '::' in function_name:
            qualified_name = function_name
        else:
            # 构建可能的qualified names
            qualified_name = self._build_qualified_name(function_name)
        
        # 解析函数调用
        return self.repo.resolve_function_call(
            qualified_name,
            self._get_current_namespace(),
            self._get_current_class()
        )
    
    def _analyze_member_call(self, field_node: Node, caller_usr_id: str) -> Optional[str]:
        """分析成员函数调用"""
        # 获取对象和方法名
        object_node = field_node.child_by_field_name('argument')
        field_name_node = field_node.child_by_field_name('field')
        
        if not field_name_node:
            return None
        
        method_name = self._get_text(field_name_node)
        
        # 尝试确定对象类型
        object_type = self._infer_object_type(object_node) if object_node else None
        
        if object_type:
            qualified_method_name = f"{object_type}::{method_name}"
        else:
            # 如果无法确定对象类型，使用当前类上下文
            current_class = self._get_current_class()
            if current_class:
                qualified_method_name = f"{current_class}::{method_name}"
            else:
                qualified_method_name = method_name
        
        return self.repo.resolve_function_call(
            qualified_method_name,
            self._get_current_namespace(),
            self._get_current_class()
        )
    
    def _analyze_operator_call(self, op_node: Node, caller_usr_id: str) -> Optional[str]:
        """分析运算符重载调用"""
        operator = op_node.child_by_field_name('operator')
        if not operator:
            return None
        
        op_text = self._get_text(operator)
        
        # 构建运算符函数名
        operator_map = {
            '+': 'operator+',
            '-': 'operator-',
            '*': 'operator*',
            '/': 'operator/',
            '=': 'operator=',
            '==': 'operator==',
            '!=': 'operator!=',
            '<': 'operator<',
            '>': 'operator>',
            '<=': 'operator<=',
            '>=': 'operator>=',
            '++': 'operator++',
            '--': 'operator--',
            '[]': 'operator[]',
            '()': 'operator()',
            '->': 'operator->',
        }
        
        operator_name = operator_map.get(op_text, f"operator{op_text}")
        
        # 尝试确定操作数类型
        if op_node.type == 'binary_expression':
            left_node = op_node.child_by_field_name('left')
            left_type = self._infer_object_type(left_node) if left_node else None
            if left_type:
                qualified_operator_name = f"{left_type}::{operator_name}"
            else:
                qualified_operator_name = operator_name
        else:
            # 一元运算符
            qualified_operator_name = operator_name
        
        return self.repo.resolve_function_call(
            qualified_operator_name,
            self._get_current_namespace(),
            self._get_current_class()
        )
    
    def _analyze_constructor_call(self, new_node: Node, caller_usr_id: str) -> Optional[str]:
        """分析构造函数调用"""
        type_node = new_node.child_by_field_name('type')
        if not type_node:
            return None
        
        type_name = self._get_text(type_node)
        constructor_name = f"{type_name}::{type_name}"  # 构造函数名与类名相同
        
        return self.repo.resolve_function_call(
            constructor_name,
            self._get_current_namespace(),
            self._get_current_class()
        )
    
    def _analyze_destructor_call(self, delete_node: Node, caller_usr_id: str) -> Optional[str]:
        """分析析构函数调用"""
        # delete表达式可能隐式调用析构函数
        # 这里简化处理，实际情况更复杂
        return None
    
    def _infer_object_type(self, object_node: Node) -> Optional[str]:
        """推断对象的类型"""
        if not object_node:
            return None
        
        # 简化的类型推断
        object_text = self._get_text(object_node)
        
        # 如果是this指针
        if object_text == 'this':
            return self._get_current_class()
        
        # 如果是变量名，可以尝试从声明中推断类型
        # 这里简化处理，实际需要更复杂的类型推断系统
        
        return None
    
    def _build_qualified_name(self, name: str) -> str:
        """根据当前上下文构建qualified name"""
        parts = []
        
        if self.current_namespace_stack:
            parts.extend(self.current_namespace_stack)
        
        if self.current_class_stack:
            parts.extend(self.current_class_stack)
        
        parts.append(name)
        return "::".join(parts)
    
    def _get_current_namespace(self) -> str:
        """获取当前命名空间"""
        return "::".join(self.current_namespace_stack) if self.current_namespace_stack else ""
    
    def _get_current_class(self) -> str:
        """获取当前类"""
        if self.current_class_stack:
            return "::".join(self.current_namespace_stack + self.current_class_stack)
        return ""
    
    def _get_text(self, node: Node) -> str:
        """获取节点的文本内容"""
        if not node:
            return ""
        return self.file_content[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')
    
    def set_context(self, namespace_stack: List[str], class_stack: List[str]):
        """设置当前的命名空间和类上下文"""
        self.current_namespace_stack = namespace_stack.copy()
        self.current_class_stack = class_stack.copy() 