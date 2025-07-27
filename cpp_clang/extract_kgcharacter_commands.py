#!/usr/bin/env python3
"""
从完整的compile_commands.json中提取KGCharacter相关的编译命令
"""

import json
import os
from pathlib import Path

def extract_kgcharacter_commands():
    """提取KGCharacter相关的编译命令"""
    
    # 源文件和目标文件路径
    source_file = Path("N:/c7_enginedev/compile_commands.json")
    target_file = Path("compile_commands.json")  # 直接保存在当前目录
    
    print(f"从 {source_file} 提取KGCharacter相关编译命令...")
    
    if not source_file.exists():
        print(f"错误: 源文件不存在: {source_file}")
        return False
    
    try:
        # 读取源文件
        with open(source_file, 'r', encoding='utf-8') as f:
            all_commands = json.load(f)
        
        print(f"已加载 {len(all_commands)} 个编译命令")
        
        # 过滤KGCharacter相关的文件
        kgcharacter_commands = []
        kgcharacter_path_pattern = "N:/c7_enginedev/Client/Plugins/KGCharacter/Source"
        
        for cmd in all_commands:
            file_path = cmd.get("file", "")
            if kgcharacter_path_pattern in file_path and file_path.endswith('.cpp'):
                kgcharacter_commands.append(cmd)
        
        print(f"找到 {len(kgcharacter_commands)} 个KGCharacter相关的C++文件")
        
        if not kgcharacter_commands:
            print("警告: 没有找到任何KGCharacter相关的编译命令")
            return False
        
        # 显示前几个文件作为示例
        print("\n前5个KGCharacter文件:")
        for i, cmd in enumerate(kgcharacter_commands[:5]):
            print(f"  {i+1}. {cmd['file']}")
        
        if len(kgcharacter_commands) > 5:
            print(f"  ... 和另外 {len(kgcharacter_commands) - 5} 个文件")
        
        # 保存到目标文件 - 覆盖现有文件
        print(f"\n准备保存到: {target_file.absolute()}")
        with open(target_file, 'w', encoding='utf-8') as f:
            json.dump(kgcharacter_commands, f, indent=2, ensure_ascii=False)
        
        print(f"\n✓ 已保存 {len(kgcharacter_commands)} 个编译命令到: {target_file.absolute()}")
        print(f"文件大小: {target_file.stat().st_size} 字节")
        
        return True
        
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = extract_kgcharacter_commands()
    if success:
        print("\n✓ KGCharacter编译命令提取完成！")
        print("现在可以运行: python analyze_cpp_project.py --max_files 10 -j 4")
    else:
        print("\n✗ 提取失败") 