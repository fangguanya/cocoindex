"""
Entity Extractor (符合 json_format.md v2.4)

从 Clang AST 中提取 C++ 实体，并将其转换为严格遵循 v2.4 规范的
结构化数据。该模块负责生成文件ID、使用USR ID作为主键、状态位掩码，
并填充所有 C++ 扩展字段。

主要改进：
- 使用USR ID作为全局唯一标识符
- 函数体代码内容提取
- 声明vs定义的优化处理
- 全局nodes映射机制
"""

from pathlib import Path
import re
from typing import List, Dict, Set, Any, Optional, Tuple

import clang.cindex as clang

from .logger import get_logger
from cpp_clang.data_structures import (
    Function, Class, Namespace, CppExtensions, CppOopExtensions,
    CallInfo, CppCallInfo, Parameter, Location, ResolvedDefinitionLocation, InheritanceInfo,
    FunctionStatusFlags, ClassStatusFlags, CallStatusFlags, EntityNode, KeyGenerator
)


class FileIdManager:
    """管理文件路径到文件ID的映射"""
    def __init__(self, project_root: str):
        self._project_root = Path(project_root).resolve()
        self._file_to_id: Dict[str, str] = {}
        self._id_counter = 1

    def get_file_id(self, file_path: Optional[str]) -> Optional[str]:
        if not file_path:
            return None
        
        # 统一路径格式
        try:
            abs_path = str(Path(file_path).resolve())
        except Exception:
            abs_path = file_path # 如果路径有问题，使用原始路径
            
        if abs_path not in self._file_to_id:
            new_id = f"f{self._id_counter:03d}"
            self._file_to_id[abs_path] = new_id
            self._id_counter += 1
        return self._file_to_id[abs_path]

    def get_file_mappings(self) -> Dict[str, str]:
        # 返回ID到路径的映射
        return {v: k for k, v in self._file_to_id.items()}


class CodeExtractor:
    """代码内容提取器"""
    def __init__(self):
        self._file_contents_cache: Dict[str, List[str]] = {}
    
    def extract_function_code(self, cursor: clang.Cursor) -> str:
        """提取函数体的源代码内容"""
        if not cursor.extent or not cursor.extent.start.file:
            return ""
        
        try:
            file_path = cursor.extent.start.file.name
            
            # 使用缓存避免重复读取同一文件
            if file_path not in self._file_contents_cache:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    self._file_contents_cache[file_path] = f.readlines()
            
            lines = self._file_contents_cache[file_path]
            
            # 提取函数范围的代码
            start_line = cursor.extent.start.line - 1  # 转为0索引
            end_line = cursor.extent.end.line
            
            if start_line < 0 or end_line > len(lines):
                return ""
            
            return ''.join(lines[start_line:end_line])
        except Exception as e:
            return ""


class EntityExtractor:
    """从 Clang AST 提取实体 (v2.4 - USR ID支持)"""

    def __init__(self, project_root: str):
        self.logger = get_logger()
        self.file_id_manager = FileIdManager(project_root)
        self.code_extractor = CodeExtractor()
        
        # 使用USR ID作为主键的容器
        self.functions: Dict[str, Function] = {}  # key: USR ID
        self.classes: Dict[str, Class] = {}       # key: USR ID
        self.namespaces: Dict[str, Namespace] = {} # key: USR ID
        
        # 新增：全局nodes映射
        self.global_nodes: Dict[str, EntityNode] = {}  # key: USR ID, value: EntityNode
        
        # 内部追踪用于建立关系
        self._processed_usrs: Set[str] = set()  # 避免重复处理

    def extract_from_files(self, parsed_files: List[Any], config: Any) -> Dict[str, Any]:
        """主入口：从已解析的文件列表提取所有实体"""
        self.logger.info("开始实体提取 (v2.4 - USR ID支持)...")
        self._reset_state()

        # 第一次遍历：提取所有声明和定义，建立实体对象
        self.logger.info("Pass 1: 提取声明和定义...")
        for parsed_file in parsed_files:
            if parsed_file.translation_unit:
                self.logger.debug(f"Pass 1 on: {parsed_file.file_path}")
                self._first_pass_visitor(parsed_file.translation_unit.cursor)

        # 第二次遍历：提取调用关系、继承关系等详细信息
        self.logger.info("Pass 2: 提取关系...")
        for parsed_file in parsed_files:
            if parsed_file.translation_unit:
                self.logger.debug(f"Pass 2 on: {parsed_file.file_path}")
                self._second_pass_visitor(parsed_file.translation_unit.cursor)

        # 第三次遍历：建立反向调用关系（called_by）
        self.logger.info("Pass 3: 建立反向调用关系...")
        self._build_reverse_call_relationships()

        self.logger.info(f"实体提取完成。函数: {len(self.functions)}, 类: {len(self.classes)}, 命名空间: {len(self.namespaces)}")
        
        return {
            "functions": self.functions,
            "classes": self.classes,
            "namespaces": self.namespaces,
            "global_nodes": {usr_id: node.to_dict() for usr_id, node in self.global_nodes.items()},
            "file_mappings": self.file_id_manager.get_file_mappings()
        }

    def _reset_state(self):
        """重置内部状态"""
        self.functions.clear()
        self.classes.clear()
        self.namespaces.clear()
        self.global_nodes.clear()
        self._processed_usrs.clear()

    def _first_pass_visitor(self, cursor: clang.Cursor):
        """第一遍遍历：提取实体声明和定义"""
        # 特殊处理根节点(TRANSLATION_UNIT)，确保遍历总是开始
        if cursor.kind == getattr(clang.CursorKind, 'TRANSLATION_UNIT', None):
            for child in cursor.get_children():
                self._first_pass_visitor(child)
            return

        # 只处理项目内的文件
        if not self._is_in_project(cursor):
            return

        try:
            # 处理函数/方法
            if cursor.kind in [
                getattr(clang.CursorKind, 'FUNCTION_DECL', None), 
                getattr(clang.CursorKind, 'CXX_METHOD', None), 
                getattr(clang.CursorKind, 'CONSTRUCTOR', None), 
                getattr(clang.CursorKind, 'DESTRUCTOR', None)
            ]:
                self._process_function_cursor(cursor)
            
            # 处理类/结构体
            elif cursor.kind in [
                getattr(clang.CursorKind, 'CLASS_DECL', None), 
                getattr(clang.CursorKind, 'STRUCT_DECL', None)
            ]:
                self._process_class_cursor(cursor)
            
            # 处理命名空间
            elif cursor.kind == getattr(clang.CursorKind, 'NAMESPACE', None):
                self._process_namespace_cursor(cursor)

            # 递归遍历
            for child in cursor.get_children():
                self._first_pass_visitor(child)
                
        except Exception as e:
            self.logger.warning(f"Pass 1 - Error processing cursor {cursor.spelling}: {e}")

    def _second_pass_visitor(self, cursor: clang.Cursor):
        """第二遍遍历：填充调用关系、继承关系等"""
        # 特殊处理根节点(TRANSLATION_UNIT)，确保遍历总是开始
        if cursor.kind == getattr(clang.CursorKind, 'TRANSLATION_UNIT', None):
            for child in cursor.get_children():
                self._second_pass_visitor(child)
            return
            
        if not self._is_in_project(cursor):
            return
            
        try:
            # 处理函数调用
            if cursor.kind in [
                getattr(clang.CursorKind, 'FUNCTION_DECL', None), 
                getattr(clang.CursorKind, 'CXX_METHOD', None), 
                getattr(clang.CursorKind, 'CONSTRUCTOR', None), 
                getattr(clang.CursorKind, 'DESTRUCTOR', None)
            ]:
                if cursor.is_definition():
                    self._extract_calls_for_function(cursor)
            
            # 处理继承关系
            elif cursor.kind in [
                getattr(clang.CursorKind, 'CLASS_DECL', None), 
                getattr(clang.CursorKind, 'STRUCT_DECL', None)
            ]:
                if cursor.is_definition():
                    self._extract_inheritance_for_class(cursor)
            
            # 递归遍历
            for child in cursor.get_children():
                self._second_pass_visitor(child)
                
        except Exception as e:
            self.logger.warning(f"Pass 2 - Error processing cursor {cursor.spelling}: {e}")

    def _process_function_cursor(self, cursor: clang.Cursor):
        """统一处理函数的声明和定义"""
        usr = cursor.get_usr()
        if not usr:
            return
            
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id:
            return

        # 检查是否已存在
        if usr in self.functions:
            existing_func = self.functions[usr]
            if cursor.is_definition() and not existing_func.is_definition:
                # 这是定义，更新现有对象
                self._update_function_with_definition(existing_func, cursor)
            elif not cursor.is_definition():
                # 这是新的声明位置
                location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
                if location not in existing_func.declaration_locations:
                    existing_func.declaration_locations.append(location)
        else:
            # 创建新的函数对象
            func = self._create_function_from_cursor(cursor)
            self.functions[usr] = func
            
            # 添加到全局nodes
            self.global_nodes[usr] = EntityNode(
                usr_id=usr,
                entity_type="function",
                entity_data=func
            )

    def _process_class_cursor(self, cursor: clang.Cursor):
        """统一处理类的声明和定义"""
        usr = cursor.get_usr()
        if not usr:
            return
            
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id:
            return

        # 检查是否已存在
        if usr in self.classes:
            existing_class = self.classes[usr]
            if cursor.is_definition() and not existing_class.is_definition:
                # 这是定义，更新现有对象
                self._update_class_with_definition(existing_class, cursor)
            elif not cursor.is_definition():
                # 这是新的声明位置
                location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
                if location not in existing_class.declaration_locations:
                    existing_class.declaration_locations.append(location)
        else:
            # 创建新的类对象
            cls = self._create_class_from_cursor(cursor)
            self.classes[usr] = cls
            
            # 添加到全局nodes
            self.global_nodes[usr] = EntityNode(
                usr_id=usr,
                entity_type="class",
                entity_data=cls
            )

    def _process_namespace_cursor(self, cursor: clang.Cursor):
        """处理命名空间"""
        usr = cursor.get_usr()
        if not usr:
            return
            
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id:
            return

        # 命名空间可能在多个文件中定义
        if usr in self.namespaces:
            existing_ns = self.namespaces[usr]
            location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
            if location not in existing_ns.declaration_locations:
                existing_ns.declaration_locations.append(location)
        else:
            # 创建新的命名空间对象
            ns = self._create_namespace_from_cursor(cursor)
            self.namespaces[usr] = ns
            
            # 添加到全局nodes
            self.global_nodes[usr] = EntityNode(
                usr_id=usr,
                entity_type="namespace",
                entity_data=ns
            )

    def _create_function_from_cursor(self, cursor: clang.Cursor) -> Function:
        """从游标创建函数对象"""
        usr = cursor.get_usr()
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        
        if not file_id:
            raise ValueError(f"无法获取文件ID for {cursor.location.file.name}")
        
        # 提取参数类型用于生成签名键值（向后兼容）
        param_types = [p.type.spelling for p in cursor.get_arguments()]
        signature_key = KeyGenerator.for_function(
            cursor.result_type.spelling, cursor.spelling, param_types, file_id
        )

        # 提取函数体代码内容
        code_content = ""
        if cursor.is_definition():
            code_content = self.code_extractor.extract_function_code(cursor)

        # 创建位置信息
        location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)

        # 创建 CppExtensions
        cpp_ext = CppExtensions(
            qualified_name=self._get_qualified_name(cursor),
            namespace=self._get_namespace_str(cursor),
            function_status_flags=self._get_function_status_flags(cursor),
            access_specifier=cursor.access_specifier.name.lower(),
            return_type=cursor.result_type.spelling,
            parameter_types={p.spelling: p.type.spelling for p in cursor.get_arguments()},
            mangled_name=cursor.mangled_name,
            usr=usr,
            signature_key=signature_key
        )

        # 创建 Function 对象
        func = Function(
            name=cursor.spelling,
            signature=cursor.displayname,
            usr_id=usr,
            definition_file_id=file_id if cursor.is_definition() else None,
            declaration_file_id=file_id if not cursor.is_definition() else None,
            start_line=cursor.extent.start.line,
            end_line=cursor.extent.end.line,
            parameters=[Parameter(name=p.spelling, type=p.type.spelling) for p in cursor.get_arguments()],
            code_content=code_content,
            declaration_locations=[location] if not cursor.is_definition() else [],
            definition_location=location if cursor.is_definition() else None,
            is_declaration=not cursor.is_definition(),
            is_definition=cursor.is_definition(),
            cpp_extensions=cpp_ext
        )
        
        return func

    def _create_class_from_cursor(self, cursor: clang.Cursor) -> Class:
        """从游标创建类对象"""
        usr = cursor.get_usr()
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        
        if not file_id:
            raise ValueError(f"无法获取文件ID for {cursor.location.file.name}")
            
        qualified_name = self._get_qualified_name(cursor)
        
        # 生成签名键值（向后兼容）
        signature_key = KeyGenerator.for_class(qualified_name, file_id)

        # 创建位置信息
        location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)

        # 提取成员方法
        member_methods = []
        for child in cursor.get_children():
            if child.kind in [
                getattr(clang.CursorKind, 'CXX_METHOD', None),
                getattr(clang.CursorKind, 'CONSTRUCTOR', None),
                getattr(clang.CursorKind, 'DESTRUCTOR', None)
            ]:
                method_usr = child.get_usr()
                if method_usr:
                    member_methods.append(method_usr)

        cpp_oop_ext = CppOopExtensions(
            qualified_name=qualified_name,
            namespace=self._get_namespace_str(cursor),
            type=cursor.kind.name.lower().replace("_decl", ""),
            class_status_flags=self._get_class_status_flags(cursor),
            usr=usr,
            signature_key=signature_key
        )

        cls = Class(
            name=cursor.spelling,
            qualified_name=qualified_name,
            usr_id=usr,
            definition_file_id=file_id if cursor.is_definition() else None,
            declaration_file_id=file_id if not cursor.is_definition() else None,
            line=cursor.location.line,
            declaration_locations=[location] if not cursor.is_definition() else [],
            definition_location=location if cursor.is_definition() else None,
            is_declaration=not cursor.is_definition(),
            is_definition=cursor.is_definition(),
            methods=member_methods,
            is_abstract=cursor.is_abstract_record(),
            cpp_oop_extensions=cpp_oop_ext
        )
        
        return cls

    def _create_namespace_from_cursor(self, cursor: clang.Cursor) -> Namespace:
        """从游标创建命名空间对象"""
        usr = cursor.get_usr()
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        
        if not file_id:
            raise ValueError(f"无法获取文件ID for {cursor.location.file.name}")
            
        qualified_name = self._get_qualified_name(cursor)
        
        # 创建位置信息
        location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)

        ns = Namespace(
            name=cursor.spelling,
            qualified_name=qualified_name,
            usr_id=usr,
            definition_file_id=file_id,
            line=cursor.location.line,
            declaration_locations=[location],
            definition_location=location,
            usr=usr
        )
        
        return ns

    def _update_function_with_definition(self, func: Function, cursor: clang.Cursor):
        """用定义更新函数对象"""
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        
        if not file_id:
            return  # 如果无法获取文件ID，跳过更新
        
        # 更新定义相关字段
        func.definition_file_id = file_id
        func.definition_location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
        func.is_definition = True
        func.start_line = cursor.extent.start.line
        func.end_line = cursor.extent.end.line
        
        # 提取函数体代码内容
        func.code_content = self.code_extractor.extract_function_code(cursor)
        
        # 更新其他可能在定义中才能获取的信息
        func.cpp_extensions.mangled_name = cursor.mangled_name

    def _update_class_with_definition(self, cls: Class, cursor: clang.Cursor):
        """用定义更新类对象"""
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        
        if not file_id:
            return  # 如果无法获取文件ID，跳过更新
        
        # 更新定义相关字段
        cls.definition_file_id = file_id
        cls.definition_location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
        cls.is_definition = True
        
        # 重新提取成员方法（定义中可能有更完整的信息）
        member_methods = []
        for child in cursor.get_children():
            if child.kind in [
                getattr(clang.CursorKind, 'CXX_METHOD', None),
                getattr(clang.CursorKind, 'CONSTRUCTOR', None),
                getattr(clang.CursorKind, 'DESTRUCTOR', None)
            ]:
                method_usr = child.get_usr()
                if method_usr:
                    member_methods.append(method_usr)
        
        cls.methods = member_methods

    def _extract_calls_for_function(self, cursor: clang.Cursor):
        """为单个函数提取所有调用关系"""
        caller_usr = cursor.get_usr()
        caller_func = self.functions.get(caller_usr)
        if not caller_func:
            return
        
        for child in cursor.walk_preorder():
            if child.kind in [
                getattr(clang.CursorKind, 'CALL_EXPR', None), 
                getattr(clang.CursorKind, 'CXX_MEMBER_CALL_EXPR', None)
            ]:
                callee_cursor = child.referenced
                if callee_cursor:
                    callee_usr = callee_cursor.get_usr()
                    
                    if callee_usr and callee_usr != caller_usr:  # 避免自调用重复
                        # 添加到calls_to列表
                        if callee_usr not in caller_func.calls_to:
                            caller_func.calls_to.append(callee_usr)
                        
                        # 添加详细调用信息
                        def_loc = callee_cursor.extent.start
                        file_id = self.file_id_manager.get_file_id(def_loc.file.name)
                        if file_id:
                            resolved_def_loc = ResolvedDefinitionLocation(
                                file_id=file_id,
                                line=def_loc.line,
                                column=def_loc.column
                            )
                            cpp_call_info = CppCallInfo(
                                call_status_flags=self._get_call_status_flags(child),
                                resolved_overload=callee_usr,
                                resolved_definition_location=resolved_def_loc
                            )
                            call_info = CallInfo(
                                to_usr_id=callee_usr,
                                line=child.location.line,
                                column=child.location.column,
                                cpp_call_info=cpp_call_info
                            )
                            caller_func.call_details.append(call_info)

    def _extract_inheritance_for_class(self, cursor: clang.Cursor):
        """为类提取继承关系"""
        class_usr = cursor.get_usr()
        cls = self.classes.get(class_usr)
        if not cls:
            return
        
        inheritance_list = []
        for base in cursor.get_children():
            if base.kind == getattr(clang.CursorKind, 'CXX_BASE_SPECIFIER', None):
                base_usr = base.type.get_declaration().get_usr()
                if base_usr:
                    # 添加到parent_classes列表
                    if base_usr not in cls.parent_classes:
                        cls.parent_classes.append(base_usr)
                    
                    # 添加到详细继承信息
                    inheritance_info = InheritanceInfo(
                        base_class_usr_id=base_usr,
                        access_specifier=base.access_specifier.name.lower(),
                        is_virtual=getattr(base, 'is_virtual_base', lambda: False)()
                    )
                    inheritance_list.append(inheritance_info)
        
        cls.cpp_oop_extensions.inheritance_list = inheritance_list

    def _build_reverse_call_relationships(self):
        """建立反向调用关系（called_by）"""
        for caller_usr, caller_func in self.functions.items():
            for callee_usr in caller_func.calls_to:
                callee_func = self.functions.get(callee_usr)
                if callee_func and caller_usr not in callee_func.called_by:
                    callee_func.called_by.append(caller_usr)

    # ==============================================================================
    # 辅助方法
    # ==============================================================================
    
    def _is_in_project(self, cursor: clang.Cursor) -> bool:
        """检查游标是否属于当前分析的项目文件"""
        if cursor.location.file:
            # 简单的判断，可以根据需要扩展
            return not cursor.location.file.name.startswith('/usr')
        return False

    def _get_qualified_name(self, cursor: clang.Cursor) -> str:
        """获取完整的限定名称"""
        if cursor.kind.is_translation_unit():
            return ""
        parent_name = self._get_qualified_name(cursor.semantic_parent)
        if parent_name:
            return f"{parent_name}::{cursor.spelling}"
        return cursor.spelling
        
    def _get_namespace_str(self, cursor: clang.Cursor) -> str:
        """获取实体的命名空间字符串"""
        parent = cursor.semantic_parent
        ns_parts = []
        while parent and not parent.kind.is_translation_unit():
            if parent.kind == getattr(clang.CursorKind, 'NAMESPACE', None):
                ns_parts.append(parent.spelling)
            parent = parent.semantic_parent
        return "::".join(reversed(ns_parts))

    def _get_function_status_flags(self, c: clang.Cursor) -> int:
        flags = 0
        if c.is_virtual_method(): flags |= FunctionStatusFlags.FUNC_IS_VIRTUAL
        if c.is_pure_virtual_method(): flags |= FunctionStatusFlags.FUNC_IS_PURE_VIRTUAL
        if c.is_static_method(): flags |= FunctionStatusFlags.FUNC_IS_STATIC
        if c.is_const_method(): flags |= FunctionStatusFlags.FUNC_IS_CONST
        # ... 其他标志位 ...
        return flags

    def _get_class_status_flags(self, c: clang.Cursor) -> int:
        flags = 0
        if c.is_abstract_record(): flags |= ClassStatusFlags.CLASS_IS_ABSTRACT
        # 安全地检查 is_polymorphic 方法是否存在
        is_poly_func = getattr(c, 'is_polymorphic', lambda: False)
        if is_poly_func(): flags |= ClassStatusFlags.CLASS_IS_POLYMORPHIC
        # ... 其他标志位 ...
        return flags

    def _get_call_status_flags(self, c: clang.Cursor) -> int:
        flags = 0
        # 虚调用判断需要更复杂的逻辑，这里简化
        if c.referenced and c.referenced.is_virtual_method():
            flags |= CallStatusFlags.CALL_IS_VIRTUAL
        return flags
