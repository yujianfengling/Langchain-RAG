"""
知识库就绪守卫（V2.5 集成修复）

统一判断"学科是否存在 / 知识库是否已构建"，供智能笔记、学习计划等
依赖 RAG 的功能在调用检索前快速失败，避免无谓的 RAG/LLM 调用。

错误类型：
- subject_not_found: 学科不存在
- knowledge_base_not_ready: 学科存在但知识库未构建
"""

from typing import Optional
from utils.logger import logger


class KBError(Exception):
    """知识库就绪异常（携带 error_type）"""

    def __init__(self, error_type: str, message: str):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


def check_kb_ready(subject_id: str) -> Optional[str]:
    """
    检查学科知识库是否就绪

    Returns:
        None = 就绪；否则返回 error_type（subject_not_found / knowledge_base_not_ready）
    """
    try:
        from services.subject_service import get_subjects
        from services.knowledge_service import list_knowledge_bases

        subjects = {s.get("id") for s in get_subjects()}
        if subject_id not in subjects:
            return "subject_not_found"

        kbs = {kb["subject_id"]: kb["status"] for kb in list_knowledge_bases()}
        if kbs.get(subject_id) != "ready":
            return "knowledge_base_not_ready"

        return None
    except Exception as e:
        # 就绪判断本身失败时，不阻断（宁可让后续 RAG 真实判断）
        logger.warning(f"知识库就绪检查失败（放行交由 RAG 判断）: {e}")
        return None


def require_kb_ready(subject_id: str) -> None:
    """
    要求知识库就绪，未就绪则抛 KBError（快速失败，不调 RAG/LLM）

    Raises:
        KBError
    """
    error_type = check_kb_ready(subject_id)
    if error_type == "subject_not_found":
        raise KBError("subject_not_found", "课程不存在，请先创建课程")
    if error_type == "knowledge_base_not_ready":
        raise KBError("knowledge_base_not_ready", "请先构建该课程的知识库")
