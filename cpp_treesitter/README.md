# Tree-sitter C++ 代码分析器

基于tree-sitter的C++代码分析器，替代clang实现，提供轻量级、高性能的C++代码分析功能。

## 特性

### 🚀 核心功能
- **完全兼容**: 输出格式严格遵循 `json_format.md` 规格
- **函数体提取**: 新增函数体文本提取功能（clang版本不支持）
- **语法解析**: 基于tree-sitter的高性能C++语法解析
- **实体提取**: 提取函数、类、命名空间、模板等C++实体
- **轻量部署**: 无需完整编译环境，只需tree-sitter库

### 🎯 技术优势
- **更快解析**: tree-sitter通常比clang解析更快
- **更好容错**: 对语法错误有更好的容错性
- **轻量安装**: 不需要LLVM/clang完整安装
- **易于集成**: 纯Python实现，易于集成到现有项目

## 安装依赖

```bash
# 安装Python依赖
pip install tree-sitter tree-sitter-cpp rich

# 或者使用requirements.txt
pip install -r requirements.txt
```

## 快速开始

### 1. 基本使用

```python
from treesitter_analyzer import TreeSitterCppAnalyzer

# 创建分析器
analyzer = TreeSitterCppAnalyzer()

# 分析C++项目
result = analyzer.analyze(
    project_root="/path/to/your/project",
    scan_directory="/path/to/source/code", 
    output_path="analysis_result.json",
    include_function_body=True  # 包含函数体文本
)

if result.success:
    print(f"分析完成！提取了 {len(result.extracted_entities['functions'])} 个函数")
else:
    print("分析失败:", result.statistics.get('error'))
```

### 2. 命令行使用

```bash
# 修改 analyze_cpp_treesitter.py 中的配置参数
python analyze_cpp_treesitter.py
```

### 3. 运行测试

```bash
# 运行测试套件验证功能
python test_treesitter_analyzer.py
```

## 配置选项

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `project_root` | str | 必填 | 项目根目录，用于路径映射 |
| `scan_directory` | str | 同project_root | 实际扫描的代码目录 |
| `output_path` | str | "treesitter_analysis_result.json" | 输出JSON文件路径 |
| `include_function_body` | bool | True | 是否包含函数体文本 |
| `verbose` | bool | False | 是否显示详细输出 |
| `max_files` | int | None | 限制处理的文件数量 |

## 输出格式

生成的JSON文件包含以下主要部分：

```json
{
  "version": "2.2",
  "language": "cpp", 
  "timestamp": "2025-01-26T...",
  "project_call_graph": {
    "functions": {
      "function_usr": {
        "name": "functionName",
        "qualified_name": "namespace::className::functionName",
        "signature": "int functionName(int param)",
        "return_type": "int",
        "parameters": [...],
        "documentation": "Function body:\n{ ... }", // 新增的函数体文本
        "cpp_extensions": {...}
      }
    },
    "classes": {...},
    "namespaces": {...}
  },
  "summary": {
    "parser_type": "tree-sitter",  // 标识解析器类型
    "function_body_included": true,
    "treesitter_stats": {
      "total_nodes_parsed": 12345,
      "function_bodies_extracted": 67,
      "avg_nodes_per_file": 156.7
    }
  }
}
```

## 与clang版本对比

| 特性 | Tree-sitter版本 | Clang版本 |
|------|----------------|-----------|
| 安装复杂度 | ⭐⭐ 简单 | ⭐⭐⭐⭐ 复杂 |
| 解析速度 | ⭐⭐⭐⭐ 快 | ⭐⭐⭐ 中等 |
| 内存占用 | ⭐⭐⭐⭐ 低 | ⭐⭐ 高 |
| 容错性 | ⭐⭐⭐⭐ 好 | ⭐⭐⭐ 中等 |
| 语义分析 | ⭐⭐ 基础 | ⭐⭐⭐⭐⭐ 完整 |
| 函数体提取 | ✅ 支持 | ❌ 不支持 |
| 编译环境依赖 | ❌ 不需要 | ✅ 需要 |

## 架构设计

```
cpp_treesitter/
├── analyze_cpp_treesitter.py       # 主入口脚本
├── test_treesitter_analyzer.py     # 测试脚本
├── treesitter_analyzer/
│   ├── __init__.py
│   ├── treesitter_parser.py        # Tree-sitter解析器
│   ├── treesitter_entity_extractor.py  # 实体提取器
│   └── treesitter_cpp_analyzer.py  # 主协调器
└── README.md
```

### 核心组件

1. **TreeSitterParser**: 基于tree-sitter的C++解析器
   - 文件解析和AST生成
   - 错误检测和诊断
   - 函数体文本提取

2. **TreeSitterEntityExtractor**: 实体提取器
   - 从AST提取函数、类、命名空间
   - 生成符合json_format.md的数据结构
   - 支持函数体文本包含

3. **TreeSitterCppAnalyzer**: 主协调器
   - 整合文件扫描、解析、提取、导出
   - 复用现有的文件扫描和JSON导出逻辑
   - 提供统一的分析接口

## 使用场景

### 适合使用tree-sitter版本的场景：
- 快速代码分析和索引
- CI/CD流水线中的代码分析
- 不需要完整语义信息的场景
- 需要函数体文本的应用
- 轻量级部署环境

### 适合使用clang版本的场景：
- 需要完整语义分析
- 精确的类型信息和重载解析
- 复杂的模板分析
- 静态分析工具

## 故障排除

### 常见问题

1. **ImportError: tree-sitter not found**
   ```bash
   pip install tree-sitter tree-sitter-cpp
   ```

2. **编码错误**
   - 解析器支持多种编码（UTF-8, UTF-16, Latin1等）
   - 自动fallback到错误忽略模式

3. **解析失败**
   - 检查C++语法是否正确
   - 查看diagnostic信息了解具体错误

### 性能优化

- 使用 `max_files` 限制文件数量进行快速测试
- 设置合适的 `exclude_patterns` 排除不必要的文件
- 关闭 `include_function_body` 可以提高处理速度

## 开发和贡献

```bash
# 克隆项目
git clone <repository>

# 安装开发依赖
pip install -r requirements.txt

# 运行测试
python test_treesitter_analyzer.py

# 运行分析
python analyze_cpp_treesitter.py
```

## 许可证

与原项目保持一致的许可证。

## 更新日志

### v1.0.0
- ✅ 完整的tree-sitter C++分析器实现
- ✅ 函数体文本提取功能
- ✅ 兼容json_format.md输出格式
- ✅ 完整的测试套件
- ✅ 详细的文档和使用说明 