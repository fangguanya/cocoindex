#!/usr/bin/env python3
"""检查libclang中可用的CursorKind枚举值"""

import clang.cindex as clang

def check_cursor_kinds():
    """检查可用的CursorKind枚举值"""
    print("检查libclang中的CursorKind枚举值...")
    
    # 查找与表达式和语句相关的CursorKind
    expr_stmt_kinds = []
    all_kinds = []
    
    for attr_name in dir(clang.CursorKind):
        if not attr_name.startswith('_'):
            try:
                attr_value = getattr(clang.CursorKind, attr_name)
                all_kinds.append((attr_name, attr_value))
                
                # 查找与表达式和语句相关的
                if any(keyword in attr_name.lower() for keyword in ['expr', 'stmt', 'statement', 'expression']):
                    expr_stmt_kinds.append((attr_name, attr_value))
            except:
                pass
    
    print(f"\n找到 {len(all_kinds)} 个CursorKind枚举值")
    print(f"其中 {len(expr_stmt_kinds)} 个与表达式/语句相关:")
    
    for name, value in sorted(expr_stmt_kinds):
        print(f"  clang.CursorKind.{name} = {value}")
    
    # 特别检查我们需要的几个
    needed_kinds = [
        'EXPR_STMT', 'EXPRESSION_STMT', 'DECL_STMT', 'DECLARATION_STMT',
        'RETURN_STMT', 'IF_STMT', 'FOR_STMT', 'WHILE_STMT', 'COMPOUND_STMT'
    ]
    
    print(f"\n检查特定的CursorKind:")
    for kind_name in needed_kinds:
        if hasattr(clang.CursorKind, kind_name):
            kind_value = getattr(clang.CursorKind, kind_name)
            print(f"  ✅ clang.CursorKind.{kind_name} = {kind_value}")
        else:
            print(f"  ❌ clang.CursorKind.{kind_name} - 不存在")

if __name__ == '__main__':
    check_cursor_kinds()