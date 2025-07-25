"""
C++ Code Analyzer Data Structures

This module defines the data structures used by the C++ code analyzer
to represent various code entities and their relationships.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union, Any
from enum import Enum
import json
from datetime import datetime


class AccessSpecifier(Enum):
    """C++ access specifiers"""
    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"


class StorageClass(Enum):
    """C++ storage classes"""
    NONE = "none"
    STATIC = "static"
    EXTERN = "extern"
    THREAD_LOCAL = "thread_local"
    MUTABLE = "mutable"


class TemplateParameterType(Enum):
    """Template parameter types"""
    TYPENAME = "typename"
    CLASS = "class"
    NON_TYPE = "non_type"
    TEMPLATE = "template"


class CallType(Enum):
    """Types of function/method calls"""
    FUNCTION_CALL = "function_call"
    METHOD_CALL = "method_call"
    CONSTRUCTOR_CALL = "constructor_call"
    DESTRUCTOR_CALL = "destructor_call"
    OPERATOR_CALL = "operator_call"
    TEMPLATE_INSTANTIATION = "template_instantiation"
    VIRTUAL_CALL = "virtual_call"


@dataclass
class SourceLocation:
    """Represents a location in source code"""
    file: str
    line: int
    column: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column
        }


@dataclass
class TemplateParameter:
    """Represents a template parameter"""
    name: str
    type: TemplateParameterType
    default_value: Optional[str] = None
    is_variadic: bool = False
    constraints: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type.value,
            "default_value": self.default_value,
            "is_variadic": self.is_variadic,
            "constraints": self.constraints
        }


@dataclass
class TemplateSpecialization:
    """Represents a template specialization"""
    specialization_args: List[str]
    file: str
    line: int
    is_partial: bool = False
    additional_methods: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "specialization_args": self.specialization_args,
            "file": self.file,
            "line": self.line,
            "is_partial": self.is_partial,
            "additional_methods": self.additional_methods
        }


@dataclass
class TemplateInstantiation:
    """Represents a template instantiation"""
    instantiation_args: List[str]
    usage_file: str
    usage_line: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instantiation_args": self.instantiation_args,
            "usage_file": self.usage_file,
            "usage_line": self.usage_line
        }


@dataclass
class VirtualTableInfo:
    """Information about virtual table"""
    has_vtable: bool
    vtable_size: int = 0
    virtual_methods: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_vtable": self.has_vtable,
            "vtable_size": self.vtable_size,
            "virtual_methods": self.virtual_methods
        }


@dataclass
class ConstructorInfo:
    """Information about constructors/destructor"""
    is_defined: bool
    is_deleted: bool = False
    is_defaulted: bool = False
    access: AccessSpecifier = AccessSpecifier.PUBLIC

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_defined": self.is_defined,
            "is_deleted": self.is_deleted,
            "is_defaulted": self.is_defaulted,
            "access": self.access.value
        }


@dataclass
class InheritanceInfo:
    """Information about class inheritance"""
    base_class: str
    access_specifier: AccessSpecifier
    is_virtual: bool = False
    is_template_base: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_class": self.base_class,
            "access_specifier": self.access_specifier.value,
            "is_virtual": self.is_virtual,
            "is_template_base": self.is_template_base
        }


@dataclass
class CppFunctionExtensions:
    """C++ specific function/method extensions"""
    qualified_name: str
    namespace: str = ""
    is_template: bool = False
    is_template_specialization: bool = False
    is_virtual: bool = False
    is_pure_virtual: bool = False
    is_override: bool = False
    is_final: bool = False
    is_static: bool = False
    is_const: bool = False
    is_noexcept: bool = False
    is_inline: bool = False
    is_constexpr: bool = False
    is_operator_overload: bool = False
    is_constructor: bool = False
    is_destructor: bool = False
    is_copy_constructor: bool = False
    is_move_constructor: bool = False
    access_specifier: AccessSpecifier = AccessSpecifier.PUBLIC
    storage_class: StorageClass = StorageClass.NONE
    calling_convention: str = "default"
    return_type: str = "void"
    parameter_types: Dict[str, str] = field(default_factory=dict)
    template_parameters: List[TemplateParameter] = field(default_factory=list)
    exception_specification: str = ""
    attributes: List[str] = field(default_factory=list)
    mangled_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "qualified_name": self.qualified_name,
            "namespace": self.namespace,
            "is_template": self.is_template,
            "is_template_specialization": self.is_template_specialization,
            "is_virtual": self.is_virtual,
            "is_pure_virtual": self.is_pure_virtual,
            "is_override": self.is_override,
            "is_final": self.is_final,
            "is_static": self.is_static,
            "is_const": self.is_const,
            "is_noexcept": self.is_noexcept,
            "is_inline": self.is_inline,
            "is_constexpr": self.is_constexpr,
            "is_operator_overload": self.is_operator_overload,
            "is_constructor": self.is_constructor,
            "is_destructor": self.is_destructor,
            "is_copy_constructor": self.is_copy_constructor,
            "is_move_constructor": self.is_move_constructor,
            "access_specifier": self.access_specifier.value,
            "storage_class": self.storage_class.value,
            "calling_convention": self.calling_convention,
            "return_type": self.return_type,
            "parameter_types": self.parameter_types,
            "template_parameters": [tp.to_dict() for tp in self.template_parameters],
            "exception_specification": self.exception_specification,
            "attributes": self.attributes,
            "mangled_name": self.mangled_name
        }


@dataclass
class CppClassExtensions:
    """C++ specific class/struct extensions"""
    qualified_name: str
    namespace: str = ""
    type: str = "class"  # class, struct, union, enum
    is_template: bool = False
    is_template_specialization: bool = False
    is_abstract: bool = False
    is_final: bool = False
    is_pod: bool = False
    is_trivial: bool = False
    is_standard_layout: bool = False
    is_polymorphic: bool = False
    inheritance_list: List[InheritanceInfo] = field(default_factory=list)
    template_parameters: List[TemplateParameter] = field(default_factory=list)
    template_specialization_args: List[str] = field(default_factory=list)
    nested_types: List[str] = field(default_factory=list)
    friend_declarations: List[str] = field(default_factory=list)
    size_in_bytes: int = 0
    alignment: int = 0
    virtual_table_info: Optional[VirtualTableInfo] = None
    constructors: Dict[str, ConstructorInfo] = field(default_factory=dict)
    destructor: Optional[ConstructorInfo] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "qualified_name": self.qualified_name,
            "namespace": self.namespace,
            "type": self.type,
            "is_template": self.is_template,
            "is_template_specialization": self.is_template_specialization,
            "is_abstract": self.is_abstract,
            "is_final": self.is_final,
            "is_pod": self.is_pod,
            "is_trivial": self.is_trivial,
            "is_standard_layout": self.is_standard_layout,
            "is_polymorphic": self.is_polymorphic,
            "inheritance_list": [ih.to_dict() for ih in self.inheritance_list],
            "template_parameters": [tp.to_dict() for tp in self.template_parameters],
            "template_specialization_args": self.template_specialization_args,
            "nested_types": self.nested_types,
            "friend_declarations": self.friend_declarations,
            "size_in_bytes": self.size_in_bytes,
            "alignment": self.alignment,
            "constructors": {k: v.to_dict() for k, v in self.constructors.items()}
        }
        
        if self.virtual_table_info:
            result["virtual_table_info"] = self.virtual_table_info.to_dict()
        
        if self.destructor:
            result["destructor"] = self.destructor.to_dict()
        
        return result


@dataclass
class CppCallInfo:
    """C++ specific call information"""
    call_type: CallType
    is_virtual_call: bool = False
    is_template_instantiation: bool = False
    template_args: List[str] = field(default_factory=list)
    is_operator_call: bool = False
    operator_type: str = ""
    is_constructor_call: bool = False
    is_static_call: bool = False
    calling_object: str = ""
    argument_types: List[str] = field(default_factory=list)
    resolved_overload: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_type": self.call_type.value,
            "is_virtual_call": self.is_virtual_call,
            "is_template_instantiation": self.is_template_instantiation,
            "template_args": self.template_args,
            "is_operator_call": self.is_operator_call,
            "operator_type": self.operator_type,
            "is_constructor_call": self.is_constructor_call,
            "is_static_call": self.is_static_call,
            "calling_object": self.calling_object,
            "argument_types": self.argument_types,
            "resolved_overload": self.resolved_overload
        }


@dataclass
class FunctionInfo:
    """Complete function information"""
    name: str
    qualified_name: str
    file: str
    start_line: int
    end_line: int
    is_local: bool = False
    parameters: List[str] = field(default_factory=list)
    calls_to: List[str] = field(default_factory=list)
    called_by: List[str] = field(default_factory=list)
    cpp_extensions: Optional[CppFunctionExtensions] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "is_local": self.is_local,
            "parameters": self.parameters,
            "calls_to": self.calls_to,
            "called_by": self.called_by
        }
        
        if self.cpp_extensions:
            result["cpp_extensions"] = self.cpp_extensions.to_dict()
        
        return result


@dataclass
class ClassInfo:
    """Complete class information"""
    name: str
    file: str
    line: int
    parent_classes: List[str] = field(default_factory=list)
    is_abstract: bool = False
    is_mixin: bool = False
    documentation: str = ""
    methods: Dict[str, FunctionInfo] = field(default_factory=dict)
    fields: List[Dict[str, Any]] = field(default_factory=list)
    cpp_oop_extensions: Optional[CppClassExtensions] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "name": self.name,
            "file": self.file,
            "line": self.line,
            "parent_classes": self.parent_classes,
            "is_abstract": self.is_abstract,
            "is_mixin": self.is_mixin,
            "documentation": self.documentation,
            "methods": {k: v.to_dict() for k, v in self.methods.items()},
            "fields": self.fields
        }
        
        if self.cpp_oop_extensions:
            result["cpp_oop_extensions"] = self.cpp_oop_extensions.to_dict()
        
        return result


@dataclass
class CallRelation:
    """Represents a function call relationship"""
    from_function: str
    to_function: str
    line: int
    column: int = 0
    call_type: str = "direct"
    resolved_path: str = ""
    cpp_call_info: Optional[CppCallInfo] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "from": self.from_function,
            "to": self.to_function,
            "line": self.line,
            "column": self.column,
            "type": self.call_type,
            "resolved_path": self.resolved_path
        }
        
        if self.cpp_call_info:
            result["cpp_call_info"] = self.cpp_call_info.to_dict()
        
        return result


@dataclass
class NamespaceInfo:
    """Namespace information"""
    name: str
    qualified_name: str
    file: str
    line: int
    is_anonymous: bool = False
    is_inline: bool = False
    parent_namespace: str = "global"
    nested_namespaces: List[str] = field(default_factory=list)
    classes: List[str] = field(default_factory=list)
    functions: List[str] = field(default_factory=list)
    variables: List[str] = field(default_factory=list)
    aliases: Dict[str, str] = field(default_factory=dict)
    using_declarations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file": self.file,
            "line": self.line,
            "is_anonymous": self.is_anonymous,
            "is_inline": self.is_inline,
            "parent_namespace": self.parent_namespace,
            "nested_namespaces": self.nested_namespaces,
            "classes": self.classes,
            "functions": self.functions,
            "variables": self.variables,
            "aliases": self.aliases,
            "using_declarations": self.using_declarations
        }


@dataclass
class TemplateInfo:
    """Template information"""
    name: str
    qualified_name: str
    file: str
    line: int
    type: str  # class_template, function_template, variable_template, alias_template
    template_parameters: List[TemplateParameter] = field(default_factory=list)
    specializations: List[TemplateSpecialization] = field(default_factory=list)
    instantiations: List[TemplateInstantiation] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file": self.file,
            "line": self.line,
            "type": self.type,
            "template_parameters": [tp.to_dict() for tp in self.template_parameters],
            "specializations": [sp.to_dict() for sp in self.specializations],
            "instantiations": [inst.to_dict() for inst in self.instantiations]
        }


@dataclass
class MacroInfo:
    """Macro information"""
    name: str
    definition: str
    file: str
    line: int
    is_function_like: bool = False
    parameters: List[str] = field(default_factory=list)
    usages: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "definition": self.definition,
            "file": self.file,
            "line": self.line,
            "is_function_like": self.is_function_like,
            "parameters": self.parameters,
            "usages": self.usages
        }


@dataclass
class PreprocessorInfo:
    """Preprocessor analysis information"""
    macros: Dict[str, MacroInfo] = field(default_factory=dict)
    includes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    conditional_compilation: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "macros": {k: v.to_dict() for k, v in self.macros.items()},
            "includes": self.includes,
            "conditional_compilation": self.conditional_compilation
        }


@dataclass
class CppStatistics:
    """C++ specific statistics"""
    namespaces: int = 0
    templates: int = 0
    template_specializations: int = 0
    template_instantiations: int = 0
    virtual_functions: int = 0
    pure_virtual_functions: int = 0
    operator_overloads: int = 0
    friend_declarations: int = 0
    macros: int = 0
    system_includes: int = 0
    user_includes: int = 0
    avg_inheritance_depth: float = 0.0
    max_template_depth: int = 0
    circular_dependencies: int = 0
    language_features: Dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "namespaces": self.namespaces,
            "templates": self.templates,
            "template_specializations": self.template_specializations,
            "template_instantiations": self.template_instantiations,
            "virtual_functions": self.virtual_functions,
            "pure_virtual_functions": self.pure_virtual_functions,
            "operator_overloads": self.operator_overloads,
            "friend_declarations": self.friend_declarations,
            "macros": self.macros,
            "system_includes": self.system_includes,
            "user_includes": self.user_includes,
            "avg_inheritance_depth": self.avg_inheritance_depth,
            "max_template_depth": self.max_template_depth,
            "circular_dependencies": self.circular_dependencies,
            "language_features": self.language_features
        }


@dataclass
class CppAnalysisResult:
    """Complete C++ analysis result"""
    version: str = "2.2"
    language: str = "cpp"
    timestamp: str = ""
    project_call_graph: Dict[str, Any] = field(default_factory=dict)
    oop_analysis: Dict[str, Any] = field(default_factory=dict)
    cpp_analysis: Dict[str, Any] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "language": self.language,
            "timestamp": self.timestamp,
            "project_call_graph": self.project_call_graph,
            "oop_analysis": self.oop_analysis,
            "cpp_analysis": self.cpp_analysis,
            "summary": self.summary
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save_to_file(self, filename: str, indent: int = 2):
        """Save analysis result to JSON file"""
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CppAnalysisResult':
        """Create analysis result from dictionary"""
        return cls(
            version=data.get("version", "2.2"),
            language=data.get("language", "cpp"),
            timestamp=data.get("timestamp", ""),
            project_call_graph=data.get("project_call_graph", {}),
            oop_analysis=data.get("oop_analysis", {}),
            cpp_analysis=data.get("cpp_analysis", {}),
            summary=data.get("summary", {})
        )

    @classmethod
    def from_json_file(cls, filename: str) -> 'CppAnalysisResult':
        """Load analysis result from JSON file"""
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data) 