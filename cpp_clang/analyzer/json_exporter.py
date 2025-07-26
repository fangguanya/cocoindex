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
from datetime import datetime, timezone
from typing import Dict, Any
from dataclasses import asdict, is_dataclass
from pathlib import Path

from .logger import get_logger


class CustomJsonEncoder(json.JSONEncoder):
    """自定义JSON编码器，用于处理 dataclass"""
    def default(self, o: Any) -> Any:
        if is_dataclass(o) and not isinstance(o, type):
            return asdict(o)
        return super().default(o)


class JsonExporter:
    """JSON 导出器 (v2.4 - USR ID支持)"""
    
    FORMAT_VERSION = "2.4"
    
    def __init__(self):
        self.logger = get_logger()

    def export(self, extracted_data: Dict[str, Any], output_path: str) -> bool:
        """
        导出最终的JSON文件。

        Args:
            extracted_data: 从 EntityExtractor 获得的数据。
            output_path: 输出文件路径。

        Returns:
            导出是否成功。
        """
        try:
            self.logger.info("开始导出 JSON (v2.4 - USR ID支持)...")

            # 1. 准备主要的 JSON 数据
            json_data = {
                "version": self.FORMAT_VERSION,
                "language": "cpp",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "file_mappings": extracted_data.get('file_mappings', {}),
                "project_call_graph": {
                    "project_info": {
                        "total_functions": len(extracted_data.get('functions', {})),
                        "total_classes": len(extracted_data.get('classes', {})),
                        "total_namespaces": len(extracted_data.get('namespaces', {}))
                    },
                    "modules": {}, # 可选，暂为空
                    "global_call_graph": {
                        "functions": extracted_data.get('functions', {})
                    },
                    "reverse_call_graph": {} # 反向图信息已合并到函数定义中
                },
                "oop_analysis": {
                    "classes": extracted_data.get('classes', {}),
                    "inheritance_graph": self._build_inheritance_graph(extracted_data.get('classes', {})),
                    "method_resolution_orders": {} # 可选
                },
                "cpp_analysis": {
                    "namespaces": extracted_data.get('namespaces', {}),
                    "templates": {}, # 可选
                    "preprocessor": {} # 可选
                },
                "summary": {
                    "format_version": self.FORMAT_VERSION,
                    "uses_usr_id": True,
                    "has_code_content": True,
                    "has_global_nodes": True
                }
            }

            # 2. 写入主要的JSON文件
            self.logger.info(f"正在将主要结果写入: {output_path}")
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False, cls=CustomJsonEncoder)
            
            # 3. 导出全局nodes到单独的文件
            if extracted_data.get('global_nodes'):
                nodes_output_path = self._get_nodes_output_path(output_path)
                self.logger.info(f"正在将全局nodes写入: {nodes_output_path}")
                self._export_global_nodes(extracted_data.get('global_nodes', {}), nodes_output_path)

            # 4. 生成兼容性映射文件（可选）
            self._export_compatibility_mappings(extracted_data, output_path)
            
            self.logger.info("JSON 导出成功!")
            return True

        except Exception as e:
            self.logger.error(f"JSON 导出失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False

    def _build_inheritance_graph(self, classes: Dict[str, Any]) -> Dict[str, Any]:
        """构建继承关系图"""
        inheritance_graph = {}
        
        for class_usr_id, class_obj in classes.items():
            if hasattr(class_obj, 'parent_classes') and class_obj.parent_classes:
                inheritance_graph[class_usr_id] = {
                    "direct_bases": class_obj.parent_classes,
                    "all_bases": self._get_all_base_classes(class_usr_id, classes, set()),
                    "derived_classes": []
                }
        
        # 填充derived_classes
        for class_usr_id, class_obj in classes.items():
            if hasattr(class_obj, 'parent_classes'):
                for base_usr_id in class_obj.parent_classes:
                    if base_usr_id in inheritance_graph:
                        if class_usr_id not in inheritance_graph[base_usr_id]["derived_classes"]:
                            inheritance_graph[base_usr_id]["derived_classes"].append(class_usr_id)
                    else:
                        inheritance_graph[base_usr_id] = {
                            "direct_bases": [],
                            "all_bases": [],
                            "derived_classes": [class_usr_id]
                        }
        
        return inheritance_graph

    def _get_all_base_classes(self, class_usr_id: str, classes: Dict[str, Any], visited: set) -> list:
        """递归获取所有基类"""
        if class_usr_id in visited:
            return []  # 避免循环继承
        
        visited.add(class_usr_id)
        all_bases = []
        
        class_obj = classes.get(class_usr_id)
        if class_obj and hasattr(class_obj, 'parent_classes'):
            for base_usr_id in class_obj.parent_classes:
                all_bases.append(base_usr_id)
                all_bases.extend(self._get_all_base_classes(base_usr_id, classes, visited))
        
        visited.remove(class_usr_id)
        return list(set(all_bases))  # 去重

    def _export_global_nodes(self, global_nodes: Dict[str, Any], output_path: str) -> bool:
        """导出全局nodes到单独的JSON文件"""
        try:
            nodes_data = {
                "version": self.FORMAT_VERSION,
                "description": "Global nodes mapping with USR ID as keys",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total_nodes": len(global_nodes),
                "global_nodes": global_nodes
            }
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(nodes_data, f, indent=2, ensure_ascii=False, cls=CustomJsonEncoder)
            
            self.logger.info(f"全局nodes导出成功: {output_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"全局nodes导出失败: {e}")
            return False

    def _export_compatibility_mappings(self, extracted_data: Dict[str, Any], output_path: str) -> bool:
        """导出USR ID到签名键值的兼容性映射"""
        try:
            # 生成USR ID到签名键值的映射
            usr_to_signature = {}
            signature_to_usr = {}
            
            # 处理函数
            functions = extracted_data.get('functions', {})
            for usr_id, func_obj in functions.items():
                if hasattr(func_obj, 'cpp_extensions') and hasattr(func_obj.cpp_extensions, 'signature_key'):
                    signature_key = func_obj.cpp_extensions.signature_key
                    if signature_key:
                        usr_to_signature[usr_id] = signature_key
                        signature_to_usr[signature_key] = usr_id
            
            # 处理类
            classes = extracted_data.get('classes', {})
            for usr_id, class_obj in classes.items():
                if hasattr(class_obj, 'cpp_oop_extensions') and hasattr(class_obj.cpp_oop_extensions, 'signature_key'):
                    signature_key = class_obj.cpp_oop_extensions.signature_key
                    if signature_key:
                        usr_to_signature[usr_id] = signature_key
                        signature_to_usr[signature_key] = usr_id
            
            if usr_to_signature:
                compatibility_path = self._get_compatibility_output_path(output_path)
                compatibility_data = {
                    "version": self.FORMAT_VERSION,
                    "description": "Compatibility mappings between USR IDs and signature keys",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "usr_to_signature": usr_to_signature,
                    "signature_to_usr": signature_to_usr
                }
                
                with open(compatibility_path, 'w', encoding='utf-8') as f:
                    json.dump(compatibility_data, f, indent=2, ensure_ascii=False)
                
                self.logger.info(f"兼容性映射导出成功: {compatibility_path}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"兼容性映射导出失败: {e}")
            return False

    def _get_nodes_output_path(self, main_output_path: str) -> str:
        """生成nodes.json的输出路径"""
        path_obj = Path(main_output_path)
        return str(path_obj.parent / f"{path_obj.stem}_nodes.json")

    def _get_compatibility_output_path(self, main_output_path: str) -> str:
        """生成compatibility.json的输出路径"""
        path_obj = Path(main_output_path)
        return str(path_obj.parent / f"{path_obj.stem}_compatibility.json")

    def _build_reverse_call_graph(self, functions: Dict[str, Any]) -> Dict[str, Any]:
        """
        构建反向调用图（已在EntityExtractor中处理，此方法保留用于兼容性）

        Args:
            functions: 函数字典 (key: USR ID, value: Function object)。

        Returns:
            函数字典（已包含反向调用关系）。
        """
        # 在新版本中，反向调用关系已在EntityExtractor中构建
        # 此方法保留用于向后兼容，但实际不执行任何操作
        self.logger.debug("反向调用图已在EntityExtractor中构建")
        return functions