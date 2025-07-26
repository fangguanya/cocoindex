"""
模板分析器

该模块实现C++模板分析功能，包括：
1. 模板参数解析
2. 模板特化检测
3. 模板实例化追踪
4. 基础的C++20 concepts支持

主要用于增强代码分析的完整性。
"""

from tree_sitter import Node
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field
import re

from .logger import Logger
from .data_structures import NodeRepository, Function, Class


@dataclass
class TemplateParameter:
    """模板参数信息"""
    name: str
    type: str  # "type", "non_type", "template"
    default_value: Optional[str] = None
    constraints: List[str] = field(default_factory=list)
    is_variadic: bool = False


@dataclass
class TemplateInfo:
    """模板信息"""
    name: str
    qualified_name: str
    template_type: str  # "class", "function", "variable", "alias"
    parameters: List[TemplateParameter] = field(default_factory=list)
    specializations: List[str] = field(default_factory=list)  # USR列表
    instantiations: List[str] = field(default_factory=list)  # USR列表
    concepts_constraints: List[str] = field(default_factory=list)
    file_path: str = ""
    start_line: int = 0


class TemplateAnalyzer:
    """模板分析器"""
    
    def __init__(self, repo: NodeRepository):
        self.repo = repo
        self.logger = Logger.get_logger()
        self.templates: Dict[str, TemplateInfo] = {}
        self.file_content: bytes = b""
    
    def set_file_content(self, content: bytes):
        """设置当前处理的文件内容"""
        self.file_content = content
    
    def analyze_template_declarations(self, root_node: Node, file_path: str):
        """分析模板声明"""
        self.file_path = file_path
        
        # 查找所有模板声明
        template_nodes = self._find_template_declarations(root_node)
        
        for template_node in template_nodes:
            template_info = self._analyze_template_declaration(template_node)
            if template_info:
                self.templates[template_info.qualified_name] = template_info
    
    def _find_template_declarations(self, node: Node) -> List[Node]:
        """查找所有模板声明"""
        template_nodes = []
        
        def traverse(n: Node):
            if n.type == 'template_declaration':
                template_nodes.append(n)
            
            for child in n.children:
                traverse(child)
        
        traverse(node)
        return template_nodes
    
    def _analyze_template_declaration(self, template_node: Node) -> Optional[TemplateInfo]:
        """分析单个模板声明"""
        try:
            # 获取模板参数列表
            template_params = self._extract_template_parameters(template_node)
            
            # 获取模板声明的主体
            declaration_node = self._get_template_declaration_body(template_node)
            if not declaration_node:
                return None
            
            # 确定模板类型和名称
            template_type, name, qualified_name = self._determine_template_info(declaration_node)
            
            if not name:
                return None
            
            template_info = TemplateInfo(
                name=name,
                qualified_name=qualified_name,
                template_type=template_type,
                parameters=template_params,
                file_path=self.file_path,
                start_line=template_node.start_point[0] + 1
            )
            
            # 检查是否是特化
            if self._is_template_specialization(template_node):
                # 找到原始模板并添加特化关系
                base_template = self._find_base_template(qualified_name)
                if base_template:
                    base_template.specializations.append(qualified_name)
            
            return template_info
            
        except Exception as e:
            self.logger.warning(f"模板分析失败: {e}")
            return None
    
    def _extract_template_parameters(self, template_node: Node) -> List[TemplateParameter]:
        """提取模板参数"""
        parameters = []
        
        # 查找template_parameter_list
        param_list_node = None
        for child in template_node.children:
            if child.type == 'template_parameter_list':
                param_list_node = child
                break
        
        if not param_list_node:
            return parameters
        
        # 解析每个参数
        for child in param_list_node.children:
            if child.type in ['type_parameter_declaration', 'parameter_declaration']:
                param = self._parse_template_parameter(child)
                if param:
                    parameters.append(param)
        
        return parameters
    
    def _parse_template_parameter(self, param_node: Node) -> Optional[TemplateParameter]:
        """解析单个模板参数"""
        param_text = self._get_text(param_node)
        
        # 基础解析
        if param_node.type == 'type_parameter_declaration':
            # typename T 或 class T
            if 'typename' in param_text or 'class' in param_text:
                # 提取参数名
                name = self._extract_parameter_name(param_text)
                return TemplateParameter(
                    name=name,
                    type="type",
                    is_variadic="..." in param_text
                )
        
        elif param_node.type == 'parameter_declaration':
            # 非类型参数：int N, size_t Size等
            name = self._extract_parameter_name(param_text)
            return TemplateParameter(
                name=name,
                type="non_type"
            )
        
        return None
    
    def _extract_parameter_name(self, param_text: str) -> str:
        """从参数文本中提取参数名"""
        # 简化实现：提取最后一个标识符
        tokens = param_text.replace(',', ' ').replace('>', ' ').split()
        for token in reversed(tokens):
            if token.isidentifier() and token not in ['typename', 'class', 'template']:
                return token
        return "unknown"
    
    def _get_template_declaration_body(self, template_node: Node) -> Optional[Node]:
        """获取模板声明的主体"""
        for child in template_node.children:
            if child.type in ['class_specifier', 'function_definition', 'declaration']:
                return child
        return None
    
    def _determine_template_info(self, declaration_node: Node) -> Tuple[str, str, str]:
        """确定模板类型和名称信息"""
        if declaration_node.type == 'class_specifier':
            name_node = declaration_node.child_by_field_name('name')
            name = self._get_text(name_node) if name_node else "unknown"
            return "class", name, name  # 简化的qualified_name
        
        elif declaration_node.type == 'function_definition':
            declarator = declaration_node.child_by_field_name('declarator')
            if declarator:
                name = self._extract_function_name_from_declarator(declarator)
                return "function", name, name
        
        return "unknown", "unknown", "unknown"
    
    def _extract_function_name_from_declarator(self, declarator: Node) -> str:
        """从函数声明符中提取函数名"""
        # 简化实现
        declarator_text = self._get_text(declarator)
        # 查找函数名（通常是第一个标识符）
        match = re.search(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', declarator_text)
        if match:
            return match.group(1)
        return "unknown"
    
    def _is_template_specialization(self, template_node: Node) -> bool:
        """检查是否是模板特化"""
        # 简化检查：查找template<>形式
        template_text = self._get_text(template_node)
        return 'template<>' in template_text or 'template <>' in template_text
    
    def _find_base_template(self, qualified_name: str) -> Optional[TemplateInfo]:
        """查找基础模板"""
        # 移除特化参数，查找基础模板
        base_name = qualified_name.split('<')[0] if '<' in qualified_name else qualified_name
        return self.templates.get(base_name)
    
    def analyze_template_instantiations(self, root_node: Node):
        """分析模板实例化"""
        # 查找模板实例化表达式
        instantiation_nodes = self._find_template_instantiations(root_node)
        
        for inst_node in instantiation_nodes:
            self._analyze_template_instantiation(inst_node)
    
    def _find_template_instantiations(self, node: Node) -> List[Node]:
        """查找模板实例化表达式"""
        instantiations = []
        
        def traverse(n: Node):
            if n.type == 'template_instantiation':
                instantiations.append(n)
            
            for child in n.children:
                traverse(child)
        
        traverse(node)
        return instantiations
    
    def _analyze_template_instantiation(self, inst_node: Node):
        """分析模板实例化"""
        # 获取模板名称
        name_node = inst_node.child_by_field_name('name')
        if not name_node:
            return
        
        template_name = self._get_text(name_node)
        
        # 获取模板参数
        args_node = inst_node.child_by_field_name('arguments')
        if not args_node:
            return
        
        # 构建实例化签名
        instantiation_sig = f"{template_name}<{self._get_text(args_node)}>"
        
        # 添加到对应模板的实例化列表
        if template_name in self.templates:
            template_info = self.templates[template_name]
            if instantiation_sig not in template_info.instantiations:
                template_info.instantiations.append(instantiation_sig)
    
    def get_templates_json(self) -> Dict[str, Any]:
        """获取模板信息的JSON表示"""
        templates_json = {}
        
        for qualified_name, template_info in self.templates.items():
            templates_json[qualified_name] = {
                "name": template_info.name,
                "qualified_name": template_info.qualified_name,
                "template_type": template_info.template_type,
                "parameters": [
                    {
                        "name": param.name,
                        "type": param.type,
                        "default_value": param.default_value,
                        "constraints": param.constraints,
                        "is_variadic": param.is_variadic
                    }
                    for param in template_info.parameters
                ],
                "specializations": template_info.specializations,
                "instantiations": template_info.instantiations,
                "concepts_constraints": template_info.concepts_constraints,
                "file_path": template_info.file_path,
                "start_line": template_info.start_line
            }
        
        return templates_json
    
    def _get_text(self, node: Node) -> str:
        """获取节点的文本内容"""
        if not node:
            return ""
        return self.file_content[node.start_byte:node.end_byte].decode('utf-8', errors='ignore') 