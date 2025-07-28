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
- 模板参数和实参提取
"""

from pathlib import Path
import re
from typing import List, Dict, Set, Any, Optional, Tuple

import clang.cindex as clang

from .logger import get_logger
from .distributed_file_manager import DistributedFileIdManager
from .data_structures import (
    Function, Class, Namespace, CppExtensions, CppOopExtensions,
    CallInfo, CppCallInfo, Parameter, Location, ResolvedDefinitionLocation, InheritanceInfo,
    FunctionStatusFlags, ClassStatusFlags, CallStatusFlags, EntityNode, TemplateParameter
)


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
            
            if file_path not in self._file_contents_cache:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    self._file_contents_cache[file_path] = f.readlines()
            
            lines = self._file_contents_cache[file_path]
            
            start_line = cursor.extent.start.line - 1
            end_line = cursor.extent.end.line
            
            if start_line < 0 or end_line > len(lines):
                return ""
            
            return ''.join(lines[start_line:end_line])
        except Exception:
            return ""


class EntityExtractor:
    """从 Clang AST 提取实体 (v2.5 - 模板支持)"""

    def __init__(self, file_id_manager: DistributedFileIdManager):
        self.logger = get_logger()
        self.file_id_manager = file_id_manager
        self.code_extractor = CodeExtractor()
        
        self.functions: Dict[str, Function] = {}
        self.classes: Dict[str, Class] = {}
        self.namespaces: Dict[str, Namespace] = {}
        self.global_nodes: Dict[str, EntityNode] = {}
        self._processed_usrs: Set[str] = set()

    def extract_from_files(self, parsed_files: List[Any], config: Any) -> Dict[str, Any]:
        self.logger.info("开始实体提取 (v2.5 - 模板支持)...")
        self._reset_state()

        self.logger.info("Pass 1: 提取声明和定义...")
        for parsed_file in parsed_files:
            if parsed_file.translation_unit:
                self._first_pass_visitor(parsed_file.translation_unit.cursor)

        self.logger.info("Pass 2: 提取关系...")
        for parsed_file in parsed_files:
            if parsed_file.translation_unit:
                self._second_pass_visitor(parsed_file.translation_unit.cursor)

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
        self.functions.clear()
        self.classes.clear()
        self.namespaces.clear()
        self.global_nodes.clear()
        self._processed_usrs.clear()

    def _first_pass_visitor(self, cursor: clang.Cursor):
        if cursor.kind == clang.CursorKind.TRANSLATION_UNIT:
            for child in cursor.get_children():
                self._first_pass_visitor(child)
            return

        try:
            if self._is_in_project(cursor):
                if cursor.kind in [
                    clang.CursorKind.FUNCTION_DECL, clang.CursorKind.CXX_METHOD, 
                    clang.CursorKind.CONSTRUCTOR, clang.CursorKind.DESTRUCTOR,
                    clang.CursorKind.FUNCTION_TEMPLATE
                ]:
                    self._process_function_cursor(cursor)
                
                elif cursor.kind in [
                    clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL,
                    clang.CursorKind.CLASS_TEMPLATE, clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION
                ]:
                    self._process_class_cursor(cursor)
                
                elif cursor.kind == clang.CursorKind.NAMESPACE:
                    self._process_namespace_cursor(cursor)

            for child in cursor.get_children():
                self._first_pass_visitor(child)
                
        except Exception as e:
            self.logger.warning(f"Pass 1 - Error processing cursor {cursor.spelling}: {e}")

    def _second_pass_visitor(self, cursor: clang.Cursor):
        if cursor.kind == clang.CursorKind.TRANSLATION_UNIT:
            for child in cursor.get_children():
                self._second_pass_visitor(child)
            return
            
        try:
            if self._is_in_project(cursor):
                if cursor.kind in [
                    clang.CursorKind.FUNCTION_DECL, clang.CursorKind.CXX_METHOD, 
                    clang.CursorKind.CONSTRUCTOR, clang.CursorKind.DESTRUCTOR,
                    clang.CursorKind.FUNCTION_TEMPLATE
                ]:
                    self._extract_calls_for_function(cursor)
                
                elif cursor.kind in [
                    clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL,
                    clang.CursorKind.CLASS_TEMPLATE, clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION
                ] and cursor.is_definition():
                    self._extract_inheritance_for_class(cursor)
            
            for child in cursor.get_children():
                self._second_pass_visitor(child)
                
        except Exception as e:
            self.logger.warning(f"Pass 2 - Error processing cursor {cursor.spelling}: {e}")

    def _process_function_cursor(self, cursor: clang.Cursor):
        usr = cursor.get_usr()
        if not usr: return
            
        file_path = cursor.location.file.name
        file_id = self.file_id_manager.get_file_id(file_path)
        if not file_id: return

        if usr in self.functions:
            existing_func = self.functions[usr]
            if cursor.is_definition() and not existing_func.is_definition:
                self._update_function_with_definition(existing_func, cursor)
            elif not cursor.is_definition():
                location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
                if location not in existing_func.declaration_locations:
                    existing_func.declaration_locations.append(location)
        else:
            func = self._create_function_from_cursor(cursor)
            self.functions[usr] = func
            self.global_nodes[usr] = EntityNode(usr, "function", func)

    def _process_class_cursor(self, cursor: clang.Cursor):
        usr = cursor.get_usr()
        if not usr: return
            
        file_path = cursor.location.file.name
        file_id = self.file_id_manager.get_file_id(file_path)
        if not file_id: return

        if usr in self.classes:
            existing_class = self.classes[usr]
            if cursor.is_definition() and not existing_class.is_definition:
                self._update_class_with_definition(existing_class, cursor)
            elif not cursor.is_definition():
                location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
                if location not in existing_class.declaration_locations:
                    existing_class.declaration_locations.append(location)
        else:
            cls = self._create_class_from_cursor(cursor)
            self.classes[usr] = cls
            self.global_nodes[usr] = EntityNode(usr, "class", cls)

    def _process_namespace_cursor(self, cursor: clang.Cursor):
        usr = cursor.get_usr()
        if not usr: return
            
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id: return

        if usr in self.namespaces:
            existing_ns = self.namespaces[usr]
            location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
            if location not in existing_ns.declaration_locations:
                existing_ns.declaration_locations.append(location)
        else:
            ns = self._create_namespace_from_cursor(cursor)
            self.namespaces[usr] = ns
            self.global_nodes[usr] = EntityNode(usr, "namespace", ns)

    def _create_function_from_cursor(self, cursor: clang.Cursor) -> Function:
        usr = cursor.get_usr()
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id: raise ValueError(f"无法获取文件ID for {cursor.location.file.name}")
        
        code_content = self.code_extractor.extract_function_code(cursor) if cursor.is_definition() else ""
        location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)

        cpp_ext = CppExtensions(
            qualified_name=self._get_qualified_name(cursor),
            namespace=self._get_namespace_str(cursor),
            function_status_flags=self._get_function_status_flags(cursor),
            access_specifier=cursor.access_specifier.name.lower(),
            return_type=cursor.result_type.spelling,
            parameter_types={p.spelling: p.type.spelling for p in cursor.get_arguments()},
            template_parameters=self._extract_template_parameters(cursor),
            mangled_name=cursor.mangled_name,
            usr=usr,
        )

        return Function(
            name=cursor.spelling, signature=cursor.displayname, usr_id=usr,
            definition_file_id=file_id if cursor.is_definition() else None,
            declaration_file_id=file_id if not cursor.is_definition() else None,
            start_line=cursor.extent.start.line, end_line=cursor.extent.end.line,
            parameters=[Parameter(name=p.spelling, type=p.type.spelling) for p in cursor.get_arguments()],
            code_content=code_content,
            declaration_locations=[location] if not cursor.is_definition() else [],
            definition_location=location if cursor.is_definition() else None,
            is_declaration=not cursor.is_definition(), is_definition=cursor.is_definition(),
            cpp_extensions=cpp_ext
        )

    def _create_class_from_cursor(self, cursor: clang.Cursor) -> Class:
        usr = cursor.get_usr()
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id: raise ValueError(f"无法获取文件ID for {cursor.location.file.name}")
            
        qualified_name = self._get_qualified_name(cursor)
        location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)

        member_methods = [child.get_usr() for child in cursor.get_children() if child.kind in [
            clang.CursorKind.CXX_METHOD, clang.CursorKind.CONSTRUCTOR, clang.CursorKind.DESTRUCTOR, clang.CursorKind.FUNCTION_TEMPLATE
        ] and child.get_usr()]

        cpp_oop_ext = CppOopExtensions(
            qualified_name=qualified_name, namespace=self._get_namespace_str(cursor),
            type=cursor.kind.name.lower().replace("_decl", ""),
            class_status_flags=self._get_class_status_flags(cursor),
            template_parameters=self._extract_template_parameters(cursor),
            template_specialization_args=self._extract_template_arguments(cursor.type),
            usr=usr,
        )

        return Class(
            name=cursor.spelling, qualified_name=qualified_name, usr_id=usr,
            definition_file_id=file_id if cursor.is_definition() else None,
            declaration_file_id=file_id if not cursor.is_definition() else None,
            line=cursor.location.line,
            declaration_locations=[location] if not cursor.is_definition() else [],
            definition_location=location if cursor.is_definition() else None,
            is_declaration=not cursor.is_definition(), is_definition=cursor.is_definition(),
            methods=member_methods, is_abstract=cursor.is_abstract_record(),
            cpp_oop_extensions=cpp_oop_ext
        )

    def _create_namespace_from_cursor(self, cursor: clang.Cursor) -> Namespace:
        usr = cursor.get_usr()
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id: raise ValueError(f"无法获取文件ID for {cursor.location.file.name}")
            
        location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
        return Namespace(
            name=cursor.spelling, qualified_name=self._get_qualified_name(cursor),
            usr_id=usr, definition_file_id=file_id, line=cursor.location.line,
            declaration_locations=[location], definition_location=location, usr=usr
        )

    def _update_function_with_definition(self, func: Function, cursor: clang.Cursor):
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id: return
        
        func.definition_file_id = file_id
        func.definition_location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
        func.is_definition = True
        func.start_line = cursor.extent.start.line
        func.end_line = cursor.extent.end.line
        func.code_content = self.code_extractor.extract_function_code(cursor)
        func.cpp_extensions.mangled_name = cursor.mangled_name

    def _update_class_with_definition(self, cls: Class, cursor: clang.Cursor):
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id: return
        
        cls.definition_file_id = file_id
        cls.definition_location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
        cls.is_definition = True
        
        cls.methods = [child.get_usr() for child in cursor.get_children() if child.kind in [
            clang.CursorKind.CXX_METHOD, clang.CursorKind.CONSTRUCTOR, clang.CursorKind.DESTRUCTOR, clang.CursorKind.FUNCTION_TEMPLATE
        ] and child.get_usr()]

    def _extract_calls_for_function(self, cursor: clang.Cursor):
        caller_usr = cursor.get_usr()
        caller_func = self.functions.get(caller_usr)
        if not caller_func: return
        
        for child in cursor.walk_preorder():
            if child.kind == clang.CursorKind.CALL_EXPR:
                callee_cursor = child.referenced
                if callee_cursor and callee_cursor.get_usr():
                    callee_usr = callee_cursor.get_usr()
                    if callee_usr == caller_usr: continue
                    
                    if callee_usr not in caller_func.calls_to:
                        caller_func.calls_to.append(callee_usr)
                    
                    def_loc_cursor = callee_cursor.get_definition() or callee_cursor
                    def_loc = def_loc_cursor.extent.start
                    file_id = self.file_id_manager.get_file_id(def_loc.file.name)
                    if file_id:
                        resolved_def_loc = ResolvedDefinitionLocation(file_id=file_id, line=def_loc.line, column=def_loc.column)
                        cpp_call_info = CppCallInfo(
                            call_status_flags=self._get_call_status_flags(child),
                            resolved_overload=callee_usr,
                            resolved_definition_location=resolved_def_loc,
                            template_args=self._extract_template_arguments(child)
                        )
                        caller_func.call_details.append(CallInfo(
                            to_usr_id=callee_usr, line=child.location.line,
                            column=child.location.column, cpp_call_info=cpp_call_info
                        ))

    def _extract_inheritance_for_class(self, cursor: clang.Cursor):
        cls = self.classes.get(cursor.get_usr())
        if not cls: return
        
        inheritance_list = []
        for base in cursor.get_children():
            if base.kind == clang.CursorKind.CXX_BASE_SPECIFIER:
                base_decl = base.type.get_declaration()
                if base_decl and base_decl.kind in [clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL, clang.CursorKind.CLASS_TEMPLATE]:
                    base_usr = base_decl.get_usr()
                    if base_usr:
                        if base_usr not in cls.parent_classes:
                            cls.parent_classes.append(base_usr)
                        
                        inheritance_list.append(InheritanceInfo(
                            base_class_usr_id=base_usr,
                            access_specifier=base.access_specifier.name.lower(),
                            is_virtual=getattr(base, 'is_virtual_base', lambda: False)()
                        ))
        
        if inheritance_list:
            cls.cpp_oop_extensions.inheritance_list = inheritance_list

    def _build_reverse_call_relationships(self):
        for caller_usr, caller_func in self.functions.items():
            for callee_usr in caller_func.calls_to:
                callee_func = self.functions.get(callee_usr)
                if callee_func and caller_usr not in callee_func.called_by:
                    callee_func.called_by.append(caller_usr)

    def _is_in_project(self, cursor: clang.Cursor) -> bool:
        if not cursor.location.file: return False
        return self.file_id_manager.get_file_id(cursor.location.file.name) is not None

    def _get_qualified_name(self, cursor: clang.Cursor) -> str:
        if cursor.kind.is_translation_unit(): return ""
        parent_name = self._get_qualified_name(cursor.semantic_parent)
        return f"{parent_name}::{cursor.spelling}" if parent_name else cursor.spelling
        
    def _get_namespace_str(self, cursor: clang.Cursor) -> str:
        parts = []
        parent = cursor.semantic_parent
        while parent and not parent.kind.is_translation_unit():
            if parent.kind == clang.CursorKind.NAMESPACE:
                parts.append(parent.spelling)
            parent = parent.semantic_parent
        return "::".join(reversed(parts))

    def _get_function_status_flags(self, c: clang.Cursor) -> int:
        flags = 0
        if c.is_virtual_method(): flags |= FunctionStatusFlags.FUNC_IS_VIRTUAL
        if c.is_pure_virtual_method(): flags |= FunctionStatusFlags.FUNC_IS_PURE_VIRTUAL
        if c.is_static_method(): flags |= FunctionStatusFlags.FUNC_IS_STATIC
        if c.is_const_method(): flags |= FunctionStatusFlags.FUNC_IS_CONST
        return flags

    def _get_class_status_flags(self, c: clang.Cursor) -> int:
        flags = 0
        if c.is_abstract_record(): flags |= ClassStatusFlags.CLASS_IS_ABSTRACT
        is_poly_func = getattr(c, 'is_polymorphic', lambda: False)
        if is_poly_func(): flags |= ClassStatusFlags.CLASS_IS_POLYMORPHIC
        return flags

    def _get_call_status_flags(self, c: clang.Cursor) -> int:
        flags = 0
        if c.referenced and c.referenced.is_virtual_method():
            flags |= CallStatusFlags.CALL_IS_VIRTUAL
        return flags

    def _extract_template_parameters(self, cursor: clang.Cursor) -> List[TemplateParameter]:
        """从模板声明中提取模板参数"""
        params = []
        for child in cursor.get_children():
            if child.kind == clang.CursorKind.TEMPLATE_TYPE_PARAMETER:
                params.append(TemplateParameter(name=child.spelling, type="typename"))
            elif child.kind == clang.CursorKind.TEMPLATE_NON_TYPE_PARAMETER:
                params.append(TemplateParameter(name=child.spelling, type=child.type.spelling))
        return params

    def _extract_template_arguments(self, type_or_cursor: Any) -> List[str]:
        """从类型或游标中提取模板实参"""
        args = []
        try:
            # 优先处理游标
            if isinstance(type_or_cursor, clang.Cursor):
                num_args = type_or_cursor.get_num_template_arguments()
                for i in range(num_args):
                    arg_kind = type_or_cursor.get_template_argument_kind(i)
                    if arg_kind == clang.TemplateArgumentKind.TYPE:
                        args.append(type_or_cursor.get_template_argument_type(i).spelling)
                    elif arg_kind == clang.TemplateArgumentKind.INTEGRAL:
                        args.append(str(type_or_cursor.get_template_argument_value(i)))
            # 其次处理类型
            elif isinstance(type_or_cursor, clang.Type):
                num_args = type_or_cursor.get_num_template_arguments()
                for i in range(num_args):
                    arg = type_or_cursor.get_template_argument_as_type(i)
                    if arg.spelling:
                        args.append(arg.spelling)
        except Exception as e:
            self.logger.debug(f"提取模板实参时出错: {e}")
        return args
