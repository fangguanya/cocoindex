"""
Entity Extractor Module

Extracts C++ entities (functions, classes, variables, namespaces, templates)
from Clang AST and converts them to structured data matching the JSON format
specification.
"""

import re
from pathlib import Path
from typing import List, Dict, Set, Any, Optional
from dataclasses import dataclass, field
from .logger import get_logger

# 导入状态位掩码定义
class FunctionStatusFlags:
    """函数状态位掩码定义"""
    FUNC_IS_TEMPLATE = 1 << 0        # bit 0: 是否模板函数
    FUNC_IS_TEMPLATE_SPEC = 1 << 1   # bit 1: 是否模板特化
    FUNC_IS_VIRTUAL = 1 << 2         # bit 2: 是否虚函数
    FUNC_IS_PURE_VIRTUAL = 1 << 3    # bit 3: 是否纯虚函数
    FUNC_IS_OVERRIDE = 1 << 4        # bit 4: 是否重写函数
    FUNC_IS_FINAL = 1 << 5           # bit 5: 是否final函数
    FUNC_IS_STATIC = 1 << 6          # bit 6: 是否静态函数
    FUNC_IS_CONST = 1 << 7           # bit 7: 是否const函数
    FUNC_IS_NOEXCEPT = 1 << 8        # bit 8: 是否noexcept
    FUNC_IS_INLINE = 1 << 9          # bit 9: 是否内联函数
    FUNC_IS_CONSTEXPR = 1 << 10      # bit 10: 是否constexpr
    FUNC_IS_OPERATOR_OVERLOAD = 1 << 11  # bit 11: 是否操作符重载
    FUNC_IS_CONSTRUCTOR = 1 << 12    # bit 12: 是否构造函数
    FUNC_IS_DESTRUCTOR = 1 << 13     # bit 13: 是否析构函数
    FUNC_IS_COPY_CONSTRUCTOR = 1 << 14   # bit 14: 是否拷贝构造函数
    FUNC_IS_MOVE_CONSTRUCTOR = 1 << 15   # bit 15: 是否移动构造函数

class ClassStatusFlags:
    """类状态位掩码定义"""
    CLASS_IS_TEMPLATE = 1 << 0       # bit 0: 是否模板类
    CLASS_IS_TEMPLATE_SPEC = 1 << 1  # bit 1: 是否模板特化
    CLASS_IS_ABSTRACT = 1 << 2       # bit 2: 是否抽象类
    CLASS_IS_FINAL = 1 << 3          # bit 3: 是否final类
    CLASS_IS_POLYMORPHIC = 1 << 4    # bit 4: 是否多态类
    CLASS_HAS_VIRTUAL_DESTRUCTOR = 1 << 5  # bit 5: 是否有虚析构函数
    CLASS_IS_POD = 1 << 6            # bit 6: 是否POD类型
    CLASS_IS_TRIVIAL = 1 << 7        # bit 7: 是否trivial类型
    CLASS_IS_STANDARD_LAYOUT = 1 << 8    # bit 8: 是否标准布局
    CLASS_HAS_CUSTOM_CONSTRUCTOR = 1 << 9    # bit 9: 是否有自定义构造函数
    CLASS_HAS_CUSTOM_DESTRUCTOR = 1 << 10   # bit 10: 是否有自定义析构函数
    CLASS_HAS_COPY_CONSTRUCTOR = 1 << 11     # bit 11: 是否有拷贝构造函数
    CLASS_HAS_MOVE_CONSTRUCTOR = 1 << 12     # bit 12: 是否有移动构造函数
    CLASS_HAS_COPY_ASSIGNMENT = 1 << 13      # bit 13: 是否有拷贝赋值操作符
    CLASS_HAS_MOVE_ASSIGNMENT = 1 << 14      # bit 14: 是否有移动赋值操作符
    CLASS_IS_UNION = 1 << 15         # bit 15: 是否联合体

class SpecialMethodStatusFlags:
    """特殊方法状态位掩码定义"""
    SPECIAL_IS_DEFINED = 1 << 0      # bit 0: 是否已定义
    SPECIAL_IS_VIRTUAL = 1 << 1      # bit 1: 是否虚函数
    SPECIAL_IS_DELETED = 1 << 2      # bit 2: 是否被删除
    SPECIAL_IS_DEFAULTED = 1 << 3    # bit 3: 是否使用默认实现

class CallStatusFlags:
    """调用状态位掩码定义 - 按照json_format.md规范"""
    CALL_IS_VIRTUAL = 1 << 0         # bit 0: 是否虚函数调用
    CALL_IS_TEMPLATE_INST = 1 << 1   # bit 1: 是否模板实例化调用
    CALL_IS_OPERATOR = 1 << 2        # bit 2: 是否操作符调用
    CALL_IS_CONSTRUCTOR = 1 << 3     # bit 3: 是否构造函数调用
    CALL_IS_STATIC = 1 << 4          # bit 4: 是否静态函数调用

@dataclass
class ExtractedFunction:
    """提取的函数信息"""
    name: str
    qualified_name: str
    signature: str
    signature_key: str
    return_type: str
    parameters: List[Dict[str, Any]]
    definition_file_id: str
    declaration_file_id: str
    definition_line: int
    declaration_line: int
    function_status_flags: int = 0
    access_specifier: str = "public"
    storage_class: str = "none"
    template_parameters: List[str] = field(default_factory=list)
    exception_specification: str = ""
    attributes: List[str] = field(default_factory=list)
    mangled_name: str = ""
    documentation: str = ""

@dataclass
class ExtractedClass:
    """提取的类信息"""
    name: str
    qualified_name: str
    qualified_key: str
    kind: str  # "class", "struct", "union"
    definition_file_id: str
    declaration_file_id: str
    definition_line: int
    declaration_line: int
    class_status_flags: int = 0
    access_specifier: str = "public"
    template_parameters: List[str] = field(default_factory=list)
    inheritance_list: List[Dict[str, Any]] = field(default_factory=list)
    nested_types: List[str] = field(default_factory=list)
    friend_declarations: List[str] = field(default_factory=list)
    size_in_bytes: int = 0
    alignment: int = 0
    virtual_table_info: Dict[str, Any] = field(default_factory=dict)
    constructors: Dict[str, int] = field(default_factory=dict)  # type -> flags
    destructor: Dict[str, int] = field(default_factory=dict)    # info -> flags
    is_mixin: bool = False
    documentation: str = ""

@dataclass
class ExtractedNamespace:
    """提取的命名空间信息"""
    name: str
    qualified_name: str
    definition_file_id: str
    definition_line: int
    aliases: List[str] = field(default_factory=list)
    using_declarations: List[str] = field(default_factory=list)
    documentation: str = ""

class EntityExtractor:
    """实体提取器 - 支持完整状态位掩码和新架构"""
    
    def __init__(self):
        self.clang_parser = None
        self.file_mappings: Dict[str, str] = {}
        self.reverse_file_mappings: Dict[str, str] = {}
        
        # 提取的实体数据
        self.functions: Dict[str, ExtractedFunction] = {}
        self.classes: Dict[str, ExtractedClass] = {}
        self.namespaces: Dict[str, ExtractedNamespace] = {}
        self.includes: Dict[str, List[Dict[str, Any]]] = {}  # file_id -> include list
        self.typedefs: Dict[str, Dict[str, Any]] = {}
        self.forward_declarations: Dict[str, List[Dict[str, Any]]] = {}
        self.ast_macros: Dict[str, Dict[str, Any]] = {}  # AST中定义的宏
        
    def extract_from_files(self, parsed_files: List[Any], file_mappings: Dict[str, str], config=None) -> Dict[str, Any]:
        """从解析的文件中提取实体"""
        self.file_mappings = file_mappings
        # 创建反向映射：绝对路径 -> 文件ID
        self.reverse_file_mappings = {}
        for file_id, rel_path in file_mappings.items():
            # 直接使用解析文件的路径来匹配
            for parsed_file in parsed_files:
                if parsed_file.file_path:
                    abs_parsed_path = str(Path(parsed_file.file_path).resolve())
                    # 简化匹配：只要文件名相同就匹配
                    if Path(rel_path).name == Path(parsed_file.file_path).name:
                        self.reverse_file_mappings[abs_parsed_path] = file_id
                        break
        
        logger = get_logger()
        logger.debug(f"文件映射: {self.file_mappings}")
        logger.debug(f"反向文件映射: {self.reverse_file_mappings}")
        logger.debug(f"要处理的文件数: {len(parsed_files)}")
        
        # 清理之前的数据
        self.functions.clear()
        self.classes.clear()
        self.namespaces.clear()
        self.includes.clear()
        self.typedefs.clear()
        self.forward_declarations.clear()
        self.ast_macros.clear()
        
        # 处理每个解析的文件
        valid_files = 0
        invalid_files = 0
        
        for parsed_file in parsed_files:
            logger.debug(f"处理文件 {parsed_file.file_path}, 成功: {parsed_file.success}, 有翻译单元: {parsed_file.translation_unit is not None}")
            
            # 严格检查translation_unit的有效性
            if parsed_file.translation_unit and parsed_file.translation_unit.cursor:
                try:
                    logger.debug(f"开始提取实体 from {parsed_file.file_path}")
                    self._extract_from_cursor(
                        parsed_file.translation_unit.cursor,
                        parsed_file.file_path
                    )
                    logger.debug(f"完成提取实体 from {parsed_file.file_path}")
                    valid_files += 1
                except Exception as e:
                    logger.error(f"提取实体失败 {parsed_file.file_path}: {e}")
                    import traceback
                    traceback.print_exc()
                    invalid_files += 1
            else:
                logger.warning(f"跳过文件 {parsed_file.file_path} - translation_unit无效")
                invalid_files += 1
        
        logger.info(f"成功处理 {valid_files} 个文件，跳过 {invalid_files} 个无效文件")
        
        return {
            'functions': {key: self._function_to_dict(func) for key, func in self.functions.items()},
            'classes': {key: self._class_to_dict(cls) for key, cls in self.classes.items()},
            'namespaces': {key: self._namespace_to_dict(ns) for key, ns in self.namespaces.items()},
            'includes': self.includes,
            'typedefs': self.typedefs,
            'forward_declarations': self.forward_declarations,
            'ast_macros': self.ast_macros
        }
    
    def _extract_from_cursor(self, cursor, current_file_path: str):
        """递归提取游标信息"""
        # 只处理来自当前文件的定义
        if not self._is_from_current_file(cursor, current_file_path):
            # 但仍然需要递归处理子游标
            for child in cursor.get_children():
                self._extract_from_cursor(child, current_file_path)
            return
        
        logger = get_logger()
        try:
            # 导入clang模块
            import clang.cindex as clang
            
            # 添加调试信息
            if cursor.spelling:
                logger.debug(f"处理游标 {cursor.spelling}, 类型: {cursor.kind}, 文件: {current_file_path}")
            
            # 根据游标类型进行处理
            if cursor.kind == clang.CursorKind.FUNCTION_DECL:
                logger.entity_found("函数", cursor.spelling, current_file_path)
                self._extract_function(cursor, current_file_path)
            elif cursor.kind == clang.CursorKind.CXX_METHOD:
                logger.entity_found("方法", cursor.spelling, current_file_path)
                self._extract_method(cursor, current_file_path)
            elif cursor.kind == clang.CursorKind.CONSTRUCTOR:
                logger.entity_found("构造函数", cursor.spelling, current_file_path)
                self._extract_constructor(cursor, current_file_path)
            elif cursor.kind == clang.CursorKind.DESTRUCTOR:
                logger.entity_found("析构函数", cursor.spelling, current_file_path)
                self._extract_destructor(cursor, current_file_path)
            elif cursor.kind == clang.CursorKind.UNION_DECL:
                logger.entity_found("联合体", cursor.spelling, current_file_path)
                self._extract_class(cursor, current_file_path)
            elif cursor.kind == clang.CursorKind.NAMESPACE:
                logger.entity_found("命名空间", cursor.spelling, current_file_path)
                self._extract_namespace(cursor, current_file_path)
            elif cursor.kind == clang.CursorKind.INCLUSION_DIRECTIVE:
                logger.entity_found("include指令", cursor.spelling, current_file_path)
                self._extract_include(cursor, current_file_path)
            elif cursor.kind == clang.CursorKind.TYPEDEF_DECL:
                logger.entity_found("typedef", cursor.spelling, current_file_path)
                self._extract_typedef(cursor, current_file_path)
            elif cursor.kind == clang.CursorKind.MACRO_DEFINITION:
                logger.entity_found("宏定义", cursor.spelling, current_file_path)
                self._extract_macro_definition(cursor, current_file_path)
            elif cursor.kind in [clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL]:
                # 检查是否为前向声明
                if self._is_forward_declaration(cursor):
                    logger.entity_found("前向声明", cursor.spelling, current_file_path)
                    self._extract_forward_declaration(cursor, current_file_path)
                else:
                    logger.entity_found("类定义", cursor.spelling, current_file_path)
                    self._extract_class(cursor, current_file_path)
            
            # 递归处理子游标
            for child in cursor.get_children():
                self._extract_from_cursor(child, current_file_path)
                
        except Exception as e:
            logger.error(f"处理游标时出错: {e}")
    
    def _is_from_current_file(self, cursor, current_file_path: str) -> bool:
        """检查游标是否来自当前文件"""
        logger = get_logger()
        if not cursor.location or not cursor.location.file:
            # 对于没有位置信息的游标，允许进一步处理
            if cursor.spelling:
                logger.debug(f"游标 {cursor.spelling} 没有位置信息，允许处理")
            return True
        
        cursor_file = str(cursor.location.file)
        current_file = str(Path(current_file_path).resolve())
        
        # 添加调试信息
        is_match = cursor_file == current_file
        if cursor.spelling:
            if is_match:
                logger.debug(f"处理游标 {cursor.spelling} - 文件匹配: {cursor_file}")
            else:
                logger.debug(f"跳过游标 {cursor.spelling} - 文件不匹配: {cursor_file} != {current_file}")
        
        return is_match
    
    def _extract_function(self, cursor, current_file_path: str):
        """提取函数信息"""
        try:
            function = ExtractedFunction(
                name=cursor.spelling or "",
                qualified_name=self._get_qualified_name(cursor),
                signature=self._get_function_signature(cursor),
                signature_key="",  # 稍后生成
                return_type=cursor.result_type.spelling if cursor.result_type else "void",
                parameters=self._extract_parameters(cursor),
                definition_file_id=self._get_file_id(current_file_path),
                declaration_file_id=self._get_file_id(current_file_path),
                definition_line=cursor.location.line if cursor.location else 0,
                declaration_line=cursor.location.line if cursor.location else 0,
                function_status_flags=self._calculate_function_status_flags(cursor),
                access_specifier=self._get_access_specifier(cursor),
                storage_class=self._get_storage_class(cursor),
                template_parameters=self._extract_template_parameters(cursor),
                exception_specification=self._get_exception_spec(cursor),
                attributes=self._extract_attributes(cursor),
                mangled_name=self._get_mangled_name(cursor),
                documentation=self._extract_documentation(cursor)
            )
            
            # 生成签名键 - 按照json_format.md规范包含文件ID后缀
            function.signature_key = self._generate_function_signature_key(
                function.return_type,
                function.name,
                [p['type'] for p in function.parameters],
                function.definition_file_id
            )
            
            self.functions[function.signature_key] = function
            
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取函数失败 {cursor.spelling}: {e}")
    
    def _extract_method(self, cursor, current_file_path: str):
        """提取方法信息（类方法）"""
        # 方法处理与函数类似，但可能有额外的类相关信息
        self._extract_function(cursor, current_file_path)
    
    def _extract_constructor(self, cursor, current_file_path: str):
        """提取构造函数信息"""
        self._extract_function(cursor, current_file_path)
    
    def _extract_destructor(self, cursor, current_file_path: str):
        """提取析构函数信息"""
        self._extract_function(cursor, current_file_path)
    
    def _extract_class(self, cursor, current_file_path: str):
        """提取类信息"""
        try:
            import clang.cindex as clang
            
            # 确定类的类型
            kind_map = {
                clang.CursorKind.CLASS_DECL: "class",
                clang.CursorKind.STRUCT_DECL: "struct",
                clang.CursorKind.UNION_DECL: "union"
            }
            
            class_obj = ExtractedClass(
                name=cursor.spelling or "",
                qualified_name=self._get_qualified_name(cursor),
                qualified_key="",  # 稍后生成
                kind=kind_map.get(cursor.kind, "class"),
                definition_file_id=self._get_file_id(current_file_path),
                declaration_file_id=self._get_file_id(current_file_path),
                definition_line=cursor.location.line if cursor.location else 0,
                declaration_line=cursor.location.line if cursor.location else 0,
                class_status_flags=self._calculate_class_status_flags(cursor),
                access_specifier=self._get_access_specifier(cursor),
                template_parameters=self._extract_template_parameters(cursor),
                inheritance_list=self._extract_inheritance(cursor),
                nested_types=self._extract_nested_types(cursor),
                friend_declarations=self._extract_friend_declarations(cursor),
                size_in_bytes=self._get_class_size(cursor),
                alignment=self._get_class_alignment(cursor),
                virtual_table_info=self._extract_virtual_table_info(cursor),
                constructors=self._extract_constructors_info(cursor),
                destructor=self._extract_destructor_info(cursor),
                is_mixin=self._is_mixin_class(cursor),
                documentation=self._extract_documentation(cursor)
            )
            
            # 生成限定键
            class_obj.qualified_key = f"{class_obj.qualified_name}_{class_obj.definition_file_id}"
            
            self.classes[class_obj.qualified_key] = class_obj
            
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取类失败 {cursor.spelling}: {e}")
    
    def _extract_namespace(self, cursor, current_file_path: str):
        """提取命名空间信息"""
        try:
            namespace = ExtractedNamespace(
                name=cursor.spelling or "",
                qualified_name=self._get_qualified_name(cursor),
                definition_file_id=self._get_file_id(current_file_path),
                definition_line=cursor.location.line if cursor.location else 0,
                aliases=self._extract_namespace_aliases(cursor),
                using_declarations=self._extract_using_declarations(cursor),
                documentation=self._extract_documentation(cursor)
            )
            
            # 使用限定名作为键
            key = f"{namespace.qualified_name}_{namespace.definition_file_id}"
            self.namespaces[key] = namespace
            
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取命名空间失败 {cursor.spelling}: {e}")
    
    def _calculate_function_status_flags(self, cursor) -> int:
        """计算函数状态位掩码"""
        flags = 0
        
        try:
            import clang.cindex as clang
            
            # 检查模板函数
            if self._is_template_function(cursor):
                flags |= FunctionStatusFlags.FUNC_IS_TEMPLATE
            
            # 检查模板特化
            if self._is_template_specialization(cursor):
                flags |= FunctionStatusFlags.FUNC_IS_TEMPLATE_SPEC
            
            # 检查虚函数
            if hasattr(cursor, 'is_virtual_method') and cursor.is_virtual_method():
                flags |= FunctionStatusFlags.FUNC_IS_VIRTUAL
                
                # 检查纯虚函数
                if hasattr(cursor, 'is_pure_virtual_method') and cursor.is_pure_virtual_method():
                    flags |= FunctionStatusFlags.FUNC_IS_PURE_VIRTUAL
            
            # 检查override和final (通过token检查)
            if self._has_override_specifier(cursor):
                flags |= FunctionStatusFlags.FUNC_IS_OVERRIDE
            
            if self._has_final_specifier(cursor):
                flags |= FunctionStatusFlags.FUNC_IS_FINAL
            
            # 检查静态方法
            if hasattr(cursor, 'is_static_method') and cursor.is_static_method():
                flags |= FunctionStatusFlags.FUNC_IS_STATIC
            
            # 检查const方法
            if hasattr(cursor, 'is_const_method') and cursor.is_const_method():
                flags |= FunctionStatusFlags.FUNC_IS_CONST
            
            # 检查noexcept
            if self._has_noexcept_specifier(cursor):
                flags |= FunctionStatusFlags.FUNC_IS_NOEXCEPT
            
            # 检查inline
            if self._is_inline_function(cursor):
                flags |= FunctionStatusFlags.FUNC_IS_INLINE
            
            # 检查constexpr
            if self._is_constexpr_function(cursor):
                flags |= FunctionStatusFlags.FUNC_IS_CONSTEXPR
            
            # 检查构造函数类型
            if cursor.kind == clang.CursorKind.CONSTRUCTOR:
                flags |= FunctionStatusFlags.FUNC_IS_CONSTRUCTOR
                
                # 检查拷贝/移动构造函数
                if self._is_copy_constructor(cursor):
                    flags |= FunctionStatusFlags.FUNC_IS_COPY_CONSTRUCTOR
                elif self._is_move_constructor(cursor):
                    flags |= FunctionStatusFlags.FUNC_IS_MOVE_CONSTRUCTOR
                    
            elif cursor.kind == clang.CursorKind.DESTRUCTOR:
                flags |= FunctionStatusFlags.FUNC_IS_DESTRUCTOR
            
            # 检查操作符重载
            if 'operator' in (cursor.spelling or ""):
                flags |= FunctionStatusFlags.FUNC_IS_OPERATOR_OVERLOAD
            
        except Exception as e:
            logger = get_logger()
            logger.error(f"计算失败: {e}")
        
        return flags
    
    def _calculate_class_status_flags(self, cursor) -> int:
        """计算类状态位掩码"""
        flags = 0
        
        try:
            import clang.cindex as clang
            
            # 定义结构体标志常量
            CLASS_IS_STRUCT = 1 << 16
            
            # 检查模板类
            if self._is_template_class(cursor):
                flags |= ClassStatusFlags.CLASS_IS_TEMPLATE
            
            # 检查模板特化
            if self._is_template_specialization(cursor):
                flags |= ClassStatusFlags.CLASS_IS_TEMPLATE_SPEC
            
            # 检查类类型
            if cursor.kind == clang.CursorKind.UNION_DECL:
                flags |= ClassStatusFlags.CLASS_IS_UNION
            elif cursor.kind == clang.CursorKind.STRUCT_DECL:
                flags |= CLASS_IS_STRUCT
            
            # 检查抽象类（有纯虚函数）
            if self._is_abstract_class(cursor):
                flags |= ClassStatusFlags.CLASS_IS_ABSTRACT
            
            # 检查final类
            if self._has_final_specifier(cursor):
                flags |= ClassStatusFlags.CLASS_IS_FINAL
            
            # 检查多态类（有虚函数）
            if self._is_polymorphic_class(cursor):
                flags |= ClassStatusFlags.CLASS_IS_POLYMORPHIC
            
            # 检查虚析构函数
            if self._has_virtual_destructor(cursor):
                flags |= ClassStatusFlags.CLASS_HAS_VIRTUAL_DESTRUCTOR
            
            # 检查POD类型 (Plain Old Data)
            if self._is_pod_class(cursor):
                flags |= ClassStatusFlags.CLASS_IS_POD
            
            # 检查trivial类型
            if self._is_trivial_class(cursor):
                flags |= ClassStatusFlags.CLASS_IS_TRIVIAL
            
            # 检查构造函数/析构函数相关标志
            has_custom_constructor = False
            has_custom_destructor = False
            has_copy_constructor = False
            has_move_constructor = False
            has_copy_assignment = False
            has_move_assignment = False
            
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.CONSTRUCTOR:
                    has_custom_constructor = True
                    if self._is_copy_constructor(child):
                        has_copy_constructor = True
                    elif self._is_move_constructor(child):
                        has_move_constructor = True
                elif child.kind == clang.CursorKind.DESTRUCTOR:
                    has_custom_destructor = True
                elif child.kind == clang.CursorKind.CXX_METHOD:
                    if 'operator=' in (child.spelling or ""):
                        # 简化检查赋值操作符
                        if '&' in str(child.type.spelling if child.type else ""):
                            has_copy_assignment = True
                        elif '&&' in str(child.type.spelling if child.type else ""):
                            has_move_assignment = True
            
            if has_custom_constructor:
                flags |= ClassStatusFlags.CLASS_HAS_CUSTOM_CONSTRUCTOR
            if has_custom_destructor:
                flags |= ClassStatusFlags.CLASS_HAS_CUSTOM_DESTRUCTOR
            if has_copy_constructor:
                flags |= ClassStatusFlags.CLASS_HAS_COPY_CONSTRUCTOR
            if has_move_constructor:
                flags |= ClassStatusFlags.CLASS_HAS_MOVE_CONSTRUCTOR
            if has_copy_assignment:
                flags |= ClassStatusFlags.CLASS_HAS_COPY_ASSIGNMENT
            if has_move_assignment:
                flags |= ClassStatusFlags.CLASS_HAS_MOVE_ASSIGNMENT
            
        except Exception as e:
            logger = get_logger()
            logger.error(f"计算失败: {e}")
        
        return flags
    
    # 工具方法
    def _get_qualified_name(self, cursor) -> str:
        """获取限定名称"""
        try:
            import clang.cindex as clang
            
            names = []
            current = cursor
            
            while current and current.kind != clang.CursorKind.TRANSLATION_UNIT:
                if current.spelling:
                    names.append(current.spelling)
                current = current.semantic_parent
            
            return "::".join(reversed(names))
        except:
            return cursor.spelling or ""
    
    def _get_function_signature(self, cursor) -> str:
        """获取函数签名"""
        try:
            # 获取返回类型
            result_type = cursor.result_type.spelling if cursor.result_type else "void"
            
            # 获取函数名
            name = cursor.spelling
            
            # 获取参数
            params = []
            for arg in cursor.get_arguments():
                param_type = arg.type.spelling if arg.type else "unknown"
                param_name = arg.spelling if arg.spelling else ""
                if param_name:
                    params.append(f"{param_type} {param_name}")
                else:
                    params.append(param_type)
            
            param_str = ", ".join(params)
            return f"{result_type} {name}({param_str})"
        except:
            return cursor.spelling or ""
    
    def _generate_function_signature_key(self, return_type: str, name: str, param_types: List[str], file_id: str) -> str:
        """生成函数签名键 - 严格按照json_format.md规范"""
        # 按照规范格式: {returnType}_{functionName}_{paramType1}_{paramType2}_..._{fileId}
        simplified_return = self._simplify_type_name(return_type)
        simplified_params = [self._simplify_type_name(param) for param in param_types]
        
        # 构建键 - 确保包含文件ID后缀
        parts = [simplified_return, name] + simplified_params + [file_id]
        return "_".join(filter(None, parts))  # 过滤空字符串
    
    def _simplify_type_name(self, type_name: str) -> str:
        """简化类型名称 - 按照json_format.md表格规则"""
        if not type_name:
            return "void"
        
        # 按照规范表格的简化规则
        simplified = type_name.strip()
        
        # 特殊规则映射
        type_mappings = {
            'const std::string&': 'constStdStringRef',
            'std::vector<int>': 'StdVectorInt',
            'const char*': 'constCharPtr',
            'unsigned long long': 'unsignedLongLong'
        }
        
        # 先检查完整匹配
        if simplified in type_mappings:
            return type_mappings[simplified]
        
        # 通用规则处理
        simplified = re.sub(r'\s+', '', simplified)  # 移除所有空格
        simplified = simplified.replace('::', '')  # MyNamespace::MyClass -> MyNamespaceMyClass
        simplified = simplified.replace('<', '')   # 移除模板参数符号
        simplified = simplified.replace('>', '')
        simplified = simplified.replace(',', '')
        simplified = simplified.replace('*', 'Ptr')  # 指针后缀
        simplified = simplified.replace('&', 'Ref')  # 引用后缀
        
        return simplified or "void"
    
    def _get_file_id(self, file_path: str) -> str:
        """获取文件ID"""
        normalized_path = str(Path(file_path).resolve())
        return self.reverse_file_mappings.get(normalized_path, "f000")
    
    # 以下是需要实现的辅助方法（目前返回默认值）
    def _extract_parameters(self, cursor) -> List[Dict[str, Any]]:
        """提取参数信息"""
        params = []
        try:
            for arg in cursor.get_arguments():
                param = {
                    'name': arg.spelling or "",
                    'type': arg.type.spelling if arg.type else "unknown",
                    'default_value': self._get_default_value(arg),
                    'is_const': self._is_const_member(arg),
                    'is_reference': '&' in (arg.type.spelling if arg.type else ""),
                    'is_pointer': '*' in (arg.type.spelling if arg.type else "")
                }
                params.append(param)
        except:
            pass
        return params
    
    def _get_access_specifier(self, cursor) -> str:
        """获取访问说明符"""
        try:
            import clang.cindex as clang
            access = cursor.access_specifier
            
            if access == clang.AccessSpecifier.PUBLIC:
                return "public"
            elif access == clang.AccessSpecifier.PROTECTED:
                return "protected"
            elif access == clang.AccessSpecifier.PRIVATE:
                return "private"
            else:
                return "public"  # 默认public
        except Exception:
            return "public"
    
    def _get_storage_class(self, cursor) -> str:
        """获取存储类"""
        try:
            import clang.cindex as clang
            storage = cursor.storage_class
            
            if storage == clang.StorageClass.NONE:
                return "none"
            elif storage == clang.StorageClass.EXTERN:
                return "extern"
            elif storage == clang.StorageClass.STATIC:
                return "static"
            elif hasattr(clang.StorageClass, 'PRIVATE_EXTERN') and storage == clang.StorageClass.PRIVATE_EXTERN:
                return "private_extern"
            elif storage == clang.StorageClass.AUTO:
                return "auto"
            elif storage == clang.StorageClass.REGISTER:
                return "register"
            else:
                return "none"
        except Exception:
                         return "none"
    
    def _is_mixin_class(self, cursor) -> bool:
        """检测mixin模式"""
        try:
            import clang.cindex as clang
            
            # Mixin类的特征：
            # 1. 通常是模板类
            # 2. 有虚函数但没有虚析构函数（不是基类）
            # 3. 通常没有数据成员，只有行为
            
            is_template = False
            has_virtual_methods = False
            has_virtual_destructor = False
            has_data_members = False
            
            # 检查是否为模板
            if cursor.kind == clang.CursorKind.CLASS_TEMPLATE:
                is_template = True
            
            # 检查成员
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.CXX_METHOD:
                    if child.is_virtual_method():
                        has_virtual_methods = True
                elif child.kind == clang.CursorKind.DESTRUCTOR:
                    if child.is_virtual_method():
                        has_virtual_destructor = True
                elif child.kind == clang.CursorKind.FIELD_DECL:
                    has_data_members = True
            
            # 简化的mixin检测：模板类 + 虚函数 - 虚析构 - 数据成员
            return is_template and has_virtual_methods and not has_virtual_destructor and not has_data_members
            
        except Exception:
            return False
    
    def _extract_template_parameters(self, cursor) -> List[str]:
        """提取模板参数"""
        template_params = []
        try:
            import clang.cindex as clang
            
            # 查找模板参数
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.TEMPLATE_TYPE_PARAMETER:
                    # 类型模板参数
                    param_name = child.spelling or f"T{len(template_params)}"
                    template_params.append(f"typename {param_name}")
                elif child.kind == clang.CursorKind.TEMPLATE_NON_TYPE_PARAMETER:
                    # 非类型模板参数
                    param_type = child.type.spelling if child.type else "int"
                    param_name = child.spelling or f"N{len(template_params)}"
                    template_params.append(f"{param_type} {param_name}")
                elif child.kind == clang.CursorKind.TEMPLATE_TEMPLATE_PARAMETER:
                    # 模板模板参数
                    param_name = child.spelling or f"TT{len(template_params)}"
                    template_params.append(f"template<class> class {param_name}")
            
            # 如果当前游标是模板特化，也要提取特化参数
            if hasattr(cursor, 'get_template_arguments'):
                try:
                    for i in range(cursor.get_template_arguments().count()):
                        arg = cursor.get_template_arguments().get(i)
                        if arg:
                            template_params.append(str(arg))
                except:
                    pass
                    
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败: {e}")
        
        return template_params
    
    def _get_exception_spec(self, cursor) -> str:
        """获取异常规范"""
        try:
            import clang.cindex as clang
            
            # 检查函数类型的异常规范
            if hasattr(cursor, 'type') and cursor.type:
                type_spelling = cursor.type.spelling
                
                # 检查noexcept规范
                if 'noexcept' in type_spelling:
                    if 'noexcept(' in type_spelling:
                        # 条件noexcept，提取条件
                        start = type_spelling.find('noexcept(') + 9
                        depth = 1
                        end = start
                        for i, char in enumerate(type_spelling[start:], start):
                            if char == '(':
                                depth += 1
                            elif char == ')':
                                depth -= 1
                                if depth == 0:
                                    end = i
                                    break
                        condition = type_spelling[start:end]
                        return f"noexcept({condition})"
                    else:
                        return "noexcept"
                
                # 检查传统的throw规范
                if 'throw(' in type_spelling:
                    start = type_spelling.find('throw(') + 6
                    end = type_spelling.find(')', start)
                    if end != -1:
                        exceptions = type_spelling[start:end].strip()
                        if not exceptions:
                            return "throw()"  # 不抛出异常
                        else:
                            return f"throw({exceptions})"
            
            # 通过token检查异常规范
            tokens = list(cursor.get_tokens())
            for i, token in enumerate(tokens):
                if token.spelling == 'noexcept':
                    if i + 1 < len(tokens) and tokens[i + 1].spelling == '(':
                        # 查找匹配的右括号
                        depth = 1
                        condition_tokens = []
                        for j in range(i + 2, len(tokens)):
                            if tokens[j].spelling == '(':
                                depth += 1
                            elif tokens[j].spelling == ')':
                                depth -= 1
                                if depth == 0:
                                    break
                            condition_tokens.append(tokens[j].spelling)
                        condition = ''.join(condition_tokens)
                        return f"noexcept({condition})"
                    else:
                        return "noexcept"
                elif token.spelling == 'throw':
                    if i + 1 < len(tokens) and tokens[i + 1].spelling == '(':
                        # 查找throw规范
                        depth = 1
                        exception_tokens = []
                        for j in range(i + 2, len(tokens)):
                            if tokens[j].spelling == '(':
                                depth += 1
                            elif tokens[j].spelling == ')':
                                depth -= 1
                                if depth == 0:
                                    break
                            exception_tokens.append(tokens[j].spelling)
                        exceptions = ''.join(exception_tokens)
                        return f"throw({exceptions})"
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败: {e}")
        
        return ""
    
    def _get_mangled_name(self, cursor) -> str:
        """获取mangled name"""
        try:
            import clang.cindex as clang
            # 尝试获取mangled name
            if hasattr(cursor, 'mangled_name') and cursor.mangled_name:
                return cursor.mangled_name
            
            # 对于C++函数，通过linkage获取mangled name
            if cursor.linkage == clang.LinkageKind.EXTERNAL:
                # 获取USR (Unified Symbol Resolution)
                usr = cursor.get_usr()
                if usr:
                    return usr
            
            return ""
        except Exception as e:
            logger = get_logger()
            logger.error(f"获取失败: {e}")
            return ""
    
    def _get_class_size(self, cursor) -> int:
        """计算类大小"""
        try:
            import clang.cindex as clang
            # 尝试获取类型大小
            if cursor.type and cursor.type.get_size() >= 0:
                return cursor.type.get_size()
            return 0
        except Exception:
            return 0
    
    def _get_class_alignment(self, cursor) -> int:
        """计算类对齐"""
        try:
            import clang.cindex as clang
            # 尝试获取类型对齐
            if cursor.type and cursor.type.get_align() >= 0:
                return cursor.type.get_align()
            return 0
        except Exception:
            return 0
    
    def _extract_virtual_table_info(self, cursor) -> Dict[str, Any]:
        """提取虚表信息"""
        vtable_info = {
            "has_vtable": False,
            "virtual_methods": [],
            "pure_virtual_methods": []
        }
        
        try:
            import clang.cindex as clang
            
            # 遍历类的方法查找虚函数
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.CXX_METHOD:
                    if child.is_virtual_method():
                        vtable_info["has_vtable"] = True
                        method_info = {
                            "name": child.spelling,
                            "qualified_name": child.displayname,
                            "is_pure": child.is_pure_virtual_method()
                        }
                        
                        if child.is_pure_virtual_method():
                            vtable_info["pure_virtual_methods"].append(method_info)
                        else:
                            vtable_info["virtual_methods"].append(method_info)
        
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败: {e}")
        
        return vtable_info
    
    def _get_default_value(self, cursor) -> str:
        """获取默认值"""
        try:
            # 尝试从token中提取默认值
            tokens = list(cursor.get_tokens())
            for i, token in enumerate(tokens):
                if token.spelling == '=' and i + 1 < len(tokens):
                    # 收集等号后的token直到分号或逗号
                    default_tokens = []
                    for j in range(i + 1, len(tokens)):
                        if tokens[j].spelling in [';', ',', ')', '}']:
                            break
                        default_tokens.append(tokens[j].spelling)
                    return ''.join(default_tokens).strip()
            return ""
        except Exception:
            return ""
    
    def _is_const_member(self, cursor) -> bool:
        """检查是否为const成员"""
        try:
            if cursor.type:
                return cursor.type.is_const_qualified()
            return False
        except Exception:
            return False
    
    def _extract_attributes(self, cursor) -> List[str]:
        """提取属性"""
        attributes = []
        try:
            import clang.cindex as clang
            
            # 遍历子游标查找属性
            for child in cursor.get_children():
                # 只使用确实存在的属性类型
                valid_attr_kinds = []
                for attr_name in ['ANNOTATE_ATTR', 'ALIGNED_ATTR', 'PACKED_ATTR', 'PURE_ATTR', 'CONST_ATTR']:
                    if hasattr(clang.CursorKind, attr_name):
                        valid_attr_kinds.append(getattr(clang.CursorKind, attr_name))
                
                if child.kind in valid_attr_kinds:
                    attr_name = child.spelling or str(child.kind).split('.')[-1].lower().replace('_attr', '')
                    attributes.append(attr_name)
            
            # 通过token检查常见的属性关键字
            tokens = list(cursor.get_tokens())
            attribute_keywords = [
                '__attribute__', '__declspec', 'alignas', 'deprecated',
                'noreturn', 'carries_dependency', 'fallthrough', 'nodiscard',
                'maybe_unused', 'likely', 'unlikely'
            ]
            
            for i, token in enumerate(tokens):
                if token.spelling in attribute_keywords:
                    if token.spelling == '__attribute__':
                        # GNU风格属性
                        if i + 1 < len(tokens) and tokens[i + 1].spelling == '(':
                            # 查找属性内容
                            depth = 1
                            attr_tokens = []
                            for j in range(i + 2, len(tokens)):
                                if tokens[j].spelling == '(':
                                    depth += 1
                                elif tokens[j].spelling == ')':
                                    depth -= 1
                                    if depth == 0:
                                        break
                                attr_tokens.append(tokens[j].spelling)
                            attr_content = ''.join(attr_tokens)
                            if attr_content:
                                attributes.append(f"__attribute__(({attr_content}))")
                    elif token.spelling == '__declspec':
                        # MSVC风格属性
                        if i + 1 < len(tokens) and tokens[i + 1].spelling == '(':
                            depth = 1
                            attr_tokens = []
                            for j in range(i + 2, len(tokens)):
                                if tokens[j].spelling == '(':
                                    depth += 1
                                elif tokens[j].spelling == ')':
                                    depth -= 1
                                    if depth == 0:
                                        break
                                attr_tokens.append(tokens[j].spelling)
                            attr_content = ''.join(attr_tokens)
                            if attr_content:
                                attributes.append(f"__declspec({attr_content})")
                    elif token.spelling == 'alignas':
                        # C++11 alignas属性
                        if i + 1 < len(tokens) and tokens[i + 1].spelling == '(':
                            depth = 1
                            attr_tokens = []
                            for j in range(i + 2, len(tokens)):
                                if tokens[j].spelling == '(':
                                    depth += 1
                                elif tokens[j].spelling == ')':
                                    depth -= 1
                                    if depth == 0:
                                        break
                                attr_tokens.append(tokens[j].spelling)
                            attr_content = ''.join(attr_tokens)
                            if attr_content:
                                attributes.append(f"alignas({attr_content})")
                    else:
                        # 简单属性
                        attributes.append(token.spelling)
            
            # 检查C++标准属性 [[attr]]
            for i, token in enumerate(tokens):
                if token.spelling == '[[':
                    attr_tokens = []
                    for j in range(i + 1, len(tokens)):
                        if tokens[j].spelling == ']]':
                            break
                        attr_tokens.append(tokens[j].spelling)
                    attr_content = ''.join(attr_tokens)
                    if attr_content:
                        attributes.append(f"[[{attr_content}]]")
                        
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败: {e}")
        
        return list(set(attributes))  # 去重
    
    def _extract_documentation(self, cursor) -> str:
        """提取文档"""
        try:
            import clang.cindex as clang
            
            # 尝试获取原始注释
            if hasattr(cursor, 'raw_comment') and cursor.raw_comment:
                return cursor.raw_comment.strip()
            
            # 尝试获取简短注释
            if hasattr(cursor, 'brief_comment') and cursor.brief_comment:
                return cursor.brief_comment.strip()
                
            # 手动查找相邻的注释
            if cursor.location and cursor.location.file:
                try:
                    # 读取源文件内容
                    source_file = str(cursor.location.file)
                    with open(source_file, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                    
                    cursor_line = cursor.location.line - 1  # 转换为0索引
                    
                    # 查找上方的注释
                    comments = []
                    
                    # 向上查找连续的注释行
                    for i in range(cursor_line - 1, -1, -1):
                        if i < len(lines):
                            line = lines[i].strip()
                            if line.startswith('///') or line.startswith('//!'):
                                # Doxygen风格注释
                                comment_text = line[3:].strip()
                                comments.insert(0, comment_text)
                            elif line.startswith('//'):
                                # 普通注释
                                comment_text = line[2:].strip()
                                comments.insert(0, comment_text)
                            elif line.startswith('/*') and line.endswith('*/'):
                                # 单行块注释
                                comment_text = line[2:-2].strip()
                                comments.insert(0, comment_text)
                            elif line.startswith('/**') and line.endswith('*/'):
                                # 单行Doxygen块注释
                                comment_text = line[3:-2].strip()
                                comments.insert(0, comment_text)
                            elif line.startswith('/*') or line.startswith('/**'):
                                # 多行块注释开始
                                block_comments = []
                                is_doxygen = line.startswith('/**')
                                
                                # 收集整个块注释
                                for j in range(i, len(lines)):
                                    block_line = lines[j].strip()
                                    if j == i:
                                        # 第一行
                                        if block_line.endswith('*/'):
                                            # 单行块注释
                                            prefix = '/**' if is_doxygen else '/*'
                                            comment_text = block_line[len(prefix):-2].strip()
                                            if comment_text:
                                                block_comments.append(comment_text)
                                            break
                                        else:
                                            # 多行块注释开始
                                            prefix = '/**' if is_doxygen else '/*'
                                            comment_text = block_line[len(prefix):].strip()
                                            if comment_text:
                                                block_comments.append(comment_text)
                                    elif block_line.endswith('*/'):
                                        # 最后一行
                                        comment_text = block_line[:-2].strip()
                                        if comment_text.startswith('*'):
                                            comment_text = comment_text[1:].strip()
                                        if comment_text:
                                            block_comments.append(comment_text)
                                        break
                                    else:
                                        # 中间行
                                        comment_text = block_line
                                        if comment_text.startswith('*'):
                                            comment_text = comment_text[1:].strip()
                                        if comment_text:
                                            block_comments.append(comment_text)
                                
                                if block_comments:
                                    comments = block_comments + comments
                                break
                            elif line and not line.startswith('#'):
                                # 非空非预处理指令行，停止查找
                                break
                    
                    # 也检查同一行的注释
                    if cursor_line < len(lines):
                        line = lines[cursor_line]
                        if '//' in line:
                            comment_start = line.find('//')
                            comment_text = line[comment_start + 2:].strip()
                            if comment_text:
                                comments.append(comment_text)
                    
                    if comments:
                        return '\n'.join(comments).strip()
                        
                except Exception as e:
                    logger = get_logger()
                    logger.error(f"读取失败: {e}")
                    
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败: {e}")
        
        return ""
    
    def _extract_inheritance(self, cursor) -> List[Dict[str, Any]]:
        """提取继承信息"""
        inheritance_list = []
        try:
            import clang.cindex as clang
            
            # 遍历子游标查找基类
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.CXX_BASE_SPECIFIER:
                    base_class_info = {
                        'base_class': '',
                        'access_specifier': 'private',  # 默认为private
                        'is_virtual': False,
                        'is_pack_expansion': False
                    }
                    
                    # 获取基类名称
                    if child.type:
                        base_class_info['base_class'] = child.type.spelling
                    
                    # 获取访问说明符
                    if hasattr(child, 'access_specifier'):
                        access_map = {
                            clang.AccessSpecifier.PUBLIC: 'public',
                            clang.AccessSpecifier.PROTECTED: 'protected',
                            clang.AccessSpecifier.PRIVATE: 'private'
                        }
                        base_class_info['access_specifier'] = access_map.get(
                            child.access_specifier, 'private'
                        )
                    
                    # 检查是否为虚继承
                    if hasattr(child, 'is_virtual_base') and child.is_virtual_base():
                        base_class_info['is_virtual'] = True
                    
                    # 通过token检查virtual关键字
                    tokens = list(child.get_tokens())
                    for token in tokens:
                        if token.spelling == 'virtual':
                            base_class_info['is_virtual'] = True
                            break
                    
                    # 检查访问说明符（通过token）
                    for token in tokens:
                        if token.spelling in ['public', 'protected', 'private']:
                            base_class_info['access_specifier'] = token.spelling
                            break
                    
                    inheritance_list.append(base_class_info)
            
            # 如果没有找到继承信息，尝试解析类声明的token
            if not inheritance_list:
                tokens = list(cursor.get_tokens())
                in_inheritance = False
                current_access = 'private'  # 默认访问级别
                current_virtual = False
                
                for i, token in enumerate(tokens):
                    if token.spelling == ':' and not in_inheritance:
                        # 找到继承开始标记
                        in_inheritance = True
                        continue
                    
                    if in_inheritance:
                        if token.spelling == '{':
                            # 类体开始，继承声明结束
                            break
                        elif token.spelling in ['public', 'protected', 'private']:
                            current_access = token.spelling
                        elif token.spelling == 'virtual':
                            current_virtual = True
                        elif token.spelling == ',':
                            # 多重继承分隔符，重置状态
                            current_access = 'private'
                            current_virtual = False
                        elif token.spelling not in [',', 'public', 'protected', 'private', 'virtual']:
                            # 基类名称
                            base_class_name = token.spelling
                            
                            # 收集完整的基类名称（处理模板和命名空间）
                            full_base_name = []
                            j = i
                            while j < len(tokens) and tokens[j].spelling not in [',', '{']:
                                if tokens[j].spelling not in ['public', 'protected', 'private', 'virtual']:
                                    full_base_name.append(tokens[j].spelling)
                                j += 1
                            
                            if full_base_name:
                                base_class_info = {
                                    'base_class': ''.join(full_base_name),
                                    'access_specifier': current_access,
                                    'is_virtual': current_virtual,
                                    'is_pack_expansion': False
                                }
                                inheritance_list.append(base_class_info)
                                
                                # 重置状态
                                current_access = 'private'
                                current_virtual = False
                                
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败: {e}")
        
        return inheritance_list
    
    def _extract_nested_types(self, cursor) -> List[str]:
        """提取嵌套类型"""
        nested_types = []
        try:
            import clang.cindex as clang
            
            # 遍历子游标查找嵌套类型
            for child in cursor.get_children():
                if child.kind in [clang.CursorKind.CLASS_DECL, 
                                 clang.CursorKind.STRUCT_DECL,
                                 clang.CursorKind.UNION_DECL,
                                 clang.CursorKind.ENUM_DECL,
                                 clang.CursorKind.TYPEDEF_DECL,
                                 clang.CursorKind.TYPE_ALIAS_DECL,
                                 clang.CursorKind.CLASS_TEMPLATE]:
                    if child.spelling:
                        # 获取完整的嵌套类型名称
                        nested_name = child.spelling
                        if child.kind == clang.CursorKind.CLASS_DECL:
                            nested_name = f"class {nested_name}"
                        elif child.kind == clang.CursorKind.STRUCT_DECL:
                            nested_name = f"struct {nested_name}"
                        elif child.kind == clang.CursorKind.UNION_DECL:
                            nested_name = f"union {nested_name}"
                        elif child.kind == clang.CursorKind.ENUM_DECL:
                            nested_name = f"enum {nested_name}"
                        elif child.kind == clang.CursorKind.TYPEDEF_DECL:
                            nested_name = f"typedef {nested_name}"
                        elif child.kind == clang.CursorKind.TYPE_ALIAS_DECL:
                            nested_name = f"using {nested_name}"
                        elif child.kind == clang.CursorKind.CLASS_TEMPLATE:
                            nested_name = f"template class {nested_name}"
                        
                        nested_types.append(nested_name)
                        
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败: {e}")
        
        return nested_types
    
    def _extract_friend_declarations(self, cursor) -> List[str]:
        """提取友元声明"""
        friend_declarations = []
        try:
            import clang.cindex as clang
            
            # 遍历子游标查找友元声明
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.FRIEND_DECL:
                    if child.spelling:
                        friend_declarations.append(child.spelling)
                    else:
                        # 如果没有spelling，尝试从类型获取
                        if child.type:
                            friend_declarations.append(child.type.spelling)
            
            # 通过token检查friend关键字
            tokens = list(cursor.get_tokens())
            in_friend_decl = False
            current_friend = []
            
            for i, token in enumerate(tokens):
                if token.spelling == 'friend':
                    in_friend_decl = True
                    current_friend = []
                    continue
                
                if in_friend_decl:
                    if token.spelling == ';':
                        # 友元声明结束
                        if current_friend:
                            friend_decl = ' '.join(current_friend)
                            if friend_decl not in friend_declarations:
                                friend_declarations.append(friend_decl)
                        in_friend_decl = False
                        current_friend = []
                    elif token.spelling == '{':
                        # 可能是友元函数定义
                        if current_friend:
                            friend_decl = ' '.join(current_friend)
                            if friend_decl not in friend_declarations:
                                friend_declarations.append(friend_decl)
                        in_friend_decl = False
                        current_friend = []
                    else:
                        current_friend.append(token.spelling)
                        
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败: {e}")
        
        return friend_declarations
    
    def _extract_constructors_info(self, cursor) -> Dict[str, int]:
        """提取构造函数信息"""
        constructors_info = {}
        try:
            import clang.cindex as clang
            
            # 遍历子游标查找构造函数
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.CONSTRUCTOR:
                    constructor_type = "default"
                    flags = 0
                    
                    # 检查构造函数类型
                    args = list(child.get_arguments())
                    if len(args) == 0:
                        constructor_type = "default"
                        flags |= SpecialMethodStatusFlags.SPECIAL_IS_DEFINED
                    elif len(args) == 1:
                        # 检查是否为拷贝或移动构造函数
                        arg_type = args[0].type.spelling if args[0].type else ""
                        class_name = cursor.spelling or ""
                        
                        if '&' in arg_type and class_name in arg_type:
                            if '&&' in arg_type:
                                constructor_type = "move"
                            else:
                                constructor_type = "copy"
                        else:
                            constructor_type = "custom"
                    else:
                        constructor_type = "custom"
                    
                    # 检查特殊状态
                    if hasattr(child, 'is_default') and child.is_default():
                        flags |= SpecialMethodStatusFlags.SPECIAL_IS_DEFAULTED
                    
                    if hasattr(child, 'is_deleted') and child.is_deleted():
                        flags |= SpecialMethodStatusFlags.SPECIAL_IS_DELETED
                    
                    # 通过token检查 = default 和 = delete
                    tokens = list(child.get_tokens())
                    for i, token in enumerate(tokens):
                        if token.spelling == '=' and i + 1 < len(tokens):
                            next_token = tokens[i + 1]
                            if next_token.spelling == 'default':
                                flags |= SpecialMethodStatusFlags.SPECIAL_IS_DEFAULTED
                            elif next_token.spelling == 'delete':
                                flags |= SpecialMethodStatusFlags.SPECIAL_IS_DELETED
                    
                    # 检查是否有定义
                    if child.is_definition():
                        flags |= SpecialMethodStatusFlags.SPECIAL_IS_DEFINED
                    
                    constructors_info[constructor_type] = flags
                    
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败: {e}")
        
        return constructors_info
    
    def _extract_destructor_info(self, cursor) -> Dict[str, int]:
        """提取析构函数信息"""
        destructor_info = {}
        try:
            import clang.cindex as clang
            
            # 遍历子游标查找析构函数
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.DESTRUCTOR:
                    flags = 0
                    
                    # 检查基本状态
                    flags |= SpecialMethodStatusFlags.SPECIAL_IS_DEFINED
                    
                    # 检查是否为虚析构函数
                    if hasattr(child, 'is_virtual_method') and child.is_virtual_method():
                        flags |= SpecialMethodStatusFlags.SPECIAL_IS_VIRTUAL
                    
                    # 检查特殊状态
                    if hasattr(child, 'is_default') and child.is_default():
                        flags |= SpecialMethodStatusFlags.SPECIAL_IS_DEFAULTED
                    
                    if hasattr(child, 'is_deleted') and child.is_deleted():
                        flags |= SpecialMethodStatusFlags.SPECIAL_IS_DELETED
                    
                    # 通过token检查virtual、= default、= delete
                    tokens = list(child.get_tokens())
                    for i, token in enumerate(tokens):
                        if token.spelling == 'virtual':
                            flags |= SpecialMethodStatusFlags.SPECIAL_IS_VIRTUAL
                        elif token.spelling == '=' and i + 1 < len(tokens):
                            next_token = tokens[i + 1]
                            if next_token.spelling == 'default':
                                flags |= SpecialMethodStatusFlags.SPECIAL_IS_DEFAULTED
                            elif next_token.spelling == 'delete':
                                flags |= SpecialMethodStatusFlags.SPECIAL_IS_DELETED
                    
                    # 检查是否有定义
                    if child.is_definition():
                        flags |= SpecialMethodStatusFlags.SPECIAL_IS_DEFINED
                    
                    destructor_info["destructor"] = flags
                    break  # 只能有一个析构函数
                    
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败: {e}")
        
        return destructor_info
    
    def _extract_namespace_aliases(self, cursor) -> List[str]:
        """提取命名空间别名"""
        aliases = []
        try:
            import clang.cindex as clang
            
            # 遍历子游标查找命名空间别名
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.NAMESPACE_ALIAS:
                    alias_name = child.spelling
                    # 获取别名目标
                    target = None
                    for grandchild in child.get_children():
                        if grandchild.kind == clang.CursorKind.NAMESPACE_REF:
                            target = grandchild.spelling
                            break
                    
                    if alias_name and target:
                        aliases.append(f"{alias_name} = {target}")
                    elif alias_name:
                        aliases.append(alias_name)
            
            # 通过token检查namespace别名语法
            tokens = list(cursor.get_tokens())
            in_namespace_alias = False
            current_alias = []
            
            for i, token in enumerate(tokens):
                if (token.spelling == 'namespace' and 
                    i + 2 < len(tokens) and 
                    tokens[i + 2].spelling == '='):
                    # 找到namespace alias语法
                    alias_name = tokens[i + 1].spelling
                    
                    # 收集等号后的内容
                    target_tokens = []
                    for j in range(i + 3, len(tokens)):
                        if tokens[j].spelling == ';':
                            break
                        target_tokens.append(tokens[j].spelling)
                    
                    if target_tokens:
                        target = ''.join(target_tokens)
                        alias_decl = f"{alias_name} = {target}"
                        if alias_decl not in aliases:
                            aliases.append(alias_decl)
                            
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败: {e}")
        
        return aliases
    
    def _extract_using_declarations(self, cursor) -> List[str]:
        """提取using声明"""
        using_declarations = []
        try:
            import clang.cindex as clang
            
            # 遍历子游标查找using声明
            for child in cursor.get_children():
                if child.kind in [clang.CursorKind.USING_DECLARATION, 
                                 clang.CursorKind.USING_DIRECTIVE]:
                    if child.spelling:
                        using_declarations.append(child.spelling)
                    else:
                        # 尝试从引用的游标获取名称
                        for grandchild in child.get_children():
                            if grandchild.spelling:
                                using_declarations.append(grandchild.spelling)
                                break
            
            # 通过token检查using声明
            tokens = list(cursor.get_tokens())
            in_using_decl = False
            current_using = []
            
            for i, token in enumerate(tokens):
                if token.spelling == 'using':
                    in_using_decl = True
                    current_using = []
                    continue
                
                if in_using_decl:
                    if token.spelling == ';':
                        # using声明结束
                        if current_using:
                            using_decl = ' '.join(current_using)
                            if using_decl not in using_declarations:
                                using_declarations.append(using_decl)
                        in_using_decl = False
                        current_using = []
                    elif token.spelling in ['namespace', 'typename']:
                        # using namespace 或 using typename
                        current_using.insert(0, token.spelling)
                    else:
                        current_using.append(token.spelling)
                        
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败: {e}")
        
        return using_declarations
    
    def _is_template_function(self, cursor) -> bool:
        """检查是否为模板函数"""
        try:
            # 检查是否有模板参数
            parent = cursor.semantic_parent
            while parent:
                import clang.cindex as clang
                if parent.kind == clang.CursorKind.FUNCTION_TEMPLATE:
                    return True
                parent = parent.semantic_parent
            return False
        except:
            return False
    
    def _is_template_class(self, cursor) -> bool:
        """检查是否为模板类"""
        try:
            import clang.cindex as clang
            return cursor.kind == clang.CursorKind.CLASS_TEMPLATE
        except:
            return False
    
    # Helper methods for status flag calculation
    def _is_template_specialization(self, cursor) -> bool:
        """检查是否为模板特化"""
        try:
            # 检查父节点是否为模板特化
            parent = cursor.semantic_parent
            while parent:
                if hasattr(parent, 'get_template_specialization_kind'):
                    return True
                parent = parent.semantic_parent
            return False
        except:
            return False
    
    def _has_override_specifier(self, cursor) -> bool:
        """检查是否有override说明符"""
        try:
            # 通过检查源码范围内的token来查找override关键字
            for token in cursor.get_tokens():
                if token.spelling == 'override':
                    return True
            return False
        except:
            return False
    
    def _has_final_specifier(self, cursor) -> bool:
        """检查是否有final说明符"""
        try:
            # 通过检查源码范围内的token来查找final关键字
            for token in cursor.get_tokens():
                if token.spelling == 'final':
                    return True
            return False
        except:
            return False
    
    def _has_noexcept_specifier(self, cursor) -> bool:
        """检查是否有noexcept说明符"""
        try:
            # 通过检查类型信息或token来查找noexcept
            if hasattr(cursor, 'type') and cursor.type:
                # 检查函数类型是否包含noexcept
                return 'noexcept' in str(cursor.type.spelling)
            return False
        except:
            return False
    
    def _is_inline_function(self, cursor) -> bool:
        """检查是否为内联函数"""
        try:
            # 检查是否在类内定义（通常是内联的）
            if cursor.is_definition() and cursor.semantic_parent:
                import clang.cindex as clang
                parent_kind = cursor.semantic_parent.kind
                if parent_kind in [clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL]:
                    return True
            
            # 检查explicit inline关键字
            for token in cursor.get_tokens():
                if token.spelling == 'inline':
                    return True
            return False
        except:
            return False
    
    def _is_constexpr_function(self, cursor) -> bool:
        """检查是否为constexpr函数"""
        try:
            for token in cursor.get_tokens():
                if token.spelling == 'constexpr':
                    return True
            return False
        except:
            return False
    
    def _is_copy_constructor(self, cursor) -> bool:
        """检查是否为拷贝构造函数"""
        try:
            import clang.cindex as clang
            if cursor.kind != clang.CursorKind.CONSTRUCTOR:
                return False
            
            # 拷贝构造函数通常有一个同类型的const引用参数
            args = list(cursor.get_arguments())
            if len(args) == 1:
                arg_type = args[0].type
                # 简化检查：参数类型包含同类名且是引用
                class_name = cursor.semantic_parent.spelling
                return ('&' in str(arg_type.spelling) and class_name in str(arg_type.spelling))
            return False
        except:
            return False
    
    def _is_move_constructor(self, cursor) -> bool:
        """检查是否为移动构造函数"""
        try:
            import clang.cindex as clang
            if cursor.kind != clang.CursorKind.CONSTRUCTOR:
                return False
            
            # 移动构造函数通常有一个同类型的右值引用参数
            args = list(cursor.get_arguments())
            if len(args) == 1:
                arg_type = args[0].type
                # 简化检查：参数类型包含同类名且是右值引用
                class_name = cursor.semantic_parent.spelling
                return ('&&' in str(arg_type.spelling) and class_name in str(arg_type.spelling))
            return False
        except:
            return False
    
    def _is_abstract_class(self, cursor) -> bool:
        """检查是否为抽象类（包含纯虚函数）"""
        try:
            for child in cursor.get_children():
                import clang.cindex as clang
                if child.kind in [clang.CursorKind.CXX_METHOD, clang.CursorKind.DESTRUCTOR]:
                    if hasattr(child, 'is_pure_virtual_method') and child.is_pure_virtual_method():
                        return True
            return False
        except:
            return False
    
    def _is_polymorphic_class(self, cursor) -> bool:
        """检查是否为多态类（包含虚函数）"""
        try:
            for child in cursor.get_children():
                import clang.cindex as clang
                if child.kind in [clang.CursorKind.CXX_METHOD, clang.CursorKind.DESTRUCTOR]:
                    if hasattr(child, 'is_virtual_method') and child.is_virtual_method():
                        return True
            return False
        except:
            return False
    
    def _has_virtual_destructor(self, cursor) -> bool:
        """检查是否有虚析构函数"""
        try:
            for child in cursor.get_children():
                import clang.cindex as clang
                if child.kind == clang.CursorKind.DESTRUCTOR:
                    if hasattr(child, 'is_virtual_method') and child.is_virtual_method():
                        return True
            return False
        except:
            return False
    
    def _is_pod_class(self, cursor) -> bool:
        """检查是否为POD类型"""
        try:
            # 简化检查：POD类型通常没有虚函数、用户定义的构造函数等
            # 这里只做基本检查
            if self._is_polymorphic_class(cursor):
                return False
            
            # 检查是否有用户定义的构造函数/析构函数
            has_user_defined_ctor = False
            for child in cursor.get_children():
                import clang.cindex as clang
                if child.kind in [clang.CursorKind.CONSTRUCTOR, clang.CursorKind.DESTRUCTOR]:
                    if not child.is_default_constructor() if hasattr(child, 'is_default_constructor') else True:
                        has_user_defined_ctor = True
                        break
            
            return not has_user_defined_ctor
        except:
            return False
    
    def _is_trivial_class(self, cursor) -> bool:
        """检查是否为trivial类型"""
        try:
            # Trivial类型的简化检查
            # 通常没有虚函数、用户定义的特殊成员函数等
            return self._is_pod_class(cursor) and not self._is_polymorphic_class(cursor)
        except:
            return False
    
    # 数据转换方法
    def _function_to_dict(self, func: ExtractedFunction) -> Dict[str, Any]:
        """将函数对象转换为字典"""
        return {
            'name': func.name,
            'qualified_name': func.qualified_name,
            'signature': func.signature,
            'return_type': func.return_type,
            'parameters': func.parameters,
            'definition_file_id': func.definition_file_id,
            'declaration_file_id': func.declaration_file_id,
            'definition_line': func.definition_line,
            'declaration_line': func.declaration_line,
            'function_status_flags': func.function_status_flags,
            'access_specifier': func.access_specifier,
            'storage_class': func.storage_class,
            'template_parameters': func.template_parameters,
            'exception_specification': func.exception_specification,
            'attributes': func.attributes,
            'mangled_name': func.mangled_name,
            'documentation': func.documentation
        }
    
    def _class_to_dict(self, cls: ExtractedClass) -> Dict[str, Any]:
        """将类对象转换为字典"""
        return {
            'name': cls.name,
            'qualified_name': cls.qualified_name,
            'kind': cls.kind,
            'definition_file_id': cls.definition_file_id,
            'declaration_file_id': cls.declaration_file_id,
            'definition_line': cls.definition_line,
            'declaration_line': cls.declaration_line,
            'class_status_flags': cls.class_status_flags,
            'access_specifier': cls.access_specifier,
            'template_parameters': cls.template_parameters,
            'inheritance_list': cls.inheritance_list,
            'nested_types': cls.nested_types,
            'friend_declarations': cls.friend_declarations,
            'size_in_bytes': cls.size_in_bytes,
            'alignment': cls.alignment,
            'virtual_table_info': cls.virtual_table_info,
            'constructors': cls.constructors,
            'destructor': cls.destructor,
            'is_mixin': cls.is_mixin,
            'documentation': cls.documentation
        }
    
    def _namespace_to_dict(self, ns: ExtractedNamespace) -> Dict[str, Any]:
        """将命名空间对象转换为字典"""
        return {
            'name': ns.name,
            'qualified_name': ns.qualified_name,
            'definition_file_id': ns.definition_file_id,
            'definition_line': ns.definition_line,
            'aliases': ns.aliases,
            'using_declarations': ns.using_declarations,
            'documentation': ns.documentation
        }
    
    # 新增的提取方法
    def _extract_include(self, cursor, current_file_path: str):
        """提取include指令信息"""
        try:
            file_id = self._get_file_id(current_file_path)
            
            if file_id not in self.includes:
                self.includes[file_id] = []
            
            # 获取被包含的文件名
            included_file = cursor.spelling or ""
            if not included_file and cursor.location and cursor.location.file:
                # 从源码中提取include内容
                tokens = list(cursor.get_tokens())
                for token in tokens:
                    if token.spelling.startswith('"') or token.spelling.startswith('<'):
                        included_file = token.spelling.strip('"<>')
                        break
            
            if included_file:
                include_info = {
                    'file': included_file,
                    'type': 'system' if included_file.startswith('<') else 'local',
                    'line': cursor.location.line if cursor.location else 0,
                    'resolved_path': ''  # 可以后续通过include路径解析
                }
                self.includes[file_id].append(include_info)
                
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败: {e}")
    
    def _extract_typedef(self, cursor, current_file_path: str):
        """提取typedef信息"""
        try:
            if not cursor.spelling:
                return
                
            file_id = self._get_file_id(current_file_path)
            
            # 获取原始类型
            underlying_type = ""
            if cursor.underlying_typedef_type:
                underlying_type = cursor.underlying_typedef_type.spelling
            
            typedef_info = {
                'name': cursor.spelling,
                'underlying_type': underlying_type,
                'file_id': file_id,
                'line': cursor.location.line if cursor.location else 0,
                'qualified_name': self._get_qualified_name(cursor),
                'documentation': self._extract_documentation(cursor)
            }
            
            self.typedefs[cursor.spelling] = typedef_info
            
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败 {cursor.spelling}: {e}")
    
    def _is_forward_declaration(self, cursor) -> bool:
        """检查是否为前向声明"""
        try:
            # 前向声明没有定义体，只有声明
            return not cursor.is_definition()
        except:
            return False
    
    def _extract_forward_declaration(self, cursor, current_file_path: str):
        """提取前向声明信息"""
        try:
            if not cursor.spelling:
                return
                
            file_id = self._get_file_id(current_file_path)
            
            if file_id not in self.forward_declarations:
                self.forward_declarations[file_id] = []
            
            import clang.cindex as clang
            kind_str = "class"
            if cursor.kind == clang.CursorKind.STRUCT_DECL:
                kind_str = "struct"
            elif cursor.kind == clang.CursorKind.UNION_DECL:
                kind_str = "union"
            
            forward_decl_info = {
                'name': cursor.spelling,
                'kind': kind_str,
                'qualified_name': self._get_qualified_name(cursor),
                'line': cursor.location.line if cursor.location else 0,
                'documentation': self._extract_documentation(cursor)
            }
            
            self.forward_declarations[file_id].append(forward_decl_info)
            
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败 {cursor.spelling}: {e}")
    
    def _extract_macro_definition(self, cursor, current_file_path: str):
        """提取宏定义信息"""
        try:
            if not cursor.spelling:
                return
                
            file_id = self._get_file_id(current_file_path)
            
            # 获取宏的值
            macro_value = ""
            try:
                # 通过tokens获取宏的完整定义
                tokens = list(cursor.get_tokens())
                if len(tokens) > 1:
                    # 第一个token是宏名，后面的是值
                    value_tokens = [token.spelling for token in tokens[1:]]
                    macro_value = ' '.join(value_tokens)
            except:
                pass
            
            macro_info = {
                'name': cursor.spelling,
                'value': macro_value,
                'file_id': file_id,
                'line': cursor.location.line if cursor.location else 0,
                'source': 'ast',
                'type': 'function_like' if '(' in macro_value else 'object_like'
            }
            
            self.ast_macros[cursor.spelling] = macro_info
            
        except Exception as e:
            logger = get_logger()
            logger.error(f"提取失败 {cursor.spelling}: {e}") 