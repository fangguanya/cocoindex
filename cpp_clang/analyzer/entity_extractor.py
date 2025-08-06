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
from .dynamic_template_resolver import DynamicTemplateResolver
from .shared_class_cache import SharedClassCache, get_shared_class_cache
from .data_structures import (
    Function, Class, Namespace, CppExtensions, CppOopExtensions,
    CallInfo, CppCallInfo, Parameter, Location, ResolvedDefinitionLocation, InheritanceInfo,
    FunctionStatusFlags, ClassStatusFlags, CallStatusFlags, EntityNode, TemplateParameter,
    MemberVariable
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

    def __init__(self, file_id_manager: DistributedFileIdManager, project_root: str = None):
        self.logger = get_logger()
        self.file_id_manager = file_id_manager
        self.code_extractor = CodeExtractor()
        self.project_root = project_root
        self.template_resolver = DynamicTemplateResolver(project_root=project_root)  # 添加动态模板解析器
        
        # 统一的共享类缓存（处理所有类型：泛型+普通）
        self.shared_class_cache: Optional[SharedClassCache] = None
        if project_root:
            try:
                self.shared_class_cache = get_shared_class_cache(project_root)
                self.logger.debug("已启用修复版的统一共享类缓存")
            except Exception as e:
                self.logger.debug(f"无法初始化共享类缓存: {e}")
                self.shared_class_cache = None
        
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
        self.member_variables: Dict[str, MemberVariable] = {}  # 成员变量字典
        self.global_nodes: Dict[str, EntityNode] = {}
        self._processed_usrs: Set[str] = set()
        self._functions_with_calls_extracted: Set[str] = set()
        
    def _get_relevant_cursor_kinds(self) -> Set[clang.CursorKind]:
        """动态构建相关的CursorKind集合，基于clang CursorKind系统性分析"""
        relevant_kinds = {
            # 基本声明
            clang.CursorKind.NAMESPACE, clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL,
            clang.CursorKind.FUNCTION_DECL, clang.CursorKind.CXX_METHOD, clang.CursorKind.CONSTRUCTOR,
            clang.CursorKind.DESTRUCTOR, clang.CursorKind.FIELD_DECL,
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

        # Pass 2.5: 模板解析（在所有基础类型提取完成后进行）
        for parsed_file in parsed_files:
            if parsed_file.translation_unit:
                self._template_resolution_pass(parsed_file.translation_unit.cursor)

        # Pass 3: 建立反向调用关系和namespace-function关联
        self._build_reverse_call_relationships()
        self._build_namespace_function_relationships()
        
        # Pass 4: 后处理 - 建立类和方法的关联（修复孤儿函数问题）
        self._build_class_method_relationships()
        
        # 构建结果
        result = {
            "functions": self.functions,
            "classes": self.classes,
            "namespaces": self.namespaces,
            "member_variables": self.member_variables,
            "global_nodes": {usr_id: node.to_dict() for usr_id, node in self.global_nodes.items()},
            "file_mappings": self.file_id_manager.get_file_mappings()
        }
        
        self.logger.info(f"实体提取完成。函数: {len(self.functions)}, 类: {len(self.classes)}, 命名空间: {len(self.namespaces)}, 成员变量: {len(self.member_variables)}")
        
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
    
    def _template_resolution_pass(self, cursor):
        """模板解析阶段：处理所有模板类型（在基础类型提取完成后）"""
        self._visit_optimized(cursor, self._resolve_template_types_only)
    
    def _resolve_template_types_only(self, cursor):
        """专门处理模板类型解析"""
        try:
            # 检查是否为模板相关的cursor
            if not self._is_template_cursor(cursor):
                return
            
            # 获取类型信息
            template_usage = self._extract_template_usage_from_cursor(cursor)
            if not template_usage:
                return
            
            type_name = template_usage.get('type_name')
            if not type_name:
                return
            
            # 检查是否已经解析过
            if self.template_resolver.is_type_fully_resolved(type_name):
                return
            
            self.logger.debug(f"模板解析阶段处理: {type_name}")
            
            # 解析这个模板类型
            new_classes = self.template_resolver.resolve_template_from_cursor(cursor, self.classes)
            
            # 将新发现的类型添加到当前类字典中
            for class_usr, class_obj in new_classes.items():
                if class_usr not in self.classes:
                    self.classes[class_usr] = class_obj
                    self.logger.debug(f"模板解析发现新类型: {type_name} -> {class_usr}")
            
        except Exception as e:
            self.logger.debug(f"模板解析时出错: {e}")
            # 不抛出异常，继续处理其他模板
    
    def _visit_optimized(self, cursor, extract_func):
        """优化的AST遍历算法 - 支持按需泛型类型解析"""
        if not cursor:
            return
            
        # 使用栈而不是递归
        stack = [cursor]
        visited = set()
        
        while stack:
            current = stack.pop()
            
            # 避免重复访问同一个cursor - 使用更可靠的标识
            try:
                cursor_usr = current.get_usr()
                cursor_location = (current.location.file.name if current.location.file else "", current.location.line)
                cursor_hash = hash((cursor_usr, current.spelling, cursor_location, current.kind))
                if cursor_hash in visited:
                    continue
                visited.add(cursor_hash)
            except:
                # 如果无法获取cursor信息，跳过
                continue
            
            # 快速跳过不需要的cursor类型
            if self._should_skip_cursor(current):
                continue
            
            # 执行提取函数（移除模板按需解析，避免stack无限增长）
            extract_func(current)
            
            # 只遍历相关的子节点
            for child in current.get_children():
                if self._should_visit_child(child):
                    stack.append(child)
    
    def _handle_template_type_on_demand(self, cursor):
        """在遍历时遇到泛型类型立即处理"""
        try:
            # 检查是否为模板相关的cursor
            if not self._is_template_cursor(cursor):
                return
            
            # 获取类型信息
            template_usage = self._extract_template_usage_from_cursor(cursor)
            if not template_usage:
                return
            
            type_name = template_usage.get('type_name')
            if not type_name:
                return
            
            # 检查是否已经解析过
            if self.template_resolver.is_type_fully_resolved(type_name):
                return
            
            # 立即解析这个泛型类型
            new_classes = self.template_resolver.resolve_template_from_cursor(cursor, self.classes)
            
            # 将新发现的类型添加到当前类字典中
            for class_usr, class_obj in new_classes.items():
                if class_usr not in self.classes:
                    self.classes[class_usr] = class_obj
                    self.logger.debug(f"遍历时发现新泛型类型: {type_name} -> {class_usr}")
            
            # 标记类型为已解析，传递完整信息
            self.template_resolver.mark_type_as_resolved(
                type_name=type_name,
                resolved_classes=new_classes
            )
            
        except Exception as e:
            self.logger.debug(f"遍历时处理泛型类型出错: {e}")
    
    def _is_template_cursor(self, cursor) -> bool:
        """判断cursor是否为模板相关的cursor"""
        try:
            import clang.cindex as clang
            
            # 直接检查cursor类型
            template_kinds = {
                clang.CursorKind.CLASS_TEMPLATE,
                clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
                clang.CursorKind.FUNCTION_TEMPLATE,
                clang.CursorKind.TEMPLATE_TYPE_PARAMETER,
                clang.CursorKind.TEMPLATE_NON_TYPE_PARAMETER,
                clang.CursorKind.TEMPLATE_TEMPLATE_PARAMETER,
                # 也包括可能的模板实例化
                clang.CursorKind.CLASS_DECL,
                clang.CursorKind.STRUCT_DECL,
                clang.CursorKind.FIELD_DECL,
                clang.CursorKind.VAR_DECL,
                clang.CursorKind.PARM_DECL,
            }
            
            if cursor.kind not in template_kinds:
                return False
            
            # 进一步检查是否涉及模板类型
            if hasattr(cursor, 'type') and cursor.type:
                type_spelling = cursor.type.spelling
                if '<' in type_spelling and '>' in type_spelling:
                    return True
            
            # 检查cursor本身的拼写
            if cursor.spelling and ('<' in cursor.spelling and '>' in cursor.spelling):
                return True
                
            # 检查是否为模板实例化
            if hasattr(cursor, 'get_num_template_arguments'):
                try:
                    if cursor.get_num_template_arguments() > 0:
                        return True
                except:
                    pass
                    
        except Exception as e:
            self.logger.debug(f"检查模板cursor时出错: {e}")
        
        return False
    
    def _extract_template_usage_from_cursor(self, cursor) -> Optional[Dict[str, Any]]:
        """从cursor中提取模板使用信息"""
        try:
            # 获取类型名
            type_name = None
            if hasattr(cursor, 'type') and cursor.type:
                type_name = cursor.type.spelling
            elif cursor.spelling:
                type_name = cursor.spelling
            
            if not type_name or not self._is_template_type(type_name):
                return None
            
            # 确定上下文
            context = 'unknown'
            import clang.cindex as clang
            if cursor.kind == clang.CursorKind.CLASS_DECL:
                context = 'class_declaration'
            elif cursor.kind == clang.CursorKind.FIELD_DECL:
                context = 'member_variable'
            elif cursor.kind == clang.CursorKind.PARM_DECL:
                context = 'parameter'
            elif cursor.kind == clang.CursorKind.VAR_DECL:
                context = 'variable'
            elif cursor.kind in [clang.CursorKind.CLASS_TEMPLATE, clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION]:
                context = 'template_declaration'
            
            return {
                'type_name': type_name,
                'context': context,
                'cursor': cursor,
                'location': f"{cursor.location.file}:{cursor.location.line}" if cursor.location.file else "unknown"
            }
            
        except Exception as e:
            self.logger.debug(f"从cursor提取模板使用信息时出错: {e}")
            return None
    
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
        """判断是否应该访问这个子节点（限制范围避免无限增长）"""
        # 只访问声明类型的节点，避免遍历过多表达式和语句
        return cursor.kind.is_declaration()
    
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
        """处理类游标（多进程安全版本）"""
        usr = cursor.get_usr()
        if not usr:
            return
            
        file_path = cursor.location.file.name
        file_id = self.file_id_manager.get_file_id(file_path)
        if not file_id: 
            self.logger.error(f"_process_class_cursor:无法获取文件ID for {file_path}")
            return 

        qualified_name = self._get_qualified_name(cursor)
        
        # 检查统一共享缓存
        if self.shared_class_cache:
            if self.shared_class_cache.is_class_resolved(usr, qualified_name):
                cached_class_data = self.shared_class_cache.get_resolved_class(usr, qualified_name)
                if cached_class_data:
                    # 从缓存重构类对象
                    cached_class = self._reconstruct_class_from_cache(cached_class_data)
                    if cached_class:
                        with self._lock:
                            if usr not in self.classes:
                                self.classes[usr] = cached_class
                                self.global_nodes[usr] = EntityNode(usr, "class", cached_class)
                                self._processed_usrs.add(usr)
                        self.logger.debug(f"类缓存命中: {qualified_name}")
                        return

        with self._lock:
            if usr in self.classes:
                existing_class = self.classes[usr]
                if cursor.is_definition() and not existing_class.is_definition:
                    # 关键修复：即使USR已处理过，也要更新类定义
                    self._update_class_with_definition(existing_class, cursor)
                elif not cursor.is_definition():
                    location = Location(file_id=file_id, line=cursor.location.line, column=cursor.location.column)
                    if location not in existing_class.declaration_locations:
                        existing_class.declaration_locations.append(location)
            else:
                # 检查是否正在被其他进程解析
                if self.shared_class_cache and self.shared_class_cache.is_class_being_resolved(usr, qualified_name):
                    self.logger.debug(f"类 {qualified_name} 正在被其他进程解析，等待...")
                    import time
                    time.sleep(0.05)
                    # 再次检查缓存
                    cached_class_data = self.shared_class_cache.get_resolved_class(usr, qualified_name)
                    if cached_class_data:
                        cached_class = self._reconstruct_class_from_cache(cached_class_data)
                        if cached_class:
                            self.classes[usr] = cached_class
                            self.global_nodes[usr] = EntityNode(usr, "class", cached_class)
                            self._processed_usrs.add(usr)
                            return
                
                # 尝试获取解析锁
                if self.shared_class_cache:
                    if not self.shared_class_cache.try_acquire_class_resolution_lock(usr, qualified_name):
                        self.logger.debug(f"无法获取类 {qualified_name} 的解析锁")
                        return
                
                try:
                    cls = self._create_class_from_cursor(cursor)
                    self.classes[usr] = cls
                    self.global_nodes[usr] = EntityNode(usr, "class", cls)
                    
                    # 将结果存入共享缓存
                    if self.shared_class_cache:
                        class_data = self._serialize_class_for_cache(cls)
                        is_template = cursor.kind in [clang.CursorKind.CLASS_TEMPLATE,
                                                    clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION]
                        self.shared_class_cache.mark_class_resolved(
                            usr=usr,
                            qualified_name=qualified_name,
                            class_data=class_data,
                            is_template=is_template
                        )
                        
                except Exception as e:
                    # 标记解析失败
                    if self.shared_class_cache:
                        self.shared_class_cache.mark_class_failed(usr, qualified_name, str(e))
                    raise
            
            # 标记USR为已处理，但在检查定义更新之后
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
        member_vars = []
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
            elif child.kind == clang.CursorKind.FIELD_DECL:
                child_usr = child.get_usr()
                if child_usr:
                    member_vars.append(child_usr)
                    # 创建成员变量对象
                    if child_usr not in self.member_variables:
                        member_var = self._create_member_variable_from_cursor(child)
                        self.member_variables[child_usr] = member_var
                        self.global_nodes[child_usr] = EntityNode(child_usr, "member_variable", member_var)

        cpp_oop_ext = CppOopExtensions(
            qualified_name=qualified_name, namespace=self._get_namespace_str(cursor),
            type=cursor.kind.name.lower().replace("_decl", ""),
            class_status_flags=self._get_class_status_flags(cursor),
            template_parameters=self._extract_template_parameters(cursor),
            template_specialization_args=self._extract_template_arguments(cursor.type),
            usr=usr,
        )

        cls = Class(
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
        cls.member_variables = member_vars  # 设置成员变量列表
        return cls

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
        
        # 修复：不要重置methods列表，而是合并新发现的方法和成员变量
        existing_methods = set(cls.methods) if cls.methods else set()
        existing_member_vars = set(cls.member_variables) if cls.member_variables else set()
        
        for child in cursor.get_children():
            if child.kind in [
                clang.CursorKind.CXX_METHOD, clang.CursorKind.CONSTRUCTOR, 
                clang.CursorKind.DESTRUCTOR, clang.CursorKind.FUNCTION_TEMPLATE
            ]:
                child_usr = child.get_usr()
                if child_usr and child_usr not in existing_methods:
                    existing_methods.add(child_usr)
                    # 确保成员函数也被添加到functions字典中
                    if child_usr not in self.functions:
                        member_func = self._create_function_from_cursor(child)
                        self.functions[child_usr] = member_func
                        self.global_nodes[child_usr] = EntityNode(child_usr, "function", member_func)
            elif child.kind == clang.CursorKind.FIELD_DECL:
                child_usr = child.get_usr()
                if child_usr and child_usr not in existing_member_vars:
                    existing_member_vars.add(child_usr)
                    # 确保成员变量也被添加到member_variables字典中
                    if child_usr not in self.member_variables:
                        member_var = self._create_member_variable_from_cursor(child)
                        self.member_variables[child_usr] = member_var
                        self.global_nodes[child_usr] = EntityNode(child_usr, "member_variable", member_var)
        
        # 更新方法和成员变量列表
        cls.methods = list(existing_methods)
        cls.member_variables = list(existing_member_vars)

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
        """提取类的继承关系（多进程安全版本）"""
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
                        
                        # 确保父类已被解析（多进程安全）
                        self._ensure_parent_class_resolved(base_decl)
                        
                        # 更新共享缓存中的继承关系
                        if self.shared_class_cache:
                            child_qualified_name = self._get_qualified_name(cursor)
                            parent_qualified_name = self._get_qualified_name(base_decl)
                            self.shared_class_cache.update_inheritance_mapping(
                                parent_usr=base_usr,
                                parent_name=parent_qualified_name,
                                child_usr=cursor.get_usr(),
                                child_name=child_qualified_name
                            )
                        
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
            # 使用cursor来确定函数的归属，这比USR字符串解析更可靠
            func_cursor = self.template_resolver.get_cursor_by_usr(func_usr)
            func_namespace = self._extract_namespace_from_cursor(func_cursor)
            func_class_cursor = self._extract_class_cursor_from_function_cursor(func_cursor)
            
            if func_class_cursor:
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
    
    def _build_class_method_relationships(self):
        """建立类与方法的关联关系 - 修复孤儿函数问题（支持模板特化智能匹配）"""
        self.logger.info("开始建立类与方法的关联关系（后处理阶段）...")
        
        fixed_classes = set()
        total_methods_added = 0
        created_specializations = 0
        
        # 遍历所有函数，找出类方法
        for func_usr, func in self.functions.items():
            # 检查是否为类方法（基于cursor语义信息）
            func_cursor = self.template_resolver.get_cursor_by_usr(func_usr)
            expected_class_cursor = self._extract_class_cursor_from_function_cursor(func_cursor)
            expected_class_usr = expected_class_cursor.get_usr() if expected_class_cursor else None
            
            if expected_class_usr:
                # 首先尝试直接匹配
                if expected_class_usr in self.classes:
                    # 直接匹配成功
                    cls = self.classes[expected_class_usr]
                    if func_usr not in cls.methods:
                        cls.methods.append(func_usr)
                        total_methods_added += 1
                        fixed_classes.add(expected_class_usr)
                else:
                    # 直接匹配失败，尝试基于cursor的模板特化智能匹配
                    matched_class_cursor = self._find_template_base_class_cursor(expected_class_cursor)
                    
                    if matched_class_cursor:
                        # 找到基础模板类，为特化版本创建类
                        specialized_class = self._create_specialized_class_from_cursor(expected_class_cursor, matched_class_cursor)
                        if specialized_class:
                            self.classes[expected_class_usr] = specialized_class
                            specialized_class.methods.append(func_usr)
                            total_methods_added += 1
                            created_specializations += 1
                            fixed_classes.add(expected_class_usr)
                            
                            self.logger.debug(f"为特化类创建了新的类定义: {expected_class_usr}")
                    else:
                        self.logger.debug(f"无法找到匹配的类或基础模板: {expected_class_usr} (函数: {getattr(func, 'qualified_name', 'unknown')})")
        
        self.logger.info(f"类方法关联修复完成: 修复了 {len(fixed_classes)} 个类，添加了 {total_methods_added} 个方法关联，创建了 {created_specializations} 个模板特化类")
    
    def _extract_namespace_from_cursor(self, cursor) -> str:
        """从cursor中提取命名空间信息"""
        if not cursor:
            return ""
        
        try:
            import clang.cindex as clang
            
            # 遍历semantic_parent查找命名空间
            namespace_parts = []
            current = cursor.semantic_parent
            
            while current:
                if current.kind == clang.CursorKind.NAMESPACE:
                    namespace_name = current.spelling or current.displayname
                    if namespace_name:
                        namespace_parts.append(namespace_name)
                current = current.semantic_parent
            
            # 反转得到正确的顺序
            namespace_parts.reverse()
            return "::".join(namespace_parts) if namespace_parts else ""
            
        except Exception as e:
            self.logger.debug(f"从cursor提取命名空间时出错: {e}")
            return ""
    
    def _find_template_base_class_cursor(self, cursor) -> Optional['clang.Cursor']:
        """基于cursor查找模板特化类的基础模板类 - 纯cursor驱动"""
        if not cursor:
            return None
        
        try:
            # 使用template_resolver的纯cursor方法获取基础模板
            base_template_cursor = self.template_resolver._extract_base_template_cursor(cursor)
            return base_template_cursor
            
        except Exception as e:
            self.logger.debug(f"查找模板基础类时出错: {e}")
            return None
    
    def _is_template_specialization_of_cursor(self, specialized_cursor, base_cursor) -> bool:
        """检查specialized_cursor是否是base_cursor的模板特化 - 纯cursor驱动"""
        if not specialized_cursor or not base_cursor:
            return False
        
        try:
            # 使用template resolver的纯cursor方法
            return self.template_resolver._are_template_variants_cursor(specialized_cursor, base_cursor)
            
        except Exception as e:
            self.logger.debug(f"检查模板特化关系时出错: {e}")
            return False
    
    def _create_specialized_class_from_cursor(self, specialized_cursor, base_cursor) -> Optional['Class']:
        """基于cursor创建模板特化类"""
        if not specialized_cursor or not base_cursor:
            return None
        
        try:
            from .data_structures import Class, CppOopExtensions
            
            # 从cursor中提取信息
            class_name = specialized_cursor.spelling or specialized_cursor.displayname
            if not class_name:
                class_name = "UnknownSpecialization"
            
            qualified_name = self._extract_qualified_name_from_cursor(specialized_cursor)
            
            # 获取位置信息
            definition_file = "<generated>"
            line_number = 0
            if hasattr(specialized_cursor, 'location') and specialized_cursor.location.file:
                definition_file = specialized_cursor.location.file.name
                line_number = specialized_cursor.location.line if hasattr(specialized_cursor.location, 'line') else 0
            
            # 创建特化类
            specialized_class = Class(
                name=class_name,
                qualified_name=qualified_name,
                usr_id=specialized_cursor.get_usr(),
                definition_file_id=definition_file,
                declaration_file_id=definition_file,
                line=line_number,
                declaration_locations=[],
                definition_location=None,
                is_declaration=True,
                is_definition=False,
                methods=[],
                is_abstract=self._is_cursor_abstract(specialized_cursor),
                cpp_oop_extensions=CppOopExtensions(qualified_name=qualified_name),
                parent_classes=[]
            )
            
            # 分析继承关系
            inheritance_info = self._analyze_cursor_inheritance_role(specialized_cursor)
            if inheritance_info.get('base_classes_count', 0) > 0:
                # 这里可以进一步处理基类信息
                pass
            
            specialized_class.is_template_specialization = True
            
            return specialized_class
            
        except Exception as e:
            self.logger.error(f"从cursor创建特化类时出错: {e}")
            return None
    
    def _extract_class_cursor_from_function_cursor(self, func_cursor) -> Optional['clang.Cursor']:
        """从函数cursor中提取类cursor"""
        if not func_cursor:
            return None
        
        try:
            import clang.cindex as clang
            
            # 使用semantic_parent获取类cursor
            current = func_cursor.semantic_parent
            while current:
                if current.kind in [clang.CursorKind.CLASS_DECL, 
                                   clang.CursorKind.STRUCT_DECL,
                                   clang.CursorKind.UNION_DECL,
                                   clang.CursorKind.CLASS_TEMPLATE,
                                   clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION]:
                    return current
                current = current.semantic_parent
            
            return None
            
        except Exception as e:
            self.logger.debug(f"从函数cursor提取类cursor时出错: {e}")
            return None
    

    
    def _create_member_variable_from_cursor(self, cursor: clang.Cursor) -> 'MemberVariable':
        """从cursor创建成员变量对象"""
        from .data_structures import MemberVariable, Location
        
        usr = cursor.get_usr()
        file_path = cursor.location.file.name
        file_id = self.file_id_manager.get_file_id(file_path)
        
        if not file_id:
            raise ValueError(f"无法获取文件ID for {file_path}")
        
        location = Location(
            file_id=file_id,
            line=cursor.location.line,
            column=cursor.location.column
        )
        
        # 提取访问说明符（public, private, protected）
        access_specifier = self._get_access_specifier(cursor)
        
        # 检查是否为静态成员
        is_static = cursor.is_static_method()  # 对于字段也适用
        
        # 检查是否为const
        is_const = cursor.type.is_const_qualified()
        
        # 检查是否为mutable（需要从源码中解析）
        is_mutable = self._is_mutable_field(cursor)
        
        # 获取类型信息
        type_name = cursor.type.spelling
        
        return MemberVariable(
            name=cursor.spelling,
            type=type_name,
            usr_id=usr,
            access_specifier=access_specifier,
            is_static=is_static,
            is_const=is_const,
            is_mutable=is_mutable,
            location=location
        )
    
    def _get_access_specifier(self, cursor: clang.Cursor) -> str:
        """获取成员的访问说明符"""
        access = cursor.access_specifier
        if access == clang.AccessSpecifier.PUBLIC:
            return "public"
        elif access == clang.AccessSpecifier.PROTECTED:
            return "protected"
        elif access == clang.AccessSpecifier.PRIVATE:
            return "private"
        else:
            return "private"  # 默认为private
    
    def _is_mutable_field(self, cursor: clang.Cursor) -> bool:
        """检查字段是否为mutable（简化实现）"""
        # 这是一个简化的实现，实际中可能需要更复杂的源码解析
        try:
            # 通过检查cursor的tokens来判断是否有mutable关键字
            tokens = list(cursor.get_tokens())
            for token in tokens:
                if token.spelling == "mutable":
                    return True
        except:
            pass
        return False
    


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
                num_args = type_or_cursor.get_num_template_arguments()
                for i in range(num_args):
                    # Type对象使用get_template_argument_type方法
                    arg_type = type_or_cursor.get_template_argument_type(i)
                    if arg_type and arg_type.spelling:
                        args.append(arg_type.spelling)
        except Exception as e:
            self.logger.debug(f"提取模板实参时出错: {e}")
        return args
    
    def analyze_complete_type_information(self, compile_commands: Optional[Dict[str, Any]] = None) -> int:
        """优化的类型信息分析：按需解析+缓存机制"""
        self.logger.info("开始优化的按需类型信息分析...")
        
        # 设置clang解析器
        if hasattr(self, '_clang_parser') and self._clang_parser:
            self.template_resolver.clang_parser = self._clang_parser
        
        # 初始化或重置类型解析缓存
        self.template_resolver.initialize_type_analysis_cache()
        
        initial_count = len(self.classes)
        
        # 方案1：从现有符号中按需解析泛型类型（优先）
        additional_classes = self._analyze_types_on_demand()
        
        # 方案2：如果需要，基于compile_commands进行补充解析
        if compile_commands and len(additional_classes) == 0:
            self.logger.info("按需解析无新发现，进行compile_commands补充解析...")
            additional_classes = self.template_resolver.extract_complete_type_information(
                compile_commands, self.classes
            )
        
        # 将提取到的类型信息添加到类字典中
        for class_usr, class_obj in additional_classes.items():
            if class_usr not in self.classes:  # 只添加新发现的类型
                self.classes[class_usr] = class_obj
                self.logger.debug(f"按需解析到新类型: {class_usr}")
        
        # 输出分析摘要
        summary = self.template_resolver.get_template_analysis_summary()
        self.logger.info(f"类型信息分析摘要: {summary}")
        
        additional_count = len(self.classes) - initial_count
        self.logger.info(f"优化类型分析完成: 新发现 {additional_count} 个类型")
        
        return additional_count
    
    def _analyze_types_on_demand(self) -> Dict[str, Any]:
        """按需分析类型信息：遇到泛型类型时立即深入解析"""
        additional_classes = {}
        
        try:
            # 遍历已发现的所有符号，寻找未完全解析的泛型类型
            symbols_to_analyze = []
            
            # 1. 检查函数中的泛型类型使用
            for func_usr, func in self.functions.items():
                template_usages = self._extract_template_usages_from_function(func)
                symbols_to_analyze.extend(template_usages)
            
            # 2. 检查类中的泛型基类和成员类型
            for class_usr, cls in self.classes.items():
                template_usages = self._extract_template_usages_from_class(cls)
                symbols_to_analyze.extend(template_usages)
            
            # 3. 按需解析每个发现的泛型类型
            for template_usage in symbols_to_analyze:
                new_classes = self._resolve_template_type_on_demand(template_usage)
                additional_classes.update(new_classes)
                
        except Exception as e:
            self.logger.debug(f"按需类型分析时出错: {e}")
        
        return additional_classes
    
    def _extract_template_usages_from_function(self, func) -> List[Dict[str, Any]]:
        """从函数中提取泛型类型使用"""
        template_usages = []
        
        try:
            # 检查函数参数中的模板类型
            if hasattr(func, 'parameters'):
                for param in func.parameters:
                    if hasattr(param, 'type_name') and self._is_template_type(param.type_name):
                        template_usages.append({
                            'type_name': param.type_name,
                            'context': 'parameter',
                            'source_function': func.usr if hasattr(func, 'usr') else None,
                            'cursor': getattr(param, '_cursor', None)
                        })
            
            # 检查返回类型中的模板类型
            if hasattr(func, 'return_type') and self._is_template_type(func.return_type):
                template_usages.append({
                    'type_name': func.return_type,
                    'context': 'return_type',
                    'source_function': func.usr if hasattr(func, 'usr') else None,
                    'cursor': getattr(func, '_cursor', None)
                })
                
        except Exception as e:
            self.logger.debug(f"从函数提取模板使用时出错: {e}")
        
        return template_usages
    
    def _extract_template_usages_from_class(self, cls) -> List[Dict[str, Any]]:
        """从类中提取泛型类型使用"""
        template_usages = []
        
        try:
            # 检查基类中的模板类型
            if hasattr(cls, 'base_classes'):
                for base_class in cls.base_classes:
                    if hasattr(base_class, 'name') and self._is_template_type(base_class.name):
                        template_usages.append({
                            'type_name': base_class.name,
                            'context': 'base_class',
                            'source_class': cls.usr if hasattr(cls, 'usr') else None,
                            'cursor': getattr(base_class, '_cursor', None)
                        })
            
            # 检查成员变量中的模板类型
            if hasattr(cls, 'member_variables'):
                for member in cls.member_variables:
                    if hasattr(member, 'type_name') and self._is_template_type(member.type_name):
                        template_usages.append({
                            'type_name': member.type_name,
                            'context': 'member_variable',
                            'source_class': cls.usr if hasattr(cls, 'usr') else None,
                            'cursor': getattr(member, '_cursor', None)
                        })
                        
        except Exception as e:
            self.logger.debug(f"从类提取模板使用时出错: {e}")
        
        return template_usages
    
    def _is_template_type(self, type_name: str) -> bool:
        """判断类型名是否为模板类型"""
        if not type_name:
            return False
        return '<' in type_name and '>' in type_name
    
    def _resolve_template_type_on_demand(self, template_usage: Dict[str, Any]) -> Dict[str, Any]:
        """按需解析特定的模板类型"""
        additional_classes = {}
        
        try:
            type_name = template_usage.get('type_name')
            cursor = template_usage.get('cursor')
            
            if not type_name:
                return additional_classes
            
            # 首先检查缓存
            if self.template_resolver.is_type_fully_resolved(type_name):
                self.logger.debug(f"类型 {type_name} 已在缓存中，跳过解析")
                return additional_classes
            
            # 尝试从cursor解析类型
            resolved_classes = self.template_resolver.resolve_template_from_cursor(cursor, self.classes)
            additional_classes.update(resolved_classes)
            
            # 标记类型为已解析，传递完整信息
            self.template_resolver.mark_type_as_resolved(
                type_name=type_name,
                resolved_classes=additional_classes
            )
            
        except Exception as e:
            self.logger.debug(f"按需解析模板类型 {template_usage.get('type_name')} 时出错: {e}")
        
        return additional_classes
    
    def get_type_analysis_statistics(self) -> Dict[str, Any]:
        """获取类型分析统计信息"""
        try:
            stats = {
                'total_resolved_types': len(self.template_resolver.type_resolution_cache),
                'cached_definitions': len(self.template_resolver.type_definition_cache),
                'dependency_relationships': len(self.template_resolver.type_dependency_graph),
                'template_instantiations': len(self.template_resolver.template_instantiations),
                'cache_hit_rate': self._calculate_cache_hit_rate(),
            }
            
            # 统计按上下文分类的类型解析
            context_stats = {}
            for type_name, resolved in self.template_resolver.type_resolution_cache.items():
                if resolved:
                    context_stats[type_name] = 'resolved'
                else:
                    context_stats[type_name] = 'pending'
            
            stats['type_resolution_details'] = context_stats
            
            return stats
            
        except Exception as e:
            self.logger.debug(f"获取类型分析统计时出错: {e}")
            return {}
    
    def _calculate_cache_hit_rate(self) -> float:
        """计算缓存命中率"""
        try:
            if not hasattr(self.template_resolver, '_cache_requests'):
                return 0.0
            
            total_requests = getattr(self.template_resolver, '_cache_requests', 0)
            cache_hits = getattr(self.template_resolver, '_cache_hits', 0)
            
            if total_requests == 0:
                return 0.0
            
            return (cache_hits / total_requests) * 100.0
            
        except Exception as e:
            self.logger.debug(f"计算缓存命中率时出错: {e}")
            return 0.0
    
    def clear_type_analysis_cache(self):
        """清理类型分析缓存（用于内存管理）"""
        try:
            self.template_resolver.initialize_type_analysis_cache()
            self.logger.info("类型分析缓存已清理")
        except Exception as e:
            self.logger.debug(f"清理类型分析缓存时出错: {e}")
    
    def _extract_additional_types_from_cursors(self) -> Dict[str, Any]:
        """从现有cursor映射中提取额外的类型信息"""
        self.logger.info("从cursor映射中提取额外类型信息...")
        
        additional_classes = {}
        
        # 遍历所有已映射的cursor，提取完整的类型信息
        for usr, cursor in self.template_resolver.usr_to_cursor_map.items():
            if usr not in self.classes:
                # 这是一个新发现的类型，尝试从cursor创建完整的类对象
                class_obj = self._create_complete_class_from_cursor(cursor)
                if class_obj:
                    additional_classes[usr] = class_obj
        
        return additional_classes
    
    def _create_complete_class_from_cursor(self, cursor) -> Optional['Class']:
        """从cursor创建完整的类对象（多进程安全版本）"""
        if not cursor:
            return None
        
        try:
            import clang.cindex as clang
            
            # 只处理类相关的cursor
            if cursor.kind not in [clang.CursorKind.CLASS_DECL, 
                                  clang.CursorKind.STRUCT_DECL,
                                  clang.CursorKind.UNION_DECL,
                                  clang.CursorKind.CLASS_TEMPLATE,
                                  clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION]:
                return None
            
            from .data_structures import Class, CppOopExtensions
            
            # 提取基本信息
            class_name = cursor.spelling or cursor.displayname
            if not class_name:
                return None
            
            qualified_name = self._extract_qualified_name_from_cursor(cursor)
            usr = cursor.get_usr()
            
            # 跳过复杂模板类型
            if self._is_complex_template_type(qualified_name):
                self.logger.error(f"跳过复杂模板类型: {qualified_name}")
                return None
            
            # 检查共享缓存是否已解析
            if self.shared_class_cache and self.shared_class_cache.is_class_resolved(usr, qualified_name):
                cached_class = self.shared_class_cache.get_resolved_class(usr, qualified_name)
                if cached_class:
                    self.logger.debug(f"类缓存命中: {qualified_name}")
                    # 从缓存的数据重构Class对象
                    return self._reconstruct_class_from_cache(cached_class)
            
            # 检查是否正在被其他进程解析，但不等待避免死循环
            if self.shared_class_cache and self.shared_class_cache.is_class_being_resolved(usr, qualified_name):
                self.logger.error(f"类 {qualified_name} 正在被其他进程解析，跳过避免循环")
                return None  # 直接跳过，避免无限等待
            
            # 尝试获取解析锁，增加重试机制
            lock_acquired = False
            if self.shared_class_cache:
                max_lock_attempts = 3
                lock_wait_time = 0.1
                
                for attempt in range(max_lock_attempts):
                    if self.shared_class_cache.try_acquire_class_resolution_lock(usr, qualified_name):
                        lock_acquired = True
                        break
                    
                    if attempt < max_lock_attempts - 1:  # 不是最后一次尝试
                        self.logger.error(f"获取类解析锁失败，第 {attempt + 1}/{max_lock_attempts} 次尝试: {qualified_name}")
                        import time
                        time.sleep(lock_wait_time)
                    else:
                        self.logger.error(f"多次尝试后仍无法获取类 {qualified_name} 的解析锁，跳过解析")
                        return None
            
            try:
                # 提取位置信息
                definition_file = "<unknown>"
                line_number = 0
                if hasattr(cursor, 'location') and cursor.location.file:
                    definition_file = cursor.location.file.name
                    line_number = cursor.location.line if hasattr(cursor.location, 'line') else 0
                
                # 分析继承关系
                parent_classes = []
                for child in cursor.get_children():
                    if child.kind == clang.CursorKind.CXX_BASE_SPECIFIER:
                        base_type = child.type
                        if base_type and base_type.get_declaration():
                            base_decl = base_type.get_declaration()
                            if base_decl.usr:
                                parent_classes.append(base_decl.usr)
                                # 递归处理父类（如果需要）
                                self._ensure_parent_class_resolved(base_decl)
                
                # 判断是否为模板类型
                is_template = cursor.kind in [clang.CursorKind.CLASS_TEMPLATE,
                                            clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION]
                
                # 创建类对象
                class_obj = Class(
                    name=class_name,
                    qualified_name=qualified_name,
                    usr_id=usr,
                    definition_file_id=definition_file,
                    declaration_file_id=definition_file,
                    line=line_number,
                    declaration_locations=[],
                    definition_location=None,
                    is_declaration=True,
                    is_definition=cursor.kind not in [clang.CursorKind.CLASS_TEMPLATE],
                    methods=[],
                    is_abstract=self._is_cursor_abstract(cursor),
                    cpp_oop_extensions=CppOopExtensions(qualified_name=qualified_name),
                    parent_classes=parent_classes
                )
                
                # 标记从AST提取的类型
                class_obj.is_ast_extracted = True
                
                # 将解析结果存入共享缓存
                if self.shared_class_cache:
                    class_data = self._serialize_class_for_cache(class_obj)
                    self.shared_class_cache.mark_class_resolved(
                        usr=usr,
                        qualified_name=qualified_name,
                        class_data=class_data,
                        parent_classes=set(parent_classes),
                        is_template=is_template
                    )
                    lock_acquired = False  # 锁已通过mark_class_resolved释放
                
                return class_obj
                
            except Exception as e:
                # 标记解析失败并释放锁
                if self.shared_class_cache and lock_acquired:
                    self.shared_class_cache.mark_class_failed(usr, qualified_name, str(e))
                    lock_acquired = False  # 锁已通过mark_class_failed释放
                self.logger.debug(f"类解析失败: {qualified_name} - {e}")
                return None
                
            finally:
                # 确保锁被释放（防止意外情况）
                if lock_acquired and self.shared_class_cache:
                    self.logger.warning(f"强制释放未处理的解析锁: {qualified_name}")
                    self.shared_class_cache.mark_class_failed(usr, qualified_name, "Unexpected lock state")
            
        except Exception as e:
            self.logger.debug(f"从cursor创建类对象时出错: {e}")
            return None
    
    def _is_complex_template_type(self, type_name: str) -> bool:
        """检查是否为过于复杂的模板类型，应该跳过处理"""
        if not type_name:
            return False
        
        # 检查是否包含过多的模板参数
        if type_name.count('<') > 3:  # 嵌套层数超过3层
            return True
        
        # 检查是否包含大量的type-parameter
        if type_name.count('type-parameter') > 5:
            return True
        
        # 检查长度是否过长
        if len(type_name) > 200:
            return True
        
        # 检查是否包含已知的问题模板类型
        problematic_patterns = [
            'tuple<type-parameter-0-0, type-parameter-0-1',
            'enable_if_t<',
            'conjunction_v<',
            '_Tuple_assignable_v<',
            '_Tuple_constructible_v<'
        ]
        
        for pattern in problematic_patterns:
            if pattern in type_name:
                return True
        
        return False

    def _ensure_parent_class_resolved(self, parent_cursor):
        """确保父类已被解析（递归处理继承链，带循环检测）"""
        try:
            if not parent_cursor:
                return
            
            parent_usr = parent_cursor.get_usr()
            if not parent_usr:
                return
            parent_qualified_name = self._extract_qualified_name_from_cursor(parent_cursor)
            
            # 跳过复杂模板类型
            if self._is_complex_template_type(parent_qualified_name):
                self.logger.error(f"跳过复杂模板父类: {parent_qualified_name}")
                return
            
            # 添加循环依赖检测
            if not hasattr(self, '_resolving_classes'):
                self._resolving_classes = set()
            
            if parent_usr in self._resolving_classes:
                self.logger.error(f"检测到循环依赖，跳过递归解析父类: {parent_qualified_name}")
                return
            
            # 如果父类未解析，触发解析
            if self.shared_class_cache and not self.shared_class_cache.is_class_resolved(parent_usr, parent_qualified_name):
                self.logger.debug(f"递归解析父类: {parent_qualified_name}")
                self._resolving_classes.add(parent_usr)
                try:
                    parent_class = self._create_complete_class_from_cursor(parent_cursor)
                    if parent_class and parent_usr not in self.classes:
                        self.classes[parent_usr] = parent_class
                finally:
                    self._resolving_classes.discard(parent_usr)
                    
        except Exception as e:
            self.logger.error(f"确保父类解析时出错: {e}")
            # 确保在异常情况下也清理循环检测集合
            if hasattr(self, '_resolving_classes') and parent_cursor:
                parent_usr = parent_cursor.get_usr()
                if parent_usr:
                    self._resolving_classes.discard(parent_usr)
    
    def _serialize_class_for_cache(self, class_obj: 'Class') -> Dict[str, Any]:
        """将类对象序列化为可缓存的数据"""
        return {
            'name': class_obj.name,
            'qualified_name': class_obj.qualified_name,
            'usr_id': class_obj.usr_id,
            'definition_file_id': class_obj.definition_file_id,
            'declaration_file_id': class_obj.declaration_file_id,
            'line': class_obj.line,
            'is_declaration': class_obj.is_declaration,
            'is_definition': class_obj.is_definition,
            'parent_classes': class_obj.parent_classes,
            'is_abstract': class_obj.is_abstract,
            'methods': class_obj.methods,
            'is_ast_extracted': getattr(class_obj, 'is_ast_extracted', False)
        }
    
    def _reconstruct_class_from_cache(self, cached_data: Dict[str, Any]) -> Optional['Class']:
        """从缓存数据重构类对象"""
        try:
            from .data_structures import Class, CppOopExtensions
            
            class_obj = Class(
                name=cached_data['name'],
                qualified_name=cached_data['qualified_name'],
                usr_id=cached_data['usr_id'],
                definition_file_id=cached_data.get('definition_file_id', '<cached>'),
                declaration_file_id=cached_data.get('declaration_file_id', '<cached>'),
                line=cached_data.get('line', 0),
                declaration_locations=[],
                definition_location=None,
                is_declaration=cached_data.get('is_declaration', True),
                is_definition=cached_data.get('is_definition', False),
                methods=cached_data.get('methods', []),
                is_abstract=cached_data.get('is_abstract', False),
                cpp_oop_extensions=CppOopExtensions(qualified_name=cached_data['qualified_name']),
                parent_classes=cached_data.get('parent_classes', [])
            )
            
            # 标记为从缓存重构
            class_obj.is_ast_extracted = cached_data.get('is_ast_extracted', False)
            class_obj.is_cache_reconstructed = True
            
            return class_obj
            
        except Exception as e:
            self.logger.debug(f"从缓存重构类对象时出错: {e}")
            return None
    
    def set_clang_parser(self, clang_parser):
        """设置clang解析器用于动态模板分析"""
        self._clang_parser = clang_parser
        if self.template_resolver:
            self.template_resolver.clang_parser = clang_parser
    

    
    def _create_simple_base_class_from_cursor(self, cursor) -> Optional['Class']:
        """基于cursor语义信息创建简单的基类"""
        if not cursor:
            return None
        
        try:
            from .data_structures import Class, CppOopExtensions
            
            # 从cursor中获取类名和相关信息
            class_name = cursor.spelling or cursor.displayname
            if not class_name:
                usr = cursor.get_usr()
            class_name = usr.split('@')[-1] if usr else "UnknownClass"
            
            # 获取定义位置信息
            definition_file = "<generated>"
            line_number = 0
            if hasattr(cursor, 'location') and cursor.location.file:
                definition_file = cursor.location.file.name
                line_number = cursor.location.line if hasattr(cursor.location, 'line') else 0
            
            # 分析cursor获取更准确的qualified_name
            qualified_name = self._extract_qualified_name_from_cursor(cursor)
            
            simple_class = Class(
                name=class_name,
                qualified_name=qualified_name,
                usr_id=cursor.get_usr(),
                definition_file_id=definition_file,
                declaration_file_id=definition_file,
                line=line_number,
                declaration_locations=[],
                definition_location=None,
                is_declaration=True,
                is_definition=False,
                methods=[],
                is_abstract=self._is_cursor_abstract(cursor),
                cpp_oop_extensions=CppOopExtensions(qualified_name=qualified_name),
                parent_classes=[]
            )
            
            simple_class.is_generated_base = True
            
            return simple_class
            
        except Exception as e:
            self.logger.debug(f"从cursor创建简单基类时出错: {e}")
            return None
    
    def _extract_qualified_name_from_cursor(self, cursor) -> str:
        """从cursor中提取完整的限定名"""
        if not cursor:
            return "UnknownClass"
        
        try:
            # 构建命名空间链
            name_parts = []
            current = cursor
            
            # 添加类名
            if current.spelling:
                name_parts.append(current.spelling)
            
            # 向上遍历命名空间
            current = current.semantic_parent
            while current:
                if hasattr(current, 'kind'):
                    import clang.cindex as clang
                    if current.kind == clang.CursorKind.NAMESPACE:
                        namespace_name = current.spelling or current.displayname
                        if namespace_name:
                            name_parts.append(namespace_name)
                current = current.semantic_parent
            
            # 反转得到正确的顺序
            name_parts.reverse()
            
            usr = cursor.get_usr()
            return "::".join(name_parts) if name_parts else usr.split('@')[-1] if usr else "UnknownClass"
            
        except Exception as e:
            self.logger.debug(f"提取cursor限定名时出错: {e}")
            return cursor.spelling or "UnknownClass"
    
    def _is_cursor_abstract(self, cursor) -> bool:
        """检查cursor是否表示抽象类"""
        if not cursor:
            return False
        
        try:
            # 检查是否有is_abstract方法
            if hasattr(cursor, 'is_abstract'):
                return cursor.is_abstract()
            
            # 检查是否有纯虚函数
            import clang.cindex as clang
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.CXX_METHOD:
                    # 检查是否为纯虚函数（简化检查）
                    if hasattr(child, 'is_pure_virtual') and child.is_pure_virtual():
                        return True
            
            return False
            
        except Exception as e:
            self.logger.debug(f"检查cursor是否抽象时出错: {e}")
            return False
    

    
    def _analyze_cursor_structure_main(self, cursor) -> Dict[str, Any]:
        """基于cursor语义信息分析类结构（纯AST方法，无回退）"""
        if not cursor:
            return {
                'is_likely_base_class': False,
                'is_external_but_referenced': False,
                'is_utility_or_policy_class': False,
                'confidence_score': 0.0
            }
        
        # 直接调用已有的_analyze_cursor_structure方法
        return self._analyze_cursor_structure(cursor)
    
    def _analyze_class_naming_patterns(self, cursor) -> Dict[str, Any]:
        """基于cursor AST语义信息分析类的特征（替代命名模式）"""
        if not cursor:
            return {
                'is_likely_base_class': False,
                'is_utility_or_policy_class': False
            }
        
        # 直接使用cursor语义信息分析
        return self._analyze_cursor_semantics(cursor)
    
    def _analyze_inheritance_relationships(self, cursor) -> Dict[str, Any]:
        """基于cursor AST语义信息分析继承关系特征"""
        if not cursor:
            return {
                'is_likely_base_class': False,
                'is_utility_or_policy_class': False
            }
        
        # 直接使用cursor的继承角色分析
        return self._analyze_cursor_inheritance_role(cursor)
    
    def _analyze_cursor_semantics_main(self, cursor) -> Dict[str, Any]:
        """基于cursor语义信息的结构分析（纯AST方法，无回退）"""
        if not cursor:
            return {
                'is_likely_base_class': False,
                'is_utility_or_policy_class': False
            }
        
        # 直接调用已有的_analyze_cursor_semantics方法
        return self._analyze_cursor_semantics(cursor)
    
    def _analyze_cursor_structure(self, cursor) -> Dict[str, Any]:
        """基于cursor语义信息分析类结构"""
        analysis = {
            'is_likely_base_class': False,
            'is_external_but_referenced': False,
            'is_utility_or_policy_class': False,
            'confidence_score': 0.0
        }
        
        try:
            import clang.cindex as clang
            
            # 1. 基于cursor类型和属性的直接分析
            if cursor.kind == clang.CursorKind.CLASS_TEMPLATE:
                analysis['is_utility_or_policy_class'] = True
            
            # 2. 分析是否为抽象类
            if hasattr(cursor, 'is_abstract') and cursor.is_abstract():
                analysis['is_likely_base_class'] = True
            
            # 3. 分析继承关系
            inheritance_analysis = self._analyze_cursor_inheritance_role(cursor)
            analysis.update(inheritance_analysis)
            
            # 4. 分析命名空间上下文
            namespace_analysis = self.template_resolver._analyze_namespace_hierarchy_cursor(cursor)
            if namespace_analysis.get('is_std_namespace', False):
                analysis['is_utility_or_policy_class'] = True
                analysis['is_external_but_referenced'] = True
            elif namespace_analysis.get('is_internal_namespace', False):
                analysis['is_utility_or_policy_class'] = True
            
            # 5. 分析模板特征
            template_analysis = self._analyze_cursor_template_characteristics(cursor)
            analysis.update(template_analysis)
            
            # 6. 分析定义位置
            location_analysis = self._analyze_cursor_location(cursor)
            analysis.update(location_analysis)
            
            # 7. 计算置信度
            analysis['confidence_score'] = self._calculate_cursor_analysis_confidence(analysis)
            
        except Exception as e:
            self.logger.debug(f"分析cursor结构时出错: {e}")
        
        return analysis
    
    def _analyze_cursor_semantics(self, cursor) -> Dict[str, Any]:
        """基于cursor语义信息的语义分析"""
        result = {
            'is_likely_base_class': False,
            'is_utility_or_policy_class': False
        }
        
        try:
            import clang.cindex as clang
            
            # 1. 分析cursor的语义角色
            if cursor.kind in [clang.CursorKind.CLASS_TEMPLATE, clang.CursorKind.FUNCTION_TEMPLATE]:
                result['is_utility_or_policy_class'] = True
            
            # 2. 检查是否为模板特化
            if self.template_resolver._is_template_specialization_cursor(cursor):
                result['is_utility_or_policy_class'] = True
            
            # 3. 分析继承关系中的角色
            inheritance_role = self._analyze_cursor_inheritance_role(cursor)
            result.update(inheritance_role)
            
            # 4. 分析命名空间语义
            namespace_context = self.template_resolver._analyze_namespace_hierarchy_cursor(cursor)
            if namespace_context.get('is_std_namespace', False):
                result['is_utility_or_policy_class'] = True
            
            # 5. 分析类型标记
            type_markers = self.template_resolver._extract_type_markers_cursor(cursor)
            if 'template' in type_markers:
                result['is_utility_or_policy_class'] = True
            
        except Exception as e:
            self.logger.debug(f"分析cursor语义时出错: {e}")
        
        return result
    
    def _analyze_cursor_inheritance_role(self, cursor) -> Dict[str, Any]:
        """分析cursor在继承关系中的角色"""
        result = {
            'is_likely_base_class': False,
            'derived_classes_count': 0,
            'base_classes_count': 0
        }
        
        try:
            import clang.cindex as clang
            
            # 统计派生类数量
            derived_classes = []
            base_classes = []
            
            # 遍历cursor的子节点，查找继承关系
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.CXX_BASE_SPECIFIER:
                    base_classes.append(child)
                elif child.kind == clang.CursorKind.CLASS_DECL:
                    # 检查是否继承自当前cursor
                    for grandchild in child.get_children():
                        if (grandchild.kind == clang.CursorKind.CXX_BASE_SPECIFIER and
                            grandchild.type and grandchild.type.get_declaration() == cursor):
                            derived_classes.append(child)
                            break
            
            result['derived_classes_count'] = len(derived_classes)
            result['base_classes_count'] = len(base_classes)
            
            # 如果有多个派生类，可能是基类
            if len(derived_classes) >= 2:
                result['is_likely_base_class'] = True
            
            # 如果有基类但没有派生类，可能是叶子类
            if len(base_classes) > 0 and len(derived_classes) == 0:
                result['is_leaf_class'] = True
            
        except Exception as e:
            self.logger.debug(f"分析cursor继承角色时出错: {e}")
        
        return result
    
    def _analyze_cursor_template_characteristics(self, cursor) -> Dict[str, Any]:
        """分析cursor的模板特征"""
        result = {}
        
        try:
            import clang.cindex as clang
            
            # 检查是否为模板
            if cursor.kind == clang.CursorKind.CLASS_TEMPLATE:
                result['is_template_definition'] = True
                result['is_utility_or_policy_class'] = True
            
            # 检查是否为模板特化
            if self.template_resolver._is_template_specialization_cursor(cursor):
                result['is_template_specialization'] = True
                result['is_utility_or_policy_class'] = True
                
                # 获取基础模板
                base_template = self.template_resolver._extract_base_template_cursor(cursor)
                if base_template:
                    result['has_base_template'] = True
                    
                    # 分析特化程度
                    if hasattr(cursor, 'get_num_template_arguments'):
                        try:
                            num_args = cursor.get_num_template_arguments()
                            result['template_args_count'] = num_args
                            if num_args > 2:
                                result['is_complex_template'] = True
                        except:
                            pass
            
            # 检查模板参数
            template_params = []
            for child in cursor.get_children():
                if child.kind in [clang.CursorKind.TEMPLATE_TYPE_PARAMETER,
                                 clang.CursorKind.TEMPLATE_NON_TYPE_PARAMETER,
                                 clang.CursorKind.TEMPLATE_TEMPLATE_PARAMETER]:
                    template_params.append(child)
            
            if template_params:
                result['template_params_count'] = len(template_params)
                if len(template_params) > 2:
                    result['is_complex_template'] = True
            
        except Exception as e:
            self.logger.debug(f"分析cursor模板特征时出错: {e}")
        
        return result
    
    def _analyze_cursor_location(self, cursor) -> Dict[str, Any]:
        """分析cursor的定义位置"""
        result = {}
        
        try:
            if hasattr(cursor, 'location') and cursor.location.file:
                file_path = cursor.location.file.name
                
                # 使用已有的文件上下文分析
                file_context = self._classify_file_context(file_path)
                result['file_context'] = file_context
                
                if file_context.get('is_external', False):
                    result['is_external_but_referenced'] = True
                
                # 分析文件名语义
                import os
                filename = os.path.basename(file_path).lower()
                if any(keyword in filename for keyword in ['base', 'abstract', 'interface']):
                    result['is_likely_base_class'] = True
                elif any(keyword in filename for keyword in ['util', 'helper', 'policy', 'trait']):
                    result['is_utility_or_policy_class'] = True
        
        except Exception as e:
            self.logger.debug(f"分析cursor位置时出错: {e}")
        
        return result
    
    def _calculate_cursor_analysis_confidence(self, analysis: Dict[str, Any]) -> float:
        """计算基于cursor分析的置信度"""
        confidence = 0.0
        
        try:
            # 基于不同信息源的可靠性计算置信度
            if analysis.get('is_template_definition', False):
                confidence += 0.3
            
            if analysis.get('is_template_specialization', False):
                confidence += 0.25
            
            if analysis.get('derived_classes_count', 0) > 0:
                confidence += 0.2
            
            if analysis.get('file_context', {}).get('is_project', False):
                confidence += 0.15
            
            if analysis.get('has_base_template', False):
                confidence += 0.1
            
            return min(confidence, 1.0)
        except Exception:
            return 0.0
    

    
    def _analyze_from_recorded_inheritance(self, usr: str) -> Optional[Dict[str, Any]]:
        """从已记录的继承关系中分析类特征"""
        if not hasattr(self.template_resolver, 'inheritance_relationships'):
            return None
        
        result = {
            'is_likely_base_class': False,
            'is_utility_or_policy_class': False
        }
        
        try:
            inheritance_relationships = self.template_resolver.inheritance_relationships
            
            # 统计继承该类的子类数量
            derived_count = sum(1 for derived_usr, base_list in inheritance_relationships.items() 
                              if usr in base_list)
            
            if derived_count >= 2:
                result['is_likely_base_class'] = True
                self.logger.debug(f"从记录的继承关系确认基类: {usr} (被 {derived_count} 个类继承)")
            
            # 分析该类的基类
            base_classes = inheritance_relationships.get(usr, [])
            if base_classes:
                # 如果基类中有已知的utility类或policy类，则该类也可能是utility类
                for base_usr in base_classes:
                    if self._is_known_utility_pattern_by_usr(base_usr):
                        result['is_utility_or_policy_class'] = True
                        break
            
            return result
        
        except Exception as e:
            self.logger.debug(f"从记录的继承关系分析时出错: {e}")
            return None
    
    def _find_derived_classes(self, base_usr: str) -> List[str]:
        """查找继承了指定基类的所有派生类"""
        derived_classes = []
        
        # 在现有类中查找
        for class_usr, class_obj in self.classes.items():
            if hasattr(class_obj, 'parent_classes') and base_usr in class_obj.parent_classes:
                derived_classes.append(class_usr)
        
        # 在模板解析器的继承关系中查找
        if hasattr(self.template_resolver, 'inheritance_relationships'):
            for derived_usr, base_list in self.template_resolver.inheritance_relationships.items():
                if base_usr in base_list and derived_usr not in derived_classes:
                    derived_classes.append(derived_usr)
        
        return derived_classes
    
    def _find_base_classes(self, derived_usr: str) -> List[str]:
        """查找指定类的所有基类"""
        base_classes = []
        
        # 从现有类中获取
        if derived_usr in self.classes:
            class_obj = self.classes[derived_usr]
            if hasattr(class_obj, 'parent_classes'):
                base_classes.extend(class_obj.parent_classes)
        
        # 从模板解析器的继承关系中获取
        if hasattr(self.template_resolver, 'inheritance_relationships'):
            recorded_bases = self.template_resolver.inheritance_relationships.get(derived_usr, [])
            base_classes.extend(recorded_bases)
        
        return list(set(base_classes))  # 去重
    
    def _analyze_base_classes_characteristics(self, base_classes: List[str]) -> Dict[str, Any]:
        """分析基类的特征"""
        result = {
            'has_utility_base': False,
            'has_abstract_base': False
        }
        
        for base_usr in base_classes:
            if self._is_known_utility_pattern_by_usr(base_usr):
                result['has_utility_base'] = True
            if self._is_known_abstract_pattern_by_usr(base_usr):
                result['has_abstract_base'] = True
        
        return result
    
    def _analyze_template_base_characteristics(self, template_usr: str) -> Dict[str, Any]:
        """分析模板基类的特征"""
        result = {
            'is_likely_base_class': False,
            'is_utility_or_policy_class': False
        }
        
        try:
            # 基于cursor提取基础模板
            template_cursor = self.template_resolver.get_cursor_by_usr(template_usr)
            if template_cursor:
                base_template_cursor = self.template_resolver._extract_base_template_cursor(template_cursor)
                
                # 分析基础模板的用途
                if base_template_cursor:
                    base_template_usr = base_template_cursor.get_usr()
                    # 检查基础模板是否被多次特化
                    if hasattr(self.template_resolver, 'template_hierarchy'):
                        specializations = self.template_resolver.template_hierarchy.get(base_template_usr, set())
                        if len(specializations) >= 2:
                            result['is_utility_or_policy_class'] = True
                    
                    # 检查是否有模板基类实例化信息
                    if hasattr(self.template_resolver, 'template_base_instantiations'):
                        for derived_usr, inst_info in self.template_resolver.template_base_instantiations.items():
                            if inst_info.get('base_usr') == template_usr:
                                result['is_likely_base_class'] = True
                                break
        
        except Exception as e:
            self.logger.debug(f"分析模板基类特征时出错: {e}")
        
        return result
    
    def _analyze_namespace_structure(self, cursor) -> Dict[str, Any]:
        """分析cursor的命名空间结构（纯cursor语义信息）"""
        if not cursor:
            return {
                'namespace_chain': [],
                'is_std_namespace': False,
                'is_internal_namespace': False,
                'namespace_depth': 0
            }
        
        return self.template_resolver._analyze_namespace_hierarchy_cursor(cursor)
    
    def _extract_type_markers(self, cursor) -> Set[str]:
        """从cursor中提取类型标记（纯cursor语义信息）"""
        if not cursor:
            return set()
        
        return self.template_resolver._extract_type_markers_cursor(cursor)
    
    def _infer_design_pattern_from_cursor(self, cursor) -> str:
        """从cursor推断设计模式 - 基于语义结构而非字符串"""
        if not cursor:
            return 'unknown'
        
        try:
            import clang.cindex as clang
            
            # 检查是否为模板特化且被多次使用（可能是policy或traits）
            if self.template_resolver._is_template_specialization_cursor(cursor):
                base_template_cursor = self.template_resolver._extract_base_template_cursor(cursor)
                if base_template_cursor:
                    # 基于template_resolver的统计信息判断是否为policy模式
                    return 'policy'
            
            # 检查是否为抽象基类（有纯虚函数）
            if self._is_known_abstract_pattern_cursor(cursor):
                return 'abstract'
            
            # 检查是否为模板类（可能是utility或traits）
            if cursor.kind == clang.CursorKind.CLASS_TEMPLATE:
                return 'template'
            
            # 检查是否有多个虚方法（可能是基类）
            virtual_method_count = 0
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.CXX_METHOD:
                    if hasattr(child, 'is_virtual_method') and child.is_virtual_method():
                        virtual_method_count += 1
            
            if virtual_method_count >= 3:  # 有多个虚方法，可能是基类
                return 'base'
            
            return 'unknown'
        except Exception as e:
            self.logger.debug(f"推断设计模式时出错: {e}")
            return 'unknown'
    
    def _is_known_utility_pattern_cursor(self, cursor) -> bool:
        """通过cursor语义信息判断是否为已知的utility模式 - 纯cursor驱动"""
        if not cursor:
            return False
        
        try:
            import clang.cindex as clang
            
            # 检查是否为标准库类型
            namespace_parts = self._extract_namespace_hierarchy_cursor(cursor)
            if 'std' in namespace_parts:
                return True
            
            # 检查是否为模板且被多次特化
            if cursor.kind == clang.CursorKind.CLASS_TEMPLATE:
                return True
            
            # 检查是否为模板特化
            if self.template_resolver._is_template_specialization_cursor(cursor):
                base_template_cursor = self.template_resolver._extract_base_template_cursor(cursor)
                if base_template_cursor:
                    # 检查基础模板是否被多次使用（通过template_resolver的统计）
                    return True
            
            # 检查类名模式（基于语义而非字符串匹配）
            class_name = cursor.spelling or cursor.displayname
            if class_name:
                utility_indicators = ['trait', 'policy', 'allocator', 'iterator', 'adapter']
                if any(indicator in class_name.lower() for indicator in utility_indicators):
                    return True
            
            return False
        except Exception as e:
            self.logger.debug(f"检查utility模式时出错: {e}")
            return False
    
    def _is_known_abstract_pattern_cursor(self, cursor) -> bool:
        """通过cursor语义信息判断是否为已知的抽象模式 - 纯cursor驱动"""
        if not cursor:
            return False
        
        try:
            import clang.cindex as clang
            
            # 检查是否为抽象类（有纯虚函数）
            has_pure_virtual = False
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.CXX_METHOD:
                    if hasattr(child, 'is_pure_virtual_method') and child.is_pure_virtual_method():
                        has_pure_virtual = True
                        break
            
            if has_pure_virtual:
                return True
            
            # 检查类名是否包含抽象模式指示器
            class_name = cursor.spelling or cursor.displayname
            if class_name:
                abstract_indicators = ['abstract', 'base', 'interface', 'virtual']
                if any(indicator in class_name.lower() for indicator in abstract_indicators):
                    return True
            
            # 检查是否有虚方法（可能是基类）
            virtual_method_count = 0
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.CXX_METHOD:
                    if hasattr(child, 'is_virtual_method') and child.is_virtual_method():
                        virtual_method_count += 1
            
            # 如果有多个虚方法，可能是抽象基类
            return virtual_method_count >= 2
            
        except Exception as e:
            self.logger.debug(f"检查抽象模式时出错: {e}")
            return False
    
    def _analyze_cursor_context(self, cursor) -> Dict[str, Any]:
        """基于cursor语义信息分析上下文 - 纯cursor驱动"""
        result = {
            'is_external_but_referenced': False,
            'is_project_internal': False,
            'is_library_type': False,
            'context_confidence': 0.0
        }
        
        if not cursor:
            return result
        
        try:
            # 1. 通过cursor的location分析文件上下文
            location_context = self._analyze_cursor_location_context(cursor)
            result.update(location_context)
            
            # 2. 通过cursor的命名空间分析库类型
            namespace_context = self._analyze_cursor_namespace_context(cursor)
            result.update(namespace_context)
            
            # 3. 通过cursor的继承关系分析重要性
            inheritance_context = self._analyze_cursor_inheritance_context(cursor)
            result.update(inheritance_context)
            
            # 4. 通过cursor的模板特征分析外部性
            template_context = self._analyze_cursor_template_context(cursor)
            result.update(template_context)
            
            # 5. 计算上下文分析的置信度
            result['context_confidence'] = self._calculate_cursor_context_confidence(result)
            
        except Exception as e:
            self.logger.debug(f"分析cursor上下文时出错: {e}")
        
        return result
    
    def _analyze_cursor_location_context(self, cursor) -> Dict[str, Any]:
        """通过cursor的location分析文件上下文"""
        result = {}
        
        try:
            if hasattr(cursor, 'location') and cursor.location.file:
                file_path = cursor.location.file.name
                file_context = self._classify_file_context(file_path)
                result.update(file_context)
        except Exception as e:
            self.logger.debug(f"分析cursor位置上下文时出错: {e}")
        
        return result
    
    def _analyze_cursor_namespace_context(self, cursor) -> Dict[str, Any]:
        """通过cursor的命名空间分析库类型"""
        result = {}
        
        try:
            namespace_parts = self._extract_namespace_hierarchy_cursor(cursor)
            
            if 'std' in namespace_parts:
                result['is_library_type'] = True
                result['is_external_but_referenced'] = True
            elif any(ns in ['detail', 'internal', 'impl'] for ns in namespace_parts):
                result['is_implementation_detail'] = True
            elif len(namespace_parts) >= 3:
                result['is_deep_namespace'] = True
        except Exception as e:
            self.logger.debug(f"分析cursor命名空间上下文时出错: {e}")
        
        return result
    
    def _analyze_cursor_inheritance_context(self, cursor) -> Dict[str, Any]:
        """通过cursor的继承关系分析重要性"""
        result = {}
        
        try:
            import clang.cindex as clang
            
            # 统计基类和虚方法
            base_class_count = 0
            virtual_method_count = 0
            
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.CXX_BASE_SPECIFIER:
                    base_class_count += 1
                elif child.kind == clang.CursorKind.CXX_METHOD:
                    if hasattr(child, 'is_virtual_method') and child.is_virtual_method():
                        virtual_method_count += 1
            
            result['base_class_count'] = base_class_count
            result['virtual_method_count'] = virtual_method_count
            
            # 如果有多个虚方法，可能是重要的基类
            if virtual_method_count >= 2:
                result['is_external_but_referenced'] = True
        except Exception as e:
            self.logger.debug(f"分析cursor继承上下文时出错: {e}")
        
        return result
    
    def _analyze_cursor_template_context(self, cursor) -> Dict[str, Any]:
        """通过cursor的模板特征分析外部性"""
        result = {}
        
        try:
            import clang.cindex as clang
            
            if cursor.kind == clang.CursorKind.CLASS_TEMPLATE:
                result['is_template_definition'] = True
                result['is_library_type'] = True
            
            if self.template_resolver._is_template_specialization_cursor(cursor):
                result['is_template_specialization'] = True
                result['is_library_type'] = True
        except Exception as e:
            self.logger.debug(f"分析cursor模板上下文时出错: {e}")
        
        return result
    
    def _calculate_cursor_context_confidence(self, analysis_result: Dict[str, Any]) -> float:
        """计算基于cursor的上下文分析置信度"""
        confidence = 0.0
        
        try:
            if analysis_result.get('is_project', False):
                confidence += 0.3
            if analysis_result.get('virtual_method_count', 0) > 0:
                confidence += 0.2
            if analysis_result.get('is_template_definition', False):
                confidence += 0.2
            if analysis_result.get('base_class_count', 0) > 0:
                confidence += 0.15
            if analysis_result.get('is_library_type', False):
                confidence += 0.15
            
            return min(confidence, 1.0)
        except Exception:
            return 0.0
    
    def _analyze_context_from_ast(self, usr: str) -> Dict[str, Any]:
        """从AST语义信息分析上下文"""
        result = {
            'is_external_but_referenced': False,
            'is_project_internal': False,
            'is_library_type': False
        }
        
        try:
            # 1. 检查是否在现有类中有定义（说明是项目内部的）
            if usr in self.classes:
                class_obj = self.classes[usr]
                result['is_project_internal'] = True
                
                # 分析定义文件路径来判断是否为外部库
                if hasattr(class_obj, 'definition_file_id'):
                    file_path = class_obj.definition_file_id
                    if self._is_external_library_file(file_path):
                        result['is_library_type'] = True
                        result['is_project_internal'] = False
            
            # 2. 通过模板解析器的继承关系分析
            if hasattr(self.template_resolver, 'inheritance_relationships'):
                inheritance_context = self._analyze_inheritance_context(usr)
                result.update(inheritance_context)
            
            # 3. 通过模板实例化信息分析
            if hasattr(self.template_resolver, 'template_instantiations'):
                template_context = self._analyze_template_instantiation_context(usr)
                result.update(template_context)
            
        except Exception as e:
            self.logger.debug(f"从AST分析上下文时出错: {e}")
        
        return result
    
    def _analyze_reference_context(self, usr: str) -> Dict[str, Any]:
        """通过引用关系分析上下文"""
        result = {}
        
        try:
            # 统计引用次数和类型
            inheritance_refs = 0
            template_refs = 0
            
            # 在继承关系中的引用
            for class_usr, class_obj in self.classes.items():
                if hasattr(class_obj, 'parent_classes') and usr in class_obj.parent_classes:
                    inheritance_refs += 1
            
            # 在模板实例化中的引用
            if hasattr(self.template_resolver, 'template_instantiations'):
                for base_template, instantiations in self.template_resolver.template_instantiations.items():
                    for inst in instantiations:
                        if usr in [inst.template_usr, inst.specialized_usr]:
                            template_refs += 1
            
            # 如果被多次引用，说明重要
            total_refs = inheritance_refs + template_refs
            if total_refs > 1:
                result['is_external_but_referenced'] = True
            
            result['inheritance_reference_count'] = inheritance_refs
            result['template_reference_count'] = template_refs
            result['total_reference_count'] = total_refs
            
        except Exception as e:
            self.logger.debug(f"分析引用上下文时出错: {e}")
        
        return result
    
    def _analyze_definition_location_context(self, usr: str) -> Dict[str, Any]:
        """通过定义位置分析上下文"""
        result = {}
        
        try:
            # 检查定义位置信息
            if usr in self.classes:
                class_obj = self.classes[usr]
                
                # 分析声明文件
                if hasattr(class_obj, 'declaration_file_id'):
                    decl_file = class_obj.declaration_file_id
                    result['declaration_context'] = self._classify_file_context(decl_file)
                
                # 分析定义文件
                if hasattr(class_obj, 'definition_file_id'):
                    def_file = class_obj.definition_file_id
                    result['definition_context'] = self._classify_file_context(def_file)
                
                # 如果定义在系统/外部文件，但被项目代码引用
                if (result.get('definition_context', {}).get('is_external', False) and
                    result.get('total_reference_count', 0) > 0):
                    result['is_external_but_referenced'] = True
            
        except Exception as e:
            self.logger.debug(f"分析定义位置上下文时出错: {e}")
        
        return result
    
    def _analyze_dependency_context(self, usr: str) -> Dict[str, Any]:
        """通过依赖关系分析外部性"""
        result = {}
        
        try:
            # 分析USR的命名空间层次结构（但基于真实的AST信息）
            namespace_analysis = self._analyze_real_namespace_context(usr)
            result.update(namespace_analysis)
            
            # 分析模板依赖关系（基于cursor）
            cursor = self.template_resolver.get_cursor_by_usr(usr)
            if cursor and self.template_resolver._is_template_specialization_cursor(cursor):
                template_deps = self._analyze_template_dependencies_cursor(cursor)
                result.update(template_deps)
            
        except Exception as e:
            self.logger.debug(f"分析依赖上下文时出错: {e}")
        
        return result
    
    def _is_external_library_file(self, file_path: str) -> bool:
        """基于文件路径判断是否为外部库文件"""
        if not file_path or file_path in ['<generated>', '<dynamic_generated>']:
            return False
        
        file_path_lower = file_path.lower()
        
        # 首先检查明确的系统路径
        system_indicators = [
            # Windows系统路径
            'c:\\program files',
            'microsoft visual studio',
            'windows kits',
            # Unix系统路径
            '/usr/include',
            '/usr/local/include',
            '/opt/',
        ]
        
        if any(indicator in file_path_lower for indicator in system_indicators):
            return True
        
        # 然后检查第三方库路径（需要更精确）
        third_party_indicators = [
            'third_party/',
            'external/',
            'vendor/',
        ]
        
        if any(indicator in file_path_lower for indicator in third_party_indicators):
            return True
        
        # 对于一般的lib/include目录，需要更谨慎判断
        # 只有当它们在明确的外部路径中时才认为是外部库
        if ('lib/' in file_path_lower or 'include/' in file_path_lower):
            # 检查是否在项目根目录的相对路径中（项目内的lib/include目录）
            if file_path_lower.startswith(('src/', 'include/', 'lib/', 'libs/', 'headers/')):
                return False  # 项目内的目录
            # 检查是否为绝对路径的外部库
            if ('/' in file_path and not file_path_lower.startswith('./')) or ('\\' in file_path and ':' in file_path):
                return True
        
        return False
    
    def _analyze_inheritance_context(self, usr: str) -> Dict[str, Any]:
        """分析继承关系上下文"""
        result = {}
        
        try:
            inheritance_relationships = self.template_resolver.inheritance_relationships
            
            # 检查该USR在继承关系中的角色
            is_base_class = any(usr in base_list for base_list in inheritance_relationships.values())
            is_derived_class = usr in inheritance_relationships
            
            result['is_inheritance_base'] = is_base_class
            result['is_inheritance_derived'] = is_derived_class
            
            # 如果是基类，统计派生类数量
            if is_base_class:
                derived_count = sum(1 for base_list in inheritance_relationships.values() if usr in base_list)
                result['derived_classes_count'] = derived_count
                
                # 多个派生类说明是重要的基类
                if derived_count >= 2:
                    result['is_external_but_referenced'] = True
            
        except Exception as e:
            self.logger.debug(f"分析继承上下文时出错: {e}")
        
        return result
    
    def _analyze_template_instantiation_context(self, usr: str) -> Dict[str, Any]:
        """分析模板实例化上下文"""
        result = {}
        
        try:
            template_instantiations = self.template_resolver.template_instantiations
            
            # 检查是否为模板基类
            is_template_base = usr in template_instantiations
            
            # 检查是否为模板实例化
            is_template_instance = any(
                usr in [inst.specialized_usr for inst in insts]
                for insts in template_instantiations.values()
            )
            
            result['is_template_base'] = is_template_base
            result['is_template_instance'] = is_template_instance
            
            # 如果是被多次实例化的模板基类
            if is_template_base:
                instantiation_count = len(template_instantiations[usr])
                result['template_instantiation_count'] = instantiation_count
                
                if instantiation_count >= 2:
                    result['is_external_but_referenced'] = True
                    result['is_library_type'] = True  # 多次实例化通常说明是库类型
            
        except Exception as e:
            self.logger.debug(f"分析模板实例化上下文时出错: {e}")
        
        return result
    
    def _classify_file_context(self, file_path: str) -> Dict[str, Any]:
        """分类文件上下文"""
        result = {
            'is_external': False,
            'is_system': False,
            'is_third_party': False,
            'is_project': False
        }
        
        if not file_path or file_path in ['<generated>', '<dynamic_generated>']:
            return result
        
        file_path_lower = file_path.lower()
        
        # 系统文件
        if any(indicator in file_path_lower for indicator in [
            'microsoft visual studio', 'windows kits', '/usr/include', '/usr/local/include'
        ]):
            result['is_system'] = True
            result['is_external'] = True
        
        # 第三方库
        elif any(indicator in file_path_lower for indicator in [
            'third_party', 'external', 'vendor', 'boost', 'qt'
        ]):
            result['is_third_party'] = True
            result['is_external'] = True
        
        # 项目文件
        else:
            result['is_project'] = True
        
        return result
    
    def _analyze_real_namespace_context(self, usr: str) -> Dict[str, Any]:
        """分析真实的命名空间上下文（基于AST而非字符串匹配）"""
        result = {}
        
        try:
            # 基于真实的命名空间信息分析
            if usr in self.namespaces:
                namespace_obj = self.namespaces[usr]
                
                # 分析命名空间的特征
                if hasattr(namespace_obj, 'name'):
                    ns_name = namespace_obj.name.lower()
                    
                    # 根据命名空间名称的语义特征判断
                    if ns_name in ['std', 'boost']:
                        result['is_standard_library'] = True
                        result['is_library_type'] = True
                    elif 'detail' in ns_name or 'internal' in ns_name or 'private' in ns_name:
                        result['is_implementation_detail'] = True
            
            # 分析命名空间层次（基于cursor而非USR字符串解析）
            cursor = self.template_resolver.get_cursor_by_usr(usr)
            if cursor:
                namespace_parts = self._extract_namespace_hierarchy_cursor(cursor)
                if namespace_parts:
                    result['namespace_depth'] = len(namespace_parts)
                    result['root_namespace'] = namespace_parts[0] if namespace_parts else None
                    
                    # 深层命名空间通常表示内部实现
                    if len(namespace_parts) >= 3:
                        result['is_deep_implementation'] = True
        
        except Exception as e:
            self.logger.debug(f"分析命名空间上下文时出错: {e}")
        
        return result
    
    def _analyze_template_dependencies_cursor(self, template_cursor) -> Dict[str, Any]:
        """分析模板依赖关系 - 基于cursor"""
        result = {}
        
        try:
            # 提取基础模板
            base_template_cursor = self.template_resolver._extract_base_template_cursor(template_cursor)
            if base_template_cursor:
                base_template_usr = base_template_cursor.get_usr()
                result['base_template'] = base_template_usr
                
                # 检查基础模板是否为外部库（基于cursor的location）
                if hasattr(base_template_cursor, 'location') and base_template_cursor.location.file:
                    file_path = base_template_cursor.location.file.name
                    if self._is_external_library_file(file_path):
                        result['has_external_template_base'] = True
                        result['is_library_type'] = True
        
        except Exception as e:
            self.logger.debug(f"分析模板依赖时出错: {e}")
        
        return result
    

    
    def _calculate_context_confidence(self, analysis_result: Dict[str, Any]) -> float:
        """计算上下文分析的置信度"""
        confidence = 0.0
        
        try:
            # 基于不同信息源计算置信度
            if analysis_result.get('is_project_internal', False):
                confidence += 0.3
            
            if analysis_result.get('total_reference_count', 0) > 0:
                confidence += 0.2
            
            if analysis_result.get('definition_context', {}).get('is_project', False):
                confidence += 0.2
            
            if analysis_result.get('is_inheritance_base', False):
                confidence += 0.15
            
            if analysis_result.get('is_template_base', False):
                confidence += 0.15
            
            return min(confidence, 1.0)
        except Exception:
            return 0.0
    
    def _analyze_reference_importance(self, usr: str) -> Dict[str, Any]:
        """分析USR在项目中的重要性"""
        result = {}
        
        # 统计该USR作为基类被引用的次数
        reference_count = 0
        for class_usr, class_obj in self.classes.items():
            if hasattr(class_obj, 'parent_classes') and usr in class_obj.parent_classes:
                reference_count += 1
        
        # 如果被多个类继承，说明是重要的基类
        if reference_count >= 2:
            result['is_likely_base_class'] = True
        
        result['reference_count'] = reference_count
        return result
    
    def _is_unreal_engine_pattern_cursor(self, cursor) -> bool:
        """检查是否为Unreal Engine相关的类 - 基于cursor语义信息"""
        if not cursor:
            return False
        
        try:
            # 基于cursor的语义信息判断Unreal Engine模式
            class_name = cursor.spelling or cursor.displayname
            if not class_name:
                return False
            
            # Unreal Engine类命名约定
            unreal_prefixes = ['F', 'T', 'U', 'A', 'E']
            if len(class_name) > 1 and class_name[0] in unreal_prefixes:
                # 检查命名空间是否包含UE相关标识
                namespace_parts = self._extract_namespace_hierarchy_cursor(cursor)
                ue_namespaces = ['unreal', 'ue', 'ue4', 'ue5', 'epic']
                
                if any(ns.lower() in ue_namespaces for ns in namespace_parts):
                    return True
                
                # 检查文件路径是否包含UE相关路径
                if hasattr(cursor, 'location') and cursor.location.file:
                    file_path = cursor.location.file.name.lower()
                    ue_paths = ['unreal', 'ue4', 'ue5', 'engine']
                    if any(path in file_path for path in ue_paths):
                        return True
            
            return False
        except Exception as e:
            self.logger.debug(f"检查Unreal Engine模式时出错: {e}")
            return False
    
    def _count_usr_references(self, usr: str) -> int:
        """统计USR在项目中的引用次数"""
        count = 0
        
        # 在所有类的parent_classes中查找
        for class_obj in self.classes.values():
            if hasattr(class_obj, 'parent_classes') and usr in class_obj.parent_classes:
                count += 1
        
        # 在函数参数类型中查找（如果需要的话，可以扩展）
        # 这里可以添加更多的引用统计逻辑
        
        return count
    
    def _calculate_generation_confidence(self, analysis: Dict[str, Any]) -> float:
        """计算生成基类的置信度分数"""
        score = 0.0
        
        # 各种因素的权重
        if analysis.get('is_likely_base_class', False):
            score += 0.4
        
        if analysis.get('is_utility_or_policy_class', False):
            score += 0.3
        
        if analysis.get('is_external_but_referenced', False):
            score += 0.2
        
        # 引用次数加分
        ref_count = analysis.get('reference_count', 0)
        if ref_count > 0:
            score += min(0.1 * ref_count, 0.3)  # 最多加0.3分
        
        return min(score, 1.0)  # 确保不超过1.0
    
    def _create_simple_base_class(self, usr: str) -> Optional[Class]:
        """创建简单的基类"""
        try:
            from .data_structures import Class, CppOopExtensions
            
            # 从cursor中提取类名（如果有cursor的话）
            cursor = self.template_resolver.get_cursor_by_usr(usr)
            if cursor:
                class_name = cursor.spelling or cursor.displayname or "UnknownClass"
            else:
                # 没有cursor时的回退方案
                class_name = "GeneratedClass"
            
            simple_class = Class(
                name=class_name,
                qualified_name=class_name,
                usr_id=usr,
                definition_file_id="<generated>",
                declaration_file_id="<generated>",
                line=0,
                declaration_locations=[],
                definition_location=None,
                is_declaration=True,
                is_definition=False,
                methods=[],
                is_abstract=False,
                cpp_oop_extensions=CppOopExtensions(qualified_name=class_name),
                parent_classes=[]
            )
            
            simple_class.is_generated_base = True
            
            return simple_class
            
        except Exception as e:
            self.logger.error(f"创建简单基类 {usr} 时出错: {e}")
            return None
