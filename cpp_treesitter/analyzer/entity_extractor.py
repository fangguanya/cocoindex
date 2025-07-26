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
    def __init__(self, file_path: str, file_content: str, repo: NodeRepository, file_id: str = None):
        """
        初始化实体提取器。
        
        :param file_path: 正在分析的文件路径。
        :param file_content: 文件内容。
        :param repo: 全局节点存储库。
        :param file_id: 文件ID（符合v2.3规范）。
        """
        self.file_path = file_path
        self.file_content = file_content.encode('utf-8') # 确保内容为bytes
        self.lines = file_content.split('\n')
        self.repo = repo
        self.call_analyzer = CallRelationshipAnalyzer(repo)
        self.current_namespace_stack: List[str] = []
        self.current_class_stack: List[str] = []
        self.logger = Logger.get_logger()
        self.file_id = file_id or "f001"  # 默认文件ID
        
        # 临时存储用于两阶段处理
        self.pending_function_calls: Dict[str, List[Tuple[Node, str]]] = {}  # function_usr -> [(call_node, caller_usr)]


    def _get_text(self, node: Node) -> str:
        """获取节点的文本内容"""
        return self.file_content[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')

    def _generate_usr(self, entity_type: str, qualified_name: str, signature: Optional[str] = None, template_params: List[str] = None) -> str:
        """生成USR ID（委托给NodeRepository）"""
        return self.repo.generate_usr(entity_type, qualified_name, signature, self.file_path, template_params)

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
        
        # 修复：检测类外成员函数定义（包含"::"的情况）
        is_out_of_class_method = False
        parent_class_usr = None
        
        if "::" in qualified_name and not self.current_class_stack:
            # 这是一个类外成员函数定义，如 A::foo()
            parts = qualified_name.split("::")
            if len(parts) >= 2:
                # 获取所属类的名称和USR
                class_parts = parts[:-1]  # 除了最后一个函数名
                class_qualified_name = "::".join(class_parts)
                
                # 尝试找到对应的类
                parent_class_usr = self._generate_usr('class', class_qualified_name)
                parent_class = self.repo.get_node(parent_class_usr)
                
                if parent_class and isinstance(parent_class, Class):
                    is_out_of_class_method = True
                    self.logger.info(f"发现类外成员函数定义: {qualified_name} -> 类 {class_qualified_name}")
        
        parameters, signature = self._extract_parameters_and_signature(declarator_node, name)
        usr = self._generate_usr('function', qualified_name, signature)
        
        # 获取已注册的函数
        function_obj = self.repo.get_node(usr)
        if not function_obj or not isinstance(function_obj, Function):
            # 如果第一阶段没有注册，现在注册
            function_obj = self._extract_function_declaration(node, is_definition=True)
        
        if function_obj:
            # 修复：如果是类外成员函数，设置parent_class并添加到类的methods列表
            if is_out_of_class_method and parent_class_usr:
                function_obj.parent_class = parent_class_usr
                
                # 将此函数添加到类的methods列表
                parent_class = self.repo.get_node(parent_class_usr)
                if parent_class and isinstance(parent_class, Class):
                    if function_obj.usr not in parent_class.methods:
                        parent_class.methods.append(function_obj.usr)
                        self.logger.info(f"已将函数 {qualified_name} 添加到类 {parent_class.qualified_name} 的methods列表")
            
            # 修复：通过CallRelationshipAnalyzer分析调用关系，确保双向关系正确建立
            body_node = node.child_by_field_name('body')
            if body_node:
                # 使用调用分析器分析函数调用，它会自动建立双向关系
                calls_to = self.call_analyzer.analyze_function_calls(node, usr, self.file_content)
                # 注意：calls_to和called_by已经在analyze_function_calls过程中通过add_call_relationship建立
                # 这里只需要确保function_obj的calls_to列表是最新的
                function_obj.calls_to = calls_to
                
                # 设置函数体内容
                if not function_obj.code_content:
                    function_obj.code_content = self._extract_function_body(body_node)
                
                # 确保标记为定义
                function_obj.is_definition = True

    def _process_class_definition(self, node: Node):
        """第二阶段：处理类定义和方法 - 增强版"""
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
        
        # 处理类体中的成员
        body_node = node.child_by_field_name('body')
        if body_node:
            self.current_class_stack.append(name)
            
            # 分析类成员
            methods, fields = self._analyze_class_members(body_node, usr)
            
            # 更新类信息
            class_obj.methods = methods
            class_obj.fields = fields
            
            # 分析访问修饰符和特殊属性
            self._analyze_class_access_modifiers(body_node, class_obj)
            
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
        """提取并格式化函数体代码内容 - 增强版"""
        if not body_node:
            return ""
        
        # 获取原始代码文本，保持所有格式
        code_content = self._get_text(body_node)
        lines = code_content.split('\n')
        
        # 过滤掉空行来计算缩进
        non_empty_lines = [line for line in lines if line.strip()]
        if not non_empty_lines:
            return ""
        
        # 计算最小缩进（忽略空行）
        min_indent = min(len(line) - len(line.lstrip()) for line in non_empty_lines)
        
        # 统一左移最小缩进，标准化格式
        formatted_lines = []
        for line in lines:
            if line.strip():  # 非空行处理
                # 左移最小缩进
                if len(line) >= min_indent:
                    clean_line = line[min_indent:]
                else:
                    clean_line = line.lstrip()
                
                # 移除行尾空格
                clean_line = clean_line.rstrip()
                formatted_lines.append(clean_line)
            else:
                # 保留空行但移除多余空格
                formatted_lines.append("")
        
        # 统一LF换行符
        result = '\n'.join(formatted_lines)
        
        # 大函数截断机制
        MAX_LINES = 500
        MAX_SIZE = 100 * 1024  # 100KB
        
        if len(formatted_lines) > MAX_LINES or len(result.encode('utf-8')) > MAX_SIZE:
            truncated_lines = formatted_lines[:MAX_LINES]
            
            # 确保截断在语法合理的地方
            truncated_result = '\n'.join(truncated_lines)
            
            # 添加截断标记
            truncated_result += '\n\n// ... [TRUNCATED: Function body too large'
            if len(formatted_lines) > MAX_LINES:
                truncated_result += f' - {len(formatted_lines)} lines, showing first {MAX_LINES}]'
            else:
                truncated_result += f' - {len(result.encode("utf-8"))} bytes, showing first {MAX_SIZE} bytes]'
            
            # 记录截断信息到日志
            self.logger.warning(f"函数体过大被截断: 原始 {len(formatted_lines)} 行, "
                              f"{len(result.encode('utf-8'))} 字节")
            
            return truncated_result
        
        # 清理结果：移除文件开头/结尾的多余空行
        result_lines = result.split('\n')
        
        # 移除开头的空行
        while result_lines and not result_lines[0].strip():
            result_lines.pop(0)
        
        # 移除结尾多余的空行（保留最后一个空行，如果有的话）
        trailing_empty_count = 0
        for line in reversed(result_lines):
            if not line.strip():
                trailing_empty_count += 1
            else:
                break
        
        # 如果末尾有超过2个空行，只保留1个
        if trailing_empty_count > 2:
            result_lines = result_lines[:-trailing_empty_count] + ['']
        
        return '\n'.join(result_lines)

    def _extract_function_name(self, declarator_node: Node, function_node: Node) -> str:
        """提取函数名称 - 增强版：支持析构函数、运算符重载等特殊函数名"""
        name = ""
        name_node = declarator_node
        
        while name_node:
            if name_node.type == 'identifier':
                name = self._get_text(name_node)
                break
            elif name_node.type == 'destructor_name':
                # 析构函数：~ClassName
                name = self._get_text(name_node)
                break
            elif name_node.type == 'operator_name':
                # 运算符重载：operator+, operator[], operator()等
                name = self._get_text(name_node)
                break
            elif name_node.type == 'operator_cast':
                # 转换运算符：operator Type()
                name = self._get_text(name_node)
                break
            elif name_node.type == 'qualified_identifier':
                # 限定标识符：namespace::function
                # 获取最后一部分作为函数名
                for child in reversed(name_node.children):
                    if child.type in ['identifier', 'destructor_name', 'operator_name']:
                        name = self._get_text(child)
                        break
                if name:
                    break
            # 嵌套的 declarator
            temp_name_node = name_node.child_by_field_name('declarator')
            if not temp_name_node:
                # 尝试查找其他可能的子节点
                for child in name_node.children:
                    if child.type in ['identifier', 'destructor_name', 'operator_name', 'operator_cast', 'qualified_identifier']:
                        name_node = child
                        break
                else:
                    break
            else:
                name_node = temp_name_node
        
        if not name:
            # 处理构造函数等特殊情况
            type_node = function_node.child_by_field_name('type')
            if type_node and self.current_class_stack:
                type_text = self._get_text(type_node)
                current_class = self.current_class_stack[-1]
                # 检查是否是构造函数（类型与当前类名相同）
                if type_text == current_class or type_text.endswith(f"::{current_class}"):
                    name = current_class
                elif type_text == "void" and not name:
                    # 可能是析构函数但没有正确识别
                    # 尝试从declarator中找到析构符号
                    declarator_text = self._get_text(declarator_node)
                    if "~" in declarator_text:
                        name = f"~{current_class}"
        
        # 最后的清理和验证
        if name:
            # 移除多余的空白字符
            name = name.strip()
            # 处理模板函数名（保留<>内容）
            if '<' in name and '>' in name:
                # 保持模板参数完整
                pass
            # 处理运算符函数名标准化
            if name.startswith('operator'):
                # 标准化运算符名称
                operator_map = {
                    'operator +': 'operator+',
                    'operator -': 'operator-',
                    'operator *': 'operator*',
                    'operator /': 'operator/',
                    'operator =': 'operator=',
                    'operator ==': 'operator==',
                    'operator !=': 'operator!=',
                    'operator <': 'operator<',
                    'operator >': 'operator>',
                    'operator <=': 'operator<=',
                    'operator >=': 'operator>=',
                    'operator ++': 'operator++',
                    'operator --': 'operator--',
                    'operator []': 'operator[]',
                    'operator ()': 'operator()',
                    'operator ->': 'operator->',
                    'operator <<': 'operator<<',
                    'operator >>': 'operator>>',
                }
                
                for spaced_op, clean_op in operator_map.items():
                    if name == spaced_op:
                        name = clean_op
                        break
        
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

    def _analyze_class_members(self, body_node: Node, class_usr: str) -> Tuple[List[str], List[str]]:
        """分析类成员，返回方法和字段的USR列表"""
        methods = []
        fields = []
        current_access = "private"  # 类默认为private
        
        for child in body_node.children:
            if child.type == 'access_specifier':
                # 更新访问修饰符
                access_text = self._get_text(child)
                if 'public' in access_text:
                    current_access = "public"
                elif 'protected' in access_text:
                    current_access = "protected"
                elif 'private' in access_text:
                    current_access = "private"
            
            elif child.type == 'function_definition':
                # 成员函数定义
                method = self._process_method_definition_enhanced(child, class_usr, current_access)
                if method:
                    methods.append(method.usr)
            
            elif child.type == 'declaration':
                # 可能是方法声明或成员变量声明
                member_usrs = self._process_member_declaration(child, class_usr, current_access)
                for usr, member_type in member_usrs:
                    if member_type == 'function':
                        methods.append(usr)
                    elif member_type == 'variable':
                        fields.append(usr)
            
            elif child.type in ['constructor_definition', 'destructor_definition']:
                # 构造函数和析构函数
                special_method = self._process_special_method(child, class_usr, current_access)
                if special_method:
                    methods.append(special_method.usr)
        
        return methods, fields

    def _process_method_definition_enhanced(self, node: Node, parent_class_usr: str, access_specifier: str = "private"):
        """处理类方法定义 - 增强版"""
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
        
        # 分析函数修饰符
        modifiers = self._extract_function_modifiers_enhanced(node, declarator_node)
        
        # 检查是否已存在
        existing = self.repo.get_node(usr)
        if existing and isinstance(existing, Function):
            method = existing
            # 更新定义信息
            method.is_definition = True
            method.access_specifier = access_specifier
            method.parent_class = parent_class_usr
            
            # 更新修饰符信息
            method.is_virtual = modifiers.get('is_virtual', False)
            method.is_pure_virtual = modifiers.get('is_pure_virtual', False)
            method.is_override = modifiers.get('is_override', False)
            method.is_final = modifiers.get('is_final', False)
            method.is_static = modifiers.get('is_static', False)
            method.is_const = modifiers.get('is_const', False)
            
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
                parent_class=parent_class_usr,
                access_specifier=access_specifier,
                is_virtual=modifiers.get('is_virtual', False),
                is_pure_virtual=modifiers.get('is_pure_virtual', False),
                is_override=modifiers.get('is_override', False),
                is_final=modifiers.get('is_final', False),
                is_static=modifiers.get('is_static', False),
                is_const=modifiers.get('is_const', False)
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

    def _process_member_declaration(self, decl_node: Node, class_usr: str, access_specifier: str) -> List[Tuple[str, str]]:
        """处理类成员声明，返回(usr, type)的列表"""
        members = []
        
        # 检查是否是函数声明
        if self._is_function_declaration(decl_node):
            function_usr = self._process_member_function_declaration(decl_node, class_usr, access_specifier)
            if function_usr:
                members.append((function_usr, 'function'))
        else:
            # 处理成员变量声明
            variable_usrs = self._process_member_variable_declaration(decl_node, class_usr, access_specifier)
            members.extend([(usr, 'variable') for usr in variable_usrs])
        
        return members

    def _process_member_function_declaration(self, decl_node: Node, class_usr: str, access_specifier: str) -> Optional[str]:
        """处理成员函数声明"""
        # 查找函数声明符
        func_declarator = None
        for child in decl_node.children:
            if child.type == 'function_declarator':
                func_declarator = child
                break
        
        if not func_declarator:
            return None
        
        # 提取函数信息
        name_node = func_declarator.child_by_field_name('declarator')
        if not name_node:
            return None
        
        name = self._get_text(name_node)
        qualifier = self._get_current_scope_qualifier()
        qualified_name = f"{qualifier}{name}"
        
        # 提取返回类型
        type_node = decl_node.child_by_field_name('type')
        return_type = self._get_text(type_node) if type_node else "void"
        
        # 提取参数和签名
        parameters, signature = self._extract_parameters_and_signature(func_declarator, name)
        usr = self._generate_usr('function', qualified_name, signature)
        
        # 分析修饰符
        modifiers = self._extract_function_modifiers_enhanced(decl_node, func_declarator)
        
        # 创建或更新函数实体
        existing = self.repo.get_node(usr)
        if existing and isinstance(existing, Function):
            # 更新声明信息
            existing.is_declaration = True
            existing.access_specifier = access_specifier
            existing.parent_class = class_usr
            
            # 更新修饰符
            existing.is_virtual = modifiers.get('is_virtual', False)
            existing.is_pure_virtual = modifiers.get('is_pure_virtual', False)
            existing.is_static = modifiers.get('is_static', False)
            existing.is_const = modifiers.get('is_const', False)
        else:
            # 创建新的函数声明
            function_decl = Function(
                usr=usr,
                name=name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=decl_node.start_point[0] + 1,
                end_line=decl_node.end_point[0] + 1,
                return_type=return_type,
                parameters=parameters,
                signature=signature,
                is_definition=False,
                is_declaration=True,
                parent_class=class_usr,
                access_specifier=access_specifier,
                is_virtual=modifiers.get('is_virtual', False),
                is_pure_virtual=modifiers.get('is_pure_virtual', False),
                is_static=modifiers.get('is_static', False),
                is_const=modifiers.get('is_const', False)
            )
            
            self.repo.register_entity(function_decl)
        
        return usr

    def _process_member_variable_declaration(self, decl_node: Node, class_usr: str, access_specifier: str) -> List[str]:
        """处理成员变量声明"""
        variables = []
        
        # 提取类型信息
        type_node = decl_node.child_by_field_name('type')
        var_type = self._get_text(type_node) if type_node else "unknown"
        
        # 分析修饰符
        is_static = 'static' in self._get_text(decl_node)
        is_const = 'const' in var_type
        is_mutable = 'mutable' in self._get_text(decl_node)
        
        # 查找所有声明符
        for child in decl_node.children:
            if child.type in ['init_declarator', 'declarator']:
                var_name = self._extract_variable_name_from_declarator(child)
                if var_name:
                    qualifier = self._get_current_scope_qualifier()
                    qualified_name = f"{qualifier}{var_name}"
                    usr = self._generate_usr('variable', qualified_name)
                    
                    # 创建变量实体
                    variable = Variable(
                        usr=usr,
                        name=var_name,
                        qualified_name=qualified_name,
                        file_path=self.file_path,
                        start_line=decl_node.start_point[0] + 1,
                        end_line=decl_node.end_point[0] + 1,
                        var_type=var_type,
                        is_const=is_const,
                        is_static=is_static,
                        access_specifier=access_specifier,
                        parent_class=class_usr
                    )
                    
                    # 添加mutable属性（如果需要的话）
                    if hasattr(variable, 'is_mutable'):
                        variable.is_mutable = is_mutable
                    
                    self.repo.register_entity(variable)
                    variables.append(usr)
        
        return variables

    def _process_special_method(self, node: Node, class_usr: str, access_specifier: str) -> Optional[Function]:
        """处理特殊成员函数（构造函数、析构函数）"""
        if node.type == 'constructor_definition':
            return self._process_constructor(node, class_usr, access_specifier)
        elif node.type == 'destructor_definition':
            return self._process_destructor(node, class_usr, access_specifier)
        return None

    def _analyze_class_access_modifiers(self, body_node: Node, class_obj: Class):
        """分析类的访问修饰符结构"""
        access_structure = {
            'public': [],
            'protected': [],
            'private': []
        }
        
        current_access = "private"  # 类默认private
        
        for child in body_node.children:
            if child.type == 'access_specifier':
                access_text = self._get_text(child)
                if 'public' in access_text:
                    current_access = "public"
                elif 'protected' in access_text:
                    current_access = "protected"
                elif 'private' in access_text:
                    current_access = "private"
            else:
                # 记录当前访问级别的成员
                if current_access not in access_structure:
                    access_structure[current_access] = []
                access_structure[current_access].append(child.type)
        
        # 将访问结构信息添加到类对象（如果支持的话）
        if hasattr(class_obj, 'access_structure'):
            class_obj.access_structure = access_structure

    def _extract_function_modifiers_enhanced(self, function_node: Node, declarator: Node) -> Dict[str, bool]:
        """提取函数修饰符 - 增强版"""
        modifiers = {
            'is_virtual': False,
            'is_pure_virtual': False,
            'is_override': False,
            'is_final': False,
            'is_static': False,
            'is_const': False,
            'is_inline': False,
            'is_constexpr': False,
            'is_noexcept': False
        }
        
        # 检查函数节点中的修饰符
        function_text = self._get_text(function_node)
        
        if 'virtual' in function_text:
            modifiers['is_virtual'] = True
        
        if '= 0' in function_text:
            modifiers['is_pure_virtual'] = True
        
        if 'override' in function_text:
            modifiers['is_override'] = True
        
        if 'final' in function_text:
            modifiers['is_final'] = True
        
        if 'static' in function_text:
            modifiers['is_static'] = True
        
        if 'inline' in function_text:
            modifiers['is_inline'] = True
        
        if 'constexpr' in function_text:
            modifiers['is_constexpr'] = True
        
        if 'noexcept' in function_text:
            modifiers['is_noexcept'] = True
        
        # 检查const成员函数
        if declarator:
            declarator_text = self._get_text(declarator)
            if declarator_text.endswith('const') or ') const' in declarator_text:
                modifiers['is_const'] = True
        
        return modifiers

    def _is_function_declaration(self, decl_node: Node) -> bool:
        """检查声明节点是否是函数声明"""
        for child in decl_node.children:
            if child.type == 'function_declarator':
                return True
        return False

    def _extract_variable_name_from_declarator(self, declarator_node: Node) -> Optional[str]:
        """从声明符中提取变量名"""
        if declarator_node.type == 'init_declarator':
            # 初始化声明符
            decl_child = declarator_node.child_by_field_name('declarator')
            if decl_child:
                return self._get_text(decl_child)
        elif declarator_node.type == 'declarator':
            # 直接声明符
            return self._get_text(declarator_node)
        
        # 递归查找标识符
        for child in declarator_node.children:
            if child.type == 'identifier':
                return self._get_text(child)
        
        return None

    def _process_constructor(self, node: Node, class_usr: str, access_specifier: str) -> Optional[Function]:
        """处理构造函数"""
        # 构造函数名与类名相同
        class_obj = self.repo.get_node(class_usr)
        if not class_obj or not isinstance(class_obj, Class):
            return None
        
        constructor_name = class_obj.name
        qualifier = self._get_current_scope_qualifier()
        qualified_name = f"{qualifier}{constructor_name}"
        
        # 提取参数
        declarator_node = node.child_by_field_name('declarator')
        parameters, signature = self._extract_parameters_and_signature(declarator_node, constructor_name)
        usr = self._generate_usr('function', qualified_name, signature)
        
        # 分析修饰符
        modifiers = self._extract_function_modifiers_enhanced(node, declarator_node)
        
        constructor = Function(
            usr=usr,
            name=constructor_name,
            qualified_name=qualified_name,
            file_path=self.file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            return_type="",  # 构造函数没有返回类型
            parameters=parameters,
            signature=signature,
            is_definition=True,
            parent_class=class_usr,
            access_specifier=access_specifier
        )
        
        # 设置构造函数标志
        if hasattr(constructor, 'is_constructor'):
            constructor.is_constructor = True
        
        # 提取函数体
        body_node = node.child_by_field_name('body')
        if body_node:
            constructor.code_content = self._extract_function_body(body_node)
        
        self.repo.register_entity(constructor)
        return constructor

    def _process_destructor(self, node: Node, class_usr: str, access_specifier: str) -> Optional[Function]:
        """处理析构函数"""
        # 析构函数名是~ClassName
        class_obj = self.repo.get_node(class_usr)
        if not class_obj or not isinstance(class_obj, Class):
            return None
        
        destructor_name = f"~{class_obj.name}"
        qualifier = self._get_current_scope_qualifier()
        qualified_name = f"{qualifier}{destructor_name}"
        
        # 析构函数没有参数
        signature = f"{destructor_name}()"
        usr = self._generate_usr('function', qualified_name, signature)
        
        # 分析修饰符
        modifiers = self._extract_function_modifiers_enhanced(node, None)
        
        destructor = Function(
            usr=usr,
            name=destructor_name,
            qualified_name=qualified_name,
            file_path=self.file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            return_type="",  # 析构函数没有返回类型
            parameters=[],
            signature=signature,
            is_definition=True,
            parent_class=class_usr,
            access_specifier=access_specifier,
            is_virtual=modifiers.get('is_virtual', False)
        )
        
        # 设置析构函数标志
        if hasattr(destructor, 'is_destructor'):
            destructor.is_destructor = True
        
        # 提取函数体
        body_node = node.child_by_field_name('body')
        if body_node:
            destructor.code_content = self._extract_function_body(body_node)
        
        self.repo.register_entity(destructor)
        return destructor 