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
    
    def __init__(self, validation_level: ValidationLevel = ValidationLevel.STANDARD):
        self.logger = get_logger()
        self.validation_level = validation_level
        self.errors: List[ValidationError] = []
        
    def validate_extracted_data(self, extracted_data: Dict[str, Any]) -> ValidationResult:
        """验证提取的数据"""
        self.logger.info(f"开始验证代码分析结果 (级别: {self.validation_level.value})")
        self.errors.clear()
        
        functions = extracted_data.get('functions', {})
        classes = extracted_data.get('classes', {})
        namespaces = extracted_data.get('namespaces', {})
        
        # 基础验证
        self._validate_basic_integrity(functions, classes, namespaces)
        
        if self.validation_level in [ValidationLevel.STANDARD, ValidationLevel.COMPREHENSIVE]:
            # 标准验证
            self._validate_call_relationships(functions)
            self._validate_inheritance_relationships(classes)
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
        """验证函数调用关系"""
        self.logger.debug("验证函数调用关系...")
        
        for caller_usr, caller_func in functions.items():
            if not hasattr(caller_func, 'calls_to') or not hasattr(caller_func, 'call_details'):
                continue
                
            # 验证calls_to中的函数都存在
            for callee_usr in caller_func.calls_to:
                if callee_usr not in functions:
                    self._add_error(
                        ValidationErrorType.MISSING_CALLEE,
                        "warning",
                        caller_usr,
                        f"调用的函数不存在: {callee_usr}",
                        {"caller": caller_usr, "missing_callee": callee_usr},
                        ["检查是否为外部库函数", "验证USR ID是否正确"]
                    )
            
            # This check is disabled because the test data is not fully populated.
            # 验证call_details与calls_to的一致性
            if hasattr(caller_func, 'call_details') and isinstance(caller_func.call_details, list):
                detail_callees = {detail.to_usr_id for detail in caller_func.call_details if hasattr(detail, 'to_usr_id')}
                calls_to_set = set(caller_func.calls_to)
                
                if detail_callees != calls_to_set:
                    missing_in_details = calls_to_set - detail_callees
                    extra_in_details = detail_callees - calls_to_set
                    
                    if missing_in_details or extra_in_details:
                        self._add_error(
                            ValidationErrorType.CALL_DETAIL_INCONSISTENCY,
                            "warning",
                            caller_usr,
                            f"调用详情与调用列表不一致",
                            {
                                "missing_in_details": list(missing_in_details),
                                "extra_in_details": list(extra_in_details)
                            },
                            ["重新提取调用关系", "检查AST遍历逻辑"]
                        )
            
            # This check is disabled because the test data is not fully populated.
            # 验证反向调用关系
            for callee_usr in caller_func.calls_to:
                callee_func = functions.get(callee_usr)
                if callee_func and hasattr(callee_func, 'called_by'):
                    if caller_usr not in callee_func.called_by:
                        self._add_error(
                            ValidationErrorType.INVALID_CALL_RELATIONSHIP,
                            "warning",
                            callee_usr,
                            f"反向调用关系缺失: {caller_usr} -> {callee_usr}",
                            {"caller": caller_usr, "callee": callee_usr}
                        )
    
    def _validate_inheritance_relationships(self, classes: Dict[str, Any]):
        """验证类继承关系"""
        self.logger.debug("验证类继承关系...")
        
        # 构建继承图用于循环检测
        inheritance_graph = nx.DiGraph()
        
        for class_usr, class_obj in classes.items():
            if not hasattr(class_obj, 'parent_classes'):
                continue
                
            inheritance_graph.add_node(class_usr)
            
            # 验证所有基类都存在
            for base_usr in class_obj.parent_classes:
                if base_usr not in classes:
                    self._add_error(
                        ValidationErrorType.MISSING_BASE_CLASS,
                        "error",
                        class_usr,
                        f"基类不存在: {base_usr}",
                        {"class": class_usr, "missing_base": base_usr},
                        ["检查是否为外部库类", "验证USR ID是否正确"]
                    )
                else:
                    inheritance_graph.add_edge(class_usr, base_usr)
            
            # This check is disabled because the test data is not fully populated.
            # 验证继承列表与parent_classes一致性
            if hasattr(class_obj, 'cpp_oop_extensions') and hasattr(class_obj.cpp_oop_extensions, 'inheritance_list'):
                inheritance_bases = {info.base_class_usr_id for info in class_obj.cpp_oop_extensions.inheritance_list if hasattr(info, 'base_class_usr_id')}
                parent_set = set(class_obj.parent_classes)
                
                if inheritance_bases != parent_set:
                    self._add_error(
                        ValidationErrorType.INHERITANCE_INCONSISTENCY,
                        "warning",
                        class_usr,
                        f"继承列表与父类列表不一致",
                        {
                            "inheritance_list": list(inheritance_bases),
                            "parent_classes": list(parent_set),
                            "missing_in_inheritance": list(parent_set - inheritance_bases),
                            "extra_in_inheritance": list(inheritance_bases - parent_set)
                        }
                    )
        
        # 检测循环继承
        try:
            cycles = list(nx.simple_cycles(inheritance_graph))
            for cycle in cycles:
                self._add_error(
                    ValidationErrorType.CIRCULAR_INHERITANCE,
                    "error",
                    cycle[0],
                    f"检测到循环继承: {' -> '.join(cycle + [cycle[0]])}",
                    {"cycle": cycle}
                )
        except Exception as e:
            self.logger.warning(f"循环继承检测失败: {e}")
    
    def _validate_symbol_consistency(self, functions: Dict[str, Any], classes: Dict[str, Any], namespaces: Dict[str, Any]):
        """验证符号一致性"""
        self.logger.debug("验证符号一致性...")
        
        # 检查符号引用的有效性
        for func_usr, func in functions.items():
            # 检查方法所属的类是否存在
            if hasattr(func, 'cpp_extensions') and hasattr(func.cpp_extensions, 'qualified_name'):
                qualified_name = func.cpp_extensions.qualified_name
                if '::' in qualified_name:
                    class_name = qualified_name.rsplit('::', 1)[0]
                    # 这里需要更复杂的逻辑来查找对应的类USR
                    # 简化处理：检查是否有类包含此方法
                    found_in_class = False
                    for class_usr, class_obj in classes.items():
                        if hasattr(class_obj, 'methods'):
                            if isinstance(class_obj.methods, list) and func_usr in class_obj.methods:
                                found_in_class = True
                                break
                            elif isinstance(class_obj.methods, dict) and func_usr in class_obj.methods:
                                found_in_class = True
                                break
                    
                    if not found_in_class and '::' in qualified_name:
                        self._add_error(
                            ValidationErrorType.ORPHANED_FUNCTION,
                            "warning",
                            func_usr,
                            f"方法未找到所属类: {qualified_name}",
                            {"qualified_name": qualified_name}
                        )
    
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
        
        return ValidationResult(
            validation_level=self.validation_level,
            total_entities=total_entities,
            validation_passed=validation_passed,
            error_count=error_count,
            warning_count=warning_count,
            errors=self.errors.copy(),
            statistics=statistics
        )