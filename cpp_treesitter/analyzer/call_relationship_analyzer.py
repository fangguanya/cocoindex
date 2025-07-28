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
from typing import List, Optional, Set, Dict, Tuple, Any
import re

from .logger import Logger
from .data_structures import NodeRepository, Function
from .type_inference import TypeInferenceEngine, TypeInfo


class CallRelationshipAnalyzer:
    """函数调用关系分析器"""
    
    def __init__(self, repo: NodeRepository):
        self.repo = repo
        self.logger = Logger.get_logger()
        self.current_namespace_stack: List[str] = []
        self.current_class_stack: List[str] = []
        
        # 集成类型推断引擎
        self.type_engine = TypeInferenceEngine(repo)
        
        # 新增：待解析调用队列
        self.pending_calls: List[Tuple[str, str, Node, bytes]] = []  # (caller_usr, qualified_name, call_node, file_content)
        self.resolved_calls_count = 0
        self.pending_calls_count = 0
        
    def analyze_function_calls(self, function_node: Node, function_usr_id: str, file_content: bytes):
        """分析函数体中的所有调用 - 增强版"""
        self.file_content = file_content
        self.type_engine.set_file_content(file_content)
        
        # 设置当前函数上下文
        self.current_function_usr = function_usr_id
        
        calls_to = []
        
        # 分析函数内的变量类型（用于类型推断）
        self.type_engine.analyze_function_variables(function_node, function_usr_id)
        
        # 获取函数体节点
        body_node = function_node.child_by_field_name('body')
        if not body_node:
            return calls_to
            
        # 查找所有函数调用表达式
        for call_node in self._find_call_expressions(body_node):
            called_usr_id = self._analyze_single_call_enhanced(call_node, function_usr_id)
            if called_usr_id:
                # 🔧 修复：允许重复调用关系，因为同一函数可能被多次调用
                # 或者重载函数可能有多个不同的调用
                if called_usr_id not in calls_to:
                    calls_to.append(called_usr_id)
                # 注意：调用关系已经在_analyze_single_call_enhanced中建立，这里不再重复
        
        return calls_to
    
    def _find_call_expressions(self, node: Node) -> List[Node]:
        """递归查找所有函数调用表达式 - 增强版：支持现代C++调用类型"""
        call_expressions = []
        
        # 定义需要识别的调用类型（扩展版）
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
            
            # 新增：现代C++特性
            'lambda_expression',        # lambda表达式调用
            'template_instantiation',   # 模板实例化
            'function_declarator',      # 函数指针/函数对象调用
            'cast_expression',          # 类型转换（可能包含构造函数调用）
            'initializer_list',         # 初始化列表（构造函数调用）
            'compound_literal_expression', # 复合字面量
            'parenthesized_expression', # 可能包含函数指针调用
            'conditional_expression',  # 三元运算符中的函数调用
            'sizeof_expression',        # sizeof表达式中的函数调用
            'alignof_expression',       # alignof表达式
            'generic_expression',       # C11泛型表达式
            'statement_expression',     # 语句表达式中的调用
        }
        
        def traverse(n: Node):
            if n.type in call_types:
                # 进一步检查是否真的是函数调用
                if self._is_function_call(n):
                    call_expressions.append(n)
            
            # 特殊处理：在lambda表达式内部查找调用
            if n.type == 'lambda_expression':
                # lambda表达式本身可能被调用，同时内部也可能有调用
                self._find_lambda_calls(n, call_expressions)
            
            # 特殊处理：模板实例化中的函数调用
            elif n.type == 'template_instantiation':
                self._find_template_calls(n, call_expressions)
            
            # 特殊处理：函数指针调用
            elif n.type == 'parenthesized_expression':
                self._find_function_pointer_calls(n, call_expressions)
            
            # 递归遍历子节点
            for child in n.children:
                traverse(child)
        
        traverse(node)
        return call_expressions
    
    def _find_lambda_calls(self, lambda_node: Node, call_expressions: List[Node]):
        """查找lambda表达式相关的调用"""
        # 1. lambda表达式本身可能被立即调用：[](){ }()
        parent = lambda_node.parent
        if parent and parent.type == 'call_expression':
            function_node = parent.child_by_field_name('function')
            if function_node == lambda_node:
                call_expressions.append(parent)
        
        # 2. lambda表达式内部的函数调用
        body_node = lambda_node.child_by_field_name('body')
        if body_node:
            for child in body_node.children:
                if child.type == 'call_expression':
                    call_expressions.append(child)
    
    def _find_template_calls(self, template_node: Node, call_expressions: List[Node]):
        """查找模板实例化中的函数调用"""
        # 模板实例化可能是函数模板的调用
        name_node = template_node.child_by_field_name('name')
        if name_node:
            # 检查是否有后续的函数调用
            parent = template_node.parent
            if parent and parent.type == 'call_expression':
                function_node = parent.child_by_field_name('function')
                if function_node == template_node:
                    call_expressions.append(parent)
    
    def _find_function_pointer_calls(self, paren_node: Node, call_expressions: List[Node]):
        """查找函数指针调用"""
        # 检查括号表达式是否是函数指针调用：(*func_ptr)()
        if len(paren_node.children) >= 3:  # ( expr )
            inner_expr = paren_node.children[1]
            if inner_expr.type == 'pointer_expression':  # *func_ptr
                # 检查是否有后续的调用
                parent = paren_node.parent
                if parent and parent.type == 'call_expression':
                    function_node = parent.child_by_field_name('function')
                    if function_node == paren_node:
                        call_expressions.append(parent)
    
    def _is_function_call(self, node: Node) -> bool:
        """判断节点是否真的是函数调用 - 增强版"""
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
        elif node.type == 'lambda_expression':
            # lambda表达式如果被立即调用
            parent = node.parent
            return parent and parent.type == 'call_expression' and parent.child_by_field_name('function') == node
        elif node.type == 'template_instantiation':
            # 模板实例化如果后面跟着调用
            parent = node.parent
            return parent and parent.type == 'call_expression' and parent.child_by_field_name('function') == node
        elif node.type == 'cast_expression':
            # 类型转换可能是构造函数调用
            return True
        elif node.type == 'initializer_list':
            # 初始化列表可能调用构造函数
            return True
        
        return False
    
    def _analyze_single_call_enhanced(self, call_node: Node, caller_usr_id: str) -> Optional[str]:
        """分析单个函数调用，返回被调用函数的USR ID - 增强版带重载决议"""
        try:
            callee_usr = None
            self.logger.info(f"📞 分析调用节点类型: {call_node.type} (caller: {caller_usr_id[:20]}...)")
            
            if call_node.type == 'call_expression':
                callee_usr = self._analyze_direct_call_enhanced(call_node, caller_usr_id)
            elif call_node.type == 'field_expression':
                callee_usr = self._analyze_member_call_enhanced(call_node, caller_usr_id)
            elif call_node.type in ['binary_expression', 'unary_expression', 'assignment_expression']:
                callee_usr = self._analyze_operator_call_enhanced(call_node, caller_usr_id)
            elif call_node.type == 'new_expression':
                callee_usr = self._analyze_constructor_call_enhanced(call_node, caller_usr_id)
            elif call_node.type == 'delete_expression':
                callee_usr = self._analyze_destructor_call(call_node, caller_usr_id)
            else:
                self.logger.warning(f"⚠️ 未处理的调用节点类型: {call_node.type}")
            
            # 修复：立即建立调用关系，确保calls_to和called_by同步
            if callee_usr:
                self.logger.info(f"🔗 建立调用关系: {caller_usr_id[:20]}... -> {callee_usr[:20]}...")
                self.repo.add_call_relationship(caller_usr_id, callee_usr)
                self.resolved_calls_count += 1
                return callee_usr
            else:
                # 如果无法立即解析，添加到待解析队列
                function_name = self._extract_call_qualified_name(call_node)
                if function_name:
                    self.pending_calls.append((caller_usr_id, function_name, call_node, self.file_content))
                    self.pending_calls_count += 1
                    self.logger.info(f"📋 添加待解析调用: {function_name}")
                else:
                    self.logger.warning(f"⚠️ 无法提取函数名")
                
            return callee_usr
        except Exception as e:
            self.logger.warning(f"Error analyzing call in {caller_usr_id}: {e}")
        
        return None

    def _analyze_destructor_call(self, delete_node: Node, caller_usr_id: str) -> Optional[str]:
        """分析析构函数调用"""
        # delete表达式可能隐式调用析构函数
        # 这里简化处理，实际情况更复杂
        return None
    
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
        # 修复：如果file_content没有设置，使用节点的text属性作为备选
        if hasattr(self, 'file_content') and self.file_content:
            return self.file_content[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')
        else:
            # 备选方案：使用节点的text属性
            return node.text.decode('utf-8', errors='ignore')
    
    def set_context(self, namespace_stack: List[str], class_stack: List[str]):
        """设置当前的命名空间和类上下文"""
        self.current_namespace_stack = namespace_stack.copy()
        self.current_class_stack = class_stack.copy() 

    def _analyze_direct_call_enhanced(self, call_node: Node, caller_usr_id: str) -> Optional[str]:
        """分析直接函数调用 - 增强版带重载决议"""
        function_node = call_node.child_by_field_name('function')
        if not function_node:
            return None
        
        # 使用增强的函数名提取方法
        function_name = self._extract_function_name_from_node(function_node)
        if not function_name:
            # 回退到原始方法
            function_name = self._get_text(function_node)
        
        # 添加调试日志
        self.logger.info(f"🔍 分析直接函数调用: {function_name} (caller: {caller_usr_id[:20]}...)")
        
        # 构建可能的qualified names
        possible_names = self._build_possible_qualified_names(function_name)
        self.logger.debug(f"可能的qualified names: {possible_names}")
        
        # 查找所有候选函数
        candidates = []
        for name in possible_names:
            functions = self.repo.find_by_qualified_name(name, 'function')
            candidates.extend(functions)
        
        if candidates:
            self.logger.debug(f"通过qualified名称找到 {len(candidates)} 个候选")
        
        # 如果没有找到候选且是简单名称，尝试简单名称匹配（类似debug脚本中的逻辑）
        if not candidates and '::' not in function_name:
            self.logger.info(f"尝试简单名称匹配: {function_name}")
            with self.repo._lock.read_lock():
                for usr, entity in self.repo.nodes.items():
                    if (hasattr(entity, 'type') and entity.type == 'function' and
                        hasattr(entity, 'name') and entity.name == function_name):
                        candidates.append(entity)
                        self.logger.info(f"简单名称匹配成功: {entity.name}")
        
        if not candidates:
            self.logger.warning(f"未找到函数 {function_name} 的候选")
            return None
        
        # 如果只有一个候选，直接返回
        if len(candidates) == 1:
            self.logger.info(f"✅ 解析成功: {function_name} -> {candidates[0].usr[:20]}...")
            return candidates[0].usr
        
        # 多个候选：进行重载决议
        best_match = self._resolve_overloaded_call(call_node, candidates)
        if best_match:
            self.logger.info(f"✅ 重载决议成功: {function_name} -> {best_match.usr[:20]}...")
            return best_match.usr
        else:
            self.logger.info(f"⚠️ 重载决议失败，使用第一个候选: {function_name} -> {candidates[0].usr[:20]}...")
            return candidates[0].usr

    def _analyze_member_call_enhanced(self, field_node: Node, caller_usr_id: str) -> Optional[str]:
        """分析成员函数调用 - 增强版"""
        # 检查是否确实是成员函数调用
        parent = field_node.parent
        if not parent or parent.type != 'call_expression':
            return None
        
        # 获取对象和方法名
        object_node = field_node.child_by_field_name('argument')
        field_name_node = field_node.child_by_field_name('field')
        
        if not field_name_node:
            return None
        
        method_name = self._get_text(field_name_node)
        self.logger.info(f"🔍 分析成员函数调用: {method_name}")
        
        # 推断对象类型
        object_type = None
        if object_node:
            object_text = self._get_text(object_node)
            self.logger.debug(f"对象表达式: {object_text}")
            
            object_type_info = self.type_engine.infer_expression_type(object_node)
            if object_type_info:
                # 🚑 关键修复：确保type_name是字符串类型
                type_name = object_type_info.type_name if isinstance(object_type_info.type_name, str) else str(object_type_info.type_name)
                object_type = self._clean_type_string(type_name)
                self.logger.debug(f"推断的对象类型: {object_type}")
        
        # 构建可能的方法qualified names
        possible_names = []
        if object_type:
            possible_names.append(f"{object_type}::{method_name}")
            # 兼容命名空间 + 类情况，如 ns::Class::method
            if self.current_namespace_stack:
                ns_prefix = "::".join(self.current_namespace_stack)
                possible_names.append(f"{ns_prefix}::{object_type}::{method_name}")
        
        # 添加当前类上下文的可能性
        current_class = self._get_current_class()
        if current_class:
            possible_names.append(f"{current_class}::{method_name}")
        
        # 如果无法确定类型，添加基本名称并尝试简单匹配
        possible_names.append(method_name)
        
        # 去重，保持原有顺序
        seen = set()
        deduped_names = []
        for n in possible_names:
            if n not in seen:
                deduped_names.append(n)
                seen.add(n)
        
        self.logger.debug(f"可能的方法名: {deduped_names}")
        
        # 查找候选方法
        candidates = []
        for name in deduped_names:
            functions = self.repo.find_by_qualified_name(name, 'function')
            candidates.extend(functions)
        
        # 🔧 修复：如果没有找到候选且是简单方法名，尝试简单名称匹配
        if not candidates and '::' not in method_name:
            self.logger.info(f"尝试简单方法名匹配: {method_name}")
            with self.repo._lock.read_lock():
                for usr, entity in self.repo.nodes.items():
                    if (hasattr(entity, 'type') and entity.type == 'function' and
                        hasattr(entity, 'name') and entity.name == method_name):
                        candidates.append(entity)
                        self.logger.debug(f"简单方法名匹配成功: {entity.qualified_name}")
        
        if not candidates:
            self.logger.warning(f"未找到方法 {method_name} 的候选")
            return None
        
        self.logger.info(f"找到 {len(candidates)} 个候选方法")
        
        # 进行重载决议
        if len(candidates) == 1:
            self.logger.info(f"✅ 单一候选，解析成功: {method_name} -> {candidates[0].usr[:20]}...")
            return candidates[0].usr
        
        best_match = self._resolve_overloaded_call(parent, candidates)
        if best_match:
            self.logger.info(f"✅ 重载决议成功: {method_name} -> {best_match.usr[:20]}...")
            return best_match.usr
        else:
            self.logger.info(f"⚠️ 重载决议失败，使用第一个候选: {method_name} -> {candidates[0].usr[:20]}...")
            return candidates[0].usr

    def _analyze_operator_call_enhanced(self, op_node: Node, caller_usr_id: str) -> Optional[str]:
        """分析运算符重载调用 - 增强版"""
        operator = op_node.child_by_field_name('operator')
        if not operator:
            return None
        
        op_text = self._get_text(operator)
        
        # 构建运算符函数名
        operator_name = f"operator{op_text}"
        
        # 对于二元运算符，尝试推断左操作数类型
        if op_node.type == 'binary_expression':
            left_node = op_node.child_by_field_name('left')
            if left_node:
                left_type_info = self.type_engine.infer_expression_type(left_node)
                if left_type_info:
                    qualified_operator_name = f"{left_type_info.type_name}::{operator_name}"
                    
                    # 查找类成员运算符重载
                    functions = self.repo.find_by_qualified_name(qualified_operator_name, 'function')
                    if functions:
                        # 进行重载决议
                        candidates = functions
                        if len(candidates) == 1:
                            return candidates[0].usr
                        
                        # 创建虚拟的调用节点进行重载决议
                        best_match = self._resolve_operator_overload(op_node, candidates, left_type_info)
                        return best_match.usr if best_match else candidates[0].usr
        
        # 查找全局运算符重载
        global_functions = self.repo.find_by_qualified_name(operator_name, 'function')
        if global_functions:
            best_match = self._resolve_operator_overload(op_node, global_functions, None)
            return best_match.usr if best_match else global_functions[0].usr
        
        return None

    def _analyze_constructor_call_enhanced(self, new_node: Node, caller_usr_id: str) -> Optional[str]:
        """分析构造函数调用 - 增强版"""
        type_node = new_node.child_by_field_name('type')
        if not type_node:
            return None
        
        type_name = self._get_text(type_node)
        
        # 构造函数名与类名相同
        constructor_name = f"{type_name}::{type_name}"
        
        # 查找构造函数
        constructors = self.repo.find_by_qualified_name(constructor_name, 'function')
        if not constructors:
            return None
        
        # 如果有多个构造函数，进行重载决议
        if len(constructors) == 1:
            return constructors[0].usr
        
        # 分析new表达式的参数来决定调用哪个构造函数
        args_node = new_node.child_by_field_name('arguments')
        if args_node:
            # 创建虚拟的调用节点用于重载决议
            best_match = self._resolve_constructor_overload(args_node, constructors)
            return best_match.usr if best_match else constructors[0].usr
        
        return constructors[0].usr

    def _resolve_overloaded_call(self, call_node: Node, candidates: List[Function]) -> Optional[Function]:
        """解析重载函数调用"""
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        
        # 获取调用参数的类型
        args_node = call_node.child_by_field_name('arguments')
        if not args_node:
            return candidates[0]  # 无参数调用，返回第一个候选
        
        # 导入TypeInfo
        from .type_inference import TypeInfo
        
        arg_types = []
        for arg in args_node.children:
            if arg.type not in [',', '(', ')']:  # 跳过逗号和括号
                # 优先使用备用类型推断，因为它更可靠
                fallback_type = self._infer_argument_type_fallback(arg)
                if fallback_type:
                    arg_types.append(TypeInfo(type_name=fallback_type, confidence=0.9))
                    self.logger.debug(f"🔍 参数类型推断: {self._get_text(arg)} -> {fallback_type}")
                else:
                    # 尝试类型推断引擎
                    arg_type_info = self.type_engine.infer_expression_type(arg)
                    if arg_type_info:
                        arg_types.append(arg_type_info)
                    else:
                        arg_types.append(TypeInfo(type_name='unknown', confidence=0.1))
                        self.logger.warning(f"⚠️ 无法推断参数类型: {self._get_text(arg)}")
        
        self.logger.debug(f"🔍 重载决议: 候选函数 {len(candidates)} 个，参数类型 {[t.type_name for t in arg_types]}")
        
        # 简单的重载决议：匹配参数数量和类型
        best_match = None
        best_score = -1
        
        for candidate in candidates:
            score = self._calculate_match_score(candidate, arg_types)
            self.logger.debug(f"   候选: {candidate.signature}, 匹配分数: {score}")
            if score > best_score:
                best_score = score
                best_match = candidate
        
        if best_match:
            self.logger.info(f"✅ 重载决议成功: 选择 {best_match.signature} (分数: {best_score})")
        else:
            self.logger.warning(f"⚠️ 重载决议失败，使用第一个候选")
            best_match = candidates[0]
        
        return best_match

    def _resolve_operator_overload(self, op_node: Node, candidates: List[Function], left_type: Optional[TypeInfo]) -> Optional[Function]:
        """解析运算符重载"""
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        
        # 收集操作数类型
        operand_types = []
        
        if op_node.type == 'binary_expression':
            left_node = op_node.child_by_field_name('left')
            right_node = op_node.child_by_field_name('right')
            
            if left_node:
                left_type = self.type_engine.infer_expression_type(left_node)
                if left_type:
                    operand_types.append(left_type)
            
            if right_node:
                right_type = self.type_engine.infer_expression_type(right_node)
                if right_type:
                    operand_types.append(right_type)
        
        elif op_node.type == 'unary_expression':
            arg_node = op_node.child_by_field_name('argument')
            if arg_node:
                arg_type = self.type_engine.infer_expression_type(arg_node)
                if arg_type:
                    operand_types.append(arg_type)
        
        # 进行重载决议
        best_match = None
        best_score = -1
        
        for candidate in candidates:
            score = self._calculate_match_score(candidate, operand_types)
            if score > best_score:
                best_score = score
                best_match = candidate
        
        return best_match

    def _resolve_constructor_overload(self, args_node: Node, candidates: List[Function]) -> Optional[Function]:
        """解析构造函数重载"""
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        
        # 分析构造函数参数
        arg_types = []
        for arg in args_node.children:
            if arg.type != ',':
                arg_type_info = self.type_engine.infer_expression_type(arg)
                if arg_type_info:
                    arg_types.append(arg_type_info)
                else:
                    arg_types.append(TypeInfo(type_name='unknown', confidence=0.1))
        
        # 进行重载决议
        best_match = None
        best_score = -1
        
        for candidate in candidates:
            score = self._calculate_match_score(candidate, arg_types)
            if score > best_score:
                best_score = score
                best_match = candidate
        
        return best_match

    def _calculate_match_score(self, function: Function, arg_types: List[TypeInfo]) -> int:
        """计算函数匹配分数"""
        if len(function.parameters) != len(arg_types):
            return -1  # 参数数量不匹配
        
        score = 0
        for param, arg_type in zip(function.parameters, arg_types):
            param_type_str = param.get('type', 'unknown')
            
            if param_type_str == arg_type.type_name:
                score += 10  # 完全匹配
            elif self._types_compatible(param_type_str, arg_type.type_name):
                score += 5   # 兼容类型
            else:
                score -= 1   # 不匹配
        
        return score

    def _types_compatible(self, param_type: str, arg_type: str) -> bool:
        """检查类型兼容性 - 修复：改进字符串类型兼容性检查"""
        if param_type == arg_type:
            return True
        
        # 🔧 修复：增强字符串兼容性检查 - const char* 与各种std::string类型的兼容
        if arg_type == "const char*":
            # const char* 可以匹配各种std::string参数类型
            string_compatible_types = [
                "std::string", "const std::string", "std::string&", "const std::string&",
                "string", "const string", "string&", "const string&"
            ]
            if param_type in string_compatible_types:
                return True
            
            # 处理带有额外空格的类型
            normalized_param = param_type.replace(" ", "")
            normalized_compatible = [t.replace(" ", "") for t in string_compatible_types]
            if normalized_param in normalized_compatible:
                return True
        
        # 反向兼容：std::string 也可以传递给 const char*
        if param_type == "const char*" and arg_type in ["std::string", "string"]:
            return True
        
        # 原有的字符串兼容性逻辑 - 保留兼容
        if arg_type == 'std::string':
            # std::string 可以匹配 const std::string&, std::string&, const std::string
            normalized_param = param_type.replace('const ', '').replace('&', '').strip()
            if normalized_param == 'std::string':
                return True
        
        # 🔧 修复：增强引用兼容性检查，特别处理const std::string&参数
        if param_type == "const std::string&" or param_type == "const std::string &":
            if arg_type in ["const char*", "std::string", "string"]:
                return True
        
        # 数值类型的隐式转换
        numeric_types = ['int', 'long', 'float', 'double', 'char', 'short']
        if param_type in numeric_types and arg_type in numeric_types:
            return True
        
        # 指针和引用兼容性
        if param_type.endswith('*') and arg_type.endswith('*'):
            base_param = param_type.rstrip('*').strip()
            base_arg = arg_type.rstrip('*').strip()
            return base_param == base_arg
        
        # const兼容性
        if 'const' in param_type and 'const' not in arg_type:
            base_param = param_type.replace('const', '').strip()
            return base_param == arg_type
        
        # 🔧 修复：增强引用兼容性检查
        if param_type.endswith('&'):
            base_param = param_type.rstrip('&').strip()
            if base_param.startswith('const '):
                base_param = base_param[6:].strip()  # 移除"const "
            
            # 检查基础类型兼容性
            if base_param == arg_type:
                return True
            
            # 特别检查字符串类型
            if base_param == "std::string" and arg_type == "const char*":
                return True
        
        # std::string与const char*兼容性（保留原逻辑）
        if param_type == "std::string" and arg_type == "const char*":
            return True
        if param_type == "const char*" and arg_type == "std::string":
            return True
        
        return False

    def _build_possible_qualified_names(self, function_name: str) -> List[str]:
        """构建可能的限定名列表"""
        possible_names = [function_name]  # 直接名称
        
        # 添加当前命名空间上下文
        if self.current_namespace_stack:
            namespace_qualifier = "::".join(self.current_namespace_stack)
            possible_names.append(f"{namespace_qualifier}::{function_name}")
        
        # 添加当前类上下文
        if self.current_class_stack:
            class_qualifier = "::".join(self.current_namespace_stack + self.current_class_stack)
            possible_names.append(f"{class_qualifier}::{function_name}")
        
        # 添加完整的作用域
        if self.current_namespace_stack and self.current_class_stack:
            full_qualifier = "::".join(self.current_namespace_stack + self.current_class_stack)
            possible_names.append(f"{full_qualifier}::{function_name}")
        
        return possible_names 

    def _extract_call_qualified_name(self, call_node: Node) -> Optional[str]:
        """从调用节点中提取限定名 - 增强版：更好地处理复杂调用模式"""
        try:
            if call_node.type == 'call_expression':
                function_node = call_node.child_by_field_name('function')
                if function_node:
                    return self._extract_function_name_from_node(function_node)
            
            elif call_node.type == 'field_expression':
                field_name_node = call_node.child_by_field_name('field')
                if field_name_node:
                    method_name = self._get_text(field_name_node)
                    # 尝试推断对象类型
                    object_node = call_node.child_by_field_name('argument')
                    if object_node:
                        object_type_info = self.type_engine.infer_expression_type(object_node)
                        if object_type_info:
                            return f"{object_type_info.type_name}::{method_name}"
                    # 回退到当前类上下文
                    current_class = self._get_current_class()
                    return f"{current_class}::{method_name}" if current_class else method_name
            
            elif call_node.type in ['binary_expression', 'unary_expression', 'assignment_expression']:
                operator = call_node.child_by_field_name('operator')
                if operator:
                    op_text = self._get_text(operator)
                    return f"operator{op_text}"
            
            elif call_node.type == 'new_expression':
                type_node = call_node.child_by_field_name('type')
                if type_node:
                    type_name = self._get_text(type_node)
                    return f"{type_name}::{type_name}"  # 构造函数
            
            return None
        except Exception as e:
            self.logger.warning(f"提取调用限定名失败: {e}")
            return None
    
    def _extract_function_name_from_node(self, function_node: Node) -> Optional[str]:
        """从函数节点中提取函数名，处理各种复杂情况 - 增强版支持UE特有模式"""
        try:
            # 情况1: 简单标识符 func()
            if function_node.type == 'identifier':
                return self._get_text(function_node)
            
            # 情况2: 限定标识符 namespace::func() 或 Class::func()
            elif function_node.type == 'qualified_identifier':
                full_name = self._get_text(function_node)
                # 对于Super::func这种情况，需要特殊处理
                if full_name.startswith('Super::'):
                    # Super是UE中的特殊关键字，指向父类
                    simple_name = full_name.replace('Super::', '')
                    # 在当前类的上下文中查找，实际是父类的方法
                    return simple_name  # 简化处理，直接返回函数名
                return full_name
            
            # 情况3: 成员访问 obj.method() 或 obj->method()
            elif function_node.type == 'field_expression':
                field_node = function_node.child_by_field_name('field')
                if field_node:
                    method_name = self._get_text(field_node)
                    # 获取对象部分
                    object_node = function_node.child_by_field_name('argument')
                    if object_node:
                        object_name = self._get_text(object_node)
                        # 对于已知的一些模式，简化处理
                        if object_name in ['Super', 'this']:
                            return method_name
                        # 处理UE常见的调用模式
                        elif object_name.endswith('Engine') or object_name.startswith('Editor'):
                            # 对于Engine相关的调用，返回方法名让简单匹配处理
                            return method_name
                        # 对于成员变量的方法调用，尝试进行类型推断
                        else:
                            # 尝试推断对象类型并构建qualified name
                            object_type = self._infer_object_type(object_node)
                            if object_type:
                                # 构建可能的qualified name
                                return f"{object_type}::{method_name}"
                            # 如果无法推断类型，返回简单方法名，让后续逻辑处理
                            return method_name
                    return method_name
            
            # 情况4: 模板函数调用 func<T>() 或 Cast<Type>()
            elif function_node.type == 'template_function':
                name_node = function_node.child_by_field_name('name')
                if name_node:
                    template_name = self._get_text(name_node)
                    # 对于UE常见的模板函数，直接返回函数名
                    if template_name in ['Cast', 'CastChecked', 'NewObject', 'CreateDefaultSubobject']:
                        return template_name
                    return template_name
            
            # 情况5: 带圆括号的表达式 (function)()
            elif function_node.type == 'parenthesized_expression':
                # 递归处理圆括号内的表达式
                inner_node = None
                for child in function_node.children:
                    if child.type != '(' and child.type != ')':
                        inner_node = child
                        break
                if inner_node:
                    return self._extract_function_name_from_node(inner_node)
                
            # 情况6: 下标表达式作为函数调用（罕见）
            elif function_node.type == 'subscript_expression':
                return None  # 暂不处理
            
            # 其他情况：尝试直接获取文本
            else:
                text = self._get_text(function_node)
                if text:
                    # 清理可能的多余字符
                    text = text.strip()
                    # 如果包含限定符，直接返回
                    if '::' in text:
                        return text
                    # 否则返回简单名称
                    elif text and text.isidentifier():
                        return text
            
            return None
        except Exception as e:
            self.logger.warning(f"从节点提取函数名失败: {e}")
            return None

    def _get_current_qualifier(self) -> str:
        """获取当前上下文的限定符前缀"""
        parts = []
        if self.current_namespace_stack:
            parts.extend(self.current_namespace_stack)
        if self.current_class_stack:
            parts.extend(self.current_class_stack)
        return "::".join(parts) + "::" if parts else ""

    def resolve_pending_calls(self) -> Dict[str, int]:
        """解析待解析的调用关系 - 第二轮补全"""
        if not self.pending_calls:
            return {"resolved": 0, "still_pending": 0}
        
        resolved_count = 0
        still_pending = []
        
        self.logger.info(f"开始第二轮调用解析，处理 {len(self.pending_calls)} 个待解析调用")
        
        for caller_usr, qualified_name, call_node, file_content in self.pending_calls:
            # 尝试多种解析策略
            resolved_usr = self._resolve_call_with_strategies(qualified_name, call_node, file_content)
            
            if resolved_usr:
                # 成功解析，建立调用关系
                self.repo.add_call_relationship(caller_usr, resolved_usr)
                resolved_count += 1
                self.logger.debug(f"成功解析调用: {qualified_name} -> {resolved_usr}")
            else:
                # 仍无法解析
                still_pending.append((caller_usr, qualified_name, call_node, file_content))
                self.logger.debug(f"仍无法解析调用: {qualified_name}")
        
        # 更新待解析队列
        self.pending_calls = still_pending
        
        result = {
            "resolved": resolved_count, 
            "still_pending": len(still_pending)
        }
        
        self.logger.info(f"第二轮调用解析完成: 成功解析 {resolved_count} 个，仍待解析 {len(still_pending)} 个")
        
        return result

    def _resolve_call_with_strategies(self, qualified_name: str, call_node: Node, file_content: bytes) -> Optional[str]:
        """使用多种策略尝试解析调用"""
        # 策略1: 直接qualified_name查找
        resolved_usr = self.repo.resolve_function_call(qualified_name, "", "")
        if resolved_usr:
            return resolved_usr
        
        # 策略2: 构建可能的限定名列表
        possible_names = self._build_possible_qualified_names(qualified_name.split("::")[-1])
        for name in possible_names:
            resolved_usr = self.repo.resolve_function_call(name, "", "")
            if resolved_usr:
                return resolved_usr
        
        # 策略3: 模糊匹配（部分名称匹配）
        function_name = qualified_name.split("::")[-1]
        candidates = []
        with self.repo._lock.read_lock():
            for usr, entity in self.repo.nodes.items():
                if (isinstance(entity, Function) and 
                    entity.name == function_name and 
                    entity.is_definition):
                    candidates.append(entity)
        
        if len(candidates) == 1:
            return candidates[0].usr
        elif len(candidates) > 1:
            # 多个候选，使用重载决议
            best_match = self._resolve_overloaded_call(call_node, candidates)
            return best_match.usr if best_match else None
        
        # 策略4: 基于类型推断的成员函数解析
        if call_node.type == 'field_expression':
            return self._resolve_member_call_by_type_inference(call_node, file_content)
        
        return None

    def _resolve_member_call_by_type_inference(self, field_node: Node, file_content: bytes) -> Optional[str]:
        """基于类型推断解析成员函数调用"""
        object_node = field_node.child_by_field_name('argument')
        field_name_node = field_node.child_by_field_name('field')
        
        if not object_node or not field_name_node:
            return None
        
        # 设置文件内容用于类型推断
        original_content = self.file_content
        self.file_content = file_content
        
        try:
            # 推断对象类型
            object_type_info = self.type_engine.infer_expression_type(object_node)
            if object_type_info:
                method_name = self._get_text(field_name_node)
                method_qualified_name = f"{object_type_info.type_name}::{method_name}"
                
                # 查找匹配的方法
                functions = self.repo.find_by_qualified_name(method_qualified_name, 'function')
                if functions:
                    # 优先返回定义
                    for func in functions:
                        if func.is_definition:
                            return func.usr
                    return functions[0].usr
        
        finally:
            # 恢复原文件内容
            self.file_content = original_content
        
        return None

    def get_pending_call_statistics(self) -> Dict[str, Any]:
        """获取待解析调用统计信息"""
        return {
            "total_calls_processed": self.resolved_calls_count + self.pending_calls_count,
            "resolved_calls": self.resolved_calls_count,
            "pending_calls": len(self.pending_calls),
            "resolution_rate": self.resolved_calls_count / max(1, self.resolved_calls_count + self.pending_calls_count),
            "pending_call_details": [
                {
                    "caller": caller_usr,
                    "qualified_name": qualified_name,
                    "call_type": call_node.type
                }
                for caller_usr, qualified_name, call_node, _ in self.pending_calls[:10]  # 只显示前10个
            ]
        } 

    def resolve_function_call(self, call_node: Node, function_usr_id: str) -> Optional[str]:
        """
        解析函数调用 - 增强版本，集成类型推断和重载决议
        
        Args:
            call_node: 函数调用节点  
            function_usr_id: 调用者函数的USR ID
            
        Returns:
            被调用函数的USR ID，如果无法解析则返回None
        """
        try:
            # 提取函数名
            function_name = self._extract_function_name_enhanced(call_node)
            if not function_name:
                return None
            
            # 提取参数类型
            argument_types = self._extract_argument_types(call_node)
            
            # 获取调用上下文
            current_namespace = "::".join(self.current_namespace_stack)
            current_class = "::".join(self.current_class_stack)
            
            # 使用增强类型推断引擎进行重载决议
            resolved_usr = self.type_engine.resolve_overloaded_function(
                function_name, 
                argument_types, 
                current_namespace
            )
            
            if resolved_usr:
                return resolved_usr
            
            # 如果增强算法失败，使用原有的简化算法作为备用
            return self._fallback_function_resolution(function_name, current_namespace, current_class)
            
        except Exception as e:
            self.logger.warning(f"解析函数调用失败: {e}")
            return None

    def _extract_function_name_enhanced(self, call_node: Node) -> Optional[str]:
        """增强版函数名提取，支持更多调用模式"""
        function_node = call_node.child_by_field_name('function')
        if not function_node:
            return None
        
        # 处理简单函数调用: func()
        if function_node.type == 'identifier':
            return self._get_text(function_node)
        
        # 处理成员函数调用: obj.method() 或 obj->method()
        elif function_node.type == 'field_expression':
            field_node = function_node.child_by_field_name('field')
            if field_node:
                method_name = self._get_text(field_node)
                object_node = function_node.child_by_field_name('argument')
                if object_node:
                    # 推断对象类型
                    object_expr = self._get_text(object_node)
                    object_type = self.type_engine.infer_expression_type(object_expr)
                    if object_type:
                        # 返回带类型限定的方法名
                        return f"{object_type}::{method_name}"
                return method_name
        
        # 处理限定函数调用: namespace::func() 或 Class::func()
        elif function_node.type == 'qualified_identifier':
            return self._get_text(function_node)
        
        # 处理模板函数调用: func<T>()
        elif function_node.type == 'template_function':
            name_node = function_node.child_by_field_name('name')
            if name_node:
                return self._get_text(name_node)
        
        # 处理函数指针调用: (*func_ptr)()
        elif function_node.type == 'parenthesized_expression':
            inner_node = function_node.children[1] if len(function_node.children) > 1 else None
            if inner_node and inner_node.type == 'pointer_expression':
                ptr_node = inner_node.child_by_field_name('argument')
                if ptr_node:
                    return self._get_text(ptr_node)
        
        return None

    def _extract_argument_types(self, call_node: Node) -> List[str]:
        """提取函数调用的参数类型列表"""
        argument_types = []
        
        arguments_node = call_node.child_by_field_name('arguments')
        if not arguments_node:
            return argument_types
        
        for arg_node in arguments_node.children:
            if arg_node.type != ',':  # 跳过逗号分隔符
                arg_expr = self._get_text(arg_node)
                arg_type = self.type_engine.infer_expression_type(arg_expr)
                if arg_type:
                    argument_types.append(arg_type)
                else:
                    # 如果无法推断类型，使用表达式模式匹配
                    fallback_type = self._infer_argument_type_fallback(arg_node)
                    argument_types.append(fallback_type or "unknown")
        
        return argument_types

    def _infer_argument_type_fallback(self, arg_node: Node) -> Optional[str]:
        """参数类型推断的备用方法 - 修复：改进字符串字面量类型推断"""
        if arg_node.type == 'number_literal':
            text = self._get_text(arg_node)
            if '.' in text or 'e' in text.lower():
                return "double" if not text.endswith('f') else "float"
            else:
                return "int"
        
        elif arg_node.type == 'string_literal':
            # 🔧 修复：字符串字面量应该优先匹配const char*，然后能隐式转换为std::string相关类型
            # 返回const char*作为基础类型，在类型兼容性检查中处理转换
            return "const char*"
        
        elif arg_node.type in ['true', 'false']:
            return "bool"
        
        elif arg_node.type == 'null':
            return "std::nullptr_t"
        
        elif arg_node.type == 'character_literal':
            return "char"
        
        elif arg_node.type == 'identifier':
            # 尝试从变量类型推断
            var_name = self._get_text(arg_node)
            return self._lookup_variable_type_simple(var_name)
        
        return None

    def _lookup_variable_type_simple(self, var_name: str) -> Optional[str]:
        """简化的变量类型查找"""
        # 检查函数参数
        current_function = self.repo.get_node(self.current_function_usr) if hasattr(self, 'current_function_usr') else None
        if isinstance(current_function, Function):
            for param in current_function.parameters:
                if param.get('name') == var_name:
                    return param.get('type')
        
        # 检查全局变量
        variables = self.repo.find_by_qualified_name(var_name, 'variable')
        if variables:
            return variables[0].var_type
        
        return None

    def _fallback_function_resolution(self, function_name: str, current_namespace: str, current_class: str) -> Optional[str]:
        """备用函数解析算法"""
        # 构建可能的函数限定名
        possible_names = self._build_possible_qualified_names(function_name)
        
        # 按优先级查找函数
        for qualified_name in possible_names:
            functions = self.repo.find_by_qualified_name(qualified_name, 'function')
            if functions:
                # 优先选择定义而不是声明
                for func in functions:
                    if func.is_definition:
                        return func.usr
                # 如果没有定义，返回第一个声明
                return functions[0].usr
        
        return None


    def _create_call_info_enhanced(self, call_node: Node, called_usr_id: str, caller_usr_id: str):
        """创建增强的调用信息对象"""
        from .data_structures import CallInfo, CppCallInfo, ResolvedDefinitionLocation
        
        # 获取调用位置
        line = call_node.start_point[0] + 1
        column = call_node.start_point[1] + 1
        
        # 分析调用类型
        call_type = self._determine_call_type(call_node)
        
        # 创建C++调用信息
        cpp_call_info = CppCallInfo()
        
        # 分析调用特征
        function_node = call_node.child_by_field_name('function')
        if function_node:
            if function_node.type == 'field_expression':
                cpp_call_info.call_type = "method_call"
                # 提取调用对象
                object_node = function_node.child_by_field_name('argument')
                if object_node:
                    cpp_call_info.calling_object = self._get_text(object_node)
            elif function_node.type == 'qualified_identifier':
                cpp_call_info.call_type = "static_call"
            elif function_node.type == 'template_function':
                cpp_call_info.call_type = "template_call"
                # 提取模板参数
                template_args = self._extract_template_arguments(function_node)
                cpp_call_info.template_args = template_args
        
        # 提取参数类型
        cpp_call_info.argument_types = self._extract_argument_types(call_node)
        
        # 解析到的重载函数USR
        cpp_call_info.resolved_overload = called_usr_id
        
        # 解析定义位置
        called_function = self.repo.get_node(called_usr_id)
        if isinstance(called_function, Function) and called_function.definition_location:
            cpp_call_info.resolved_definition_location = ResolvedDefinitionLocation(
                file_id=called_function.definition_location.file_id,
                line=called_function.definition_location.line,
                column=called_function.definition_location.column
            )
        
        return CallInfo(
            to_usr_id=called_usr_id,
            line=line,
            column=column,
            type=call_type,
            resolved_definition_file_id=cpp_call_info.resolved_definition_location.file_id if cpp_call_info.resolved_definition_location else None,
            cpp_call_info=cpp_call_info
        )

    def _determine_call_type(self, call_node: Node) -> str:
        """确定调用类型"""
        function_node = call_node.child_by_field_name('function')
        if not function_node:
            return "unknown"
        
        if function_node.type == 'field_expression':
            return "member_call"
        elif function_node.type == 'qualified_identifier':
            return "qualified_call"
        elif function_node.type == 'template_function':
            return "template_call"
        elif function_node.type == 'identifier':
            return "direct_call"
        else:
            return "other"

    def _extract_template_arguments(self, template_node: Node) -> List[str]:
        """提取模板参数"""
        template_args = []
        
        args_node = template_node.child_by_field_name('arguments')
        if args_node:
            for arg_node in args_node.children:
                if arg_node.type != ',':  # 跳过逗号
                    arg_text = self._get_text(arg_node)
                    template_args.append(arg_text)
        
        return template_args 
    def _infer_object_type(self, object_node: Node) -> Optional[str]:
        """推断对象的类型，返回类型名称"""
        try:
            if not object_node:
                return None
            
            object_text = self._get_text(object_node)
            if not object_text:
                return None
            
            # 使用现有的TypeInferenceEngine进行类型推断
            inferred_type = self.type_engine.infer_expression_type(
                object_text, 
                self.current_function_usr
            )
            
            if inferred_type:
                # 清理类型字符串，移除指针标记和其他修饰符
                clean_type = self._clean_type_string(inferred_type)
                self.logger.debug(f"类型推断成功: {object_text} -> {clean_type}")
                return clean_type
                            
            self.logger.debug(f"无法推断对象类型: {object_text}")
            return None
            
        except Exception as e:
            self.logger.warning(f"推断对象类型时出错: {e}")
            return None
    
    def _clean_type_string(self, type_string: str) -> str:
        """清理类型字符串，移除指针、引用等修饰符"""
        if not type_string:
            return ""
        
        # 移除常见的类型修饰符
        cleaned = type_string.strip()
        cleaned = cleaned.replace('*', '').replace('&', '').replace('const', '').strip()
        
        # 移除模板参数（简化处理）
        if '<' in cleaned and '>' in cleaned:
            template_start = cleaned.find('<')
            cleaned = cleaned[:template_start]
        
        # 移除命名空间前缀（如果需要的话）
        if '::' in cleaned:
            parts = cleaned.split('::')
            cleaned = parts[-1]  # 取最后一部分作为类名
        
        return cleaned.strip()
    
