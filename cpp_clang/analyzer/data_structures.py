"""
C++ 代码分析器数据结构 (版本 2.4 - 性能优化版)

该模块定义了用于表示C++代码实体的所有数据结构，
严格遵循 `json_format.md` v2.3 规范，并添加USR ID支持。
主要特性包括：
- 使用USR ID作为全局唯一标识符
- 使用位掩码 (status flags) 替代多个布尔字段。
- 引入 `cpp_extensions` 结构来封装C++特有属性。
- 支持函数签名键值和文件ID映射。
- 函数体代码内容提取支持
- 性能优化：移除@dataclass装饰器，使用普通类以提升性能
"""

from typing import Dict, List, Optional, Any, Tuple, Union
import re

# ==============================================================================
# 状态位掩码定义 (与 json_format.md 一致)
# ==============================================================================

class FunctionStatusFlags:
    FUNC_IS_TEMPLATE            = 1 << 0
    FUNC_IS_TEMPLATE_SPEC       = 1 << 1
    FUNC_IS_VIRTUAL             = 1 << 2
    FUNC_IS_PURE_VIRTUAL        = 1 << 3
    FUNC_IS_OVERRIDE            = 1 << 4
    FUNC_IS_FINAL               = 1 << 5
    FUNC_IS_STATIC              = 1 << 6
    FUNC_IS_CONST               = 1 << 7
    FUNC_IS_NOEXCEPT            = 1 << 8
    FUNC_IS_INLINE              = 1 << 9
    FUNC_IS_CONSTEXPR           = 1 << 10
    FUNC_IS_OPERATOR_OVERLOAD   = 1 << 11
    FUNC_IS_CONSTRUCTOR         = 1 << 12
    FUNC_IS_DESTRUCTOR          = 1 << 13
    FUNC_IS_COPY_CONSTRUCTOR    = 1 << 14
    FUNC_IS_MOVE_CONSTRUCTOR    = 1 << 15

class ClassStatusFlags:
    CLASS_IS_TEMPLATE           = 1 << 0
    CLASS_IS_TEMPLATE_SPEC      = 1 << 1
    CLASS_IS_ABSTRACT           = 1 << 2
    CLASS_IS_FINAL              = 1 << 3
    CLASS_IS_POD                = 1 << 4
    CLASS_IS_TRIVIAL            = 1 << 5
    CLASS_IS_STANDARD_LAYOUT    = 1 << 6
    CLASS_IS_POLYMORPHIC        = 1 << 7

class CallStatusFlags:
    CALL_IS_VIRTUAL             = 1 << 0
    CALL_IS_TEMPLATE_INST       = 1 << 1
    CALL_IS_OPERATOR            = 1 << 2
    CALL_IS_CONSTRUCTOR         = 1 << 3
    CALL_IS_STATIC              = 1 << 4

class SpecialMethodStatusFlags:
    SPECIAL_IS_DEFINED          = 1 << 0
    SPECIAL_IS_VIRTUAL          = 1 << 1
    SPECIAL_IS_DELETED          = 1 << 2
    SPECIAL_IS_DEFAULTED        = 1 << 3

# ==============================================================================
# 核心数据结构 - 性能优化版本
# ==============================================================================

class Location:
    """代码位置信息 (使用文件ID) - 性能优化版"""
    def __init__(self, file_id: str, line: int, column: int):
        self.file_id = file_id
        self.line = line
        self.column = column
    
    def __eq__(self, other):
        if not isinstance(other, Location):
            return False
        return (self.file_id == other.file_id and 
                self.line == other.line and 
                self.column == other.column)
    
    def __hash__(self):
        return hash((self.file_id, self.line, self.column))

class Parameter:
    """函数参数 - 性能优化版"""
    def __init__(self, name: str, type: str, default_value: Optional[str] = None):
        self.name = name
        self.type = type  # 简化后的类型名
        self.default_value = default_value

class TemplateParameter:
    """模板参数 - 性能优化版"""
    def __init__(self, name: str, type: str):
        self.name = name
        self.type = type  # e.g., "typename", "int"

class MemberVariable:
    """类成员变量 - 性能优化版"""
    def __init__(self, name: str, type: str, usr_id: str, access_specifier: str = "private",
                 is_static: bool = False, is_const: bool = False, is_mutable: bool = False,
                 location: Optional[Location] = None, default_value: Optional[str] = None):
        self.name = name
        self.type = type
        self.usr_id = usr_id
        self.access_specifier = access_specifier  # public, private, protected
        self.is_static = is_static
        self.is_const = is_const
        self.is_mutable = is_mutable
        self.location = location
        self.default_value = default_value

class ResolvedDefinitionLocation:
    """解析的定义位置 - 性能优化版"""
    def __init__(self, file_id: str, line: int, column: int):
        self.file_id = file_id
        self.line = line
        self.column = column

class CppCallInfo:
    """C++ 调用关系扩展信息 - 性能优化版"""
    def __init__(self, call_status_flags: int = 0, call_type: str = "method_call",
                 template_args: Optional[List[str]] = None, operator_type: str = "",
                 calling_object: str = "", argument_types: Optional[List[str]] = None,
                 resolved_overload: str = "", 
                 resolved_definition_location: Optional[ResolvedDefinitionLocation] = None):
        self.call_status_flags = call_status_flags
        self.call_type = call_type
        self.template_args = template_args or []
        self.operator_type = operator_type
        self.calling_object = calling_object
        self.argument_types = argument_types or []
        self.resolved_overload = resolved_overload
        self.resolved_definition_location = resolved_definition_location

class CallInfo:
    """函数调用信息 - 详细的调用信息 - 性能优化版"""
    def __init__(self, to_usr_id: str, line: int, column: int, type: str = "direct",
                 resolved_definition_file_id: Optional[str] = None,
                 cpp_call_info: Optional[CppCallInfo] = None):
        self.to_usr_id = to_usr_id  # 被调用函数的USR ID
        self.line = line
        self.column = column
        self.type = type  # e.g., direct, virtual_call
        self.resolved_definition_file_id = resolved_definition_file_id
        self.cpp_call_info = cpp_call_info or CppCallInfo()

class CppExtensions:
    """函数/方法 C++ 扩展字段 - 性能优化版"""
    def __init__(self, qualified_name: str, namespace: str = "",
                 function_status_flags: int = 0, access_specifier: str = "public",
                 storage_class: str = "none", calling_convention: str = "default",
                 return_type: str = "void", parameter_types: Optional[Dict[str, str]] = None,
                 template_parameters: Optional[List[TemplateParameter]] = None,
                 exception_specification: str = "", attributes: Optional[List[str]] = None,
                 mangled_name: str = "", usr: Optional[str] = None):
        self.qualified_name = qualified_name
        self.namespace = namespace
        self.function_status_flags = function_status_flags
        self.access_specifier = access_specifier
        self.storage_class = storage_class
        self.calling_convention = calling_convention
        self.return_type = return_type
        self.parameter_types = parameter_types or {}
        self.template_parameters = template_parameters or []
        self.exception_specification = exception_specification
        self.attributes = attributes or []
        self.mangled_name = mangled_name
        self.usr = usr  # USR作为内部关联和调试使用

class Function:
    """函数/方法实体 (符合 json_format.md v2.4 - USR ID支持) - 性能优化版"""
    def __init__(self, name: str, signature: str, usr_id: str,
                 definition_file_id: Optional[str] = None, declaration_file_id: Optional[str] = None,
                 start_line: int = 0, end_line: int = 0, is_local: bool = False,
                 parameters: Optional[List[Parameter]] = None, code_content: str = "",
                 declaration_locations: Optional[List[Location]] = None,
                 definition_location: Optional[Location] = None,
                 is_declaration: bool = False, is_definition: bool = False,
                 calls_to: Optional[List[str]] = None, called_by: Optional[List[str]] = None,
                 call_details: Optional[List[CallInfo]] = None,
                 cpp_extensions: Optional[CppExtensions] = None):
        # 顶层字段
        self.name = name
        self.signature = signature  # 完整函数签名
        self.usr_id = usr_id  # USR ID作为唯一标识
        self.definition_file_id = definition_file_id
        self.declaration_file_id = declaration_file_id
        self.start_line = start_line
        self.end_line = end_line
        self.is_local = is_local
        self.parameters = parameters or []
        
        # 函数体代码内容
        self.code_content = code_content
        
        # 声明vs定义的处理
        self.declaration_locations = declaration_locations or []
        self.definition_location = definition_location
        self.is_declaration = is_declaration
        self.is_definition = is_definition
        
        # 调用关系使用USR ID列表
        self.calls_to = calls_to or []  # USR ID列表
        self.called_by = called_by or []  # USR ID列表
        self.call_details = call_details or []  # 详细调用信息
        
        # C++ 扩展
        self.cpp_extensions = cpp_extensions or CppExtensions(qualified_name=name)

class InheritanceInfo:
    """继承信息 - 性能优化版"""
    def __init__(self, base_class_usr_id: str, access_specifier: str = "public",
                 is_virtual: bool = False):
        self.base_class_usr_id = base_class_usr_id  # 基类的USR ID
        self.access_specifier = access_specifier
        self.is_virtual = is_virtual

class SpecialMethodInfo:
    """构造/析构函数信息 - 性能优化版"""
    def __init__(self, special_method_status_flags: int = 0, access: str = "public"):
        self.special_method_status_flags = special_method_status_flags
        self.access = access

class CppOopExtensions:
    """类/结构体 C++ OOP 扩展字段 - 性能优化版"""
    def __init__(self, qualified_name: str, namespace: str = "", type: str = "class",
                 class_status_flags: int = 0, inheritance_list: Optional[List[InheritanceInfo]] = None,
                 template_parameters: Optional[List[TemplateParameter]] = None,
                 template_specialization_args: Optional[List[str]] = None,
                 nested_types: Optional[List[str]] = None,
                 friend_declarations: Optional[List[str]] = None,
                 size_in_bytes: int = 0, alignment: int = 0,
                 virtual_table_info: Optional[Dict[str, Any]] = None,
                 constructors: Optional[Dict[str, SpecialMethodInfo]] = None,
                 destructor: Optional[SpecialMethodInfo] = None,
                 usr: Optional[str] = None):
        self.qualified_name = qualified_name
        self.namespace = namespace
        self.type = type  # class or struct
        self.class_status_flags = class_status_flags
        self.inheritance_list = inheritance_list or []
        self.template_parameters = template_parameters or []
        self.template_specialization_args = template_specialization_args or []
        self.nested_types = nested_types or []
        self.friend_declarations = friend_declarations or []
        self.size_in_bytes = size_in_bytes
        self.alignment = alignment
        self.virtual_table_info = virtual_table_info or {}
        self.constructors = constructors or {}
        self.destructor = destructor
        self.usr = usr  # USR作为内部关联和调试使用

class Class:
    """类/结构体实体 (符合 json_format.md v2.4 - USR ID支持) - 性能优化版"""
    def __init__(self, name: str, qualified_name: str, usr_id: str,
                 definition_file_id: Optional[str] = None, declaration_file_id: Optional[str] = None,
                 line: int = 0, declaration_locations: Optional[List[Location]] = None,
                 definition_location: Optional[Location] = None,
                 is_declaration: bool = False, is_definition: bool = False,
                 parent_classes: Optional[List[str]] = None, is_abstract: bool = False,
                 is_mixin: bool = False, documentation: str = "",
                 methods: Optional[List[str]] = None, fields: Optional[Dict[str, Any]] = None,
                 cpp_oop_extensions: Optional[CppOopExtensions] = None):
        self.name = name
        self.qualified_name = qualified_name
        self.usr_id = usr_id  # USR ID作为唯一标识
        self.definition_file_id = definition_file_id
        self.declaration_file_id = declaration_file_id
        self.line = line
        
        # 声明vs定义的处理
        self.declaration_locations = declaration_locations or []
        self.definition_location = definition_location
        self.is_declaration = is_declaration
        self.is_definition = is_definition
        
        self.parent_classes = parent_classes or []  # 基类的USR ID
        self.is_abstract = is_abstract  # 将从 status_flags 解析
        self.is_mixin = is_mixin
        self.documentation = documentation
        self.methods = methods or []  # 方法的USR ID
        self.member_variables = []  # 成员变量的USR ID列表
        self.fields = fields or {}  # 保留原有字段以兼容性
        self.cpp_oop_extensions = cpp_oop_extensions or CppOopExtensions(qualified_name=qualified_name)

class Namespace:
    """命名空间实体 - 性能优化版"""
    def __init__(self, name: str, qualified_name: str, usr_id: str, definition_file_id: str,
                 line: int, declaration_locations: Optional[List[Location]] = None,
                 definition_location: Optional[Location] = None,
                 is_anonymous: bool = False, is_inline: bool = False,
                 parent_namespace: str = "global", nested_namespaces: Optional[List[str]] = None,
                 classes: Optional[List[str]] = None, functions: Optional[List[str]] = None,
                 variables: Optional[List[str]] = None, aliases: Optional[Dict[str, str]] = None,
                 using_declarations: Optional[List[str]] = None, usr: Optional[str] = None):
        self.name = name
        self.qualified_name = qualified_name
        self.usr_id = usr_id  # USR ID作为唯一标识
        self.definition_file_id = definition_file_id
        self.line = line
        
        # 声明vs定义的处理（命名空间可能在多个文件中）
        self.declaration_locations = declaration_locations or []
        self.definition_location = definition_location
        
        self.is_anonymous = is_anonymous
        self.is_inline = is_inline
        self.parent_namespace = parent_namespace
        self.nested_namespaces = nested_namespaces or []  # USR ID列表
        self.classes = classes or []  # USR ID列表
        self.functions = functions or []  # USR ID列表
        self.variables = variables or []  # USR ID列表
        self.aliases = aliases or {}
        self.using_declarations = using_declarations or []
        self.usr = usr  # USR作为内部关联和调试使用

# ==============================================================================
# 全局节点系统 - 性能优化版本
# ==============================================================================

class EntityNode:
    """统一的实体节点，支持多种类型 - 性能优化版"""
    def __init__(self, usr_id: str, entity_type: str, entity_data: Union[Function, Class, Namespace]):
        self.usr_id = usr_id
        self.entity_type = entity_type  # "function", "class", "namespace", "variable"
        self.entity_data = entity_data
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式用于JSON序列化"""
        return {
            "usr_id": self.usr_id,
            "entity_type": self.entity_type,
            "entity_data": self.entity_data.__dict__ if hasattr(self.entity_data, '__dict__') else str(self.entity_data)
        }