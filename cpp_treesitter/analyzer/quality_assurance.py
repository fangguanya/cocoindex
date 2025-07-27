"""
C++ 代码分析质量保证模块

该模块提供全面的质量保证和验证机制，确保分析结果的正确性和完整性。

主要功能：
1. 解析完整性验证 - 确保所有函数、类、调用都被正确提取
2. 函数体验证 - 确保所有函数体都被保存和导出
3. 关系验证 - 验证调用关系和继承关系的完整性和正确性
4. 质量报告 - 生成详细的质量指标和建议
"""

from tree_sitter import Node
from typing import Dict, List, Tuple, Set, Any, Optional
import json
import time
from pathlib import Path
from dataclasses import dataclass, field

from .data_structures import NodeRepository, Function, Class, Entity
from .logger import Logger


@dataclass
class CompletenessReport:
    """完整性验证报告"""
    # AST统计
    ast_function_definitions: int = 0
    ast_function_declarations: int = 0
    ast_class_definitions: int = 0
    ast_class_declarations: int = 0
    ast_call_expressions: int = 0
    ast_inheritance_declarations: int = 0
    
    # 提取统计
    extracted_functions: int = 0
    extracted_classes: int = 0
    extracted_call_relationships: int = 0
    extracted_inheritance_relationships: int = 0
    
    # 覆盖率
    function_extraction_rate: float = 0.0
    class_extraction_rate: float = 0.0
    call_extraction_rate: float = 0.0
    inheritance_extraction_rate: float = 0.0
    
    # 遗漏列表
    missing_functions: List[Dict[str, Any]] = field(default_factory=list)
    missing_classes: List[Dict[str, Any]] = field(default_factory=list)
    missing_calls: List[Dict[str, Any]] = field(default_factory=list)
    missing_inheritance: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class FunctionBodyReport:
    """函数体验证报告"""
    total_function_definitions: int = 0
    functions_with_body: int = 0
    functions_without_body: int = 0
    empty_function_bodies: int = 0
    
    functions_missing_body: List[Dict[str, Any]] = field(default_factory=list)
    functions_with_empty_body: List[Dict[str, Any]] = field(default_factory=list)
    
    body_preservation_rate: float = 0.0


@dataclass
class RelationshipReport:
    """关系验证报告"""
    total_call_relationships: int = 0
    bidirectional_consistent_calls: int = 0
    orphaned_call_references: int = 0
    
    total_inheritance_relationships: int = 0
    valid_inheritance_references: int = 0
    orphaned_inheritance_references: int = 0
    
    call_consistency_rate: float = 0.0
    inheritance_validity_rate: float = 0.0
    
    orphaned_calls: List[Dict[str, Any]] = field(default_factory=list)
    invalid_inheritance: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class QualityAssuranceReport:
    """综合质量保证报告"""
    timestamp: str = ""
    analysis_time_seconds: float = 0.0
    
    completeness: CompletenessReport = field(default_factory=CompletenessReport)
    function_bodies: FunctionBodyReport = field(default_factory=FunctionBodyReport)
    relationships: RelationshipReport = field(default_factory=RelationshipReport)
    
    overall_quality_score: float = 0.0
    recommendations: List[str] = field(default_factory=list)


class CompletenessValidator:
    """完整性验证器 - 通过AST统计验证解析完整性"""
    
    def __init__(self, repo: NodeRepository):
        self.repo = repo
        self.logger = Logger.get_logger()
    
    def validate_completeness(self, file_contents: Dict[str, bytes], parsed_trees: Dict[str, Node]) -> CompletenessReport:
        """验证解析完整性"""
        self.logger.info("🔍 开始解析完整性验证...")
        start_time = time.time()
        
        report = CompletenessReport()
        
        # 统计AST中的实际数量
        for file_path, tree in parsed_trees.items():
            file_content = file_contents.get(file_path, b"")
            self._count_ast_entities(tree, file_path, file_content, report)
        
        # 统计提取的数量
        self._count_extracted_entities(report)
        
        # 计算覆盖率
        self._calculate_coverage_rates(report)
        
        # 识别遗漏的实体
        self._identify_missing_entities(parsed_trees, file_contents, report)
        
        validation_time = time.time() - start_time
        self.logger.info(f"✅ 完整性验证完成，耗时: {validation_time:.2f}秒")
        
        return report
    
    def _count_ast_entities(self, tree: Node, file_path: str, file_content: bytes, report: CompletenessReport):
        """统计AST中的实体数量"""
        
        def traverse_node(node: Node):
            # 统计函数定义和声明
            if node.type == 'function_definition':
                report.ast_function_definitions += 1
            elif node.type == 'function_declarator':
                # 检查是否为声明（不是定义的一部分）
                if not self._is_part_of_definition(node):
                    report.ast_function_declarations += 1
            
            # 统计类定义和声明
            elif node.type in ['class_specifier', 'struct_specifier']:
                if self._is_class_definition(node):
                    report.ast_class_definitions += 1
                else:
                    report.ast_class_declarations += 1
            
            # 统计函数调用
            elif node.type == 'call_expression':
                report.ast_call_expressions += 1
            
            # 统计继承声明
            elif node.type == 'base_class_clause':
                report.ast_inheritance_declarations += 1
            
            # 递归遍历子节点
            for child in node.children:
                traverse_node(child)
        
        traverse_node(tree)
    
    def _is_part_of_definition(self, node: Node) -> bool:
        """检查函数声明器是否是函数定义的一部分"""
        parent = node.parent
        while parent:
            if parent.type == 'function_definition':
                return True
            parent = parent.parent
        return False
    
    def _is_class_definition(self, node: Node) -> bool:
        """检查是否为类定义（有类体）"""
        for child in node.children:
            if child.type == 'field_declaration_list':
                return True
        return False
    
    def _count_extracted_entities(self, report: CompletenessReport):
        """统计提取的实体数量"""
        for entity in self.repo.get_all_nodes():
            if isinstance(entity, Function):
                report.extracted_functions += 1
            elif isinstance(entity, Class):
                report.extracted_classes += 1
        
        # 统计调用关系
        for usr, relationships in self.repo.call_relationships['calls_to'].items():
            report.extracted_call_relationships += len(relationships)
        
        # 统计继承关系
        for entity in self.repo.get_all_nodes():
            if isinstance(entity, Class) and hasattr(entity, 'base_classes'):
                report.extracted_inheritance_relationships += len(entity.base_classes)
    
    def _calculate_coverage_rates(self, report: CompletenessReport):
        """计算覆盖率"""
        total_ast_functions = report.ast_function_definitions + report.ast_function_declarations
        if total_ast_functions > 0:
            report.function_extraction_rate = report.extracted_functions / total_ast_functions
        
        total_ast_classes = report.ast_class_definitions + report.ast_class_declarations
        if total_ast_classes > 0:
            report.class_extraction_rate = report.extracted_classes / total_ast_classes
        
        if report.ast_call_expressions > 0:
            report.call_extraction_rate = report.extracted_call_relationships / report.ast_call_expressions
        
        if report.ast_inheritance_declarations > 0:
            report.inheritance_extraction_rate = report.extracted_inheritance_relationships / report.ast_inheritance_declarations
    
    def _identify_missing_entities(self, parsed_trees: Dict[str, Node], file_contents: Dict[str, bytes], report: CompletenessReport):
        """识别遗漏的实体（简化实现）"""
        # 这里可以实现更详细的遗漏实体识别逻辑
        # 例如：找出AST中存在但未被提取的函数、类等
        
        # 计算遗漏数量
        total_ast_functions = report.ast_function_definitions + report.ast_function_declarations
        missing_function_count = total_ast_functions - report.extracted_functions
        if missing_function_count > 0:
            report.missing_functions = [{"count": missing_function_count, "details": "具体遗漏函数需要进一步分析"}]
        
        total_ast_classes = report.ast_class_definitions + report.ast_class_declarations
        missing_class_count = total_ast_classes - report.extracted_classes
        if missing_class_count > 0:
            report.missing_classes = [{"count": missing_class_count, "details": "具体遗漏类需要进一步分析"}]


class FunctionBodyValidator:
    """函数体验证器"""
    
    def __init__(self, repo: NodeRepository):
        self.repo = repo
        self.logger = Logger.get_logger()
    
    def validate_function_bodies(self) -> FunctionBodyReport:
        """验证函数体完整性"""
        self.logger.info("🔍 开始函数体验证...")
        start_time = time.time()
        
        report = FunctionBodyReport()
        
        # 统计所有函数定义
        for entity in self.repo.get_all_nodes():
            if isinstance(entity, Function):
                # 只检查函数定义，不检查声明
                if hasattr(entity, 'is_definition') and entity.is_definition:
                    report.total_function_definitions += 1
                    
                    # 检查是否有函数体
                    if hasattr(entity, 'code_content') and entity.code_content:
                        if entity.code_content.strip():
                            report.functions_with_body += 1
                        else:
                            report.empty_function_bodies += 1
                            report.functions_with_empty_body.append({
                                "usr": entity.usr,
                                "qualified_name": entity.qualified_name,
                                "file_path": entity.file_path,
                                "line": entity.start_line
                            })
                    else:
                        report.functions_without_body += 1
                        report.functions_missing_body.append({
                            "usr": entity.usr,
                            "qualified_name": entity.qualified_name,
                            "file_path": entity.file_path,
                            "line": entity.start_line
                        })
        
        # 计算保存率
        if report.total_function_definitions > 0:
            report.body_preservation_rate = report.functions_with_body / report.total_function_definitions
        
        validation_time = time.time() - start_time
        self.logger.info(f"✅ 函数体验证完成，耗时: {validation_time:.2f}秒")
        
        return report


class RelationshipValidator:
    """关系验证器"""
    
    def __init__(self, repo: NodeRepository):
        self.repo = repo
        self.logger = Logger.get_logger()
    
    def validate_relationships(self) -> RelationshipReport:
        """验证关系完整性和正确性"""
        self.logger.info("🔍 开始关系验证...")
        start_time = time.time()
        
        report = RelationshipReport()
        
        # 验证调用关系
        self._validate_call_relationships(report)
        
        # 验证继承关系
        self._validate_inheritance_relationships(report)
        
        validation_time = time.time() - start_time
        self.logger.info(f"✅ 关系验证完成，耗时: {validation_time:.2f}秒")
        
        return report
    
    def _validate_call_relationships(self, report: RelationshipReport):
        """验证调用关系"""
        calls_to = self.repo.call_relationships['calls_to']
        called_by = self.repo.call_relationships['called_by']
        
        # 统计总调用关系
        for caller, callees in calls_to.items():
            report.total_call_relationships += len(callees)
        
        # 检查双向一致性
        consistent_count = 0
        for caller, callees in calls_to.items():
            for callee in callees:
                # 检查反向关系是否存在
                if callee in called_by and caller in called_by[callee]:
                    consistent_count += 1
                else:
                    report.orphaned_calls.append({
                        "caller": caller,
                        "callee": callee,
                        "issue": "missing reverse relationship"
                    })
        
        report.bidirectional_consistent_calls = consistent_count
        report.orphaned_call_references = report.total_call_relationships - consistent_count
        
        if report.total_call_relationships > 0:
            report.call_consistency_rate = consistent_count / report.total_call_relationships
    
    def _validate_inheritance_relationships(self, report: RelationshipReport):
        """验证继承关系"""
        valid_count = 0
        
        for entity in self.repo.get_all_nodes():
            if isinstance(entity, Class) and hasattr(entity, 'base_classes'):
                for base_usr in entity.base_classes:
                    report.total_inheritance_relationships += 1
                    
                    # 检查基类是否存在
                    if base_usr in self.repo.nodes:
                        valid_count += 1
                    else:
                        report.invalid_inheritance.append({
                            "derived_class": entity.usr,
                            "missing_base_class": base_usr,
                            "issue": "base class not found in repository"
                        })
        
        report.valid_inheritance_references = valid_count
        report.orphaned_inheritance_references = report.total_inheritance_relationships - valid_count
        
        if report.total_inheritance_relationships > 0:
            report.inheritance_validity_rate = valid_count / report.total_inheritance_relationships


class QualityAssuranceReporter:
    """质量保证报告器"""
    
    def __init__(self):
        self.logger = Logger.get_logger()
    
    def generate_comprehensive_report(self, repo: NodeRepository, 
                                     file_contents: Dict[str, bytes], 
                                     parsed_trees: Dict[str, Node]) -> QualityAssuranceReport:
        """生成全面的质量保证报告"""
        self.logger.info("📊 开始生成质量保证报告...")
        start_time = time.time()
        
        # 创建验证器
        completeness_validator = CompletenessValidator(repo)
        function_body_validator = FunctionBodyValidator(repo)
        relationship_validator = RelationshipValidator(repo)
        
        # 执行各项验证
        completeness_report = completeness_validator.validate_completeness(file_contents, parsed_trees)
        function_body_report = function_body_validator.validate_function_bodies()
        relationship_report = relationship_validator.validate_relationships()
        
        # 生成综合报告
        report = QualityAssuranceReport()
        report.timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        report.analysis_time_seconds = time.time() - start_time
        report.completeness = completeness_report
        report.function_bodies = function_body_report
        report.relationships = relationship_report
        
        # 计算总体质量评分
        report.overall_quality_score = self._calculate_overall_quality_score(report)
        
        # 生成建议
        report.recommendations = self._generate_recommendations(report)
        
        total_time = time.time() - start_time
        self.logger.info(f"✅ 质量保证报告生成完成，耗时: {total_time:.2f}秒")
        
        return report
    
    def _calculate_overall_quality_score(self, report: QualityAssuranceReport) -> float:
        """计算总体质量评分 (0-100)"""
        scores = []
        
        # 完整性评分 (权重: 30%)
        completeness_score = (
            report.completeness.function_extraction_rate * 0.4 +
            report.completeness.class_extraction_rate * 0.3 +
            report.completeness.call_extraction_rate * 0.2 +
            report.completeness.inheritance_extraction_rate * 0.1
        ) * 30
        scores.append(completeness_score)
        
        # 函数体完整性评分 (权重: 25%)
        function_body_score = report.function_bodies.body_preservation_rate * 25
        scores.append(function_body_score)
        
        # 关系正确性评分 (权重: 25%)
        relationship_score = (
            report.relationships.call_consistency_rate * 0.7 +
            report.relationships.inheritance_validity_rate * 0.3
        ) * 25
        scores.append(relationship_score)
        
        # 数据质量评分 (权重: 20%)
        # 基于遗漏和错误的数量
        missing_functions = report.completeness.missing_functions if isinstance(report.completeness.missing_functions, list) else []
        missing_classes = report.completeness.missing_classes if isinstance(report.completeness.missing_classes, list) else []
        orphaned_calls = report.relationships.orphaned_calls if isinstance(report.relationships.orphaned_calls, list) else []
        invalid_inheritance = report.relationships.invalid_inheritance if isinstance(report.relationships.invalid_inheritance, list) else []
        
        total_entities = len(missing_functions) + len(missing_classes)
        total_errors = len(orphaned_calls) + len(invalid_inheritance)
        
        if total_entities + total_errors == 0:
            data_quality_score = 20
        else:
            # 简化评分逻辑
            error_rate = min(1.0, (total_errors) / max(1, total_entities + total_errors))
            data_quality_score = (1 - error_rate) * 20
        
        scores.append(data_quality_score)
        
        return round(sum(scores), 2)
    
    def _generate_recommendations(self, report: QualityAssuranceReport) -> List[str]:
        """生成改进建议"""
        recommendations = []
        
        # 完整性建议
        if report.completeness.function_extraction_rate < 0.95:
            recommendations.append(f"函数提取覆盖率仅为 {report.completeness.function_extraction_rate:.1%}，建议检查函数提取器的匹配规则")
        
        if report.completeness.class_extraction_rate < 0.95:
            recommendations.append(f"类提取覆盖率仅为 {report.completeness.class_extraction_rate:.1%}，建议检查类提取器的匹配规则")
        
        if report.completeness.call_extraction_rate < 0.80:
            recommendations.append(f"调用关系提取覆盖率仅为 {report.completeness.call_extraction_rate:.1%}，建议增强调用分析器")
        
        # 函数体建议
        if report.function_bodies.body_preservation_rate < 0.95:
            recommendations.append(f"函数体保存率仅为 {report.function_bodies.body_preservation_rate:.1%}，建议检查函数体提取逻辑")
        
        if hasattr(report.function_bodies, 'functions_without_body') and report.function_bodies.functions_without_body:
            func_without_body = report.function_bodies.functions_without_body
            if isinstance(func_without_body, list):
                recommendations.append(f"发现 {len(func_without_body)} 个函数定义缺少函数体，需要调查原因")
            else:
                recommendations.append(f"发现 {func_without_body} 个函数定义缺少函数体，需要调查原因")
        
        # 关系建议
        if report.relationships.call_consistency_rate < 0.98:
            recommendations.append(f"调用关系一致性仅为 {report.relationships.call_consistency_rate:.1%}，建议修复双向关系同步")
        
        if report.relationships.orphaned_inheritance_references > 0:
            recommendations.append(f"发现 {report.relationships.orphaned_inheritance_references} 个无效继承引用，建议检查USR生成逻辑")
        
        # 总体建议
        if report.overall_quality_score < 85:
            recommendations.append("总体质量评分较低，建议进行全面的系统优化")
        elif report.overall_quality_score >= 95:
            recommendations.append("分析质量优秀！继续保持高标准")
        
        return recommendations
    
    def export_report(self, report: QualityAssuranceReport, output_path: str):
        """导出质量保证报告"""
        self.logger.info(f"📝 导出质量保证报告到: {output_path}")
        
        # 转换为可序列化的字典
        report_dict = self._report_to_dict(report)
        
        # 写入JSON文件
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report_dict, f, ensure_ascii=False, indent=2)
        
        self.logger.info("✅ 质量保证报告导出完成")
    
    def _report_to_dict(self, report: QualityAssuranceReport) -> Dict[str, Any]:
        """将报告转换为字典格式"""
        return {
            "meta": {
                "timestamp": report.timestamp,
                "analysis_time_seconds": report.analysis_time_seconds,
                "overall_quality_score": report.overall_quality_score
            },
            "completeness": {
                "ast_statistics": {
                    "function_definitions": report.completeness.ast_function_definitions,
                    "function_declarations": report.completeness.ast_function_declarations,
                    "class_definitions": report.completeness.ast_class_definitions,
                    "class_declarations": report.completeness.ast_class_declarations,
                    "call_expressions": report.completeness.ast_call_expressions,
                    "inheritance_declarations": report.completeness.ast_inheritance_declarations
                },
                "extraction_statistics": {
                    "extracted_functions": report.completeness.extracted_functions,
                    "extracted_classes": report.completeness.extracted_classes,
                    "extracted_call_relationships": report.completeness.extracted_call_relationships,
                    "extracted_inheritance_relationships": report.completeness.extracted_inheritance_relationships
                },
                "coverage_rates": {
                    "function_extraction_rate": report.completeness.function_extraction_rate,
                    "class_extraction_rate": report.completeness.class_extraction_rate,
                    "call_extraction_rate": report.completeness.call_extraction_rate,
                    "inheritance_extraction_rate": report.completeness.inheritance_extraction_rate
                },
                "missing_entities": {
                    "functions": report.completeness.missing_functions,
                    "classes": report.completeness.missing_classes,
                    "calls": report.completeness.missing_calls,
                    "inheritance": report.completeness.missing_inheritance
                }
            },
            "function_bodies": {
                "statistics": {
                    "total_function_definitions": report.function_bodies.total_function_definitions,
                    "functions_with_body": report.function_bodies.functions_with_body,
                    "functions_without_body": report.function_bodies.functions_without_body,
                    "empty_function_bodies": report.function_bodies.empty_function_bodies,
                    "body_preservation_rate": report.function_bodies.body_preservation_rate
                },
                "issues": {
                    "functions_missing_body": report.function_bodies.functions_missing_body,
                    "functions_with_empty_body": report.function_bodies.functions_with_empty_body
                }
            },
            "relationships": {
                "call_relationships": {
                    "total": report.relationships.total_call_relationships,
                    "bidirectional_consistent": report.relationships.bidirectional_consistent_calls,
                    "orphaned_references": report.relationships.orphaned_call_references,
                    "consistency_rate": report.relationships.call_consistency_rate,
                    "orphaned_calls": report.relationships.orphaned_calls
                },
                "inheritance_relationships": {
                    "total": report.relationships.total_inheritance_relationships,
                    "valid_references": report.relationships.valid_inheritance_references,
                    "orphaned_references": report.relationships.orphaned_inheritance_references,
                    "validity_rate": report.relationships.inheritance_validity_rate,
                    "invalid_inheritance": report.relationships.invalid_inheritance
                }
            },
            "recommendations": report.recommendations
        }
    
    def print_summary(self, report: QualityAssuranceReport):
        """打印质量保证报告摘要"""
        print("\n" + "="*80)
        print("🎯 C++ 代码分析质量保证报告")
        print("="*80)
        
        print(f"📊 总体质量评分: {report.overall_quality_score}/100")
        print(f"⏱️  分析耗时: {report.analysis_time_seconds:.2f}秒")
        print(f"🕐 报告时间: {report.timestamp}")
        
        print("\n📈 完整性统计:")
        print(f"  函数提取覆盖率: {report.completeness.function_extraction_rate:.1%} "
              f"({report.completeness.extracted_functions}/{report.completeness.ast_function_definitions + report.completeness.ast_function_declarations})")
        print(f"  类提取覆盖率: {report.completeness.class_extraction_rate:.1%} "
              f"({report.completeness.extracted_classes}/{report.completeness.ast_class_definitions + report.completeness.ast_class_declarations})")
        print(f"  调用关系覆盖率: {report.completeness.call_extraction_rate:.1%} "
              f"({report.completeness.extracted_call_relationships}/{report.completeness.ast_call_expressions})")
        
        print("\n💾 函数体统计:")
        print(f"  函数体保存率: {report.function_bodies.body_preservation_rate:.1%} "
              f"({report.function_bodies.functions_with_body}/{report.function_bodies.total_function_definitions})")
        print(f"  缺少函数体: {report.function_bodies.functions_without_body}个")
        print(f"  空函数体: {report.function_bodies.empty_function_bodies}个")
        
        print("\n🔗 关系质量:")
        print(f"  调用关系一致性: {report.relationships.call_consistency_rate:.1%}")
        print(f"  继承关系有效性: {report.relationships.inheritance_validity_rate:.1%}")
        print(f"  孤立调用引用: {report.relationships.orphaned_call_references}个")
        print(f"  无效继承引用: {report.relationships.orphaned_inheritance_references}个")
        
        if report.recommendations:
            print("\n💡 改进建议:")
            for i, rec in enumerate(report.recommendations[:5], 1):
                print(f"  {i}. {rec}")
            if len(report.recommendations) > 5:
                print(f"  ... 还有 {len(report.recommendations) - 5} 条建议")
        
        print("\n" + "="*80) 