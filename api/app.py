"""
智慧教育RAG系统 - FastAPI应用配置

功能：
1. 配置CORS中间件
2. 注册请求日志中间件（RequestIDMiddleware）
3. 注册路由
4. 配置全局异常处理（带request_id）
5. 健康检查接口（检查LLM、RAG、配置状态）
"""

import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings, APP_VERSION
from api.routes import chat, knowledge, subjects, models, education
from api.middleware import RequestIDMiddleware, get_request_id
from api.exceptions import (
    APIException,
    KnowledgeBaseNotFoundError,
    LLMServiceError,
    RAGServiceError,
    ValidationError,
)
from utils.logger import logger

app = FastAPI(
    title="智慧教育RAG系统API",
    description="基于RAG的教育知识问答系统API接口",
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.service.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册请求日志中间件（必须在CORS之后注册）
app.add_middleware(RequestIDMiddleware)

# 注册路由
app.include_router(chat.router, prefix="/api", tags=["聊天"])
app.include_router(knowledge.router, prefix="/api", tags=["知识库"])
app.include_router(subjects.router, prefix="/api", tags=["学科"])
app.include_router(models.router, prefix="/api", tags=["模型"])
app.include_router(education.router, prefix="/api", tags=["教育"])


# ============ 全局异常处理（统一返回格式，带request_id） ============

def _error_response(request: Request, status_code: int, message: str) -> JSONResponse:
    """构建统一错误响应"""
    request_id = get_request_id(request)
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "message": message,
            "request_id": request_id,
        },
    )


@app.exception_handler(APIException)
async def api_exception_handler(request: Request, exc: APIException):
    """处理自定义API异常"""
    logger.warning(f"APIException: {exc.message}", extra={"request_context": {"request_id": get_request_id(request)}})
    return _error_response(request, exc.status_code, exc.message)


@app.exception_handler(KnowledgeBaseNotFoundError)
async def knowledge_base_not_found_handler(request: Request, exc: KnowledgeBaseNotFoundError):
    """处理知识库不存在异常"""
    logger.warning(f"KnowledgeBaseNotFoundError: {exc.message}", extra={"request_context": {"request_id": get_request_id(request)}})
    return _error_response(request, 404, exc.message)


@app.exception_handler(LLMServiceError)
async def llm_service_error_handler(request: Request, exc: LLMServiceError):
    """处理LLM服务异常"""
    logger.error(f"LLMServiceError: {exc.message}", extra={"request_context": {"request_id": get_request_id(request)}})
    return _error_response(request, 500, exc.message)


@app.exception_handler(RAGServiceError)
async def rag_service_error_handler(request: Request, exc: RAGServiceError):
    """处理RAG服务异常"""
    logger.error(f"RAGServiceError: {exc.message}", extra={"request_context": {"request_id": get_request_id(request)}})
    return _error_response(request, 500, exc.message)


@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    """处理参数验证异常"""
    logger.warning(f"ValidationError: {exc.message}", extra={"request_context": {"request_id": get_request_id(request)}})
    return _error_response(request, 400, exc.message)


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """
    处理未知异常
    安全策略：不向前端暴露traceback、文件路径、Python错误堆栈
    详细错误信息仅写入服务端日志
    """
    request_id = get_request_id(request)
    logger.error(
        f"Unhandled exception: {type(exc).__name__}: {str(exc)}",
        extra={"request_context": {"request_id": request_id}},
        exc_info=True
    )
    return _error_response(request, 500, "服务器内部错误，请稍后重试")


# ============ 路由 ============

# ============ 模块级导入（避免动态import） ============

# LLM模块
_llm_available = False
_get_model_info = None
_check_llm_availability = None
try:
    from llm import get_model_info as _get_model_info, check_llm_availability as _check_llm_availability
    _llm_available = True
except Exception as e:
    logger.warning(f"LLM模块导入失败: {e}")

# RAG模块
_rag_available = False
_check_rag_health = None
try:
    from rag import check_rag_health as _check_rag_health
    _rag_available = True
except Exception as e:
    logger.warning(f"RAG模块导入失败: {e}")

# Vector Store目录
_VECTOR_STORE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "vector_store")


@app.get("/health")
async def health_check():
    """
    健康检查接口
    
    返回：
    {
        "status": "ok",
        "version": "2.1.0",
        "services": {
            "llm": true/false,
            "rag": true/false,
            "config": true/false
        }
    }
    """
    services = {
        "llm": False,
        "rag": False,
        "config": True,
    }
    
    # 检查配置是否正常
    try:
        _ = settings.llm.provider
        _ = settings.rag.top_k
    except Exception as e:
        logger.warning(f"Health check - config error: {e}")
        services["config"] = False
    
    # 检查LLM是否可调用（使用模块级导入）
    if _llm_available and _check_llm_availability is not None:
        try:
            llm_status = _check_llm_availability()
            services["llm"] = llm_status.get("available", False)
        except Exception as e:
            logger.warning(f"Health check - LLM check error: {e}")
            services["llm"] = False
    
    # 检查RAG服务（使用模块级导入）
    if _rag_available and _check_rag_health is not None:
        try:
            rag_health = _check_rag_health()
            services["rag"] = rag_health.get("status") == "ok"
        except Exception as e:
            logger.warning(f"Health check - RAG check error: {e}")
            services["rag"] = False
    else:
        # 回退：检查向量存储目录是否存在
        services["rag"] = os.path.exists(_VECTOR_STORE_DIR)
    
    # 整体状态：只要配置正常就返回ok
    overall_status = "ok" if services["config"] else "degraded"
    
    return {
        "success": True,
        "data": {
            "status": overall_status,
            "version": APP_VERSION,
            "services": services,
        },
        "message": "ok",
    }
