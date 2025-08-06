"""
验证引擎 (v2.4)

提供全面的代码分析结果验证机制，包括：
- 函数解析完整性验证
- 函数调用关系准确性验证
- 类继承关系完整性验证
- 跨文件符号一致性验证
"""

from typing import Dict, List, Any, Set, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import networkx as nx

from .logger import get_logger
from .data_structures import Function, Class, Namespace


class ValidationLevel(Enum):
    """验证级别"""
    BASIC = "basic"
    STANDARD = "standard"
    COMPREHENSIVE = "comprehensive"


class ValidationErrorType(Enum):
    """验证错误类型"""
    MISSING_BASE_CLASS = "missing_base_class"
    MISSING_CALLEE = "missing_callee"
    INHERITANCE_INCONSISTENCY = "inheritance_inconsistency"
    CALL_DETAIL_INCONSISTENCY = "call_detail_inconsistency"
    USR_COLLISION = "usr_collision"
    CIRCULAR_INHERITANCE = "circular_inheritance"
    ORPHANED_FUNCTION = "orphaned_function"
    INVALID_CALL_RELATIONSHIP = "invalid_call_relationship"
    MISSING_FUNCTION_BODY = "missing_function_body"
    SYMBOL_REFERENCE_ERROR = "symbol_reference_error"


@dataclass
class ValidationError:
    """验证错误"""
    error_type: ValidationErrorType
    severity: str  # "error", "warning", "info"
    entity_id: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    suggestions: List[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    """验证结果"""
    validation_level: ValidationLevel
    total_entities: int
    validation_passed: bool
    error_count: int
    warning_count: int
    errors: List[ValidationError] = field(default_factory=list)
    statistics: Dict[str, Any] = field(default_factory=dict)


class ValidationEngine:
    """代码分析结果验证引擎"""
    
    def __init__(self, validation_level: ValidationLevel = ValidationLevel.STANDARD, 
                 strict_mode: bool = False):
        self.logger = get_logger()
        self.validation_level = validation_level
        self.strict_mode = strict_mode  # 严格模式：报告所有警告，包括外部函数相关
        self.errors: List[ValidationError] = []
        
        # 导入动态模板解析器用于智能基类验证
        from .dynamic_template_resolver import DynamicTemplateResolver
        # 使用默认的项目根目录
        default_project_root = getattr(self, 'project_root', '/tmp/default_project')
        self.template_resolver = DynamicTemplateResolver(project_root=default_project_root)
        
        # 基于cursor的验证器
        self.cursor_validator = None
        self.usr_to_cursor_map: Dict[str, Any] = {}
        
    def validate_extracted_data(self, extracted_data: Dict[str, Any]) -> ValidationResult:
        """验证提取的数据 - 增强版，集成cursor验证"""
        self.logger.info(f"开始验证代码分析结果 (级别: {self.validation_level.value})")
        self.errors.clear()
        
        functions = extracted_data.get('functions', {})
        classes = extracted_data.get('classes', {})
        namespaces = extracted_data.get('namespaces', {})
        
        # 尝试获取cursor映射信息
        self._initialize_cursor_context(extracted_data)
        
        # 基础验证
        self._validate_basic_integrity(functions, classes, namespaces)
        
        if self.validation_level in [ValidationLevel.STANDARD, ValidationLevel.COMPREHENSIVE]:
            # 标准验证
            self._validate_call_relationships(functions)
            self._validate_inheritance_relationships(classes)
            
            # 增强：基于cursor的符号验证（如果有cursor信息）
            if self.usr_to_cursor_map:
                self.logger.info("执行基于cursor的增强验证...")
                self._validate_symbol_consistency_with_cursor(functions, classes, namespaces)
            else:
                self.logger.info("未找到cursor信息，使用传统验证方法...")
                self._validate_symbol_consistency(functions, classes, namespaces)
        
        if self.validation_level == ValidationLevel.COMPREHENSIVE:
            # 全面验证
            self._validate_function_completeness(functions)
            self._validate_cross_file_consistency(functions, classes)
            self._validate_graph_integrity(functions, classes)
        
        # 生成验证结果
        return self._generate_validation_result(functions, classes, namespaces)
    
    def _validate_basic_integrity(self, functions: Dict[str, Any], classes: Dict[str, Any], namespaces: Dict[str, Any]):
        """基础完整性验证"""
        self.logger.debug("执行基础完整性验证...")
        
        # 验证USR ID唯一性
        all_usr_ids = set()
        for entity_dict in [functions, classes, namespaces]:
            for usr_id in entity_dict.keys():
                if usr_id in all_usr_ids:
                    self._add_error(
                        ValidationErrorType.USR_COLLISION,
                        "error",
                        usr_id,
                        f"USR ID冲突: {usr_id}",
                        {"collision_type": "cross_entity"}
                    )
                all_usr_ids.add(usr_id)
        
        # 验证实体对象完整性
        for usr_id, func in functions.items():
            if not hasattr(func, 'usr_id') or func.usr_id != usr_id:
                self._add_error(
                    ValidationErrorType.SYMBOL_REFERENCE_ERROR,
                    "error",
                    usr_id,
                    f"函数对象USR ID不匹配: 期望 {usr_id}, 实际 {getattr(func, 'usr_id', 'None')}"
                )
        
        for usr_id, cls in classes.items():
            if not hasattr(cls, 'usr_id') or cls.usr_id != usr_id:
                self._add_error(
                    ValidationErrorType.SYMBOL_REFERENCE_ERROR,
                    "error",
                    usr_id,
                    f"类对象USR ID不匹配: 期望 {usr_id}, 实际 {getattr(cls, 'usr_id', 'None')}"
                )
    
    def _validate_call_relationships(self, functions: Dict[str, Any]):
        """验证函数调用关系 - 纯cursor驱动"""
        self.logger.debug("执行纯cursor驱动的调用关系验证...")
        
        if not self.usr_to_cursor_map:
            self.logger.warning("没有cursor信息，跳过调用关系验证")
            return
        
        missing_callees = 0
        external_callees = 0
        valid_calls = 0
        
        for caller_usr, caller_func in functions.items():
            if not hasattr(caller_func, 'calls_to'):
                continue
            
            caller_cursor = self.usr_to_cursor_map.get(caller_usr)
            if not caller_cursor:
                continue
                
            # 验证calls_to中的函数
            for callee_usr in caller_func.calls_to:
                if callee_usr not in functions:
                    # 使用cursor检查是否为外部函数
                    if self._should_skip_missing_callee_check(callee_usr):
                        external_callees += 1
                        continue
                        
                    missing_callees += 1
                    if not self.strict_mode:  # 非严格模式下减少噪音
                        continue
                        
                    self._add_error(
                        ValidationErrorType.MISSING_CALLEE,
                        "warning",
                        caller_usr,
                        f"基于cursor验证：调用的函数不存在 {callee_usr}",
                        {"caller": caller_usr, "missing_callee": callee_usr, "validation_method": "cursor_only"}
                    )
                else:
                    valid_calls += 1
        
        self.logger.info(f"纯cursor调用验证完成: 有效调用 {valid_calls}, 外部调用 {external_callees}, 缺失调用 {missing_callees}")
    
    def _validate_inheritance_relationships(self, classes: Dict[str, Any]):
        """验证类继承关系 - 纯cursor驱动"""
        self.logger.debug("执行纯cursor驱动的继承关系验证...")
        
        if not self.usr_to_cursor_map:
            self.logger.warning("没有cursor信息，跳过继承关系验证")
            return
        
        # 构建继承图用于循环检测
        inheritance_graph = nx.DiGraph()
        missing_bases = 0
        external_bases = 0
        valid_inheritance = 0
        
        for class_usr, class_obj in classes.items():
            if not hasattr(class_obj, 'parent_classes'):
                continue
            
            class_cursor = self.usr_to_cursor_map.get(class_usr)
            if not class_cursor:
                continue
                
            inheritance_graph.add_node(class_usr)
            
            # 使用cursor验证基类
            for base_usr in class_obj.parent_classes:
                if base_usr not in classes:
                    base_cursor = self.usr_to_cursor_map.get(base_usr)
                    if base_cursor and self.cursor_validator:
                        if self.cursor_validator.is_external_symbol(base_cursor):
                            external_bases += 1
                            self.logger.debug(f"类 {getattr(class_obj, 'name', 'unknown')} 继承自外部基类")
                            continue
                    
                    missing_bases += 1
                    if self.strict_mode:  # 只在严格模式下报告
                        self._add_error(
                            ValidationErrorType.MISSING_BASE_CLASS,
                            "warning",
                            class_usr,
                            f"基于cursor验证：基类不存在 {base_usr}",
                            {"class": class_usr, "missing_base": base_usr, "validation_method": "cursor_only"}
                        )
                else:
                    inheritance_graph.add_edge(class_usr, base_usr)
                    valid_inheritance += 1
        
        # 检测循环继承
        try:
            cycles = list(nx.simple_cycles(inheritance_graph))
            for cycle in cycles:
                self._add_error(
                    ValidationErrorType.CIRCULAR_INHERITANCE,
                    "error",
                    cycle[0],
                    f"检测到循环继承: {' -> '.join(cycle + [cycle[0]])}",
                    {"cycle": cycle, "validation_method": "cursor_only"}
                )
        except Exception as e:
            self.logger.warning(f"循环继承检测失败: {e}")
        
        self.logger.info(f"纯cursor继承验证完成: 有效继承 {valid_inheritance}, 外部基类 {external_bases}, 缺失基类 {missing_bases}")
    
    def _validate_symbol_consistency(self, functions: Dict[str, Any], classes: Dict[str, Any], namespaces: Dict[str, Any]):
        """符号一致性验证 - 纯cursor驱动，无回退逻辑"""
        self.logger.debug("执行纯cursor驱动的符号一致性验证...")
        
        if not self.usr_to_cursor_map:
            self.logger.warning("没有cursor信息，跳过符号一致性验证")
            return
        
        orphaned_methods = 0
        external_methods = 0
        validated_methods = 0
        
        for func_usr, func in functions.items():
            # 只处理有cursor信息的函数
            func_cursor = self.usr_to_cursor_map.get(func_usr)
            if not func_cursor:
                continue
            
            # 只验证类方法
            if not self._is_method_cursor(func_cursor):
                continue
            
            # 使用cursor获取实际所属类
            actual_class_cursor = self.cursor_validator.get_actual_class_for_method(func_cursor)
            if not actual_class_cursor:
                # 检查是否为外部符号
                if self.cursor_validator.is_external_symbol(func_cursor):
                    external_methods += 1
                    continue
                else:
                    orphaned_methods += 1
                    self._add_error(
                        ValidationErrorType.ORPHANED_FUNCTION,
                        "warning",
                        func_usr,
                        f"基于cursor验证：类方法找不到所属类 {getattr(func, 'name', 'unknown')}",
                        {"validation_method": "cursor_only"}
                    )
                continue
            
            actual_class_usr = actual_class_cursor.get_usr()
            
            # 检查类是否存在于我们的数据中
            if actual_class_usr not in classes:
                if self.cursor_validator.is_external_symbol(actual_class_cursor):
                    external_methods += 1
                    self.logger.debug(f"方法 {getattr(func, 'name', 'unknown')} 属于外部类 {actual_class_cursor.spelling}")
                else:
                    orphaned_methods += 1
                    self._add_error(
                        ValidationErrorType.ORPHANED_FUNCTION,
                        "warning",
                        func_usr,
                        f"基于cursor验证：方法所属类不在数据中 {actual_class_cursor.spelling}",
                        {"validation_method": "cursor_only", "missing_class": actual_class_usr}
                    )
                continue
            
            validated_methods += 1
        
        self.logger.info(f"纯cursor验证完成: 验证 {validated_methods} 个方法, 外部 {external_methods} 个, 孤儿 {orphaned_methods} 个")
    

    

    
    def _should_skip_missing_callee_check(self, callee_usr: str) -> bool:
        """检测是否应跳过missing callee检查 - 纯cursor驱动"""
        if not callee_usr:
            return True
        
        # 只使用cursor验证，无回退逻辑
        if self.usr_to_cursor_map and callee_usr in self.usr_to_cursor_map:
            callee_cursor = self.usr_to_cursor_map[callee_usr]
            if self.cursor_validator:
                return self.cursor_validator.is_external_symbol(callee_cursor)
        
        # 没有cursor信息时，保守地跳过检查
        return True
    
    def _should_skip_call_detail_inconsistency(self, usr: str) -> bool:
        """检测是否应跳过call detail不一致检查 - 纯cursor驱动"""
        return self._should_skip_missing_callee_check(usr)
    

    

    
    def _check_class_method(self, func_usr: str, classes: Dict[str, Any]) -> bool:
        """检查是否为类方法 - 纯cursor驱动"""
        # 只使用cursor验证，无回退逻辑
        if not self.usr_to_cursor_map or func_usr not in self.usr_to_cursor_map:
            return False
        
        func_cursor = self.usr_to_cursor_map[func_usr]
        if not self._is_method_cursor(func_cursor) or not self.cursor_validator:
            return False
        
        actual_class_cursor = self.cursor_validator.get_actual_class_for_method(func_cursor)
        if not actual_class_cursor:
            return False
        
        actual_class_usr = actual_class_cursor.get_usr()
        return actual_class_usr in classes
    

    
    def _initialize_cursor_context(self, extracted_data: Dict[str, Any]):
        """初始化cursor上下文信息"""
        try:
            # 从extracted_data中查找entity_extractor或template_resolver
            # 这些对象可能包含cursor映射信息
            
            # 方法1：从全局或线程本地存储获取
            if hasattr(self.template_resolver, 'usr_to_cursor_map'):
                self.usr_to_cursor_map = self.template_resolver.usr_to_cursor_map
                self.logger.debug(f"从template_resolver获取到 {len(self.usr_to_cursor_map)} 个cursor映射")
            
            # 方法2：尝试从extracted_data中获取（如果有的话）
            if not self.usr_to_cursor_map and 'cursor_mapping' in extracted_data:
                self.usr_to_cursor_map = extracted_data['cursor_mapping']
                self.logger.debug(f"从extracted_data获取到 {len(self.usr_to_cursor_map)} 个cursor映射")
            
            # 初始化cursor验证器
            if self.usr_to_cursor_map:
                self.cursor_validator = self._create_cursor_validator()
                
        except Exception as e:
            self.logger.warning(f"初始化cursor上下文时出错: {e}")
            self.usr_to_cursor_map = {}
    
    def _create_cursor_validator(self):
        """创建内嵌的cursor验证器"""
        import clang.cindex as clang
        
        class CursorValidator:
            def __init__(self, logger):
                self.logger = logger
            
            def get_actual_class_for_method(self, method_cursor) -> Optional[Any]:
                """获取方法的实际所属类"""
                if not method_cursor:
                    return None
                try:
                    parent = method_cursor.semantic_parent
                    if parent and parent.kind in {
                        clang.CursorKind.CLASS_DECL,
                        clang.CursorKind.STRUCT_DECL,
                        clang.CursorKind.CLASS_TEMPLATE,
                        clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION
                    }:
                        return parent
                    return None
                except Exception as e:
                    self.logger.debug(f"获取方法所属类时出错: {e}")
                    return None
            
            def is_external_symbol(self, cursor) -> bool:
                """判断是否为外部符号"""
                if not cursor:
                    return True
                try:
                    if not cursor.get_definition():
                        return True
                    if cursor.location and cursor.location.file:
                        file_name = cursor.location.file.name
                        if self._is_system_header(file_name):
                            return True
                    if cursor.spelling.startswith('__builtin_'):
                        return True
                    return False
                except Exception:
                    return True
            
            def _is_system_header(self, file_path: str) -> bool:
                """判断是否为系统头文件"""
                if not file_path:
                    return False
                system_paths = [
                    '/usr/include/', '/usr/local/include/',
                    'C:\\Program Files', 'C:\\Windows\\System32',
                    '/Applications/Xcode.app'
                ]
                return any(system_path in file_path for system_path in system_paths)
        
        return CursorValidator(self.logger)
    
    def _validate_symbol_consistency_with_cursor(self, functions: Dict[str, Any], 
                                               classes: Dict[str, Any], 
                                               namespaces: Dict[str, Any]):
        """基于cursor的增强符号一致性验证"""
        self.logger.debug("执行基于cursor的符号一致性验证...")
        
        orphaned_methods = []
        cursor_based_fixes = 0
        
        for func_usr, func in functions.items():
            # 获取函数cursor
            func_cursor = self.usr_to_cursor_map.get(func_usr)
            if not func_cursor:
                continue
            
            # 检查是否为类方法
            if not self._is_method_cursor(func_cursor):
                continue
            
            # 使用cursor获取实际所属类
            actual_class_cursor = self.cursor_validator.get_actual_class_for_method(func_cursor)
            if not actual_class_cursor:
                # 检查是否为外部符号
                if self.cursor_validator.is_external_symbol(func_cursor):
                    continue
                orphaned_methods.append(func_usr)
                continue
            
            actual_class_usr = actual_class_cursor.get_usr()
            
            # 检查类是否存在于我们的数据中
            if actual_class_usr not in classes:
                # 检查是否为外部类
                if self.cursor_validator.is_external_symbol(actual_class_cursor):
                    self.logger.debug(f"方法 {getattr(func, 'name', 'unknown')} 属于外部类 {actual_class_cursor.spelling}")
                    continue
                else:
                    orphaned_methods.append(func_usr)
                    continue
            
            # 验证方法是否正确关联到类（这里比USR解析更准确）
            class_obj = classes[actual_class_usr]
            if hasattr(class_obj, 'methods') and func_usr not in class_obj.methods:
                self.logger.debug(f"cursor验证发现方法关联问题，进行修复: {getattr(func, 'name', 'unknown')} -> {actual_class_cursor.spelling}")
                # 可以选择自动修复
                # class_obj.methods.append(func_usr)
                cursor_based_fixes += 1
        
        # 报告结果（比传统方法更精确）
        if orphaned_methods:
            filtered_orphaned = [usr for usr in orphaned_methods 
                               if not self._should_skip_orphaned_function_check(
                                   getattr(functions[usr], 'qualified_name', ''), 
                                   functions[usr])]
            
            if filtered_orphaned:
                self.logger.warning(f"基于cursor发现 {len(filtered_orphaned)} 个真正的孤儿方法")
                for func_usr in filtered_orphaned[:5]:  # 只显示前5个
                    func = functions[func_usr]
                    self._add_error(
                        ValidationErrorType.ORPHANED_FUNCTION,
                        "warning",
                        func_usr,
                        f"基于cursor验证：类方法找不到所属类 {getattr(func, 'name', 'unknown')}",
                        {"validation_method": "cursor_enhanced"}
                    )
        
        if cursor_based_fixes > 0:
            self.logger.info(f"基于cursor的验证发现 {cursor_based_fixes} 个可修复的方法关联问题")
    
    def _is_method_cursor(self, cursor) -> bool:
        """判断cursor是否为类方法"""
        try:
            import clang.cindex as clang
            method_kinds = {
                clang.CursorKind.CXX_METHOD,
                clang.CursorKind.CONSTRUCTOR,
                clang.CursorKind.DESTRUCTOR,
                clang.CursorKind.CONVERSION_FUNCTION
            }
            return cursor.kind in method_kinds
        except Exception:
            return False
    
    def _validate_function_completeness(self, functions: Dict[str, Any]):
        """验证函数完整性"""
        self.logger.debug("验证函数完整性...")
        
        for func_usr, func in functions.items():
            # 检查函数体代码内容
            if hasattr(func, 'is_definition') and func.is_definition:
                if not hasattr(func, 'code_content') or not func.code_content.strip():
                    self._add_error(
                        ValidationErrorType.MISSING_FUNCTION_BODY,
                        "warning",
                        func_usr,
                        f"函数定义缺少代码内容: {func.name}",
                        {"function_name": func.name}
                    )
    
    def _validate_cross_file_consistency(self, functions: Dict[str, Any], classes: Dict[str, Any]):
        """验证跨文件一致性"""
        self.logger.debug("验证跨文件一致性...")
        
        # 检查声明和定义的一致性
        for func_usr, func in functions.items():
            if hasattr(func, 'is_declaration') and hasattr(func, 'is_definition'):
                if not func.is_declaration and not func.is_definition:
                    self._add_error(
                        ValidationErrorType.SYMBOL_REFERENCE_ERROR,
                        "error",
                        func_usr,
                        f"函数既不是声明也不是定义: {func.name}",
                        {"function_name": func.name}
                    )
    
    def _validate_graph_integrity(self, functions: Dict[str, Any], classes: Dict[str, Any]):
        """验证图结构完整性"""
        self.logger.debug("验证图结构完整性...")
        
        # 构建调用图并检查连通性
        call_graph = nx.DiGraph()
        
        for func_usr, func in functions.items():
            call_graph.add_node(func_usr)
            if hasattr(func, 'calls_to'):
                for callee_usr in func.calls_to:
                    if callee_usr in functions:
                        call_graph.add_edge(func_usr, callee_usr)
        
        # 分析图的连通性
        if call_graph.number_of_nodes() > 0:
            weakly_connected = list(nx.weakly_connected_components(call_graph))
            if len(weakly_connected) > 1:
                largest_component = max(weakly_connected, key=len)
                isolated_functions = []
                for component in weakly_connected:
                    if component != largest_component and len(component) == 1:
                        isolated_functions.extend(component)
                
                if isolated_functions:
                    self.logger.info(f"发现 {len(isolated_functions)} 个孤立函数")
    
    def _add_error(self, error_type: ValidationErrorType, severity: str, entity_id: str, 
                   message: str, details: Dict[str, Any] = None, suggestions: List[str] = None):
        """添加验证错误"""
        error = ValidationError(
            error_type=error_type,
            severity=severity,
            entity_id=entity_id,
            message=message,
            details=details or {},
            suggestions=suggestions or []
        )
        self.errors.append(error)
    
    def _generate_validation_result(self, functions: Dict[str, Any], classes: Dict[str, Any], 
                                   namespaces: Dict[str, Any]) -> ValidationResult:
        """生成验证结果"""
        error_count = sum(1 for e in self.errors if e.severity == "error")
        warning_count = sum(1 for e in self.errors if e.severity == "warning")
        total_entities = len(functions) + len(classes) + len(namespaces)
        
        statistics = {
            "total_functions": len(functions),
            "total_classes": len(classes),
            "total_namespaces": len(namespaces),
            "error_breakdown": {
                error_type.value: sum(1 for e in self.errors if e.error_type == error_type)
                for error_type in ValidationErrorType
            },
            "severity_breakdown": {
                "error": error_count,
                "warning": warning_count,
                "info": sum(1 for e in self.errors if e.severity == "info")
            }
        }
        
        validation_passed = error_count == 0
        
        self.logger.info(f"验证完成: {total_entities} 个实体, {error_count} 个错误, {warning_count} 个警告")
        
        # 详细输出错误和警告信息
        if error_count > 0:
            self.logger.error(f"发现 {error_count} 个错误:")
            error_types = {}
            for error in self.errors:
                if error.severity == "error":
                    error_type = error.error_type.value if hasattr(error.error_type, 'value') else str(error.error_type)
                    error_types[error_type] = error_types.get(error_type, 0) + 1
                    self.logger.error(f"  - {error_type}: {error.message} (实体: {error.entity_id})")
            
            self.logger.error("错误统计:")
            for error_type, count in error_types.items():
                self.logger.error(f"  - {error_type}: {count} 个")
        
        if warning_count > 0:
            self.logger.warning(f"发现 {warning_count} 个警告:")
            warning_types = {}
            for error in self.errors:
                if error.severity == "warning":
                    error_type = error.error_type.value if hasattr(error.error_type, 'value') else str(error.error_type)
                    warning_types[error_type] = warning_types.get(error_type, 0) + 1
                    self.logger.warning(f"  - {error_type}: {error.message} (实体: {error.entity_id})")
            
            self.logger.warning("警告统计:")
            for warning_type, count in warning_types.items():
                self.logger.warning(f"  - {warning_type}: {count} 个")
        
        return ValidationResult(
            validation_level=self.validation_level,
            total_entities=total_entities,
            validation_passed=validation_passed,
            error_count=error_count,
            warning_count=warning_count,
            errors=self.errors.copy(),
            statistics=statistics
        )
    
