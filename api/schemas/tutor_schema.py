"""统一伴学接口数据模型（2026-10-07）。"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TutorStreamRequest(BaseModel):
    message: str
    subject_id: str
    ability: Optional[str] = "chat"          # chat|explain|practice|map|organize
    doc_ids: Optional[List[str]] = None      # 资料范围（None/空 = 全部）
    node_id: Optional[str] = None            # 当前知识点
    conversation_id: Optional[str] = None
    model: Optional[str] = None


class TutorSessionUpdate(BaseModel):
    subject_id: str
    ability: Optional[str] = None
    doc_ids: Optional[List[str]] = None
    node_id: Optional[str] = None
    conversation_id: Optional[str] = None
    goal: Optional[str] = None
    deadline: Optional[str] = None
    daily_minutes: Optional[int] = None


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
