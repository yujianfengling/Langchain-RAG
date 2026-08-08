# api/routes/chat.py
# 聊天接口路由

import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import List

from api.schemas.chat_schema import ChatRequest, ChatResponse
from api.schemas.response_schema import SuccessResponse, ErrorResponse
from chat_service import chat_with_ai, chat_stream
from api.exceptions import LLMServiceError, RAGServiceError

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    AI聊天接口
    
    - **question**: 用户问题
    - **subject_id**: 学科ID
    - **model**: 模型名称（可选，默认使用配置中的默认模型）
    - **history**: 对话历史
    """
    try:
        result = chat_with_ai(
            question=request.question,
            subject_id=request.subject_id,
            model=request.model,
            history=request.history,
            image_data=request.image_data,
            image_mime_type=request.image_mime_type,
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
            model=result.get("model", "mimo"),
            latency=result.get("latency", 0.0),
        )
    except (LLMServiceError, RAGServiceError, HTTPException):
        raise
    except Exception as e:
        # 捕获ValidationError等异常
        error_str = str(e)
        if any(keyword in error_str for keyword in ["LLM", "模型", "API Key", "API密钥"]):
            raise LLMServiceError("LLM服务不可用，请检查API配置")
        raise HTTPException(status_code=500, detail="服务器内部错误，请稍后重试")


@router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest):
    """
    流式聊天接口（SSE）
    
    - **question**: 用户问题
    - **subject_id**: 学科ID
    - **history**: 对话历史
    """
    try:
        generator = chat_stream(
            question=request.question,
            subject_id=request.subject_id,
            history=request.history,
            model=request.model,
            image_data=request.image_data,
            image_mime_type=request.image_mime_type,
        )
        
        def sse_generator():
            for event in generator:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        
        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
        )
    except Exception as e:
        error_str = str(e)
        if any(keyword in error_str for keyword in ["LLM", "模型", "API Key", "API密钥"]):
            raise LLMServiceError("LLM服务不可用，请检查API配置")
        elif any(keyword in error_str for keyword in ["RAG", "知识库"]):
            raise RAGServiceError(error_str)
        else:
            raise HTTPException(status_code=500, detail="服务器内部错误，请稍后重试")