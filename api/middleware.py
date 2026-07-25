"""
请求日志中间件
为每个请求生成唯一request_id，记录请求/响应信息
"""

import time
import uuid
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("api.middleware")


def generate_request_id() -> str:
    """生成短UUID作为request_id"""
    return uuid.uuid4().hex[:12]


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    请求ID与日志中间件
    
    功能：
    1. 每个请求自动生成唯一的X-Request-ID
    2. 记录请求方法、路径、状态码、耗时
    3. 将request_id注入到请求上下文，供异常处理器使用
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 生成request_id（优先使用客户端传入的）
        request_id = request.headers.get("X-Request-ID") or generate_request_id()
        
        # 将request_id存入请求状态，供后续使用
        request.state.request_id = request_id
        
        # 记录请求开始时间
        start_time = time.time()
        
        # 构建请求日志上下文
        log_context = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "client": request.client.host if request.client else "unknown",
        }
        
        logger.info(
            "Request started",
            extra={"request_context": log_context}
        )
        
        # 处理请求
        try:
            response = await call_next(request)
        except Exception as exc:
            # 计算耗时
            duration_ms = round((time.time() - start_time) * 1000, 2)
            log_context["duration_ms"] = duration_ms
            log_context["status_code"] = 500
            log_context["error"] = str(exc)
            
            logger.error(
                "Request failed with unhandled exception",
                extra={"request_context": log_context},
                exc_info=True
            )
            raise
        
        # 计算耗时
        duration_ms = round((time.time() - start_time) * 1000, 2)
        
        # 记录请求完成日志
        log_context["status_code"] = response.status_code
        log_context["duration_ms"] = duration_ms
        
        # 根据状态码选择日志级别
        if response.status_code >= 500:
            logger.error("Request completed", extra={"request_context": log_context})
        elif response.status_code >= 400:
            logger.warning("Request completed", extra={"request_context": log_context})
        else:
            logger.info("Request completed", extra={"request_context": log_context})
        
        # 将request_id添加到响应头
        response.headers["X-Request-ID"] = request_id
        
        return response


def get_request_id(request: Request) -> str:
    """
    从请求中获取request_id
    
    Args:
        request: FastAPI请求对象
        
    Returns:
        request_id字符串，如不存在则返回"unknown"
    """
    return getattr(request.state, "request_id", "unknown")
