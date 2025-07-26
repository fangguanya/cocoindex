"""
JSON Exporter (符合 json_format.md v2.4)

该模块负责将分析器提取的实体数据进行最后的处理并导出为
符合 v2.4 规范的 JSON 文件。

主要职责：
- 支持USR ID作为主键的数据结构
- 导出全局nodes映射
- 构建反向调用图 (`called_by`)。
- 添加顶层的元数据 (version, timestamp)。
- 将最终的数据结构序列化为 JSON。
- 导出额外的nodes.json文件
"""

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List
from datetime import datetime

from .data_structures import Project, NodeRepository, Entity, Function, Class, Namespace


class CustomJsonEncoder(json.JSONEncoder):
    """自定义JSON编码器，用于处理dataclass"""
    def default(self, o: Any) -> Any:
        if is_dataclass(o):
            return asdict(o)
        return super().default(o)


class JsonExporter:
    """将分析结果导出为JSON格式"""

    def __init__(self):
        self.version = "2.4"
        self.language = "cpp"

    def export_analysis_result(self, project: Project, repo: NodeRepository, output_path: str):
        """
        导出主分析结果JSON文件，符合json_format.md规范.
        
        :param project: 分析后的Project对象.
        :param repo: 包含所有节点的NodeRepository.
        :param output_path: 输出文件路径.
        """
        
        # 构建符合json_format.md的完整结构
        analysis_result = {
            "version": self.version,
            "language": self.language,
            "timestamp": datetime.now().isoformat(),
            "file_mappings": self._build_file_mappings(repo),
            "project_call_graph": {
                "project_info": {
                    "name": project.name,
                    "total_files": len(project.files),
                    "total_functions": len(project.functions),
                    "total_classes": len(project.classes),
                    "total_namespaces": len(project.namespaces)
                },
                "modules": self._build_modules_info(project, repo),
                "global_call_graph": self._build_global_call_graph(repo),
                "reverse_call_graph": self._build_reverse_call_graph(repo)
            },
            "oop_analysis": {
                "classes": self._build_classes_analysis(repo),
                "inheritance_graph": self._build_inheritance_graph(repo),
                "method_resolution_orders": {}  # 简化处理
            },
            "cpp_analysis": {
                "namespaces": self._build_namespaces_analysis(repo),
                "templates": {},  # 简化处理
                "preprocessor": {}  # 简化处理
            },
            "summary": self._build_summary(project, repo)
        }

        self._write_json(analysis_result, output_path)

    def export_nodes_json(self, repo: NodeRepository, output_path: str):
        """
        导出全局节点映射JSON文件.
        
        :param repo: 包含所有节点的NodeRepository.
        :param output_path: 输出文件路径.
        """
        nodes_data = {
            "version": self.version,
            "timestamp": datetime.now().isoformat(),
            "node_type": "global_entities",
            "total_entities": len(repo.nodes),
            "statistics": repo.get_statistics(),
            "entities": {}
        }
        
        # 以USR ID为key，完整实体对象为value
        for usr_id, entity in repo.nodes.items():
            nodes_data["entities"][usr_id] = {
                "type": entity.type,
                "data": self._entity_to_dict(entity)
            }
            
        self._write_json(nodes_data, output_path)

    def _build_file_mappings(self, repo: NodeRepository) -> Dict[str, str]:
        """构建文件ID映射"""
        file_mappings = {}
        files = set()
        
        # 收集所有文件路径
        for entity in repo.nodes.values():
            if hasattr(entity, 'file_path') and entity.file_path:
                files.add(entity.file_path)
        
        # 生成文件ID映射
        for i, file_path in enumerate(sorted(files), 1):
            file_id = f"f{i:03d}"
            file_mappings[file_id] = file_path
        
        return file_mappings

    def _build_modules_info(self, project: Project, repo: NodeRepository) -> Dict[str, Any]:
        """构建模块信息"""
        modules = {}
        
        # 按文件组织模块
        file_mappings = self._build_file_mappings(repo)
        reverse_file_mappings = {v: k for k, v in file_mappings.items()}
        
        for file_path in project.files:
            file_id = reverse_file_mappings.get(file_path, f"f{len(modules)+1:03d}")
            entities = repo.get_nodes_by_file(file_path)
            
            modules[file_id] = {
                "file_path": file_path,
                "functions": [e.usr for e in entities if isinstance(e, Function)],
                "classes": [e.usr for e in entities if isinstance(e, Class)],
                "namespaces": [e.usr for e in entities if isinstance(e, Namespace)]
            }
        
        return modules

    def _build_global_call_graph(self, repo: NodeRepository) -> Dict[str, List[str]]:
        """构建全局调用图"""
        call_graph = {}
        
        for usr_id, entity in repo.nodes.items():
            if isinstance(entity, Function) and entity.calls_to:
                call_graph[usr_id] = entity.calls_to
        
        return call_graph

    def _build_reverse_call_graph(self, repo: NodeRepository) -> Dict[str, List[str]]:
        """构建反向调用图"""
        reverse_call_graph = {}
        
        for usr_id, entity in repo.nodes.items():
            if isinstance(entity, Function) and entity.called_by:
                reverse_call_graph[usr_id] = entity.called_by
        
        return reverse_call_graph

    def _build_classes_analysis(self, repo: NodeRepository) -> Dict[str, Any]:
        """构建类分析信息"""
        classes_analysis = {}
        
        for usr_id, entity in repo.nodes.items():
            if isinstance(entity, Class):
                classes_analysis[usr_id] = {
                    "name": entity.name,
                    "qualified_name": entity.qualified_name,
                    "signature": f"{entity.qualified_name}_{entity.file_path}",  # 向后兼容
                    "definition_file_id": self._get_file_id_for_path(entity.file_path),
                    "start_line": entity.start_line,
                    "end_line": entity.end_line,
                    "is_local": False,  # 简化处理
                    "methods": entity.methods,
                    "fields": getattr(entity, 'fields', []),
                    "parent_classes": entity.base_classes,
                    "derived_classes": entity.derived_classes,
                    "is_abstract": entity.is_abstract,
                    "documentation": getattr(entity, 'documentation', ""),
                    "cpp_oop_extensions": self._build_cpp_oop_extensions(entity)
                }
        
        return classes_analysis

    def _build_inheritance_graph(self, repo: NodeRepository) -> Dict[str, List[str]]:
        """构建继承图"""
        inheritance_graph = {}
        
        for usr_id, entity in repo.nodes.items():
            if isinstance(entity, Class) and entity.base_classes:
                inheritance_graph[usr_id] = entity.base_classes
        
        return inheritance_graph

    def _build_namespaces_analysis(self, repo: NodeRepository) -> Dict[str, Any]:
        """构建命名空间分析信息"""
        namespaces_analysis = {}
        
        for usr_id, entity in repo.nodes.items():
            if isinstance(entity, Namespace):
                namespaces_analysis[usr_id] = {
                    "name": entity.name,
                    "qualified_name": entity.qualified_name,
                    "definition_file_id": self._get_file_id_for_path(entity.file_path),
                    "line": entity.start_line,
                    "is_anonymous": getattr(entity, 'is_anonymous', False),
                    "is_inline": getattr(entity, 'is_inline', False),
                    "parent_namespace": getattr(entity, 'parent_namespace', "global"),
                    "nested_namespaces": getattr(entity, 'nested_namespaces', []),
                    "classes": getattr(entity, 'classes', []),
                    "functions": getattr(entity, 'functions', []),
                    "variables": getattr(entity, 'variables', []),
                    "aliases": getattr(entity, 'aliases', {}),
                    "using_declarations": getattr(entity, 'using_declarations', [])
                }
        
        return namespaces_analysis

    def _build_cpp_oop_extensions(self, class_entity: Class) -> Dict[str, Any]:
        """构建C++ OOP扩展信息"""
        return {
            "qualified_name": class_entity.qualified_name,
            "namespace": "::".join(class_entity.qualified_name.split("::")[:-1]) if "::" in class_entity.qualified_name else "",
            "type": "struct" if class_entity.is_struct else "class",
            "class_status_flags": 0,  # 简化处理
            "inheritance_list": [],  # 简化处理
            "template_parameters": [],
            "template_specialization_args": [],
            "nested_types": [],
            "friend_declarations": [],
            "size_in_bytes": 0,
            "alignment": 0,
            "virtual_table_info": {},
            "constructors": {},
            "destructor": None,
            "usr": class_entity.usr,
            "signature_key": f"{class_entity.qualified_name}_{self._get_file_id_for_path(class_entity.file_path)}"
        }

    def _build_summary(self, project: Project, repo: NodeRepository) -> Dict[str, Any]:
        """构建摘要信息"""
        stats = repo.get_statistics()
        
        return {
            "parser_type": "tree-sitter",
            "function_body_included": True,
            "usr_id_system": True,
            "two_phase_analysis": True,
            "total_entities": stats['total_entities'],
            "entities_by_type": stats['by_type'],
            "call_relationships": stats['call_relationships'],
            "files_analyzed": stats['files_analyzed'],
            "treesitter_stats": {
                "total_nodes_parsed": sum(stats['by_type'].values()),
                "function_bodies_extracted": stats['by_type'].get('function', 0),
                "avg_nodes_per_file": round(stats['total_entities'] / max(stats['files_analyzed'], 1), 2)
            }
        }

    def _get_file_id_for_path(self, file_path: str) -> str:
        """为文件路径生成文件ID（简化实现）"""
        # 这里简化处理，实际应该使用正确的文件映射
        return "f001"  # 在实际实现中应该查找正确的文件ID

    def _get_entities_from_usrs(self, usrs: List[str], repo: NodeRepository) -> List[Dict[str, Any]]:
        """根据USR列表从存储库中获取并序列化实体"""
        entities = []
        for usr in usrs:
            node = repo.get_node(usr)
            if node:
                entities.append(self._entity_to_dict(node))
        return entities

    def _entity_to_dict(self, entity: Entity) -> Dict[str, Any]:
        """将单个实体对象转换为字典"""
        # asdict能很好地处理dataclass的转换
        entity_dict = asdict(entity)
        
        # 为函数添加扩展信息
        if isinstance(entity, Function):
            entity_dict["cpp_extensions"] = {
                "qualified_name": entity.qualified_name,
                "namespace": "::".join(entity.qualified_name.split("::")[:-1]) if "::" in entity.qualified_name else "",
                "function_status_flags": 0,  # 简化处理
                "access_specifier": getattr(entity, 'access_specifier', 'public'),
                "storage_class": "none",
                "calling_convention": "default",
                "return_type": entity.return_type,
                "parameter_types": {p.get('name', ''): p.get('type', '') for p in entity.parameters},
                "template_parameters": [],
                "exception_specification": "",
                "attributes": [],
                "mangled_name": "",
                "usr": entity.usr,
                "signature_key": f"{entity.qualified_name}_{self._get_file_id_for_path(entity.file_path)}"
            }
        
        return entity_dict

    def _write_json(self, data: Dict, output_path: str):
        """将字典写入JSON文件"""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, cls=CustomJsonEncoder, indent=2, ensure_ascii=False) 