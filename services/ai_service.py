"""
AI服务模块 - 公共RAG调用逻辑

职责：
1. 获取知识库上下文（RAG检索）
2. 调用LLM生成回答
3. 返回基础回答结构

chat_service: 负责普通聊天逻辑（流式/非流式）
education_service: 负责教育prompt增强（总结/解析/建议）
"""

import time
from typing import List, Dict, Any, Optional

from utils.logger import logger

# 导入RAG模块
try:
    from rag import retrieve_context, build_context
    RAG_AVAILABLE = True
except ImportError as e:
    RAG_AVAILABLE = False
    RAG_IMPORT_ERROR = str(e)

# 导入LLM模块
try:
    from llm import get_llm, get_llm_provider_info
    LLM_AVAILABLE = True
except ImportError as e:
    LLM_AVAILABLE = False
    LLM_IMPORT_ERROR = str(e)


class AIServiceError(Exception):
    """AI服务异常"""
    pass


def get_rag_context(
    question: str,
    subject_id: Optional[str] = None,
    top_k: int = 5
) -> List:
    """
    获取RAG上下文文档
    
    Args:
        question: 用户问题
        subject_id: 学科ID（可选）
        top_k: 返回文档数量
        
    Returns:
        相关文档列表
        
    Raises:
        AIServiceError: RAG检索失败时抛出（不再静默返回空列表）
    """
    if not RAG_AVAILABLE:
        error_msg = f"RAG模块不可用: {RAG_IMPORT_ERROR if not RAG_AVAILABLE else ''}"
        logger.error(error_msg)
        raise AIServiceError(error_msg)
    
    try:
        context_docs = retrieve_context(
            question=question,
            subject_id=subject_id,
            top_k=top_k
        )
        return context_docs
    except Exception as e:
        error_msg = f"RAG检索失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise AIServiceError(error_msg)


def build_context_string(context_docs: List) -> str:
    """
    将文档列表构建为上下文字符串
    
    Args:
        context_docs: 文档列表
        
    Returns:
        上下文字符串
    """
    if not context_docs:
        return ""
    
    try:
        return build_context(context_docs)
    except Exception as e:
        logger.warning(f"构建上下文字符串失败: {e}")
        return ""


def extract_sources(context_docs: List) -> List[Dict[str, Any]]:
    """
    从上下文文档中提取来源信息（API 边界统一结构）

    返回稳定结构：
    {
        "id": 1,
        "file_name": "数据结构.pdf",   # 优先取 metadata.file_name，避免绝对路径
        "page": 12,                    # 无页码时为 None
        "type": "text",                # modality/type，无则 text
    }

    Args:
        context_docs: 上下文文档列表

    Returns:
        来源信息列表
    """
    sources = []
    for i, doc in enumerate(context_docs):
        metadata = doc.metadata if hasattr(doc, 'metadata') else {}

        # 优先取 file_name（不含路径）；回退到取 source 的最后一段；再回退到占位
        file_name = metadata.get('file_name')
        if not file_name:
            src = metadata.get('source') or metadata.get('filename') or ''
            file_name = str(src).split('\\')[-1].split('/')[-1] if src else f'文档_{i+1}'

        page = metadata.get('page')
        doc_type = metadata.get('type') or metadata.get('modality') or 'text'

        sources.append({
            "id": i + 1,
            "file_name": file_name,
            "page": page,
            "type": doc_type,
        })

    return sources


def call_llm(
    prompt: str,
    system_message: str = "你是一名智慧教育助手。",
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None
) -> str:
    """
    调用LLM生成回答（非流式）
    
    Args:
        prompt: 用户提示词
        system_message: 系统消息
        model: 模型名称（可选，默认使用配置中的默认模型）
        temperature: 温度参数
        max_tokens: 最大token数
        
    Returns:
        LLM生成的回答内容
        
    Raises:
        AIServiceError: LLM调用失败
    """
    if not LLM_AVAILABLE:
        raise AIServiceError(f"LLM模块不可用: {LLM_IMPORT_ERROR if not LLM_AVAILABLE else ''}")
    
    try:
        # 使用新的ModelProvider架构
        from services.model_service import get_model_provider
        
        provider = get_model_provider(model)
        
        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ]
        
        result = provider.chat(messages, temperature=temperature, max_tokens=max_tokens)
        return result.get("answer", "")
        
    except Exception as e:
        logger.error(f"LLM调用失败: {e}", exc_info=True)
        raise AIServiceError(f"LLM调用失败: {str(e)}")


def get_model_info() -> Dict[str, Any]:
    """
    获取当前LLM模型信息
    
    Returns:
        模型信息字典，包含 model, provider 等
    """
    if not LLM_AVAILABLE:
        return {"model": "unknown", "provider": "unknown", "available": False}
    
    try:
        llm_info = get_llm_provider_info()
        return {
            "model": llm_info.get("model", "unknown"),
            "provider": llm_info.get("provider", "unknown"),
            "available": True,
        }
    except Exception as e:
        logger.warning(f"获取模型信息失败: {e}")
        return {"model": "unknown", "provider": "unknown", "available": False}


def retrieve_and_build_context(
    question: str,
    subject_id: Optional[str] = None,
    top_k: int = 5
) -> Dict[str, Any]:
    """
    一站式RAG检索 + 上下文构建
    
    Args:
        question: 用户问题
        subject_id: 学科ID
        top_k: 返回文档数量
        
    Returns:
        {
            "context": str,          # 构建好的上下文字符串
            "sources": List[Dict],   # 来源信息列表
            "context_used": bool,    # 是否使用了上下文
            "documents": List,       # 原始文档列表
        }
    """
    # 1. 获取RAG上下文
    context_docs = get_rag_context(question, subject_id, top_k)
    context_used = len(context_docs) > 0
    
    # 2. 构建上下文字符串
    context = build_context_string(context_docs) if context_used else ""
    
    # 3. 提取来源信息
    sources = extract_sources(context_docs) if context_used else []
    
    return {
        "context": context,
        "sources": sources,
        "context_used": context_used,
        "documents": context_docs,
    }


def check_ai_service_status() -> Dict[str, Any]:
    """
    检查AI服务状态
    
    Returns:
        状态信息字典
    """
    status = {
        "rag_available": RAG_AVAILABLE,
        "llm_available": LLM_AVAILABLE,
    }
    
    if not RAG_AVAILABLE:
        status["rag_import_error"] = RAG_IMPORT_ERROR
    
    if not LLM_AVAILABLE:
        status["llm_import_error"] = LLM_IMPORT_ERROR
    
    if LLM_AVAILABLE:
        status["model_info"] = get_model_info()
    
    return status


# 公开API
__all__ = [
    "AIServiceError",
    "get_rag_context",
    "build_context_string",
    "extract_sources",
    "call_llm",
    "get_model_info",
    "retrieve_and_build_context",
    "check_ai_service_status",
]
