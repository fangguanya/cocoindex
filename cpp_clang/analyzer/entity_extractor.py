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
import threading
from typing import List, Dict, Set, Any, Optional, Tuple

import clang.cindex as clang

from .logger import get_logger
from .distributed_file_manager import DistributedFileIdManager
from .performance_profiler import profiler, profile_function, DetailedLogger
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
    """从 Clang AST 提取实体 (v2.5 - 模板支持 + 性能优化)"""

    def __init__(self, file_id_manager: DistributedFileIdManager):
        self.logger = get_logger()
        self.file_id_manager = file_id_manager
        self.code_extractor = CodeExtractor()
        
        # 线程安全的数据结构
        self._lock = threading.RLock()
        self.functions: Dict[str, Function] = {}
        self.classes: Dict[str, Class] = {}
        self.namespaces: Dict[str, Namespace] = {}
        self.global_nodes: Dict[str, EntityNode] = {}
        self._processed_usrs: Set[str] = set()
        self._functions_with_calls_extracted: Set[str] = set()
        
        # 性能优化：缓存常用查询结果
        self._cursor_cache: Dict[str, Any] = {}
        self._qualified_name_cache: Dict[str, str] = {}

    @profile_function("EntityExtractor.extract_from_files")
    def extract_from_files(self, parsed_files: List[Any], config: Any) -> Dict[str, Any]:
        logger = DetailedLogger("实体提取")
        
        with profiler.timer("extract_reset_state"):
            self._reset_state()
        
        logger.checkpoint("状态重置完成", parsed_files_count=len(parsed_files))

        # Pass 1: 提取声明和定义 - 优化版
        with profiler.timer("extract_pass1_declarations"):
            for i, parsed_file in enumerate(parsed_files):
                if parsed_file.translation_unit:
                    file_logger = DetailedLogger(f"Pass1-文件{i+1}")
                    with profiler.timer("first_pass_single_file", {'file': parsed_file.file_path}):
                        self._first_pass_visitor_optimized(parsed_file.translation_unit.cursor)
                    file_logger.finish()

        logger.checkpoint("Pass 1 完成", 
                         functions_found=len(self.functions),
                         classes_found=len(self.classes),
                         namespaces_found=len(self.namespaces))

        # Pass 2: 提取关系 - 优化版
        with profiler.timer("extract_pass2_relationships"):
            for i, parsed_file in enumerate(parsed_files):
                if parsed_file.translation_unit:
                    file_logger = DetailedLogger(f"Pass2-文件{i+1}")
                    with profiler.timer("second_pass_single_file", {'file': parsed_file.file_path}):
                        self._second_pass_visitor_optimized(parsed_file.translation_unit.cursor)
                    file_logger.finish()

        logger.checkpoint("Pass 2 完成")

        # Pass 3: 建立反向调用关系
        with profiler.timer("extract_pass3_reverse_calls"):
            self._build_reverse_call_relationships()

        logger.checkpoint("Pass 3 完成")
        
        # 构建结果
        with profiler.timer("extract_build_result"):
            result = {
                "functions": self.functions,
                "classes": self.classes,
                "namespaces": self.namespaces,
                "global_nodes": {usr_id: node.to_dict() for usr_id, node in self.global_nodes.items()},
                "file_mappings": self.file_id_manager.get_file_mappings()
            }

        total_time = logger.finish("实体提取完成")
        
        if total_time > 5.0:  # 如果实体提取超过5秒，记录警告
            self.logger.warning(f"⚠️  实体提取耗时过长: {total_time:.2f}s")
        
        self.logger.info(f"实体提取完成。函数: {len(self.functions)}, 类: {len(self.classes)}, 命名空间: {len(self.namespaces)}")
        
        return result

    def _reset_state(self):
        """重置状态 - 线程安全版本"""
        with self._lock:
            self.functions.clear()
            self.classes.clear()
            self.namespaces.clear()
            self.global_nodes.clear()
            self._functions_with_calls_extracted.clear()
            self._processed_usrs.clear()
            self._cursor_cache.clear()
            self._qualified_name_cache.clear()

    def _first_pass_visitor_optimized(self, cursor):
        """优化版第一遍遍历 - 只提取声明和定义"""
        with profiler.timer("first_pass_visitor"):
            self._visit_optimized(cursor, self._extract_declarations_only)
    
    def _second_pass_visitor_optimized(self, cursor):
        """优化版第二遍遍历 - 只提取关系"""
        with profiler.timer("second_pass_visitor"):
            self._visit_optimized(cursor, self._extract_relationships_only)
    
    def _visit_optimized(self, cursor, extract_func):
        """优化的AST遍历算法 - 减少递归调用和内存分配"""
        if not cursor:
            return
            
        # 使用栈而不是递归，避免Python递归限制和开销
        stack = [cursor]
        visited = set()  # 防止重复访问
        
        while stack:
            current = stack.pop()
            
            # 避免重复访问同一个cursor
            cursor_hash = hash((current.spelling, current.location.file, current.location.line))
            if cursor_hash in visited:
                continue
            visited.add(cursor_hash)
            
            # 快速跳过不需要的cursor类型
            if self._should_skip_cursor(current):
                continue
                
            # 执行提取函数
            extract_func(current)
            
            # 只遍历相关的子节点，跳过不必要的节点
            for child in current.get_children():
                if self._should_visit_child(child):
                    stack.append(child)
    
    def _should_skip_cursor(self, cursor):
        """判断是否应该跳过这个cursor"""
        # 跳过注释、预处理指令等不相关节点
        skip_kinds = {
            clang.CursorKind.UNEXPOSED_DECL,
            clang.CursorKind.MACRO_DEFINITION,
            clang.CursorKind.INCLUSION_DIRECTIVE,
            clang.CursorKind.UNEXPOSED_STMT,
        }
        return cursor.kind in skip_kinds
    
    def _should_visit_child(self, cursor):
        """判断是否应该访问这个子节点"""
        # 只访问相关的节点类型
        relevant_kinds = {
            clang.CursorKind.NAMESPACE,
            clang.CursorKind.CLASS_DECL,
            clang.CursorKind.STRUCT_DECL,
            clang.CursorKind.FUNCTION_DECL,
            clang.CursorKind.CXX_METHOD,
            clang.CursorKind.CONSTRUCTOR,
            clang.CursorKind.DESTRUCTOR,
            clang.CursorKind.CALL_EXPR,
            clang.CursorKind.MEMBER_REF_EXPR,
            clang.CursorKind.DECL_REF_EXPR,
            clang.CursorKind.CLASS_TEMPLATE,
            clang.CursorKind.FUNCTION_TEMPLATE,
            clang.CursorKind.TEMPLATE_TYPE_PARAMETER,
            clang.CursorKind.TEMPLATE_NON_TYPE_PARAMETER,
            # 语句类型 - 确保能够遍历函数体
            clang.CursorKind.COMPOUND_STMT,
            clang.CursorKind.IF_STMT,
            clang.CursorKind.FOR_STMT,
            clang.CursorKind.WHILE_STMT,
            clang.CursorKind.DO_STMT,
            clang.CursorKind.SWITCH_STMT,
            clang.CursorKind.CASE_STMT,
            clang.CursorKind.DEFAULT_STMT,
            clang.CursorKind.BREAK_STMT,
            clang.CursorKind.CONTINUE_STMT,
            clang.CursorKind.RETURN_STMT,
            clang.CursorKind.GOTO_STMT,
            clang.CursorKind.LABEL_STMT,
            clang.CursorKind.UNEXPOSED_STMT,
            clang.CursorKind.DECL_STMT,
            clang.CursorKind.NULL_STMT,
            # C++特定语句
            clang.CursorKind.CXX_TRY_STMT,
            clang.CursorKind.CXX_CATCH_STMT,
            clang.CursorKind.CXX_FOR_RANGE_STMT,
            # 表达式类型 - 确保能够找到函数调用
            clang.CursorKind.UNEXPOSED_EXPR,
            clang.CursorKind.PAREN_EXPR,
            clang.CursorKind.INIT_LIST_EXPR,
            clang.CursorKind.LAMBDA_EXPR,
            clang.CursorKind.ARRAY_SUBSCRIPT_EXPR,
            clang.CursorKind.BINARY_OPERATOR,
            clang.CursorKind.UNARY_OPERATOR,
            clang.CursorKind.CONDITIONAL_OPERATOR,
            clang.CursorKind.CSTYLE_CAST_EXPR,
            clang.CursorKind.CXX_FUNCTIONAL_CAST_EXPR,
            clang.CursorKind.CXX_STATIC_CAST_EXPR,
            clang.CursorKind.CXX_DYNAMIC_CAST_EXPR,
            clang.CursorKind.CXX_REINTERPRET_CAST_EXPR,
            clang.CursorKind.CXX_CONST_CAST_EXPR,
            # 模板相关
            clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
            clang.CursorKind.TEMPLATE_REF,
        }
        return (cursor.kind in relevant_kinds or 
                cursor.kind.is_declaration() or 
                cursor.kind.is_statement() or 
                cursor.kind.is_expression())
    
    def _extract_declarations_only(self, cursor):
        """第一遍：只提取声明和定义"""
        if cursor.kind == clang.CursorKind.NAMESPACE:
            self._extract_namespace(cursor)
        elif cursor.kind in [clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL, clang.CursorKind.CLASS_TEMPLATE]:
            self._extract_class(cursor)
        elif cursor.kind in [clang.CursorKind.FUNCTION_DECL, clang.CursorKind.CXX_METHOD, 
                           clang.CursorKind.CONSTRUCTOR, clang.CursorKind.DESTRUCTOR, clang.CursorKind.FUNCTION_TEMPLATE]:
            self._extract_function(cursor)
    
    def _extract_relationships_only(self, cursor):
        """第二遍：提取关系（调用和继承）"""
        # 提取调用关系
        if cursor.is_definition() and cursor.kind in [
            clang.CursorKind.FUNCTION_DECL, clang.CursorKind.CXX_METHOD,
            clang.CursorKind.CONSTRUCTOR, clang.CursorKind.DESTRUCTOR,
            clang.CursorKind.FUNCTION_TEMPLATE
        ]:
            usr = cursor.get_usr()
            # 确保函数在我们的跟踪列表中，并且尚未处理
            if usr and usr in self.functions and usr not in self._functions_with_calls_extracted:
                self._extract_calls_for_function(cursor)
                self._functions_with_calls_extracted.add(usr)

        # 提取继承关系
        elif cursor.is_definition() and cursor.kind in [
            clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL, clang.CursorKind.CLASS_TEMPLATE
        ]:
            usr = cursor.get_usr()
            # 确保类在我们的跟踪列表中
            if usr and usr in self.classes:
                self._extract_inheritance_for_class(cursor)
    
    def _extract_namespace(self, cursor):
        """提取命名空间 - 优化版"""
        self._process_namespace_cursor(cursor)
    
    def _extract_class(self, cursor):
        """提取类 - 优化版"""
        self._process_class_cursor(cursor)
    
    def _extract_function(self, cursor):
        """提取函数 - 优化版"""
        self._process_function_cursor(cursor)
    
    

    def _process_function_cursor(self, cursor: clang.Cursor):
        """处理函数游标 - 线程安全和性能优化版"""
        usr = cursor.get_usr()
        if not usr:
            return
        
        # 确保主模板被处理
        if cursor.is_definition() and cursor.kind == clang.CursorKind.CLASS_DECL and hasattr(cursor.type, 'get_specialized_template'):
            primary_template_cursor = cursor.type.get_specialized_template()
            if primary_template_cursor:
                self._process_class_cursor(primary_template_cursor.get_declaration())

        if usr in self._processed_usrs:
            return
            
        file_path = cursor.location.file.name
        file_id = self.file_id_manager.get_file_id(file_path)
        if not file_id: return

        with self._lock:
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
                self._processed_usrs.add(usr)

    def _process_class_cursor(self, cursor: clang.Cursor):
        """处理类游标 - 线程安全和性能优化版"""
        usr = cursor.get_usr()
        if not usr:
            return
        
        # 确保主模板被处理
        if cursor.is_definition() and cursor.kind == clang.CursorKind.CLASS_DECL and hasattr(cursor.type, 'get_specialized_template'):
            primary_template_cursor = cursor.type.get_specialized_template()
            if primary_template_cursor:
                self._process_class_cursor(primary_template_cursor.get_declaration())

        if usr in self._processed_usrs:
            return
            
        file_path = cursor.location.file.name
        file_id = self.file_id_manager.get_file_id(file_path)
        if not file_id: return

        with self._lock:
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
                self._processed_usrs.add(usr)

    def _process_namespace_cursor(self, cursor: clang.Cursor):
        """处理命名空间游标 - 线程安全和性能优化版"""
        usr = cursor.get_usr()
        if not usr: return
            
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id: return

        with self._lock:
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
                if callee_cursor and callee_cursor.kind.is_declaration():
                    # 优先使用定义游标，因为它包含最准确的信息
                    target_cursor = callee_cursor.get_definition() or callee_cursor
                    
                    callee_usr = target_cursor.get_usr()
                    if not callee_usr or callee_usr == caller_usr:
                        continue

                    # 添加调用关系 (USR)
                    if callee_usr not in caller_func.calls_to:
                        caller_func.calls_to.append(callee_usr)

                    # 添加详细调用信息
                    def_loc = target_cursor.extent.start
                    file_id = self.file_id_manager.get_file_id(def_loc.file.name) if def_loc.file else None
                    
                    resolved_def_loc = None
                    if file_id and def_loc.line is not None:
                        resolved_def_loc = ResolvedDefinitionLocation(file_id=file_id, line=def_loc.line, column=def_loc.column)

                    cpp_call_info = CppCallInfo(
                        call_status_flags=self._get_call_status_flags(child),
                        resolved_overload=callee_usr,
                        resolved_definition_location=resolved_def_loc,
                        template_args=self._extract_template_arguments(child)
                    )
                    
                    caller_func.call_details.append(CallInfo(
                        to_usr_id=callee_usr,
                        line=child.location.line,
                        column=child.location.column,
                        cpp_call_info=cpp_call_info
                    ))

    def _get_primary_template_cursor(self, cursor: clang.Cursor) -> clang.Cursor:
        """
        如果给定的游标是模板特化，则返回其主模板的游标。
        否则，返回原始游标。
        """
        if hasattr(cursor, 'get_specialized_template'):
            primary_template = cursor.get_specialized_template()
            if primary_template:
                return primary_template
        return cursor

    def _extract_inheritance_for_class(self, cursor: clang.Cursor):
        cls = self.classes.get(cursor.get_usr())
        if not cls: return
        
        inheritance_list = []
        for base in cursor.get_children():
            if base.kind == clang.CursorKind.CXX_BASE_SPECIFIER:
                base_decl = base.type.get_declaration()
                
                if base_decl and base_decl.kind in [
                    clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL, 
                    clang.CursorKind.CLASS_TEMPLATE, clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION
                ]:
                    # 统一处理，获取主模板的USR以确保一致性
                    final_decl = self._get_primary_template_cursor(base_decl)
                    base_usr = final_decl.get_usr()

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
        """获取限定名称 - 缓存优化版"""
        cursor_key = f"{cursor.get_usr()}:{cursor.spelling}"
        
        # 检查缓存
        if cursor_key in self._qualified_name_cache:
            return self._qualified_name_cache[cursor_key]
        
        if cursor.kind.is_translation_unit():
            result = ""
        else:
            parent_name = self._get_qualified_name(cursor.semantic_parent)
            result = f"{parent_name}::{cursor.spelling}" if parent_name else cursor.spelling
        
        # 缓存结果
        self._qualified_name_cache[cursor_key] = result
        return result
        
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
