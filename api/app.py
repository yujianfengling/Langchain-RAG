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
from api.routes import chat, knowledge, subjects, models, education, notes, learning, conversations, audio, agent, auth, quiz, student, memory, plan_agent, agent_run, teacher
from api.middleware import RequestIDMiddleware, get_request_id
# 注册学习闭环事件处理器（阶段一：作答→画像→错题→复习→计划）
import services.learning_handlers  # noqa: F401
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
app.include_router(notes.router, prefix="/api", tags=["智能笔记"])
app.include_router(learning.router, prefix="/api", tags=["学习计划"])
app.include_router(conversations.router, prefix="/api", tags=["对话"])
app.include_router(audio.router, prefix="/api", tags=["语音"])
app.include_router(agent.router, prefix="/api", tags=["智能Agent"])
app.include_router(auth.router, prefix="/api", tags=["身份"])
app.include_router(quiz.router, prefix="/api", tags=["自测"])
app.include_router(student.router, prefix="/api", tags=["学生端"])
app.include_router(memory.router, prefix="/api", tags=["学习记忆"])
app.include_router(plan_agent.router, prefix="/api", tags=["计划调整"])
app.include_router(agent_run.router, prefix="/api", tags=["学习Agent"])
app.include_router(teacher.router, prefix="/api", tags=["教师端"])


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
# 向量库目录：统一由数据根目录推导（支持 EDU_DATA_DIR 隔离），
# 不再硬编码 <项目根>/data，避免隔离运行时判断错目录
from config.path_config import get_data_dir  # noqa: E402

_VECTOR_STORE_DIR = str(get_data_dir() / "vector_store")


@app.get("/")
async def root():
    """
    根路径欢迎接口

    返回：
    {
        "service": "智慧教育RAG系统",
        "version": "2.2.1",
        "status": "running"
    }
    """
    return {
        "service": "智慧教育RAG系统",
        "version": APP_VERSION,
        "status": "running",
    }


@app.get("/health")
@app.get("/api/health")
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
        },
        "data_scope": {
            "isolated": true/false,   # 是否运行在 EDU_DATA_DIR 指定的隔离数据目录
            "dir_name": "git_text_e2e_data"   # 只给目录名，不泄露绝对路径
        }
    }

    说明：`data_scope` 供自动测试/真实模型验收脚本在**写入数据前**判断当前后端
    是否运行在隔离目录上，避免继续把测试账号与学习记录散落到正式库。
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
            "data_scope": _data_scope(),
            "capabilities": _capabilities(),
        },
        "message": "ok",
    }


def _capabilities() -> dict:
    """
    能力清单：文本 / 视觉 / 语音，各自"用什么提供方 + 当前是否可用 + 不可用原因"

    2026-09-25：默认文本模型切到 DeepSeek（MiMo 会员到期）；
    视觉（qwen-vision）与语音识别（ASR）单独判定，不共用文本模型配置。
    （2026-10-01：回答朗读 TTS 已整体移除，这里不再上报 local_tts / mimo_tts。）
    """
    caps: dict = {}
    # 文本
    try:
        from config.models import get_default_model, get_model_config, get_model_api_key
        provider = get_default_model()
        caps["text"] = {
            "provider": provider,
            "model": get_model_config(provider).get("model_name", ""),
            "available": bool(get_model_api_key(provider)),
        }
    except Exception as e:
        caps["text"] = {"provider": None, "available": False, "reason": str(e)}
    # 视觉
    try:
        from services.multimodal_service import MultimodalService
        svc = MultimodalService()
        caps["vision"] = {
            "provider": getattr(svc.vision_provider, "model_name", None),
            "available": bool(svc.vision_provider),
            "reason": svc.vision_unavailable_reason,
        }
    except Exception as e:
        caps["vision"] = {"provider": None, "available": False, "reason": str(e)}
    # 语音识别（ASR）：只有"语音输入"一条链路
    try:
        from services.audio_service import (local_speech_status, mimo_speech_status,
                                            speech_capability)
        status = speech_capability()
        local = status.get("local") or {}
        caps["speech"] = status                      # 汇总（含 backend 选择）
        caps["local_asr"] = local.get("asr") or {"available": False}
        caps["speech_service"] = {
            "running": bool(local.get("running")),
            "url": local.get("url"),
            "reason": local.get("reason") or "",
            "vram": local.get("vram") or {},
        }
        caps["mimo_asr"] = mimo_speech_status()
    except Exception as e:
        caps["speech"] = {"available": False, "reason": str(e)}
        caps["local_asr"] = {"available": False, "reason": str(e)}
    return caps


def _data_scope() -> dict:
    """当前后端所用数据目录的范围（隔离判断），不返回绝对路径"""
    from pathlib import Path
    from config.path_config import get_data_dir
    default_dir = (Path(__file__).resolve().parent.parent / "data").resolve()
    try:
        used = get_data_dir().resolve()
    except Exception:
        used = default_dir
    env_set = bool(os.environ.get("EDU_DATA_DIR", "").strip())
    return {
        "isolated": bool(env_set and used != default_dir),
        "dir_name": used.name,
    }


@app.get("/health/ready")
@app.get("/api/health/ready")
async def health_ready():
    """
    服务就绪检查（不触发Embedding加载、不调用外部API）

    返回RAG检索模式与Embedding加载状态，供前端区分：
    - hybrid_ready: FAISS + BM25 + RRF 全部就绪
    - bm25_degraded: 向量检索不可用，仅关键词检索
    - loading / not_loaded: 向量检索组件尚未就绪
    - unavailable: 检索服务不可用

    不返回本地绝对路径、异常堆栈与任何密钥。
    """
    try:
        from rag import get_rag_readiness
        readiness = get_rag_readiness()
        return {
            "success": True,
            "data": readiness,
            "message": "ok",
        }
    except Exception as e:
        logger.error(f"Health ready check error: {type(e).__name__}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "data": None,
                "message": "服务器内部错误，请稍后重试",
                "request_id": None,
            },
        )
