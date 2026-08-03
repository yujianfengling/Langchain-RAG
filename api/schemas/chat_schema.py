# api/schemas/chat_schema.py
# 聊天接口数据模型

from typing import List, Optional
from pydantic import BaseModel


class ChatRequest(BaseModel):
    """聊天请求"""
    question: str
    subject_id: str
    model: Optional[str] = None
    history: List[dict] = []
    image_data: Optional[str] = None  # base64编码的图片数据
    image_mime_type: Optional[str] = None  # 图片MIME类型


class ChatResponse(BaseModel):
    """聊天响应"""
    success: bool = True
    answer: str
    sources: List[dict] = []
    model: str = "mimo"
    latency: float = 0.0


class ChatStreamRequest(BaseModel):
    """流式聊天请求"""
    question: str
    subject_id: str
    history: List[dict] = []