"""
Clang Parser Module

Core C++ parsing functionality using libclang. Handles AST generation,
symbol resolution, and extraction of language constructs like functions,
classes, namespaces, and templates.
"""

import json
import time
import subprocess
import platform
import shlex
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass

import clang.cindex as clang
from rich.console import Console
from rich.progress import Progress, TaskID
from .logger import get_logger

@dataclass
class DiagnosticInfo:
    """诊断信息"""
    severity: str  # 'error', 'warning', 'info', 'note'
    message: str
    file_path: str
    line: int
    column: int
    category: str

@dataclass
class ParsedFile:
    """解析后的文件信息"""
    file_path: str
    translation_unit: Any  # clang.TranslationUnit
    success: bool
    diagnostics: List[DiagnosticInfo]
    parse_time: float

class ClangParser:
    """Clang解析器 - 支持compile_commands.json和动态编译参数"""
    
    def __init__(self, console: Optional[Console] = None):
        """初始化解析器"""
        self.console = console or Console()
        self.index = None
        # 修改数据结构：存储每个文件的编译参数和工作目录
        self.compile_commands: Dict[str, Dict[str, Any]] = {}  # file -> {"args": List[str], "directory": str}
        self._current_directory = ""  # 用于解析rsp相对路径
        self._working_directory = ""  # clang执行的工作目录（保留向后兼容）
        self._initialize_index()
    
    def _get_ue_constexpr_args(self) -> List[str]:
        """获取UE5.4 constexpr支持的专用编译参数 - 简化版本"""
        return [
            # 核心constexpr支持 - 这些是最关键的参数
            '-fconstexpr-steps=5000000',      # 大幅增加constexpr计算步数
            '-fconstexpr-depth=2048',         # 增加constexpr递归深度
            
            "-Wno-invalid-constexpr",   # 奇怪了，，关闭constexpr的编译错误先...add by fg
        ]
    
    def set_working_directory(self, working_dir: str):
        """设置clang执行的工作目录"""
        self._working_directory = working_dir
        if self.console:
            self.console.print(f"✓ 设置clang工作目录: {working_dir}", style="green")
    
    def _initialize_index(self):
        """初始化libclang索引"""
        logger = get_logger()
        try:
            self.index = clang.Index.create()
            if self.console:
                self.console.print("✓ libclang索引初始化成功", style="green")
            logger.info("libclang索引初始化成功")
        except Exception as e:
            if self.console:
                self.console.print(f"✗ libclang索引初始化失败: {e}", style="red")
            logger.error(f"libclang索引初始化失败: {e}")
            raise
    
    def load_compile_commands(self, compile_commands_path: str) -> bool:
        """加载compile_commands.json文件"""
        try:
            from .logger import get_logger
            logger = get_logger()
            
            logger.info(f"加载 compile_commands.json: {compile_commands_path}")
            
            with open(compile_commands_path, 'r', encoding='utf-8') as f:
                commands_data = json.load(f)
            
            self.compile_commands = self._parse_compile_commands(commands_data)
            
            logger.info(f"已加载 compile_commands.json: {len(self.compile_commands)} 文件")
            if len(self.compile_commands) > 0:
                sample_file = list(self.compile_commands.keys())[0]
                sample_args_data = self.compile_commands[sample_file]
                sample_args = sample_args_data["args"]
                logger.debug(f"示例编译参数 ({len(sample_args)} 个): {' '.join(sample_args[:5])}...")
            
            if self.console:
                self.console.print(f"✓ 已加载 compile_commands.json: {len(self.compile_commands)} 文件", style="green")
            
            return True
            
        except Exception as e:
            if self.console:
                self.console.print(f"✗ 加载 compile_commands.json 失败: {e}", style="red")
            return False
    
    def _parse_command_string(self, command: str) -> List[str]:
        """解析编译命令字符串，包括UE的@rsp文件支持"""
        try:
            # 使用shlex.split处理带引号的参数
            if platform.system() == 'Windows':
                args = shlex.split(command, posix=False)
            else:
                args = shlex.split(command)
            
            # 处理UE的@response_file.rsp格式
            processed_args = []
            for arg in args:
                if arg.startswith('@'):
                    # 解析.rsp响应文件
                    arg = arg[1:].strip('"\'')
                    rsp_args = self._parse_rsp_file(arg)
                    processed_args.extend(rsp_args)
                else:
                    processed_args.append(arg)
            
            return processed_args
            
        except ValueError:
            # 如果shlex.split失败，使用简单的空格分割
            return command.split()
    
    def _parse_rsp_file(self, rsp_path: str) -> List[str]:
        """解析UE的.rsp响应文件"""
        try:
            # 处理相对路径 - UE的rsp路径是相对于directory字段的
            if not Path(rsp_path).is_absolute() and self._current_directory:
                rsp_path = str(Path(self._current_directory) / rsp_path)
            
            # 尝试读取rsp文件
            rsp_file = Path(rsp_path)
            if rsp_file.exists():
                content = rsp_file.read_text(encoding='utf-8', errors='ignore')
                
                # rsp文件通常每行一个参数，或者用空格分隔
                args = []
                for line in content.splitlines():
                    line = line.strip()
                    if line and not line.startswith('#'):  # 跳过空行和注释
                        # 使用shlex分割每行，处理引号
                        try:
                            line_args = shlex.split(line)
                            args.extend(line_args)
                        except ValueError:
                            # 如果解析失败，按空格分割
                            args.extend(line.split())
                
                from .logger import get_logger
                logger = get_logger()
                logger.rsp_file_parsed(rsp_path, len(args))
                
                if self.console:
                    self.console.print(f"✓ 解析RSP文件: {rsp_path} ({len(args)} 参数)", style="green")
                
                return args
            else:
                from .logger import get_logger
                logger = get_logger()
                logger.warning(f"RSP文件未找到: {rsp_path}")
                return []
                
        except Exception as e:
            from .logger import get_logger
            logger = get_logger()
            logger.error(f"解析RSP文件失败 {rsp_path}: {e}")
            return []
    
    def _expand_response_files_recursive(self, args: List[str], visited_files: Optional[Set[str]] = None) -> List[str]:
        """递归展开所有@响应文件，防止循环引用"""
        if visited_files is None:
            visited_files = set()
        
        expanded_args = []
        for arg in args:
            if arg.startswith('@'):
                # 移除引号和@符号
                rsp_path = arg[1:].strip('"\'')
                
                # 获取绝对路径以便比较，防止循环引用
                if Path(rsp_path).is_absolute():
                    abs_path = str(Path(rsp_path).resolve())
                else:
                    if self._current_directory:
                        abs_path = str(Path(self._current_directory, rsp_path).resolve())
                    else:
                        abs_path = str(Path(rsp_path).resolve())
                
                if abs_path in visited_files:
                    # 检测到循环引用，记录警告并跳过
                    from .logger import get_logger
                    logger = get_logger()
                    logger.warning(f"检测到循环引用的响应文件: {abs_path}")
                    if self.console:
                        self.console.print(f"⚠️  跳过循环引用的RSP文件: {abs_path}", style="yellow")
                    continue
                
                # 标记当前文件为正在访问
                visited_files.add(abs_path)
                try:
                    # 解析响应文件
                    rsp_args = self._parse_rsp_file(rsp_path)
                    # 递归处理从响应文件中读取的参数
                    nested_expanded = self._expand_response_files_recursive(rsp_args, visited_files)
                    expanded_args.extend(nested_expanded)
                finally:
                    # 移除标记以允许在其他分支中使用相同文件
                    visited_files.discard(abs_path)
            else:
                expanded_args.append(arg)
        
        return expanded_args
    
    def _parse_compile_commands(self, commands_data: List[Dict]) -> Dict[str, Dict[str, Any]]:
        """解析compile_commands.json数据，支持UE的@rsp文件"""
        file_commands = {}
        
        for entry in commands_data:
            file_path = entry.get('file', '')
            command = entry.get('command', '')
            arguments = entry.get('arguments', [])
            directory = entry.get('directory', '')
            
            if not file_path:
                continue
            
            # 临时保存当前目录，用于解析rsp相对路径
            self._current_directory = directory
            
            # 解析编译命令
            if command:
                args = self._parse_command_string(command)
            elif arguments:
                args = arguments[1:]  # 去掉编译器路径
            else:
                continue
            
            # 处理和清理参数
            processed_args = self._process_compile_args(args)
            
            normalized_file_path = self._normalize_path(file_path)
            file_commands[normalized_file_path] = {"args": processed_args, "directory": directory}
        
        return file_commands
    
    def _convert_msvc_to_clang(self, arg: str) -> Optional[str]:
        """将MSVC参数转换为clang参数
        
        基于官方文档和实际测试的MSVC到clang参数映射表
        """
        
        # 标准转换表 - 基于官方文档验证
        conversions = {
            # C++ 标准
            '/std:c++20': '-std=c++20',
            '/std:c++17': '-std=c++17', 
            '/std:c++14': '-std=c++14',
            '/std:c++11': '-std=c++11',
            
            # 警告级别
            '/W0': '-w',
            '/W1': '-Wall',
            '/W2': '-Wall',
            '/W3': '-Wall',
            '/W4': '-Wall -Wextra',
            '/Wall': '-Wall -Wextra -Wpedantic',
            '/WX': '-Werror',
            
            # 优化
            '/O1': '-Os',      # 优化大小
            '/O2': '-O2',      # 优化速度
            '/Od': '-O0',      # 禁用优化
            '/Ox': '-O3',      # 最大优化
            '/Os': '-Os',      # 优化大小
            '/Ot': '-O2',      # 优化速度
            
            # 异常处理
            '/EHsc': '-fexceptions',
            '/EHs': '-fexceptions',
            '/EHc': '-fexceptions',
            '/EHa': '-fexceptions',  # 异步异常
            
            # RTTI
            '/GR': '',           # 启用RTTI（clang默认启用）
            '/GR-': '-fno-rtti', # 禁用RTTI
            
            # 调试信息
            '/Zi': '-g',
            '/Z7': '-g',
            '/ZI': '-g',
            
            # 代码生成
            '/TC': '-x c',       # 强制C模式
            '/TP': '-x c++',     # 强制C++模式
            '/Gd': '',           # __cdecl调用约定（默认）
            '/Gr': '',           # __fastcall调用约定
            '/Gz': '',           # __stdcall调用约定
            
            # 运行时库（clang-cl会自动处理）
            '/MT': '',           # 静态链接
            '/MTd': '',          # 静态链接调试版
            '/MD': '',           # 动态链接
            '/MDd': '',          # 动态链接调试版
            
            # 编译器行为
            '/permissive-': '-pedantic',
            '/nologo': '',       # 不显示版权信息
            '/bigobj': '',       # 大对象支持（clang不需要）
            
            # 特殊标志
            '/volatile:iso': '',      # ISO volatile语义
            '/volatile:ms': '',       # MS volatile语义
            '/Zc:wchar_t': '',        # wchar_t是内置类型
            '/Zc:wchar_t-': '',       # wchar_t不是内置类型
            '/Zc:forScope': '',       # for循环作用域
            '/Zc:inline': '',         # 内联函数处理
        }
        
        # 直接转换
        if arg in conversions:
            result = conversions[arg]
            return result if result else None
        
        # 模式匹配转换
        
        # 禁用特定警告 /wd4996 -> -Wno-deprecated-declarations
        if arg.startswith('/wd'):
            warning_id = arg[3:]
            warning_mappings = {
                '4996': '-Wno-deprecated-declarations',
                '4100': '-Wno-unused-parameter',
                '4101': '-Wno-unused-variable',
                '4189': '-Wno-unused-variable',
                '4244': '-Wno-conversion',
                '4267': '-Wno-conversion',
                '4305': '-Wno-literal-conversion',
                '4309': '-Wno-constant-conversion',
                '4456': '-Wno-shadow',
                '4457': '-Wno-shadow',
                '4458': '-Wno-shadow',
                '4459': '-Wno-shadow',
            }
            return warning_mappings.get(warning_id)
        
        # 预编译头参数（跳过，libclang不需要）
        if arg.startswith(('/Yc', '/Yu', '/Fp')):
            return None
            
        # 编译器一致性开关（大多数跳过）
        if arg.startswith('/Zc:'):
            # 少数几个重要的保留 - 增加constexpr相关参数
            important_zc = {
                '/Zc:__cplusplus': '',  # 正确的__cplusplus值
                '/Zc:sizedDealloc': '',  # C++14 sized deallocation
                '/Zc:constexpr': '',     # MSVC constexpr支持（UE核心参数已涵盖）
                '/Zc:strictStrings': '', # 严格字符串字面量处理
                '/Zc:implicitNoexcept': '', # 隐式noexcept
                '/Zc:lambda': '',       # C++11 lambda表达式
                '/Zc:auto': '',         # C++11 auto类型推导
                '/Zc:inline': '',       # 内联函数处理
                '/Zc:wchar_t': '',      # wchar_t类型处理
            }
            return important_zc.get(arg)
            
        # 链接器相关参数（直接跳过）
        if arg.startswith(('/link', '/ENTRY:', '/SUBSYSTEM:', '/MACHINE:', '/LIBPATH:')):
            return None
            
        # C++20模块相关参数（clang可能不完全支持）
        if arg.startswith(('/module:', '/interface', '/internalPartition')):
            return None
            
        # 其他优化参数
        if arg.startswith('/O') and len(arg) > 2:
            other_opts = {
                '/Ob0': '',     # 禁用内联
                '/Ob1': '',     # 只内联标记为inline的函数
                '/Ob2': '',     # 内联合适的函数
                '/Oi': '',      # 生成内置函数
                '/Oy': '',      # 省略帧指针
                '/Oy-': '-fno-omit-frame-pointer',
            }
            return other_opts.get(arg)
            
        # 控制流保护等安全特性
        if arg.startswith('/guard:'):
            return None  # clang有自己的控制流保护实现
            
        # 其他MSVC特定参数，跳过
        msvc_specific_prefixes = [
            '/await', '/experimental:', '/kernel', '/homeparams',
            '/Qfast_transcendentals', '/QIfist', '/Qimprecise_fwaits',
            '/Qpar', '/Qsafe_fp_loads', '/Qvec-report'
        ]
        
        for prefix in msvc_specific_prefixes:
            if arg.startswith(prefix):
                return None
        
        # 未知参数，返回None表示跳过
        return None

    def _process_compile_args(self, raw_args: List[str]) -> List[str]:
        """处理和清理编译参数"""
        # 使用递归方法展开所有@rsp文件（包括嵌套的）
        expanded_args = self._expand_response_files_recursive(raw_args)
        
        processed_args = []
        skip_next = False
        seen_includes = set()
        
        # 添加UE5.4专用的constexpr支持参数
        processed_args.extend(self._get_ue_constexpr_args())
        
        # 特殊处理：为UE constexpr问题添加额外的数学函数支持
        processed_args.extend([
            '-D__builtin_ctz=__builtin_ctz',  # 确保内置函数可用
            '-fno-builtin',                   # 禁用某些内置函数优化
            '-fno-strict-aliasing',           # 放宽别名规则
        ])
        
        for i, arg in enumerate(expanded_args):
            if skip_next:
                skip_next = False
                continue
            
            # 跳过编译器路径（处理带引号的情况）
            arg_clean = arg.strip('"\'')
            if arg_clean.endswith(('clang-cl.exe', 'clang.exe', 'gcc', 'g++')):
                continue
            
            # 跳过输入/输出文件
            if arg in ['-o', '-c', '/c'] and i + 1 < len(expanded_args):
                skip_next = True
                continue
            
            # 跳过源文件和目标文件
            if arg.endswith(('.cpp', '.cc', '.cxx', '.c', '.C', '.obj', '.o')):
                continue
            
            # 跳过链接器相关参数
            if arg.startswith(('/link', '-link', '/SUBSYSTEM', '/MACHINE')):
                continue
            
            # 处理include路径去重
            if arg in ['-I', '/I'] and i + 1 < len(expanded_args):
                include_path = expanded_args[i + 1]
                if include_path not in seen_includes:
                    processed_args.append('-I' + include_path)
                    seen_includes.add(include_path)
                skip_next = True
            elif arg.startswith(('-I', '/I')) and len(arg) > 2:
                include_path = arg[2:]
                if include_path not in seen_includes:
                    processed_args.append('-I' + include_path)
                    seen_includes.add(include_path)
            # 处理宏定义 - 改进的过滤逻辑
            elif arg.startswith(('-D', '/D')):
                macro_def = arg[2:]
                processed_macro = self._process_macro_definition(macro_def)
                if processed_macro:
                    processed_args.append('-D' + processed_macro)
            # 处理独立的宏定义值（当/D后面跟着单独的参数时）
            elif i > 0 and expanded_args[i-1] in ['-D', '/D']:
                processed_macro = self._process_macro_definition(arg)
                if processed_macro:
                    processed_args.append('-D' + processed_macro)
                # 注意：这种情况下不需要skip_next，因为当前参数已经被处理了
            # 处理MSVC外部include路径 /external:I -> -isystem (更准确的映射)
            elif arg in ['/external:I'] and i + 1 < len(expanded_args):
                include_path = expanded_args[i + 1]
                if include_path not in seen_includes:
                    processed_args.extend(['-isystem', include_path])
                    seen_includes.add(include_path)
                skip_next = True
            elif arg.startswith('/external:I') and len(arg) > 11:
                include_path = arg[11:]
                if include_path not in seen_includes:
                    processed_args.extend(['-isystem', include_path])
                    seen_includes.add(include_path)
            # 处理MSVC系统include路径 /imsvc -> -isystem (更准确的映射)
            elif arg in ['/imsvc'] and i + 1 < len(expanded_args):
                include_path = expanded_args[i + 1]
                if include_path not in seen_includes:
                    processed_args.extend(['-isystem', include_path])
                    seen_includes.add(include_path)
                skip_next = True
            elif arg.startswith('/imsvc') and len(arg) > 6:
                # 处理 /imsvc"路径" 或 /imsvc路径 格式
                include_path = arg[6:].strip('"\'')
                if include_path and include_path not in seen_includes:
                    processed_args.extend(['-isystem', include_path])
                    seen_includes.add(include_path)
            # 处理MSVC强制包含文件 /FI
            elif arg in ['/FI'] and i + 1 < len(expanded_args):
                force_include = expanded_args[i + 1]
                processed_args.extend(['-include', force_include])
                skip_next = True
            elif arg.startswith('/FI') and len(arg) > 3:
                force_include = arg[3:]
                processed_args.append('-include' + force_include)
            # 标准化MSVC参数为clang参数
            elif arg.startswith('/'):
                converted = self._convert_msvc_to_clang(arg)
                if converted:
                    # 处理可能包含多个参数的转换结果
                    if ' ' in converted:
                        processed_args.extend(converted.split())
                    else:
                        processed_args.append(converted)
                # 如果converted为None，则跳过该参数
                continue
            else:
                processed_args.append(arg)
        
        return processed_args
    
    def _process_macro_definition(self, macro_def: str) -> Optional[str]:
        """处理宏定义，修复常见的语法问题"""
        # 跳过一些可能有问题的宏定义，但保留常用的Windows宏
        if any(skip_pattern in macro_def for skip_pattern in ['WIN32_LEAN_AND_MEAN', 'NOMINMAX']):
            return macro_def
        
        # 修正无效的宏定义语法 UCLASS() -> UCLASS=
        if '()' in macro_def and '=' not in macro_def:
            macro_name = macro_def.replace('()', '')
            # UE特定宏的处理
            if macro_name in ['UCLASS', 'USTRUCT', 'UENUM', 'UFUNCTION', 'UPROPERTY', 
                             'GENERATED_BODY', 'GENERATED_UCLASS_BODY', 'GENERATED_USTRUCT_BODY']:
                return f'{macro_name}='
            else:
                return f'{macro_name}='
        
        # 处理一些可能导致解析问题的宏
        if macro_def in ['UNICODE', '_UNICODE']:
            return macro_def
        
        # 跳过一些可能有问题的MSVC特定宏
        problematic_macros = ['_MSC_VER', '_WIN32', '_WIN64']
        if any(problematic in macro_def for problematic in problematic_macros):
            # 这些宏通常会自动定义，跳过可能重复的定义
            return None
        
        return macro_def
    
    def _get_file_compile_args(self, file_path: str) -> List[str]:
        """获取文件特定的编译参数"""
        # 规范化输入路径
        normalized_input = self._normalize_path(file_path)
        return self.compile_commands[normalized_input]["args"]
    
    def _get_file_directory(self, file_path: str) -> str:
        """获取文件的编译目录"""
        normalized_input = self._normalize_path(file_path)
        return self.compile_commands[normalized_input]["directory"]
    
    def _normalize_path(self, path: str) -> str:
        """规范化路径 - 处理大小写、软链接等"""
        try:
            # 解析软链接并转换为绝对路径
            resolved = Path(path).resolve()
            # 在Windows下统一为小写，在Unix系统下保持原样
            import platform
            if platform.system().lower() == 'windows':
                return resolved.as_posix().lower()
            else:
                return resolved.as_posix()
        except Exception:
            # 如果路径规范化失败，返回原路径
            return str(path)
    
    def parse_files(self, file_paths: List[str], progress: Optional[Progress] = None, 
                   task_id: Optional[TaskID] = None) -> List[ParsedFile]:
        """解析多个文件"""
        results = []
        
        for i, file_path in enumerate(file_paths):
            if progress and task_id:
                progress.update(task_id, completed=i)
            
            # 获取文件特定的编译参数和工作目录
            file_args = self._get_file_compile_args(file_path)
            file_directory = self._get_file_directory(file_path)
            parsed_file = self._parse_single_file(file_path, file_args, file_directory)
            results.append(parsed_file)
            
            # 输出解析状态
            if parsed_file.success:
                if self.console:
                    self.console.print(f"✓ {file_path}", style="green")
            else:
                if self.console:
                    self.console.print(f"✗ {file_path}: {len(parsed_file.diagnostics)} issues", style="red")
        
        if progress and task_id:
            progress.update(task_id, completed=len(file_paths))
        
        return results
    
    def _parse_single_file(self, file_path: str, compile_args: Optional[List[str]] = None, directory: Optional[str] = None) -> ParsedFile:
        """解析单个文件"""
        start_time = time.time()
        
        try:
            # 使用文件特定的编译参数或默认参数
            args = compile_args
            
            # 创建翻译单元
            if not self.index:
                raise RuntimeError("libclang索引未初始化")
            
            # 使用文件特定的directory或全局的_working_directory（向后兼容）
            original_cwd = None
            target_directory = directory or self._working_directory
            
            if target_directory and Path(target_directory).exists():
                import os
                original_cwd = os.getcwd()
                os.chdir(target_directory)
                if self.console:
                    self.console.print(f"🔀 切换工作目录到: {target_directory}", style="dim cyan")
                
            try:    
                tu = self.index.parse(
                    file_path,
                    args=args,
                    options=clang.TranslationUnit.PARSE_NONE
                )
            finally:
                # 恢复原始工作目录
                if original_cwd:
                    import os
                    os.chdir(original_cwd)
            
            # 收集详细诊断信息
            diagnostics = []
            # 显示详细的诊断信息
            error_count = 0
            for diag in tu.diagnostics:
                if diag.severity >= clang.Diagnostic.Warning:
                    severity_map = {
                        clang.Diagnostic.Ignored: 'ignored',
                        clang.Diagnostic.Note: 'note',
                        clang.Diagnostic.Warning: 'warning',
                        clang.Diagnostic.Error: 'error',
                        clang.Diagnostic.Fatal: 'fatal'
                    }
                    
                    diagnostics.append(DiagnosticInfo(
                        severity=severity_map.get(diag.severity, 'unknown'),
                        message=diag.spelling,
                        file_path=str(diag.location.file) if diag.location.file else file_path,
                        line=diag.location.line,
                        column=diag.location.column,
                        category=diag.category_name
                    ))
                    severity_name = {
                        clang.Diagnostic.Warning: 'WARNING',
                        clang.Diagnostic.Error: 'ERROR',
                        clang.Diagnostic.Fatal: 'FATAL'
                    }.get(diag.severity, 'UNKNOWN')
                    
                    if self.console:
                        self.console.print(f"  {severity_name}: {diag.spelling}", style="red" if diag.severity >= clang.Diagnostic.Error else "yellow")
                    
                    if diag.severity >= clang.Diagnostic.Error:
                        error_count += 1
            
            success = error_count == 0
            parse_time = time.time() - start_time
            return ParsedFile(
                file_path=file_path,
                translation_unit=tu,
                success=success,
                diagnostics=diagnostics,
                parse_time=parse_time
            )
            
        except Exception as e:
            parse_time = time.time() - start_time
            return ParsedFile(
                file_path=file_path,
                translation_unit=None,
                success=False,
                diagnostics=[DiagnosticInfo(
                    severity='fatal',
                    message=f"Parse error: {str(e)}",
                    file_path=file_path,
                    line=0,
                    column=0,
                    category='parse_error'
                )],
                parse_time=parse_time
            )
    
    def cleanup(self):
        """清理资源"""
        # libclang会自动管理资源
        pass 