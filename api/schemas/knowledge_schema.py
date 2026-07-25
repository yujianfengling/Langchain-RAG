# api/schemas/knowledge_schema.py
# 知识库接口数据模型

from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime


class KnowledgeBuildRequest(BaseModel):
    """知识库构建请求"""
    subject_id: str
    path: str


class KnowledgeListResponse(BaseModel):
    """知识库列表响应"""
    subjects: List[str]


class KnowledgeStatsResponse(BaseModel):
    """知识库统计响应"""
    subject_id: str
    stats: dict


class SubjectInfo(BaseModel):
    """学科信息"""
    subject_id: str
    name: str
    description: Optional[str] = None


class DocumentInfo(BaseModel):
    """文档信息"""
    name: str
    size: str
    type: str
    upload_time: str


class DocumentListResponse(BaseModel):
    """文档列表响应"""
    subject_id: str
    documents: List[DocumentInfo]


class UploadResponse(BaseModel):
    """文件上传响应"""
    subject_id: str
    filename: str
    size: int
    status: str
    message: str


class EducationSummaryRequest(BaseModel):
    """知识点总结请求"""
    text: str


class EducationExplainRequest(BaseModel):
    """题目解析请求"""
    question: str


class EducationAdviceRequest(BaseModel):
    """学习建议请求"""
    history: List[dict] = []


# ========== 教育路由所需 Schema（由 education.py 导入） ==========

class SummarizeRequest(BaseModel):
    """知识点总结请求（教育路由用）"""
    knowledge_points: List[str]


class SummaryResponse(BaseModel):
    """知识点总结响应"""
    success: bool
    data: Dict[str, Any]
    message: str


class ExplainRequest(BaseModel):
    """题目解析请求"""
    question: str


class ExplanationResponse(BaseModel):
    """题目解析响应"""
    success: bool
    data: Dict[str, Any]
    message: str


class AdviceRequest(BaseModel):
    """学习建议请求"""
    history: Optional[List[Dict[str, Any]]] = None


class AdviceResponse(BaseModel):
    """学习建议响应"""
    success: bool
    data: Dict[str, Any]
    message: str


class KnowledgeResponse(BaseModel):
    """通用知识库响应"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    message: str