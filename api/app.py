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
import threading
import time
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
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
# 启动优化（2026-10-03）：不在模块导入期 `from rag import ...`（会连带
# torch/transformers，实测拖慢启动约 7 秒）。改为首次真正需要时惰性导入一次；
# /health **不再**触发它（见下面的 _rag_state_for_health），
# 否则等于把 7.5s 从启动挪到第一次健康检查，反而更慢（已实测）。
_rag_available = None      # None = 尚未探测；True/False = 探测结果
_check_rag_health = None
_rag_import_error = None


def _load_rag_health_checker() -> bool:
    """惰性导入 rag 的健康检查函数（只尝试一次，结果缓存）"""
    global _rag_available, _check_rag_health, _rag_import_error
    if _rag_available is False:
        return False
    if _check_rag_health is None:
        try:
            from services.rag_loader import load_rag
            _check_rag_health = getattr(load_rag(), "check_rag_health")
            _rag_available = True
        except Exception as e:      # pragma: no cover - 依赖缺失环境
            _rag_import_error = str(e)
            _rag_available = False
            logger.warning(f"RAG模块导入失败: {e}")
    return bool(_rag_available)


# ---- 延迟预热（2026-10-03 第 4 轮） ----
# 状态：disabled / pending（等待延迟）/ loading / ready / failed
_RAG_WARM: dict = {"state": "pending", "error": "", "delay_s": None}


def _warm_embedding_model() -> bool:
    """预热**本地 Embedding 模型**（首次检索的真正大头，实测冷启动 13.3s 中的 ~10s）

    背景（2026-10-04 实测）：只导入 rag 不够。冷启动时"首次知识库提问"要 13.29s，
    其中大部分是加载本地 Embedding（bge 系列，约 10s）；预热完成后同类提问只要 1.78s。
    因此延迟预热除了导入 rag，还要把 Embedding 也加载好，否则负责人打开系统后
    第一次提问仍要等十几秒——"health 返回快"并不能证明系统已就绪。
    """
    try:
        from services.rag_loader import load_rag
        rag = load_rag()
        getter = getattr(rag, "get_embedding_model", None) or getattr(rag, "embedding_model", None)
        if getter is None:
            _RAG_WARM["embedding"] = "unsupported"
            return False
        if callable(getter):
            getter()
        _RAG_WARM["embedding"] = "ready"
        return True
    except Exception as e:      # 预热失败不影响正常请求（首次提问会自行加载）
        _RAG_WARM["embedding"] = f"failed: {type(e).__name__}: {str(e)[:120]}"
        logger.warning(f"[warmup] Embedding 预热失败（不影响请求，首次提问会自行加载）: {e}")
        return False


def _warm_rag_background() -> None:
    """后台预热：导入 rag + 加载本地 Embedding（不做检索、不碰向量库）

    修复（2026-10-03）：原先一次性尝试。若此刻正好有请求在导入 rag，
    Python 3.14 的跨线程模块锁死锁检测会让本线程失败（实测
    `deadlock detected by _ModuleLock('rag.rag_service')`），预热就永久停在 failed。
    现在：导入已由 services/rag_loader 的进程级锁串行化，这里再加**有限重试**
    （幂等、无副作用）——请求线程一旦完成导入，重试会立即复用。
    """
    _RAG_WARM["state"] = "loading"
    for attempt in range(1, 4):
        ok = _load_rag_health_checker()
        if ok:
            _RAG_WARM["attempts"] = attempt
            _RAG_WARM["error"] = ""
            # 导入成功后继续把 Embedding 加载好，让"第一次提问"不再等十几秒
            _warm_embedding_model()
            _RAG_WARM["state"] = "ready"
            logger.info(f"[warmup] rag 延迟预热完成：state=ready（第 {attempt} 次尝试）"
                        f"，embedding={_RAG_WARM.get('embedding')}")
            return
        _RAG_WARM["attempts"] = attempt
        _RAG_WARM["error"] = _rag_import_error or ""
        logger.warning(f"[warmup] rag 预热第 {attempt} 次未成功：{_RAG_WARM['error'][:200]}")
        time.sleep(1.0 * attempt)
    _RAG_WARM["state"] = "failed"
    logger.warning(f"[warmup] rag 延迟预热失败：{_RAG_WARM['error'][:200]}")


def _rag_state_for_health() -> bool:
    """给 /health 的**轻量**判断：不导入 rag（用向量库目录粗判）

    预热完成后按真实探测结果返回；未完成时不假装"已就绪"，也不误报失败。
    """
    state = _RAG_WARM.get("state")
    if state == "ready":
        return True
    if state == "failed":
        return False
    return os.path.exists(_VECTOR_STORE_DIR)


@app.on_event("startup")
def _start_rag_warmup() -> None:
    """启动后**延迟**预热 rag（默认 15 秒；RAG_WARMUP_DELAY_SECONDS=0 关闭）

    为什么延迟而不是立即（同机 A/B 实测）：
        立即预热线程：startup 日志 1.14s → 7.63s，首次 /api/health 3.78s → 9.89s（更差）
        延迟预热    ：启动窗口与首次健康检查都不受影响，学生提问时 rag 已就绪
    """
    try:
        delay = float(os.getenv("RAG_WARMUP_DELAY_SECONDS", "15") or 15)
    except ValueError:
        delay = 15.0
    _RAG_WARM["delay_s"] = delay
    if delay <= 0:
        _RAG_WARM["state"] = "disabled"
        logger.info("[warmup] RAG_WARMUP_DELAY_SECONDS=0，跳过 rag 预热")
        return
    timer = threading.Timer(delay, _warm_rag_background)
    timer.daemon = True
    timer.start()

# Vector Store目录
# 向量库目录：统一由数据根目录推导（支持 EDU_DATA_DIR 隔离），
# 不再硬编码 <项目根>/data，避免隔离运行时判断错目录
from config.path_config import get_data_dir  # noqa: E402

_VECTOR_STORE_DIR = str(get_data_dir() / "vector_store")


# 部署角色（2026-10-02）：同一套前端要拆成"学生端 / 教师端"两个入口时使用。
# 例：APP_ROLE=student 的进程只把根路径送到 /student；教师端另起一个进程、另一个端口。
# 注意这只是**入口分离**，真正的权限始终由登录身份决定（见 api/deps.py）。
_APP_ROLE = (os.getenv("APP_ROLE") or "").strip().lower()
if _APP_ROLE not in ("student", "teacher"):
    _APP_ROLE = ""


@app.get("/")
async def root():
    """
    根路径。

    - **分角色部署**（APP_ROLE=student/teacher）：直接跳到对应入口，
      于是"学生端"和"教师端"可以是两个进程、两个端口、两个地址；
    - **已构建前端（有 frontend/dist）**：返回平台首页；
    - **开发模式（无 dist）**：返回服务信息 JSON，便于确认后端是否起来。

    此前这里恒定返回 JSON，导致即使托管了前端、访问根路径也看不到平台。
    """
    dist_index = Path(__file__).resolve().parent.parent / "frontend" / "dist" / "index.html"
    if _APP_ROLE and dist_index.is_file():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/{_APP_ROLE}", status_code=307)
    if dist_index.is_file():
        from fastapi.responses import FileResponse
        return FileResponse(str(dist_index))
    return {
        "service": "智慧教育RAG系统",
        "version": APP_VERSION,
        "status": "running",
        "app_role": _APP_ROLE or None,
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
    
    # 检查RAG服务（启动优化 2026-10-03）：此处**不**导入 rag，避免把 7.5s
    # 的导入代价挪到第一次健康检查上（实测那样反而更慢）。
    services["rag"] = _rag_state_for_health()
    
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
            # 启动优化：rag 延迟预热状态（pending/loading/ready/failed/disabled）
            "rag_warmup": dict(_RAG_WARM),
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
        # 走同一把进程级锁（services/rag_loader），避免与延迟预热线程并发导入 rag
        from services.rag_loader import load_rag
        get_rag_readiness = getattr(load_rag(), "get_rag_readiness")
        from services import capability_status, usage_ledger
        readiness = dict(get_rag_readiness())
        # 运行态外部能力（LLM / 联网 / 视觉 / 语音）：反映**真实调用结果**，
        # 与 /health 的配置态 capabilities 互补。降级时这里能看到原因。
        readiness["external_capabilities"] = capability_status.snapshot()
        # 当日外部调用用量与预算（成本可见；超限时后台预生成会自动停）
        readiness["usage_today"] = usage_ledger.budget_status()
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


# ============================================================
# 生产模式：托管前端构建产物（frontend/dist）
# ============================================================
#
# 单机部署时让后端直接服务前端，**一个进程就是一个完整平台**——不需要额外装
# nginx、也不用再起一个前端服务。开发模式（npm run dev，dist 不存在）下这段
# 会安静跳过并提示，不影响原有开发流程。
#
# 注意：catch-all 路由必须**最后注册**，否则会抢走前面所有 API 路由。
_DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if (_DIST_DIR / "index.html").is_file():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _assets_dir = _DIST_DIR / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    # 这些前缀属于后端自己的命名空间，不能被 SPA 回退吞掉
    _BACKEND_PREFIXES = ("api/", "docs", "redoc", "openapi.json", "health")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        """SPA 路由回退：非后端路径一律交给前端路由（返回 index.html）"""
        if full_path.startswith(_BACKEND_PREFIXES):
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(str(_DIST_DIR / "index.html"))

    logger.info(f"已托管前端构建产物: {_DIST_DIR}")
else:
    logger.info("未检测到 frontend/dist —— 跳过后端静态托管"
                "（开发模式请用 npm run dev；生产请先 npm run build）")
