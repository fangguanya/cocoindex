"""
高效文件过滤器模块

统一处理所有文件过滤逻辑，预编译模式以提高性能。
支持多种过滤模式：
- 通配符目录模式: */dirname/*
- 文件扩展名模式: *.ext  
- 路径包含模式: 直接字符串匹配
- 隐藏文件模式: */.*
"""

import re
from pathlib import Path
from typing import List, Set, Pattern, Tuple
from dataclasses import dataclass
from functools import lru_cache


@dataclass
class CompiledFilter:
    """预编译的过滤器"""
    # 目录模式 - 使用正则表达式预编译
    dir_patterns: List[Pattern[str]]
    dir_names: Set[str]  # 快速查找的目录名集合
    
    # 扩展名模式
    extensions: Set[str]  # 需要排除的文件扩展名
    
    # 路径包含模式
    path_contains: Set[str]  # 路径中需要包含的字符串
    
    # 特殊模式
    exclude_hidden: bool  # 是否排除隐藏文件


class UnifiedFileFilter:
    """统一的高效文件过滤器"""
    
    # 默认的Unreal Engine排除模式
    DEFAULT_EXCLUDE_PATTERNS = [
        '*/Intermediate/*',      # UE中间文件
        '*/Binaries/*',          # UE二进制文件
        '*/DerivedDataCache/*',  # UE缓存
        '*/Saved/*',             # UE保存文件
        '*/.vs/*',               # Visual Studio
        '*/.vscode/*',           # VS Code
        '*/.git/*',              # Git目录
        '*/node_modules/*',      # Node.js
        '*/__pycache__/*',       # Python缓存
        '*/CMakeFiles/*',        # CMake生成文件
        '*/build/*',             # 通用构建目录
        '*/dist/*',              # 发布目录
        '*/obj/*',               # 目标文件目录
        '*/Debug/*',             # Debug输出
        '*/Release/*',           # Release输出
        '*/x64/*',               # x64输出
        '*/Win32/*',             # Win32输出
        "*/.*",                  # 隐藏文件/目录
        "*/Build/*",             # UE构建目录
        "*/Content/*",           # UE内容目录
        "*.luac",                # Lua编译文件
        "*/Engine/Source/Programs/*",  # UE引擎程序源码
        "*/ThirdParty/*",        # 第三方库
        "*/Client_WwiseProject/*",  # Wwise项目文件
    ]
    
    def __init__(self, exclude_patterns: List[str] = None):
        """
        初始化文件过滤器
        
        Args:
            exclude_patterns: 排除模式列表，如果为None则使用默认模式
        """
        if exclude_patterns is None:
            exclude_patterns = self.DEFAULT_EXCLUDE_PATTERNS.copy()
        
        self.raw_patterns = exclude_patterns
        self.compiled_filter = self._compile_patterns(exclude_patterns)
        
        # 统计信息
        self.total_checks = 0
        self.excluded_count = 0
        self.cache_hits = 0
    
    def _compile_patterns(self, patterns: List[str]) -> CompiledFilter:
        """预编译所有过滤模式以提高性能"""
        dir_patterns = []
        dir_names = set()
        extensions = set()
        path_contains = set()
        exclude_hidden = False
        
        for pattern in patterns:
            # 处理通配符目录模式 */dirname/*
            if pattern.startswith('*/') and pattern.endswith('/*'):
                dir_name = pattern[2:-2]  # 去掉 */ 和 /*
                dir_names.add(dir_name)
                
                # 编译正则表达式，同时匹配 / 和 \ 作为路径分隔符
                regex_pattern = f'(?:^|[/\\\\]){re.escape(dir_name)}(?:[/\\\\]|$)'
                dir_patterns.append(re.compile(regex_pattern, re.IGNORECASE))
                
            # 处理文件扩展名模式 *.ext
            elif pattern.startswith('*.'):
                ext = pattern[1:]  # 去掉 *
                extensions.add(ext.lower())
                
            # 处理隐藏文件模式 */.*
            elif pattern == '*/.*':
                exclude_hidden = True
                
            # 处理直接包含的模式
            else:
                path_contains.add(pattern)
        
        return CompiledFilter(
            dir_patterns=dir_patterns,
            dir_names=dir_names,
            extensions=extensions,
            path_contains=path_contains,
            exclude_hidden=exclude_hidden
        )
    
    @lru_cache(maxsize=8192)  # 缓存最近检查的文件路径结果
    def should_exclude(self, file_path: str) -> bool:
        """
        检查文件是否应该被排除（带缓存）
        
        Args:
            file_path: 文件路径字符串
            
        Returns:
            bool: True表示应该排除，False表示应该包含
        """
        self.total_checks += 1
        
        # 快速检查：文件扩展名
        if self.compiled_filter.extensions:
            file_path_lower = file_path.lower()
            for ext in self.compiled_filter.extensions:
                if file_path_lower.endswith(ext):
                    self.excluded_count += 1
                    return True
        
        # 快速检查：路径包含模式
        for contains_pattern in self.compiled_filter.path_contains:
            if contains_pattern in file_path:
                self.excluded_count += 1
                return True
        
        # 快速检查：目录名是否在排除列表中
        path_obj = Path(file_path)
        path_parts = path_obj.parts
        
        # 检查是否包含需要排除的目录名
        for part in path_parts:
            if part in self.compiled_filter.dir_names:
                self.excluded_count += 1
                return True
        
        # 正则表达式检查（较慢，放在最后）
        for dir_pattern in self.compiled_filter.dir_patterns:
            if dir_pattern.search(file_path):
                self.excluded_count += 1
                return True
        
        # 检查隐藏文件
        if self.compiled_filter.exclude_hidden:
            for part in path_parts:
                if part.startswith('.') and part not in ('.', '..'):
                    self.excluded_count += 1
                    return True
        
        return False
    
    def should_exclude_path(self, file_path: Path) -> bool:
        """
        检查Path对象是否应该被排除
        
        Args:
            file_path: Path对象
            
        Returns:
            bool: True表示应该排除，False表示应该包含
        """
        return self.should_exclude(str(file_path))
    
    def filter_files(self, file_paths: List[Path]) -> List[Path]:
        """
        批量过滤文件列表
        
        Args:
            file_paths: 文件路径列表
            
        Returns:
            List[Path]: 过滤后的文件列表
        """
        result = []
        excluded_count = 0
        
        for file_path in file_paths:
            if not self.should_exclude_path(file_path):
                result.append(file_path)
            else:
                excluded_count += 1
        
        return result
    
    def get_statistics(self) -> dict:
        """获取过滤统计信息"""
        return {
            'total_checks': self.total_checks,
            'excluded_count': self.excluded_count,
            'included_count': self.total_checks - self.excluded_count,
            'exclusion_rate': (self.excluded_count / max(self.total_checks, 1)) * 100,
            'cache_info': self.should_exclude.cache_info()._asdict(),
            'compiled_patterns': {
                'dir_patterns_count': len(self.compiled_filter.dir_patterns),
                'dir_names_count': len(self.compiled_filter.dir_names),
                'extensions_count': len(self.compiled_filter.extensions),
                'path_contains_count': len(self.compiled_filter.path_contains),
                'exclude_hidden': self.compiled_filter.exclude_hidden
            }
        }
    
    def clear_cache(self):
        """清理缓存"""
        self.should_exclude.cache_clear()
    
    def add_exclude_pattern(self, pattern: str):
        """
        动态添加排除模式（会重新编译）
        
        Args:
            pattern: 新的排除模式
        """
        if pattern not in self.raw_patterns:
            self.raw_patterns.append(pattern)
            self.compiled_filter = self._compile_patterns(self.raw_patterns)
            self.clear_cache()
    
    def remove_exclude_pattern(self, pattern: str):
        """
        移除排除模式（会重新编译）
        
        Args:
            pattern: 要移除的排除模式
        """
        if pattern in self.raw_patterns:
            self.raw_patterns.remove(pattern)
            self.compiled_filter = self._compile_patterns(self.raw_patterns)
            self.clear_cache()


# 全局单例实例
_global_filter = None

def get_global_filter() -> UnifiedFileFilter:
    """获取全局文件过滤器单例"""
    global _global_filter
    if _global_filter is None:
        _global_filter = UnifiedFileFilter()
    return _global_filter

def set_global_filter(filter_instance: UnifiedFileFilter):
    """设置全局文件过滤器实例"""
    global _global_filter
    _global_filter = filter_instance

def create_unreal_filter() -> UnifiedFileFilter:
    """创建专门用于Unreal Engine项目的文件过滤器"""
    return UnifiedFileFilter(UnifiedFileFilter.DEFAULT_EXCLUDE_PATTERNS)


# 便捷函数
def should_exclude_file(file_path: str) -> bool:
    """使用全局过滤器检查文件是否应该被排除"""
    return get_global_filter().should_exclude(file_path)

def filter_cpp_files(file_paths: List[Path]) -> List[Path]:
    """使用全局过滤器过滤C++文件列表"""
    return get_global_filter().filter_files(file_paths) 