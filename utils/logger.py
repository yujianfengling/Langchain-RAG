"""
智能教学助手 - 日志管理模块

功能：
1. 启动时自动创建logs目录
2. 统一日志格式：时间 | level | module | request_id | message
3. 支持 info / warning / error 级别
4. 异常自动记录exc_info
5. 支持request_id上下文传递
"""

import os
import logging
import logging.handlers
from datetime import datetime


class RequestIDFilter(logging.Filter):
    """
    日志过滤器：从日志记录中提取request_id
    如果存在request_context，则提取request_id
    """
    def filter(self, record):
        request_context = getattr(record, "request_context", None)
        if request_context:
            record.request_id = request_context.get("request_id", "-")
        else:
            if not hasattr(record, "request_id"):
                record.request_id = "-"
        return True


class RequestContextFormatter(logging.Formatter):
    """
    自定义日志格式化器
    支持request_context附加信息的格式化输出
    """
    
    def format(self, record):
        # 如果有request_context，将其关键信息附加到消息中
        request_context = getattr(record, "request_context", None)
        if request_context:
            parts = []
            if "method" in request_context:
                parts.append(f"method={request_context['method']}")
            if "path" in request_context:
                parts.append(f"path={request_context['path']}")
            if "status_code" in request_context:
                parts.append(f"status={request_context['status_code']}")
            if "duration_ms" in request_context:
                parts.append(f"duration={request_context['duration_ms']}ms")
            if "error" in request_context:
                parts.append(f"error={request_context['error']}")
            if parts:
                record.msg = f"{record.msg} [{' | '.join(parts)}]"
        
        return super().format(record)


def _default_log_dir() -> str:
    """
    日志目录：设置 `EDU_DATA_DIR` 时随隔离目录走，否则保持 `<项目根>/logs`

    修复（2026-10-01）：原先硬编码 `<项目根>/logs`，隔离运行时日志仍写正式目录。
    这里用惰性导入 + 兜底，避免与 config 包产生导入环；语义与
    `config.path_config.get_log_dir()` 保持一致（单一来源）。
    """
    try:
        from config.path_config import get_log_dir
        return str(get_log_dir())
    except Exception:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")


def setup_logger():
    """
    配置全局日志系统
    
    日志格式：时间 | level | module | request_id | message
    
    Returns:
        logging.Logger: 配置好的logger实例
    """
    # 日志目录
    log_dir = _default_log_dir()
    
    # 自动创建logs目录
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    
    # 日志文件路径
    log_file = os.path.join(log_dir, "app.log")
    
    # 创建logger
    logger = logging.getLogger("teaching_assistant")
    logger.setLevel(logging.INFO)
    
    # 清除已有的handler（避免重复添加）
    logger.handlers.clear()
    
    # 日志格式：时间 | level | module | request_id | message
    formatter = RequestContextFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(module)-20s | %(request_id)-12s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # 添加request_id过滤器
    request_id_filter = RequestIDFilter()
    
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(request_id_filter)
    logger.addHandler(console_handler)
    
    # 文件处理器（按大小轮转）
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(request_id_filter)
    logger.addHandler(file_handler)
    
    # 防止日志向上传播
    logger.propagate = False
    
    return logger


# 全局logger实例
logger = setup_logger()


def get_logger(module_name: str = None):
    """
    获取指定模块的logger
    
    Args:
        module_name: 模块名称
        
    Returns:
        logging.Logger: logger实例
    """
    if module_name:
        return logging.getLogger(f"teaching_assistant.{module_name}")
    return logger


def log_chat(user_id: str, question: str, answer: str, duration: float = 0):
    """记录聊天日志"""
    logger.info(f"Chat | user={user_id} | q={question[:100]} | a={answer[:100]} | duration={duration:.2f}s")


def log_error(module: str, error: Exception, context: str = ""):
    """记录错误日志"""
    ctx = f" | context={context}" if context else ""
    logger.error(f"Error in {module}: {error}{ctx}", exc_info=True)


def log_performance(operation: str, duration: float, details: str = ""):
    """记录性能日志"""
    det = f" | {details}" if details else ""
    logger.info(f"Perf | {operation} | duration={duration:.2f}s{det}")


# 公开API
__all__ = [
    "logger",
    "get_logger",
    "setup_logger",
    "log_chat",
    "log_error",
    "log_performance",
]
