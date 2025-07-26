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
from typing import Any, Dict, List, Optional, Tuple
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
    
    # 统一版本号管理
    SCHEMA_VERSION = "2.4"  # 统一版本号

    def __init__(self, file_manager=None):
        self.version = self.SCHEMA_VERSION  # 使用统一版本
        self.language = "cpp"
        self.file_manager = file_manager
        # 修复：添加文件映射缓存
        self._file_mappings: Dict[str, str] = {}
        self._reverse_file_mappings: Dict[str, str] = {}

    def _validate_call_graph_consistency(self, repo: NodeRepository) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
        """校验并修复调用图双向一致性"""
        calls_to = {}
        called_by = {}
        
        # 收集所有调用关系
        for usr, entity in repo.nodes.items():
            if isinstance(entity, Function):
                if entity.calls_to:
                    calls_to[usr] = entity.calls_to.copy()
                if entity.called_by:
                    called_by[usr] = entity.called_by.copy()
        
        # 确保双向对齐：∀callee∈called_by ⇒ caller∈calls_to
        inconsistencies_found = 0
        
        for caller, callees in calls_to.items():
            for callee in callees:
                if callee not in called_by:
                    called_by[callee] = []
                if caller not in called_by[callee]:
                    called_by[callee].append(caller)
                    inconsistencies_found += 1
        
        for callee, callers in called_by.items():
            for caller in callers:
                if caller not in calls_to:
                    calls_to[caller] = []
                if callee not in calls_to[caller]:
                    calls_to[caller].append(callee)
                    inconsistencies_found += 1
        
        if inconsistencies_found > 0:
            from .logger import Logger
            logger = Logger.get_logger()
            logger.info(f"修复了 {inconsistencies_found} 处调用图不一致问题")
        
        return calls_to, called_by

    def _validate_data_consistency(self, repo: NodeRepository) -> Dict[str, Any]:
        """全面的数据一致性检查"""
        issues = {
            "missing_usrs": [],
            "orphaned_calls": [],
            "invalid_references": [],
            "statistics": {}
        }
        
        # 检查调用关系中的USR是否都存在
        for usr, entity in repo.nodes.items():
            if isinstance(entity, Function):
                for callee_usr in entity.calls_to:
                    if callee_usr not in repo.nodes:
                        issues["orphaned_calls"].append({
                            "caller": usr,
                            "missing_callee": callee_usr
                        })
                
                for caller_usr in entity.called_by:
                    if caller_usr not in repo.nodes:
                        issues["orphaned_calls"].append({
                            "callee": usr,
                            "missing_caller": caller_usr
                        })
        
        # 检查类方法引用
        for usr, entity in repo.nodes.items():
            if isinstance(entity, Class):
                for method_usr in entity.methods:
                    if method_usr not in repo.nodes:
                        issues["invalid_references"].append({
                            "class": usr,
                            "missing_method": method_usr
                        })
        
        # 统计信息
        issues["statistics"] = {
            "total_entities": len(repo.nodes),
            "total_orphaned_calls": len(issues["orphaned_calls"]),
            "total_invalid_references": len(issues["invalid_references"])
        }
        
        return issues

    def export_analysis_result(self, project: Project, repo: NodeRepository, output_path: str):
        """
        导出主分析结果JSON文件，符合json_format.md规范.
        
        :param project: 分析后的Project对象.
        :param repo: 包含所有节点的NodeRepository.
        :param output_path: 输出文件路径.
        """
        
        # 修复：初始化文件映射
        self._initialize_file_mappings(repo)
        
        # 保存repo引用以便在其他方法中使用
        self.repo = repo
        
        # 数据一致性校验和修复
        validated_calls_to, validated_called_by = self._validate_call_graph_consistency(repo)
        consistency_issues = self._validate_data_consistency(repo)
        
        # 记录数据质量信息
        from .logger import Logger
        logger = Logger.get_logger()
        if consistency_issues["statistics"]["total_orphaned_calls"] > 0:
            logger.warning(f"发现 {consistency_issues['statistics']['total_orphaned_calls']} 个孤立的调用关系")
        if consistency_issues["statistics"]["total_invalid_references"] > 0:
            logger.warning(f"发现 {consistency_issues['statistics']['total_invalid_references']} 个无效引用")
        
        logger.info("🏗️  开始构建分析结果数据结构...")
        
        # 构建符合v2.4规范的完整结构
        logger.info("📋 构建基础信息...")
        analysis_result = {
            "version": self.SCHEMA_VERSION,  # 使用统一版本
            "language": "cpp", 
            "timestamp": datetime.now().isoformat(),
            "file_mappings": self._file_mappings,
            "data_quality": {
                "consistency_check": consistency_issues["statistics"],
                "validated_call_graph": True,
                "total_entities": len(repo.nodes)
            },
            "entities": {},  # 先创建空的entities
            "metadata": {},
            "config": {},
            "project_call_graph": {},
            "oop_analysis": {},
            "cpp_analysis": {},
            "summary": {}
        }
        
        logger.info("🔧 构建函数实体...")
        analysis_result["entities"]["functions"] = self._build_functions_entities(repo)
        logger.info(f"✅ 函数实体构建完成，数量: {len(analysis_result['entities']['functions'])}")
        
        logger.info("🏛️  构建类实体...")
        analysis_result["entities"]["classes"] = self._build_classes_entities(repo)
        logger.info(f"✅ 类实体构建完成，数量: {len(analysis_result['entities']['classes'])}")
        
        logger.info("🗂️  构建命名空间实体...")
        analysis_result["entities"]["namespaces"] = self._build_namespaces_entities(repo)
        logger.info(f"✅ 命名空间实体构建完成，数量: {len(analysis_result['entities']['namespaces'])}")
        
        logger.info("📄 构建模板实体...")
        analysis_result["entities"]["templates"] = self._build_templates_entities()
        logger.info("✅ 模板实体构建完成")
        
        logger.info("⚙️  构建操作符实体...")
        analysis_result["entities"]["operators"] = self._build_operators_entities(repo)
        logger.info("✅ 操作符实体构建完成")
        
        logger.info("🔗 构建调用关系...")
        analysis_result["entities"]["call_relations"] = self._build_call_relations(repo)
        logger.info(f"✅ 调用关系构建完成，数量: {len(analysis_result['entities']['call_relations'])}")
        
        logger.info("🏗️  构建继承关系...")
        analysis_result["entities"]["inheritance_relations"] = self._build_inheritance_relations(repo)
        logger.info("✅ 继承关系构建完成")
        
        logger.info("🧠 构建类型推理信息...")
        analysis_result["entities"]["type_inference_info"] = self._build_type_inference_info(repo)
        logger.info("✅ 类型推理信息构建完成")
        
        logger.info("📊 构建元数据...")
        analysis_result["metadata"] = self._build_metadata_v24(project, repo)
        logger.info("✅ 元数据构建完成")
        
        logger.info("⚙️  构建配置信息...")
        analysis_result["config"] = self._build_config_v24(project)
        logger.info("✅ 配置信息构建完成")
        
        logger.info("🌐 构建项目调用图...")
        logger.info("  - 构建项目信息...")
        project_info = {
            "name": project.name,
            "total_files": len(project.files),
            "total_functions": len(project.functions),
            "total_classes": len(project.classes),
            "total_namespaces": len(project.namespaces)
        }
        logger.info("  ✅ 项目信息构建完成")
        
        logger.info("  - 构建模块信息...")
        modules_info = self._build_modules_info(project, repo)
        logger.info(f"  ✅ 模块信息构建完成，数量: {len(modules_info)}")
        
        logger.info("  - 添加全局调用图（这可能需要较长时间）...")
        analysis_result["project_call_graph"] = {
            "project_info": project_info,
            "modules": modules_info,
            "global_call_graph": validated_calls_to,  # 使用校验后的数据
            "reverse_call_graph": validated_called_by   # 使用校验后的数据
        }
        logger.info(f"  ✅ 项目调用图构建完成，calls_to: {len(validated_calls_to)}, called_by: {len(validated_called_by)}")
        
        logger.info("🏛️  构建OOP分析...")
        analysis_result["oop_analysis"] = {
            "classes": self._build_classes_analysis(repo),
            "inheritance_graph": self._build_inheritance_graph(repo),
            "method_resolution_orders": {}  # 简化处理
        }
        logger.info("✅ OOP分析构建完成")
        
        logger.info("🔧 构建C++分析...")
        analysis_result["cpp_analysis"] = {
            "namespaces": self._build_namespaces_analysis(repo),
            "templates": {},  # 简化处理
            "preprocessor": {}  # 简化处理
        }
        logger.info("✅ C++分析构建完成")
        
        logger.info("📋 构建摘要...")
        analysis_result["summary"] = self._build_summary(project, repo)
        logger.info("✅ 摘要构建完成")
        
        logger.info("💾 开始写入JSON文件...")
        self._write_json(analysis_result, output_path)
        logger.info("✅ JSON文件写入完成")

    def export_nodes_json(self, repo: NodeRepository, output_path: str):
        """
        导出全局节点映射JSON文件.
        
        :param repo: 包含所有节点的NodeRepository.
        :param output_path: 输出文件路径.
        """
        # 修复：确保文件映射已初始化
        if not self._file_mappings:
            self._initialize_file_mappings(repo)
            
        nodes_data = {
            "version": self.SCHEMA_VERSION,  # 使用统一版本
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

    def _initialize_file_mappings(self, repo: NodeRepository):
        """初始化文件映射缓存"""
        if self.file_manager:
            # 使用文件管理器的映射
            self._file_mappings = self.file_manager.get_file_mappings()
            self._reverse_file_mappings = self.file_manager.get_reverse_mappings()
        else:
            # 备用方案：从repo中收集文件
            files = set()
            
            # 收集所有文件路径
            for entity in repo.nodes.values():
                if hasattr(entity, 'file_path') and entity.file_path:
                    files.add(entity.file_path)
            
            # 生成文件ID映射
            self._file_mappings.clear()
            self._reverse_file_mappings.clear()
            
            for i, file_path in enumerate(sorted(files), 1):
                file_id = f"f{i:03d}"
                self._file_mappings[file_id] = file_path
                self._reverse_file_mappings[file_path] = file_id

    def _build_file_mappings(self, repo: NodeRepository) -> Dict[str, str]:
        """构建文件ID映射（保持向后兼容）"""
        if not self._file_mappings:
            self._initialize_file_mappings(repo)
        return self._file_mappings.copy()

    def _build_modules_info(self, project: Project, repo: NodeRepository) -> Dict[str, Any]:
        """构建模块信息"""
        modules = {}
        
        # 按文件组织模块
        for file_path in project.files:
            file_id = self._get_file_id_for_path(file_path)
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
        """构建C++ OOP扩展信息 - 完善版：填充继承关系和类状态标志"""
        # 构建继承关系列表
        inheritance_list = []
        for base_class_usr in class_entity.base_classes:
            base_class = self.repo.get_node(base_class_usr) if hasattr(self, 'repo') else None
            inheritance_info = {
                "base_class_usr_id": base_class_usr,
                "access_specifier": "public",  # 默认public，实际应从AST解析
                "is_virtual": False  # 默认非虚继承
            }
            
            # 如果能找到基类，尝试获取更多信息
            if base_class and isinstance(base_class, Class):
                inheritance_info["base_class_name"] = base_class.name
                inheritance_info["base_class_qualified_name"] = base_class.qualified_name
            
            inheritance_list.append(inheritance_info)
        
        # 计算类状态标志
        class_status_flags = self._compute_class_status_flags(class_entity)
        
        # 构建构造函数信息
        constructors = {}
        destructor = None
        
        # 从methods中查找构造函数和析构函数
        if hasattr(self, 'repo'):
            for method_usr in class_entity.methods:
                method = self.repo.get_node(method_usr)
                if method and isinstance(method, Function):
                    method_name = method.name
                    
                    # 检测构造函数（名称与类名相同）
                    if method_name == class_entity.name:
                        constructor_info = {
                            "special_method_status_flags": 0,  # 可进一步解析
                            "access": getattr(method, 'access_specifier', 'public'),
                            "usr": method_usr
                        }
                        constructors[method_usr] = constructor_info
                    
                    # 检测析构函数（以~开头）
                    elif method_name.startswith('~') and method_name[1:] == class_entity.name:
                        destructor = {
                            "special_method_status_flags": 0,
                            "access": getattr(method, 'access_specifier', 'public'),
                            "usr": method_usr
                        }
        
        return {
            "qualified_name": class_entity.qualified_name,
            "namespace": "::".join(class_entity.qualified_name.split("::")[:-1]) if "::" in class_entity.qualified_name else "",
            "type": "struct" if class_entity.is_struct else "class",
            "class_status_flags": class_status_flags,
            "inheritance_list": inheritance_list,
            "template_parameters": getattr(class_entity, 'template_parameters', []),
            "template_specialization_args": getattr(class_entity, 'template_specialization_args', []),
            "nested_types": getattr(class_entity, 'nested_types', []),
            "friend_declarations": getattr(class_entity, 'friend_declarations', []),
            "size_in_bytes": getattr(class_entity, 'size_in_bytes', 0),
            "alignment": getattr(class_entity, 'alignment', 0),
            "virtual_table_info": getattr(class_entity, 'virtual_table_info', {}),
            "constructors": constructors,
            "destructor": destructor,
            "usr": class_entity.usr,
            "signature_key": f"{class_entity.qualified_name}_{self._get_file_id_for_path(class_entity.file_path)}"
        }
    
    def _compute_class_status_flags(self, class_entity: Class) -> int:
        """计算类状态标志位"""
        from .data_structures import ClassStatusFlags
        
        flags = 0
        
        # 检查是否为抽象类
        if class_entity.is_abstract:
            flags |= ClassStatusFlags.CLASS_IS_ABSTRACT
        
        # 检查是否为模板类
        if class_entity.is_template:
            flags |= ClassStatusFlags.CLASS_IS_TEMPLATE
        
        # 检查是否为final类（如果有相关信息）
        if hasattr(class_entity, 'is_final') and getattr(class_entity, 'is_final', False):
            flags |= ClassStatusFlags.CLASS_IS_FINAL
        
        # 检查是否为POD类型（简化判断）
        if hasattr(class_entity, 'is_pod') and getattr(class_entity, 'is_pod', False):
            flags |= ClassStatusFlags.CLASS_IS_POD
        
        # 检查是否为多态类（有虚函数）
        has_virtual_methods = False
        if hasattr(self, 'repo'):
            for method_usr in class_entity.methods:
                method = self.repo.get_node(method_usr)
                if method and isinstance(method, Function):
                    if getattr(method, 'is_virtual', False) or getattr(method, 'is_pure_virtual', False):
                        has_virtual_methods = True
                        break
        
        if has_virtual_methods:
            flags |= ClassStatusFlags.CLASS_IS_POLYMORPHIC
        
        return flags

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
        """修复：为文件路径生成正确的文件ID"""
        # 确保文件映射已初始化
        if not self._reverse_file_mappings:
            # 如果映射未初始化，返回默认值并记录警告
            return "f001"
        
        # 从反向映射中查找文件ID
        file_id = self._reverse_file_mappings.get(file_path)
        if file_id:
            return file_id
        
        # 如果找不到，尝试标准化路径后再查找
        normalized_path = str(Path(file_path).resolve()).replace('\\', '/')
        for path, fid in self._reverse_file_mappings.items():
            if str(Path(path).resolve()).replace('\\', '/') == normalized_path:
                return fid
        
        # 如果仍然找不到，返回默认值
        return "f001"

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
        from .logger import Logger
        logger = Logger.get_logger()
        
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # 计算数据大小统计
        logger.info(f"💾 准备写入JSON到: {path}")
        if 'entities' in data:
            entities = data['entities']
            logger.info(f"   - 函数数量: {len(entities.get('functions', {}))}")
            logger.info(f"   - 类数量: {len(entities.get('classes', {}))}")
            logger.info(f"   - 命名空间数量: {len(entities.get('namespaces', {}))}")
            logger.info(f"   - 调用关系数量: {len(entities.get('call_relations', {}))}")
        
        if 'project_call_graph' in data:
            pcg = data['project_call_graph']
            if 'global_call_graph' in pcg:
                logger.info(f"   - 全局调用图条目: {len(pcg['global_call_graph'])}")
            if 'reverse_call_graph' in pcg:
                logger.info(f"   - 反向调用图条目: {len(pcg['reverse_call_graph'])}")
        
        logger.info("🔄 开始JSON序列化...")
        import time
        start_time = time.time()
        
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, cls=CustomJsonEncoder, indent=2, ensure_ascii=False)
            
            serialize_time = time.time() - start_time
            file_size_mb = path.stat().st_size / (1024 * 1024)
            logger.info(f"✅ JSON序列化完成: {file_size_mb:.2f}MB, 耗时: {serialize_time:.2f}秒")
            
        except Exception as e:
            serialize_time = time.time() - start_time
            logger.error(f"❌ JSON序列化失败 (耗时: {serialize_time:.2f}秒): {e}")
            raise

    def _build_functions_entities(self, repo: NodeRepository) -> Dict[str, Any]:
        """构建 entities.functions 映射 - 修复关键缺失功能"""
        functions_entities = {}
        
        for usr_id, entity in repo.nodes.items():
            if isinstance(entity, Function):
                # 生成符合v2.3规范的签名键值
                file_id = self._get_file_id_for_path(entity.file_path)
                signature_key = repo.generate_signature_key(
                    'function', 
                    entity.qualified_name, 
                    entity.signature,
                    getattr(entity, 'template_params', None),
                    file_id
                )
                
                # 构建符合规范的函数实体信息
                function_data = {
                    "name": entity.name,
                    "qualified_name": entity.qualified_name,
                    "signature": entity.signature,
                    "return_type": entity.return_type or "void",
                    "parameters": entity.parameters,
                    "usr": entity.usr,
                    "definition_file_id": file_id,
                    "declaration_file_id": file_id,  # v2.3规范要求
                    "start_line": entity.start_line,
                    "end_line": entity.end_line,
                    "is_definition": entity.is_definition,
                    "is_declaration": getattr(entity, 'is_declaration', False),
                    "is_local": False,  # 简化处理
                    "documentation": entity.code_content if entity.code_content else "",
                    "calls_to": entity.calls_to,
                    "called_by": entity.called_by,
                    "complexity": getattr(entity, 'complexity', 0),
                    "cpp_extensions": {
                        "qualified_name": entity.qualified_name,
                        "namespace": "::".join(entity.qualified_name.split("::")[:-1]) if "::" in entity.qualified_name else "",
                        "function_status_flags": self._compute_function_status_flags(entity),
                        "access_specifier": getattr(entity, 'access_specifier', 'public'),
                        "storage_class": "none",
                        "calling_convention": "default",
                        "return_type": entity.return_type or "void",
                        "parameter_types": {p.get('name', f'param_{i}'): p.get('type', 'unknown') for i, p in enumerate(entity.parameters)},
                        "template_parameters": getattr(entity, 'template_params', []),
                        "exception_specification": "",
                        "attributes": [],
                        "mangled_name": "",
                        "usr": entity.usr,
                        "signature_key": signature_key
                    }
                }
                
                # 使用签名键值作为主键（v2.3规范要求）
                functions_entities[signature_key] = function_data
        
        return functions_entities

    def _build_classes_entities(self, repo: NodeRepository) -> Dict[str, Any]:
        """构建 entities.classes 映射"""
        classes_entities = {}
        
        for usr_id, entity in repo.nodes.items():
            if isinstance(entity, Class):
                # 生成符合v2.3规范的签名键值
                file_id = self._get_file_id_for_path(entity.file_path)
                signature_key = repo.generate_signature_key(
                    'class', 
                    entity.qualified_name,
                    None,  # 类没有函数签名
                    getattr(entity, 'template_params', None),
                    file_id
                )
                
                class_data = {
                    "name": entity.name,
                    "qualified_name": entity.qualified_name,
                    "usr": entity.usr,
                    "definition_file_id": file_id,
                    "declaration_file_id": file_id,  # v2.3规范要求
                    "start_line": entity.start_line,
                    "end_line": entity.end_line,
                    "is_definition": entity.is_definition,
                    "is_declaration": getattr(entity, 'is_declaration', False),
                    "is_local": False,  # 简化处理
                    "methods": entity.methods,
                    "fields": getattr(entity, 'fields', []),
                    "parent_classes": entity.base_classes,
                    "derived_classes": entity.derived_classes,
                    "is_abstract": entity.is_abstract,
                    "is_mixin": False,  # v2.3规范要求
                    "is_struct": entity.is_struct,
                    "documentation": getattr(entity, 'documentation', ""),
                    "cpp_oop_extensions": self._build_cpp_oop_extensions(entity)
                }
                
                # 使用签名键值作为主键（v2.3规范要求）
                classes_entities[signature_key] = class_data
        
        return classes_entities

    def _build_namespaces_entities(self, repo: NodeRepository) -> Dict[str, Any]:
        """构建 entities.namespaces 映射"""
        namespaces_entities = {}
        
        for usr_id, entity in repo.nodes.items():
            if isinstance(entity, Namespace):
                namespace_data = {
                    "name": entity.name,
                    "qualified_name": entity.qualified_name,
                    "usr": entity.usr,
                    "definition_file_id": self._get_file_id_for_path(entity.file_path),
                    "start_line": entity.start_line,
                    "end_line": entity.end_line,
                    "is_anonymous": getattr(entity, 'is_anonymous', False),
                    "is_inline": getattr(entity, 'is_inline', False),
                    "parent_namespace": getattr(entity, 'parent_namespace', "global"),
                    "nested_namespaces": getattr(entity, 'nested_namespaces', []),
                    "classes": getattr(entity, 'classes', []),
                    "functions": getattr(entity, 'functions', []),
                    "variables": getattr(entity, 'variables', [])
                }
                
                namespaces_entities[usr_id] = namespace_data
        
        return namespaces_entities

    def _build_call_relations(self, repo: NodeRepository) -> List[Dict[str, Any]]:
        """构建调用关系列表"""
        call_relations = []
        
        for usr_id, entity in repo.nodes.items():
            if isinstance(entity, Function) and entity.calls_to:
                for callee_usr in entity.calls_to:
                    # 查找详细的调用信息
                    call_detail = None
                    for call_info in getattr(entity, 'call_details', []):
                        if call_info.to_usr_id == callee_usr:
                            call_detail = call_info
                            break
                    
                    relation = {
                        "caller_usr": usr_id,
                        "callee_usr": callee_usr,
                        "call_type": call_detail.type if call_detail else "direct",
                        "line": call_detail.line if call_detail else entity.start_line,
                        "column": call_detail.column if call_detail else 0
                    }
                    
                    call_relations.append(relation)
        
        return call_relations

    def _build_inheritance_relations(self, repo: NodeRepository) -> List[Dict[str, Any]]:
        """构建继承关系列表"""
        inheritance_relations = []
        
        for usr_id, entity in repo.nodes.items():
            if isinstance(entity, Class) and entity.base_classes:
                for base_class_usr in entity.base_classes:
                    base_class = repo.get_node(base_class_usr)
                    
                    relation = {
                        "derived_class_usr": usr_id,
                        "base_class_usr": base_class_usr,
                        "access_specifier": "public",  # 默认public，实际应从AST解析
                        "is_virtual": False,  # 默认非虚继承
                        "derived_class_name": entity.qualified_name,
                        "base_class_name": base_class.qualified_name if base_class else "unknown"
                    }
                    
                    inheritance_relations.append(relation)
        
        return inheritance_relations

    def _build_type_inference_info(self, repo: NodeRepository) -> Dict[str, Any]:
        """构建类型推断信息（简化版）"""
        # 这里可以在后续增强时集成实际的类型推断结果
        return {
            "inferences": [],
            "template_deductions": [],
            "auto_deductions": [],
            "confidence_statistics": {
                "high_confidence": 0,
                "medium_confidence": 0,  
                "low_confidence": 0
            }
        }

    def _build_metadata_v23(self, project: Project, repo: NodeRepository) -> Dict[str, Any]:
        """构建符合v2.3规范的元数据"""
        stats = repo.get_statistics()
        file_stats = self.file_manager.get_statistics() if self.file_manager else {}
        
        return {
            "generated_at": datetime.now().isoformat(),
            "generator": "cpp_treesitter_v2.3",
            "project_name": project.name,
            "total_files": len(project.files),
            "analysis_time_seconds": 0,  # 可在分析器中计算实际时间
            "schema_version": "2.3",
            "parser_version": "tree-sitter",
            "features_enabled": [
                "usr_generation",
                "status_flags", 
                "file_mapping",
                "signature_keys",
                "type_inference",
                "overload_resolution",
                "class_methods"
            ]
        }

    def _build_config_v23(self, project: Project) -> Dict[str, Any]:
        """构建符合v2.3规范的配置信息"""
        return {
            "parser_type": "tree-sitter",
            "features_enabled": [
                "template_analysis",
                "type_inference", 
                "operator_overloading",
                "parallel_processing"
            ],
            "analysis_mode": "two_phase",
            "max_workers": 1,  # 当前实现为单线程
            "project_root": project.name,
            "scan_directory": project.name,
            "include_function_body": True,
            "generate_usr": True,
            "use_file_mapping": True
        }

    def _compute_function_status_flags(self, function_entity: Function) -> int:
        """计算函数状态标志位"""
        from .data_structures import FunctionStatusFlags
        
        flags = 0
        
        # 检查各种函数属性
        if getattr(function_entity, 'is_static', False):
            flags |= FunctionStatusFlags.FUNC_IS_STATIC
        
        if getattr(function_entity, 'is_virtual', False):
            flags |= FunctionStatusFlags.FUNC_IS_VIRTUAL
        
        if getattr(function_entity, 'is_pure_virtual', False):
            flags |= FunctionStatusFlags.FUNC_IS_PURE_VIRTUAL
        
        if getattr(function_entity, 'is_override', False):
            flags |= FunctionStatusFlags.FUNC_IS_OVERRIDE
        
        if getattr(function_entity, 'is_final', False):
            flags |= FunctionStatusFlags.FUNC_IS_FINAL
        
        if getattr(function_entity, 'is_const', False):
            flags |= FunctionStatusFlags.FUNC_IS_CONST
        
        # 检查特殊函数类型
        if function_entity.name.startswith('operator'):
            flags |= FunctionStatusFlags.FUNC_IS_OPERATOR_OVERLOAD
        
        # 检查构造函数/析构函数（通过名称判断）
        if function_entity.name.startswith('~'):
            flags |= FunctionStatusFlags.FUNC_IS_DESTRUCTOR
        elif hasattr(function_entity, 'parent_class') and function_entity.parent_class:
            # 如果函数名与类名相同，可能是构造函数
            parent_class = self.repo.get_node(function_entity.parent_class) if hasattr(self, 'repo') else None
            if parent_class and function_entity.name == parent_class.name:
                flags |= FunctionStatusFlags.FUNC_IS_CONSTRUCTOR
        
        return flags 

    def _build_templates_entities(self) -> Dict[str, Any]:
        """构建模板实体信息"""
        # 从分析器获取模板信息（需要在主分析器中传递）
        if hasattr(self, 'template_analyzer') and self.template_analyzer:
            return self.template_analyzer.get_templates_json()
        
        # 备用：从repo中查找模板相关实体
        templates = {}
        
        for usr_id, entity in self.repo.nodes.items():
            # 检查实体是否是模板
            if hasattr(entity, 'template_params') and entity.template_params:
                entity_type = "unknown"
                if isinstance(entity, Function):
                    entity_type = "function"
                elif isinstance(entity, Class):
                    entity_type = "class"
                
                templates[usr_id] = {
                    "name": entity.name,
                    "qualified_name": entity.qualified_name,
                    "template_type": entity_type,
                    "parameters": [
                        {
                            "name": param,
                            "type": "type",  # 简化处理
                            "default_value": None,
                            "constraints": [],
                            "is_variadic": False
                        }
                        for param in entity.template_params
                    ],
                    "specializations": [],
                    "instantiations": [],
                    "concepts_constraints": [],
                    "file_path": entity.file_path,
                    "start_line": entity.start_line
                }
        
        return templates 

    def _build_operators_entities(self, repo: NodeRepository) -> Dict[str, Any]:
        """构建操作符重载实体信息"""
        operators = {}
        
        for usr_id, entity in repo.nodes.items():
            if isinstance(entity, Function) and self._is_operator_function(entity):
                operator_info = self._analyze_operator_function(entity)
                
                if operator_info:
                    operators[usr_id] = {
                        "name": entity.name,
                        "qualified_name": entity.qualified_name,
                        "operator_symbol": operator_info['symbol'],
                        "operator_type": operator_info['type'],
                        "arity": operator_info['arity'],
                        "is_member": operator_info['is_member'],
                        "is_friend": operator_info.get('is_friend', False),
                        "return_type": entity.return_type,
                        "parameters": entity.parameters,
                        "usr": usr_id,
                        "file_path": entity.file_path,
                        "start_line": entity.start_line,
                        "end_line": entity.end_line,
                        "access_specifier": getattr(entity, 'access_specifier', 'public'),
                        "is_virtual": getattr(entity, 'is_virtual', False),
                        "signature": entity.signature
                    }
        
        return operators

    def _is_operator_function(self, function: Function) -> bool:
        """检查函数是否是操作符重载"""
        return function.name.startswith('operator') or 'operator' in function.qualified_name

    def _analyze_operator_function(self, function: Function) -> Optional[Dict[str, Any]]:
        """分析操作符重载函数"""
        if not self._is_operator_function(function):
            return None
        
        # 提取操作符符号
        operator_symbol = self._extract_operator_symbol(function.name)
        if not operator_symbol:
            return None
        
        # 分析操作符类型和元数
        operator_type = self._classify_operator_type(operator_symbol)
        arity = self._determine_operator_arity(operator_symbol, function)
        
        # 判断是否是成员函数
        is_member = hasattr(function, 'parent_class') and function.parent_class
        
        return {
            'symbol': operator_symbol,
            'type': operator_type,
            'arity': arity,
            'is_member': is_member,
            'is_friend': getattr(function, 'is_friend', False)
        }

    def _extract_operator_symbol(self, function_name: str) -> Optional[str]:
        """从函数名中提取操作符符号"""
        if not function_name.startswith('operator'):
            return None
        
        # 移除"operator"前缀
        symbol_part = function_name[8:]  # len("operator") = 8
        
        # 常见操作符映射
        operator_symbols = {
            '+': '+', '-': '-', '*': '*', '/': '/', '%': '%',
            '=': '=', '==': '==', '!=': '!=', '<': '<', '>': '>',
            '<=': '<=', '>=': '>=', '&&': '&&', '||': '||',
            '&': '&', '|': '|', '^': '^', '~': '~',
            '<<': '<<', '>>': '>>', '++': '++', '--': '--',
            '+=': '+=', '-=': '-=', '*=': '*=', '/=': '/=',
            '[]': '[]', '()': '()', 'new': 'new', 'delete': 'delete'
        }
        
        # 直接查找
        if symbol_part in operator_symbols:
            return symbol_part
        
        # 处理特殊情况
        if symbol_part == '()':
            return '()'
        elif symbol_part == '[]':
            return '[]'
        elif symbol_part in ['new', 'delete']:
            return symbol_part
        
        return symbol_part  # 返回原始符号

    def _classify_operator_type(self, operator_symbol: str) -> str:
        """分类操作符类型"""
        arithmetic_ops = {'+', '-', '*', '/', '%', '++', '--'}
        comparison_ops = {'==', '!=', '<', '>', '<=', '>='}
        logical_ops = {'&&', '||', '!'}
        bitwise_ops = {'&', '|', '^', '~', '<<', '>>'}
        assignment_ops = {'=', '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=', '<<=', '>>='}
        
        if operator_symbol in arithmetic_ops:
            return 'arithmetic'
        elif operator_symbol in comparison_ops:
            return 'comparison'
        elif operator_symbol in logical_ops:
            return 'logical'
        elif operator_symbol in bitwise_ops:
            return 'bitwise'
        elif operator_symbol in assignment_ops:
            return 'assignment'
        elif operator_symbol == '[]':
            return 'subscript'
        elif operator_symbol == '()':
            return 'function_call'
        elif operator_symbol in ['new', 'delete']:
            return 'memory'
        else:
            return 'other'

    def _determine_operator_arity(self, operator_symbol: str, function: Function) -> str:
        """确定操作符的元数"""
        # 基于参数数量判断
        param_count = len(function.parameters)
        
        # 成员函数有隐式的this参数
        is_member = hasattr(function, 'parent_class') and function.parent_class
        
        if operator_symbol in ['++', '--', '+', '-', '*', '&', '!', '~']:
            # 这些操作符可以是一元或二元
            if is_member:
                # 成员函数：0个参数=一元，1个参数=二元
                return 'unary' if param_count == 0 else 'binary'
            else:
                # 非成员函数：1个参数=一元，2个参数=二元
                return 'unary' if param_count == 1 else 'binary'
        
        elif operator_symbol in ['=', '+=', '-=', '*=', '/=', '%=', '<<', '>>', '==', '!=', '<', '>', '<=', '>=']:
            return 'binary'
        
        elif operator_symbol == '()':
            return 'variadic'  # 函数调用操作符可以有任意数量的参数
        
        elif operator_symbol in ['new', 'delete']:
            return 'unary'
        
        else:
            # 根据参数数量推断
            if param_count == 0 or (param_count == 1 and not is_member):
                return 'unary'
            elif param_count == 1 or (param_count == 2 and not is_member):
                return 'binary'
            else:
                return 'variadic' 

    def _build_metadata_v24(self, project: Project, repo: NodeRepository) -> Dict[str, Any]:
        """构建符合v2.4规范的元数据"""
        stats = repo.get_statistics()
        file_stats = self.file_manager.get_statistics() if self.file_manager else {}
        
        return {
            "generated_at": datetime.now().isoformat(),
            "generator": "cpp_treesitter_v2.4",
            "project_name": project.name,
            "total_files": len(project.files),
            "analysis_time_seconds": 0,  # 可在分析器中计算实际时间
            "schema_version": self.SCHEMA_VERSION,  # 使用统一版本
            "parser_version": "tree-sitter",
            "features_enabled": [
                "usr_generation",
                "status_flags", 
                "file_mapping",
                "signature_keys",
                "type_inference",
                "overload_resolution",
                "class_methods",
                "data_validation",  # 新增功能
                "call_graph_consistency"  # 新增功能
            ],
            "performance_stats": stats.get('lock_performance', {}),
            "data_quality_score": self._calculate_data_quality_score(repo)
        }

    def _build_config_v24(self, project: Project) -> Dict[str, Any]:
        """构建符合v2.4规范的配置信息"""
        return {
            "parser_type": "tree-sitter",
            "features_enabled": [
                "template_analysis",
                "type_inference", 
                "operator_overloading",
                "parallel_processing",
                "data_validation",  # 新增
                "call_graph_consistency_check"  # 新增
            ],
            "analysis_mode": "two_phase",
            "max_workers": 1,  # 当前实现为单线程
            "project_root": project.name,
            "scan_directory": project.name,
            "include_function_body": True,
            "generate_usr": True,
            "use_file_mapping": True,
            "enable_data_validation": True,  # 新增配置
            "schema_version": self.SCHEMA_VERSION
        }
    
    def _calculate_data_quality_score(self, repo: NodeRepository) -> float:
        """计算数据质量分数"""
        issues = self._validate_data_consistency(repo)
        total_entities = len(repo.nodes)
        
        if total_entities == 0:
            return 1.0
        
        # 基于问题数量计算质量分数
        total_issues = (
            len(issues.get("orphaned_calls", [])) + 
            len(issues.get("invalid_references", []))
        )
        
        quality_score = max(0.0, 1.0 - (total_issues / total_entities))
        return round(quality_score, 3) 