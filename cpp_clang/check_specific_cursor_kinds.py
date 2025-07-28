#!/usr/bin/env python3
"""检查特定的CursorKind是否存在"""

import clang.cindex as clang

def check_specific_kinds():
    """检查特定的CursorKind是否存在"""
    
    # 我们在代码中使用的CursorKind
    kinds_to_check = [
        'BINARY_OPERATOR',
        'UNARY_OPERATOR', 
        'CONDITIONAL_OPERATOR',
        'ARRAY_SUBSCRIPT_EXPR',
        'CSTYLE_CAST_EXPR',
        'CXX_FUNCTIONAL_CAST_EXPR',
        'CXX_STATIC_CAST_EXPR',
        'CXX_DYNAMIC_CAST_EXPR',
        'CXX_REINTERPRET_CAST_EXPR',
        'CXX_CONST_CAST_EXPR',
        'CXX_TRY_STMT',
        'CXX_CATCH_STMT',
        'CXX_FOR_RANGE_STMT',
        'TEMPLATE_REF',
        'CLASS_TEMPLATE_PARTIAL_SPECIALIZATION',
    ]
    
    print("检查特定的CursorKind是否存在:")
    for kind_name in kinds_to_check:
        if hasattr(clang.CursorKind, kind_name):
            kind_value = getattr(clang.CursorKind, kind_name)
            print(f"  ✅ clang.CursorKind.{kind_name} = {kind_value}")
        else:
            print(f"  ❌ clang.CursorKind.{kind_name} - 不存在")
            
    # 查找替代方案
    print(f"\n查找可能的替代方案:")
    all_kinds = [name for name in dir(clang.CursorKind) if not name.startswith('_')]
    
    for missing_kind in ['BINARY_OPERATOR', 'UNARY_OPERATOR', 'CONDITIONAL_OPERATOR']:
        if not hasattr(clang.CursorKind, missing_kind):
            print(f"\n为 {missing_kind} 查找替代:")
            for kind_name in all_kinds:
                if any(keyword in kind_name.lower() for keyword in missing_kind.lower().split('_')):
                    print(f"  可能的替代: clang.CursorKind.{kind_name}")

if __name__ == '__main__':
    check_specific_kinds()