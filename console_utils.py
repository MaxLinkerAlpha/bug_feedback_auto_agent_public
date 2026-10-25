# -*- coding: utf-8 -*-
"""
控制台输出与日志分级系统

功能：
- 日志分级：DEBUG（仅文件）/ INFO（用户可见）/ WARN / ERROR
- 同时输出到终端和日志文件
- 用户可配置日志级别
- 线程安全

使用方式：
    from console_utils import get_logger
    log = get_logger("INFO")  # 默认 INFO 级别
    log.info("用户可见信息")
    log.debug("调试信息（仅写入文件）")
    log.warn("警告")
    log.error("错误")
"""
import sys
import os
import io
import time
import logging
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler

LOG_FILE = "bug_feedback_agent.log"
_loggers = {}

class ConsoleFormatter(logging.Formatter):
    """自定义格式化：去掉 DEBUG 前缀，保持简洁"""
    FORMATS = {
        logging.DEBUG: "[{asctime}] {message}",
        logging.INFO: "[{asctime}] {message}",
        logging.WARNING: "[{asctime}] [WARN] {message}",
        logging.ERROR: "[{asctime}] [ERROR] {message}",
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, "[{asctime}] {message}")
        formatter = logging.Formatter(log_fmt, datefmt="%H:%M:%S", style="{")
        return formatter.format(record)

class FileFormatter(logging.Formatter):
    """文件格式化：包含完整时间戳和级别"""
    def format(self, record):
        record.asctime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"[{record.asctime}] [{record.levelname}] {record.getMessage()}"

class FlushingStreamHandler(logging.StreamHandler):
    """强制刷新的 StreamHandler，确保日志立即显示

    三层刷新策略：
    1. super().emit() - 正常写入缓冲区
    2. self.flush() - 刷新 logging handler 缓冲
    3. sys.stdout.flush() - 强制刷新操作系统级缓冲区（关键！）
    """
    def emit(self, record):
        super().emit(record)
        self.flush()
        import sys
        if self.stream == sys.stdout or self.stream == sys.stderr:
            try:
                self.stream.flush()
                import os
                if sys.platform == "win32":
                    os.fsync(self.stream.fileno())
            except (OSError, ValueError):
                pass

def get_logger(level: str = "INFO") -> logging.Logger:
    """
    获取或创建日志器

    Args:
        level: 日志级别，可选 DEBUG / INFO / WARN / ERROR

    Returns:
        logging.Logger 实例
    """
    if level in _loggers:
        return _loggers[level]

    logger = logging.getLogger(f"bug_feedback_{level}")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    level_value = getattr(logging, level.upper(), logging.INFO)

    if not logger.handlers:
        stream_handler = FlushingStreamHandler(sys.stdout)
        stream_handler.setLevel(level_value)
        stream_handler.setFormatter(ConsoleFormatter())
        logger.addHandler(stream_handler)

        try:
            file_handler = RotatingFileHandler(
                LOG_FILE,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(FileFormatter())
            logger.addHandler(file_handler)
        except Exception:
            pass

    _loggers[level] = logger
    return logger

def setup_windows_console():
    """Windows 控制台 UTF-8 强制修复（仅在项目入口调用一次）
    
    同时解决 Windows 终端输出缓冲问题：
    - 设置 PYTHONUNBUFFERED 环境变量
    - 重写内置 print 函数添加自动刷新
    """
    if sys.platform == "win32":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        os.environ["PYTHONUNBUFFERED"] = "1"
        
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=False)
            sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=False)
        else:
            try:
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
                sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
            except Exception:
                pass
    
    # 全局重写 print 函数，确保每次调用都立即刷新
    # 这是最关键的修复：解决 Windows 终端需要 Ctrl+A 才能看到输出的 bug
    import builtins
    _original_print = builtins.print

    def _flushing_print(*args, **kwargs):
        """带自动刷新的 print 函数

        刷新策略：
        1. 强制设置 flush=True（即使调用者未指定）
        2. 调用原始 print 输出
        3. 显式刷新 sys.stdout/stderr（绕过 Python 缓冲）
        4. Windows 下额外调用 os.fsync() 刷新操作系统缓冲区
        """
        kwargs.setdefault('flush', True)
        stream = kwargs.get('file', sys.stdout)
        _original_print(*args, **kwargs)

        try:
            stream.flush()
            if sys.platform == "win32":
                import os
                os.fsync(stream.fileno())
        except (OSError, ValueError, AttributeError):
            pass

    builtins.print = _flushing_print

def get_user_logger() -> logging.Logger:
    """获取面向用户的日志器（默认 INFO 级别）"""
    return get_logger("INFO")

def get_debug_logger() -> logging.Logger:
    """获取调试日志器（DEBUG 级别，包含详细执行步骤）"""
    return get_logger("DEBUG")
