# api/schemas/chat_schema.py
# 聊天接口数据模型

from typing import List, Optional
from pydantic import BaseModel, Field


def _default_text_model() -> str:
    """未指定模型时的默认文本模型（与 config/models.get_default_model 保持一致）"""
    from config.models import get_default_model
    return get_default_model()


class ChatRequest(BaseModel):
    """聊天请求"""
    question: str
    subject_id: str
    model: Optional[str] = None
    history: List[dict] = []
    image_data: Optional[str] = None  # base64编码的图片数据
    image_mime_type: Optional[str] = None  # 图片MIME类型
    conversation_id: Optional[str] = None  # 会话ID（V2.5.1：服务端持久化上下文）
    mode: Optional[str] = None  # 学习模式：chat|smart_note|summary|explanation|advice|quiz
    # 资料范围（2026-10-06）：仅在这些文件内检索；None/空 = 本学科全部资料
    source_files: Optional[List[str]] = None


class ChatResponse(BaseModel):
    """聊天响应

    结构化模式（smart_note/summary/explanation/advice/quiz）额外返回
    message_type 与 payload —— 否则 response_model 会把结构化卡片数据过滤掉，
    非流式调用方拿不到题单/笔记内容。
    """
    success: bool = True
    answer: str
    sources: List[dict] = []
    # 实际使用的文本模型（未指定时由后端默认模型决定，默认 DeepSeek）
    model: str = Field(default_factory=_default_text_model)
    # 提供方键（deepseek / mimo / qwen）与展示名，便于核验"显示 = 实际调用"
    provider: Optional[str] = None
    provider_display: Optional[str] = None
    latency: float = 0.0
    # 检索到课程资料并放进上下文（不等于"回答引用了它"）
    context_used: bool = False
    # 回答中显式标注引用的来源编号（如 [1][2]）——"实际引用"的确定性信号
    cited_source_ids: List[int] = []
    message_type: Optional[str] = None
    payload: Optional[dict] = None
    detail_level: Optional[str] = None
    # 本轮教学策略与依据（画像 → 教学安排的可解释输出）
    teaching: Optional[dict] = None


class ChatStreamRequest(BaseModel):
    """流式聊天请求"""
    question: str
    subject_id: str
    history: List[dict] = []