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
import os
import threading
from threading import RLock, Lock
from contextlib import contextmanager
import logging

# ==============================================================================
# 线程安全支持
# ==============================================================================

class ReadWriteLock:
    """写优先的读写锁实现，防止writer饥饿"""
    def __init__(self):
        self._read_ready = threading.Condition(threading.RLock())
        self._readers = 0
        self._writers_waiting = 0  # 等待的写线程数
        self._writer_active = False  # 是否有活跃的写线程
        
        # 性能监控
        self._read_acquisitions = 0
        self._write_acquisitions = 0
        self._write_wait_time_total = 0.0
        self._stats_lock = threading.Lock()

    @contextmanager
    def read_lock(self):
        """获取读锁的上下文管理器"""
        self.acquire_read()
        try:
            yield
        finally:
            self.release_read()

    @contextmanager
    def write_lock(self):
        """获取写锁的上下文管理器"""
        self.acquire_write()
        try:
            yield
        finally:
            self.release_write()

    def acquire_read(self):
        """获取读锁 - 写优先策略"""
        import time
        start_time = time.time()
        
        with self._read_ready:
            # 写优先：如果有写线程等待或活跃，读线程需要等待
            while self._writers_waiting > 0 or self._writer_active:
                self._read_ready.wait()
            
            self._readers += 1
            
        # 更新统计
        with self._stats_lock:
            self._read_acquisitions += 1

    def release_read(self):
        """释放读锁"""
        with self._read_ready:
            self._readers -= 1
            # 如果没有读线程了，通知等待的写线程
            if self._readers == 0:
                self._read_ready.notify_all()

    def acquire_write(self):
        """获取写锁 - 写优先策略"""
        import time
        start_time = time.time()
        
        with self._read_ready:
            # 标记有写线程等待
            self._writers_waiting += 1
            
            try:
                # 等待所有读线程完成且没有活跃的写线程
                while self._readers > 0 or self._writer_active:
                    self._read_ready.wait()
                
                # 获得写锁
                self._writer_active = True
                
            finally:
                # 无论是否获得锁，都要减少等待计数
                self._writers_waiting -= 1
        
        # 更新统计
        wait_time = time.time() - start_time
        with self._stats_lock:
            self._write_acquisitions += 1
            self._write_wait_time_total += wait_time

    def release_write(self):
        """释放写锁"""
        with self._read_ready:
            self._writer_active = False
            # 通知所有等待的线程（读线程和写线程）
            self._read_ready.notify_all()
    
    def get_lock_statistics(self) -> Dict[str, Any]:
        """获取锁使用统计信息"""
        with self._stats_lock:
            avg_write_wait = (self._write_wait_time_total / max(self._write_acquisitions, 1)) * 1000  # 毫秒
            
            return {
                "read_acquisitions": self._read_acquisitions,
                "write_acquisitions": self._write_acquisitions,
                "average_write_wait_ms": round(avg_write_wait, 2),
                "total_write_wait_time": round(self._write_wait_time_total, 3),
                "current_readers": self._readers,
                "writers_waiting": self._writers_waiting,
                "writer_active": self._writer_active
            }

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
# 统一的核心数据结构（基于Entity体系）
# ==============================================================================

@dataclass
class Location:
    """代码位置信息 (使用文件ID)"""
    file_id: str
    line: int
    column: int

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
    is_template: bool = False
    template_params: List[str] = field(default_factory=list)
    access_specifier: str = "default"
    parent_class: Optional[str] = None # USR of parent class
    code_content: str = ""  # 函数体源代码
    call_details: List[CallInfo] = field(default_factory=list)  # 详细调用信息
    
    # 新增：声明vs定义的处理
    declaration_locations: List[Location] = field(default_factory=list)
    definition_location: Optional[Location] = None
    is_declaration: bool = False

    def __post_init__(self):
        super().__post_init__()
        self.type = 'function'

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
    
    # 新增：声明vs定义的处理
    declaration_locations: List[Location] = field(default_factory=list)
    definition_location: Optional[Location] = None
    is_declaration: bool = False
    
    # C++ OOP 扩展信息
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

    def __post_init__(self):
        super().__post_init__()
        self.type = 'class'

@dataclass
class Namespace(Entity):
    """表示一个C++命名空间"""
    parent_namespace: Optional[str] = None # USR of parent
    children: List[str] = field(default_factory=list) # USRs of children namespaces, classes, functions
    
    # 新增：声明vs定义的处理（命名空间可能在多个文件中）
    declaration_locations: List[Location] = field(default_factory=list)
    definition_location: Optional[Location] = None
    
    is_anonymous: bool = False
    is_inline: bool = False
    nested_namespaces: List[str] = field(default_factory=list)  # USR ID列表
    classes: List[str] = field(default_factory=list)  # USR ID列表
    functions: List[str] = field(default_factory=list)  # USR ID列表
    variables: List[str] = field(default_factory=list)  # USR ID列表
    aliases: Dict[str, str] = field(default_factory=dict)
    using_declarations: List[str] = field(default_factory=list)
    
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

    def __init__(self):
        self.nodes: Dict[str, Entity] = {}
        self.qualified_name_index: Dict[str, List[str]] = {}  # qualified_name -> [usr_ids]
        self.file_index: Dict[str, List[str]] = {}  # file_path -> [usr_ids]
        self.call_relationships: Dict[str, Dict[str, List[str]]] = {
            'calls_to': {},    # caller_usr -> [callee_usrs]
            'called_by': {}    # callee_usr -> [caller_usrs]
        }
        self._lock = ReadWriteLock()  # 使用读写锁

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(NodeRepository, cls).__new__(cls)
        return cls._instance

    def generate_usr(self, entity_type: str, qualified_name: str, signature: Optional[str] = None, file_path: str = "", template_params: List[str] = None) -> str:
        """
        生成USR (Unified Symbol Resolution) - 增强版
        
        支持功能:
        - 函数重载区分（通过增强的函数签名）
        - 模板参数编码（增强版）
        - 文件作用域处理
        - 多种实体类型支持
        
        Args:
            entity_type: 实体类型
            qualified_name: 限定名称
            signature: 函数签名（可选）
            file_path: 文件路径（可选）
            template_params: 模板参数（可选）
            
        Returns:
            USR字符串
        """
        if not qualified_name:
            return ""

        
        with self._lock.read_lock(): # 使用读锁
            if entity_type == 'function':
                # 函数USR: c:@F@namespace::func@(int,double)@template<T,U>
                normalized_sig = self._normalize_function_signature_enhanced(signature) if signature else "()"
                template_info = self._encode_template_params(template_params) if template_params else ""
                
                # 对于静态函数，在signature中加入文件标识而不是USR中
                if self._is_static_function_context(qualified_name, file_path):
                    file_id = self._get_file_identifier(file_path)
                    normalized_sig = f"{normalized_sig}@{file_id}"
                
                usr_parts = [
                    qualified_name,
                    normalized_sig,
                    template_info
                ]
                base_usr = f"c:@F@{'@'.join(filter(None, usr_parts))}"
                
            elif entity_type in ['class', 'struct']:
                # 类USR: c:@S@namespace::class@template<T>
                template_info = self._encode_template_params(template_params) if template_params else ""
                
                usr_parts = [
                    qualified_name,
                    template_info
                ]
                base_usr = f"c:@S@{'@'.join(filter(None, usr_parts))}"
                
            elif entity_type == 'namespace':
                # 命名空间USR: c:@N@namespace
                base_usr = f"c:@N@{qualified_name}"
                
            elif entity_type == 'variable':
                # 变量USR: c:@V@qualified_name@scope_info（如果需要区分作用域）
                scope_info = self._get_variable_scope_info(qualified_name, file_path)
                if scope_info:
                    base_usr = f"c:@V@{qualified_name}@{scope_info}"
                else:
                    base_usr = f"c:@V@{qualified_name}"
                
            elif entity_type == 'enum':
                # 枚举USR: c:@E@namespace::enum
                base_usr = f"c:@E@{qualified_name}"
                
            elif entity_type == 'typedef':
                # 类型别名USR: c:@T@namespace::typedef
                base_usr = f"c:@T@{qualified_name}"
                
            elif entity_type == 'macro':
                # 宏USR: c:@M@macro_name
                base_usr = f"c:@M@{qualified_name}"
                
            else:
                # 通用USR
                base_usr = f"c:@{entity_type}@{qualified_name}"
            
            # USR安全性验证：确保只包含可打印字符
            try:
                # 移除任何不可打印字符，但保留基本符号
                safe_usr = ''.join(char for char in base_usr if char.isprintable() and ord(char) < 127)
                
                # 验证USR格式的基本正确性
                if not safe_usr.startswith('c:@'):
                    logging.warning(f"Generated USR format invalid: {base_usr[:50]}...")
                    # 重新构建安全的USR
                    safe_qualified_name = ''.join(char for char in qualified_name if char.isprintable() and ord(char) < 127)
                    safe_usr = f"c:@{entity_type}@{safe_qualified_name}"
                
                return safe_usr
                
            except Exception as e:
                # 如果USR安全化失败，返回最基本的USR
                logging.warning(f"USR safety validation failed: {e}, using basic USR")
                safe_name = ''.join(char for char in str(qualified_name) if char.isalnum() or char in '_:')
                return f"c:@{entity_type}@{safe_name}"

    def _is_static_function_context(self, qualified_name: str, file_path: str) -> bool:
        """检查是否是需要文件区分的静态函数上下文 - 增强版"""
        # 1. 检查是否是文件级静态函数（不在类或命名空间中）
        if not qualified_name or '::' not in qualified_name:
            return True
        
        # 2. 检查是否是匿名命名空间中的函数
        if qualified_name.startswith('::') or '(anonymous)' in qualified_name:
            return True
        
        # 3. 检查是否是已知的静态函数模式（更精确的模式匹配）
        function_name = qualified_name.split('::')[-1]
        
        # 静态函数命名模式（可配置）
        static_patterns = [
            r'^.*_static$',      # 以_static结尾
            r'^static_.*',       # 以static_开头
            r'^.*_impl$',        # 内部实现函数
            r'^.*_helper$',      # 辅助函数
            r'^.*_internal$',    # 内部函数
            r'^__.*__$',         # 双下划线包围（通常是内部函数）
            r'^_.*_$',           # 单下划线包围
            r'^.*_detail$',      # 实现细节函数
            r'^.*_private$',     # 私有函数
            r'^get_.*_instance$', # 单例模式的获取函数
        ]
        
        for pattern in static_patterns:
            if re.match(pattern, function_name):
                return True
        
        # 4. 检查是否是模板特化（模板特化通常需要文件区分）
        if '<' in qualified_name and '>' in qualified_name:
            return True
        
        # 5. 检查是否包含某些关键词（通常表明是文件级函数）
        internal_keywords = ['detail', 'impl', 'internal', 'anonymous', 'local']
        qualified_lower = qualified_name.lower()
        for keyword in internal_keywords:
            if keyword in qualified_lower:
                return True
        
        return False

    def _get_file_identifier(self, file_path: str) -> str:
        """获取文件标识符（用于静态函数区分） - 增强版"""
        if not file_path:
            return "unknown"
        
        # 使用更稳定的文件标识符生成方法
        filename = os.path.basename(file_path)
        name_without_ext = os.path.splitext(filename)[0]
        
        # 生成6位hash后缀，更好的唯一性保证
        file_hash = hashlib.md5(file_path.encode('utf-8')).hexdigest()[:6]
        
        # 清理文件名，移除非法字符
        clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', name_without_ext)
        
        if len(clean_name) <= 6:
            # 文件名足够短，使用文件名+hash
            return f"{clean_name}_{file_hash}"
        else:
            # 文件名太长，使用前6个字符+hash
            prefix = clean_name[:6]
            return f"{prefix}_{file_hash}"

    def _get_variable_scope_info(self, qualified_name: str, file_path: str) -> str:
        """获取变量作用域信息（如果需要区分同名变量） - 增强版"""
        # 1. 对于全局变量，需要文件区分
        if not qualified_name or '::' not in qualified_name:
            return self._get_file_identifier(file_path)
        
        # 2. 对于static成员变量，也可能需要区分
        if 'static' in qualified_name.lower():
            return self._get_file_identifier(file_path)
        
        # 3. 对于匿名命名空间中的变量
        if '(anonymous)' in qualified_name:
            return self._get_file_identifier(file_path)
        
        # 4. 对于模板变量（C++14 variable templates）
        if '<' in qualified_name and '>' in qualified_name:
            return self._get_file_identifier(file_path)
        
        # 5. 对于内部变量（根据命名模式判断）
        internal_patterns = [
            r'.*_internal$', r'.*_impl$', r'.*_detail$', 
            r'.*_private$', r'.*_local$'
        ]
        
        for pattern in internal_patterns:
            if re.match(pattern, qualified_name.split('::')[-1]):
                return self._get_file_identifier(file_path)
        
        return ""

    def _resolve_usr_conflict(self, base_usr: str, entity_type: str, qualified_name: str, file_path: str = "") -> str:
        """解决USR冲突"""
        # 检查是否已存在
        if base_usr not in self.nodes:
            return base_usr
        
        existing_entity = self.nodes[base_usr]
        
        # 如果是同一类型且qualified_name相同，这可能是合法的声明/定义对
        if (existing_entity.type == entity_type and 
            existing_entity.qualified_name == qualified_name):
            return base_usr  # 允许合并
        
        # 检查是否是ODR违规（One Definition Rule）
        if entity_type in ['function', 'class', 'variable']:
            # 记录ODR违规警告
            from .logger import Logger
            logger = Logger.get_logger()
            logger.warning(f"潜在ODR违规: {qualified_name} 在多处定义 - {file_path}")
        
        # 生成冲突解决后缀
        conflict_suffix = 1
        while f"{base_usr}@ODR{conflict_suffix}" in self.nodes:
            conflict_suffix += 1
        
        return f"{base_usr}@ODR{conflict_suffix}"

    def _normalize_function_signature_enhanced(self, signature: str) -> str:
        """
        增强版函数签名标准化 - 支持现代C++特性
        
        新增支持:
        - const/volatile cv限定符
        - 引用限定符 (&, &&)
        - noexcept规范
        - 返回类型
        - requires约束
        - 指针/数组维度区分
        
        Args:
            signature: 原始函数签名
            
        Returns:
            标准化后的签名，格式: (params)@cv_quals@ref_qual@noexcept@return_type@requires
        """
        if not signature:
            return "()"
        
        # 分离函数签名的各个组件
        components = self._parse_function_signature_components(signature)
        
        # 标准化参数列表
        normalized_params = self._normalize_parameter_list_enhanced(components['parameters'])
        
        # 构建完整的签名字符串
        sig_parts = [f"({normalized_params})"]
        
        # 添加CV限定符
        if components['cv_qualifiers']:
            sig_parts.append(f"cv:{components['cv_qualifiers']}")
        
        # 添加引用限定符
        if components['ref_qualifier']:
            sig_parts.append(f"ref:{components['ref_qualifier']}")
        
        # 添加noexcept规范
        if components['noexcept_spec']:
            sig_parts.append(f"noexcept:{components['noexcept_spec']}")
        
        # 添加返回类型（用于区分重载）
        if components['return_type']:
            sig_parts.append(f"ret:{components['return_type']}")
        
        # 添加requires约束摘要
        if components['requires_clause']:
            requires_hash = hashlib.md5(components['requires_clause'].encode('utf-8')).hexdigest()[:8]
            sig_parts.append(f"req:{requires_hash}")
        
        return "@".join(sig_parts)

    def _parse_function_signature_components(self, signature: str) -> Dict[str, str]:
        """
        解析函数签名的各个组件
        
        Args:
            signature: 完整的函数签名
            
        Returns:
            包含各个组件的字典
        """
        components = {
            'return_type': '',
            'parameters': '',
            'cv_qualifiers': '',
            'ref_qualifier': '',
            'noexcept_spec': '',
            'requires_clause': ''
        }
        
        # 移除多余空格并清理
        signature = re.sub(r'\s+', ' ', signature.strip())
        
        # 1. 提取requires子句（在最后）
        requires_match = re.search(r'\s+requires\s+(.+)$', signature)
        if requires_match:
            components['requires_clause'] = requires_match.group(1).strip()
            signature = signature[:requires_match.start()]
        
        # 2. 提取noexcept规范
        noexcept_patterns = [
            r'\s+noexcept\(([^)]+)\)',  # noexcept(expression)
            r'\s+noexcept\s*$',         # noexcept
            r'\s+noexcept\s+',          # noexcept后面还有其他
        ]
        
        for pattern in noexcept_patterns:
            match = re.search(pattern, signature)
            if match:
                if match.groups():
                    components['noexcept_spec'] = match.group(1).strip()
                else:
                    components['noexcept_spec'] = 'true'
                signature = signature[:match.start()] + signature[match.end():]
                break
        
        # 3. 提取引用限定符 (& 或 &&)
        ref_qual_match = re.search(r'\)\s*(&&?)\s*(?:const|volatile|\s)*$', signature)
        if ref_qual_match:
            components['ref_qualifier'] = ref_qual_match.group(1)
            signature = signature[:ref_qual_match.start(1)] + signature[ref_qual_match.end(1):]
        
        # 4. 提取CV限定符
        cv_match = re.search(r'\)\s*((?:const|volatile|\s)+)(?:&&?|\s)*$', signature)
        if cv_match:
            cv_text = cv_match.group(1).strip()
            cv_quals = []
            if 'const' in cv_text:
                cv_quals.append('const')
            if 'volatile' in cv_text:
                cv_quals.append('volatile')
            components['cv_qualifiers'] = ','.join(cv_quals)
            signature = signature[:cv_match.start(1)] + signature[cv_match.end(1):]
        
        # 5. 提取参数列表
        param_match = re.search(r'\(([^)]*)\)', signature)
        if param_match:
            components['parameters'] = param_match.group(1).strip()
            # 移除参数部分，留下返回类型
            return_type_part = signature[:param_match.start()].strip()
            if return_type_part:
                components['return_type'] = self._normalize_type_name_enhanced(return_type_part)
        
        return components

    def _normalize_parameter_list_enhanced(self, params_str: str) -> str:
        """
        增强版参数列表标准化
        
        Args:
            params_str: 参数列表字符串
            
        Returns:
            标准化的参数列表
        """
        if not params_str:
            return ""
        
        # 解析参数，支持复杂类型
        params = self._parse_parameter_list_enhanced(params_str)
        
        # 标准化每个参数类型
        normalized_params = []
        for param in params:
            normalized_type = self._normalize_parameter_type_enhanced(param)
            normalized_params.append(normalized_type)
        
        return ','.join(normalized_params)

    def _parse_parameter_list_enhanced(self, params_str: str) -> List[str]:
        """
        增强版参数列表解析 - 支持更复杂的类型
        
        Args:
            params_str: 参数列表字符串
            
        Returns:
            参数类型列表
        """
        params = []
        current_param = ""
        depth = 0
        in_string = False
        bracket_depth = 0
        
        i = 0
        while i < len(params_str):
            char = params_str[i]
            
            # 处理字符串字面量
            if char in ['"', "'"]:
                in_string = not in_string
                current_param += char
            
            elif not in_string:
                # 处理各种括号
                if char in '(<':
                    depth += 1
                    current_param += char
                elif char in '[':
                    bracket_depth += 1
                    current_param += char
                elif char in ')>':
                    depth -= 1
                    current_param += char
                elif char in ']':
                    bracket_depth -= 1
                    current_param += char
                elif char == ',' and depth == 0 and bracket_depth == 0:
                    # 找到参数分隔符
                    if current_param.strip():
                        param_type = self._extract_parameter_type_enhanced(current_param.strip())
                        params.append(param_type)
                    current_param = ""
                else:
                    current_param += char
            else:
                current_param += char
            
            i += 1
        
        # 处理最后一个参数
        if current_param.strip():
            param_type = self._extract_parameter_type_enhanced(current_param.strip())
            params.append(param_type)
        
        return params

    def _extract_parameter_type_enhanced(self, param_str: str) -> str:
        """
        从参数声明中提取类型 - 增强版
        
        Args:
            param_str: 参数声明字符串
            
        Returns:
            参数类型
        """
        # 移除默认值
        if '=' in param_str:
            param_str = param_str.split('=')[0].strip()
        
        # 处理函数指针类型：int (*func)(int, double)
        if '(*' in param_str and ')' in param_str:
            return param_str  # 保持函数指针完整声明
        
        # 处理引用和指针的参数名移除
        # 例：const std::string& name -> const std::string&
        
        # 首先标准化空格
        param_str = re.sub(r'\s+', ' ', param_str.strip())
        
        # 识别类型修饰符
        type_keywords = ['const', 'volatile', 'mutable', 'static', 'extern', 'inline', 'virtual']
        pointer_ref_pattern = r'[*&]+'
        
        # 分解参数为token
        tokens = param_str.split()
        
        # 从右侧开始移除参数名
        # 最右侧的标识符通常是参数名（除非它是类型的一部分）
        result_tokens = []
        i = len(tokens) - 1
        
        while i >= 0:
            token = tokens[i]
            
            # 如果token是类型修饰符或包含特殊字符，保留
            if (token in type_keywords or 
                re.search(pointer_ref_pattern, token) or
                '::' in token or 
                '<' in token or '>' in token or
                '[' in token or ']' in token or
                '(' in token or ')' in token):
                result_tokens.insert(0, token)
            
            # 如果是简单标识符且是第一次遇到，可能是参数名
            elif i == len(tokens) - 1 and token.isidentifier():
                # 跳过参数名
                pass
            else:
                result_tokens.insert(0, token)
            
            i -= 1
        
        return ' '.join(result_tokens)

    def _normalize_parameter_type_enhanced(self, param_type: str) -> str:
        """
        增强版参数类型标准化 - 区分指针/引用/数组
        
        Args:
            param_type: 参数类型字符串
            
        Returns:
            标准化的参数类型，包含指针/引用/数组信息
        """
        if not param_type:
            return "void"
        
        # 基础类型标准化
        normalized = self._normalize_type_name_enhanced(param_type)
        
        # 分析指针/引用/数组维度
        type_category = self._analyze_type_category(normalized)
        
        # 构建标准化格式: base_type@category
        if isinstance(type_category, dict):
            return f"{type_category['base_type']}@{type_category['category']}"
        
        return normalized

    def _analyze_type_category(self, type_str: str) -> Union[str, Dict[str, str]]:
        """
        分析类型的类别（值/指针/引用/数组）
        
        Args:
            type_str: 类型字符串
            
        Returns:
            类型类别信息
        """
        if not type_str:
            return 'value'
        
        # 移除空格进行分析
        clean_type = re.sub(r'\s+', '', type_str)
        
        # 分析数组维度
        array_matches = re.findall(r'\[([^\]]*)\]', clean_type)
        if array_matches:
            base_type = re.sub(r'\[[^\]]*\]', '', clean_type)
            array_dims = len(array_matches)
            return {
                'base_type': base_type,
                'category': f'array[{array_dims}]'
            }
        
        # 分析指针层数
        pointer_count = clean_type.count('*')
        if pointer_count > 0:
            base_type = clean_type.replace('*', '')
            return {
                'base_type': base_type,
                'category': f'ptr[{pointer_count}]'
            }
        
        # 分析引用类型
        if clean_type.endswith('&&'):
            base_type = clean_type[:-2]
            return {
                'base_type': base_type,
                'category': 'rref'
            }
        elif clean_type.endswith('&'):
            base_type = clean_type[:-1]
            return {
                'base_type': base_type,
                'category': 'lref'
            }
        
        # 值类型
        return 'value'

    def _normalize_type_name_enhanced(self, type_name: str) -> str:
        """
        增强版类型名称标准化
        
        Args:
            type_name: 原始类型名
            
        Returns:
            标准化后的类型名
        """
        if not type_name:
            return "void"
        
        # 确保输入是字符串类型
        if not isinstance(type_name, str):
            type_name = str(type_name)
        
        # 移除不可见字符和控制字符，保留基本的ASCII和UTF-8字符
        type_name = ''.join(char for char in type_name if char.isprintable() or char.isspace())
        
        # 移除换行符和其他控制字符
        type_name = type_name.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
        
        # 移除多余空格
        type_name = re.sub(r'\s+', ' ', type_name.strip())
        
        # 标准化const位置
        type_name = re.sub(r'\bconst\s+', 'const ', type_name)
        type_name = re.sub(r'\s+const\b', ' const', type_name)
        
        # 标准化指针和引用
        type_name = re.sub(r'\s*\*\s*', '*', type_name)
        type_name = re.sub(r'\s*&\s*', '&', type_name)
        
        # 标准化模板参数空格
        type_name = re.sub(r'<\s+', '<', type_name)
        type_name = re.sub(r'\s+>', '>', type_name)
        type_name = re.sub(r',\s+', ',', type_name)
        
        # 标准化命名空间分隔符
        type_name = re.sub(r'\s*::\s*', '::', type_name)
        
        return type_name.strip()

    def _encode_template_params(self, template_params: List[str]) -> str:
        """
        编码模板参数为USR组件 - 增强版
        
        新增支持:
        - 模板参数种类区分 (typename/class vs int vs auto vs template template)
        - 实例化参数vs声明参数区分
        - 偏特化模式识别
        - 约束/概念摘要
        - 安全的特殊字符处理
        
        Args:
            template_params: 模板参数列表
            
        Returns:
            编码后的模板参数字符串，格式: tpl_<kind_hash>_<params_hash>
        """
        if not template_params:
            return ""
        
        try:
            # 预处理模板参数，确保安全的字符串处理
            safe_template_params = []
            for param in template_params:
                if not isinstance(param, str):
                    param = str(param)
                
                # 移除不可见字符和控制字符
                safe_param = ''.join(char for char in param if char.isprintable() or char.isspace())
                safe_template_params.append(safe_param)
            
            # 分析模板参数种类和特性
            param_analysis = self._analyze_template_parameters(safe_template_params)
            
            # 生成参数种类摘要
            kind_signature = self._generate_template_kind_signature(param_analysis)
            
            # 生成参数内容摘要  
            content_signature = self._generate_template_content_signature(param_analysis)
            
            # 安全的字符串编码：确保输入是有效的UTF-8
            try:
                kind_bytes = kind_signature.encode('utf-8', errors='replace')
                content_bytes = content_signature.encode('utf-8', errors='replace')
            except UnicodeError:
                # 如果编码失败，使用ASCII安全模式
                kind_bytes = kind_signature.encode('ascii', errors='ignore')
                content_bytes = content_signature.encode('ascii', errors='ignore')
            
            # 结合种类和内容生成最终hash
            kind_hash = hashlib.md5(kind_bytes).hexdigest()[:8]
            content_hash = hashlib.md5(content_bytes).hexdigest()[:8]
            
            return f"tpl_{kind_hash}_{content_hash}"
            
        except Exception as e:
            # 如果模板参数处理失败，返回安全的默认值
            logging.warning(f"Template parameter encoding failed: {e}, using fallback")
            fallback_signature = "".join(str(p) for p in template_params if p)
            fallback_hash = hashlib.md5(fallback_signature.encode('ascii', errors='ignore')).hexdigest()[:8]
            return f"tpl_fallback_{fallback_hash}"

    def _analyze_template_parameters(self, template_params: List[str]) -> List[Dict[str, Any]]:
        """
        分析模板参数的详细特性
        
        Args:
            template_params: 模板参数列表
            
        Returns:
            参数分析结果列表
        """
        analysis_results = []
        
        for param in template_params:
            param_info = {
                'raw_param': param.strip(),
                'kind': 'unknown',
                'name': '',
                'default_value': '',
                'constraints': '',
                'is_pack': False,
                'is_specialized': False
            }
            
            # 标准化参数
            normalized_param = re.sub(r'\s+', ' ', param.strip())
            
            # 检查是否有默认值
            if '=' in normalized_param:
                param_part, default_part = normalized_param.split('=', 1)
                param_info['default_value'] = default_part.strip()
                normalized_param = param_part.strip()
            
            # 检查是否是参数包 (...)
            if '...' in normalized_param:
                param_info['is_pack'] = True
                normalized_param = normalized_param.replace('...', '').strip()
            
            # 分析参数种类
            param_info.update(self._classify_template_parameter(normalized_param))
            
            analysis_results.append(param_info)
        
        return analysis_results

    def _classify_template_parameter(self, param_str: str) -> Dict[str, str]:
        """
        分类模板参数种类
        
        Args:
            param_str: 模板参数字符串
            
        Returns:
            分类结果
        """
        result = {
            'kind': 'unknown',
            'name': '',
            'constraints': ''
        }
        
        # 移除多余空格
        param_str = re.sub(r'\s+', ' ', param_str.strip())
        
        # 1. 类型参数 (typename/class)
        typename_match = re.match(r'^(typename|class)\s+([A-Za-z_][A-Za-z0-9_]*)', param_str)
        if typename_match:
            result['kind'] = 'type'
            result['name'] = typename_match.group(2)
            
            # 检查约束 (C++20 concepts)
            remaining = param_str[typename_match.end():].strip()
            if remaining:
                result['constraints'] = remaining
            
            return result
        
        # 2. 非类型参数 (int, bool, etc.)
        nontype_match = re.match(r'^([^=\s]+)\s+([A-Za-z_][A-Za-z0-9_]*)', param_str)
        if nontype_match:
            type_part = nontype_match.group(1)
            name_part = nontype_match.group(2)
            
            # 检查是否是已知的非类型参数类型
            nontype_keywords = ['int', 'bool', 'char', 'size_t', 'auto', 'decltype']
            for keyword in nontype_keywords:
                if keyword in type_part:
                    result['kind'] = 'nontype'
                    result['name'] = name_part
                    result['constraints'] = type_part
                    return result
        
        # 3. 模板模板参数
        template_template_match = re.match(r'^template\s*<([^>]*)>\s*(typename|class)\s+([A-Za-z_][A-Za-z0-9_]*)', param_str)
        if template_template_match:
            result['kind'] = 'template_template'
            result['name'] = template_template_match.group(3)
            result['constraints'] = template_template_match.group(1)
            return result
        
        # 4. C++20 概念约束参数
        concept_match = re.match(r'^([A-Za-z_][A-Za-z0-9_:]*)\s+([A-Za-z_][A-Za-z0-9_]*)', param_str)
        if concept_match and not typename_match:
            # 可能是概念约束
            result['kind'] = 'concept'
            result['name'] = concept_match.group(2)
            result['constraints'] = concept_match.group(1)
            return result
        
        # 5. auto参数 (C++17/20)
        if param_str.startswith('auto'):
            auto_match = re.match(r'^auto\s+([A-Za-z_][A-Za-z0-9_]*)', param_str)
            if auto_match:
                result['kind'] = 'auto'
                result['name'] = auto_match.group(1)
                return result
        
        # 6. 特化参数（具体实例化值）
        if not any(keyword in param_str for keyword in ['typename', 'class', 'template', 'auto']):
            # 可能是特化参数
            result['kind'] = 'specialized'
            result['name'] = param_str
            return result
        
        # 默认情况
        result['name'] = param_str
        return result

    def _generate_template_kind_signature(self, param_analysis: List[Dict[str, Any]]) -> str:
        """
        生成模板参数种类签名
        
        Args:
            param_analysis: 参数分析结果
            
        Returns:
            种类签名字符串
        """
        kind_parts = []
        
        for param in param_analysis:
            kind_part = param['kind']
            
            # 添加修饰符
            if param['is_pack']:
                kind_part += '_pack'
            
            if param['is_specialized']:
                kind_part += '_spec'
            
            if param['constraints']:
                # 对约束进行安全的摘要处理
                try:
                    constraint_str = str(param['constraints'])
                    # 移除特殊字符
                    safe_constraint = ''.join(char for char in constraint_str if char.isprintable())
                    constraint_hash = hashlib.md5(safe_constraint.encode('utf-8', errors='replace')).hexdigest()[:4]
                    kind_part += f'_c{constraint_hash}'
                except Exception:
                    # 如果约束处理失败，忽略约束
                    pass
            
            kind_parts.append(kind_part)
        
        return '|'.join(kind_parts)

    def _generate_template_content_signature(self, param_analysis: List[Dict[str, Any]]) -> str:
        """
        生成模板参数内容签名
        
        Args:
            param_analysis: 参数分析结果
            
        Returns:
            内容签名字符串
        """
        content_parts = []
        
        for param in param_analysis:
            content_part = str(param['name'])
            
            # 对于特化参数，包含完整内容
            if param['is_specialized']:
                content_part = str(param['raw_param'])
            
            # 安全处理默认值
            if param['default_value']:
                try:
                    default_str = str(param['default_value'])
                    # 移除特殊字符
                    safe_default = ''.join(char for char in default_str if char.isprintable())
                    default_hash = hashlib.md5(safe_default.encode('utf-8', errors='replace')).hexdigest()[:4]
                    content_part += f'_d{default_hash}'
                except Exception:
                    # 如果默认值处理失败，忽略默认值
                    pass
            
            # 确保content_part是安全的字符串
            safe_content = ''.join(char for char in content_part if char.isprintable())
            content_parts.append(safe_content)
        
        return '|'.join(content_parts)

    def generate_template_instantiation_usr(self, base_usr: str, instantiation_args: List[str]) -> str:
        """
        为模板实例化生成专用USR
        
        Args:
            base_usr: 基础模板USR
            instantiation_args: 实例化参数列表
            
        Returns:
            实例化USR
        """
        if not instantiation_args:
            return base_usr
        
        try:
            # 标准化实例化参数
            normalized_args = []
            for arg in instantiation_args:
                # 确保参数是字符串且安全
                if not isinstance(arg, str):
                    arg = str(arg)
                
                # 移除特殊字符
                safe_arg = ''.join(char for char in arg if char.isprintable() or char.isspace())
                normalized_arg = self._normalize_type_name_enhanced(safe_arg.strip())
                normalized_args.append(normalized_arg)
            
            # 生成实例化签名
            inst_signature = f"<{','.join(normalized_args)}>"
            
            # 安全编码
            try:
                inst_bytes = inst_signature.encode('utf-8', errors='replace')
            except UnicodeError:
                inst_bytes = inst_signature.encode('ascii', errors='ignore')
            
            inst_hash = hashlib.md5(inst_bytes).hexdigest()[:12]
            
            # 附加到基础USR
            return f"{base_usr}@inst_{inst_hash}"
            
        except Exception as e:
            # 如果处理失败，返回基础USR
            logging.warning(f"Template instantiation USR generation failed: {e}")
            return base_usr

    def generate_template_specialization_usr(self, base_usr: str, specialization_pattern: str) -> str:
        """
        为模板特化生成专用USR
        
        Args:
            base_usr: 基础模板USR
            specialization_pattern: 特化模式
            
        Returns:
            特化USR
        """
        if not specialization_pattern:
            return base_usr
        
        try:
            # 确保输入是安全的字符串
            if not isinstance(specialization_pattern, str):
                specialization_pattern = str(specialization_pattern)
            
            # 移除特殊字符
            safe_pattern = ''.join(char for char in specialization_pattern if char.isprintable() or char.isspace())
            
            # 标准化特化模式
            normalized_pattern = self._normalize_specialization_pattern(safe_pattern)
            
            # 安全编码
            try:
                pattern_bytes = normalized_pattern.encode('utf-8', errors='replace')
            except UnicodeError:
                pattern_bytes = normalized_pattern.encode('ascii', errors='ignore')
            
            # 生成特化签名
            spec_hash = hashlib.md5(pattern_bytes).hexdigest()[:12]
            
            return f"{base_usr}@spec_{spec_hash}"
            
        except Exception as e:
            # 如果处理失败，返回基础USR
            logging.warning(f"Template specialization USR generation failed: {e}")
            return base_usr

    def _normalize_specialization_pattern(self, pattern: str) -> str:
        """
        标准化模板特化模式
        
        Args:
            pattern: 特化模式字符串
            
        Returns:
            标准化后的模式
        """
        # 移除多余空格
        pattern = re.sub(r'\s+', ' ', pattern.strip())
        
        # 标准化模板参数格式
        pattern = re.sub(r'<\s+', '<', pattern)
        pattern = re.sub(r'\s+>', '>', pattern)
        pattern = re.sub(r',\s+', ',', pattern)
        
        # 标准化类型名
        if '<' in pattern and '>' in pattern:
            # 提取模板参数并标准化
            match = re.search(r'<([^>]+)>', pattern)
            if match:
                args_str = match.group(1)
                args = [arg.strip() for arg in args_str.split(',')]
                normalized_args = [self._normalize_type_name_enhanced(arg) for arg in args]
                normalized_template = f"<{','.join(normalized_args)}>"
                pattern = pattern[:match.start()] + normalized_template + pattern[match.end():]
        
        return pattern

    def generate_signature_key(self, entity_type: str, qualified_name: str, 
                             signature: Optional[str] = None, 
                             template_params: List[str] = None,
                             file_id: str = "") -> str:
        """
        生成符合v2.3规范的签名键值
        
        格式：{returnType}_{functionName}_{paramType1}_{paramType2}_..._{fileId}
        
        Args:
            entity_type: 实体类型
            qualified_name: 限定名
            signature: 函数签名
            template_params: 模板参数
            file_id: 文件ID
            
        Returns:
            签名键值字符串
        """
        if entity_type == 'function' and signature:
            return self._generate_function_signature_key(qualified_name, signature, file_id)
        elif entity_type in ['class', 'struct']:
            return self._generate_class_signature_key(qualified_name, file_id, template_params)
        else:
            # 其他类型使用简化格式
            clean_name = self._simplify_type_for_key(qualified_name)
            return f"{clean_name}_{file_id}" if file_id else clean_name

    def _generate_function_signature_key(self, qualified_name: str, signature: str, file_id: str) -> str:
        """生成函数签名键值"""
        # 解析函数名和返回类型
        func_name = qualified_name.split("::")[-1]  # 获取函数名
        
        # 从签名中提取返回类型（简化处理）
        return_type = "void"  # 默认返回类型
        
        # 解析参数类型
        match = re.search(r'\((.*?)\)', signature)
        param_types = []
        if match:
            params_str = match.group(1).strip()
            if params_str:
                params = self._parse_parameter_list_enhanced(params_str)
                param_types = [self._simplify_type_for_key(p) for p in params]
        
        # 构建签名键值
        key_parts = [
            self._simplify_type_for_key(return_type),
            func_name
        ]
        key_parts.extend(param_types)
        
        if file_id:
            key_parts.append(file_id)
        
        return '_'.join(key_parts)

    def _generate_class_signature_key(self, qualified_name: str, file_id: str, template_params: List[str] = None) -> str:
        """生成类签名键值"""
        clean_name = self._simplify_type_for_key(qualified_name)
        
        if template_params:
            template_str = '_'.join([self._simplify_type_for_key(p) for p in template_params])
            key_parts = [clean_name, template_str]
        else:
            key_parts = [clean_name]
        
        if file_id:
            key_parts.append(file_id)
        
        return '_'.join(key_parts)

    def _simplify_type_for_key(self, type_name: str) -> str:
        """简化类型名用于键值生成"""
        if not isinstance(type_name, str):
            type_name = str(type_name)
        
        # 移除空格和特殊字符
        simplified = re.sub(r'\s+', '', type_name)
        simplified = simplified.replace('::', '')
        simplified = simplified.replace('<', '')
        simplified = simplified.replace('>', '')
        simplified = simplified.replace('*', 'Ptr')
        simplified = simplified.replace('&', 'Ref')
        simplified = simplified.replace(',', '')
        simplified = simplified.replace('(', '')
        simplified = simplified.replace(')', '')
        simplified = simplified.replace('[', '')
        simplified = simplified.replace(']', '')
        
        # 处理常见类型缩写
        type_mapping = {
            'const': 'const',
            'unsigned': 'u',
            'long': 'l',
            'short': 's',
            'char': 'c',
            'int': 'i',
            'float': 'f',
            'double': 'd',
            'bool': 'b',
            'void': 'v',
            'string': 'str',
            'vector': 'vec',
            'map': 'map',
            'set': 'set',
            'list': 'list'
        }
        
        # 应用类型映射
        for original, abbreviated in type_mapping.items():
            simplified = simplified.replace(original, abbreviated)
        
        return simplified if simplified else "unknown"

    def register_entity(self, entity: Entity) -> str:
        """注册实体并返回USR ID"""
        if not entity.usr:
            signature = getattr(entity, 'signature', None)
            entity.usr = self.generate_usr(
                entity.type, 
                entity.qualified_name, 
                signature,
                entity.file_path
            )
        
        self.add_node(entity)
        return entity.usr

    def add_node(self, node: Entity):
        """添加一个新节点。如果已存在，则尝试合并信息。修复版：防止重要信息丢失。"""
        if not node.usr:
            # 在没有有效USR的情况下无法添加
            return

        with self._lock.write_lock(): # 使用写锁
            existing_node = self.nodes.get(node.usr)
            if existing_node:
                # 修复：增强合并逻辑，保护重要信息不丢失
                merged_node = self._smart_merge_nodes(existing_node, node)
                self.nodes[node.usr] = merged_node
            else:
                self.nodes[node.usr] = node
            
            # 更新索引
            self._update_indexes(self.nodes[node.usr])

    def _smart_merge_nodes(self, existing_node: Entity, new_node: Entity) -> Entity:
        """智能合并两个节点，保护重要信息不丢失 - 增强版字段级合并"""
        # 确定主节点（优先选择定义）
        if new_node.is_definition and not existing_node.is_definition:
            primary_node = new_node
            secondary_node = existing_node
        elif existing_node.is_definition and not new_node.is_definition:
            primary_node = existing_node  
            secondary_node = new_node
        else:
            # 如果都是定义或都是声明，优先选择内容更丰富的节点
            primary_node = new_node if self._node_content_score(new_node) >= self._node_content_score(existing_node) else existing_node
            secondary_node = existing_node if primary_node == new_node else new_node

        # 函数特有信息的字段级智能合并
        if isinstance(primary_node, Function) and isinstance(secondary_node, Function):
            self._merge_function_fields(primary_node, secondary_node)
        
        # 类特有信息的字段级智能合并
        elif isinstance(primary_node, Class) and isinstance(secondary_node, Class):
            self._merge_class_fields(primary_node, secondary_node)

        # 命名空间特有信息的字段级智能合并
        elif isinstance(primary_node, Namespace) and isinstance(secondary_node, Namespace):
            self._merge_namespace_fields(primary_node, secondary_node)

        # 合并通用Entity信息
        self._merge_entity_common_fields(primary_node, secondary_node)

        return primary_node
    
    def _merge_function_fields(self, primary: Function, secondary: Function):
        """函数字段的智能合并"""
        # 合并调用关系（去重）
        primary.calls_to = list(set(primary.calls_to + secondary.calls_to))
        primary.called_by = list(set(primary.called_by + secondary.called_by))
        
        # 合并调用详情（基于唯一键去重）
        if hasattr(primary, 'call_details') and hasattr(secondary, 'call_details'):
            existing_details = {(cd.to_usr_id, cd.line, cd.column) for cd in primary.call_details}
            for call_detail in secondary.call_details:
                key = (call_detail.to_usr_id, call_detail.line, call_detail.column)
                if key not in existing_details:
                    primary.call_details.append(call_detail)
        
        # 智能合并参数信息（选择更详细的）
        if not primary.parameters and secondary.parameters:
            primary.parameters = secondary.parameters
        elif secondary.parameters and len(secondary.parameters) > len(primary.parameters or []):
            # 如果secondary的参数更详细，使用secondary的
            primary.parameters = secondary.parameters
        elif (primary.parameters and secondary.parameters and 
              len(primary.parameters) == len(secondary.parameters)):
            # 参数数量相同时，合并参数信息（选择非空的字段）
            for i, (p_param, s_param) in enumerate(zip(primary.parameters, secondary.parameters)):
                if not p_param.get('name') and s_param.get('name'):
                    primary.parameters[i]['name'] = s_param['name']
                if not p_param.get('type') and s_param.get('type'):
                    primary.parameters[i]['type'] = s_param['type']
        
        # 智能合并返回类型（优先非空且更具体的）
        if not primary.return_type and secondary.return_type:
            primary.return_type = secondary.return_type
        elif secondary.return_type and len(secondary.return_type) > len(primary.return_type or ""):
            # 如果secondary的返回类型更具体，使用secondary的
            primary.return_type = secondary.return_type
        
        # 智能合并函数体内容（优先完整定义）
        if secondary.is_definition and secondary.code_content and not primary.code_content:
            primary.code_content = secondary.code_content
        elif secondary.code_content and len(secondary.code_content) > len(primary.code_content or ""):
            # 如果secondary的函数体更完整，使用secondary的
            primary.code_content = secondary.code_content
        
        # 保护复杂度信息（选择更大的值）
        if hasattr(secondary, 'complexity') and secondary.complexity > getattr(primary, 'complexity', 0):
            primary.complexity = secondary.complexity
        
        # 合并函数修饰符（OR逻辑）
        modifier_fields = ['is_virtual', 'is_pure_virtual', 'is_override', 'is_final', 'is_static', 'is_const']
        for field in modifier_fields:
            if hasattr(secondary, field) and getattr(secondary, field, False):
                setattr(primary, field, True)
    
    def _merge_class_fields(self, primary: Class, secondary: Class):
        """类字段的智能合并"""
        # 合并方法列表（去重）
        primary.methods = list(set(primary.methods + secondary.methods))
        
        # 合并字段列表（去重）
        if hasattr(primary, 'fields') and hasattr(secondary, 'fields'):
            primary_fields = getattr(primary, 'fields', []) or []
            secondary_fields = getattr(secondary, 'fields', []) or []
            primary.fields = list(set(primary_fields + secondary_fields))
        
        # 合并继承关系（去重）
        primary.base_classes = list(set(primary.base_classes + secondary.base_classes))
        primary.derived_classes = list(set(primary.derived_classes + secondary.derived_classes))
        
        # 合并模板信息
        if hasattr(secondary, 'template_parameters') and getattr(secondary, 'template_parameters'):
            if not getattr(primary, 'template_parameters', None):
                primary.template_parameters = secondary.template_parameters
        
        # 合并类修饰符（OR逻辑）
        class_modifier_fields = ['is_abstract', 'is_template']
        for field in class_modifier_fields:
            if hasattr(secondary, field) and getattr(secondary, field, False):
                setattr(primary, field, True)
    
    def _merge_namespace_fields(self, primary: Namespace, secondary: Namespace):
        """命名空间字段的智能合并"""
        # 合并子实体列表（去重）
        if hasattr(primary, 'children') and hasattr(secondary, 'children'):
            primary_children = getattr(primary, 'children', []) or []
            secondary_children = getattr(secondary, 'children', []) or []
            primary.children = list(set(primary_children + secondary_children))
        
        # 合并嵌套元素（去重）
        nested_fields = ['nested_namespaces', 'classes', 'functions', 'variables']
        for field in nested_fields:
            if hasattr(primary, field) and hasattr(secondary, field):
                primary_list = getattr(primary, field, []) or []
                secondary_list = getattr(secondary, field, []) or []
                setattr(primary, field, list(set(primary_list + secondary_list)))
        
        # 合并别名字典
        if hasattr(secondary, 'aliases') and getattr(secondary, 'aliases'):
            if not getattr(primary, 'aliases', None):
                primary.aliases = {}
            primary.aliases.update(secondary.aliases)
        
        # 合并using声明（去重）
        if hasattr(primary, 'using_declarations') and hasattr(secondary, 'using_declarations'):
            primary_using = getattr(primary, 'using_declarations', []) or []
            secondary_using = getattr(secondary, 'using_declarations', []) or []
            primary.using_declarations = list(set(primary_using + secondary_using))
    
    def _merge_entity_common_fields(self, primary: Entity, secondary: Entity):
        """合并Entity通用字段"""
        # 合并声明ID列表（去重）
        primary.declaration_ids = list(set(
            primary.declaration_ids + 
            secondary.declaration_ids + 
            [secondary.id]
        ))
        
        # 智能合并定义ID信息
        if secondary.definition_id:
            if not primary.definition_id:
                primary.definition_id = secondary.definition_id
            else:
                # 如果都有定义ID，添加到声明列表中
                primary.declaration_ids.append(secondary.definition_id)

        # 合并位置信息（只在Function、Class、Namespace等子类中处理，它们有这些属性）
        if hasattr(primary, 'declaration_locations') and hasattr(secondary, 'declaration_locations'):
            primary.declaration_locations.extend(secondary.declaration_locations)
        
        if (not getattr(primary, 'definition_location', None) and 
            hasattr(secondary, 'definition_location') and 
            getattr(secondary, 'definition_location', None)):
            primary.definition_location = secondary.definition_location
        
        # 合并声明/定义标志（只在有这些属性的子类中处理）
        if hasattr(primary, 'is_declaration') and hasattr(secondary, 'is_declaration'):
            primary.is_declaration = primary.is_declaration or secondary.is_declaration
        if hasattr(primary, 'is_definition') and hasattr(secondary, 'is_definition'):
            primary.is_definition = primary.is_definition or secondary.is_definition

    def _node_content_score(self, node: Entity) -> int:
        """计算节点内容丰富程度评分，用于合并时选择主节点"""
        score = 0
        
        # 基础评分
        if node.is_definition:
            score += 100
        
        # 函数特有评分
        if isinstance(node, Function):
            if getattr(node, 'code_content', ''):
                score += 50
            if getattr(node, 'calls_to', []):
                score += len(node.calls_to) * 2
            if getattr(node, 'parameters', []):
                score += len(node.parameters) * 3
            if getattr(node, 'return_type', ''):
                score += 10
            if getattr(node, 'complexity', 0) > 0:
                score += 20
        
        # 类特有评分
        elif isinstance(node, Class):
            if getattr(node, 'methods', []):
                score += len(node.methods) * 5
            if getattr(node, 'fields', []):
                score += len(getattr(node, 'fields', [])) * 3
            if getattr(node, 'base_classes', []):
                score += len(node.base_classes) * 8
        
        # 命名空间特有评分
        elif isinstance(node, Namespace):
            if hasattr(node, 'children') and getattr(node, 'children', []):
                score += len(node.children) * 2
        
        return score

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
        with self._lock.read_lock():
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
        """解析函数调用，返回被调用函数的USR ID - 增强版：更好地处理复杂调用"""
        # 去除可能的多余空格
        called_name = called_name.strip()
        if not called_name:
            return None
        
        # 构建可能的qualified names
        possible_names = [
            called_name,  # 直接名称
        ]
        
        # 添加上下文相关的名称
        if context_namespace:
            possible_names.append(f"{context_namespace}::{called_name}")
        if context_class:
            possible_names.append(f"{context_class}::{called_name}")
        if context_namespace and context_class:
            possible_names.append(f"{context_namespace}::{context_class}::{called_name}")
        
        # 使用qualified_name索引优先查找
        for name in possible_names:
            functions = self.find_by_qualified_name(name, 'function')
            if functions:
                # 优先返回定义而不是声明
                for func in functions:
                    if hasattr(func, 'is_definition') and func.is_definition:
                        return func.usr
                # 如果没有定义，返回第一个声明
                return functions[0].usr
        
        # 如果qualified索引查找失败，进行简单名称匹配（适合debug脚本中的成功案例）
        if '::' not in called_name:
            # 查找所有同名函数
            matching_functions = []
            for usr, entity in self.nodes.items():
                if (hasattr(entity, 'type') and entity.type == 'function' and 
                    hasattr(entity, 'name') and entity.name == called_name):
                    matching_functions.append(entity)
            
            if matching_functions:
                # 优先返回定义
                for func in matching_functions:
                    if hasattr(func, 'is_definition') and func.is_definition:
                        return func.usr
                # 如果没有定义，返回第一个
                return matching_functions[0].usr
        
        # 最后的策略：对于qualified name，尝试提取简单名称并匹配
        if '::' in called_name:
            simple_name = called_name.split('::')[-1]
            if simple_name and simple_name != called_name:
                # 递归调用，但只使用简单名称
                return self.resolve_function_call(simple_name, context_namespace, context_class)
        
        return None

    def add_call_relationship(self, caller_usr: str, callee_usr: str):
        """添加函数调用关系"""
        with self._lock.write_lock():
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
            caller = self.nodes.get(caller_usr)
            callee = self.nodes.get(callee_usr)
            
            if isinstance(caller, Function):
                if callee_usr not in caller.calls_to:
                    caller.calls_to.append(callee_usr)
            
            if isinstance(callee, Function):
                if caller_usr not in callee.called_by:
                    callee.called_by.append(caller_usr)

    def get_node(self, usr: str) -> Optional[Entity]:
        """通过USR获取一个节点"""
        with self._lock.read_lock():
            return self.nodes.get(usr)

    def get_all_nodes(self) -> List[Entity]:
        """获取所有节点"""
        with self._lock.read_lock():
            return list(self.nodes.values())

    def get_nodes_by_type(self, entity_type: str) -> List[Entity]:
        """获取指定类型的所有节点"""
        with self._lock.read_lock():
            return [node for node in self.nodes.values() if node.type == entity_type]

    def get_nodes_by_file(self, file_path: str) -> List[Entity]:
        """获取指定文件中的所有节点"""
        with self._lock.read_lock():
            usr_ids = self.file_index.get(file_path, [])
            return [self.nodes[usr_id] for usr_id in usr_ids if usr_id in self.nodes]

    def clear(self):
        """清空存储库"""
        with self._lock.write_lock():
            self.nodes.clear()
            self.qualified_name_index.clear()
            self.file_index.clear()
            self.call_relationships = {'calls_to': {}, 'called_by': {}}

    def get_statistics(self) -> Dict[str, Any]:
        """获取存储库统计信息"""
        with self._lock.read_lock():
            # 计算实际的调用关系总数，而不是有调用关系的函数数量
            total_call_relationships = 0
            for caller, callees in self.call_relationships['calls_to'].items():
                total_call_relationships += len(callees)
            
            stats = {
                'total_entities': len(self.nodes),
                'by_type': {},
                'call_relationships': total_call_relationships,  # 修复：计算实际调用关系总数
                'files_analyzed': len(self.file_index),
                'lock_performance': self._lock.get_lock_statistics()  # 添加锁性能统计
            }
            
            for entity in self.nodes.values():
                stats['by_type'][entity.type] = stats['by_type'].get(entity.type, 0) + 1
            
            return stats
    
    def get_lock_statistics(self) -> Dict[str, Any]:
        """获取详细的锁使用统计信息"""
        return self._lock.get_lock_statistics()

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

    def generate_lambda_usr(self, file_path: str, line: int, column: int, capture_signature: str = "") -> str:
        """
        为Lambda表达式生成唯一USR
        
        格式: c:@L@<file_id>@<line>@<col>[@capture_<hash>]
        
        Args:
            file_path: 文件路径
            line: 行号
            column: 列号
            capture_signature: 捕获列表签名（可选）
            
        Returns:
            Lambda USR
        """
        file_id = self._get_file_identifier(file_path)
        base_usr = f"c:@L@{file_id}@{line}@{column}"
        
        # 如果有捕获列表，添加捕获签名
        if capture_signature:
            capture_hash = hashlib.md5(capture_signature.encode('utf-8')).hexdigest()[:8]
            base_usr += f"@cap_{capture_hash}"
        
        return base_usr

    def generate_lambda_operator_call_usr(self, lambda_usr: str, operator_signature: str = "") -> str:
        """
        为Lambda的operator()生成USR
        
        Args:
            lambda_usr: Lambda表达式的USR
            operator_signature: operator()的签名
            
        Returns:
            Lambda operator() USR
        """
        if operator_signature:
            normalized_sig = self._normalize_function_signature_enhanced(operator_signature)
            return f"{lambda_usr}@op{normalized_sig}"
        
        return f"{lambda_usr}@op()"

    def generate_lambda_capture_info(self, capture_list_node: Optional[Any]) -> Dict[str, Any]:
        """
        分析Lambda捕获列表信息
        
        Args:
            capture_list_node: tree-sitter捕获列表节点
            
        Returns:
            捕获信息字典
        """
        capture_info = {
            'has_captures': False,
            'capture_default': None,  # None, 'by_copy', 'by_reference'
            'explicit_captures': [],
            'signature': ''
        }
        
        if not capture_list_node:
            return capture_info
        
        # 解析捕获列表的文本
        capture_text = self._extract_node_text(capture_list_node)
        if not capture_text or capture_text == '[]':
            return capture_info
        
        capture_info['has_captures'] = True
        
        # 移除方括号
        capture_content = capture_text.strip()[1:-1].strip()
        
        if not capture_content:
            return capture_info
        
        # 分析捕获模式
        if capture_content.startswith('='):
            capture_info['capture_default'] = 'by_copy'
            remaining = capture_content[1:].strip()
            if remaining.startswith(','):
                remaining = remaining[1:].strip()
        elif capture_content.startswith('&'):
            capture_info['capture_default'] = 'by_reference'
            remaining = capture_content[1:].strip()
            if remaining.startswith(','):
                remaining = remaining[1:].strip()
        else:
            remaining = capture_content
        
        # 解析显式捕获
        if remaining:
            captures = [c.strip() for c in remaining.split(',')]
            for capture in captures:
                if capture:
                    capture_info['explicit_captures'].append(self._parse_lambda_capture(capture))
        
        # 生成签名
        capture_info['signature'] = self._generate_capture_signature(capture_info)
        
        return capture_info

    def _parse_lambda_capture(self, capture_str: str) -> Dict[str, str]:
        """
        解析单个Lambda捕获项
        
        Args:
            capture_str: 捕获字符串
            
        Returns:
            捕获项信息
        """
        capture_info = {
            'name': '',
            'mode': 'by_copy',  # 'by_copy', 'by_reference', 'init_capture'
            'type': '',
            'init_expression': ''
        }
        
        capture_str = capture_str.strip()
        
        # 检查是否是引用捕获
        if capture_str.startswith('&'):
            capture_info['mode'] = 'by_reference'
            capture_str = capture_str[1:].strip()
        
        # 检查是否是初始化捕获 (C++14)
        if '=' in capture_str:
            parts = capture_str.split('=', 1)
            var_part = parts[0].strip()
            init_part = parts[1].strip()
            
            capture_info['mode'] = 'init_capture'
            capture_info['init_expression'] = init_part
            
            # 变量部分可能包含类型
            if ' ' in var_part:
                # 可能是 "auto x" 或 "int& y" 等
                tokens = var_part.split()
                capture_info['type'] = ' '.join(tokens[:-1])
                capture_info['name'] = tokens[-1]
            else:
                capture_info['name'] = var_part
        else:
            capture_info['name'] = capture_str
        
        return capture_info

    def _generate_capture_signature(self, capture_info: Dict[str, Any]) -> str:
        """
        生成捕获列表签名
        
        Args:
            capture_info: 捕获信息
            
        Returns:
            捕获签名字符串
        """
        if not capture_info['has_captures']:
            return ''
        
        sig_parts = []
        
        # 默认捕获模式
        if capture_info['capture_default']:
            sig_parts.append(capture_info['capture_default'])
        
        # 显式捕获
        for capture in capture_info['explicit_captures']:
            capture_sig = f"{capture['mode']}:{capture['name']}"
            if capture['type']:
                capture_sig += f"[{capture['type']}]"
            if capture['init_expression']:
                # 对初始化表达式进行哈希
                init_hash = hashlib.md5(capture['init_expression'].encode('utf-8')).hexdigest()[:4]
                capture_sig += f"={init_hash}"
            sig_parts.append(capture_sig)
        
        return '|'.join(sig_parts)

    def _extract_node_text(self, node: Any) -> str:
        """
        从tree-sitter节点提取文本
        
        Args:
            node: tree-sitter节点
            
        Returns:
            节点文本
        """
        if hasattr(node, 'text'):
            return node.text.decode('utf-8')
        return ''