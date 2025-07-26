"""
专业化提取器模块

该模块将EntityExtractor的职责按实体类型进行分离，每个专业化提取器负责处理特定类型的实体。
主要优势：
- 单一职责原则：每个提取器只负责一种实体类型
- 可扩展性：易于添加新的实体类型支持
- 可测试性：每个提取器可以独立测试
- 可维护性：逻辑清晰，易于理解和修改
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from tree_sitter import Node

from .data_structures import (
    Entity, Function, Class, Namespace, Variable, Enum, Location,
    NodeRepository, FunctionStatusFlags, ClassStatusFlags
)
from .file_manager import get_file_manager
from .logger import Logger


class BaseExtractor(ABC):
    """提取器基类"""
    
    def __init__(self, file_path: str, file_content: str, repo: NodeRepository, file_id: str):
        self.file_path = file_path
        self.file_content = file_content
        self.repo = repo
        self.file_id = file_id
        self.logger = Logger.get_logger()
        
        # 上下文信息
        self.current_namespace_stack: List[str] = []
        self.current_class_stack: List[str] = []
        
    @abstractmethod
    def can_extract(self, node: Node) -> bool:
        """检查是否可以提取此节点"""
        pass
    
    @abstractmethod
    def extract(self, node: Node) -> Optional[Entity]:
        """提取实体"""
        pass
    
    def _get_text(self, node: Node) -> str:
        """获取节点文本"""
        if not node:
            return ""
        return self.file_content[node.start_byte:node.end_byte]
    
    def _get_current_namespace(self) -> str:
        """获取当前命名空间"""
        return "::".join(self.current_namespace_stack)
    
    def _get_current_class(self) -> str:
        """获取当前类"""
        return "::".join(self.current_namespace_stack + self.current_class_stack)
    
    def _create_location(self, node: Node) -> Location:
        """创建位置信息"""
        return Location(
            file_id=self.file_id,
            line=node.start_point[0] + 1,
            column=node.start_point[1] + 1
        )


class NamespaceExtractor(BaseExtractor):
    """命名空间提取器"""
    
    def can_extract(self, node: Node) -> bool:
        return node.type == 'namespace_definition'
    
    def extract(self, node: Node) -> Optional[Namespace]:
        try:
            # 提取命名空间名称
            name_node = node.child_by_field_name('name')
            if not name_node:
                return None
            
            name = self._get_text(name_node)
            qualified_name = f"{self._get_current_namespace()}::{name}" if self.current_namespace_stack else name
            
            # 创建USR
            usr = self.repo.generate_usr('namespace', qualified_name, file_path=self.file_path)
            
            # 创建命名空间实体
            namespace = Namespace(
                usr=usr,
                name=name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                is_definition=True,
                parent_namespace=self.repo.generate_usr('namespace', self._get_current_namespace()) if self.current_namespace_stack else None,
                definition_location=self._create_location(node)
            )
            
            # 检查是否是匿名命名空间
            if name == "" or name.isspace():
                namespace.is_anonymous = True
                namespace.name = "(anonymous)"
                namespace.qualified_name = f"{self._get_current_namespace()}::(anonymous)" if self.current_namespace_stack else "(anonymous)"
            
            self.logger.debug(f"提取命名空间: {namespace.qualified_name}")
            return namespace
            
        except Exception as e:
            self.logger.error(f"命名空间提取失败 {node.start_point}: {e}")
            return None


class ClassExtractor(BaseExtractor):
    """类/结构体提取器"""
    
    def can_extract(self, node: Node) -> bool:
        return node.type in ['class_specifier', 'struct_specifier']
    
    def extract(self, node: Node) -> Optional[Class]:
        try:
            # 提取类名
            name_node = node.child_by_field_name('name')
            if not name_node:
                return None
            
            name = self._get_text(name_node)
            qualified_name = f"{self._get_current_namespace()}::{name}" if self.current_namespace_stack else name
            
            # 创建USR
            usr = self.repo.generate_usr('class', qualified_name, file_path=self.file_path)
            
            # 创建类实体
            class_entity = Class(
                usr=usr,
                name=name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                is_definition=self._is_class_definition(node),
                is_struct=(node.type == 'struct_specifier'),
                parent_namespace=self.repo.generate_usr('namespace', self._get_current_namespace()) if self.current_namespace_stack else None,
                definition_location=self._create_location(node) if self._is_class_definition(node) else None
            )
            
            # 如果只是声明，添加到声明位置列表
            if not class_entity.is_definition:
                class_entity.declaration_locations.append(self._create_location(node))
                class_entity.is_declaration = True
            
            # 分析模板参数
            self._analyze_template_parameters(node, class_entity)
            
            # 分析继承关系
            self._analyze_inheritance(node, class_entity)
            
            # 如果是定义，分析类体
            if class_entity.is_definition:
                self._analyze_class_body(node, class_entity)
            
            self.logger.debug(f"提取类: {class_entity.qualified_name} ({'定义' if class_entity.is_definition else '声明'})")
            return class_entity
            
        except Exception as e:
            self.logger.error(f"类提取失败 {node.start_point}: {e}")
            return None
    
    def _is_class_definition(self, node: Node) -> bool:
        """检查是否是类定义而不是声明"""
        # 查找类体
        for child in node.children:
            if child.type == 'field_declaration_list':
                return True
        return False
    
    def _analyze_template_parameters(self, node: Node, class_entity: Class):
        """分析模板参数"""
        # 查找模板声明
        parent = node.parent
        if parent and parent.type == 'template_declaration':
            template_params = parent.child_by_field_name('parameters')
            if template_params:
                class_entity.is_template = True
                # 提取模板参数详情
                for param_node in template_params.children:
                    if param_node.type == 'type_parameter_declaration':
                        param_name = self._extract_template_param_name(param_node)
                        if param_name:
                            class_entity.template_parameters.append({
                                'name': param_name,
                                'type': 'typename',
                                'default': None
                            })
    
    def _extract_template_param_name(self, param_node: Node) -> Optional[str]:
        """提取模板参数名"""
        for child in param_node.children:
            if child.type == 'type_identifier':
                return self._get_text(child)
        return None
    
    def _analyze_inheritance(self, node: Node, class_entity: Class):
        """分析继承关系"""
        # 查找基类列表
        base_list = node.child_by_field_name('base_class_clause')
        if base_list:
            for child in base_list.children:
                if child.type == 'base_class_clause':
                    base_name = self._extract_base_class_name(child)
                    if base_name:
                        # 解析基类的USR
                        base_qualified_name = self._resolve_base_class_name(base_name)
                        base_usr = self.repo.generate_usr('class', base_qualified_name, file_path=self.file_path)
                        class_entity.base_classes.append(base_usr)
    
    def _extract_base_class_name(self, base_node: Node) -> Optional[str]:
        """提取基类名称"""
        for child in base_node.children:
            if child.type in ['type_identifier', 'qualified_identifier']:
                return self._get_text(child)
        return None
    
    def _resolve_base_class_name(self, base_name: str) -> str:
        """解析基类的完全限定名"""
        # 简化实现：如果没有命名空间前缀，使用当前命名空间
        if '::' not in base_name and self.current_namespace_stack:
            return f"{self._get_current_namespace()}::{base_name}"
        return base_name
    
    def _analyze_class_body(self, node: Node, class_entity: Class):
        """分析类体（占位符，实际实现中会调用其他提取器）"""
        # 这里会在实际集成时调用FunctionExtractor和VariableExtractor
        # 来处理类成员
        pass


class FunctionExtractor(BaseExtractor):
    """函数提取器"""
    
    def can_extract(self, node: Node) -> bool:
        return node.type in ['function_definition', 'function_declarator']
    
    def extract(self, node: Node) -> Optional[Function]:
        try:
            # 区分函数定义和声明
            is_definition = node.type == 'function_definition'
            
            # 对于函数定义，提取declarator
            if is_definition:
                declarator = node.child_by_field_name('declarator')
                if not declarator:
                    return None
                function_node = declarator
            else:
                function_node = node
            
            # 提取函数名
            name = self._extract_function_name(function_node)
            if not name:
                return None
            
            # 构建限定名
            if self.current_class_stack:
                qualified_name = f"{self._get_current_class()}::{name}"
            elif self.current_namespace_stack:
                qualified_name = f"{self._get_current_namespace()}::{name}"
            else:
                qualified_name = name
            
            # 提取函数签名
            signature = self._extract_function_signature(function_node)
            
            # 创建USR
            usr = self.repo.generate_usr('function', qualified_name, signature, self.file_path)
            
            # 创建函数实体
            function = Function(
                usr=usr,
                name=name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                signature=signature,
                is_definition=is_definition,
                parent_class=self.repo.generate_usr('class', self._get_current_class()) if self.current_class_stack else None
            )
            
            # 设置位置信息
            if is_definition:
                function.definition_location = self._create_location(node)
            else:
                function.declaration_locations.append(self._create_location(node))
                function.is_declaration = True
            
            # 分析函数属性
            self._analyze_function_attributes(node, function)
            
            # 分析返回类型
            function.return_type = self._extract_return_type(node)
            
            # 分析参数
            function.parameters = self._extract_function_parameters(function_node)
            
            # 如果是定义，提取函数体
            if is_definition:
                function.code_content = self._extract_function_body(node)
                
            self.logger.debug(f"提取函数: {function.qualified_name} ({'定义' if is_definition else '声明'})")
            return function
            
        except Exception as e:
            self.logger.error(f"函数提取失败 {node.start_point}: {e}")
            return None
    
    def _extract_function_name(self, declarator: Node) -> Optional[str]:
        """提取函数名"""
        if declarator.type == 'function_declarator':
            # 查找函数名
            for child in declarator.children:
                if child.type == 'identifier':
                    return self._get_text(child)
                elif child.type == 'qualified_identifier':
                    # 处理类方法
                    parts = self._get_text(child).split('::')
                    return parts[-1] if parts else None
        return None
    
    def _extract_function_signature(self, declarator: Node) -> str:
        """提取函数签名"""
        if declarator.type == 'function_declarator':
            # 查找参数列表
            params = declarator.child_by_field_name('parameters')
            if params:
                return self._get_text(params)
        return "()"
    
    def _extract_return_type(self, node: Node) -> Optional[str]:
        """提取返回类型"""
        if node.type == 'function_definition':
            # 查找类型说明符
            for child in node.children:
                if child.type in ['primitive_type', 'type_identifier', 'qualified_identifier']:
                    return self._get_text(child)
        return None
    
    def _extract_function_parameters(self, declarator: Node) -> List[Dict[str, str]]:
        """提取函数参数"""
        parameters = []
        
        params_node = declarator.child_by_field_name('parameters')
        if not params_node:
            return parameters
        
        for param in params_node.children:
            if param.type == 'parameter_declaration':
                param_info = self._extract_single_parameter(param)
                if param_info:
                    parameters.append(param_info)
        
        return parameters
    
    def _extract_single_parameter(self, param_node: Node) -> Optional[Dict[str, str]]:
        """提取单个参数信息"""
        param_type = None
        param_name = None
        
        # 提取参数类型
        type_node = param_node.child_by_field_name('type')
        if type_node:
            param_type = self._get_text(type_node)
        
        # 提取参数名
        declarator = param_node.child_by_field_name('declarator')
        if declarator:
            param_name = self._get_text(declarator)
        
        if param_type:
            return {
                'type': param_type,
                'name': param_name or '',
                'default': None  # TODO: 提取默认值
            }
        
        return None
    
    def _analyze_function_attributes(self, node: Node, function: Function):
        """分析函数属性"""
        # 检查访问说明符（如果在类中）
        if self.current_class_stack:
            function.access_specifier = self._get_current_access_level()
        
        # 检查函数修饰符
        # TODO: 分析 virtual, static, const, override 等修饰符
        
    def _get_current_access_level(self) -> str:
        """获取当前访问级别（简化实现）"""
        # 这里需要更复杂的逻辑来跟踪访问说明符
        return "public"  # 默认值
    
    def _extract_function_body(self, node: Node) -> str:
        """提取函数体代码"""
        body_node = node.child_by_field_name('body')
        if body_node:
            return self._get_text(body_node)
        return ""


class VariableExtractor(BaseExtractor):
    """变量提取器"""
    
    def can_extract(self, node: Node) -> bool:
        return node.type in ['declaration', 'field_declaration']
    
    def extract(self, node: Node) -> Optional[Variable]:
        try:
            # 提取变量类型
            var_type = self._extract_variable_type(node)
            if not var_type:
                return None
            
            # 提取变量名（可能有多个变量在一个声明中）
            variable_names = self._extract_variable_names(node)
            if not variable_names:
                return None
            
            # 为简化，只处理第一个变量
            var_name = variable_names[0]
            
            # 构建限定名
            if self.current_class_stack:
                qualified_name = f"{self._get_current_class()}::{var_name}"
            elif self.current_namespace_stack:
                qualified_name = f"{self._get_current_namespace()}::{var_name}"
            else:
                qualified_name = var_name
            
            # 创建USR
            usr = self.repo.generate_usr('variable', qualified_name, file_path=self.file_path)
            
            # 创建变量实体
            variable = Variable(
                usr=usr,
                name=var_name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                var_type=var_type,
                is_definition=True,
                parent_class=self.repo.generate_usr('class', self._get_current_class()) if self.current_class_stack else None
            )
            
            # 分析变量属性
            self._analyze_variable_attributes(node, variable)
            
            self.logger.debug(f"提取变量: {variable.qualified_name} ({variable.var_type})")
            return variable
            
        except Exception as e:
            self.logger.error(f"变量提取失败 {node.start_point}: {e}")
            return None
    
    def _extract_variable_type(self, node: Node) -> Optional[str]:
        """提取变量类型"""
        # 查找类型说明符
        for child in node.children:
            if child.type in ['primitive_type', 'type_identifier', 'qualified_identifier', 'template_type']:
                return self._get_text(child)
        return None
    
    def _extract_variable_names(self, node: Node) -> List[str]:
        """提取变量名列表"""
        names = []
        
        for child in node.children:
            if child.type == 'init_declarator':
                declarator = child.child_by_field_name('declarator')
                if declarator:
                    name = self._extract_declarator_name(declarator)
                    if name:
                        names.append(name)
            elif child.type == 'identifier':
                names.append(self._get_text(child))
        
        return names
    
    def _extract_declarator_name(self, declarator: Node) -> Optional[str]:
        """从声明器中提取名称"""
        if declarator.type == 'identifier':
            return self._get_text(declarator)
        elif declarator.type == 'pointer_declarator':
            # 处理指针变量
            for child in declarator.children:
                if child.type == 'identifier':
                    return self._get_text(child)
        elif declarator.type == 'array_declarator':
            # 处理数组变量
            declarator_child = declarator.child_by_field_name('declarator')
            if declarator_child:
                return self._extract_declarator_name(declarator_child)
        
        return None
    
    def _analyze_variable_attributes(self, node: Node, variable: Variable):
        """分析变量属性"""
        # 检查是否是常量
        node_text = self._get_text(node)
        if 'const' in node_text:
            variable.is_const = True
        
        if 'static' in node_text:
            variable.is_static = True
        
        # 设置访问说明符（如果是类成员）
        if self.current_class_stack:
            variable.access_specifier = self._get_current_access_level()
    
    def _get_current_access_level(self) -> str:
        """获取当前访问级别（简化实现）"""
        return "public"  # 默认值


class EnumExtractor(BaseExtractor):
    """枚举提取器"""
    
    def can_extract(self, node: Node) -> bool:
        return node.type in ['enum_specifier']
    
    def extract(self, node: Node) -> Optional[Enum]:
        try:
            # 提取枚举名
            name_node = node.child_by_field_name('name')
            name = self._get_text(name_node) if name_node else "(anonymous)"
            
            # 构建限定名
            if self.current_namespace_stack:
                qualified_name = f"{self._get_current_namespace()}::{name}"
            else:
                qualified_name = name
            
            # 创建USR
            usr = self.repo.generate_usr('enum', qualified_name, file_path=self.file_path)
            
            # 创建枚举实体
            enum_entity = Enum(
                usr=usr,
                name=name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                is_definition=True
            )
            
            # 提取枚举值
            enum_entity.values = self._extract_enum_values(node)
            
            # 提取底层类型（C++11 枚举类）
            enum_entity.underlying_type = self._extract_underlying_type(node)
            
            self.logger.debug(f"提取枚举: {enum_entity.qualified_name}")
            return enum_entity
            
        except Exception as e:
            self.logger.error(f"枚举提取失败 {node.start_point}: {e}")
            return None
    
    def _extract_enum_values(self, node: Node) -> List[Dict[str, Any]]:
        """提取枚举值"""
        values = []
        
        # 查找枚举体
        body = node.child_by_field_name('body')
        if body:
            for child in body.children:
                if child.type == 'enumerator':
                    value_info = self._extract_single_enum_value(child)
                    if value_info:
                        values.append(value_info)
        
        return values
    
    def _extract_single_enum_value(self, enumerator: Node) -> Optional[Dict[str, Any]]:
        """提取单个枚举值"""
        name_node = enumerator.child_by_field_name('name')
        if not name_node:
            return None
        
        name = self._get_text(name_node)
        
        # 检查是否有显式值
        value_node = enumerator.child_by_field_name('value')
        value = self._get_text(value_node) if value_node else None
        
        return {
            'name': name,
            'value': value,
            'line': enumerator.start_point[0] + 1
        }
    
    def _extract_underlying_type(self, node: Node) -> Optional[str]:
        """提取底层类型"""
        # 查找类型说明符（C++11 强类型枚举）
        for child in node.children:
            if child.type in ['primitive_type', 'type_identifier']:
                return self._get_text(child)
        return None


class ModularEntityExtractor:
    """模块化实体提取器管理器"""
    
    def __init__(self, file_path: str, file_content: str, repo: NodeRepository, file_id: str):
        self.file_path = file_path
        self.file_content = file_content
        self.repo = repo
        self.file_id = file_id
        self.logger = Logger.get_logger()
        
        # 初始化专业化提取器
        self.extractors = [
            NamespaceExtractor(file_path, file_content, repo, file_id),
            ClassExtractor(file_path, file_content, repo, file_id),
            FunctionExtractor(file_path, file_content, repo, file_id),
            VariableExtractor(file_path, file_content, repo, file_id),
            EnumExtractor(file_path, file_content, repo, file_id)
        ]
        
        # 上下文管理
        self.namespace_stack: List[str] = []
        self.class_stack: List[str] = []
    
    def extract_from_node(self, node: Node) -> Optional[Entity]:
        """从节点提取实体"""
        # 更新所有提取器的上下文
        self._update_extractors_context()
        
        # 尝试每个提取器
        for extractor in self.extractors:
            if extractor.can_extract(node):
                entity = extractor.extract(node)
                if entity:
                    return entity
        
        return None
    
    def _update_extractors_context(self):
        """更新所有提取器的上下文"""
        for extractor in self.extractors:
            extractor.current_namespace_stack = self.namespace_stack.copy()
            extractor.current_class_stack = self.class_stack.copy()
    
    def enter_namespace(self, namespace_name: str):
        """进入命名空间"""
        self.namespace_stack.append(namespace_name)
    
    def exit_namespace(self):
        """退出命名空间"""
        if self.namespace_stack:
            self.namespace_stack.pop()
    
    def enter_class(self, class_name: str):
        """进入类"""
        self.class_stack.append(class_name)
    
    def exit_class(self):
        """退出类"""
        if self.class_stack:
            self.class_stack.pop() 