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
import uuid
import hashlib

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
class Entity:
    """基本代码实体的基类"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    usr: str = ""  # 全局唯一资源标识符 (USR)
    name: str = ""
    qualified_name: str = ""
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    type: str = "entity" # "function", "class", "namespace", "variable", "enum"
    is_definition: bool = False
    definition_id: Optional[str] = None
    declaration_ids: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.qualified_name:
            self.qualified_name = self.name
        # USR的生成将由提取器在有足够上下文时处理

@dataclass
class Function(Entity):
    """表示一个函数或方法"""
    signature: str = ""
    return_type: Optional[str] = None
    parameters: List[Dict[str, str]] = field(default_factory=list)
    calls_to: List[str] = field(default_factory=list)  # List of USRs
    called_by: List[str] = field(default_factory=list) # List of USRs
    complexity: int = 0
    is_static: bool = False
    is_virtual: bool = False
    is_pure_virtual: bool = False
    is_override: bool = False
    is_final: bool = False
    is_const: bool = False
    access_specifier: str = "default"
    parent_class: Optional[str] = None # USR of parent class
    code_content: str = ""  # 函数体源代码

    def __post_init__(self):
        super().__post_init__()
        self.type = 'function'

@dataclass
class Class(Entity):
    """表示一个类或结构体"""
    base_classes: List[str] = field(default_factory=list)
    derived_classes: List[str] = field(default_factory=list)
    methods: List[str] = field(default_factory=list) # List of function USRs
    fields: List[str] = field(default_factory=list) # List of variable USRs
    is_struct: bool = False
    is_abstract: bool = False
    is_template: bool = False
    parent_namespace: Optional[str] = None # USR of parent namespace

    def __post_init__(self):
        super().__post_init__()
        self.type = 'class'

@dataclass
class Namespace(Entity):
    """表示一个C++命名空间"""
    parent_namespace: Optional[str] = None # USR of parent
    children: List[str] = field(default_factory=list) # USRs of children namespaces, classes, functions
    
    def __post_init__(self):
        super().__post_init__()
        self.type = 'namespace'

@dataclass
class Variable(Entity):
    """表示一个变量"""
    var_type: Optional[str] = None
    is_const: bool = False
    is_static: bool = False
    access_specifier: str = "default"
    initializer: Optional[str] = None
    parent_class: Optional[str] = None # USR of parent class

    def __post_init__(self):
        super().__post_init__()
        self.type = 'variable'

@dataclass
class Enum(Entity):
    """表示一个枚举类型"""
    underlying_type: Optional[str] = None
    values: List[Dict[str, Any]] = field(default_factory=list)
    
    def __post_init__(self):
        super().__post_init__()
        self.type = 'enum'

@dataclass
class Project:
    """表示整个分析的项目"""
    name: str
    files: List[str] = field(default_factory=list)
    
    # 这些列表现在存储实体的USR，而不是完整对象
    functions: List[str] = field(default_factory=list)
    classes: List[str] = field(default_factory=list)
    namespaces: List[str] = field(default_factory=list)
    
    call_graph: Dict[str, List[str]] = field(default_factory=dict)
    reverse_call_graph: Dict[str, List[str]] = field(default_factory=dict)
    inheritance_graph: Dict[str, List[str]] = field(default_factory=dict)

    def add_file(self, file_path: str):
        if file_path not in self.files:
            self.files.append(file_path)

    def add_entity(self, entity: Entity):
        """根据实体类型将其USR添加到相应的列表中"""
        if isinstance(entity, Function) and entity.usr not in self.functions:
            self.functions.append(entity.usr)
        elif isinstance(entity, Class) and entity.usr not in self.classes:
            self.classes.append(entity.usr)
        elif isinstance(entity, Namespace) and entity.usr not in self.namespaces:
            self.namespaces.append(entity.usr)

    def build_graphs(self, node_repository: 'NodeRepository'):
        """使用节点存储库中的信息构建调用图和继承图"""
        self.call_graph.clear()
        self.reverse_call_graph.clear()
        self.inheritance_graph.clear()

        for usr, node in node_repository.nodes.items():
            if isinstance(node, Function):
                if node.calls_to:
                    self.call_graph[usr] = node.calls_to
                for callee_usr in node.calls_to:
                    self.reverse_call_graph.setdefault(callee_usr, []).append(usr)
            elif isinstance(node, Class):
                if node.base_classes:
                    self.inheritance_graph[usr] = node.base_classes


class NodeRepository:
    """一个用于存储和管理所有代码实体的全局存储库"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(NodeRepository, cls).__new__(cls)
            cls._instance.nodes: Dict[str, Entity] = {}
            cls._instance.qualified_name_index: Dict[str, List[str]] = {}  # qualified_name -> [usr_ids]
            cls._instance.file_index: Dict[str, List[str]] = {}  # file_path -> [usr_ids]
            cls._instance.call_relationships: Dict[str, Dict[str, List[str]]] = {
                'calls_to': {},    # caller_usr -> [callee_usrs]
                'called_by': {}    # callee_usr -> [caller_usrs]
            }
        return cls._instance

    def generate_usr(self, entity_type: str, qualified_name: str, signature: str = None, file_path: str = "") -> str:
        """
        生成全局唯一的USR ID
        
        格式规范：
        - 函数: c:@F@<qualified_name>@<signature_hash>
        - 类/结构: c:@S@<qualified_name>
        - 命名空间: c:@N@<qualified_name>
        - 变量: c:@V@<qualified_name>
        - 枚举: c:@E@<qualified_name>
        """
        if entity_type == 'function':
            # 为函数生成基于签名的USR
            if signature:
                # 标准化签名以确保一致性
                normalized_sig = self._normalize_signature(signature)
                sig_hash = hashlib.md5(normalized_sig.encode('utf-8')).hexdigest()[:8]
                return f"c:@F@{qualified_name}@{sig_hash}"
            else:
                # 没有签名的情况，使用文件路径作为区分
                file_hash = hashlib.md5(file_path.encode('utf-8')).hexdigest()[:8]
                return f"c:@F@{qualified_name}@{file_hash}"
        elif entity_type in ['class', 'struct']:
            return f"c:@S@{qualified_name}"
        elif entity_type == 'namespace':
            return f"c:@N@{qualified_name}"
        elif entity_type == 'variable':
            return f"c:@V@{qualified_name}"
        elif entity_type == 'enum':
            return f"c:@E@{qualified_name}"
        else:
            # 备用方案：基于路径和名称的唯一标识
            path_hash = hashlib.md5(f"{file_path}::{qualified_name}".encode('utf-8')).hexdigest()[:8]
            return f"c:@U@{qualified_name}@{path_hash}"

    def _normalize_signature(self, signature: str) -> str:
        """标准化函数签名以确保USR一致性"""
        # 移除多余空格
        normalized = re.sub(r'\s+', ' ', signature.strip())
        # 移除参数名称，只保留类型
        normalized = re.sub(r'\b\w+\s*(?=[,)])', '', normalized)
        # 标准化const位置
        normalized = re.sub(r'\s*const\s*', ' const ', normalized)
        return normalized.strip()

    def register_entity(self, entity: Entity) -> str:
        """注册实体并返回USR ID"""
        if not entity.usr:
            entity.usr = self.generate_usr(
                entity.type, 
                entity.qualified_name, 
                getattr(entity, 'signature', None),
                entity.file_path
            )
        
        self.add_node(entity)
        return entity.usr

    def add_node(self, node: Entity):
        """添加一个新节点。如果已存在，则尝试合并信息。"""
        if not node.usr:
            # 在没有有效USR的情况下无法添加
            return

        existing_node = self.nodes.get(node.usr)
        if existing_node:
            # 如果新节点是定义，而旧节点是声明，则用新节点替换
            if node.is_definition and not existing_node.is_definition:
                # 在替换之前，保留必要的信息，比如调用者
                if isinstance(node, Function) and isinstance(existing_node, Function):
                    node.called_by = list(set(node.called_by + existing_node.called_by))
                
                # 更新声明ID列表
                node.declaration_ids = list(set(node.declaration_ids + existing_node.declaration_ids + [existing_node.id]))
                if existing_node.definition_id:
                     node.declaration_ids.append(existing_node.definition_id)
                
                self.nodes[node.usr] = node
            
            # 如果两者都是声明，可以合并一些信息
            elif not node.is_definition and not existing_node.is_definition:
                existing_node.declaration_ids.append(node.id)
            
            # 如果旧节点是定义，新节点是声明
            elif existing_node.is_definition and not node.is_definition:
                existing_node.declaration_ids.append(node.id)
                # 不需要替换，但可以记录这个声明的位置

        else:
            self.nodes[node.usr] = node
            
        # 更新索引
        self._update_indexes(node)

    def _update_indexes(self, node: Entity):
        """更新各种索引以支持快速查找"""
        # 更新qualified_name索引
        if node.qualified_name not in self.qualified_name_index:
            self.qualified_name_index[node.qualified_name] = []
        if node.usr not in self.qualified_name_index[node.qualified_name]:
            self.qualified_name_index[node.qualified_name].append(node.usr)
        
        # 更新文件索引
        if node.file_path not in self.file_index:
            self.file_index[node.file_path] = []
        if node.usr not in self.file_index[node.file_path]:
            self.file_index[node.file_path].append(node.usr)

    def find_by_qualified_name(self, qualified_name: str, entity_type: str = None) -> List[Entity]:
        """通过qualified_name查找实体"""
        usr_ids = self.qualified_name_index.get(qualified_name, [])
        entities = [self.nodes[usr_id] for usr_id in usr_ids if usr_id in self.nodes]
        
        if entity_type:
            entities = [e for e in entities if e.type == entity_type]
        
        return entities

    def find_by_signature(self, signature: str) -> Optional[Entity]:
        """通过函数签名查找函数"""
        normalized_sig = self._normalize_signature(signature)
        for usr_id, entity in self.nodes.items():
            if isinstance(entity, Function) and hasattr(entity, 'signature'):
                if self._normalize_signature(entity.signature) == normalized_sig:
                    return entity
        return None

    def resolve_function_call(self, called_name: str, context_namespace: str = "", context_class: str = "") -> Optional[str]:
        """解析函数调用，返回被调用函数的USR ID"""
        # 构建可能的qualified names
        possible_names = [
            called_name,  # 直接名称
            f"{context_namespace}::{called_name}" if context_namespace else called_name,
            f"{context_class}::{called_name}" if context_class else called_name,
            f"{context_namespace}::{context_class}::{called_name}" if context_namespace and context_class else called_name
        ]
        
        for name in possible_names:
            functions = self.find_by_qualified_name(name, 'function')
            if functions:
                # 优先返回定义而不是声明
                for func in functions:
                    if func.is_definition:
                        return func.usr
                # 如果没有定义，返回第一个声明
                return functions[0].usr
        
        return None

    def add_call_relationship(self, caller_usr: str, callee_usr: str):
        """添加函数调用关系"""
        # 更新calls_to关系
        if caller_usr not in self.call_relationships['calls_to']:
            self.call_relationships['calls_to'][caller_usr] = []
        if callee_usr not in self.call_relationships['calls_to'][caller_usr]:
            self.call_relationships['calls_to'][caller_usr].append(callee_usr)
        
        # 更新called_by关系
        if callee_usr not in self.call_relationships['called_by']:
            self.call_relationships['called_by'][callee_usr] = []
        if caller_usr not in self.call_relationships['called_by'][callee_usr]:
            self.call_relationships['called_by'][callee_usr].append(caller_usr)
        
        # 同步更新实体对象中的关系
        caller = self.get_node(caller_usr)
        callee = self.get_node(callee_usr)
        
        if isinstance(caller, Function):
            if callee_usr not in caller.calls_to:
                caller.calls_to.append(callee_usr)
        
        if isinstance(callee, Function):
            if caller_usr not in callee.called_by:
                callee.called_by.append(caller_usr)

    def get_node(self, usr: str) -> Optional[Entity]:
        """通过USR获取一个节点"""
        return self.nodes.get(usr)

    def get_all_nodes(self) -> List[Entity]:
        """获取所有节点"""
        return list(self.nodes.values())

    def get_nodes_by_type(self, entity_type: str) -> List[Entity]:
        """获取指定类型的所有节点"""
        return [node for node in self.nodes.values() if node.type == entity_type]

    def get_nodes_by_file(self, file_path: str) -> List[Entity]:
        """获取指定文件中的所有节点"""
        usr_ids = self.file_index.get(file_path, [])
        return [self.nodes[usr_id] for usr_id in usr_ids if usr_id in self.nodes]

    def clear(self):
        """清空存储库"""
        self.nodes.clear()
        self.qualified_name_index.clear()
        self.file_index.clear()
        self.call_relationships = {'calls_to': {}, 'called_by': {}}

    def get_statistics(self) -> Dict[str, Any]:
        """获取存储库统计信息"""
        stats = {
            'total_entities': len(self.nodes),
            'by_type': {},
            'call_relationships': len(self.call_relationships['calls_to']),
            'files_analyzed': len(self.file_index)
        }
        
        for entity in self.nodes.values():
            stats['by_type'][entity.type] = stats['by_type'].get(entity.type, 0) + 1
        
        return stats

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