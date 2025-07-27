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

import gc
import json
from dataclasses import asdict, is_dataclass, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
import re

from .data_structures import Project, NodeRepository, Entity, Function, Class, Namespace


class CustomJsonEncoder(json.JSONEncoder):
    """自定义JSON编码器，用于处理dataclass"""
    def default(self, o: Any) -> Any:
        if is_dataclass(o):
            # 使用快速字典转换，避免asdict()的递归开销
            return self._entity_to_dict_fast(o)
        return super().default(o)
    
    def _entity_to_dict_fast(self, entity) -> Dict[str, Any]:
        """快速实体转字典方法，避免asdict()递归开销"""
        if hasattr(entity, 'type'):
            # 基础Entity信息
            result = {
                "type": getattr(entity, 'type', ''),
                "name": getattr(entity, 'name', ''),
                "qualified_name": getattr(entity, 'qualified_name', ''),
                "file_path": getattr(entity, 'file_path', ''),
                "start_line": getattr(entity, 'start_line', 0),
                "end_line": getattr(entity, 'end_line', 0),
                "usr": getattr(entity, 'usr', '')
            }
            
            # 添加特定类型的字段
            if hasattr(entity, 'signature'):
                result["signature"] = getattr(entity, 'signature', '')
            if hasattr(entity, 'parameters'):
                result["parameters"] = getattr(entity, 'parameters', [])
            if hasattr(entity, 'return_type'):
                result["return_type"] = getattr(entity, 'return_type', '')
            if hasattr(entity, 'calls_to'):
                result["calls_to"] = getattr(entity, 'calls_to', [])
            if hasattr(entity, 'called_by'):
                result["called_by"] = getattr(entity, 'called_by', [])
            if hasattr(entity, 'base_classes'):
                result["base_classes"] = getattr(entity, 'base_classes', [])
            if hasattr(entity, 'methods'):
                result["methods"] = getattr(entity, 'methods', [])
            # ✅ 添加函数体内容导出
            if hasattr(entity, 'code_content'):
                result["code_content"] = getattr(entity, 'code_content', '')
            
            return result
        else:
            # 对于非Entity类型的dataclass，回退到简单处理
            return {field.name: getattr(entity, field.name, None) 
                    for field in fields(entity) if not field.name.startswith('_')}
    


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
        使用流式导出优化性能，避免一次性构建完整数据结构.
        
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
        
        logger.info("🏗️  开始流式构建和导出分析结果...")
        
        # 使用流式导出，边构建边写入
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"💾 准备写入JSON到: {path}")
        
        import time
        start_time = time.time()
        
        try:
            with open(path, 'w', encoding='utf-8') as f:
                # 开始JSON对象
                f.write('{\n')
                
                # 写入基础信息
                logger.info("📋 写入基础信息...")
                f.write(f'  "version": "{self.SCHEMA_VERSION}",\n')
                f.write('  "language": "cpp",\n')
                f.write(f'  "timestamp": "{datetime.now().isoformat()}",\n')
                
                # 写入文件映射
                f.write('  "file_mappings": ')
                json.dump(self._file_mappings, f, cls=CustomJsonEncoder, ensure_ascii=False)
                f.write(',\n')
                
                # 写入数据质量信息
                f.write('  "data_quality": ')
                data_quality = {
                    "consistency_check": consistency_issues["statistics"],
                    "validated_call_graph": True,
                    "total_entities": len(repo.nodes)
                }
                json.dump(data_quality, f, cls=CustomJsonEncoder, ensure_ascii=False)
                f.write(',\n')
                
                # 开始entities对象
                f.write('  "entities": {\n')
                
                # 流式写入函数实体
                logger.info("🔧 流式写入函数实体...")
                f.write('    "functions": ')
                self._write_functions_streaming(f, repo)
                f.write(',\n')
                
                # 流式写入类实体
                logger.info("🏛️  流式写入类实体...")
                f.write('    "classes": ')
                self._write_classes_streaming(f, repo)
                f.write(',\n')
                
                # 流式写入命名空间实体
                logger.info("🗂️  流式写入命名空间实体...")
                f.write('    "namespaces": ')
                self._write_namespaces_streaming(f, repo)
                f.write(',\n')
                
                # 写入其他实体（简化处理）
                f.write('    "templates": {},\n')
                f.write('    "operators": ')
                self._write_operators_streaming(f, repo)
                f.write(',\n')
                
                # 流式写入调用关系
                logger.info("🔗 流式写入调用关系...")
                f.write('    "call_relations": ')
                self._write_call_relations_streaming(f, repo)
                f.write(',\n')
                
                # 写入其他关系
                f.write('    "inheritance_relations": {},\n')
                f.write('    "type_inference_info": {}\n')
                
                # 结束entities对象
                f.write('  },\n')
                
                # 写入元数据
                logger.info("📊 写入元数据...")
                import time
                metadata_start = time.time()
                f.write('  "metadata": ')
                metadata = self._build_metadata_v24(project, repo)
                json.dump(metadata, f, cls=CustomJsonEncoder, ensure_ascii=False)
                f.write(',\n')
                metadata_time = time.time() - metadata_start
                logger.info(f"   ✅ 元数据写入完成，耗时: {metadata_time:.2f}秒")
                
                # 写入配置
                logger.info("⚙️ 写入配置信息...")
                config_start = time.time()
                f.write('  "config": ')
                config = self._build_config_v24(project)
                json.dump(config, f, cls=CustomJsonEncoder, ensure_ascii=False)
                f.write(',\n')
                config_time = time.time() - config_start
                logger.info(f"   ✅ 配置信息写入完成，耗时: {config_time:.2f}秒")
                
                # 流式写入项目调用图
                logger.info("🌐 流式写入项目调用图...")
                f.write('  "project_call_graph": {\n')
                
                # 项目信息
                logger.info("   📈 构建项目信息...")
                project_info_start = time.time()
                project_info = {
                    "name": project.name,
                    "total_files": len(project.files),
                    "total_functions": len(project.functions),
                    "total_classes": len(project.classes),
                    "total_namespaces": len(project.namespaces)
                }
                f.write('    "project_info": ')
                json.dump(project_info, f, cls=CustomJsonEncoder, ensure_ascii=False)
                f.write(',\n')
                project_info_time = time.time() - project_info_start
                logger.info(f"   ✅ 项目信息构建完成，耗时: {project_info_time:.2f}秒")
                
                # 模块信息 - 流式写入
                logger.info("   📈 构建模块信息...")
                modules_start = time.time()
                f.write('    "modules": ')
                self._write_modules_info_streaming(f, project, repo)
                f.write(',\n')
                modules_time = time.time() - modules_start
                logger.info(f"   ✅ 模块信息构建完成: {len(project.files)} 个模块，耗时: {modules_time:.2f}秒")
                
                # 全局调用图 - 分批写入
                logger.info("   📈 写入全局调用图...")
                f.write('    "global_call_graph": ')
                self._write_call_graph_streaming(f, validated_calls_to)
                f.write(',\n')
                
                # 反向调用图 - 分批写入
                logger.info("   📈 写入反向调用图...")
                f.write('    "reverse_call_graph": ')
                self._write_call_graph_streaming(f, validated_called_by)
                f.write('\n')
                
                f.write('  },\n')
                
                # 简化的其他部分
                f.write('  "oop_analysis": {},\n')
                f.write('  "cpp_analysis": {},\n')
                
                # 摘要
                f.write('  "summary": ')
                summary = self._build_summary(project, repo)
                json.dump(summary, f, cls=CustomJsonEncoder, ensure_ascii=False)
                f.write('\n')
                
                # 结束JSON对象
                f.write('}\n')
            
            export_time = time.time() - start_time
            file_size_mb = path.stat().st_size / (1024 * 1024)
            logger.info(f"✅ 流式导出完成: {file_size_mb:.2f}MB, 耗时: {export_time:.2f}秒")
            
        except Exception as e:
            export_time = time.time() - start_time
            logger.error(f"❌ 流式导出失败 (耗时: {export_time:.2f}秒): {e}")
            raise

    def export_nodes_json(self, repo: NodeRepository, output_path: str):
        """
        导出全局节点映射JSON文件 (高性能流式版本).
        
        :param repo: 包含所有节点的NodeRepository.
        :param output_path: 输出文件路径.
        """
        from .logger import Logger
        logger = Logger.get_logger()
        
        logger.info("🔗 开始高性能流式导出全局nodes映射...")
        
        # 修复：确保文件映射已初始化
        if not self._file_mappings:
            logger.info("🔧 初始化文件映射...")
            self._initialize_file_mappings(repo)
            logger.info(f"✅ 文件映射初始化完成: {len(self._file_mappings)} 个文件")
            
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"💾 准备写入JSON到: {path}")
        logger.info(f"   - 总节点数量: {len(repo.nodes)}")
        
        import time
        start_time = time.time()
        
        try:
            with open(path, 'w', encoding='utf-8', buffering=65536) as f:  # 增大缓冲区
                # 写入JSON开始
                f.write('{\n')
                
                # 写入基础信息
                logger.info("📋 写入基础元数据...")
                f.write('  "version": "')
                f.write(self.SCHEMA_VERSION)
                f.write('",\n')
                
                f.write('  "timestamp": "')
                f.write(datetime.now().isoformat())
                f.write('",\n')
                
                f.write('  "node_type": "global_entities",\n')
                f.write(f'  "total_entities": {len(repo.nodes)},\n')
                
                # 写入统计信息（使用正确的JSON格式）
                stats = repo.get_statistics()
                f.write('  "statistics": ')
                json.dump({
                    "total_entities": stats.get("total_entities", 0),
                    "by_type": stats.get("by_type", {}),
                    "call_relationships": stats.get("call_relationships", 0),
                    "files_analyzed": stats.get("files_analyzed", 0)
                }, f, cls=CustomJsonEncoder, ensure_ascii=False)
                f.write(',\n')
                
                # 开始entities对象
                f.write('  "entities": {\n')
                
                # 流式写入实体 - 优化版本
                logger.info("🔄 开始高性能流式写入实体...")
                entity_count = 0
                total_entities = len(repo.nodes)
                
                # 预先获取所有实体以避免重复访问
                entities_list = list(repo.nodes.items())
                
                for i, (usr_id, entity) in enumerate(entities_list):
                    try:
                        # 每100个实体输出进度并回收内存（更频繁）
                        if entity_count % 100 == 0:
                            if entity_count % 1000 == 0:  # 每1000个输出详细进度
                                logger.info(f"📊 处理进度: {entity_count}/{total_entities} ({entity_count/total_entities*100:.1f}%)")
                            # 强制垃圾回收（更频繁）
                            gc.collect()
                        
                        # 清理USR以防止JSON格式错误
                        sanitized_usr = self._sanitize_usr_for_json_key(usr_id)
                        
                        # 使用字符串拼接而不是JSON序列化
                        f.write(f'    "{sanitized_usr}": {{\n')
                        f.write(f'      "type": "{entity.type}",\n')
                        f.write('      "data": ')
                        
                        # 快速转换实体为字典并写入
                        entity_data = self._entity_to_dict_fast(entity)
                        # 使用更快的JSON序列化
                        json.dump(entity_data, f, cls=None, separators=(',', ':'), ensure_ascii=False)
                        
                        # 如果不是最后一个实体，添加逗号
                        if i < total_entities - 1:
                            f.write('\n    },\n')
                        else:
                            f.write('\n    }\n')
                            
                        entity_count += 1
                        
                    except Exception as e:
                        logger.error(f"❌ 处理实体 {usr_id} 时出错: {e}")
                        # 继续处理其他实体
                        if i < total_entities - 1:
                            f.write('\n    },\n')
                        else:
                            f.write('\n    }\n')
                        continue
                
                # 结束entities对象和JSON
                f.write('  }\n')
                f.write('}\n')
                
                logger.info(f"✅ 实体写入完成: {entity_count} 个实体")
            
            export_time = time.time() - start_time
            file_size_mb = path.stat().st_size / (1024 * 1024)
            logger.info(f"✅ 全局nodes映射导出完成: {file_size_mb:.2f}MB, 耗时: {export_time:.2f}秒")
            
        except Exception as e:
            export_time = time.time() - start_time
            logger.error(f"❌ 全局nodes映射导出失败 (耗时: {export_time:.2f}秒): {e}")
            raise

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

    def _write_modules_info_streaming(self, f, project: Project, repo: NodeRepository):
        """流式写入模块信息，避免内存峰值"""
        from .logger import Logger
        logger = Logger.get_logger()
        import time
        
        total_files = len(project.files)
        logger.info(f"      📈 开始处理 {total_files} 个文件的模块信息...")
        
        start_time = time.time()
        batch_size = 200  # 增加批次大小以减少日志频率
        
        f.write('{\n')
        
        # 高性能预处理：构建所有必要的映射
        logger.info(f"      🔄 高性能预处理实体数据...")
        preprocess_start = time.time()
        
        # 1. 预建文件ID映射 - 避免重复的路径查找
        file_id_map = {}
        sorted_files = sorted(project.files)  # 排序确保一致性
        for i, file_path in enumerate(sorted_files):
            file_id_map[file_path] = f"f{i+1:03d}"  # f001, f002, f003...
        
        # 2. 批量获取所有实体数据
        file_entities_map = {}
        entities_processed = 0
        for file_path in project.files:
            entities = repo.get_nodes_by_file(file_path)
            entities_processed += len(entities)
            file_entities_map[file_path] = {
                "functions": [e.usr for e in entities if isinstance(e, Function)],
                "classes": [e.usr for e in entities if isinstance(e, Class)],
                "namespaces": [e.usr for e in entities if isinstance(e, Namespace)]
            }
        
        preprocess_time = time.time() - preprocess_start
        logger.info(f"      ✅ 预处理完成: {entities_processed} 个实体，耗时: {preprocess_time:.2f}秒")
        
        # 高速写入阶段
        write_start = time.time()
        
        # 按文件顺序快速写入
        for i, file_path in enumerate(project.files):
            # 减少日志频率以提高性能
            if i % batch_size == 0 and i > 0:
                elapsed = time.time() - write_start
                rate = i / elapsed if elapsed > 0 else 0
                remaining = (total_files - i) / rate if rate > 0 else 0
                logger.info(f"      📊 写入进度: {i}/{total_files} ({i/total_files*100:.1f}%) - 速度: {rate:.1f}/秒 - 剩余: {remaining:.1f}秒")
            
            # 使用预建的映射，避免昂贵的路径查找
            file_id = file_id_map.get(file_path, f"f{i+1:03d}")
            
            # 使用预处理的数据
            module_info = {
                "file_path": file_path,
                **file_entities_map[file_path]
            }
            
            f.write(f'      "{file_id}": ')
            json.dump(module_info, f, ensure_ascii=False, separators=(',', ':'))
            
            if i < total_files - 1:
                f.write(',')
            f.write('\n')
        
        f.write('    }')
        
        total_time = time.time() - start_time
        write_time = time.time() - write_start
        logger.info(f"      ✅ 模块信息完成: {total_files} 个文件，总耗时: {total_time:.2f}秒，写入耗时: {write_time:.2f}秒")

    def _build_modules_info(self, project: Project, repo: NodeRepository) -> Dict[str, Any]:
        """构建模块信息（保留用于向后兼容）"""
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
        """优化的文件路径到ID映射方法"""
        # 确保文件映射已初始化
        if not self._reverse_file_mappings:
            return "f001"
        
        # 快速查找 - 直接键匹配
        file_id = self._reverse_file_mappings.get(file_path)
        if file_id:
            return file_id
        
        # 如果直接查找失败，使用缓存的标准化路径查找
        if not hasattr(self, '_normalized_path_cache'):
            self._normalized_path_cache = {}
        
        # 检查缓存
        if file_path in self._normalized_path_cache:
            return self._normalized_path_cache[file_path]
        
        # 标准化当前路径并查找
        try:
            normalized_path = str(Path(file_path).resolve()).replace('\\', '/')
            for path, fid in self._reverse_file_mappings.items():
                # 只有在必要时才标准化映射中的路径
                if '\\' in path or not path.startswith('/'):
                    mapped_normalized = str(Path(path).resolve()).replace('\\', '/')
                    if mapped_normalized == normalized_path:
                        self._normalized_path_cache[file_path] = fid
                        return fid
        except (OSError, ValueError):
            # 路径标准化失败，继续使用默认值
            pass
        
        # 缓存未找到的结果，避免重复计算
        self._normalized_path_cache[file_path] = "f001"
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
        """将单个实体对象转换为字典 - 高性能版本"""
        # 手动构建字典，避免asdict()的递归开销
        entity_dict = {
            "id": entity.id,
            "usr": entity.usr,
            "name": entity.name,
            "qualified_name": entity.qualified_name,
            "file_path": entity.file_path,
            "start_line": entity.start_line,
            "end_line": entity.end_line,
            "type": entity.type,
            "is_definition": entity.is_definition
        }
        
        # 只为特定类型添加额外字段
        if isinstance(entity, Function):
            entity_dict.update({
                "signature": entity.signature,
                "return_type": entity.return_type,
                "parameters": entity.parameters,
                "calls_to": entity.calls_to,
                "called_by": entity.called_by,
                "complexity": entity.complexity,
                "is_static": entity.is_static,
                "is_virtual": entity.is_virtual,
                "access_specifier": entity.access_specifier,
                "parent_class": entity.parent_class,
                "cpp_extensions": {
                    "qualified_name": entity.qualified_name,
                    "namespace": "::".join(entity.qualified_name.split("::")[:-1]) if "::" in entity.qualified_name else "",
                    "function_status_flags": 0,
                    "access_specifier": entity.access_specifier,
                    "return_type": entity.return_type or "",
                    "usr": entity.usr
                }
            })
        elif isinstance(entity, Class):
            entity_dict.update({
                "base_classes": entity.base_classes,
                "derived_classes": entity.derived_classes,
                "methods": entity.methods,
                "fields": entity.fields,
                "is_struct": entity.is_struct,
                "is_abstract": entity.is_abstract,
                "is_template": entity.is_template,
                "parent_namespace": entity.parent_namespace
            })
        elif isinstance(entity, Namespace):
            entity_dict.update({
                "parent_namespace": entity.parent_namespace,
                "children": getattr(entity, 'children', [])
            })
        
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

    def _sanitize_usr_for_json_key(self, usr: str) -> str:
        """
        清理USR以用作JSON键值，移除可能破坏JSON格式的字符
        
        Args:
            usr: 原始USR字符串
            
        Returns:
            清理后的USR字符串，适合用作JSON键值
        """
        if not usr:
            return "unknown_usr"
        
        # 移除或替换可能破坏JSON的字符
        sanitized = usr.replace('\n', ' ')  # 将换行符替换为空格
        sanitized = sanitized.replace('\r', ' ')  # 将回车符替换为空格
        sanitized = sanitized.replace('\t', ' ')  # 将制表符替换为空格
        sanitized = re.sub(r'\s+', ' ', sanitized)  # 将多个空格合并为单个空格
        sanitized = sanitized.strip()  # 移除首尾空格
        
        # 移除可能的控制字符
        sanitized = ''.join(char for char in sanitized if ord(char) >= 32 or char in [' '])
        
        # 确保不为空
        if not sanitized:
            return "unknown_usr"
            
        return sanitized

    def _write_functions_streaming(self, f, repo: NodeRepository):
        """流式写入函数实体，分批处理避免内存峰值"""
        from .logger import Logger
        logger = Logger.get_logger()
        import time
        
        f.write('{\n')
        functions = [node for node in repo.nodes.values() if node.type == 'function']
        total_functions = len(functions)
        
        logger.info(f"   📈 开始处理 {total_functions} 个函数实体...")
        start_time = time.time()
        batch_size = 100
        
        for i, func in enumerate(functions):
            # 批次进度日志
            if i % batch_size == 0 and i > 0:
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                remaining = (total_functions - i) / rate if rate > 0 else 0
                logger.info(f"   📊 函数进度: {i}/{total_functions} ({i/total_functions*100:.1f}%) - 处理速度: {rate:.1f}/秒 - 预估剩余: {remaining:.1f}秒")
                
            # 清理USR以防止JSON格式错误
            sanitized_usr = self._sanitize_usr_for_json_key(func.usr)
            f.write(f'      "{sanitized_usr}": ')
            func_data = self._build_function_entity(func, repo)
            json.dump(func_data, f, cls=CustomJsonEncoder, ensure_ascii=False)
            
            if i < total_functions - 1:
                f.write(',')
            f.write('\n')
            
            # 每处理100个函数释放一次内存
            if i % 100 == 0:
                import gc
                gc.collect()
        
        f.write('    }')
        total_time = time.time() - start_time
        logger.info(f"   ✅ 函数实体写入完成: {total_functions} 个，耗时: {total_time:.2f}秒")

    def _write_classes_streaming(self, f, repo: NodeRepository):
        """流式写入类实体，分批处理避免内存峰值"""
        from .logger import Logger
        logger = Logger.get_logger()
        import time
        
        f.write('{\n')
        classes = [node for node in repo.nodes.values() if node.type == 'class']
        total_classes = len(classes)
        
        logger.info(f"   📈 开始处理 {total_classes} 个类实体...")
        start_time = time.time()
        batch_size = 50
        
        for i, cls in enumerate(classes):
            # 批次进度日志
            if i % batch_size == 0 and i > 0:
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                remaining = (total_classes - i) / rate if rate > 0 else 0
                logger.info(f"   📊 类进度: {i}/{total_classes} ({i/total_classes*100:.1f}%) - 处理速度: {rate:.1f}/秒 - 预估剩余: {remaining:.1f}秒")
                
            # 清理USR以防止JSON格式错误
            sanitized_usr = self._sanitize_usr_for_json_key(cls.usr)
            f.write(f'      "{sanitized_usr}": ')
            cls_data = self._build_class_entity(cls, repo)
            json.dump(cls_data, f, cls=CustomJsonEncoder, ensure_ascii=False)
            
            if i < total_classes - 1:
                f.write(',')
            f.write('\n')
            
            # 内存管理
            if i % 50 == 0:
                import gc
                gc.collect()
        
        f.write('    }')
        total_time = time.time() - start_time
        logger.info(f"   ✅ 类实体写入完成: {total_classes} 个，耗时: {total_time:.2f}秒")

    def _write_namespaces_streaming(self, f, repo: NodeRepository):
        """流式写入命名空间实体，分批处理避免内存峰值"""
        from .logger import Logger
        logger = Logger.get_logger()
        import time
        
        f.write('{\n')
        namespaces = [node for node in repo.nodes.values() if node.type == 'namespace']
        total_namespaces = len(namespaces)
        
        logger.info(f"   📈 开始处理 {total_namespaces} 个命名空间实体...")
        start_time = time.time()
        
        for i, ns in enumerate(namespaces):
            # 清理USR以防止JSON格式错误
            sanitized_usr = self._sanitize_usr_for_json_key(ns.usr)
            f.write(f'      "{sanitized_usr}": ')
            ns_data = self._build_namespace_entity(ns, repo)
            json.dump(ns_data, f, cls=CustomJsonEncoder, ensure_ascii=False)
            
            if i < total_namespaces - 1:
                f.write(',')
            f.write('\n')
        
        f.write('    }')
        total_time = time.time() - start_time
        logger.info(f"   ✅ 命名空间实体写入完成: {total_namespaces} 个，耗时: {total_time:.2f}秒")

    def _write_operators_streaming(self, f, repo: NodeRepository):
        """流式写入操作符实体"""
        from .logger import Logger
        logger = Logger.get_logger()
        import time
        
        logger.info("   📈 开始处理操作符实体...")
        start_time = time.time()
        
        operators = self._build_operators_entities(repo)
        json.dump(operators, f, cls=CustomJsonEncoder, ensure_ascii=False)
        
        total_time = time.time() - start_time
        logger.info(f"   ✅ 操作符实体写入完成: {len(operators)} 个，耗时: {total_time:.2f}秒")

    def _write_call_relations_streaming(self, f, repo: NodeRepository):
        """流式写入调用关系，分批处理大量数据"""
        from .logger import Logger
        logger = Logger.get_logger()
        import time
        
        f.write('[\n')
        
        # 获取所有调用关系
        logger.info("   📈 开始收集调用关系...")
        collect_start = time.time()
        
        call_relations = []
        for node in repo.nodes.values():
            if hasattr(node, 'calls_to') and node.calls_to:
                for callee_usr in node.calls_to:
                    call_relations.append({
                        "caller_usr": node.usr,
                        "callee_usr": callee_usr,
                        "call_type": "direct"
                    })
        
        collect_time = time.time() - collect_start
        total_relations = len(call_relations)
        logger.info(f"   📊 调用关系收集完成: {total_relations} 个关系，耗时: {collect_time:.2f}秒")
        
        logger.info(f"   📈 开始写入 {total_relations} 个调用关系...")
        write_start = time.time()
        batch_size = 500  # 分批处理，减少内存压力
        
        for i in range(0, total_relations, batch_size):
            batch = call_relations[i:i + batch_size]
            batch_start = time.time()
            
            for j, relation in enumerate(batch):
                f.write('      ')
                json.dump(relation, f, cls=CustomJsonEncoder, ensure_ascii=False)
                
                if i + j < total_relations - 1:
                    f.write(',')
                f.write('\n')
            
            # 批次进度日志
            batch_time = time.time() - batch_start
            processed = i + len(batch)
            elapsed = time.time() - write_start
            rate = processed / elapsed if elapsed > 0 else 0
            remaining = (total_relations - processed) / rate if rate > 0 else 0
            
            logger.info(f"   📊 调用关系进度: {processed}/{total_relations} ({processed/total_relations*100:.1f}%) - 批次耗时: {batch_time:.2f}秒 - 预估剩余: {remaining:.1f}秒")
            
            # 释放内存
            import gc
            gc.collect()
        
        f.write('    ]')
        total_time = time.time() - write_start
        logger.info(f"   ✅ 调用关系写入完成: {total_relations} 个，耗时: {total_time:.2f}秒")

    def _write_call_graph_streaming(self, f, call_graph_data):
        """流式写入调用图数据，分批处理"""
        from .logger import Logger
        logger = Logger.get_logger()
        import time
        
        f.write('{\n')
        
        total_entries = len(call_graph_data)
        logger.info(f"   📈 开始写入调用图: {total_entries} 个条目...")
        start_time = time.time()
        
        batch_size = 200  # 减小批次大小
        processed = 0
        
        for caller_usr, callees in call_graph_data.items():
            f.write(f'      "{caller_usr}": ')
            json.dump(list(callees), f, cls=CustomJsonEncoder, ensure_ascii=False)
            
            processed += 1
            if processed < total_entries:
                f.write(',')
            f.write('\n')
            
            # 定期输出进度和释放内存
            if processed % batch_size == 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = (total_entries - processed) / rate if rate > 0 else 0
                logger.info(f"   📊 调用图进度: {processed}/{total_entries} ({processed/total_entries*100:.1f}%) - 处理速度: {rate:.1f}/秒 - 预估剩余: {remaining:.1f}秒")
                
                import gc
                gc.collect()
        
        f.write('    }')
        total_time = time.time() - start_time
        logger.info(f"   ✅ 调用图写入完成: {total_entries} 个条目，耗时: {total_time:.2f}秒")

    def _build_function_entity(self, func_node, repo: NodeRepository) -> Dict[str, Any]:
        """构建单个函数实体数据"""
        return {
            "usr": func_node.usr,
            "name": func_node.name,
            "signature": getattr(func_node, 'signature', ''),
            "file_path": getattr(func_node, 'file_path', ''),
            "line_number": getattr(func_node, 'line_number', 0),
            "is_definition": getattr(func_node, 'is_definition', False),
            "parameters": getattr(func_node, 'parameters', []),
            "return_type": getattr(func_node, 'return_type', ''),
            "calls_to": list(getattr(func_node, 'calls_to', [])),
            "called_by": list(getattr(func_node, 'called_by', []))
        }

    def _build_class_entity(self, class_node, repo: NodeRepository) -> Dict[str, Any]:
        """构建单个类实体数据"""
        return {
            "usr": class_node.usr,
            "name": class_node.name,
            "file_path": getattr(class_node, 'file_path', ''),
            "line_number": getattr(class_node, 'line_number', 0),
            "is_definition": getattr(class_node, 'is_definition', False),
            "members": getattr(class_node, 'members', []),
            "methods": getattr(class_node, 'methods', []),
            "base_classes": getattr(class_node, 'base_classes', [])
        }

    def _build_namespace_entity(self, ns_node, repo: NodeRepository) -> Dict[str, Any]:
        """构建单个命名空间实体数据"""
        return {
            "usr": ns_node.usr,
            "name": ns_node.name,
            "file_path": getattr(ns_node, 'file_path', ''),
            "line_number": getattr(ns_node, 'line_number', 0),
            "members": getattr(ns_node, 'members', [])
        } 

    def _entity_to_dict_fast(self, entity: Entity) -> Dict[str, Any]:
        """将单个实体对象转换为字典 - 高性能版本"""
        # 手动构建字典，避免asdict()的递归开销
        entity_dict = {
            "id": entity.id,
            "usr": entity.usr,
            "name": entity.name,
            "qualified_name": entity.qualified_name,
            "file_path": entity.file_path,
            "start_line": entity.start_line,
            "end_line": entity.end_line,
            "type": entity.type,
            "is_definition": entity.is_definition
        }
        
        # 只为特定类型添加额外字段
        if isinstance(entity, Function):
            entity_dict.update({
                "signature": entity.signature,
                "return_type": entity.return_type,
                "parameters": entity.parameters,
                "calls_to": entity.calls_to,
                "called_by": entity.called_by,
                "complexity": entity.complexity,
                "is_static": entity.is_static,
                "is_virtual": entity.is_virtual,
                "access_specifier": entity.access_specifier,
                "parent_class": entity.parent_class,
                "code_content": getattr(entity, 'code_content', ''),  # ✅ 添加函数体内容
                "cpp_extensions": {
                    "qualified_name": entity.qualified_name,
                    "namespace": "::".join(entity.qualified_name.split("::")[:-1]) if "::" in entity.qualified_name else "",
                    "function_status_flags": 0,
                    "access_specifier": entity.access_specifier,
                    "return_type": entity.return_type or "",
                    "usr": entity.usr
                }
            })
        elif isinstance(entity, Class):
            entity_dict.update({
                "base_classes": entity.base_classes,
                "derived_classes": entity.derived_classes,
                "methods": entity.methods,
                "fields": entity.fields,
                "is_struct": entity.is_struct,
                "is_abstract": entity.is_abstract,
                "is_template": entity.is_template,
                "parent_namespace": entity.parent_namespace
            })
        elif isinstance(entity, Namespace):
            entity_dict.update({
                "parent_namespace": entity.parent_namespace,
                "children": getattr(entity, 'children', [])
            })
        
        return entity_dict 