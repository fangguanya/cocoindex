"""
动态模板解析器 - 基于clang AST的智能模板基类分析
不依赖硬编码模式，而是通过分析compilation database和AST来动态发现模板关系
"""

import clang.cindex as clang
import re
from typing import Dict, List, Optional, Set, Tuple, Any
from pathlib import Path
from .logger import get_logger
from .clang_parser import ClangParser

class TemplateInstantiationInfo:
    """模板实例化信息"""
    
    def __init__(self, template_usr: str, specialized_usr: str, template_args: List[str], 
                 source_location: str = "", cursor_kind: str = ""):
        self.template_usr = template_usr  # 基础模板的USR
        self.specialized_usr = specialized_usr  # 特化版本的USR
        self.template_args = template_args  # 模板参数
        self.source_location = source_location  # 源码位置
        self.cursor_kind = cursor_kind  # cursor类型


class DynamicTemplateResolver:
    """动态模板解析器 - 基于clang AST分析"""
    
    def __init__(self, clang_parser: Optional[ClangParser] = None, project_root: str = None):
        self.logger = get_logger()
        self.clang_parser = clang_parser
        self.project_root = project_root
        
        # 模板实例化缓存
        self.template_instantiations: Dict[str, List[TemplateInstantiationInfo]] = {}
        self.template_base_mapping: Dict[str, str] = {}  # specialized_usr -> template_usr
        self.template_hierarchy: Dict[str, Set[str]] = {}  # template -> specializations
        
        # USR解析缓存
        self.usr_analysis_cache: Dict[str, Dict[str, Any]] = {}
        
        # 新增：USR到cursor的映射，用于基于AST语义信息的分析
        self.usr_to_cursor_map: Dict[str, 'clang.Cursor'] = {}
        self.cursor_analysis_cache: Dict[str, Dict[str, Any]] = {}
        
        # 继承关系记录（从模板特化中提取）
        self.inheritance_relationships: Dict[str, List[str]] = {}
        self.template_base_instantiations: Dict[str, List[str]] = {}
        
        # 类型依赖图，用于追踪类型间的依赖关系
        self.type_dependency_graph: Dict[str, Set[str]] = {}
        
        # 缓存性能统计
        self._cache_requests = 0
        self._cache_hits = 0
        
        # 强制使用多进程共享缓存（强制全局记录）
        self.shared_cache = None  # Type: Optional[SharedClassCache]
        if not project_root:
            raise RuntimeError("DynamicTemplateResolver 必须提供 project_root 参数以启用强制全局缓存")
        
        try:
            from .shared_class_cache import get_shared_class_cache
            self.shared_cache = get_shared_class_cache(project_root)
            if not self.shared_cache:
                raise RuntimeError("共享类缓存初始化失败，无法获取有效的缓存实例")
            self.logger.debug("已启用强制全局多进程共享类缓存")
        except Exception as e:
            error_msg = f"无法初始化共享类缓存，所有模板类型的解析都必须全局记录: {e}"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)
    
    def analyze_template_relationships(self, compile_commands: Dict[str, Any], 
                                     existing_classes: Dict[str, Any]) -> Dict[str, Any]:
        """实时分析模板关系 - 在解析时立即处理模板特化和继承"""
        self.logger.info("开始实时模板关系分析...")
        
        # 建立USR到cursor的映射
        if self.clang_parser:
            cursor_count = self._build_complete_cursor_mapping(compile_commands)
            self.logger.info(f"构建了 {cursor_count} 个cursor映射")
        
        # 实时分析所有cursor的模板关系（边解析边处理）
        generated_count = self._analyze_and_generate_templates_realtime(existing_classes)
        
        self.logger.info(f"实时模板分析完成: 发现 {len(self.template_instantiations)} 个模板实例化，"
                        f"生成 {generated_count} 个基类")
        
        return self._get_generated_template_classes()
    
    def _analyze_and_generate_templates_realtime(self, existing_classes: Dict[str, Any]) -> int:
        """实时分析模板关系并生成缺失的基类"""
        generated_count = 0
        
        try:
            for usr, cursor in self.usr_to_cursor_map.items():
                # 跳过已存在的类
                if usr in existing_classes:
                    continue
                
                # 处理模板特化类型时立即分析其基础模板
                if self._is_template_specialization_cursor(cursor):
                    base_template_cursor = self._extract_base_template_cursor(cursor)
                    if base_template_cursor:
                        base_usr = base_template_cursor.get_usr()
                        if base_usr and base_usr not in existing_classes:
                            # 立即生成基础模板类
                            generated_class = self._create_template_base_class_from_cursor(
                                base_template_cursor, existing_classes
                            )
                            if generated_class:
                                existing_classes[base_usr] = generated_class
                                generated_count += 1
                                self.logger.debug(f"实时生成模板基类: {base_template_cursor.spelling}")
                        
                        # 记录模板实例化关系
                        self._record_template_instantiation_cursor(
                            base_template_cursor, cursor,
                            cursor.location.file.name if cursor.location.file else "<unknown>"
                        )
                
                # 处理继承关系时立即分析父类模板
                self._analyze_inheritance_and_generate_parents(cursor, existing_classes)
                
        except Exception as e:
            self.logger.debug(f"实时模板分析时出错: {e}")
        
        return generated_count
    
    def _analyze_inheritance_and_generate_parents(self, cursor, existing_classes: Dict[str, Any]) -> Dict[str, Any]:
        """分析继承关系并立即生成缺失的父类模板"""
        generated_classes = {}
        
        try:
            # 遍历基类
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.CXX_BASE_SPECIFIER:
                    base_type = child.type
                    if base_type:
                        base_decl = base_type.get_declaration()
                        if base_decl:
                            base_usr = base_decl.get_usr()
                            if base_usr and base_usr not in existing_classes:
                                # 如果父类是模板特化，立即生成其基础模板
                                if self._is_template_specialization_cursor(base_decl):
                                    base_template_cursor = self._extract_base_template_cursor(base_decl)
                                    if base_template_cursor:
                                        base_template_usr = base_template_cursor.get_usr()
                                        if base_template_usr and base_template_usr not in existing_classes:
                                            generated_class = self._create_template_base_class_from_cursor(
                                                base_template_cursor, existing_classes
                                            )
                                            if generated_class:
                                                existing_classes[base_template_usr] = generated_class
                                                generated_classes[base_template_usr] = generated_class
                                                self.logger.debug(f"继承分析时生成模板基类: {base_template_cursor.spelling}")
        except Exception as e:
            self.logger.debug(f"分析继承关系时出错: {e}")
        
        return generated_classes
    
    def _get_generated_template_classes(self) -> Dict[str, Any]:
        """获取所有生成的模板类（从实例化记录中提取）"""
        generated_classes = {}
        
        try:
            # 从template_hierarchy中提取生成的基类
            for base_usr, specializations in self.template_hierarchy.items():
                base_cursor = self.get_cursor_by_usr(base_usr)
                if base_cursor:
                    # 检查是否为动态生成的（通过检查是否有对应的特化）
                    if len(specializations) > 0:
                        generated_class = self._create_template_base_class_from_cursor(base_cursor, {})
                        if generated_class:
                            generated_classes[base_usr] = generated_class
        except Exception as e:
            self.logger.debug(f"获取生成的模板类时出错: {e}")
        
        return generated_classes
    
    def process_template_specialization_immediate(self, specialization_cursor, existing_classes: Dict[str, Any]) -> Dict[str, Any]:
        """处理模板特化时立即解析所有相关信息 - 完整的实时处理"""
        generated_classes = {}
        
        try:
            if not self._is_template_specialization_cursor(specialization_cursor):
                return generated_classes
                
            # 1. 立即提取基础模板
            base_template_cursor = self._extract_base_template_cursor(specialization_cursor)
            if not base_template_cursor:
                return generated_classes
                
            base_usr = base_template_cursor.get_usr()
            specialized_usr = specialization_cursor.get_usr()
            
            if not base_usr or not specialized_usr:
                return generated_classes
            
            # 2. 立即记录模板实例化关系
            self._record_template_instantiation_cursor(
                base_template_cursor, specialization_cursor,
                specialization_cursor.location.file.name if specialization_cursor.location.file else "<unknown>"
            )
            
            # 3. 立即生成基础模板类（如果不存在）
            if base_usr not in existing_classes:
                generated_class = self._create_template_base_class_from_cursor(base_template_cursor, existing_classes)
                if generated_class:
                    generated_classes[base_usr] = generated_class
                    existing_classes[base_usr] = generated_class  # 立即添加到现有类中
                    self.logger.debug(f"立即处理特化时生成基类: {base_template_cursor.spelling}")
            
            # 4. 立即处理继承关系和模板参数
            self._extract_template_info_immediate(specialization_cursor, base_template_cursor)
            
            # 5. 立即处理父类模板
            parent_generated = self._analyze_inheritance_and_generate_parents(specialization_cursor, existing_classes)
            generated_classes.update(parent_generated if parent_generated else {})
            
        except Exception as e:
            self.logger.debug(f"立即处理模板特化时出错: {e}")
        
        return generated_classes
    
    def _extract_template_info_immediate(self, specialization_cursor, base_template_cursor):
        """立即提取模板特化的所有相关信息"""
        try:
            # 提取模板参数
            template_args = self._extract_template_arguments(specialization_cursor)
            
            # 分析模板特化的具体类型信息
            if hasattr(specialization_cursor, 'type') and specialization_cursor.type:
                concrete_args = self._extract_concrete_template_args(specialization_cursor.type)
                if concrete_args:
                    self.logger.debug(f"模板特化 {specialization_cursor.spelling} 的具体参数: {concrete_args}")
            
            # 分析命名空间和限定名
            qualified_name = self._extract_qualified_name_from_cursor(specialization_cursor)
            base_qualified_name = self._extract_qualified_name_from_cursor(base_template_cursor)
            
            self.logger.debug(f"模板关系: {qualified_name} -> {base_qualified_name}")
            
        except Exception as e:
            self.logger.debug(f"提取模板信息时出错: {e}")
    
    def _build_complete_cursor_mapping(self, compile_commands: Dict[str, Any]) -> int:
        """建立完整的cursor映射 - 纯cursor驱动"""
        initial_count = len(self.usr_to_cursor_map)
        
        try:
            for tu_data in compile_commands.get('translation_units', []):
                if hasattr(tu_data, 'cursor') or (isinstance(tu_data, dict) and 'translation_unit' in tu_data):
                    tu = tu_data.get('translation_unit') if isinstance(tu_data, dict) else tu_data
                    if tu:
                        self._build_usr_cursor_mapping(tu)
        except Exception as e:
            self.logger.debug(f"构建cursor映射时出错: {e}")
        
        return len(self.usr_to_cursor_map) - initial_count
    
    def _create_template_base_class_from_cursor(self, base_cursor, existing_classes: Dict[str, Any]) -> Any:
        """基于cursor创建模板基类"""
        from .data_structures import Class, CppOopExtensions
        
        try:
            base_usr = base_cursor.get_usr()
            class_name = base_cursor.spelling or base_cursor.displayname or "UnknownTemplate"
            
            # 从cursor中提取更准确的信息
            qualified_name = self._extract_qualified_name_from_cursor(base_cursor)
            
            # 检查是否有已知的特化版本来推断结构
            specializations = self.template_hierarchy.get(base_usr, set())
            parent_classes = []
            
            # 从特化版本中推断可能的父类
            for spec_usr in specializations:
                if spec_usr in existing_classes:
                    spec_class = existing_classes[spec_usr]
                    if hasattr(spec_class, 'parent_classes'):
                        # 使用cursor将特化版本的父类转换为基础版本
                        for parent_usr in spec_class.parent_classes:
                            base_parent = self._convert_to_base_template_cursor(parent_usr)
                            if base_parent and base_parent != base_usr:
                                parent_classes.append(base_parent)
            
            # 去重
            parent_classes = list(set(parent_classes))
            
            template_class = Class(
                name=class_name,
                qualified_name=qualified_name,
                usr_id=base_usr,
                definition_file_id="<cursor_generated>",
                declaration_file_id="<cursor_generated>",
                line=base_cursor.location.line if base_cursor.location else 0,
                declaration_locations=[],
                definition_location=None,
                is_declaration=True,
                is_definition=False,
                methods=[],
                is_abstract=False,
                cpp_oop_extensions=CppOopExtensions(qualified_name=qualified_name),
                parent_classes=parent_classes
            )
            
            # 标记为基于cursor生成的模板类
            template_class.is_cursor_generated_template = True
            template_class.template_instantiations = list(specializations)
            
            return template_class
            
        except Exception as e:
            self.logger.error(f"基于cursor创建模板基类时出错: {e}")
            return None
    
    def _extract_qualified_name_from_cursor(self, cursor) -> str:
        """从cursor中提取完整的限定名"""
        try:
            name_parts = []
            current = cursor
            
            # 添加类名
            if current.spelling:
                name_parts.append(current.spelling)
            
            # 向上遍历命名空间
            current = current.semantic_parent
            while current:
                if hasattr(current, 'kind'):
                    import clang.cindex as clang
                    if current.kind == clang.CursorKind.NAMESPACE:
                        namespace_name = current.spelling or current.displayname
                        if namespace_name:
                            name_parts.append(namespace_name)
                current = current.semantic_parent
            
            # 反转得到正确的顺序
            name_parts.reverse()
            
            return "::".join(name_parts) if name_parts else cursor.spelling or "UnknownClass"
            
        except Exception as e:
            self.logger.debug(f"提取cursor限定名时出错: {e}")
            return cursor.spelling or "UnknownClass"
    
    def _extract_template_instantiations_from_compile_db(self, compile_commands: Dict[str, Any]):
        """从编译数据库中提取模板实例化信息"""
        if not self.clang_parser:
            self.logger.warning("没有clang解析器，跳过AST模板分析")
            return
        
        # 遍历所有编译单元，查找模板实例化
        for file_path, compile_info in compile_commands.items():
            try:
                self._analyze_file_template_instantiations(file_path, compile_info)
            except Exception as e:
                self.logger.debug(f"分析文件 {file_path} 的模板实例化时出错: {e}")
    
    def _analyze_file_template_instantiations(self, file_path: str, compile_info: Dict[str, Any]):
        """分析单个文件的模板实例化"""
        # 解析翻译单元
        try:
            translation_unit = self.clang_parser._create_translation_unit(file_path, compile_info)
            if not translation_unit:
                return
            
            # 遍历AST寻找模板相关的cursor
            self._traverse_ast_for_templates(translation_unit.cursor, file_path)
            
        except Exception as e:
            self.logger.debug(f"解析文件 {file_path} 时出错: {e}")
    
    def _traverse_ast_for_templates(self, cursor: clang.Cursor, file_path: str):
        """遍历AST查找模板实例化"""
        template_kinds = {
            clang.CursorKind.CLASS_TEMPLATE,
            clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION, 
            clang.CursorKind.FUNCTION_TEMPLATE,
            clang.CursorKind.TEMPLATE_TYPE_PARAMETER,
            clang.CursorKind.TEMPLATE_NON_TYPE_PARAMETER,
            clang.CursorKind.TEMPLATE_TEMPLATE_PARAMETER,
            # 添加模板实例化相关的类型
            clang.CursorKind.CLASS_DECL,  # 可能是模板实例化
            clang.CursorKind.STRUCT_DECL,
        }
        
        if cursor.kind in template_kinds:
            self._analyze_template_cursor(cursor, file_path)
        
        # 递归遍历子节点
        for child in cursor.get_children():
            if self._should_analyze_cursor(child, file_path):
                self._traverse_ast_for_templates(child, file_path)
    
    def _should_analyze_cursor(self, cursor: clang.Cursor, target_file: str) -> bool:
        """判断是否应该分析这个cursor"""
        # 跳过系统头文件
        if cursor.location.file:
            file_str = str(cursor.location.file)
            if self._is_system_header(file_str):
                return False
        
        return True
    
    def _is_system_header(self, file_path: str) -> bool:
        """判断是否为系统头文件"""
        system_patterns = [
            'microsoft visual studio',
            'windows kits',
            '/usr/include',
            '/usr/local/include',
            'c:\\program files',
            'msvc',
            'ucrt',
            'shared\\include'
        ]
        
        file_lower = file_path.lower()
        return any(pattern in file_lower for pattern in system_patterns)
    
    def _analyze_template_cursor(self, cursor: clang.Cursor, file_path: str):
        """分析模板相关的cursor - 纯cursor驱动"""
        try:
            usr = cursor.get_usr()
            if not usr:
                return
            
            # 记录cursor映射
            self.usr_to_cursor_map[usr] = cursor
            
            # 1. 直接通过cursor分析模板特化
            if self._is_template_specialization_cursor(cursor):
                base_template_cursor = self._extract_base_template_cursor(cursor)
                if base_template_cursor and base_template_cursor.get_usr():
                    self._record_template_instantiation_cursor(
                        base_template_cursor, cursor, file_path
                    )
            
            # 2. 通过clang类型系统直接分析模板特化
            self._analyze_template_specialization_type(cursor, file_path)
            
            # 3. 提取继承关系信息
            self._extract_inheritance_from_template_specialization(cursor, file_path)
            
            # 4. 分析模板参数
            template_args = self._extract_template_arguments(cursor)
            if template_args:
                self.logger.debug(f"发现模板实例化: {cursor.spelling} with args: {template_args}")
        
        except Exception as e:
            self.logger.debug(f"分析模板cursor时出错: {e}")
    
    def _analyze_template_specialization_type(self, cursor: clang.Cursor, file_path: str):
        """通过clang类型系统直接分析模板特化"""
        try:
            # 获取cursor的类型
            cursor_type = cursor.type
            if not cursor_type:
                return
            
            # 检查是否为模板特化类型
            if cursor_type.kind == clang.TypeKind.RECORD:
                # 获取类型声明
                type_decl = cursor_type.get_declaration()
                if type_decl and type_decl.kind == clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION:
                    self._process_template_specialization_declaration(type_decl, cursor, file_path)
                elif type_decl and self._is_template_instantiation(type_decl):
                    self._process_template_instantiation_declaration(type_decl, cursor, file_path)
        
        except Exception as e:
            self.logger.debug(f"分析模板特化类型时出错: {e}")
    
    def _extract_inheritance_from_template_specialization(self, cursor: clang.Cursor, file_path: str):
        """从模板特化中提取继承关系"""
        try:
            # 遍历子cursor查找继承关系
            for child in cursor.get_children():
                if child.kind == clang.CursorKind.CXX_BASE_SPECIFIER:
                    self._process_base_class_specifier(child, cursor, file_path)
        
        except Exception as e:
            self.logger.debug(f"提取模板特化继承关系时出错: {e}")
    
    def _process_template_specialization_declaration(self, type_decl: clang.Cursor, 
                                                   original_cursor: clang.Cursor, file_path: str):
        """处理模板特化声明"""
        try:
            specialization_usr = type_decl.get_usr()
            original_usr = original_cursor.get_usr()
            
            if specialization_usr and original_usr:
                # 获取模板参数信息
                template_args = self._extract_template_args_from_type(type_decl)
                
                # 查找基础模板
                primary_template = self._find_primary_template(type_decl)
                if primary_template:
                    primary_usr = primary_template.get_usr()
                    if primary_usr:
                        self._record_template_instantiation(
                            primary_usr, specialization_usr, template_args, 
                            f"{file_path}:{type_decl.location.line}",
                            str(type_decl.kind)
                        )
                        self.logger.debug(f"发现模板特化关系: {primary_usr} -> {specialization_usr}")
        
        except Exception as e:
            self.logger.debug(f"处理模板特化声明时出错: {e}")
    
    def _process_template_instantiation_declaration(self, type_decl: clang.Cursor, 
                                                  original_cursor: clang.Cursor, file_path: str):
        """处理模板实例化声明"""
        try:
            instantiation_usr = type_decl.get_usr()
            if not instantiation_usr:
                return
            
            # 获取模板实例化的类型信息
            template_info = self._extract_template_instantiation_info(type_decl)
            if template_info:
                self.logger.debug(f"发现模板实例化: {instantiation_usr} -> {template_info}")
        
        except Exception as e:
            self.logger.debug(f"处理模板实例化声明时出错: {e}")
    
    def _process_base_class_specifier(self, base_spec: clang.Cursor, 
                                    derived_cursor: clang.Cursor, file_path: str):
        """处理基类指定符，提取继承关系"""
        try:
            # 获取基类类型
            base_type = base_spec.type
            if not base_type:
                return
            
            # 获取基类声明
            base_decl = base_type.get_declaration()
            if not base_decl:
                return
            
            base_usr = base_decl.get_usr()
            derived_usr = derived_cursor.get_usr()
            
            if base_usr and derived_usr:
                # 记录继承关系
                self._record_inheritance_relationship(derived_usr, base_usr, file_path)
                
                # 如果基类是模板特化，进行特殊处理（直接使用cursor）
                if self._is_template_specialization_cursor(base_decl):
                    self._handle_template_base_class_cursor(base_decl, derived_usr, base_spec, file_path)
        
        except Exception as e:
            self.logger.debug(f"处理基类指定符时出错: {e}")
    
    def _is_template_instantiation(self, cursor: clang.Cursor) -> bool:
        """检查cursor是否为模板实例化"""
        try:
            # 检查cursor的kind
            if cursor.kind in [clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL]:
                # 检查是否有模板参数
                template_params = [child for child in cursor.get_children() 
                                 if child.kind in [clang.CursorKind.TEMPLATE_TYPE_PARAMETER,
                                                   clang.CursorKind.TEMPLATE_NON_TYPE_PARAMETER,
                                                   clang.CursorKind.TEMPLATE_TEMPLATE_PARAMETER]]
                if template_params:
                    return True
                
                # 使用cursor方法检查模板特化
                if self._is_template_specialization_cursor(cursor):
                    return True
            
            return False
        except Exception:
            return False
    
    def _find_primary_template(self, specialization: clang.Cursor) -> Optional[clang.Cursor]:
        """查找模板特化的主模板"""
        try:
            # 通过specialization的引用找到主模板
            if hasattr(specialization, 'get_specialized_template'):
                return specialization.get_specialized_template()
            
            # 回退方法：通过名称查找
            template_name = specialization.spelling
            if template_name:
                # 在相同翻译单元中查找同名的主模板
                # 这里可以实现更复杂的查找逻辑
                pass
            
            return None
        except Exception:
            return None
    
    def _extract_template_args_from_type(self, type_decl: clang.Cursor) -> List[str]:
        """从类型声明中提取模板参数"""
        args = []
        try:
            # 遍历模板参数
            for child in type_decl.get_children():
                if child.kind == clang.CursorKind.TEMPLATE_TYPE_PARAMETER:
                    args.append(child.spelling or f"T{len(args)}")
                elif child.kind == clang.CursorKind.TEMPLATE_NON_TYPE_PARAMETER:
                    args.append(f"NonType{len(args)}")
                elif child.kind == clang.CursorKind.TEMPLATE_TEMPLATE_PARAMETER:
                    args.append(f"Template{len(args)}")
        except Exception as e:
            self.logger.debug(f"提取模板参数时出错: {e}")
        
        return args
    
    def _extract_template_instantiation_info(self, type_decl: clang.Cursor) -> Optional[Dict[str, Any]]:
        """提取模板实例化信息"""
        try:
            info = {
                'template_name': type_decl.spelling,
                'template_args': [],
                'specialization_kind': str(type_decl.kind)
            }
            
            # 提取模板参数类型
            cursor_type = type_decl.type
            if cursor_type:
                # 尝试获取模板参数（这部分可能需要根据clang版本调整）
                info['type_kind'] = str(cursor_type.kind)
            
            return info
        except Exception as e:
            self.logger.debug(f"提取模板实例化信息时出错: {e}")
            return None
    
    def _record_inheritance_relationship(self, derived_usr: str, base_usr: str, file_path: str):
        """记录继承关系"""
        try:
            if not hasattr(self, 'inheritance_relationships'):
                self.inheritance_relationships = {}
            
            if derived_usr not in self.inheritance_relationships:
                self.inheritance_relationships[derived_usr] = []
            
            if base_usr not in self.inheritance_relationships[derived_usr]:
                self.inheritance_relationships[derived_usr].append(base_usr)
                self.logger.debug(f"记录继承关系: {derived_usr} -> {base_usr} ({file_path})")
        
        except Exception as e:
            self.logger.debug(f"记录继承关系时出错: {e}")
    
    def _handle_template_base_class_cursor(self, base_cursor, derived_usr: str, 
                                         base_spec: clang.Cursor, file_path: str):
        """处理模板基类的特殊情况 - 基于cursor"""
        try:
            base_usr = base_cursor.get_usr()
            if not base_usr:
                return
            
            # 分析模板基类的实例化信息
            base_type = base_spec.type
            if base_type:
                # 提取具体的模板参数
                template_args = self._extract_concrete_template_args(base_type)
                if template_args:
                    self.logger.debug(f"模板基类 {base_cursor.spelling} 的具体参数: {template_args}")
                    
                    # 记录模板基类的实例化信息
                    if not hasattr(self, 'template_base_instantiations'):
                        self.template_base_instantiations = {}
                    
                    self.template_base_instantiations[derived_usr] = {
                        'base_usr': base_usr,
                        'base_cursor': base_cursor,  # 存储cursor对象
                        'template_args': template_args,
                        'source_location': f"{file_path}:{base_spec.location.line}"
                    }
        
        except Exception as e:
            self.logger.debug(f"处理模板基类时出错: {e}")
    
    def _handle_template_base_class(self, base_usr: str, derived_usr: str, 
                                  base_spec: clang.Cursor, file_path: str):
        """处理模板基类的特殊情况 - 保留回退逻辑"""
        base_cursor = self.get_cursor_by_usr(base_usr)
        if base_cursor:
            self._handle_template_base_class_cursor(base_cursor, derived_usr, base_spec, file_path)
        else:
            # 无cursor时的简化处理
            try:
                base_type = base_spec.type
                if base_type:
                    template_args = self._extract_concrete_template_args(base_type)
                    if template_args:
                        if not hasattr(self, 'template_base_instantiations'):
                            self.template_base_instantiations = {}
                        
                        self.template_base_instantiations[derived_usr] = {
                            'base_usr': base_usr,
                            'template_args': template_args,
                            'source_location': f"{file_path}:{base_spec.location.line}"
                        }
            except Exception as e:
                self.logger.debug(f"处理模板基类时出错: {e}")
    
    def _extract_concrete_template_args(self, template_type: clang.Type) -> List[str]:
        """使用clang API提取具体的模板参数"""
        args = []
        try:
            # 优先使用clang.Type的模板参数API
            if hasattr(template_type, 'get_num_template_arguments'):
                num_args = template_type.get_num_template_arguments()
                for i in range(num_args):
                    try:
                        arg_type = template_type.get_template_argument_type(i)
                        if arg_type and arg_type.spelling:
                            args.append(arg_type.spelling)
                    except Exception as e:
                        self.logger.debug(f"提取模板参数 {i} 时出错: {e}")
                        continue
            
            # 如果上述方法不可用，尝试通过cursor获取
            elif hasattr(template_type, 'get_declaration'):
                decl_cursor = template_type.get_declaration()
                if decl_cursor:
                    template_args = self._extract_template_arguments(decl_cursor)
                    args.extend(template_args)
        
        except Exception as e:
            self.logger.debug(f"提取具体模板参数时出错: {e}")
        
        return args
    
    def _is_template_specialization_usr(self, usr: str) -> bool:
        """检查USR是否表示模板特化（基于cursor语义信息）"""
        # 优先使用cursor语义信息
        cursor = self.get_cursor_by_usr(usr)
        if cursor:
            return self._is_template_specialization_cursor(cursor)
        return False
    
    def _extract_base_template_usr(self, specialized_usr: str) -> str:
        """从特化USR中提取基础模板USR（基于cursor语义信息）"""
        # 优先使用cursor语义信息
        cursor = self.get_cursor_by_usr(specialized_usr)
        if cursor:
            base_cursor = self._extract_base_template_cursor(cursor)
            if base_cursor:
                usr = base_cursor.get_usr()
                if usr:
                    return usr
        
        # 如果没有cursor映射，回退到字符串分析（保持兼容性）
        return self._extract_base_template_usr_fallback(specialized_usr)
    
    def _are_template_variants_cursor(self, cursor1, cursor2) -> bool:
        """基于cursor语义信息检查是否为同一模板的不同变体"""
        try:
            # 获取两个cursor指向的基础模板
            base_template1 = self._get_base_template_cursor(cursor1)
            base_template2 = self._get_base_template_cursor(cursor2)
            
            # 如果都有基础模板，比较它们是否相同
            if base_template1 and base_template2:
                return self._are_same_template_cursor(base_template1, base_template2)
            
            # 如果一个是基础模板，一个是特化
            if base_template1 and not base_template2:
                return self._are_same_template_cursor(base_template1, cursor2)
            
            if base_template2 and not base_template1:
                return self._are_same_template_cursor(base_template2, cursor1)
            
            # 如果都没有基础模板，检查是否本身就是同一个模板
            if not base_template1 and not base_template2:
                return self._are_same_template_cursor(cursor1, cursor2)
            
            return False
            
        except Exception as e:
            self.logger.debug(f"基于cursor检查模板变体时出错: {e}")
            return False
    
    def _get_base_template_cursor(self, cursor):
        """获取cursor指向的基础模板cursor"""
        try:
            # 对于模板特化，获取基础模板
            if hasattr(cursor, 'get_specialized_template'):
                specialized = cursor.get_specialized_template()
                if specialized and specialized != cursor:
                    return specialized
            
            # 对于模板实例化，获取原始模板
            if hasattr(cursor, 'get_template'):
                template = cursor.get_template()
                if template and template != cursor:
                    return template
            
            # 检查是否本身就是模板定义
            if self._is_template_definition_cursor(cursor):
                return cursor
            
            return None
            
        except Exception as e:
            self.logger.debug(f"获取基础模板cursor时出错: {e}")
            return None
    
    def _are_same_template_cursor(self, cursor1, cursor2) -> bool:
        """检查两个cursor是否表示同一个模板"""
        try:
            # 最直接的方法：比较USR
            if cursor1.usr and cursor2.usr:
                return cursor1.usr == cursor2.usr
            
            # 如果USR不可用，比较位置信息
            if (hasattr(cursor1, 'location') and hasattr(cursor2, 'location') and
                cursor1.location.file and cursor2.location.file):
                
                return (cursor1.location.file.name == cursor2.location.file.name and
                        cursor1.location.line == cursor2.location.line and
                        cursor1.location.column == cursor2.location.column)
            
            # 如果位置信息不可用，比较名称和种类
            if (hasattr(cursor1, 'spelling') and hasattr(cursor2, 'spelling') and
                hasattr(cursor1, 'kind') and hasattr(cursor2, 'kind')):
                
                return (cursor1.spelling == cursor2.spelling and 
                        cursor1.kind == cursor2.kind)
            
            return False
            
        except Exception as e:
            self.logger.debug(f"比较cursor时出错: {e}")
            return False
    

    

    
    def _extract_template_arguments(self, cursor: clang.Cursor) -> List[str]:
        """使用clang cursor API提取模板参数"""
        args = []
        try:
            # 使用标准的clang cursor模板参数API
            if hasattr(cursor, 'get_num_template_arguments'):
                num_args = cursor.get_num_template_arguments()
                for i in range(num_args):
                    try:
                        # 获取模板参数的类型
                        arg_kind = cursor.get_template_argument_kind(i)
                        if arg_kind == clang.TemplateArgumentKind.TYPE:
                            arg_type = cursor.get_template_argument_type(i)
                            if arg_type and arg_type.spelling:
                                args.append(arg_type.spelling)
                        elif arg_kind == clang.TemplateArgumentKind.INTEGRAL:
                            # 非类型模板参数（整数值）
                            arg_value = cursor.get_template_argument_value(i)
                            args.append(str(arg_value))
                        elif arg_kind == clang.TemplateArgumentKind.DECLARATION:
                            # 声明类型的模板参数
                            arg_cursor = cursor.get_template_argument_cursor(i)
                            if arg_cursor and arg_cursor.spelling:
                                args.append(arg_cursor.spelling)
                    except Exception as e:
                        self.logger.debug(f"提取模板参数 {i} 时出错: {e}")
                        continue
        
        except Exception as e:
            self.logger.debug(f"提取模板参数时出错: {e}")
        
        return args
    
    def _record_template_instantiation_cursor(self, base_template_cursor, specialization_cursor, file_path: str):
        """记录模板实例化信息 - 纯cursor驱动"""
        try:
            base_usr = base_template_cursor.get_usr()
            specialized_usr = specialization_cursor.get_usr()
            
            if not base_usr or not specialized_usr:
                return
            
            template_args = self._extract_template_arguments(specialization_cursor)
            source_location = f"{file_path}:{specialization_cursor.location.line}" if specialization_cursor.location else file_path
            cursor_kind = str(specialization_cursor.kind)
            
            instantiation = TemplateInstantiationInfo(
                template_usr=base_usr,
                specialized_usr=specialized_usr,
                template_args=template_args,
                source_location=source_location,
                cursor_kind=cursor_kind
            )
            
            if base_usr not in self.template_instantiations:
                self.template_instantiations[base_usr] = []
            
            self.template_instantiations[base_usr].append(instantiation)
            self.template_base_mapping[specialized_usr] = base_usr
            
            # 更新层次结构
            if base_usr not in self.template_hierarchy:
                self.template_hierarchy[base_usr] = set()
            self.template_hierarchy[base_usr].add(specialized_usr)
            
            self.logger.debug(f"记录模板实例化: {base_template_cursor.spelling} -> {specialization_cursor.spelling}")
            
        except Exception as e:
            self.logger.debug(f"记录模板实例化时出错: {e}")
    
    def _analyze_existing_class_templates(self, existing_classes: Dict[str, Any]):
        """分析现有类中的模板关系 - 纯cursor驱动"""
        for class_usr, class_obj in existing_classes.items():
            cursor = self.get_cursor_by_usr(class_usr)
            if not cursor:
                continue
                
            if self._is_template_specialization_cursor(cursor):
                base_template_cursor = self._extract_base_template_cursor(cursor)
                if base_template_cursor:
                    base_usr = base_template_cursor.get_usr()
                    if base_usr:
                        self.template_base_mapping[class_usr] = base_usr
                        
                        if base_usr not in self.template_hierarchy:
                            self.template_hierarchy[base_usr] = set()
                        self.template_hierarchy[base_usr].add(class_usr)
    
    def _identify_missing_template_bases(self, existing_classes: Dict[str, Any]) -> Set[str]:
        """识别缺失的模板基类 - 纯cursor驱动"""
        missing_bases = set()
        
        # 检查所有映射的基类是否存在
        for specialized_usr, base_usr in self.template_base_mapping.items():
            if base_usr not in existing_classes:
                missing_bases.add(base_usr)
        
        # 检查现有类的parent_classes中的缺失基类
        for class_usr, class_obj in existing_classes.items():
            if hasattr(class_obj, 'parent_classes'):
                for parent_usr in class_obj.parent_classes:
                    if parent_usr not in existing_classes:
                        # 使用cursor检查是否为模板特化
                        parent_cursor = self.get_cursor_by_usr(parent_usr)
                        if parent_cursor and self._is_template_specialization_cursor(parent_cursor):
                            missing_bases.add(parent_usr)
                        elif parent_cursor:
                            # 检查是否有对应的基础模板
                            base_template_cursor = self._extract_base_template_cursor(parent_cursor)
                            if base_template_cursor:
                                base_template_usr = base_template_cursor.get_usr()
                                if base_template_usr:
                                    missing_bases.add(base_template_usr)
        
        return missing_bases
    
    def _infer_template_base_from_usage_cursor(self, missing_usr: str, existing_classes: Dict[str, Any]) -> Optional[str]:
        """通过cursor语义信息推断模板基类 - 纯cursor驱动"""
        missing_cursor = self.get_cursor_by_usr(missing_usr)
        if not missing_cursor:
            return None
        
        # 1. 直接从cursor提取基础模板
        if self._is_template_specialization_cursor(missing_cursor):
            base_template_cursor = self._extract_base_template_cursor(missing_cursor)
            if base_template_cursor:
                return base_template_cursor.get_usr()
        
        # 2. 通过已记录的模板实例化关系查找
        inferred_base = self._infer_from_template_instantiations_cursor(missing_cursor)
        if inferred_base:
            return inferred_base
        
        # 3. 通过继承关系中的模板模式推断
        inferred_from_inheritance = self._infer_from_inheritance_patterns_cursor(missing_cursor, existing_classes)
        if inferred_from_inheritance:
            return inferred_from_inheritance
        
        return None
    
    def _infer_from_template_instantiations_cursor(self, missing_cursor) -> Optional[str]:
        """从已记录的模板实例化关系中推断基类 - 纯cursor驱动"""
        try:
            missing_usr = missing_cursor.get_usr()
            if not missing_usr:
                return None
            
            # 检查template_instantiations中是否有相关信息
            for base_template_usr, instantiations in self.template_instantiations.items():
                for instantiation in instantiations:
                    # 通过cursor比较而非字符串比较
                    instantiation_cursor = self.get_cursor_by_usr(instantiation.specialized_usr)
                    if instantiation_cursor and self._are_template_variants_cursor(missing_cursor, instantiation_cursor):
                        return base_template_usr
            
            return None
        except Exception as e:
            self.logger.debug(f"从模板实例化推断时出错: {e}")
            return None
    
    def _infer_from_inheritance_patterns_cursor(self, missing_cursor, existing_classes: Dict[str, Any]) -> Optional[str]:
        """从继承关系模式中推断模板基类 - 纯cursor驱动"""
        try:
            if not hasattr(self, 'inheritance_relationships'):
                return None
            
            missing_usr = missing_cursor.get_usr()
            if not missing_usr:
                return None
            
            # 查找使用missing_usr作为基类的派生类
            derived_classes = []
            for derived_usr, base_list in self.inheritance_relationships.items():
                if missing_usr in base_list:
                    derived_classes.append(derived_usr)
            
            if not derived_classes:
                return None
            
            # 分析这些派生类的其他基类，寻找模板模式
            for derived_usr in derived_classes:
                other_bases = [base for base in self.inheritance_relationships.get(derived_usr, []) 
                              if base != missing_usr]
                
                for other_base_usr in other_bases:
                    other_base_cursor = self.get_cursor_by_usr(other_base_usr)
                    if other_base_cursor and self._is_template_specialization_cursor(other_base_cursor):
                        potential_base_cursor = self._extract_base_template_cursor(other_base_cursor)
                        if potential_base_cursor and self._could_be_template_variant_cursor(missing_cursor, potential_base_cursor):
                            return potential_base_cursor.get_usr()
            
            return None
        except Exception as e:
            self.logger.debug(f"从继承模式推断时出错: {e}")
            return None
    
    def _could_be_template_variant_cursor(self, cursor, potential_base_template_cursor) -> bool:
        """检查cursor是否可能是指定基础模板的变体 - 纯cursor驱动"""
        try:
            # 直接通过cursor的模板关系检查
            if self._is_template_specialization_cursor(cursor):
                base_cursor = self._extract_base_template_cursor(cursor)
                if base_cursor:
                    return self._are_same_template_cursor(base_cursor, potential_base_template_cursor)
            
            # 检查是否为同一模板家族
            return self._are_template_variants_cursor(cursor, potential_base_template_cursor)
            
        except Exception as e:
            self.logger.debug(f"检查模板变体时出错: {e}")
            return False
    
    def _are_template_variants(self, usr1: str, usr2: str) -> bool:
        """检查两个USR是否为同一模板的不同变体 - 纯cursor驱动"""
        try:
            cursor1 = self.get_cursor_by_usr(usr1)
            cursor2 = self.get_cursor_by_usr(usr2)
            
            if cursor1 and cursor2:
                return self._are_template_variants_cursor(cursor1, cursor2)
            
            # 无cursor信息时返回False，不进行低质量的字符串匹配
            return False
            
        except Exception as e:
            self.logger.debug(f"检查模板变体时出错: {e}")
            return False
    

    
    def _generate_template_base_classes(self, missing_bases: Set[str], 
                                      existing_classes: Dict[str, Any]) -> Dict[str, Any]:
        """为缺失的模板基类生成定义"""
        generated_classes = {}
        
        for base_usr in missing_bases:
            try:
                generated_class = self._create_template_base_class(base_usr, existing_classes)
                if generated_class:
                    generated_classes[base_usr] = generated_class
                    self.logger.debug(f"动态生成模板基类: {base_usr}")
            except Exception as e:
                self.logger.error(f"生成模板基类 {base_usr} 时出错: {e}")
        
        return generated_classes
    
    def _create_template_base_class(self, base_usr: str, existing_classes: Dict[str, Any]) -> Any:
        """创建模板基类 - 纯cursor驱动"""
        from .data_structures import Class, CppOopExtensions
        
        # 从cursor获取类名（如果有）
        base_cursor = self.get_cursor_by_usr(base_usr)
        if base_cursor:
            class_name = base_cursor.spelling or base_cursor.displayname
        else:
            class_name = self._extract_class_name_from_usr_simple(base_usr)
        
        # 检查是否有已知的特化版本来推断结构
        specializations = self.template_hierarchy.get(base_usr, set())
        parent_classes = []
        
        # 从特化版本中推断可能的父类
        for spec_usr in specializations:
            if spec_usr in existing_classes:
                spec_class = existing_classes[spec_usr]
                if hasattr(spec_class, 'parent_classes'):
                    # 使用cursor将特化版本的父类转换为基础版本
                    for parent_usr in spec_class.parent_classes:
                        base_parent = self._convert_to_base_template_cursor(parent_usr)
                        if base_parent and base_parent != base_usr:
                            parent_classes.append(base_parent)
        
        # 去重
        parent_classes = list(set(parent_classes))
        
        template_class = Class(
            name=class_name,
            qualified_name=class_name,
            usr_id=base_usr,
            definition_file_id="<dynamic_generated>",
            declaration_file_id="<dynamic_generated>",
            line=0,
            declaration_locations=[],
            definition_location=None,
            is_declaration=True,
            is_definition=False,
            methods=[],
            is_abstract=False,
            cpp_oop_extensions=CppOopExtensions(qualified_name=class_name),
            parent_classes=parent_classes
        )
        
        # 标记为动态生成的模板类
        template_class.is_dynamic_template = True
        template_class.template_instantiations = list(specializations)
        
        return template_class
    
    def _convert_to_base_template_cursor(self, specialized_usr: str) -> Optional[str]:
        """将特化USR转换为基础模板USR - 纯cursor驱动"""
        cursor = self.get_cursor_by_usr(specialized_usr)
        if cursor:
            base_cursor = self._extract_base_template_cursor(cursor)
            if base_cursor:
                return base_cursor.get_usr()
        return None
    
    def _extract_class_name_from_usr_simple(self, usr: str) -> str:
        """简单的USR类名提取 - 最后的回退方案"""
        # 只使用最基本的模式，不依赖复杂的字符串解析
        if '@' in usr:
            parts = usr.split('@')
            for part in reversed(parts):
                if part and part.isalnum() and len(part) > 1:
                    return part
        return "DynamicTemplate"
    
    def get_template_analysis_summary(self) -> Dict[str, Any]:
        """获取模板分析摘要"""
        summary = {
            'total_template_bases': len(self.template_hierarchy),
            'total_instantiations': sum(len(insts) for insts in self.template_instantiations.values()),
            'template_hierarchy': {k: list(v) for k, v in self.template_hierarchy.items()},
            'base_mapping_count': len(self.template_base_mapping)
        }
        
        # 添加新的继承关系信息
        if hasattr(self, 'inheritance_relationships'):
            summary['inheritance_relationships_count'] = len(self.inheritance_relationships)
            summary['inheritance_relationships'] = self.inheritance_relationships
        
        if hasattr(self, 'template_base_instantiations'):
            summary['template_base_instantiations_count'] = len(self.template_base_instantiations)
            summary['template_base_instantiations'] = self.template_base_instantiations
        
        return summary
    
    def _build_usr_cursor_mapping(self, translation_unit):
        """构建USR到cursor的映射"""
        try:
            def visit_cursor(cursor):
                usr = cursor.get_usr()
                if usr:
                    self.usr_to_cursor_map[usr] = cursor
                
                for child in cursor.get_children():
                    visit_cursor(child)
            
            if translation_unit and translation_unit.cursor:
                visit_cursor(translation_unit.cursor)
                self.logger.debug(f"构建了 {len(self.usr_to_cursor_map)} 个USR到cursor的映射")
        except Exception as e:
            self.logger.warning(f"构建USR-cursor映射时出错: {e}")
    
    def get_cursor_by_usr(self, usr: str):
        """根据USR获取对应的cursor"""
        return self.usr_to_cursor_map.get(usr)
    
    def _is_template_specialization_cursor(self, cursor) -> bool:
        """基于cursor对象直接判断是否为模板特化（使用clang原生属性）"""
        if not cursor:
            return False
        
        try:
            import clang.cindex as clang
            
            # 1. 直接通过cursor的kind判断模板特化类型
            template_specialization_kinds = [
                clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
                # 注意：clang python绑定中可能没有CLASS_TEMPLATE_SPECIALIZATION
                # 需要通过其他方式检测完整特化
            ]
            
            # 检查是否存在其他模板特化相关的kind
            for attr_name in ['CLASS_TEMPLATE_SPECIALIZATION', 'VAR_TEMPLATE_SPECIALIZATION', 
                             'FUNCTION_TEMPLATE_SPECIALIZATION']:
                if hasattr(clang.CursorKind, attr_name):
                    template_specialization_kinds.append(getattr(clang.CursorKind, attr_name))
            
            if cursor.kind in template_specialization_kinds:
                return True
            
            # 2. 检查是否为模板实例化（通过get_specialized_template）
            if hasattr(cursor, 'get_specialized_template'):
                try:
                    specialized_template = cursor.get_specialized_template()
                    if specialized_template and specialized_template != cursor:
                        return True
                except:
                    pass  # 某些clang版本可能不支持此方法
            
            # 3. 检查cursor是否有模板参数（另一个模板实例化的标志）
            if hasattr(cursor, 'get_num_template_arguments'):
                try:
                    num_args = cursor.get_num_template_arguments()
                    if num_args > 0:
                        return True
                except:
                    pass
            
            # 4. 检查类型是否为模板特化类型
            if hasattr(cursor, 'type') and cursor.type:
                cursor_type = cursor.type
                
                # 检查类型的声明是否为模板特化
                if hasattr(cursor_type, 'get_declaration'):
                    type_decl = cursor_type.get_declaration()
                    if type_decl and type_decl != cursor:
                        if type_decl.kind in template_specialization_kinds:
                            return True
                
                # 检查是否为模板实例化类型
                if hasattr(cursor_type, 'get_num_template_arguments'):
                    try:
                        if cursor_type.get_num_template_arguments() > 0:
                            return True
                    except:
                        pass
            
            # 5. 检查是否存在模板参数子cursor
            try:
                template_param_kinds = [
                    clang.CursorKind.TEMPLATE_TYPE_PARAMETER,
                    clang.CursorKind.TEMPLATE_NON_TYPE_PARAMETER,
                    clang.CursorKind.TEMPLATE_TEMPLATE_PARAMETER,
                ]
                
                for child in cursor.get_children():
                    if child.kind in template_param_kinds:
                        # 如果有模板参数，并且不是主模板定义，那就是实例化
                        if cursor.kind != clang.CursorKind.CLASS_TEMPLATE:
                            return True
                        break
            except:
                pass
            
            # 6. 检查cursor的特化状态（如果clang提供相关属性）
            if hasattr(cursor, 'is_specialization'):
                try:
                    return cursor.is_specialization()
                except:
                    pass
            
            return False
            
        except Exception as e:
            self.logger.debug(f"判断模板特化时出错: {e}")
            return False
    
    def _extract_base_template_cursor(self, cursor):
        """从模板特化cursor中提取基础模板cursor（替代字符串解析）"""
        if not cursor:
            return None
        
        try:
            # 首先检查cursor本身是否就是模板定义
            if self._is_template_definition_cursor(cursor):
                return cursor
            
            # 使用clang的语义信息获取基础模板
            if hasattr(cursor, 'get_specialized_template'):
                base_template = cursor.get_specialized_template()
                if base_template and base_template != cursor:
                    return base_template
            
            # 对于模板实例化，尝试获取原始模板
            if hasattr(cursor, 'get_template'):
                template = cursor.get_template()
                if template and template != cursor:
                    return template
            
            # 对于类模板特化，尝试从语义父级查找
            if hasattr(cursor, 'semantic_parent'):
                parent = cursor.semantic_parent
                while parent:
                    if self._is_template_definition_cursor(parent):
                        return parent
                    parent = parent.semantic_parent
            
            return None
            
        except Exception as e:
            self.logger.debug(f"提取基础模板时出错: {e}")
            return None
    
    def _is_template_definition_cursor(self, cursor) -> bool:
        """判断cursor是否为模板定义"""
        if not cursor:
            return False
        
        try:
            import clang.cindex as clang
            
            template_definition_kinds = [
                clang.CursorKind.CLASS_TEMPLATE,
                clang.CursorKind.FUNCTION_TEMPLATE,
            ]
            
            # 检查可选的CursorKind属性
            optional_kinds = ['VAR_TEMPLATE', 'TYPE_ALIAS_TEMPLATE_DECL']
            for kind_name in optional_kinds:
                if hasattr(clang.CursorKind, kind_name):
                    template_definition_kinds.append(getattr(clang.CursorKind, kind_name))
            
            return cursor.kind in template_definition_kinds
            
        except Exception as e:
            self.logger.debug(f"判断模板定义时出错: {e}")
            return False
    
    def _analyze_namespace_hierarchy_cursor(self, cursor) -> Dict[str, Any]:
        """基于cursor分析命名空间层次（替代字符串解析）"""
        result = {
            'namespace_chain': [],
            'is_std_namespace': False,
            'is_internal_namespace': False,
            'namespace_depth': 0
        }
        
        if not cursor:
            return result
        
        try:
            # 通过semantic_parent遍历命名空间层次
            current = cursor.semantic_parent
            namespace_chain = []
            
            while current:
                if hasattr(current, 'kind'):
                    import clang.cindex as clang
                    if current.kind == clang.CursorKind.NAMESPACE:
                        namespace_name = current.spelling or current.displayname
                        if namespace_name:
                            namespace_chain.append(namespace_name)
                            
                            # 直接从namespace对象判断，而非字符串匹配
                            if namespace_name == 'std':
                                result['is_std_namespace'] = True
                            elif namespace_name.lower() in ['private', 'internal', 'detail']:
                                result['is_internal_namespace'] = True
                
                current = current.semantic_parent
            
            # 反转得到从根到叶的顺序
            namespace_chain.reverse()
            result['namespace_chain'] = namespace_chain
            result['namespace_depth'] = len(namespace_chain)
            
        except Exception as e:
            self.logger.debug(f"分析命名空间层次时出错: {e}")
        
        return result
    
    def _extract_type_markers_cursor(self, cursor) -> Set[str]:
        """从cursor提取类型标记（替代字符串解析）"""
        markers = set()
        
        if not cursor:
            return markers
        
        try:
            import clang.cindex as clang
            
            # 直接从cursor的kind获取类型信息
            if cursor.kind == clang.CursorKind.UNION_DECL:
                markers.add('union')
            elif cursor.kind in [clang.CursorKind.CLASS_DECL, clang.CursorKind.STRUCT_DECL]:
                markers.add('struct_or_class')
            elif cursor.kind in [
                clang.CursorKind.CLASS_TEMPLATE,
                clang.CursorKind.CLASS_TEMPLATE_SPECIALIZATION,
                clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
                clang.CursorKind.FUNCTION_TEMPLATE,
                clang.CursorKind.VAR_TEMPLATE,
            ]:
                markers.add('template')
            
            # 检查类型的其他特征
            if hasattr(cursor, 'type'):
                cursor_type = cursor.type
                if cursor_type:
                    if cursor_type.kind == clang.TypeKind.RECORD:
                        markers.add('record_type')
                    elif cursor_type.kind == clang.TypeKind.POINTER:
                        markers.add('pointer_type')
                    elif cursor_type.kind == clang.TypeKind.TYPEDEF:
                        markers.add('typedef')
            
        except Exception as e:
            self.logger.debug(f"提取类型标记时出错: {e}")
        
        return markers
    
    # 纯cursor驱动的公共方法
    def is_template_specialization_usr(self, usr: str) -> bool:
        """检查USR是否表示模板特化 - 纯cursor驱动"""
        cursor = self.get_cursor_by_usr(usr)
        if cursor:
            return self._is_template_specialization_cursor(cursor)
        
        # 无cursor信息时返回False，不使用低质量的字符串解析
        return False
    
    def extract_base_template_usr(self, specialized_usr: str) -> str:
        """提取基础模板USR - 纯cursor驱动"""
        cursor = self.get_cursor_by_usr(specialized_usr)
        if cursor:
            base_cursor = self._extract_base_template_cursor(cursor)
            if base_cursor:
                usr = base_cursor.get_usr()
                if usr:
                    return usr
        
        # 无cursor信息时返回空字符串
        return ""
    
    def _are_template_variants_cursor(self, cursor1, cursor2) -> bool:
        """基于cursor语义信息检查是否为同一模板的不同变体"""
        try:
            # 获取两个cursor指向的基础模板
            base_template1 = self._get_base_template_cursor(cursor1)
            base_template2 = self._get_base_template_cursor(cursor2)
            
            # 如果都有基础模板，比较它们是否相同
            if base_template1 and base_template2:
                return self._are_same_template_cursor(base_template1, base_template2)
            
            # 如果一个是基础模板，一个是特化
            if base_template1 and not base_template2:
                return self._are_same_template_cursor(base_template1, cursor2)
            
            if base_template2 and not base_template1:
                return self._are_same_template_cursor(base_template2, cursor1)
            
            # 如果都没有基础模板，检查是否本身就是同一个模板
            if not base_template1 and not base_template2:
                return self._are_same_template_cursor(cursor1, cursor2)
            
            return False
            
        except Exception as e:
            self.logger.debug(f"基于cursor检查模板变体时出错: {e}")
            return False
    
    def _get_base_template_cursor(self, cursor):
        """获取cursor指向的基础模板cursor"""
        try:
            # 对于模板特化，获取基础模板
            if hasattr(cursor, 'get_specialized_template'):
                specialized = cursor.get_specialized_template()
                if specialized and specialized != cursor:
                    return specialized
            
            # 对于模板实例化，获取原始模板
            if hasattr(cursor, 'get_template'):
                template = cursor.get_template()
                if template and template != cursor:
                    return template
            
            # 检查是否本身就是模板定义
            if self._is_template_definition_cursor(cursor):
                return cursor
            
            return None
            
        except Exception as e:
            self.logger.debug(f"获取基础模板cursor时出错: {e}")
            return None
    
    def _are_same_template_cursor(self, cursor1, cursor2) -> bool:
        """检查两个cursor是否表示同一个模板"""
        try:
            # 最直接的方法：比较USR
            if cursor1.usr and cursor2.usr:
                return cursor1.usr == cursor2.usr
            
            # 如果USR不可用，比较位置信息
            if (hasattr(cursor1, 'location') and hasattr(cursor2, 'location') and
                cursor1.location.file and cursor2.location.file):
                
                return (cursor1.location.file.name == cursor2.location.file.name and
                        cursor1.location.line == cursor2.location.line and
                        cursor1.location.column == cursor2.location.column)
            
            # 如果位置信息不可用，比较名称和种类
            if (hasattr(cursor1, 'spelling') and hasattr(cursor2, 'spelling') and
                hasattr(cursor1, 'kind') and hasattr(cursor2, 'kind')):
                
                return (cursor1.spelling == cursor2.spelling and 
                        cursor1.kind == cursor2.kind)
            
            return False
            
        except Exception as e:
            self.logger.debug(f"比较cursor时出错: {e}")
            return False
    
    def extract_complete_type_information(self, compile_commands: Dict[str, Any], existing_classes: Dict[str, Any]) -> Dict[str, Any]:
        """从translation unit中正向提取完整的类型信息"""
        additional_classes = {}
        
        try:
            # 如果有compile_commands，使用它们来解析更多文件
            if compile_commands and self.clang_parser:
                # 重新构建usr_to_cursor_map，确保包含所有类型
                self._build_complete_usr_cursor_mapping(compile_commands)
                
                # 从扩展的cursor映射中提取类型
                for usr, cursor in self.usr_to_cursor_map.items():
                    if usr not in existing_classes:
                        class_obj = self._create_class_from_cursor(cursor)
                        if class_obj:
                            additional_classes[usr] = class_obj
            
            self.logger.info(f"从AST提取到 {len(additional_classes)} 个新类型")
            
        except Exception as e:
            self.logger.debug(f"提取完整类型信息时出错: {e}")
        
        return additional_classes
    
    def initialize_type_analysis_cache(self):
        """初始化类型分析缓存（强制全局缓存版本）"""
        # 仅清理依赖图和统计信息，不使用本地缓存
        self.type_dependency_graph.clear()
        self._cache_requests = 0
        self._cache_hits = 0
        self.logger.debug("类型分析缓存已初始化（强制全局模式）")
    
    def is_type_fully_resolved(self, type_name: str) -> bool:
        """检查类型是否已完全解析（强制全局缓存版本）"""
        self._cache_requests += 1
        
        # 强制使用共享缓存
        if not self.shared_cache:
            raise RuntimeError(f"无法检查类型 {type_name} 的解析状态：共享缓存未初始化")
        
        # 使用统一的类缓存方法
        if self.shared_cache.is_class_resolved("", type_name):
            self._cache_hits += 1
            self.logger.debug(f"全局缓存命中: {type_name}")
            return True
        
        return False
    
    def mark_type_as_resolved(self, type_name: str, resolved_classes: Dict[str, Any] = None, 
                            dependencies: Set[str] = None, parent_templates: Set[str] = None,
                            specializations: Set[str] = None):
        """标记类型为已解析（强制全局缓存版本）"""
        
        # 强制使用共享缓存
        if not self.shared_cache:
            raise RuntimeError(f"无法标记类型 {type_name} 为已解析：共享缓存未初始化")
        
        # 将模板数据适配到统一的类缓存格式
        class_data = {
            'type_name': type_name,
            'resolved_classes': resolved_classes or {},
            'dependencies': list(dependencies) if dependencies else [],
            'parent_templates': list(parent_templates) if parent_templates else [],
            'specializations': list(specializations) if specializations else [],
            'is_template': True  # 标记为模板类型
        }
        
        self.shared_cache.mark_class_resolved(
            usr="",  # 模板类型可能没有USR
            qualified_name=type_name,
            class_data=class_data,
            is_template=True
        )
        self.logger.debug(f"模板类型 {type_name} 已标记为完全解析（强制全局缓存）")
    
    def resolve_template_from_cursor(self, cursor, existing_classes: Dict[str, Any]) -> Dict[str, Any]:
        """从cursor按需解析模板类型（多进程安全版本）"""
        resolved_classes = {}
        
        try:
            if not cursor:
                return resolved_classes
            
            import clang.cindex as clang
            
            # 获取cursor的类型信息
            cursor_type = cursor.type if hasattr(cursor, 'type') else None
            type_name = cursor_type.spelling if cursor_type else cursor.spelling
            
            if not type_name:
                return resolved_classes
            
            # 添加递归深度控制
            if not hasattr(self, '_recursion_stack'):
                self._recursion_stack = set()
            
            if type_name in self._recursion_stack:
                self.logger.debug(f"检测到递归调用，跳过: {type_name}")
                return resolved_classes
            
            # 首先检查是否已解析（强制全局缓存）
            if self.is_type_fully_resolved(type_name):
                # 从统一共享缓存获取结果
                cached_result = self.shared_cache.get_resolved_class("", type_name)
                if cached_result and 'resolved_classes' in cached_result:
                    return cached_result['resolved_classes']
                return resolved_classes
            
            # 检查是否正在被其他进程解析
            if self.shared_cache.is_class_being_resolved("", type_name):
                self.logger.debug(f"类型 {type_name} 正在被其他进程解析，等待...")
                # 简单等待策略，实际项目中可以用更复杂的等待机制
                import time
                time.sleep(0.1)
                # 再次检查是否已完成
                if self.is_type_fully_resolved(type_name):
                    cached_result = self.shared_cache.get_resolved_class("", type_name)
                    if cached_result and 'resolved_classes' in cached_result:
                        return cached_result['resolved_classes']
                # 如果仍未完成，跳过避免死循环
                self.logger.debug(f"类型 {type_name} 仍在被其他进程解析，跳过以避免死循环")
                return resolved_classes
            
            # 尝试获取解析锁
            if not self.shared_cache.try_acquire_class_resolution_lock("", type_name):
                self.logger.debug(f"无法获取类型 {type_name} 的解析锁，可能被其他进程处理")
                return resolved_classes
            
            self._recursion_stack.add(type_name)
            try:
                self.logger.debug(f"开始解析模板类型: {type_name}")
                
                # 分析cursor的模板信息
                if self._is_template_related_cursor(cursor):
                    # 处理模板特化
                    if self._is_template_specialization_cursor(cursor):
                        new_classes = self.process_template_specialization_immediate(cursor, existing_classes)
                        resolved_classes.update(new_classes)
                    
                    # 处理基础模板
                    elif cursor.kind == clang.CursorKind.CLASS_TEMPLATE:
                        class_obj = self._create_template_base_class_from_cursor(cursor, existing_classes)
                        if class_obj:
                            usr = cursor.get_usr()
                            if usr:
                                resolved_classes[usr] = class_obj
                    
                    # 分析依赖的类型
                    dependent_types = self._extract_dependent_types_from_cursor(cursor)
                    self._update_type_dependency_graph(type_name, dependent_types)
                    
                    # 递归解析依赖的类型（但要避免无限递归）
                    for dep_type in dependent_types:
                        if (dep_type != type_name and 
                            dep_type not in self._recursion_stack and 
                            not self.is_type_fully_resolved(dep_type)):
                            
                            # 检查依赖类型是否正在被解析，避免循环依赖导致的死循环
                            if self.shared_cache.is_class_being_resolved("", dep_type):
                                self.logger.debug(f"跳过正在解析的依赖类型: {dep_type} (避免循环依赖)")
                                continue
                            
                            dep_cursor = self._find_cursor_for_type(dep_type)
                            if dep_cursor:
                                dep_classes = self.resolve_template_from_cursor(dep_cursor, existing_classes)
                                resolved_classes.update(dep_classes)
                
                # 标记为已解析（强制全局缓存）
                self.mark_type_as_resolved(
                    type_name=type_name,
                    resolved_classes=resolved_classes,
                    dependencies=self.type_dependency_graph.get(type_name, set())
                )
                
                self.logger.debug(f"模板类型解析完成: {type_name}, 发现 {len(resolved_classes)} 个类")
                
            except Exception as e:
                # 标记解析失败（强制全局缓存）
                self.shared_cache.mark_class_failed("", type_name, str(e))
                self.logger.debug(f"解析模板类型失败: {type_name} - {e}")
                raise
            finally:
                # 确保从递归栈中移除当前类型
                self._recursion_stack.discard(type_name)
            
        except Exception as e:
            self.logger.debug(f"从cursor解析模板类型时出错: {e}")
        
        return resolved_classes
    
    def resolve_template_from_type_name(self, type_name: str, existing_classes: Dict[str, Any]) -> Dict[str, Any]:
        """从类型名按需解析模板类型（强制全局缓存版本）"""
        resolved_classes = {}
        
        try:
            # 检查全局缓存
            self._cache_requests += 1
            if self.is_type_fully_resolved(type_name):
                self._cache_hits += 1
                cached_result = self.shared_cache.get_resolved_class("", type_name)
                if cached_result and 'resolved_classes' in cached_result:
                    return cached_result['resolved_classes']
            
            # 禁用字符串模板解析，直接抛错
            raise NotImplementedError(
                f"字符串模板解析已被禁用以提升性能。类型名: {type_name}\n"
                f"请使用基于 clang cursor 的模板解析方法。\n"
                f"尝试使用 resolve_template_from_cursor() 方法代替。"
            )

            
        except Exception as e:
            self.logger.debug(f"从类型名解析模板类型 {type_name} 时出错: {e}")
        
        return resolved_classes
    
    def _is_template_related_cursor(self, cursor) -> bool:
        """判断cursor是否与模板相关"""
        try:
            import clang.cindex as clang
            template_kinds = {
                clang.CursorKind.CLASS_TEMPLATE,
                clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
                clang.CursorKind.FUNCTION_TEMPLATE,
                clang.CursorKind.TEMPLATE_TYPE_PARAMETER,
                clang.CursorKind.TEMPLATE_NON_TYPE_PARAMETER,
                clang.CursorKind.TEMPLATE_TEMPLATE_PARAMETER,
            }
            
            if cursor.kind in template_kinds:
                return True
            
            # 检查是否为模板实例化的类型
            if hasattr(cursor, 'type') and cursor.type:
                type_spelling = cursor.type.spelling
                return '<' in type_spelling and '>' in type_spelling
                
        except Exception as e:
            self.logger.debug(f"检查模板相关cursor时出错: {e}")
        
        return False
    
    def _extract_dependent_types_from_cursor(self, cursor) -> Set[str]:
        """从cursor中提取依赖的类型"""
        dependent_types = set()
        
        try:
            # 分析基类
            for child in cursor.get_children():
                if hasattr(child, 'kind'):
                    import clang.cindex as clang
                    if child.kind == clang.CursorKind.CXX_BASE_SPECIFIER:
                        if hasattr(child, 'type') and child.type:
                            dependent_types.add(child.type.spelling)
            
            # 分析成员变量的类型
            for child in cursor.get_children():
                if hasattr(child, 'kind'):
                    import clang.cindex as clang
                    if child.kind == clang.CursorKind.FIELD_DECL:
                        if hasattr(child, 'type') and child.type:
                            type_spelling = child.type.spelling
                            if '<' in type_spelling and '>' in type_spelling:
                                dependent_types.add(type_spelling)
                                
        except Exception as e:
            self.logger.debug(f"提取依赖类型时出错: {e}")
        
        return dependent_types
    
    def _update_type_dependency_graph(self, type_name: str, dependent_types: Set[str]):
        """更新类型依赖图"""
        if type_name not in self.type_dependency_graph:
            self.type_dependency_graph[type_name] = set()
        self.type_dependency_graph[type_name].update(dependent_types)
    
    def _parse_template_type_name(self, type_name: str) -> tuple[str, List[str]]:
        """废弃：不再支持字符串解析模板类型名"""
        raise NotImplementedError(
            f"字符串模板解析已被禁用以提升性能。类型名: {type_name}\n"
            f"请使用基于 clang cursor 的模板解析方法。\n"
            f"如果这是必需的功能，请重新设计以避免字符串解析。"
        )
    
    def _find_cursor_for_type(self, type_name: str):
        """根据类型名查找对应的cursor"""
        try:
            # 首先在usr_to_cursor_map中查找
            for usr, cursor in self.usr_to_cursor_map.items():
                if cursor.spelling == type_name or cursor.displayname == type_name:
                    return cursor
            
            # 如果找不到，尝试模糊匹配
            for usr, cursor in self.usr_to_cursor_map.items():
                if type_name in cursor.spelling or type_name in cursor.displayname:
                    return cursor
                    
        except Exception as e:
            self.logger.debug(f"查找类型 {type_name} 的cursor时出错: {e}")
        
        return None
    
    def _build_complete_usr_cursor_mapping(self, compile_commands: Dict[str, Any]):
        """构建完整的USR到cursor映射"""
        try:
            if not self.clang_parser:
                return
            
            # 遍历所有已解析的translation units
            for file_path, args in compile_commands.items():
                try:
                    # 重新解析文件以获取完整的AST
                    translation_unit = self.clang_parser._parse_file_with_cache(file_path, args)
                    if translation_unit:
                        self._build_usr_cursor_mapping(translation_unit)
                        
                except Exception as e:
                    self.logger.debug(f"构建完整映射时解析文件出错 {file_path}: {e}")
                    
        except Exception as e:
            self.logger.debug(f"构建完整USR映射时出错: {e}")
    
    def _create_class_from_cursor(self, cursor) -> Optional[Any]:
        """从cursor创建类对象（简化版本）"""
        if not cursor:
            return None
        
        try:
            import clang.cindex as clang
            
            # 只处理类相关的cursor
            if cursor.kind not in [clang.CursorKind.CLASS_DECL, 
                                  clang.CursorKind.STRUCT_DECL,
                                  clang.CursorKind.UNION_DECL,
                                  clang.CursorKind.CLASS_TEMPLATE,
                                  clang.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION]:
                return None
            
            # 这里需要导入Class，但为了避免循环导入，返回基本信息
            class_info = {
                'name': cursor.spelling or cursor.displayname,
                'usr': cursor.get_usr(),
                'kind': cursor.kind,
                'location': cursor.location.file.name if cursor.location.file else "<unknown>",
                'line': cursor.location.line if hasattr(cursor.location, 'line') else 0
            }
            
            return class_info
            
        except Exception as e:
            self.logger.debug(f"从cursor创建类对象时出错: {e}")
            return None