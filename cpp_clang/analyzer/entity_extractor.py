"""
Entity Extractor (符合 json_format.md v2.4) - 完整版，基于clang CursorKind系统性分析
"""

import logging
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
    """从 Clang AST 提取实体 (v2.6 - 基于clang CursorKind系统性分析的完整函数调用识别)"""

    def __init__(self, file_id_manager: DistributedFileIdManager):
        self.logger = get_logger()
        self.file_id_manager = file_id_manager
        self.code_extractor = CodeExtractor()
        
        # 确保线程锁在多进程环境中能正确创建
        self._initialize_thread_safe_data()
        
        # 性能优化：缓存常用查询结果
        self._cursor_cache: Dict[str, Any] = {}
        self._qualified_name_cache: Dict[str, str] = {}
        self._relevant_kinds = self._get_relevant_cursor_kinds()
    
    def _initialize_thread_safe_data(self):
        """初始化线程安全的数据结构 - 修复多进程序列化问题"""
        # 每次都重新创建线程锁，避免序列化问题
        self._lock = threading.RLock()
        self.functions: Dict[str, Function] = {}
        self.classes: Dict[str, Class] = {}
        self.namespaces: Dict[str, Namespace] = {}
        self.global_nodes: Dict[str, EntityNode] = {}
        self._processed_usrs: Set[str] = set()
        self._functions_with_calls_extracted: Set[str] = set()
        
    def _get_relevant_cursor_kinds(self) -> Set[clang.CursorKind]:
        """动态构建相关的CursorKind集合，基于clang CursorKind系统性分析"""
        relevant_kinds = {
            # 基本声明
            clang.CursorKind.NAMESPACE, clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL,
            clang.CursorKind.FUNCTION_DECL, clang.CursorKind.CXX_METHOD, clang.CursorKind.CONSTRUCTOR,
            clang.CursorKind.DESTRUCTOR,
            # 基本表达式和引用
            clang.CursorKind.CALL_EXPR, clang.CursorKind.MEMBER_REF_EXPR, clang.CursorKind.DECL_REF_EXPR,
            # 模板
            clang.CursorKind.CLASS_TEMPLATE, clang.CursorKind.FUNCTION_TEMPLATE,
            clang.CursorKind.TEMPLATE_TYPE_PARAMETER, clang.CursorKind.TEMPLATE_NON_TYPE_PARAMETER,
            # 语句
            clang.CursorKind.COMPOUND_STMT, clang.CursorKind.IF_STMT, clang.CursorKind.FOR_STMT,
            clang.CursorKind.WHILE_STMT, clang.CursorKind.DO_STMT, clang.CursorKind.SWITCH_STMT,
            clang.CursorKind.CASE_STMT, clang.CursorKind.DEFAULT_STMT, clang.CursorKind.RETURN_STMT,
            # 表达式
            clang.CursorKind.UNEXPOSED_EXPR, clang.CursorKind.INIT_LIST_EXPR, clang.CursorKind.LAMBDA_EXPR,
            clang.CursorKind.BINARY_OPERATOR, clang.CursorKind.UNARY_OPERATOR,
            # C++特定表达式
            clang.CursorKind.CXX_NEW_EXPR, clang.CursorKind.CXX_DELETE_EXPR,
            clang.CursorKind.CXX_FUNCTIONAL_CAST_EXPR, clang.CursorKind.CXX_STATIC_CAST_EXPR,
            clang.CursorKind.CXX_DYNAMIC_CAST_EXPR, clang.CursorKind.CXX_REINTERPRET_CAST_EXPR,
            clang.CursorKind.CXX_CONST_CAST_EXPR,
            # Objective-C表达式
            clang.CursorKind.OBJC_MESSAGE_EXPR, clang.CursorKind.OBJC_SELECTOR_EXPR,
            # Block表达式
            clang.CursorKind.BLOCK_EXPR,
        }
        
        # 动态添加可能不存在的Kind（基于clang分析结果）
        optional_kinds = [
            "CXX_OPERATOR_CALL_EXPR", "CXX_MEMBER_CALL_EXPR", "CXX_CONSTRUCT_EXPR",
            "CXX_TEMPORARY_OBJECT_EXPR", "CXX_TRY_STMT", "CXX_CATCH_STMT", "CXX_FOR_RANGE_STMT"
        ]
        
        for kind_name in optional_kinds:
            if hasattr(clang.CursorKind, kind_name):
                relevant_kinds.add(getattr(clang.CursorKind, kind_name))
                
        return relevant_kinds

    def extract_from_files(self, parsed_files: List[Any], config: Any) -> Dict[str, Any]:
        """提取实体的主要方法"""
        self._reset_state()
        
        # Pass 1: 提取声明和定义
        for parsed_file in parsed_files:
            if parsed_file.translation_unit:
                self._first_pass_visitor_optimized(parsed_file.translation_unit.cursor)

        # Pass 2: 提取关系
        for parsed_file in parsed_files:
            if parsed_file.translation_unit:
                self._second_pass_visitor_optimized(parsed_file.translation_unit.cursor)

        # Pass 3: 建立反向调用关系和namespace-function关联
        self._build_reverse_call_relationships()
        self._build_namespace_function_relationships()
        
        # 构建结果
        result = {
            "functions": self.functions,
            "classes": self.classes,
            "namespaces": self.namespaces,
            "global_nodes": {usr_id: node.to_dict() for usr_id, node in self.global_nodes.items()},
            "file_mappings": self.file_id_manager.get_file_mappings()
        }
        
        self.logger.info(f"实体提取完成。函数: {len(self.functions)}, 类: {len(self.classes)}, 命名空间: {len(self.namespaces)}")
        
        return result

    def _reset_state(self):
        """重置状态 - 修复多进程安全问题"""
        # 重新初始化线程安全的数据结构
        self._initialize_thread_safe_data()
        self._cursor_cache.clear()
        self._qualified_name_cache.clear()

    def _first_pass_visitor_optimized(self, cursor):
        """第一遍遍历 - 只提取声明和定义"""
        self._visit_optimized(cursor, self._extract_declarations_only)
    
    def _second_pass_visitor_optimized(self, cursor):
        """第二遍遍历 - 只提取关系"""
        self._visit_optimized(cursor, self._extract_relationships_only)
    
    def _visit_optimized(self, cursor, extract_func):
        """优化的AST遍历算法"""
        if not cursor:
            return
            
        # 使用栈而不是递归
        stack = [cursor]
        visited = set()
        
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
            
            # 只遍历相关的子节点
            for child in current.get_children():
                if self._should_visit_child(child):
                    stack.append(child)
    
    def _should_skip_cursor(self, cursor):
        """判断是否应该跳过这个cursor"""
        skip_kinds = {
            clang.CursorKind.UNEXPOSED_DECL,
            clang.CursorKind.MACRO_DEFINITION,
            clang.CursorKind.INCLUSION_DIRECTIVE,
            clang.CursorKind.UNEXPOSED_STMT,
        }
        return cursor.kind in skip_kinds
    
    def _should_visit_child(self, cursor):
        """判断是否应该访问这个子节点"""
        return (cursor.kind in self._relevant_kinds or 
                cursor.kind.is_declaration() or 
                cursor.kind.is_statement() or 
                cursor.kind.is_expression() or
                cursor.kind.is_reference())
    
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
        if cursor.kind in [
            clang.CursorKind.FUNCTION_DECL, clang.CursorKind.CXX_METHOD,
            clang.CursorKind.CONSTRUCTOR, clang.CursorKind.DESTRUCTOR,
            clang.CursorKind.FUNCTION_TEMPLATE
        ]:
            usr = cursor.get_usr()
            if usr and usr in self.functions and usr not in self._functions_with_calls_extracted:
                self._extract_calls_for_function(cursor)
                self._functions_with_calls_extracted.add(usr)

        # 提取继承关系
        elif cursor.kind in [
            clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL, clang.CursorKind.CLASS_TEMPLATE
        ]:
            usr = cursor.get_usr()
            if usr and usr in self.classes:
                self._extract_inheritance_for_class(cursor)
    
    def _extract_namespace(self, cursor):
        """提取命名空间"""
        self._process_namespace_cursor(cursor)
    
    def _extract_class(self, cursor):
        """提取类"""
        self._process_class_cursor(cursor)
    
    def _extract_function(self, cursor):
        """提取函数"""
        self._process_function_cursor(cursor)

    def _process_function_cursor(self, cursor: clang.Cursor):
        """处理函数游标"""
        usr = cursor.get_usr()
        if not usr:
            return

        if usr in self._processed_usrs:
            return
            
        file_path = cursor.location.file.name
        file_id = self.file_id_manager.get_file_id(file_path)
        if not file_id: 
            return

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
        """处理类游标"""
        usr = cursor.get_usr()
        if not usr:
            return

        if usr in self._processed_usrs:
            return
            
        file_path = cursor.location.file.name
        file_id = self.file_id_manager.get_file_id(file_path)
        if not file_id: 
            self.logger.error(f"_process_class_cursor:无法获取文件ID for {file_path}")
            return 

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
        """处理命名空间游标"""
        usr = cursor.get_usr()
        if not usr: 
            return
            
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id: 
            self.logger.error(f"_process_namespace_cursor:无法获取文件ID for {cursor.location.file.name}")
            return

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
        """从游标创建函数对象"""
        usr = cursor.get_usr()
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id: 
            raise ValueError(f"无法获取文件ID for {cursor.location.file.name}")
        
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
        """从游标创建类对象"""
        usr = cursor.get_usr()
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id: 
            raise ValueError(f"无法获取文件ID for {cursor.location.file.name}")
            
        qualified_name = self._get_qualified_name(cursor)
        location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)

        member_methods = []
        for child in cursor.get_children():
            if child.kind in [
                clang.CursorKind.CXX_METHOD, clang.CursorKind.CONSTRUCTOR, 
                clang.CursorKind.DESTRUCTOR, clang.CursorKind.FUNCTION_TEMPLATE
            ]:
                child_usr = child.get_usr()
                if child_usr:
                    member_methods.append(child_usr)
                    # 确保成员函数也被添加到functions字典中
                    if child_usr not in self.functions:
                        member_func = self._create_function_from_cursor(child)
                        self.functions[child_usr] = member_func
                        self.global_nodes[child_usr] = EntityNode(child_usr, "function", member_func)

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
        """从游标创建命名空间对象"""
        usr = cursor.get_usr()
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id: 
            raise ValueError(f"无法获取文件ID for {cursor.location.file.name}")
            
        location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
        return Namespace(
            name=cursor.spelling, qualified_name=self._get_qualified_name(cursor),
            usr_id=usr, definition_file_id=file_id, line=cursor.location.line,
            declaration_locations=[location], definition_location=location, usr=usr
        )

    def _update_function_with_definition(self, func: Function, cursor: clang.Cursor):
        """用定义更新函数"""
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id: 
            return
        
        func.definition_file_id = file_id
        func.definition_location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
        func.is_definition = True
        func.start_line = cursor.extent.start.line
        func.end_line = cursor.extent.end.line
        func.code_content = self.code_extractor.extract_function_code(cursor)
        func.cpp_extensions.mangled_name = cursor.mangled_name

    def _update_class_with_definition(self, cls: Class, cursor: clang.Cursor):
        """用定义更新类"""
        file_id = self.file_id_manager.get_file_id(cursor.location.file.name)
        if not file_id: 
            return
        
        cls.definition_file_id = file_id
        cls.definition_location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
        cls.is_definition = True
        
        cls.methods = []
        for child in cursor.get_children():
            if child.kind in [
                clang.CursorKind.CXX_METHOD, clang.CursorKind.CONSTRUCTOR, 
                clang.CursorKind.DESTRUCTOR, clang.CursorKind.FUNCTION_TEMPLATE
            ]:
                child_usr = child.get_usr()
                if child_usr:
                    cls.methods.append(child_usr)
                    # 确保成员函数也被添加到functions字典中
                    if child_usr not in self.functions:
                        member_func = self._create_function_from_cursor(child)
                        self.functions[child_usr] = member_func
                        self.global_nodes[child_usr] = EntityNode(child_usr, "function", member_func)

    def _extract_calls_for_function(self, cursor: clang.Cursor):
        """提取函数调用关系 - 完整版，基于clang CursorKind系统性分析"""
        caller_usr = cursor.get_usr()
        caller_func = self.functions.get(caller_usr)
        if not caller_func:
            return

        # 使用完整的调用提取逻辑，识别所有类型的函数调用
        for child in cursor.get_children():
            self._extract_calls_recursive(child, caller_func, caller_usr)

    def _extract_calls_recursive(self, cursor: clang.Cursor, caller_func: Function, caller_usr: str):
        """递归提取调用 - 完整版，基于clang CursorKind系统性分析"""
        
        callee_cursor = None
        call_type = None
        
        # === 基于clang分析的完整函数调用类型处理 ===
        
        if cursor.kind == clang.CursorKind.CALL_EXPR:
            # 普通函数调用：func()
            callee_cursor = cursor.referenced
            call_type = "call_expr"
            
        elif cursor.kind == clang.CursorKind.MEMBER_REF_EXPR:
            # 成员函数引用：obj.method 或 obj->method
            callee_cursor = cursor.referenced
            call_type = "member_ref_expr"
            
        elif cursor.kind == clang.CursorKind.DECL_REF_EXPR:
            # 函数声明引用：直接引用函数名
            callee_cursor = cursor.referenced
            # 验证引用的是函数类型
            if callee_cursor and callee_cursor.kind in [
                clang.CursorKind.FUNCTION_DECL, clang.CursorKind.CXX_METHOD,
                clang.CursorKind.CONSTRUCTOR, clang.CursorKind.DESTRUCTOR,
                clang.CursorKind.FUNCTION_TEMPLATE
            ]:
                call_type = "decl_ref_expr"
            else:
                callee_cursor = None  # 不是函数引用，跳过
                
        # === C++特定调用类型（动态检查支持） ===
        elif hasattr(clang.CursorKind, 'CXX_MEMBER_CALL_EXPR') and cursor.kind == clang.CursorKind.CXX_MEMBER_CALL_EXPR:
            # C++成员函数调用：obj.method() 或 obj->method()
            callee_cursor = cursor.referenced
            call_type = "cxx_member_call_expr"
            
        elif hasattr(clang.CursorKind, 'CXX_OPERATOR_CALL_EXPR') and cursor.kind == clang.CursorKind.CXX_OPERATOR_CALL_EXPR:
            # C++操作符重载调用：operator+(), operator[]() 等
            callee_cursor = cursor.referenced
            call_type = "cxx_operator_call_expr"
            
        elif hasattr(clang.CursorKind, 'CXX_CONSTRUCT_EXPR') and cursor.kind == clang.CursorKind.CXX_CONSTRUCT_EXPR:
            # C++构造函数调用：MyClass() 或隐式构造
            callee_cursor = cursor.referenced
            call_type = "cxx_construct_expr"
            
        elif hasattr(clang.CursorKind, 'CXX_TEMPORARY_OBJECT_EXPR') and cursor.kind == clang.CursorKind.CXX_TEMPORARY_OBJECT_EXPR:
            # C++临时对象表达式：MyClass(args)
            callee_cursor = cursor.referenced
            call_type = "cxx_temporary_object_expr"
            
        elif cursor.kind == clang.CursorKind.CXX_NEW_EXPR:
            # C++ new表达式：new MyClass()
            # 特殊处理：查找内部的构造函数调用
            for child in cursor.get_children():
                if hasattr(clang.CursorKind, 'CXX_CONSTRUCT_EXPR') and child.kind == clang.CursorKind.CXX_CONSTRUCT_EXPR:
                    callee_cursor = child.referenced
                    call_type = "cxx_new_expr"
                    break
            # 如果没找到构造函数，可能是内置类型
            if not callee_cursor:
                callee_cursor = cursor.referenced
                call_type = "cxx_new_expr"
                
        elif cursor.kind == clang.CursorKind.CXX_DELETE_EXPR:
            # C++ delete表达式：delete ptr
            callee_cursor = cursor.referenced
            call_type = "cxx_delete_expr"
            
        # === C++类型转换调用 ===
        elif cursor.kind == clang.CursorKind.CXX_FUNCTIONAL_CAST_EXPR:
            # C++函数式类型转换：Type(value)
            callee_cursor = cursor.referenced
            call_type = "cxx_functional_cast_expr"
            
        elif cursor.kind == clang.CursorKind.CXX_STATIC_CAST_EXPR:
            # C++静态类型转换：static_cast<Type>(value)
            callee_cursor = cursor.referenced
            call_type = "cxx_static_cast_expr"
            
        elif cursor.kind == clang.CursorKind.CXX_DYNAMIC_CAST_EXPR:
            # C++动态类型转换：dynamic_cast<Type>(value)
            callee_cursor = cursor.referenced
            call_type = "cxx_dynamic_cast_expr"
            
        elif cursor.kind == clang.CursorKind.CXX_REINTERPRET_CAST_EXPR:
            # C++重解释类型转换：reinterpret_cast<Type>(value)
            callee_cursor = cursor.referenced
            call_type = "cxx_reinterpret_cast_expr"
            
        elif cursor.kind == clang.CursorKind.CXX_CONST_CAST_EXPR:
            # C++常量类型转换：const_cast<Type>(value)
            callee_cursor = cursor.referenced
            call_type = "cxx_const_cast_expr"
            
        # === Lambda和Block表达式 ===
        elif cursor.kind == clang.CursorKind.LAMBDA_EXPR:
            # Lambda表达式定义（不是调用，但可能包含调用）
            # 通常不需要记录lambda定义本身，而是其内部的调用
            pass
            
        elif cursor.kind == clang.CursorKind.BLOCK_EXPR:
            # Block表达式（Objective-C/C++扩展）
            callee_cursor = cursor.referenced
            call_type = "block_expr"
            
        # === Objective-C调用 ===
        elif cursor.kind == clang.CursorKind.OBJC_MESSAGE_EXPR:
            # Objective-C消息发送：[obj method]
            callee_cursor = cursor.referenced
            call_type = "objc_message_expr"
            
        elif cursor.kind == clang.CursorKind.OBJC_SELECTOR_EXPR:
            # Objective-C选择器表达式：@selector(method)
            callee_cursor = cursor.referenced
            call_type = "objc_selector_expr"
        
        # === 处理找到的调用 ===
        if callee_cursor and callee_cursor.kind.is_declaration():
            callee_usr = callee_cursor.get_usr()
            if callee_usr and callee_usr != caller_usr:
                # 添加到调用列表
                if callee_usr not in caller_func.calls_to:
                    caller_func.calls_to.append(callee_usr)
                
                # 记录调用详情（用于调试和分析）
                self.logger.debug(f"发现{call_type}: {caller_usr} -> {callee_usr} ({callee_cursor.spelling})")
        
        # === 递归处理子节点 ===
        for child in cursor.get_children():
            self._extract_calls_recursive(child, caller_func, caller_usr)

    def _extract_inheritance_for_class(self, cursor: clang.Cursor):
        """提取类的继承关系"""
        cls = self.classes.get(cursor.get_usr())
        if not cls: 
            return
        
        inheritance_list = []
        for base in cursor.get_children():
            if base.kind == clang.CursorKind.CXX_BASE_SPECIFIER:
                base_decl = base.type.get_declaration()
                
                if base_decl and base_decl.kind in [
                    clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL, 
                    clang.CursorKind.CLASS_TEMPLATE, clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION
                ]:
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
        """建立反向调用关系"""
        for caller_usr, caller_func in self.functions.items():
            for callee_usr in caller_func.calls_to:
                callee_func = self.functions.get(callee_usr)
                if callee_func and caller_usr not in callee_func.called_by:
                    callee_func.called_by.append(caller_usr)
    
    def _build_namespace_function_relationships(self):
        """建立命名空间与函数的关联关系 - 基于AST父子关系的正确实现"""
        self.logger.info("开始建立命名空间与函数的关联关系（基于AST结构）...")
        
        # 第一步：为每个命名空间初始化函数列表
        for ns_usr, ns_obj in self.namespaces.items():
            if not hasattr(ns_obj, 'functions') or ns_obj.functions is None:
                ns_obj.functions = []
        
        # 第二步：遍历所有函数，基于USR结构建立正确的关联
        namespace_function_count = 0
        global_function_count = 0
        class_method_count = 0
        
        for func_usr, func in self.functions.items():
            # 使用USR来确定函数的归属，这比字符串解析更可靠
            func_namespace = self._extract_namespace_from_usr(func_usr)
            func_class = self._extract_class_from_usr(func_usr)
            
            if func_class:
                # 这是一个类方法，应该归属于类而不是命名空间
                class_method_count += 1
                continue
            elif func_namespace:
                # 这是一个命名空间函数，找到对应的命名空间
                for ns_usr, ns_obj in self.namespaces.items():
                    if hasattr(ns_obj, 'qualified_name') and ns_obj.qualified_name == func_namespace:
                        ns_obj.functions.append(func_usr)
                        namespace_function_count += 1
                        break
            else:
                # 全局函数
                global_function_count += 1
        
        self.logger.info(f"函数关联完成: 命名空间函数={namespace_function_count}, 类方法={class_method_count}, 全局函数={global_function_count}")
    
    def _extract_namespace_from_usr(self, usr: str) -> str:
        """从USR中提取命名空间信息"""
        # USR格式分析：c:@N@std@F@function_name 表示 std命名空间中的函数
        if not usr.startswith('c:@'):
            return ""
        
        parts = usr.split('@')
        namespace_parts = []
        
        i = 1  # 跳过 'c:'
        while i < len(parts):
            if parts[i] == 'N' and i + 1 < len(parts):
                # 找到命名空间标记
                namespace_parts.append(parts[i + 1])
                i += 2
            elif parts[i] in ['F', 'S', 'ST']:
                # 到达函数或结构体/模板定义，停止
                break
            else:
                i += 1
        
        return '::'.join(namespace_parts) if namespace_parts else ""
    
    def _extract_class_from_usr(self, usr: str) -> str:
        """从USR中提取类信息"""
        # USR格式分析：c:@S@ClassName@F@method_name 表示类中的方法
        if not usr.startswith('c:@'):
            return ""
        
        parts = usr.split('@')
        
        for i, part in enumerate(parts):
            if part == 'S' and i + 1 < len(parts):
                # 找到结构体/类标记
                return parts[i + 1]
        
        return ""

    def _get_qualified_name(self, cursor: clang.Cursor) -> str:
        """获取限定名称"""
        cursor_key = f"{cursor.get_usr()}:{cursor.spelling}"
        
        if cursor_key in self._qualified_name_cache:
            return self._qualified_name_cache[cursor_key]
        
        if cursor.kind.is_translation_unit():
            result = ""
        else:
            parent_name = self._get_qualified_name(cursor.semantic_parent)
            result = f"{parent_name}::{cursor.spelling}" if parent_name else cursor.spelling
        
        self._qualified_name_cache[cursor_key] = result
        return result
        
    def _get_namespace_str(self, cursor: clang.Cursor) -> str:
        """获取命名空间字符串"""
        parts = []
        parent = cursor.semantic_parent
        while parent and not parent.kind.is_translation_unit():
            if parent.kind == clang.CursorKind.NAMESPACE:
                parts.append(parent.spelling)
            parent = parent.semantic_parent
        return "::".join(reversed(parts))

    def _get_function_status_flags(self, c: clang.Cursor) -> int:
        """获取函数状态标志"""
        flags = 0
        if c.is_virtual_method(): flags |= FunctionStatusFlags.FUNC_IS_VIRTUAL
        if c.is_pure_virtual_method(): flags |= FunctionStatusFlags.FUNC_IS_PURE_VIRTUAL
        if c.is_static_method(): flags |= FunctionStatusFlags.FUNC_IS_STATIC
        if c.is_const_method(): flags |= FunctionStatusFlags.FUNC_IS_CONST
        return flags

    def _get_class_status_flags(self, c: clang.Cursor) -> int:
        """获取类状态标志"""
        flags = 0
        if c.is_abstract_record(): flags |= ClassStatusFlags.CLASS_IS_ABSTRACT
        is_poly_func = getattr(c, 'is_polymorphic', lambda: False)
        if is_poly_func(): flags |= ClassStatusFlags.CLASS_IS_POLYMORPHIC
        return flags

    def _get_call_status_flags(self, c: clang.Cursor) -> int:
        """获取调用状态标志"""
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
            if isinstance(type_or_cursor, clang.Cursor):
                num_args = type_or_cursor.get_num_template_arguments()
                for i in range(num_args):
                    arg_kind = type_or_cursor.get_template_argument_kind(i)
                    if arg_kind == clang.TemplateArgumentKind.TYPE:
                        args.append(type_or_cursor.get_template_argument_type(i).spelling)
                    elif arg_kind == clang.TemplateArgumentKind.INTEGRAL:
                        args.append(str(type_or_cursor.get_template_argument_value(i)))
            elif isinstance(type_or_cursor, clang.Type):
                # Type对象的模板参数提取需要特殊处理
                try:
                    num_args = type_or_cursor.get_num_template_arguments()
                    for i in range(num_args):
                        # Type对象使用get_template_argument_type方法
                        arg_type = type_or_cursor.get_template_argument_type(i)
                        if arg_type and arg_type.spelling:
                            args.append(arg_type.spelling)
                except AttributeError:
                    # 如果Type对象不支持模板参数提取，尝试从spelling中解析
                    type_spelling = type_or_cursor.spelling
                    if '<' in type_spelling and '>' in type_spelling:
                        # 简单的模板参数解析：提取<>内的内容
                        start = type_spelling.find('<')
                        end = type_spelling.rfind('>')
                        if start != -1 and end != -1 and end > start:
                            template_args = type_spelling[start+1:end]
                            # 简单分割（不处理嵌套模板）
                            args.extend([arg.strip() for arg in template_args.split(',') if arg.strip()])
        except Exception as e:
            self.logger.debug(f"提取模板实参时出错: {e}")
        return args
