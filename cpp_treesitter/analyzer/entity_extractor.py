"""
Tree-sitter Entity Extractor Module

从tree-sitter AST中提取C++实体（函数、类、变量、命名空间、模板等）
并转换为结构化数据，匹配JSON格式规范。
支持函数体文本提取功能、USR ID生成、全局节点注册、声明/定义分离和调用关系分析。

增强功能：
1. USR ID生成系统
2. 全局节点注册表
3. 改进的类方法提取
4. 调用关系分析
5. 声明/定义分离处理
6. 完整的函数体代码提取

符合json_format.md规范的增强实现。
"""
# type: ignore
from tree_sitter import Node
from typing import List, Optional, Dict, Tuple
import re

from .logger import Logger
from .data_structures import (
    Function, Class, Namespace, Enum, Variable,
    NodeRepository
)
from .call_relationship_analyzer import CallRelationshipAnalyzer


class EntityExtractor:
    def __init__(self, file_path: str, file_content: str, repo: NodeRepository):
        """
        初始化实体提取器。
        
        :param file_path: 正在分析的文件路径。
        :param file_content: 文件内容。
        :param repo: 全局节点存储库。
        """
        self.file_path = file_path
        self.file_content = file_content.encode('utf-8') # 确保内容为bytes
        self.lines = file_content.split('\n')
        self.repo = repo
        self.call_analyzer = CallRelationshipAnalyzer(repo)
        self.current_namespace_stack: List[str] = []
        self.current_class_stack: List[str] = []
        self.logger = Logger.get_logger()
        
        # 临时存储用于两阶段处理
        self.pending_function_calls: Dict[str, List[Tuple[Node, str]]] = {}  # function_usr -> [(call_node, caller_usr)]


    def _get_text(self, node: Node) -> str:
        """获取节点的文本内容"""
        return self.file_content[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')

    def _generate_usr(self, entity_type: str, qualified_name: str, signature: Optional[str] = None) -> str:
        """生成USR ID（委托给NodeRepository）"""
        return self.repo.generate_usr(entity_type, qualified_name, signature, self.file_path)

    def _get_current_scope_qualifier(self) -> str:
        """获取当前作用域的限定符前缀"""
        qualifier_parts = []
        if self.current_namespace_stack:
            qualifier_parts.extend(self.current_namespace_stack)
        if self.current_class_stack:
            qualifier_parts.extend(self.current_class_stack)
        return "::".join(qualifier_parts) + "::" if qualifier_parts else ""

    def phase_one_collect_declarations(self, root_node: Node):
        """第一阶段：收集所有声明"""
        self.logger.info(f"Phase 1: Collecting declarations from {self.file_path}")
        self.current_namespace_stack.clear()
        self.current_class_stack.clear()
        self._traverse_for_declarations(root_node)

    def phase_two_process_definitions(self, root_node: Node):
        """第二阶段：处理定义并完善内容"""
        self.logger.info(f"Phase 2: Processing definitions and call relationships from {self.file_path}")
        self.current_namespace_stack.clear()
        self.current_class_stack.clear()
        self._traverse_for_definitions(root_node)

    def _traverse_for_declarations(self, node: Node):
        """第一阶段遍历：只收集声明"""
        node_stack = [(node, [])]  # (node, namespace_stack)
        
        while node_stack:
            current_node, ns_stack = node_stack.pop()
            self.current_namespace_stack = ns_stack.copy()
            
            # 更新调用分析器的上下文
            self.call_analyzer.set_context(self.current_namespace_stack, self.current_class_stack)
            
            if current_node.type == 'namespace_definition':
                self._extract_namespace_declaration(current_node)
            elif current_node.type in ['class_specifier', 'struct_specifier']:
                self._extract_class_declaration(current_node)
            elif current_node.type == 'function_definition':
                self._extract_function_declaration(current_node, is_definition=True)
            elif current_node.type == 'declaration':
                self._extract_declaration_node(current_node)
            elif current_node.type == 'enum_specifier':
                self._extract_enum_declaration(current_node)
            
            # 将子节点逆序入栈，传播命名空间上下文
            if hasattr(current_node, 'children'):
                for child in reversed(current_node.children):
                    child_ns_stack = ns_stack.copy()
                    if current_node.type == 'namespace_definition':
                        name_node = current_node.child_by_field_name('name')
                        if name_node:
                            child_ns_stack.append(self._get_text(name_node))
                    node_stack.append((child, child_ns_stack))

    def _traverse_for_definitions(self, node: Node):
        """第二阶段遍历：处理定义和调用关系"""
        node_stack = [(node, [], [])]  # (node, namespace_stack, class_stack)
        
        while node_stack:
            current_node, ns_stack, class_stack = node_stack.pop()
            self.current_namespace_stack = ns_stack.copy()
            self.current_class_stack = class_stack.copy()
            
            # 更新调用分析器的上下文
            self.call_analyzer.set_context(self.current_namespace_stack, self.current_class_stack)
            
            if current_node.type == 'function_definition':
                self._process_function_definition(current_node)
            elif current_node.type in ['class_specifier', 'struct_specifier']:
                self._process_class_definition(current_node)
            
            # 将子节点逆序入栈，传播上下文
            if hasattr(current_node, 'children'):
                for child in reversed(current_node.children):
                    child_ns_stack = ns_stack.copy()
                    child_class_stack = class_stack.copy()
                    
                    if current_node.type == 'namespace_definition':
                        name_node = current_node.child_by_field_name('name')
                        if name_node:
                            child_ns_stack.append(self._get_text(name_node))
                    elif current_node.type in ['class_specifier', 'struct_specifier']:
                        name_node = current_node.child_by_field_name('name')
                        if name_node:
                            child_class_stack.append(self._get_text(name_node))
                    
                    node_stack.append((child, child_ns_stack, child_class_stack))

    def _extract_namespace_declaration(self, node: Node):
        """提取命名空间声明"""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return  # 匿名命名空间在第一阶段跳过
        
        name = self._get_text(name_node)
        qualified_name = "::".join(self.current_namespace_stack + [name])
        usr = self._generate_usr('namespace', qualified_name)
        
        namespace = Namespace(
            usr=usr,
            name=name,
            qualified_name=qualified_name,
            file_path=self.file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            is_definition=True
        )
        
        self.repo.register_entity(namespace)

    def _extract_class_declaration(self, node: Node):
        """提取类声明"""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return

        name = self._get_text(name_node)
        qualifier = self._get_current_scope_qualifier()
        qualified_name = f"{qualifier}{name}"
        usr = self._generate_usr('class', qualified_name)

        has_body = node.child_by_field_name('body') is not None
        
        class_obj = Class(
            usr=usr,
            name=name,
            qualified_name=qualified_name,
            file_path=self.file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            is_struct=node.type == 'struct_specifier',
            is_definition=has_body,
            is_declaration=not has_body
        )

        # 提取基类信息
        base_clause = node.child_by_field_name('base_clause')
        if base_clause:
            class_obj.base_classes = self._extract_base_classes(base_clause)
        
        self.repo.register_entity(class_obj)

    def _extract_function_declaration(self, node: Node, is_definition: bool = False):
        """提取函数声明"""
        declarator_node = node.child_by_field_name('declarator')
        if not declarator_node:
            return

        name = self._extract_function_name(declarator_node, node)
        if not name:
            return
            
        qualifier = self._get_current_scope_qualifier()
        qualified_name = f"{qualifier}{name}"
        
        type_node = node.child_by_field_name('type')
        return_type = self._get_text(type_node) if type_node else "void"

        parameters, signature = self._extract_parameters_and_signature(declarator_node, name)
        usr = self._generate_usr('function', qualified_name, signature)
        
        # 检查是否已存在
        existing = self.repo.get_node(usr)
        if existing and isinstance(existing, Function):
            # 如果是定义且现有的是声明，则更新
            if is_definition and not existing.is_definition:
                existing.is_definition = True
                body_node = node.child_by_field_name('body')
                if body_node:
                    existing.code_content = self._extract_function_body(body_node)
            return existing
        
        function_obj = Function(
            usr=usr,
            name=name,
            qualified_name=qualified_name,
            file_path=self.file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            return_type=return_type,
            parameters=parameters,
            signature=signature,
            is_definition=is_definition,
            is_declaration=not is_definition
        )

        if is_definition:
            body_node = node.child_by_field_name('body')
            if body_node:
                function_obj.code_content = self._extract_function_body(body_node)

        self.repo.register_entity(function_obj)
        return function_obj

    def _process_function_definition(self, node: Node):
        """第二阶段：处理函数定义和调用关系"""
        declarator_node = node.child_by_field_name('declarator')
        if not declarator_node:
            return

        name = self._extract_function_name(declarator_node, node)
        if not name:
            return
            
        qualifier = self._get_current_scope_qualifier()
        qualified_name = f"{qualifier}{name}"
        
        parameters, signature = self._extract_parameters_and_signature(declarator_node, name)
        usr = self._generate_usr('function', qualified_name, signature)
        
        # 获取已注册的函数
        function_obj = self.repo.get_node(usr)
        if not function_obj or not isinstance(function_obj, Function):
            # 如果第一阶段没有注册，现在注册
            function_obj = self._extract_function_declaration(node, is_definition=True)
        
        if function_obj:
            # 分析函数调用关系
            body_node = node.child_by_field_name('body')
            if body_node:
                calls_to = self.call_analyzer.analyze_function_calls(node, usr, self.file_content)
                function_obj.calls_to = calls_to

    def _process_class_definition(self, node: Node):
        """第二阶段：处理类定义和方法"""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return

        name = self._get_text(name_node)
        qualifier = self._get_current_scope_qualifier()
        qualified_name = f"{qualifier}{name}"
        usr = self._generate_usr('class', qualified_name)
        
        class_obj = self.repo.get_node(usr)
        if not class_obj or not isinstance(class_obj, Class):
            return
        
        # 处理类体中的方法
        body_node = node.child_by_field_name('body')
        if body_node:
            self.current_class_stack.append(name)
            methods = []
            
            for child in body_node.children:
                if child.type == 'function_definition':
                    method = self._process_method_definition(child, usr)
                    if method:
                        methods.append(method.usr)
                elif child.type == 'declaration':
                    # 处理方法声明
                    self._extract_declaration_node(child)
            
            class_obj.methods = methods
            self.current_class_stack.pop()

    def _process_method_definition(self, node: Node, parent_class_usr: str):
        """处理类方法定义"""
        declarator_node = node.child_by_field_name('declarator')
        if not declarator_node:
            return None

        name = self._extract_function_name(declarator_node, node)
        if not name:
            return None
            
        qualifier = self._get_current_scope_qualifier()
        qualified_name = f"{qualifier}{name}"
        
        type_node = node.child_by_field_name('type')
        return_type = self._get_text(type_node) if type_node else "void"

        parameters, signature = self._extract_parameters_and_signature(declarator_node, name)
        usr = self._generate_usr('function', qualified_name, signature)
        
        # 检查是否已存在
        existing = self.repo.get_node(usr)
        if existing and isinstance(existing, Function):
            method = existing
            # 更新定义信息
            method.is_definition = True
            body_node = node.child_by_field_name('body')
            if body_node:
                method.code_content = self._extract_function_body(body_node)
        else:
            # 创建新方法
            method = Function(
                usr=usr,
                name=name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                return_type=return_type,
                parameters=parameters,
                signature=signature,
                is_definition=True,
                parent_class=parent_class_usr
            )
            
            body_node = node.child_by_field_name('body')
            if body_node:
                method.code_content = self._extract_function_body(body_node)
            
            self.repo.register_entity(method)
        
        # 分析方法调用关系
        if method.code_content:
            calls_to = self.call_analyzer.analyze_function_calls(node, usr, self.file_content)
            method.calls_to = calls_to
        
        return method

    def _extract_function_body(self, body_node: Node) -> str:
        """提取函数体代码内容"""
        if not body_node:
            return ""
        
        # 获取原始代码文本
        code_content = self._get_text(body_node)
        
        # 标准化格式：去除多余空白，保持缩进
        lines = code_content.split('\n')
        normalized_lines = []
        for line in lines:
            stripped = line.rstrip()
            if stripped:  # 保留非空行
                normalized_lines.append(stripped)
        
        return '\n'.join(normalized_lines)

    def _extract_function_name(self, declarator_node: Node, function_node: Node) -> str:
        """提取函数名称"""
        name = ""
        name_node = declarator_node
        
        while name_node:
            if name_node.type == 'identifier':
                name = self._get_text(name_node)
                break
            elif name_node.type == 'operator_name':
                name = self._get_text(name_node)
                break
            # 嵌套的 declarator
            temp_name_node = name_node.child_by_field_name('declarator')
            if not temp_name_node:
                break
            name_node = temp_name_node
        
        if not name:
            # 处理构造函数等特殊情况
            type_node = function_node.child_by_field_name('type')
            if type_node and self.current_class_stack and self._get_text(type_node) == self.current_class_stack[-1]:
                name = self._get_text(type_node)
        
        return name

    def _extract_parameters_and_signature(self, declarator_node: Node, func_name: str) -> Tuple[List[Dict[str, str]], str]:
        """提取参数和签名"""
        params = []
        param_types = []
        params_node = declarator_node.child_by_field_name('parameters')
        
        if params_node:
            for param in params_node.children:
                if param.type == 'parameter_declaration':
                    type_node = param.child_by_field_name('type')
                    param_type = self._get_text(type_node) if type_node else "void"
                    
                    name = ""
                    decl_node = param.child_by_field_name('declarator')
                    if decl_node:
                        name = self._get_text(decl_node)

                    params.append({"name": name, "type": param_type})
                    param_types.append(param_type)
        
        signature = f"{func_name}({', '.join(param_types)})"
        return params, signature

    def _extract_base_classes(self, base_clause: Node) -> List[str]:
        """提取基类列表"""
        base_classes = []
        for child in base_clause.children:
            if child.type == 'type_identifier':
                base_classes.append(self._get_text(child))
        return base_classes

    def _extract_declaration_node(self, node: Node):
        """提取声明节点（可能是函数声明或变量声明）"""
        # 函数声明
        func_decl_nodes = self._find_nodes_by_type(node, 'function_declarator')
        if func_decl_nodes:
            self._extract_function_declaration_from_decl(node, func_decl_nodes[0])
            return
        
        # 变量声明等其他处理...

    def _extract_function_declaration_from_decl(self, decl_node: Node, declarator: Node):
        """从声明节点提取函数声明"""
        name = ""
        name_node = declarator.child_by_field_name('declarator')
        if name_node:
            name = self._get_text(name_node)
        
        if not name:
            return

        type_node = decl_node.child_by_field_name('type')
        return_type = self._get_text(type_node) if type_node else "void"
        
        params, signature = self._extract_parameters_and_signature(declarator, name)

        qualifier = self._get_current_scope_qualifier()
        qualified_name = f"{qualifier}{name}"
        usr = self._generate_usr('function', qualified_name, signature)

        function_decl = Function(
            usr=usr,
            name=name,
            qualified_name=qualified_name,
            file_path=self.file_path,
            start_line=decl_node.start_point[0] + 1,
            end_line=decl_node.end_point[0] + 1,
            return_type=return_type,
            parameters=params,
            signature=signature,
            is_definition=False,
            is_declaration=True
        )
        
        self.repo.register_entity(function_decl)

    def _extract_enum_declaration(self, node: Node):
        """提取枚举声明"""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return
        
        name = self._get_text(name_node)
        qualifier = self._get_current_scope_qualifier()
        qualified_name = f"{qualifier}{name}"
        usr = self._generate_usr('enum', qualified_name)
        
        enum_obj = Enum(
            usr=usr,
            name=name,
            qualified_name=qualified_name,
            file_path=self.file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            is_definition=True
        )
        
        self.repo.register_entity(enum_obj)

    def _find_nodes_by_type(self, node: Node, type_name: str) -> List[Node]:
        """递归查找指定类型的所有节点"""
        nodes = []
        queue = [node]
        while queue:
            current_node = queue.pop(0)
            if current_node.type == type_name:
                nodes.append(current_node)
            if hasattr(current_node, 'children'):
                queue.extend(current_node.children)
        return nodes

    # 保留原有的traverse方法作为向后兼容
    def traverse(self, node: Node):
        """遍历AST节点以提取实体（向后兼容）"""
        self.phase_one_collect_declarations(node)
        self.phase_two_process_definitions(node) 