# Code Analyzer - JSON Output Format Documentation

This document describes the JSON output formats for the Code Analyzer. It was originally created for Lua (via Emmylua) and has been extended to support Python and C# in a backward-compatible way.

**Note on Language Extensions:** For details on language-specific fields (Python, C#), please see the corresponding sections added before the Changelog. Existing parsers for the original Lua format can safely ignore these new fields.

## Table of Contents
- [Call Chain Analysis JSON Format](#call-chain-analysis-json-format)
- [OOP Analysis JSON Format](#oop-analysis-json-format)
- [Project Analysis JSON Format](#project-analysis-json-format)
- [Combined Analysis JSON Format](#combined-analysis-json-format)

## Call Chain Analysis JSON Format

The call chain analysis produces a JSON structure that represents function call relationships across your Lua codebase.

### Top-Level Structure

```json
{
  "version": "1.0",
  "timestamp": "2024-01-15T10:30:00Z",
  "files": {
    "path/to/file.lua": {
      "functions": { ... },
      "calls": [ ... ]
    }
  },
  "call_chains": { ... },
  "statistics": { ... }
}
```

### File Entry

Each file contains information about functions defined and calls made within that file.

**Note**: As of the latest update, function names now include full signatures with parameters and return indicators.

```json
{
  "path/to/file.lua": {
    "functions": {
      "function_name": {
        "name": "function_name(param1, param2) -> ...",
        "start_line": 10,
        "end_line": 25,
        "is_local": true,
        "parameters": ["param1", "param2"],
        "calls_to": ["other_function", "module.function"],
        "called_by": ["main", "init"]
      }
    },
    "calls": [
      {
        "from": "function_name",
        "to": "other_function",
        "line": 15,
        "column": 8,
        "type": "direct",
        "resolved_path": "path/to/other_file.lua"
      }
    ]
  }
}
```

#### Function Signature Format

The `name` field in function entries now includes:
- **Function name**: The original function name
- **Parameters**: Full parameter list in parentheses, e.g., `(param1, param2, ...)`
- **Return indicator**: `-> ...` is appended if the function contains return statements

Examples:
- `"processData(input, options)"` - Function with parameters but no return
- `"calculate(x, y) -> ..."` - Function with parameters and return value(s)
- `"initialize()"` - Function with no parameters and no return
- `"getValue() -> ..."` - Function with no parameters but has return value(s)

#### Complete Example Output

Here's a real example showing the new function signature format in the context of the full JSON structure:

```json
{
  "call_graph": {
    "main": ["init", "processData"],
    "processData": ["validate", "transform", "save"],
    "validate": ["checkType", "checkRange"]
  },
  "functions": {
    "main": {
      "name": "main(args) -> ...",
      "file": "app.lua",
      "is_local": false,
      "is_method": false,
      "location": [10, 50]
    },
    "processData": {
      "name": "processData(data, options)",
      "file": "processor.lua",
      "is_local": true,
      "is_method": false,
      "location": [15, 45]
    },
    "validate": {
      "name": "validate(input) -> ...",
      "file": "validator.lua",
      "is_local": false,
      "is_method": false,
      "location": [5, 25]
    },
    "Config:load": {
      "name": "Config:load(filepath) -> ...",
      "file": "config.lua",
      "is_local": false,
      "is_method": true,
      "location": [30, 60]
    }
  },
  "reverse_graph": {
    "init": ["main"],
    "processData": ["main"],
    "validate": ["processData"]
  }
}
```

### Call Chain Entry

```json
{
  "call_chains": {
    "main": {
      "entry_point": "main",
      "file": "main.lua",
      "chains": [
        {
          "path": ["main", "init", "loadConfig", "parseJSON"],
          "depth": 4,
          "has_recursion": false
        }
      ]
    }
  }
}
```

### Statistics

```json
{
  "statistics": {
    "total_functions": 45,
    "total_calls": 123,
    "cross_file_calls": 34,
    "max_call_depth": 7,
    "recursive_functions": ["fibonacci", "tree_walk"],
    "unreachable_functions": ["unused_helper"],
    "entry_points": ["main", "test_suite"]
  }
}
```

## OOP Analysis JSON Format

The OOP analysis produces a comprehensive JSON structure representing object-oriented patterns found in your Lua code.

### Top-Level Structure

```json
{
  "version": "1.0",
  "timestamp": "2024-01-15T10:30:00Z",
  "classes": { ... },
  "inheritance_graph": { ... },
  "method_resolution_orders": { ... },
  "statistics": { ... }
}
```

### Class Definition

```json
{
  "classes": {
    "Player": {
      "name": "Player",
      "file": "game/player.lua",
      "line": 15,
      "parent_classes": ["Character", "Serializable"],
      "is_abstract": false,
      "is_mixin": false,
      "documentation": "Represents a player character in the game",
      "metadata": {
        "author": "GameDev Team",
        "since": "v1.0"
      },
      "methods": [
        {
          "name": "ctor",
          "type": "constructor",
          "visibility": "public",
          "start_line": 20,
          "end_line": 25,
          "parameters": [
            {
              "name": "name",
              "type": "string",
              "optional": false
            },
            {
              "name": "level",
              "type": "number",
              "optional": true,
              "default": "1"
            }
          ],
          "documentation": "Creates a new player instance"
        },
        {
          "name": "levelUp",
          "type": "instance",
          "visibility": "public",
          "start_line": 27,
          "end_line": 30,
          "parameters": [],
          "documentation": "Increases player level by 1"
        },
        {
          "name": "getMaxLevel",
          "type": "static",
          "visibility": "public",
          "start_line": 32,
          "end_line": 34,
          "parameters": [],
          "return_type": "number",
          "documentation": "Returns the maximum achievable level"
        }
      ],
      "fields": [
        {
          "name": "health",
          "visibility": "private",
          "type": "number",
          "documentation": "Current health points"
        },
        {
          "name": "inventory",
          "visibility": "public",
          "type": "table",
          "documentation": "Player's item inventory"
        }
      ]
    }
  }
}
```

### Inheritance Graph

```json
{
  "inheritance_graph": {
    "nodes": [
      {
        "class": "Object",
        "is_root": true,
        "children": ["Character", "Item"]
      },
      {
        "class": "Character",
        "is_root": false,
        "parents": ["Object"],
        "children": ["Player", "NPC"]
      },
      {
        "class": "Player",
        "is_root": false,
        "parents": ["Character", "Serializable"],
        "children": []
      }
    ],
    "edges": [
      {
        "from": "Object",
        "to": "Character",
        "type": "inheritance"
      },
      {
        "from": "Character",
        "to": "Player",
        "type": "inheritance"
      },
      {
        "from": "Serializable",
        "to": "Player",
        "type": "mixin"
      }
    ]
  }
}
```

### Method Resolution Orders

```json
{
  "method_resolution_orders": {
    "Player": ["Player", "Character", "Serializable", "Object"],
    "NPC": ["NPC", "Character", "Object"]
  }
}
```

### OOP Statistics

```json
{
  "statistics": {
    "total_classes": 15,
    "abstract_classes": 3,
    "mixins": 5,
    "max_inheritance_depth": 4,
    "average_methods_per_class": 8.5,
    "average_fields_per_class": 4.2,
    "classes_with_multiple_inheritance": 3,
    "most_derived_class": {
      "name": "BossEnemy",
      "depth": 4
    },
    "largest_class": {
      "name": "GameEngine",
      "method_count": 45,
      "field_count": 23
    },
    "circular_inheritances": [
      ["ClassA", "ClassB", "ClassC", "ClassA"]
    ],
    "pattern_usage": {
      "DefineClass": 12,
      "CustomClass": 3
    }
  }
}
```

## Project Analysis JSON Format

The project analysis provides overall project structure and dependencies:

### Top-Level Structure

```json
{
  "version": "1.0",
  "timestamp": "2024-01-15T10:30:00Z",
  "modules": { ... },
  "dependencies": { ... },
  "symbols": { ... },
  "statistics": { ... }
}
```

### Module Entry

```json
{
  "modules": {
    "game.player": {
      "file": "game/player.lua",
      "exports": ["Player", "PlayerController"],
      "imports": ["game.character", "utils.math"],
      "dependencies": {
        "direct": ["game.character", "utils.math"],
        "transitive": ["game.object", "core.base"]
      }
    }
  }
}
```

### Symbol Table

```json
{
  "symbols": {
    "global": {
      "Player": {
        "type": "class",
        "file": "game/player.lua",
        "line": 15
      },
      "gameConfig": {
        "type": "table",
        "file": "config.lua",
        "line": 1
      }
    },
    "functions": {
      "initGame": {
        "file": "main.lua",
        "line": 10,
        "is_global": true
      }
    }
  }
}
```

## Combined Analysis JSON Format

When running all analyzers together, the output combines all analysis results:

```json
{
  "version": "1.0",
  "timestamp": "2024-01-15T10:30:00Z",
  "project": {
    "name": "MyLuaProject",
    "root": "/path/to/project",
    "files_analyzed": 45,
    "total_lines": 12500
  },
  "call_chain_analysis": {
    // Call chain analysis results
  },
  "oop_analysis": {
    // OOP analysis results
  },
  "project_analysis": {
    // Project analysis results
  },
  "cross_analysis": {
    "class_method_calls": {
      "Player.attack": {
        "calls": ["Enemy.takeDamage", "Effect.play"],
        "called_by": ["GameLoop.update", "Player.performAction"]
      }
    },
    "inheritance_dependencies": {
      "Player": {
        "requires_modules": ["game.character", "traits.serializable"],
        "required_by_modules": ["game.save_system", "ui.player_panel"]
      }
    },
    "entry_points_by_class": {
      "GameEngine": ["main", "init"],
      "TestSuite": ["runTests", "runBenchmarks"]
    }
  },
  "diagnostics": {
    "errors": [
      {
        "type": "circular_inheritance",
        "severity": "error",
        "message": "Circular inheritance detected: A -> B -> C -> A",
        "location": {
          "file": "classes.lua",
          "line": 45
        }
      }
    ],
    "warnings": [
      {
        "type": "unused_method",
        "severity": "warning",
        "message": "Method 'Player.oldFunction' is never called",
        "location": {
          "file": "game/player.lua",
          "line": 120
        }
      }
    ]
  }
}
```

## Usage Examples

### Parsing Call Chain JSON

```python
import json

with open('call_chain_output.json', 'r') as f:
    data = json.load(f)

# Find all functions that call a specific function
target_function = "processData"
callers = []
for file_path, file_data in data['files'].items():
    for call in file_data['calls']:
        if call['to'] == target_function:
            callers.append({
                'file': file_path,
                'function': call['from'],
                'line': call['line']
            })
```

### Parsing OOP Analysis JSON

```python
import json

with open('oop_analysis.json', 'r') as f:
    data = json.load(f)

# Build class hierarchy tree
def print_hierarchy(class_name, indent=0):
    class_info = data['classes'].get(class_name)
    if class_info:
        print("  " * indent + f"- {class_name}")
        # Find children
        for node in data['inheritance_graph']['nodes']:
            if class_name in node.get('parents', []):
                print_hierarchy(node['class'], indent + 1)

# Print hierarchy starting from root classes
for node in data['inheritance_graph']['nodes']:
    if node.get('is_root', False):
        print_hierarchy(node['class'])
```

### Generating Reports

```python
import json

def generate_complexity_report(analysis_data):
    report = []
    
    # Call chain complexity
    cc_stats = analysis_data['call_chain_analysis']['statistics']
    report.append(f"Max call depth: {cc_stats['max_call_depth']}")
    report.append(f"Recursive functions: {len(cc_stats['recursive_functions'])}")
    
    # OOP complexity
    oop_stats = analysis_data['oop_analysis']['statistics']
    report.append(f"Total classes: {oop_stats['total_classes']}")
    report.append(f"Max inheritance depth: {oop_stats['max_inheritance_depth']}")
    report.append(f"Multiple inheritance cases: {oop_stats['classes_with_multiple_inheritance']}")
    
    # Circular dependencies
    circular_count = len(oop_stats.get('circular_inheritances', []))
    if circular_count > 0:
        report.append(f"WARNING: {circular_count} circular inheritance chains detected!")
    
    return "\n".join(report)
```

## Schema Validation

JSON schemas are available for validating the output:

- `schemas/call_chain_schema.json`
- `schemas/oop_analysis_schema.json`
- `schemas/project_analysis_schema.json`
- `schemas/combined_analysis_schema.json`

Example validation:

```python
import json
import jsonschema

def validate_output(json_file, schema_file):
    with open(json_file, 'r') as f:
        data = json.load(f)
    
    with open(schema_file, 'r') as f:
        schema = json.load(f)
    
    try:
        jsonschema.validate(data, schema)
        print("✓ JSON output is valid")
    except jsonschema.exceptions.ValidationError as e:
---

## Python Project Analysis JSON Format

This section describes the comprehensive, project-wide analysis format generated by the Python analyzer. This format is self-contained and provides a full picture of the project.

### Top-Level Structure

The JSON output consists of three main sections: `project_call_graph`, `oop_analysis`, and `summary`.

```json
{
  "project_call_graph": { ... },
  "oop_analysis": { ... },
  "summary": { ... }
}
```

### 1. `project_call_graph`

This section contains all data related to function and method calls within the project.

#### `project_info`

Provides high-level metrics about the analyzed project.

**Example:**
```json
"project_info": {
  "root_path": "ai-agent-kgagent/",
  "total_files": 40,
  "total_functions": 227,
  "analysis_timestamp": "2025-07-24T12:48:16.f+0800"
}
```

#### `modules`

A dictionary where each key is a file path. Each entry details the file's imports, functions, and local call graphs.

**Example Module Entry:**
```json
"modules": {
  "debug_location.py": {
    "file_path": "debug_location.py",
    "imports": [
      {
        "module": "os",
        "alias": null
      }
    ],
    "functions": {
      "test_newline_handling": {
        "name": "test_newline_handling(file_path) -> ...",
        "qualified_name": "debug_location.test_newline_handling"
      }
    },
    "local_calls": { ... }
  }
}
```

### 2. `oop_analysis`

This section provides data related to classes, inheritance, and methods.

#### `classes`

A dictionary of all classes found in the project. Each entry contains details about the class, its methods, and parent classes.

**Example Class Entry:**
```json
"classes": {
  "Toolbox": {
    "name": "Toolbox",
    "file_path": "agent/tools/tool_base.py",
    "parent_classes": [],
    "methods": {
      "Toolbox:register_action": {
        "name": "register_action(cls, type, prefix) -> ...",
        "qualified_name": "agent.tools.tool_base.Toolbox.register_action"
      }
    }
  }
}
```

### 3. `summary`

Provides a final summary of the analysis.

**Example:**
```json
"summary": {
  "total_files": 40,
  "total_functions": 227,
  "total_classes": 37,
  "timestamp": "2025-07-24T12:48:16.f+0800"
}
```

---

## C# Analysis JSON Format Extensions

This section details the backward-compatible extensions for C# analysis. These fields are added to the existing structures and can be safely ignored by parsers that only target Lua or Python. It is recommended to add a top-level `"language": "csharp"` field to the output.

### Method Definition (C# Extension)

C#-specific details are added inside a `csharp_extensions` object within a function/method's definition.

```json
"MyMethod": {
  // ... base fields from Lua format ...
  "csharp_extensions": {
    "qualified_name": "MyNamespace.MyClass.MyMethod",
    "is_async": true,
    "is_static": false,
    "is_extension_method": false,
    "visibility": "public", // "public", "protected", "internal", "private"
    "attributes": ["TestMethod", "Obsolete(\"Use NewMethod instead\")"],
    "type_parameters": ["T", "U"],
    "return_type": "Task<List<string>>",
    "parameters": { "param1": "string", "param2": "int" },
    "docstring_id": "M:MyNamespace.MyClass.MyMethod(System.String,System.Int32)"
  }
}
```

### Class/Interface/Struct Definition (C# Extension)

C#-specific OOP details are added inside a `csharp_oop_extensions` object.

```json
"MyClass": {
  // ... base fields from Lua format ...
  "csharp_oop_extensions": {
    "qualified_name": "MyNamespace.MyClass",
    "type": "class", // "class", "struct", "interface", "enum"
    "is_abstract": true,
    "is_sealed": false,
    "is_static": false,
    "is_partial": true,
    "base_types": ["BaseClass", "IMyInterface"],
    "attributes": ["Serializable"],
    "properties": [
      { "name": "MyProperty", "type": "string", "visibility": "public" }
    ],
    "events": [
      { "name": "MyEvent", "type": "EventHandler", "visibility": "public" }
    ],
    "mro": ["MyClass", "BaseClass", "object"]
  }
}
```

### Call Definition (C# Extension)

```json
{
  // ... base fields from Lua format ...
  "csharp_call_info": {
    "call_type": "method_invocation", // e.g., method_invocation, object_creation, delegate_invocation
    "is_async_await": true,
    "is_conditional": false // e.g., `?.` operator
  }
}
```

### Top-Level C#-Specific Sections

For project-wide C# information, new top-level keys are added.

```json
{
  // ... other top-level keys ...
  "csharp_analysis": {
    "namespaces": {
      "MyNamespace.Services": {
        "files": ["Service1.cs", "Service2.cs"]
      }
    },
    "using_directives": {
      "path/to/file.cs": {
        "global": ["System.Text.Json"],
        "local": ["System", "System.Collections.Generic", "MyNamespace.Models"]
      }
    },
    "assembly_references": ["System.dll", "Newtonsoft.Json.dll"]
  },
  "csharp_statistics": {
    "async_methods": 42,
    "extension_methods": 10,
    "structs": 5,
    "interfaces": 15,
    "attributes_usage": {
      "Serializable": 20,
      "TestMethod": 100
    }
  }
}
```

## Changelog

### Version 2.1 (Latest)
- **C# Support**: Added backward-compatible extensions for C# analysis using the Roslyn API. Includes new `csharp_*` namespaced fields for features like namespaces, attributes, properties, interfaces, and async/await.

### Version 2.0
- **Python Support**: Added backward-compatible extensions to support Python analysis. This includes new `python_*` namespaced fields to capture features like decorators, type hints, async functions, etc., without altering the base structure.
- **Generalization**: Renamed document from "Emmylua Analyzer" to "Code Analyzer" to reflect multi-language support. A top-level `language` field is recommended.

### Version 1.1 (Lua)
- **Enhanced Function Signatures**: Function names in the JSON output now include:
  - Full parameter lists in parentheses, e.g., `functionName(param1, param2)`
  - Return value indicators (`-> ...`) for functions that contain return statements
  - Examples:
    - `"calculate(x, y) -> ..."` - Function with parameters and return value
    - `"initialize()"` - Function with no parameters and no return
- **Backward Compatibility**: The function lookup keys remain unchanged; only the display names include signatures

### Version 1.0
- Initial release with basic call chain analysis
- Support for JSON, DOT, and Text output formats
- Function metadata including file location, local/global status, and method identification