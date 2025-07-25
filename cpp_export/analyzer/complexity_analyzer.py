"""
Complexity Analyzer Module

Analyzes code complexity metrics including:
- Lines of Code (LOC)
- Cyclomatic Complexity
- Function/Class size metrics
- Nesting depth analysis
"""

import re
from pathlib import Path
from typing import Dict, Any, List
import clang.cindex as clang

class ComplexityAnalyzer:
    """代码复杂度分析器"""
    
    def __init__(self):
        self.metrics = {}
    
    def analyze_parsed_files(self, parsed_files: List[Any]) -> Dict[str, Any]:
        """分析解析后的文件复杂度"""
        total_metrics = {
            "total_lines_of_code": 0,
            "executable_lines": 0,
            "comment_lines": 0,
            "blank_lines": 0,
            "max_function_complexity": 0,
            "max_class_complexity": 0,
            "average_nesting_depth": 0.0,
            "max_nesting_depth": 0,
            "average_function_length": 0,
            "average_class_size": 0,
            "cyclomatic_complexity": 0
        }
        
        function_complexities = []
        function_lengths = []
        class_sizes = []
        nesting_depths = []
        
        for parsed_file in parsed_files:
            if parsed_file.translation_unit:
                # 分析文件的LOC
                file_metrics = self._analyze_file_loc(parsed_file.file_path)
                total_metrics["total_lines_of_code"] += file_metrics["total_lines"]
                total_metrics["comment_lines"] += file_metrics["comment_lines"]
                total_metrics["blank_lines"] += file_metrics["blank_lines"]
                total_metrics["executable_lines"] += file_metrics["executable_lines"]
                
                # 分析AST复杂度
                ast_metrics = self._analyze_ast_complexity(parsed_file.translation_unit.cursor)
                function_complexities.extend(ast_metrics["function_complexities"])
                function_lengths.extend(ast_metrics["function_lengths"])
                class_sizes.extend(ast_metrics["class_sizes"])
                nesting_depths.extend(ast_metrics["nesting_depths"])
        
        # 计算平均值和最大值
        if function_complexities:
            total_metrics["max_function_complexity"] = max(function_complexities)
            total_metrics["cyclomatic_complexity"] = sum(function_complexities)
        
        if function_lengths:
            total_metrics["average_function_length"] = sum(function_lengths) / len(function_lengths)
        
        if class_sizes:
            total_metrics["average_class_size"] = sum(class_sizes) / len(class_sizes)
            total_metrics["max_class_complexity"] = max(class_sizes)
        
        if nesting_depths:
            total_metrics["average_nesting_depth"] = sum(nesting_depths) / len(nesting_depths)
            total_metrics["max_nesting_depth"] = max(nesting_depths)
        
        return total_metrics
    
    def _analyze_file_loc(self, file_path: str) -> Dict[str, int]:
        """分析文件的代码行数"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except Exception:
            return {"total_lines": 0, "comment_lines": 0, "blank_lines": 0, "executable_lines": 0}
        
        total_lines = len(lines)
        comment_lines = 0
        blank_lines = 0
        
        in_multiline_comment = False
        
        for line in lines:
            line = line.strip()
            
            # 空行
            if not line:
                blank_lines += 1
                continue
            
            # 检查多行注释
            if '/*' in line and '*/' not in line:
                in_multiline_comment = True
                comment_lines += 1
                continue
            elif '*/' in line:
                in_multiline_comment = False
                comment_lines += 1
                continue
            elif in_multiline_comment:
                comment_lines += 1
                continue
            
            # 单行注释
            if line.startswith('//') or line.startswith('#'):
                comment_lines += 1
                continue
        
        executable_lines = total_lines - comment_lines - blank_lines
        
        return {
            "total_lines": total_lines,
            "comment_lines": comment_lines,
            "blank_lines": blank_lines,
            "executable_lines": executable_lines
        }
    
    def _analyze_ast_complexity(self, cursor) -> Dict[str, List[int]]:
        """分析AST复杂度"""
        function_complexities = []
        function_lengths = []
        class_sizes = []
        nesting_depths = []
        
        self._traverse_ast(cursor, function_complexities, function_lengths, 
                          class_sizes, nesting_depths, 0)
        
        return {
            "function_complexities": function_complexities,
            "function_lengths": function_lengths,
            "class_sizes": class_sizes,
            "nesting_depths": nesting_depths
        }
    
    def _traverse_ast(self, cursor, function_complexities, function_lengths, 
                     class_sizes, nesting_depths, current_depth):
        """递归遍历AST"""
        current_depth += 1
        nesting_depths.append(current_depth)
        
        # 分析函数复杂度
        if cursor.kind in [clang.CursorKind.FUNCTION_DECL, 
                          clang.CursorKind.CXX_METHOD,
                          clang.CursorKind.CONSTRUCTOR,
                          clang.CursorKind.DESTRUCTOR]:
            complexity = self._calculate_cyclomatic_complexity(cursor)
            function_complexities.append(complexity)
            
            length = self._calculate_function_length(cursor)
            function_lengths.append(length)
        
        # 分析类复杂度
        elif cursor.kind in [clang.CursorKind.CLASS_DECL,
                           clang.CursorKind.STRUCT_DECL]:
            size = self._calculate_class_size(cursor)
            class_sizes.append(size)
        
        # 递归处理子节点
        for child in cursor.get_children():
            self._traverse_ast(child, function_complexities, function_lengths,
                             class_sizes, nesting_depths, current_depth)
    
    def _calculate_cyclomatic_complexity(self, cursor) -> int:
        """计算函数的循环复杂度"""
        complexity = 1  # 基础复杂度
        
        # 遍历函数体，统计决策点
        for child in cursor.get_children():
            complexity += self._count_decision_points(child)
        
        return complexity
    
    def _count_decision_points(self, cursor) -> int:
        """计算决策点数量"""
        decision_points = 0
        
        # 条件语句
        if cursor.kind in [clang.CursorKind.IF_STMT,
                          clang.CursorKind.WHILE_STMT,
                          clang.CursorKind.FOR_STMT,
                          clang.CursorKind.DO_STMT,
                          clang.CursorKind.SWITCH_STMT,
                          clang.CursorKind.CASE_STMT,
                          clang.CursorKind.CONDITIONAL_OPERATOR]:
            decision_points += 1
        
        # 逻辑操作符
        elif cursor.kind == clang.CursorKind.BINARY_OPERATOR:
            # 检查是否为 && 或 ||
            tokens = list(cursor.get_tokens())
            for token in tokens:
                if token.spelling in ['&&', '||']:
                    decision_points += 1
                    break
        
        # 递归计算子节点
        for child in cursor.get_children():
            decision_points += self._count_decision_points(child)
        
        return decision_points
    
    def _calculate_function_length(self, cursor) -> int:
        """计算函数长度（行数）"""
        if not cursor.extent:
            return 0
        
        start_line = cursor.extent.start.line
        end_line = cursor.extent.end.line
        
        return max(1, end_line - start_line + 1)
    
    def _calculate_class_size(self, cursor) -> int:
        """计算类的大小（成员数量）"""
        member_count = 0
        
        for child in cursor.get_children():
            if child.kind in [clang.CursorKind.CXX_METHOD,
                             clang.CursorKind.CONSTRUCTOR,
                             clang.CursorKind.DESTRUCTOR,
                             clang.CursorKind.FIELD_DECL,
                             clang.CursorKind.VAR_DECL]:
                member_count += 1
        
        return member_count 