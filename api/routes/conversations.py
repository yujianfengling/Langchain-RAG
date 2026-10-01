# api/routes/conversations.py
# 历史对话接口路由（V2.5.1 Checkpoint A）
#
# 阶段一补验：会话与消息都是**个人数据**，全部端点必须做对象级归属校验。
# 归属校验失败统一按 404 返回（不暴露"该会话存在但不属于你"）。

from typing import Optional
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import require_current_user
from api.schemas.response_schema import SuccessResponse
from services.conversation_service import (
    ConversationServiceError,
    create_conversation,
    list_conversations,
    get_conversation,
    delete_conversation,
    list_messages,
)

router = APIRouter()


class CreateConversationRequest(BaseModel):
    """创建会话请求"""
    subject_id: str
    model: Optional[str] = None
    title: Optional[str] = None


def _not_found(message: str = "会话不存在"):
    return JSONResponse(
        status_code=404,
        content={"success": False, "data": None,
                 "message": message, "error_type": "invalid_request"},
    )


@router.get("/conversations")
async def get_conversations(subject_id: Optional[str] = None,
                            user_id: str = Depends(require_current_user)):
    """获取会话列表（仅本人的会话，可按学科过滤，按更新时间倒序）"""
    try:
        conversations = list_conversations(subject_id=subject_id, user_id=user_id)
        return SuccessResponse(data=conversations, message="获取会话列表成功")
    except ConversationServiceError as e:
        return JSONResponse(
            status_code=400,
            content={"success": False, "data": None,
                     "message": e.message, "error_type": e.error_type},
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": "加载会话列表失败", "error_type": "database_error"},
        )


@router.post("/conversations")
async def post_conversation(request: CreateConversationRequest,
                            user_id: str = Depends(require_current_user)):
    """创建新会话（绑定学科，归属当前用户）"""
    try:
        conv = create_conversation(
            subject_id=request.subject_id,
            model=request.model,
            title=request.title,
            user_id=user_id,
        )
        return SuccessResponse(data=conv, message="会话创建成功")
    except ConversationServiceError as e:
        return JSONResponse(
            status_code=400,
            content={"success": False, "data": None,
                     "message": e.message, "error_type": e.error_type},
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": "创建会话失败", "error_type": "database_error"},
        )


@router.get("/conversations/{conversation_id}")
async def get_conversation_detail(conversation_id: str,
                                  user_id: str = Depends(require_current_user)):
    """获取单个会话（仅本人）"""
    try:
        conv = get_conversation(conversation_id, user_id=user_id)
        if not conv:
            return _not_found()
        return SuccessResponse(data=conv, message="获取会话成功")
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": "获取会话失败", "error_type": "database_error"},
        )


@router.delete("/conversations/{conversation_id}")
async def remove_conversation(conversation_id: str,
                              user_id: str = Depends(require_current_user)):
    """删除会话及其全部消息（仅本人）"""
    try:
        ok = delete_conversation(conversation_id, user_id=user_id)
        if not ok:
            return _not_found()
        return SuccessResponse(data=None, message="会话已删除")
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": "删除会话失败", "error_type": "database_error"},
        )


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation_id: str,
                                    user_id: str = Depends(require_current_user)):
    """获取会话的全部消息（仅本人；按时间正序，含结构化 payload）"""
    try:
        conv = get_conversation(conversation_id, user_id=user_id)
        if not conv:
            return _not_found()
        messages = list_messages(conversation_id, user_id=user_id)
        return SuccessResponse(data=messages, message="获取消息成功")
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": "获取消息失败", "error_type": "database_error"},
        )
