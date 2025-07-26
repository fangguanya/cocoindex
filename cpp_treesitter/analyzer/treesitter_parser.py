"""
Tree-sitter C++ Parser Module

基于tree-sitter的C++解析器，替代clang解析器。
提供文件解析、AST生成和基础符号表功能。
"""

import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field

try:
    import tree_sitter
    import tree_sitter_cpp
except ImportError:
    raise ImportError("需要安装 tree-sitter 和 tree-sitter-cpp: pip install tree-sitter tree-sitter-cpp")

from rich.console import Console
from rich.progress import Progress, TaskID

@dataclass
class TreeSitterDiagnostic:
    """Tree-sitter诊断信息"""
    severity: str  # 'error', 'warning', 'info'
    message: str
    file_path: str
    line: int
    column: int
    node_type: str = ""

@dataclass 
class ParsedTreeSitterFile:
    """Tree-sitter解析后的文件信息"""
    file_path: str
    tree: Optional[tree_sitter.Tree]
    source_code: str
    success: bool
    diagnostics: List[TreeSitterDiagnostic]
    parse_time: float
    node_count: int = 0

class TreeSitterParser:
    """基于tree-sitter的C++解析器"""
    
    def __init__(self, console: Optional[Console] = None):
        """初始化解析器"""
        self.console = console or Console()
        self.language = None
        self.parser = None
        self._initialize_parser()
        
        # 统计信息
        self.total_nodes_parsed = 0
        self.total_files_parsed = 0
    
    def _initialize_parser(self):
        """初始化tree-sitter解析器"""
        try:
            # 获取C++语言
            self.language = tree_sitter.Language(tree_sitter_cpp.language())
            
            # 创建解析器
            self.parser = tree_sitter.Parser(self.language)
            
            if self.console:
                self.console.print("✓ Tree-sitter C++解析器初始化成功", style="green")
        except Exception as e:
            if self.console:
                self.console.print(f"✗ Tree-sitter解析器初始化失败: {e}", style="red")
            raise
    
    def parse_files(self, file_paths: List[str], progress: Optional[Progress] = None, 
                   task: Optional[TaskID] = None) -> List[ParsedTreeSitterFile]:
        """解析多个文件"""
        parsed_files = []
        
        for i, file_path in enumerate(file_paths):
            try:
                parsed_file = self.parse_file(file_path)
                parsed_files.append(parsed_file)
                
                # 更新统计
                if parsed_file.success:
                    self.total_nodes_parsed += parsed_file.node_count
                    self.total_files_parsed += 1
                
                # 更新进度
                if progress and task:
                    progress.update(task, advance=1)
                    
            except Exception as e:
                # 创建失败的解析结果
                diagnostic = TreeSitterDiagnostic(
                    severity="error",
                    message=f"解析文件失败: {str(e)}",
                    file_path=file_path,
                    line=0,
                    column=0
                )
                
                parsed_file = ParsedTreeSitterFile(
                    file_path=file_path,
                    tree=None,
                    source_code="",
                    success=False,
                    diagnostics=[diagnostic],
                    parse_time=0.0
                )
                parsed_files.append(parsed_file)
        
        return parsed_files
    
    def parse_file(self, file_path: str) -> ParsedTreeSitterFile:
        """解析单个文件"""
        start_time = time.time()
        
        try:
            # 读取源代码
            source_code = self._read_source_file(file_path)
            if source_code is None:
                raise Exception(f"无法读取文件: {file_path}")
            
            # 使用tree-sitter解析
            tree = self.parser.parse(source_code.encode('utf-8'))
            
            # 计算节点数量
            node_count = self._count_nodes(tree.root_node)
            
            # 检查解析错误
            diagnostics = self._check_parse_errors(tree, file_path)
            
            parse_time = time.time() - start_time
            
            return ParsedTreeSitterFile(
                file_path=file_path,
                tree=tree,
                source_code=source_code,
                success=len([d for d in diagnostics if d.severity == "error"]) == 0,
                diagnostics=diagnostics,
                parse_time=parse_time,
                node_count=node_count
            )
            
        except Exception as e:
            parse_time = time.time() - start_time
            diagnostic = TreeSitterDiagnostic(
                severity="error",
                message=str(e),
                file_path=file_path,
                line=0,
                column=0
            )
            
            return ParsedTreeSitterFile(
                file_path=file_path,
                tree=None,
                source_code="",
                success=False,
                diagnostics=[diagnostic],
                parse_time=parse_time
            )
    
    def _read_source_file(self, file_path: str) -> Optional[str]:
        """读取源代码文件"""
        try:
            # 尝试多种编码
            encodings = ['utf-8', 'utf-16', 'latin1', 'cp1252']
            
            for encoding in encodings:
                try:
                    with open(file_path, 'r', encoding=encoding, errors='replace') as f:
                        return f.read()
                except UnicodeDecodeError:
                    continue
            
            # 如果所有编码都失败，使用二进制模式并忽略错误
            with open(file_path, 'rb') as f:
                content = f.read()
                return content.decode('utf-8', errors='ignore')
                
        except Exception as e:
            if self.console:
                self.console.print(f"[red]读取文件失败 {file_path}: {e}[/red]")
            return None
    
    def _count_nodes(self, node: tree_sitter.Node) -> int:
        """递归计算AST节点数量"""
        count = 1  # 当前节点
        for child in node.children:
            count += self._count_nodes(child)
        return count
    
    def _check_parse_errors(self, tree: tree_sitter.Tree, file_path: str) -> List[TreeSitterDiagnostic]:
        """检查解析错误"""
        diagnostics = []
        
        # 检查是否有错误节点
        self._find_error_nodes(tree.root_node, file_path, diagnostics)
        
        return diagnostics
    
    def _find_error_nodes(self, node: tree_sitter.Node, file_path: str, 
                         diagnostics: List[TreeSitterDiagnostic]):
        """查找错误节点"""
        if node.type == "ERROR":
            diagnostic = TreeSitterDiagnostic(
                severity="error",
                message=f"语法错误: 无法解析的代码段",
                file_path=file_path,
                line=node.start_point[0] + 1,  # tree-sitter使用0基索引
                column=node.start_point[1] + 1,
                node_type=node.type
            )
            diagnostics.append(diagnostic)
        
        # 递归检查子节点
        for child in node.children:
            self._find_error_nodes(child, file_path, diagnostics)
    
    def get_node_text(self, node: tree_sitter.Node, source_code: str) -> str:
        """获取节点对应的源代码文本"""
        try:
            source_bytes = source_code.encode('utf-8')
            return source_bytes[node.start_byte:node.end_byte].decode('utf-8')
        except Exception:
            return ""
    
    def find_nodes_by_type(self, root_node: tree_sitter.Node, node_types: Set[str]) -> List[tree_sitter.Node]:
        """查找指定类型的所有节点"""
        found_nodes = []
        
        def visit_node(node: tree_sitter.Node):
            if node.type in node_types:
                found_nodes.append(node)
            
            for child in node.children:
                visit_node(child)
        
        visit_node(root_node)
        return found_nodes
    
    def get_function_body_text(self, function_node: tree_sitter.Node, source_code: str) -> str:
        """获取函数体文本 - 新增功能"""
        # 查找函数体节点
        compound_statement = None
        for child in function_node.children:
            if child.type == "compound_statement":
                compound_statement = child
                break
        
        if compound_statement:
            return self.get_node_text(compound_statement, source_code)
        else:
            return ""
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取解析统计信息"""
        return {
            "total_files_parsed": self.total_files_parsed,
            "total_nodes_parsed": self.total_nodes_parsed,
            "avg_nodes_per_file": self.total_nodes_parsed / max(1, self.total_files_parsed)
        } 