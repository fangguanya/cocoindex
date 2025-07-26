"""
Tree-sitter C++ 分析器模块

提供基于tree-sitter的C++代码分析功能，替代基于clang的实现。
支持所有json_format.md规格，并增加函数体文本提取功能。

主要组件：
- EntityExtractor: 实体提取器
- CppAnalyzer: 主分析器
- CallRelationshipAnalyzer: 调用关系分析器
- JsonExporter: JSON导出器
- NodeRepository: 全局节点存储库
"""

from .entity_extractor import EntityExtractor  
from .cpp_analyzer import CppAnalyzer
from .call_relationship_analyzer import CallRelationshipAnalyzer
from .json_exporter import JsonExporter
from .data_structures import NodeRepository, Function, Class, Namespace
from .logger import Logger

__all__ = [
    'EntityExtractor',
    'CppAnalyzer', 
    'CallRelationshipAnalyzer',
    'JsonExporter',
    'NodeRepository',
    'Function',
    'Class', 
    'Namespace',
    'Logger'
]

__version__ = '2.4.0' 