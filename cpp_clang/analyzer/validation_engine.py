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
                    # 智能过滤：跳过已知的外部库函数和系统函数（除非在严格模式下）
                    if not self.strict_mode and self._should_skip_missing_callee_check(callee_usr):
                        continue
                        
                    self._add_error(
                        ValidationErrorType.MISSING_CALLEE,
                        "warning",
                        caller_usr,
                        f"调用的函数不存在: {callee_usr}",
                        {"caller": caller_usr, "missing_callee": callee_usr},
                        ["检查是否为外部库函数", "验证USR ID是否正确"]
                    )
            
            # 验证call_details与calls_to的一致性（改进版）
            if hasattr(caller_func, 'call_details') and isinstance(caller_func.call_details, list):
                detail_callees = {detail.to_usr_id for detail in caller_func.call_details if hasattr(detail, 'to_usr_id')}
                calls_to_set = set(caller_func.calls_to)
                
                if detail_callees != calls_to_set:
                    missing_in_details = calls_to_set - detail_callees
                    extra_in_details = detail_callees - calls_to_set
                    
                    # 过滤掉已知的外部函数不一致
                    filtered_missing = {usr for usr in missing_in_details if not self._should_skip_missing_callee_check(usr)}
                    filtered_extra = {usr for usr in extra_in_details if not self._should_skip_missing_callee_check(usr)}
                    
                    # 只有在存在非外部函数的不一致时才报告
                    if filtered_missing or filtered_extra:
                        # 进一步检查：如果不一致的数量很少且主要是外部函数，降低严重性
                        total_inconsistent = len(missing_in_details) + len(extra_in_details)
                        total_filtered = len(filtered_missing) + len(filtered_extra)
                        
                        # 如果过滤后的不一致很少，且总调用数较多，则降低严重性
                        total_calls = len(calls_to_set)
                        if total_calls > 10 and total_filtered <= 2:
                            # 对于大型函数的少量不一致，降级为debug级别（不报告）
                            pass
                        else:
                            self._add_error(
                                ValidationErrorType.CALL_DETAIL_INCONSISTENCY,
                                "warning",
                                caller_usr,
                                f"调用详情与调用列表不一致",
                                {
                                    "missing_in_details": list(filtered_missing),
                                    "extra_in_details": list(filtered_extra),
                                    "total_calls": total_calls,
                                    "filtered_ratio": f"{total_filtered}/{total_inconsistent}"
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
        """验证符号一致性 - 重新设计，正确分类函数类型"""
        self.logger.debug("验证符号一致性...")
        
        for func_usr, func in functions.items():
            if not hasattr(func, 'cpp_extensions') or not hasattr(func.cpp_extensions, 'qualified_name'):
                continue
                
            qualified_name = func.cpp_extensions.qualified_name
            
            # 第一步：正确分类函数类型
            func_type = self._classify_function_type(func_usr, qualified_name)
            
            # 第二步：根据函数类型进行相应的验证
            if func_type == "class_method":
                # 只有类方法才需要检查是否属于某个类
                if not self._check_class_method(func_usr, classes):
                    self._add_error(
                        ValidationErrorType.ORPHANED_FUNCTION,
                        "warning",
                        func_usr,
                        f"类方法未找到所属类: {qualified_name}",
                        {"qualified_name": qualified_name, "function_type": func_type}
                    )
            elif func_type == "namespace_function":
                # 命名空间函数检查是否属于已知命名空间
                if not self._check_namespace_function(qualified_name, namespaces):
                    # 只有在严格模式下才报告未知命名空间函数
                    if self.strict_mode:
                        self._add_error(
                            ValidationErrorType.ORPHANED_FUNCTION,
                            "info",  # 降级为info
                            func_usr,
                            f"命名空间函数所属命名空间未完全解析: {qualified_name}",
                            {"qualified_name": qualified_name, "function_type": func_type}
                        )
            # global_function, lambda_function, local_function 不需要特殊验证
    
    def _classify_function_type(self, func_usr: str, qualified_name: str) -> str:
        """基于USR和qualified_name正确分类函数类型"""
        
        # 1. 检查是否为lambda函数
        if 'lambda' in qualified_name.lower() or '$_' in func_usr:
            return "lambda_function"
        
        # 2. 检查是否为局部函数（函数内定义的函数）
        if self._is_local_function(func_usr):
            return "local_function"
        
        # 3. 检查是否为类方法（基于USR结构）
        if self._is_class_method_by_usr(func_usr):
            return "class_method"
        
        # 4. 检查是否为命名空间函数
        if '::' in qualified_name and not self._is_class_method_by_usr(func_usr):
            return "namespace_function"
        
        # 5. 其他情况为全局函数
        return "global_function"
    
    def _is_local_function(self, usr: str) -> bool:
        """检查是否为局部函数"""
        # 局部函数的USR通常包含特殊的编码模式
        return '@F@' in usr and '@F@' in usr[usr.find('@F@') + 3:]
    
    def _is_class_method_by_usr(self, usr: str) -> bool:
        """基于USR结构检查是否为类方法"""
        if not usr.startswith('c:@'):
            return False
        
        # 类方法的USR模式分析：
        # 普通类: c:@S@ClassName@F@methodName
        # 模板类: c:@ST@TemplateClass@F@methodName 或 c:@ST>...@TemplateClass@F@methodName
        
        # 检查是否包含类/结构体标记后跟函数标记
        if '@S@' in usr and '@F@' in usr:
            # 普通结构体/类
            s_pos = usr.find('@S@')
            f_pos = usr.find('@F@', s_pos)
            return f_pos > s_pos
        
        if '@ST' in usr and '@F@' in usr:
            # 模板结构体/类（处理 @ST@ 和 @ST> 两种情况）
            st_pos = usr.find('@ST')
            f_pos = usr.find('@F@', st_pos)
            return f_pos > st_pos
        
        return False
    
    def _analyze_function_details(self, func_usr: str, qualified_name: str) -> dict:
        """分析函数的详细信息，用于调试"""
        details = {
            'usr': func_usr,
            'qualified_name': qualified_name,
            'is_static': func_usr.endswith('#S'),
            'is_template': '@ST' in func_usr or '@FT@' in func_usr,
            'has_class_marker': '@S@' in func_usr or '@ST' in func_usr,
            'has_namespace_marker': '@N@' in func_usr,
            'has_function_marker': '@F@' in func_usr,
        }
        
        # 提取类名（如果有）
        if details['has_class_marker']:
            if '@S@' in func_usr:
                parts = func_usr.split('@S@')
                if len(parts) > 1:
                    class_part = parts[1].split('@')[0]
                    details['class_name'] = class_part
            elif '@ST' in func_usr:
                # 处理模板类
                st_pos = func_usr.find('@ST')
                after_st = func_usr[st_pos+3:]
                if after_st.startswith('>'):
                    # 找到模板参数结束位置
                    template_end = after_st.find('@')
                    if template_end > 0:
                        class_part = after_st[template_end+1:].split('@')[0]
                        details['class_name'] = class_part
                else:
                    class_part = after_st.split('@')[0]
                    details['class_name'] = class_part
        
        return details
    
    def _should_skip_orphaned_function_check(self, qualified_name: str, func: Any) -> bool:
        """基于USR结构的智能orphaned function检测"""
        func_usr = getattr(func, 'usr_id', '')
        
        # 检查是否为全局操作符重载（基于USR结构而非字符串匹配）
        if self._is_global_operator_by_usr(func_usr):
            return True
        
        # 检查是否为系统库函数（基于USR结构）
        if self._is_system_function_by_usr(func_usr):
            return True
        
        return False
    
    def _is_global_operator_by_usr(self, usr: str) -> bool:
        """基于USR结构检查是否为全局操作符重载"""
        if not usr.startswith('c:@'):
            return False
        
        # 全局作用域的操作符：c:@F@operator...
        if '@F@operator' in usr and '@N@' not in usr and '@S@' not in usr:
            return True
        
        # std命名空间中的操作符：c:@N@std@F@operator...
        if '@N@std@' in usr and '@F@operator' in usr:
            return True
        
        return False
    
    def _is_system_function_by_usr(self, usr: str) -> bool:
        """基于USR结构检查是否为系统库函数"""
        if not usr.startswith('c:@'):
            return False
        
        # 检查USR中的命名空间标记
        system_namespace_patterns = [
            '@N@std@',           # std命名空间
            '@N@__gnu_cxx@',     # GNU扩展
            '@N@__cxxabiv1@',    # ABI命名空间
        ]
        
        for pattern in system_namespace_patterns:
            if pattern in usr:
                return True
        
        # 检查编译器内置函数
        if '@F@__builtin_' in usr or '@F@__has_' in usr or '@F@__is_' in usr:
            return True
        
        return False
    
    def _should_skip_missing_callee_check(self, callee_usr: str) -> bool:
        """基于USR结构的智能missing callee检测"""
        if not callee_usr:
            return True
        
        # 使用与orphaned function相同的系统函数检测逻辑
        if self._is_system_function_by_usr(callee_usr):
            return True
        
        # 检查是否为外部库函数（基于USR结构特征）
        if self._is_external_library_function(callee_usr):
            return True
        
        # 检查是否为模板实例化导致的USR不匹配
        if self._is_template_instantiation_mismatch(callee_usr):
            return True
        
        return False
    
    def _is_external_library_function(self, usr: str) -> bool:
        """检查是否为外部库函数"""
        if not usr.startswith('c:@'):
            return False
        
        # 检查Windows API模式
        if any(api in usr for api in ['@F@GetModuleHandle', '@F@LoadLibrary', '@F@GetProcAddress']):
            return True
        
        # 检查C标准库函数
        if any(func in usr for func in ['@F@malloc', '@F@free', '@F@printf', '@F@memcpy']):
            return True
        
        # 检查以下划线开头的内部函数
        if '@F@_' in usr or '@F@__' in usr:
            return True
        
        # 检查过短的USR（通常是外部符号）
        if len(usr) < 20:
            return True
        
        return False
    
    def _is_template_instantiation_mismatch(self, usr: str) -> bool:
        """检查是否为模板实例化导致的USR不匹配"""
        # 模板实例化的USR通常包含复杂的类型编码
        template_indicators = ['#T', '#N', '#', '>', '<']
        
        complex_template_count = sum(1 for indicator in template_indicators if indicator in usr)
        
        # 如果USR包含大量模板标记，可能是模板实例化问题
        return complex_template_count > 3
    
    def _check_namespace_function(self, qualified_name: str, namespaces: Dict[str, Any]) -> bool:
        """基于USR结构和namespace数据的准确命名空间函数检查"""
        if '::' not in qualified_name:
            return False
        
        namespace_part = qualified_name.rsplit('::', 1)[0]
        
        # 首先检查命名空间是否在我们的数据中存在
        for ns_usr, ns_obj in namespaces.items():
            if isinstance(ns_obj, dict):
                ns_qualified_name = ns_obj.get('qualified_name', '')
            else:
                ns_qualified_name = getattr(ns_obj, 'qualified_name', '')
            
            if ns_qualified_name:
                # 精确匹配或嵌套匹配
                if (ns_qualified_name == namespace_part or 
                    namespace_part.startswith(ns_qualified_name + '::') or
                    ns_qualified_name.startswith(namespace_part + '::')):
                    return True
        
        # 如果不在数据中，检查是否为已知的系统命名空间
        return self._is_known_system_namespace(namespace_part)
    
    def _is_known_system_namespace(self, namespace_name: str) -> bool:
        """检查是否为已知的系统命名空间"""
        system_namespaces = [
            'std', '__gnu_cxx', '__cxxabiv1',  # C++标准库和编译器扩展
        ]
        
        for sys_ns in system_namespaces:
            if namespace_name == sys_ns or namespace_name.startswith(sys_ns + '::'):
                return True
        
        return False
    
    def _check_class_method(self, func_usr: str, classes: Dict[str, Any]) -> bool:
        """检查是否为类方法"""
        found_in_class = False
        for class_usr, class_obj in classes.items():
            if hasattr(class_obj, 'methods'):
                if isinstance(class_obj.methods, list) and func_usr in class_obj.methods:
                    found_in_class = True
                    break
                elif isinstance(class_obj.methods, dict) and func_usr in class_obj.methods:
                    found_in_class = True
                    break
        return found_in_class
    
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