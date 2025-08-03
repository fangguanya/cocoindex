"""
C++ Code Analyzer Package

A comprehensive C++ code analysis toolkit for C++ projects.
Extracts entities, relationships, and exports to structured JSON format.
"""

from .cpp_analyzer import CppAnalyzer
from .file_scanner import FileScanner
from .clang_parser import ClangParser
from .entity_extractor import EntityExtractor
from .json_exporter import JsonExporter

__version__ = "1.0.0"
__author__ = "C++ Analysis Team"

__all__ = [
    "CppAnalyzer",
    "FileScanner", 
    "ClangParser",
    "EntityExtractor",
    "JsonExporter"
] 