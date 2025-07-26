"""
C++ 代码分析器数据结构 (版本 2.4)

该模块定义了用于表示C++代码实体的所有数据结构，
严格遵循 `json_format.md` v2.3 规范，并添加USR ID支持。
主要特性包括：
- 使用USR ID作为全局唯一标识符
- 使用位掩码 (status flags) 替代多个布尔字段。
- 引入 `cpp_extensions` 结构来封装C++特有属性。
- 支持函数签名键值和文件ID映射。
- 函数体代码内容提取支持
"""

from dataclasses import dataclass, field
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
# 核心数据结构
# ==============================================================================

@dataclass
class Location:
    """代码位置信息 (使用文件ID)"""
    file_id: str
    line: int
    column: int

@dataclass
class Parameter:
    """函数参数"""
    name: str
    type: str  # 简化后的类型名
    default_value: Optional[str] = None

@dataclass
class ResolvedDefinitionLocation:
    file_id: str
    line: int
    column: int

@dataclass
class CppCallInfo:
    """C++ 调用关系扩展信息"""
    call_status_flags: int = 0
    call_type: str = "method_call"
    template_args: List[str] = field(default_factory=list)
    operator_type: str = ""
    calling_object: str = ""
    argument_types: List[str] = field(default_factory=list)
    resolved_overload: str = "" # 解析到的重载函数的USR ID
    resolved_definition_location: Optional[ResolvedDefinitionLocation] = None

@dataclass
class CallInfo:
    """函数调用信息 - 详细的调用信息"""
    to_usr_id: str  # 被调用函数的USR ID
    line: int
    column: int
    type: str = "direct"  # e.g., direct, virtual_call
    resolved_definition_file_id: Optional[str] = None
    cpp_call_info: CppCallInfo = field(default_factory=CppCallInfo)

@dataclass
class CppExtensions:
    """函数/方法 C++ 扩展字段"""
    qualified_name: str
    namespace: str = ""
    function_status_flags: int = 0
    access_specifier: str = "public"
    storage_class: str = "none"
    calling_convention: str = "default"
    return_type: str = "void"
    parameter_types: Dict[str, str] = field(default_factory=dict)
    template_parameters: List[Dict[str, Any]] = field(default_factory=list)
    exception_specification: str = ""
    attributes: List[str] = field(default_factory=list)
    mangled_name: str = ""
    usr: Optional[str] = None # USR作为内部关联和调试使用
    # 新增：保留签名键值用于向后兼容
    signature_key: str = ""

@dataclass
class Function:
    """函数/方法实体 (符合 json_format.md v2.4 - USR ID支持)"""
    # 顶层字段
    name: str
    signature: str  # 完整函数签名
    usr_id: str  # 新增：USR ID作为唯一标识
    definition_file_id: Optional[str] = None
    declaration_file_id: Optional[str] = None
    start_line: int = 0
    end_line: int = 0
    is_local: bool = False
    parameters: List[Parameter] = field(default_factory=list)
    
    # 新增：函数体代码内容
    code_content: str = ""
    
    # 新增：声明vs定义的处理
    declaration_locations: List[Location] = field(default_factory=list)
    definition_location: Optional[Location] = None
    is_declaration: bool = False
    is_definition: bool = False
    
    # 修改：调用关系使用USR ID列表
    calls_to: List[str] = field(default_factory=list)  # USR ID列表
    called_by: List[str] = field(default_factory=list)  # USR ID列表
    call_details: List[CallInfo] = field(default_factory=list)  # 详细调用信息
    
    # C++ 扩展
    cpp_extensions: CppExtensions = field(default_factory=CppExtensions)

@dataclass
class InheritanceInfo:
    """继承信息"""
    base_class_usr_id: str # 基类的USR ID
    access_specifier: str = "public"
    is_virtual: bool = False

@dataclass
class SpecialMethodInfo:
    """构造/析构函数信息"""
    special_method_status_flags: int = 0
    access: str = "public"

@dataclass
class CppOopExtensions:
    """类/结构体 C++ OOP 扩展字段"""
    qualified_name: str
    namespace: str = ""
    type: str = "class"  # class or struct
    class_status_flags: int = 0
    inheritance_list: List[InheritanceInfo] = field(default_factory=list)
    template_parameters: List[Dict[str, Any]] = field(default_factory=list)
    template_specialization_args: List[str] = field(default_factory=list)
    nested_types: List[str] = field(default_factory=list)
    friend_declarations: List[str] = field(default_factory=list)
    size_in_bytes: int = 0
    alignment: int = 0
    virtual_table_info: Dict[str, Any] = field(default_factory=dict)
    constructors: Dict[str, SpecialMethodInfo] = field(default_factory=dict)
    destructor: Optional[SpecialMethodInfo] = None
    usr: Optional[str] = None # USR作为内部关联和调试使用
    # 新增：保留签名键值用于向后兼容
    signature_key: str = ""

@dataclass
class Class:
    """类/结构体实体 (符合 json_format.md v2.4 - USR ID支持)"""
    name: str
    qualified_name: str
    usr_id: str  # 新增：USR ID作为唯一标识
    definition_file_id: Optional[str] = None
    declaration_file_id: Optional[str] = None
    line: int = 0
    
    # 新增：声明vs定义的处理
    declaration_locations: List[Location] = field(default_factory=list)
    definition_location: Optional[Location] = None
    is_declaration: bool = False
    is_definition: bool = False
    
    parent_classes: List[str] = field(default_factory=list) # 基类的USR ID
    is_abstract: bool = False # 将从 status_flags 解析
    is_mixin: bool = False
    documentation: str = ""
    methods: List[str] = field(default_factory=list) # 方法的USR ID
    fields: Dict[str, Any] = field(default_factory=dict)
    cpp_oop_extensions: CppOopExtensions = field(default_factory=CppOopExtensions)

@dataclass
class Namespace:
    """命名空间实体"""
    name: str
    qualified_name: str
    usr_id: str  # 新增：USR ID作为唯一标识
    definition_file_id: str
    line: int
    
    # 新增：声明vs定义的处理（命名空间可能在多个文件中）
    declaration_locations: List[Location] = field(default_factory=list)
    definition_location: Optional[Location] = None
    
    is_anonymous: bool = False
    is_inline: bool = False
    parent_namespace: str = "global"
    nested_namespaces: List[str] = field(default_factory=list)  # USR ID列表
    classes: List[str] = field(default_factory=list)  # USR ID列表
    functions: List[str] = field(default_factory=list)  # USR ID列表
    variables: List[str] = field(default_factory=list)  # USR ID列表
    aliases: Dict[str, str] = field(default_factory=dict)
    using_declarations: List[str] = field(default_factory=list)
    usr: Optional[str] = None # USR作为内部关联和调试使用

# ==============================================================================
# 新增：全局节点系统
# ==============================================================================

@dataclass
class EntityNode:
    """统一的实体节点，支持多种类型"""
    usr_id: str
    entity_type: str  # "function", "class", "namespace", "variable"
    entity_data: Union[Function, Class, Namespace]
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式用于JSON序列化"""
        return {
            "usr_id": self.usr_id,
            "entity_type": self.entity_type,
            "entity_data": self.entity_data.__dict__ if hasattr(self.entity_data, '__dict__') else str(self.entity_data)
        }

# ==============================================================================
# 新增：向后兼容性支持
# ==============================================================================

class KeyGenerator:
    """生成符合规范的函数和类的签名键值（用于向后兼容）"""
    @staticmethod
    def _simplify_type(type_name: str) -> str:
        """简化C++类型名以用于键值生成"""
        if not isinstance(type_name, str):
            type_name = "unknown"
        
        # 规则参考 json_format.md
        s = re.sub(r'\bconst\b', 'const', type_name)
        s = s.replace('::', '')
        s = s.replace('<', '')
        s = s.replace('>', '')
        s = s.replace('*', 'Ptr')
        s = s.replace('&', 'Ref')
        s = s.replace(' ', '')
        s = s.replace(',', '')
        return s

    @classmethod
    def for_function(cls, return_type: str, func_name: str, param_types: List[str], file_id: str) -> str:
        """生成函数签名键值（用于向后兼容）"""
        simplified_params = [cls._simplify_type(p) for p in param_types]
        return f"{cls._simplify_type(return_type)}_{func_name}_{'_'.join(simplified_params)}_{file_id}"

    @classmethod
    def for_class(cls, qualified_name: str, file_id: str) -> str:
        """生成类签名键值（用于向后兼容）"""
        return f"{cls._simplify_type(qualified_name)}_{file_id}" 