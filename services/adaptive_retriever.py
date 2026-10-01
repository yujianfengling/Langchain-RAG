"""
自适应检索模块（V2.6 Agent核心）

根据用户画像和历史交互动态调整检索策略，实现个性化问答。

设计原则：
- 根据用户知识水平调整 top_k 和 rerank 策略
- 根据用户反馈历史调整检索权重
- 根据学习计划进度调整上下文
- 复用现有 RAG 模块，不重复实现检索逻辑
"""

from typing import List, Dict, Any, Optional
from utils.logger import logger

try:
    from rag import retrieve_context, build_context, rerank_documents
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False

from services.user_profiler import (
    get_or_create_profile,
    get_user_interactions,
    get_weak_points,
    get_strong_points,
    analyze_user_performance,
)


def get_adaptive_retrieval_params(user_level: float) -> Dict[str, Any]:
    """
    根据用户知识水平返回自适应检索参数

    Args:
        user_level: 0-1，用户知识水平

    Returns:
        自适应检索参数
    """
    if user_level < 0.3:  # 初学者
        return {"top_k": 8, "include_basics": True, "detail_level": "concise"}
    elif user_level < 0.7:  # 中级
        return {"top_k": 5, "include_basics": False, "detail_level": "balanced"}
    else:  # 高级
        return {"top_k": 3, "include_basics": False, "detail_level": "expanded"}


def retrieve_with_user_context(
    question: str,
    user_id: str = "default_user",
    subject_id: Optional[str] = None,
    override_top_k: Optional[int] = None,
) -> Dict[str, Any]:
    """
    结合用户画像的自适应检索

    Args:
        question: 用户问题
        user_id: 用户ID
        subject_id: 学科ID
        override_top_k: 覆盖 top_k

    Returns:
        {
            "context": str,
            "sources": list,
            "context_used": bool,
            "documents": list,
            "user_level": float,
            "adaptive_params": dict,
        }
    """
    if not RAG_AVAILABLE:
        return {"context": "", "sources": [], "context_used": False, "documents": [], "user_level": 0.5, "adaptive_params": {}}

    try:
        # 1. 获取用户画像
        profile = get_or_create_profile(user_id)
        user_level = profile.get("knowledge_level", {}).get(subject_id, 0.5) if subject_id else 0.5

        # 2. 获取自适应参数
        params = get_adaptive_retrieval_params(user_level)
        top_k = override_top_k or params["top_k"]

        # 3. 执行检索（按查看者可见范围过滤课程资料：教师草稿不得进入学生上下文）
        docs = retrieve_context(question=question, subject_id=subject_id, top_k=top_k)
        from services import teaching_access
        docs = teaching_access.filter_visible_documents(user_id, subject_id, docs)
        context_used = len(docs) > 0
        context = build_context(docs) if context_used else ""

        # 4. 提取来源
        sources = []
        for i, doc in enumerate(docs):
            metadata = doc.metadata if hasattr(doc, "metadata") else {}
            file_name = metadata.get("file_name") or str(metadata.get("source", "")).split("\\")[-1].split("/")[-1] if metadata.get("source") else f"doc_{i+1}"
            sources.append({"id": i + 1, "file_name": file_name, "page": metadata.get("page"), "type": metadata.get("type", "text")})

        return {
            "context": context,
            "sources": sources,
            "context_used": context_used,
            "documents": docs,
            "user_level": user_level,
            "adaptive_params": params,
        }
    except Exception as e:
        logger.error(f"自适应检索失败: {e}", exc_info=True)
        return {"context": "", "sources": [], "context_used": False, "documents": [], "user_level": 0.5, "adaptive_params": {}}


def get_personalized_system_prompt(user_id: str, subject_id: Optional[str] = None, base_prompt: str = "你是一名智慧教育助手。") -> str:
    """
    根据用户画像生成个性化系统提示词

    Args:
        user_id: 用户ID
        subject_id: 学科ID
        base_prompt: 基础提示词

    Returns:
        个性化系统提示词
    """
    try:
        profile = get_or_create_profile(user_id)
        user_level = profile.get("knowledge_level", {}).get(subject_id, 0.5) if subject_id else 0.5
        style = profile.get("learning_style", "balanced")

        # 根据知识水平调整提示词
        level_hint = ""
        if user_level < 0.3:
            level_hint = "用户是初学者，请用通俗易懂的语言解释，多举例子，避免过于专业的术语。"
        elif user_level < 0.7:
            level_hint = "用户有一定基础，可以适当使用专业术语，但仍需清晰解释关键概念。"
        else:
            level_hint = "用户有较强基础，可以直接使用专业术语，提供深入分析。"

        # 根据学习风格调整
        style_hint = ""
        if style == "visual":
            style_hint = "请多使用图表、示意图、流程图等可视化方式辅助说明。"
        elif style == "auditory":
            style_hint = "请多用口语化表达，适合朗读理解。"
        elif style == "reading":
            style_hint = "请提供结构化的文字说明，便于阅读理解。"
        elif style == "kinesthetic":
            style_hint = "请多提供实践练习和动手操作的建议。"

        parts = [base_prompt, level_hint, style_hint]
        return " ".join(p for p in parts if p)
    except Exception as e:
        logger.warning(f"生成个性化提示词失败，使用默认: {e}")
        return base_prompt


def get_learning_context_for_chat(user_id: str, subject_id: Optional[str] = None) -> Dict[str, Any]:
    """
    获取用户学习上下文，用于聊天时的个性化

    Returns:
        {
            "user_level": float,
            "weak_points": list,
            "strong_points": list,
            "recent_interactions": list,
            "performance": dict,
        }
    """
    try:
        profile = get_or_create_profile(user_id)
        user_level = profile.get("knowledge_level", {}).get(subject_id, 0.5) if subject_id else 0.5

        weak = get_weak_points(user_id, subject_id) if subject_id else []
        strong = get_strong_points(user_id, subject_id) if subject_id else []
        recent = get_user_interactions(user_id, subject_id, limit=5)
        perf = analyze_user_performance(user_id, subject_id) if subject_id else {}

        return {
            "user_level": user_level,
            "weak_points": weak,
            "strong_points": strong,
            "recent_interactions": recent,
            "performance": perf,
        }
    except Exception as e:
        logger.warning(f"获取学习上下文失败: {e}")
        return {"user_level": 0.5, "weak_points": [], "strong_points": [], "recent_interactions": [], "performance": {}}


__all__ = [
    "get_adaptive_retrieval_params",
    "retrieve_with_user_context",
    "get_personalized_system_prompt",
    "get_learning_context_for_chat",
]
