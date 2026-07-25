"""
聊天服务模块
负责协调RAG检索、Prompt构造和LLM调用，提供智能问答功能
使用统一的LLM接口和独立的提示词模板
"""

import os
import time
from typing import List, Dict, Any, Optional, Union, Generator

# 导入本地RAG服务模块
try:
    from rag import retrieve_context, build_context, list_available_subjects
    RAG_AVAILABLE = True
except ImportError as e:
    RAG_AVAILABLE = False
    IMPORT_ERROR = str(e)

# 导入LLM模块
try:
    from llm import get_llm, get_llm_provider_info, check_llm_availability
    LLM_AVAILABLE = True
except ImportError as e:
    LLM_AVAILABLE = False
    LLM_IMPORT_ERROR = str(e)

# 导入提示词模板
try:
    from prompts import get_education_prompt_with_context, get_education_prompt_with_history
    PROMPTS_AVAILABLE = True
except ImportError as e:
    PROMPTS_AVAILABLE = False
    PROMPTS_IMPORT_ERROR = str(e)


# 默认配置
DEFAULT_TOP_K = 5


def chat_with_ai(
    question: str,
    subject_id: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None
) -> Dict[str, Any]:
    """
    智能问答主函数（非流式）
    
    Args:
        question: 用户问题
        subject_id: 学科ID（可选，用于指定知识库）
        history: 对话历史列表，格式为 [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        temperature: 温度参数（可选）
        max_tokens: 最大token数（可选）
        
    Returns:
        响应字典，包含：
        - success: 是否成功
        - answer: AI回答内容
        - sources: 引用来源列表
        - context_used: 是否使用了RAG上下文
        - subject_id: 实际使用的学科ID
        - model: 使用的模型名称
        - latency: 响应时间（秒）
        - error: 错误信息（如果失败）
    """
    start_time = time.time()
    
    # 初始化响应
    response = {
        "success": False,
        "answer": None,
        "sources": [],
        "context_used": False,
        "subject_id": subject_id,
        "model": None,
        "confidence": "medium",
        "latency": 0.0,
        "error": None
    }
    
    # 输入验证
    if not question or not question.strip():
        response["error"] = "问题不能为空"
        response["latency"] = time.time() - start_time
        return response
    
    # 检查依赖
    if not RAG_AVAILABLE:
        response["error"] = f"RAG模块不可用: {IMPORT_ERROR}"
        response["latency"] = time.time() - start_time
        return response
    
    if not LLM_AVAILABLE:
        response["error"] = f"LLM模块不可用: {LLM_IMPORT_ERROR}"
        response["latency"] = time.time() - start_time
        return response
    
    if not PROMPTS_AVAILABLE:
        response["error"] = f"提示词模块不可用: {PROMPTS_IMPORT_ERROR}"
        response["latency"] = time.time() - start_time
        return response
    
    try:
        # 1. 获取RAG上下文（可能抛出RuntimeError）
        try:
            context_docs = _retrieve_context(question, subject_id)
            context_used = len(context_docs) > 0
        except RuntimeError as rag_error:
            # RAG检索失败，返回明确错误信息
            response["error"] = str(rag_error)
            response["latency"] = time.time() - start_time
            return response
        
        # 2. 构建上下文字符串
        if context_used:
            context = build_context(context_docs)
            # 提取来源信息
            response["sources"] = _extract_sources(context_docs)
        else:
            context = ""
        
        # 3. 构建提示词
        if history:
            formatted_history = _format_history(history)
            prompt = get_education_prompt_with_context(question, context, formatted_history)
        else:
            prompt = get_education_prompt_with_context(question, context)
        
        # 4. 调用LLM
        llm = get_llm(temperature=temperature, max_tokens=max_tokens)
        
        # 获取模型信息
        llm_info = get_llm_provider_info()
        response["model"] = llm_info.get("model", "unknown")
        
        # 调用LLM
        answer = _call_llm(llm, prompt)
        
        # 5. 更新响应
        response["success"] = True
        response["answer"] = answer
        response["context_used"] = context_used
        response["confidence"] = "high" if context_used else "medium"
        response["latency"] = time.time() - start_time
        
        return response
        
    except Exception as e:
        response["error"] = f"处理问题时发生错误: {str(e)}"
        response["latency"] = time.time() - start_time
        return response


def chat_stream(
    question: str,
    subject_id: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None
) -> Generator[Dict[str, Any], None, None]:
    """
    智能问答流式接口
    
    Args:
        question: 用户问题
        subject_id: 学科ID（可选，用于指定知识库）
        history: 对话历史列表
        temperature: 温度参数（可选）
        max_tokens: 最大token数（可选）
        
    Yields:
        流式响应字典，包含：
        - type: 响应类型（"start", "chunk", "end", "error"）
        - content: 内容片段
        - sources: 引用来源列表（仅在start时提供）
        - model: 使用的模型名称（仅在start时提供）
        - latency: 总响应时间（仅在end时提供）
        - error: 错误信息（仅在error时提供）
    """
    start_time = time.time()
    
    # 检查依赖
    if not RAG_AVAILABLE:
        yield {
            "type": "error",
            "error": f"RAG模块不可用: {IMPORT_ERROR}",
            "latency": time.time() - start_time
        }
        return
    
    if not LLM_AVAILABLE:
        yield {
            "type": "error",
            "error": f"LLM模块不可用: {LLM_IMPORT_ERROR}",
            "latency": time.time() - start_time
        }
        return
    
    if not PROMPTS_AVAILABLE:
        yield {
            "type": "error",
            "error": f"提示词模块不可用: {PROMPTS_IMPORT_ERROR}",
            "latency": time.time() - start_time
        }
        return
    
    # 输入验证
    if not question or not question.strip():
        yield {
            "type": "error",
            "error": "问题不能为空",
            "latency": time.time() - start_time
        }
        return
    
    try:
        # 1. 获取RAG上下文（可能抛出RuntimeError）
        try:
            context_docs = _retrieve_context(question, subject_id)
            context_used = len(context_docs) > 0
        except RuntimeError as rag_error:
            # RAG检索失败，发送错误事件
            yield {
                "type": "error",
                "error": str(rag_error),
                "latency": time.time() - start_time
            }
            return
        
        # 2. 构建上下文字符串
        if context_used:
            context = build_context(context_docs)
            sources = _extract_sources(context_docs)
        else:
            context = ""
            sources = []
        
        # 3. 构建提示词
        if history:
            formatted_history = _format_history(history)
            prompt = get_education_prompt_with_context(question, context, formatted_history)
        else:
            prompt = get_education_prompt_with_context(question, context)
        
        # 4. 获取LLM实例
        llm = get_llm(temperature=temperature, max_tokens=max_tokens)
        
        # 获取模型信息
        llm_info = get_llm_provider_info()
        model_name = llm_info.get("model", "unknown")
        
        # 5. 发送开始信号
        yield {
            "type": "start",
            "sources": sources,
            "model": model_name,
            "confidence": "high" if context_used else "medium",
            "context_used": context_used,
            "subject_id": subject_id
        }
        
        # 6. 流式调用LLM
        full_answer = ""
        for chunk in _call_llm_stream(llm, prompt):
            full_answer += chunk
            yield {
                "type": "chunk",
                "content": chunk
            }
        
        # 7. 发送结束信号
        yield {
            "type": "end",
            "full_answer": full_answer,
            "latency": time.time() - start_time
        }
        
    except Exception as e:
        yield {
            "type": "error",
            "error": f"处理问题时发生错误: {str(e)}",
            "latency": time.time() - start_time
        }


def _retrieve_context(
    question: str,
    subject_id: Optional[str] = None,
    top_k: int = DEFAULT_TOP_K
) -> List:
    """
    获取RAG上下文
    
    Args:
        question: 用户问题
        subject_id: 学科ID（可选）
        top_k: 返回文档数量
        
    Returns:
        相关文档列表
        
    Raises:
        RuntimeError: RAG检索失败时抛出（不再静默返回空列表）
    """
    try:
        # 调用rag_service的retrieve_context函数
        context_docs = retrieve_context(
            question=question,
            subject_id=subject_id,
            top_k=top_k
        )
        return context_docs
    except Exception as e:
        # 记录错误日志并抛出异常
        error_msg = f"RAG检索失败: {str(e)}"
        print(f"[ERROR] {error_msg}")  # 保留print用于控制台输出
        raise RuntimeError(error_msg)


def _extract_sources(context_docs: List) -> List[Dict[str, Any]]:
    """
    从上下文文档中提取来源信息
    
    Args:
        context_docs: 上下文文档列表
        
    Returns:
        来源信息列表，每个来源包含：
        - filename: 文件名
        - content: 内容片段
    """
    sources = []
    
    for i, doc in enumerate(context_docs):
        # 从metadata中提取文件名
        metadata = doc.metadata if hasattr(doc, 'metadata') else {}
        filename = metadata.get('source', f'文档_{i+1}')
        
        # 提取内容片段（前200字符）
        content_preview = doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content
        
        source_info = {
            "filename": filename,
            "content": content_preview,
        }
        sources.append(source_info)
    
    return sources


def _call_llm(llm: Any, prompt: str) -> str:
    """
    调用LLM（非流式）
    
    Args:
        llm: LLM实例
        prompt: 提示词
        
    Returns:
        LLM生成的回答内容
    """
    try:
        # 尝试使用LangChain的invoke方法
        from langchain_core.messages import HumanMessage, SystemMessage
        
        messages = [
            SystemMessage(content="你是一名智慧教育助手。"),
            HumanMessage(content=prompt)
        ]
        
        response = llm.invoke(messages)
        
        # 提取回答内容
        if hasattr(response, 'content'):
            return response.content
        else:
            return str(response)
            
    except ImportError:
        # 如果LangChain不可用，尝试直接调用
        if hasattr(llm, 'invoke'):
            response = llm.invoke(prompt)
            return str(response)
        elif hasattr(llm, 'generate'):
            response = llm.generate([prompt])
            if hasattr(response, 'generations') and response.generations:
                return response.generations[0][0].text
            else:
                return str(response)
        else:
            raise RuntimeError("LLM实例不支持标准调用方法")


def _call_llm_stream(llm: Any, prompt: str) -> Generator[str, None, None]:
    """
    流式调用LLM
    
    Args:
        llm: LLM实例
        prompt: 提示词
        
    Yields:
        文本片段
    """
    try:
        # 尝试使用LangChain的stream方法
        from langchain_core.messages import HumanMessage, SystemMessage
        
        messages = [
            SystemMessage(content="你是一名智慧教育助手。"),
            HumanMessage(content=prompt)
        ]
        
        # 检查是否支持流式输出
        if hasattr(llm, 'stream'):
            for chunk in llm.stream(messages):
                if hasattr(chunk, 'content'):
                    yield chunk.content
                else:
                    yield str(chunk)
        else:
            # 如果不支持流式，使用普通调用并模拟流式
            response = llm.invoke(messages)
            content = response.content if hasattr(response, 'content') else str(response)
            # 模拟流式输出，每次发送几个字符
            chunk_size = 10
            for i in range(0, len(content), chunk_size):
                yield content[i:i+chunk_size]
                
    except ImportError:
        # 如果LangChain不可用，尝试直接调用
        if hasattr(llm, 'stream'):
            for chunk in llm.stream(prompt):
                yield str(chunk)
        else:
            # 模拟流式输出
            if hasattr(llm, 'invoke'):
                response = llm.invoke(prompt)
                content = str(response)
            elif hasattr(llm, 'generate'):
                response = llm.generate([prompt])
                if hasattr(response, 'generations') and response.generations:
                    content = response.generations[0][0].text
                else:
                    content = str(response)
            else:
                raise RuntimeError("LLM实例不支持标准调用方法")
            
            # 模拟流式输出
            chunk_size = 10
            for i in range(0, len(content), chunk_size):
                yield content[i:i+chunk_size]


def _format_history(
    history: List[Dict[str, str]],
    max_turns: int = 5
) -> str:
    """
    格式化对话历史
    
    Args:
        history: 对话历史列表
        max_turns: 最大保留轮数
        
    Returns:
        格式化的历史字符串
    """
    if not history:
        return ""
    
    # 限制历史轮数
    recent_history = history[-max_turns * 2:]  # 每轮包含user和assistant
    
    formatted_parts = []
    for i, msg in enumerate(recent_history):
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
    检查聊天服务模块的状态
    
    Returns:
        状态信息字典
    """
    status = {
        "rag_available": RAG_AVAILABLE,
        "llm_available": LLM_AVAILABLE,
        "prompts_available": PROMPTS_AVAILABLE,
        "default_top_k": DEFAULT_TOP_K
    }
    
    if not RAG_AVAILABLE:
        status["rag_import_error"] = IMPORT_ERROR
    
    if not LLM_AVAILABLE:
        status["llm_import_error"] = LLM_IMPORT_ERROR
    
    if not PROMPTS_AVAILABLE:
        status["prompts_import_error"] = PROMPTS_IMPORT_ERROR
    
    # 检查LLM提供商状态
    if LLM_AVAILABLE:
        try:
            llm_status = check_llm_availability()
            status["llm_provider"] = llm_status
        except Exception as e:
            status["llm_provider_error"] = str(e)
    
    # 检查环境变量
    api_key = os.getenv("DEEPSEEK_API_KEY")
    status["deepseek_api_key_configured"] = bool(api_key)
    
    return status


# 便捷函数：快速问答
def quick_chat(question: str, subject_id: Optional[str] = None) -> str:
    """
    快速问答函数（简化接口）
    
    Args:
        question: 用户问题
        subject_id: 学科ID（可选）
        
    Returns:
        AI回答内容（字符串）
        
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
    "quick_chat"
]