#!/usr/bin/env python3
"""
统一日志管理系统
"""

import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

class CppAnalyzerLogger:
    """C++分析器专用日志管理器"""
    
    def __init__(self, log_file: Optional[str] = None, console_level: str = "INFO", file_level: str = "DEBUG"):
        """
        初始化日志管理器
        
        Args:
            log_file: 日志文件路径，默认为当前目录下的cpp_analyzer.log
            console_level: 控制台日志级别
            file_level: 文件日志级别
        """
        # 设置日志文件路径
        if log_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = f"cpp_analyzer_{timestamp}.log"
        
        self.log_file = Path(log_file)
        self.logger = logging.getLogger("cpp_analyzer")
        self.logger.setLevel(logging.DEBUG)
        
        # 清除已有的处理器
        self.logger.handlers.clear()
        
        # 创建文件处理器
        file_handler = logging.FileHandler(self.log_file, encoding='utf-8')
        file_handler.setLevel(getattr(logging, file_level.upper()))
        
        # 创建控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, console_level.upper()))
        
        # 创建格式器
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
        )
        console_formatter = logging.Formatter(
            '%(levelname)s: %(message)s'
        )
        
        # 设置格式器
        file_handler.setFormatter(file_formatter)
        console_handler.setFormatter(console_formatter)
        
        # 添加处理器
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        
        self.info(f"日志系统初始化完成，日志文件: {self.log_file.absolute()}")
    
    def debug(self, message: str):
        """DEBUG级别日志"""
        self.logger.debug(message)
    
    def info(self, message: str):
        """INFO级别日志"""
        self.logger.info(message)
    
    def warning(self, message: str):
        """WARNING级别日志"""
        self.logger.warning(message)
    
    def error(self, message: str):
        """ERROR级别日志"""
        self.logger.error(message)
    
    def critical(self, message: str):
        """CRITICAL级别日志"""
        self.logger.critical(message)
    
    def section(self, title: str):
        """记录分节标题"""
        separator = "=" * 60
        self.info(separator)
        self.info(f" {title}")
        self.info(separator)
    
    def subsection(self, title: str):
        """记录子节标题"""
        separator = "-" * 40
        self.info(separator)
        self.info(f" {title}")
        self.info(separator)
    
    def progress(self, message: str, current: int = 0, total: int = 0):
        """记录进度信息"""
        if total > 0:
            percentage = (current / total) * 100
            self.info(f"[{current}/{total} - {percentage:.1f}%] {message}")
        else:
            self.info(f"[{current}] {message}")
    
    def entity_found(self, entity_type: str, entity_name: str, file_path: str):
        """记录发现的实体"""
        self.debug(f"发现{entity_type}: {entity_name} (文件: {file_path})")
    
    def file_processed(self, file_path: str, success: bool, entity_count: int = 0):
        """记录文件处理结果"""
        status = "成功" if success else "失败"
        self.info(f"文件处理{status}: {file_path} (实体数: {entity_count})")
    
    def compilation_info(self, file_path: str, args_count: int):
        """记录编译信息"""
        self.debug(f"编译参数加载: {file_path} ({args_count}个参数)")
    
    def rsp_file_parsed(self, rsp_path: str, args_count: int):
        """记录RSP文件解析"""
        self.info(f"RSP文件解析: {rsp_path} ({args_count}个参数)")
    
    def analysis_summary(self, stats: dict):
        """记录分析摘要"""
        self.section("分析摘要")
        for key, value in stats.items():
            self.info(f"{key}: {value}")
    
    def get_log_path(self) -> Path:
        """获取日志文件路径"""
        return self.log_file


# 全局日志实例
_global_logger: Optional[CppAnalyzerLogger] = None
_root_configured = False

def get_logger() -> CppAnalyzerLogger:
    """获取全局日志实例"""
    global _global_logger
    if _global_logger is None:
        _global_logger = CppAnalyzerLogger()
    return _global_logger

def configure_root_logger(log_file: Optional[str] = None):
    """配置根日志器，确保所有 logging.xxx() 调用都能正确输出到文件"""
    global _root_configured
    if _root_configured:
        return
    
    # 清除根日志器的现有处理器
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 设置日志文件路径
    if log_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"root_logger_{timestamp}.log"
    
    # 创建文件处理器 - 确保所有日志都写入文件
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    
    # 创建格式器
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # 配置根日志器
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    _root_configured = True
    
    # 记录配置信息
    root_logger.info(f"根日志器配置完成，日志文件: {log_file}")
    root_logger.debug("根日志器DEBUG级别测试 - 这条消息应该出现在日志文件中")

def setup_logging_for_script(script_name: str) -> logging.Logger:
    """
    为脚本设置完整的日志配置，包括根日志器
    
    Args:
        script_name: 脚本名称，用于生成日志文件名
    
    Returns:
        配置好的标准日志器
    """
    import time
    timestamp = int(time.time())
    
    # 配置根日志器
    root_log_file = f"{script_name}_debug_{timestamp}.log"
    configure_root_logger(root_log_file)
    
    # 返回根日志器
    return logging.getLogger()

def set_quiet_mode():
    """设置静默模式（只输出ERROR级别到控制台）"""
    global _global_logger
    if _global_logger:
        for handler in _global_logger.logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
                handler.setLevel(logging.ERROR)

def set_verbose_mode():
    """设置详细模式（输出DEBUG级别到控制台）"""
    global _global_logger
    if _global_logger:
        for handler in _global_logger.logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
                handler.setLevel(logging.DEBUG) 