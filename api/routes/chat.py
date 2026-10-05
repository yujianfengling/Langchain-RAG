# api/routes/chat.py
# 聊天接口路由

import asyncio
import json
import threading
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from typing import List, Optional

from api.schemas.chat_schema import ChatRequest, ChatResponse
from api.schemas.response_schema import SuccessResponse, ErrorResponse
from api.deps import get_current_user, require_subject_read
from chat_service import chat_with_ai, chat_stream
from api.exceptions import LLMServiceError, RAGServiceError
from utils.logger import logger

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, user_id: str = Depends(get_current_user)):
    """
    AI聊天接口（非流式）

    V2.5.2：chat_with_ai 为同步阻塞（RAG + LLM requests），
    通过 run_in_threadpool 移出事件循环，避免长时间阻塞其他请求。

    阶段一：用户身份由后端统一机制取得（不信任请求体传入的 user_id），
    用于学习记录归属与个性化提示。

    - **question**: 用户问题
    - **subject_id**: 学科ID
    - **model**: 模型名称（可选，默认使用配置中的默认模型）
    - **history**: 对话历史
    - **conversation_id**: 会话ID（V2.5.1）
    - **mode**: 学习模式（V2.5.1）
    """
    # 学科归属校验（2026-10-01）：问答会读取该学科的知识库，必须与知识库接口同一套规则。
    # 课程学科 → 仅授课教师 / 在班学生 / 管理员；历史（公共）学科 → 沿用"登录即可读"。
    # 这样匿名请求（会被回退成 default_user）无法借该身份读取课程资料。
    if request.subject_id:
        await require_subject_read(request.subject_id, user_id)
    try:
        result = await run_in_threadpool(
            chat_with_ai,
            request.question,
            request.subject_id,
            request.model,
            request.history,
            None,  # temperature
            None,  # max_tokens
            request.image_data,
            request.image_mime_type,
            request.conversation_id,
            request.mode,
            user_id,
        )

        # 检查是否成功
        if not result.get("success", False):
            error_msg = result.get("error", "未知错误")
            # LLM配置/API Key错误时返回明确提示
            if any(keyword in error_msg for keyword in ["LLM", "模型", "API Key", "API密钥", "Key未配置"]):
                raise LLMServiceError("LLM服务不可用，请检查API配置")
            elif any(keyword in error_msg for keyword in ["RAG", "知识库"]):
                raise RAGServiceError(error_msg)
            else:
                raise HTTPException(status_code=500, detail=error_msg)

        return ChatResponse(
            success=True,
            answer=result["answer"],
            sources=result.get("sources", []),
            model=result.get("model"),
            provider=result.get("provider"),
            provider_display=result.get("provider_display"),
            latency=result.get("latency", 0.0),
            context_used=result.get("context_used", False),
            cited_source_ids=result.get("cited_source_ids") or [],
            # 结构化模式：卡片数据必须透出，否则前端无法渲染笔记/题单
            message_type=result.get("message_type"),
            payload=result.get("payload"),
            detail_level=result.get("detail_level"),
            teaching=result.get("teaching"),
        )
    except (LLMServiceError, RAGServiceError, HTTPException):
        raise
    except Exception as e:
        # 捕获ValidationError等异常
        error_str = str(e)
        if any(keyword in error_str for keyword in ["LLM", "模型", "API Key", "API密钥"]):
            raise LLMServiceError("LLM服务不可用，请检查API配置")
        raise HTTPException(status_code=500, detail="服务器内部错误，请稍后重试")


async def _chat_stream_async(request: ChatRequest, user_id: Optional[str] = None):
    """
    流式聊天（V2.5.2 Queue 模式）

    同步 Provider 流（chat_stream）由后台线程读取，
    事件通过 asyncio.Queue 传递给本 async generator 逐块 yield。

    取消传播：客户端断开 → 本生成器被取消 → finally 置 stop_event
    → worker 线程停止迭代并 close 上游生成器（关闭 requests 连接，不再产生 token）。

    阶段一：user_id 由路由层从后端身份机制取得并透传，用于学习记录归属。
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=32)
    stop_event = threading.Event()

    def _worker():
        gen = None
        try:
            gen = chat_stream(
                question=request.question,
                subject_id=request.subject_id,
                history=request.history,
                model=request.model,
                image_data=request.image_data,
                image_mime_type=request.image_mime_type,
                conversation_id=request.conversation_id,
                mode=request.mode,
                user_id=user_id,
            )
            for event in gen:
                if stop_event.is_set():
                    # 客户端已断开：关闭上游生成器（传播到 requests 连接关闭）
                    return
                _safe_put(loop, queue, event)
            _safe_put(loop, queue, None)  # 正常结束哨兵
        except Exception as e:
            logger.error(f"[ChatStream] worker 异常: {e}")
            _safe_put(loop, queue, {"type": "error", "message": "服务器内部错误", "error_type": "unknown"})
        finally:
            _safe_close(gen)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue  # 空转等事件；客户端断开时 finally 会触发 stop
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        # 客户端断开 / 取消 → 通知 worker 停止并关闭上游
        stop_event.set()
        thread.join(timeout=2.0)


def _safe_put(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, item):
    """线程安全入队（loop 已关闭时静默忽略）"""
    try:
        loop.call_soon_threadsafe(queue.put_nowait, item)
    except Exception:
        pass


def _safe_close(gen):
    """安全关闭同步生成器"""
    try:
        if gen is not None:
            gen.close()
    except Exception:
        pass


@router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest, user_id: str = Depends(get_current_user)):
    """
    流式聊天接口（SSE）

    阶段一：用户身份由后端统一机制取得，用于学习记录归属与个性化提示。

    - **question**: 用户问题
    - **subject_id**: 学科ID
    - **history**: 对话历史
    - **conversation_id**: 会话ID（V2.5.1，服务端持久化上下文）
    - **mode**: 学习模式（chat|smart_note|summary|explanation|advice|quiz）
    """
    # 学科归属校验：与非流式入口同一规则（课程学科仅教师/在班学生/管理员可读）
    if request.subject_id:
        await require_subject_read(request.subject_id, user_id)
    try:
        return StreamingResponse(
            _chat_stream_async(request, user_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        error_str = str(e)
        if any(keyword in error_str for keyword in ["LLM", "模型", "API Key", "API密钥"]):
            raise LLMServiceError("LLM服务不可用，请检查API配置")
        elif any(keyword in error_str for keyword in ["RAG", "知识库"]):
            raise RAGServiceError(error_str)
        else:
            raise HTTPException(status_code=500, detail="服务器内部错误，请稍后重试")
