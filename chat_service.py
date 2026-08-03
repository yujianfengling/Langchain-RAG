"""
聊天服务模块
负责协调RAG检索、Prompt构造和LLM调用，提供智能问答功能

职责边界：
- 聊天编排（流式/非流式）
- Prompt构造
- 对话历史格式化

公共逻辑（RAG检索、LLM调用、来源提取）委托给 ai_service.py

v2.1 更新：
- 移除与 ai_service.py 重复的 _retrieve_context / _extract_sources / _call_llm
- 统一通过 services.ai_service 调用公共逻辑
"""

import time
from typing import List, Dict, Any, Optional, Generator

# 导入公共AI服务（RAG检索、LLM调用、来源提取）
from services.ai_service import (
    AIServiceError,
    get_rag_context,
    extract_sources,
    call_llm,
)

# 导入LLM模块（流式调用需要直接获取LLM实例）
try:
    from llm import get_llm, get_llm_provider_info
    LLM_AVAILABLE = True
except ImportError as e:
    LLM_AVAILABLE = False
    _LLM_IMPORT_ERROR = str(e)

# 导入RAG上下文构建（用于Prompt构造）
try:
    from rag import build_context
    RAG_AVAILABLE = True
except ImportError as e:
    RAG_AVAILABLE = False
    _RAG_IMPORT_ERROR = str(e)

# 导入提示词模板
try:
    from prompts import get_education_prompt_with_context, get_education_prompt_with_history
    PROMPTS_AVAILABLE = True
except ImportError as e:
    PROMPTS_AVAILABLE = False
    _PROMPTS_IMPORT_ERROR = str(e)

# 导入日志
from utils.logger import logger

# 默认配置
_DEFAULT_TOP_K = 5
_DEFAULT_SYSTEM_MESSAGE = "你是一名智慧教育助手。"


# ============ 非流式聊天 ============

def chat_with_ai(
    question: str,
    subject_id: Optional[str] = None,
    model: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    image_data: Optional[str] = None,
    image_mime_type: Optional[str] = None
) -> Dict[str, Any]:
    """
    智能问答主函数（非流式）

    Args:
        question: 用户问题
        subject_id: 学科ID（可选，用于指定知识库）
        model: 模型名称（可选，默认使用配置中的默认模型）
        history: 对话历史列表
        temperature: 温度参数（可选）
        max_tokens: 最大token数（可选）
        image_data: 图片数据（base64编码，可选）
        image_mime_type: 图片MIME类型（可选）

    Returns:
        响应字典：success, answer, sources, context_used, subject_id, model, latency, error
    """
    start_time = time.time()

    response = {
        "success": False,
        "answer": None,
        "sources": [],
        "context_used": False,
        "subject_id": subject_id,
        "model": model or "mimo",
        "confidence": "medium",
        "latency": 0.0,
        "error": None,
    }

    # 输入验证
    if not question or not question.strip():
        response["error"] = "问题不能为空"
        response["latency"] = time.time() - start_time
        return response

    # 依赖检查
    if not RAG_AVAILABLE:
        response["error"] = f"RAG模块不可用: {_RAG_IMPORT_ERROR}"
        response["latency"] = time.time() - start_time
        return response

    if not LLM_AVAILABLE:
        response["error"] = f"LLM模块不可用: {_LLM_IMPORT_ERROR}"
        response["latency"] = time.time() - start_time
        return response

    if not PROMPTS_AVAILABLE:
        response["error"] = f"提示词模块不可用: {_PROMPTS_IMPORT_ERROR}"
        response["latency"] = time.time() - start_time
        return response

    try:
        # 1. RAG检索（通过ai_service公共接口）
        try:
            context_docs = get_rag_context(question, subject_id, _DEFAULT_TOP_K)
            context_used = len(context_docs) > 0
        except AIServiceError as e:
            response["error"] = str(e)
            response["latency"] = time.time() - start_time
            return response

        # 2. 构建上下文 + 提取来源
        context = build_context(context_docs) if context_used else ""
        response["sources"] = extract_sources(context_docs) if context_used else []

        # 3. 构造Prompt
        if history:
            formatted_history = _format_history(history)
            prompt = get_education_prompt_with_context(question, context, formatted_history)
        else:
            prompt = get_education_prompt_with_context(question, context)

        # 4. 调用LLM（通过ai_service公共接口）
        answer = call_llm(
            prompt=prompt,
            system_message=_DEFAULT_SYSTEM_MESSAGE,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # 5. 获取模型信息
        from services.model_service import get_model_config
        model_config = get_model_config(model)
        response["model"] = model_config.get("name", model or "mimo")

        # 6. 返回结果
        response["success"] = True
        response["answer"] = answer
        response["context_used"] = context_used
        response["confidence"] = "high" if context_used else "medium"
        response["latency"] = time.time() - start_time
        return response

    except AIServiceError as e:
        response["error"] = str(e)
        response["latency"] = time.time() - start_time
        return response
    except Exception as e:
        logger.error(f"聊天处理异常: {e}", exc_info=True)
        response["error"] = f"处理问题时发生错误: {str(e)}"
        response["latency"] = time.time() - start_time
        return response


# ============ 流式聊天 ============

def chat_stream(
    question: str,
    subject_id: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    image_data: Optional[str] = None,
    image_mime_type: Optional[str] = None
) -> Generator[Dict[str, Any], None, None]:
    """
    智能问答流式接口（SSE）

    Args:
        question: 用户问题
        subject_id: 学科ID
        history: 对话历史
        temperature: 温度参数
        max_tokens: 最大token数
        image_data: 图片数据（base64编码，可选）
        image_mime_type: 图片MIME类型（可选）

    Yields:
        流式事件字典：type=start|chunk|end|error
    """
    start_time = time.time()

    # 依赖检查
    if not LLM_AVAILABLE:
        yield {"type": "error", "error": f"LLM模块不可用: {_LLM_IMPORT_ERROR}", "latency": time.time() - start_time}
        return
    if not RAG_AVAILABLE:
        yield {"type": "error", "error": f"RAG模块不可用: {_RAG_IMPORT_ERROR}", "latency": time.time() - start_time}
        return
    if not PROMPTS_AVAILABLE:
        yield {"type": "error", "error": f"提示词模块不可用: {_PROMPTS_IMPORT_ERROR}", "latency": time.time() - start_time}
        return

    # 输入验证
    if not question or not question.strip():
        yield {"type": "error", "error": "问题不能为空", "latency": time.time() - start_time}
        return

    try:
        # 1. RAG检索
        try:
            context_docs = get_rag_context(question, subject_id, _DEFAULT_TOP_K)
            context_used = len(context_docs) > 0
        except AIServiceError as e:
            yield {"type": "error", "error": str(e), "latency": time.time() - start_time}
            return

        # 2. 构建上下文 + 来源
        context = build_context(context_docs) if context_used else ""
        sources = extract_sources(context_docs) if context_used else []

        # 3. 构造Prompt
        if history:
            formatted_history = _format_history(history)
            prompt = get_education_prompt_with_context(question, context, formatted_history)
        else:
            prompt = get_education_prompt_with_context(question, context)

        # 4. 获取LLM实例 + 模型信息
        llm = get_llm(temperature=temperature, max_tokens=max_tokens)
        llm_info = get_llm_provider_info()
        model_name = llm_info.get("model", "unknown")

        # 5. 发送start事件
        yield {
            "type": "start",
            "sources": sources,
            "model": model_name,
            "confidence": "high" if context_used else "medium",
            "context_used": context_used,
            "subject_id": subject_id,
        }

        # 6. 流式调用LLM
        full_answer = ""
        for chunk in _call_llm_stream(llm, prompt):
            full_answer += chunk
            yield {"type": "chunk", "content": chunk}

        # 7. 发送end事件
        yield {
            "type": "end",
            "full_answer": full_answer,
            "latency": time.time() - start_time,
        }

    except AIServiceError as e:
        yield {"type": "error", "error": str(e), "latency": time.time() - start_time}
    except Exception as e:
        logger.error(f"流式聊天异常: {e}", exc_info=True)
        yield {"type": "error", "error": f"处理问题时发生错误: {str(e)}", "latency": time.time() - start_time}


# ============ 私有辅助函数（chat_service独有的逻辑） ============

def _call_llm_stream(llm: Any, prompt: str) -> Generator[str, None, None]:
    """
    流式调用LLM（chat_service独有，ai_service不提供流式接口）

    Args:
        llm: LLM实例
        prompt: 提示词

    Yields:
        文本片段
    """
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content=_DEFAULT_SYSTEM_MESSAGE),
            HumanMessage(content=prompt),
        ]

        if hasattr(llm, "stream"):
            for chunk in llm.stream(messages):
                yield chunk.content if hasattr(chunk, "content") else str(chunk)
        else:
            # 不支持流式，回退为一次性调用并模拟流式输出
            response = llm.invoke(messages)
            content = response.content if hasattr(response, "content") else str(response)
            chunk_size = 10
            for i in range(0, len(content), chunk_size):
                yield content[i : i + chunk_size]

    except ImportError:
        # LangChain不可用，直接调用
        if hasattr(llm, "stream"):
            for chunk in llm.stream(prompt):
                yield str(chunk)
        else:
            response = llm.invoke(prompt) if hasattr(llm, "invoke") else llm.generate([prompt])
            content = ""
            if hasattr(response, "content"):
                content = response.content
            elif hasattr(response, "generations") and response.generations:
                content = response.generations[0][0].text
            else:
                content = str(response)
            chunk_size = 10
            for i in range(0, len(content), chunk_size):
                yield content[i : i + chunk_size]


def _format_history(history: List[Dict[str, str]], max_turns: int = 5) -> str:
    """
    格式化对话历史（chat_service独有逻辑）

    Args:
        history: 对话历史列表
        max_turns: 最大保留轮数

    Returns:
        格式化的历史字符串
    """
    if not history:
        return ""

    recent_history = history[-max_turns * 2:]

    formatted_parts = []
    for msg in recent_history:
        role = msg.get("role", "").lower()
        content = msg.get("content", "").strip()
        if not content:
            continue
        if role == "user":
            formatted_parts.append(f"学生：{content}")
        elif role == "assistant":
            formatted_parts.append(f"助手：{content}")

    return "\n".join(formatted_parts)


def check_chat_service_status() -> Dict[str, Any]:
    """
    检查聊天服务状态

    Returns:
        状态信息字典
    """
    from services.ai_service import check_ai_service_status

    ai_status = check_ai_service_status()

    status = {
        "rag_available": RAG_AVAILABLE,
        "llm_available": LLM_AVAILABLE,
        "prompts_available": PROMPTS_AVAILABLE,
        "default_top_k": _DEFAULT_TOP_K,
        "ai_service": ai_status,
    }

    return status


# 便捷函数
def quick_chat(question: str, subject_id: Optional[str] = None) -> str:
    """
    快速问答（简化接口）

    Args:
        question: 用户问题
        subject_id: 学科ID

    Returns:
        AI回答字符串

    Raises:
        RuntimeError: 问答失败
    """
    response = chat_with_ai(question, subject_id)
    if response["success"]:
        return response["answer"]
    else:
        raise RuntimeError(response["error"])


# 公开API
__all__ = [
    "chat_with_ai",
    "chat_stream",
    "check_chat_service_status",
    "quick_chat",
]
