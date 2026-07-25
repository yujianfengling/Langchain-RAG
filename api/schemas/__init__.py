# api/schemas/__init__.py
# 数据模型模块

from .chat_schema import ChatRequest, ChatResponse
from .knowledge_schema import KnowledgeBuildRequest, KnowledgeListResponse
from .response_schema import SuccessResponse, ErrorResponse

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "KnowledgeBuildRequest",
    "KnowledgeListResponse",
    "SuccessResponse",
    "ErrorResponse",
]