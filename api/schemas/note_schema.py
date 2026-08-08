# api/schemas/note_schema.py
# 智能笔记数据模型（V2.5 Task B）

from typing import Optional
from pydantic import BaseModel


class SmartNoteRequest(BaseModel):
    """智能笔记生成请求"""
    subject_id: str
    topic: str
    user_instruction: Optional[str] = None
    model: Optional[str] = None
