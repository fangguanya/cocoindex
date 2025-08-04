#!/usr/bin/env python3
"""
路径解析修复工具
专门解决PCH文件中相对路径解析问题
"""

import os
import re
from typing import List, Dict, Tuple, Optional
from pathlib import Path

class PathResolver:
    """路径解析器"""
    
    def __init__(self, working_directory: str, logger=None):
        self.working_directory = working_directory
        self.logger = logger
        self.path_cache = {}  # 缓存已解析的路径
    
    def resolve_pch_includes(self, pch_file_path: str) -> Dict[str, str]:
        """解析PCH文件中的include路径"""
        if not os.path.exists(pch_file_path):
            return {}
        
        try:
            with open(pch_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            if self.logger:
                self.logger.error(f"读取PCH文件失败: {e}")
            return {}
        
        # 提取所有include语句
        include_pattern = r'#include\s*["<]([^">]+)[">]'
        includes = re.findall(include_pattern, content)
        
        resolved_paths = {}
        pch_dir = os.path.dirname(pch_file_path)
        
        for include_path in includes:
            resolved = self._resolve_single_path(include_path, pch_dir)
            if resolved:
                resolved_paths[include_path] = resolved
                if self.logger:
                    self.logger.debug(f"路径解析: {include_path} -> {resolved}")
            else:
                if self.logger:
                    self.logger.warning(f"无法解析路径: {include_path}")
        
        return resolved_paths
    
    def _resolve_single_path(self, include_path: str, reference_dir: str) -> Optional[str]:
        """解析单个include路径 - 简化版：只使用working_directory作为基准"""
        if include_path in self.path_cache:
            return self.path_cache[include_path]
        
        # 如果已经是绝对路径且存在，直接返回
        if os.path.isabs(include_path):
            if os.path.exists(include_path):
                self.path_cache[include_path] = include_path
                return include_path
            else:
                # 绝对路径但文件不存在，直接报错
                if self.logger:
                    self.logger.error(f"绝对路径文件不存在: {include_path}")
                self.path_cache[include_path] = None
                return None
        
        # 相对路径：从working_directory开始拼接
        resolved_path = os.path.join(self.working_directory, include_path)
        normalized_path = os.path.normpath(resolved_path)
        
        if os.path.exists(normalized_path):
            self.path_cache[include_path] = normalized_path
            if self.logger:
                self.logger.debug(f"路径解析成功: {include_path} -> {normalized_path}")
            return normalized_path
        else:
            # 从working_directory找不到，直接报错
            if self.logger:
                self.logger.error(f"相对路径文件不存在: {include_path} (基于工作目录: {self.working_directory})")
                self.logger.error(f"尝试的完整路径: {normalized_path}")
            self.path_cache[include_path] = None
            return None
    

    
    def create_fixed_pch_content(self, pch_file_path: str) -> str:
        """创建修复路径后的PCH内容"""
        if not os.path.exists(pch_file_path):
            return ""
        
        try:
            with open(pch_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            if self.logger:
                self.logger.error(f"读取PCH文件失败: {e}")
            return ""
        
        # 解析所有include路径
        resolved_paths = self.resolve_pch_includes(pch_file_path)
        
        # 替换内容中的路径
        fixed_content = content
        for original_path, resolved_path in resolved_paths.items():
            if resolved_path:
                # 替换include语句中的路径
                old_include = f'#include "{original_path}"'
                new_include = f'#include "{resolved_path}"'
                fixed_content = fixed_content.replace(old_include, new_include)
                
                # 也处理<>格式的include
                old_include_sys = f'#include <{original_path}>'
                new_include_sys = f'#include <{resolved_path}>'
                fixed_content = fixed_content.replace(old_include_sys, new_include_sys)
        
        return fixed_content
    
    def create_alternative_include_args(self, pch_file_path: str, original_args: List[str]) -> List[str]:
        """创建替代的include参数，直接包含解析后的文件而不是PCH"""
        resolved_paths = self.resolve_pch_includes(pch_file_path)
        
        # 过滤掉原始的PCH include参数
        filtered_args = []
        skip_next = False
        
        for i, arg in enumerate(original_args):
            if skip_next:
                skip_next = False
                continue
            
            if arg == '-include' and i + 1 < len(original_args):
                next_arg = original_args[i + 1]
                if pch_file_path in next_arg or 'PCH.' in next_arg:
                    # 跳过PCH的include，我们会用解析后的文件替换
                    skip_next = True
                    if self.logger:
                        self.logger.info(f"跳过PCH include: {next_arg}")
                    continue
            
            filtered_args.append(arg)
        
        # 添加解析后的文件
        for original_path, resolved_path in resolved_paths.items():
            if resolved_path and resolved_path.endswith('.h'):
                filtered_args.extend(['-include', resolved_path])
                if self.logger:
                    self.logger.info(f"添加解析后的include: {resolved_path}")
        
        return filtered_args
    
    def analyze_path_issues(self, pch_file_path: str) -> Dict[str, any]:
        """分析路径问题"""
        analysis = {
            "pch_file_exists": os.path.exists(pch_file_path),
            "working_directory": self.working_directory,
            "pch_directory": os.path.dirname(pch_file_path),
            "include_analysis": {},
            "resolution_success_rate": 0.0,
            "recommendations": []
        }
        
        if not analysis["pch_file_exists"]:
            analysis["recommendations"].append("PCH文件不存在")
            return analysis
        
        resolved_paths = self.resolve_pch_includes(pch_file_path)
        total_includes = len(resolved_paths)
        successful_resolutions = sum(1 for path in resolved_paths.values() if path is not None)
        
        analysis["include_analysis"] = {
            "total_includes": total_includes,
            "resolved_includes": successful_resolutions,
            "failed_includes": total_includes - successful_resolutions,
            "resolution_details": resolved_paths
        }
        
        if total_includes > 0:
            analysis["resolution_success_rate"] = successful_resolutions / total_includes
        
        # 生成建议
        if analysis["resolution_success_rate"] < 1.0:
            analysis["recommendations"].append("存在无法解析的include路径")
            
        if analysis["resolution_success_rate"] == 0.0:
            analysis["recommendations"].append("所有include路径都无法解析，可能需要调整工作目录")
        elif analysis["resolution_success_rate"] < 0.5:
            analysis["recommendations"].append("大部分include路径无法解析，建议检查项目结构")
        
        return analysis

def test_path_resolver():
    """测试路径解析器"""
    print("🧪 测试路径解析器")
    print("=" * 50)
    
    working_dir = "N:/c7_enginedev/Engine/Source"
    pch_file = "N:/c7_enginedev/Client/Plugins/GMESDK/Intermediate/Build/Win64/x64/UnrealEditor/Development/GMESDK/PCH.GMESDK.h"
    
    resolver = PathResolver(working_dir)
    
    print(f"📁 工作目录: {working_dir}")
    print(f"📄 PCH文件: {pch_file}")
    print()
    
    # 分析路径问题
    analysis = resolver.analyze_path_issues(pch_file)
    
    print("📊 路径分析结果:")
    print(f"   PCH文件存在: {'✅' if analysis['pch_file_exists'] else '❌'}")
    
    include_analysis = analysis.get("include_analysis", {})
    if include_analysis:
        total = include_analysis.get("total_includes", 0)
        resolved = include_analysis.get("resolved_includes", 0)
        success_rate = analysis.get("resolution_success_rate", 0) * 100
        
        print(f"   总include数: {total}")
        print(f"   成功解析数: {resolved}")
        print(f"   成功率: {success_rate:.1f}%")
        
        print("\n📋 路径解析详情:")
        resolution_details = include_analysis.get("resolution_details", {})
        for original, resolved in resolution_details.items():
            status = "✅" if resolved else "❌"
            print(f"   {status} {original}")
            if resolved:
                print(f"      -> {resolved}")
    
    recommendations = analysis.get("recommendations", [])
    if recommendations:
        print(f"\n💡 建议:")
        for rec in recommendations:
            print(f"   • {rec}")
    
    # 测试创建替代参数
    print(f"\n🔧 测试创建替代include参数:")
    original_args = ['-x', 'c++', '-include', pch_file, '-std=c++20']
    alternative_args = resolver.create_alternative_include_args(pch_file, original_args)
    
    print(f"   原始参数数量: {len(original_args)}")
    print(f"   替代参数数量: {len(alternative_args)}")
    
    if len(alternative_args) != len(original_args):
        print("   参数变化:")
        for i, (orig, alt) in enumerate(zip(original_args, alternative_args)):
            if orig != alt:
                print(f"     {i}: {orig} -> {alt}")

if __name__ == "__main__":
    test_path_resolver()