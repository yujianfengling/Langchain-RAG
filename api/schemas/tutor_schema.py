"""统一伴学接口数据模型（2026-10-07）。"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# 引导方式：direct=直接回答（默认，保持既有行为）；socratic=先问再提示，不直接给答案。
GuidanceMode = Literal["direct", "socratic"]


class TutorStreamRequest(BaseModel):
    message: str
    subject_id: str
    ability: Optional[str] = "chat"          # chat|explain|practice|map|organize
    guidance_mode: Optional[GuidanceMode] = None  # 缺省 = 沿用会话设置
    doc_ids: Optional[List[str]] = None      # 资料范围（None/空 = 全部）
    node_id: Optional[str] = None            # 当前知识点
    conversation_id: Optional[str] = None
    model: Optional[str] = None


class TutorSessionUpdate(BaseModel):
    subject_id: str
    ability: Optional[str] = None
    guidance_mode: Optional[GuidanceMode] = None
    doc_ids: Optional[List[str]] = None
    node_id: Optional[str] = None
    conversation_id: Optional[str] = None
    goal: Optional[str] = None
    deadline: Optional[str] = None
    daily_minutes: Optional[int] = None


class WhiteboardRequest(BaseModel):
    node_id: Optional[str] = None
    doc_ids: Optional[List[str]] = None
    subject_id: str
    topic: str
    template: Optional[str] = None
    model: Optional[str] = None


class MindmapRequest(BaseModel):
    """白板里"按这一页课件生成思维导图"的请求（2026-11-14）。

    file_name + page（0 基页码）定位当前展示的课件页；page 为 null 时只按 topic 兜底检索。
    """
    subject_id: str
    file_name: Optional[str] = None
    page: Optional[int] = None
    topic: Optional[str] = None
    model: Optional[str] = None


class MindboardSaveRequest(BaseModel):
    """白板思维板书存档（2026-11-14）：按 用户+学科+key 覆盖保存。"""
    subject_id: str
    key: str = "free"
    data: Dict[str, Any]


class MapGenerateRequest(BaseModel):
    subject_id: str
    doc_ids: Optional[List[str]] = None
    force: bool = False
    model: Optional[str] = None


class ArtifactCreate(BaseModel):
    subject_id: str
    kind: str
    title: str
    content: Dict[str, Any] = Field(default_factory=dict)
    node_id: Optional[str] = None
    doc_ids: Optional[List[str]] = None


class ArtifactUpdate(BaseModel):
    subject_id: str
    title: Optional[str] = None
    content: Optional[Dict[str, Any]] = None


class PracticeAnswerRequest(BaseModel):
    subject_id: str
    question_id: str
    answer: Optional[str] = None
    node_id: Optional[str] = None
    client_token: Optional[str] = None
    conversation_id: Optional[str] = None
    duration_sec: Optional[int] = None


class NodeStatusRequest(BaseModel):
    subject_id: str
    node_id: str
    status: str
