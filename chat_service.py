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
# 导入用户画像模块（V2.6 Agent自我学习）
try:
    from services.user_profiler import record_interaction, update_knowledge_level
    from services.adaptive_retriever import get_personalized_system_prompt
    from services.learning_loop import on_note_generated, on_quiz_answered, on_chat_completed
    AGENT_AVAILABLE = True
except ImportError:
    AGENT_AVAILABLE = False

from utils.logger import logger

# 默认配置
_DEFAULT_TOP_K = 5
_DEFAULT_SYSTEM_MESSAGE = "你是一名智慧教育助手。"

# 结构化学习模式（V2.5.1）：对应 message_type
# smart_note/quiz 依赖 RAG + 知识库就绪守卫；summary/explanation/advice 纯 LLM
STRUCTURED_MODES = ("smart_note", "summary", "explanation", "advice", "quiz")

# 用户要求详细解释时的关键词（V2.5.3）
_DETAIL_KEYWORDS = (
    "详细讲讲", "详细解释", "详细推导", "展开解释", "展开讲讲",
    "深入分析", "深入讲解", "完整介绍", "全面分析", "一步一步",
    "详细回答", "详细说说", "请详细", "帮我详细",
)

# 详细程度（V2.5.3）
DETAIL_CONCISE = "concise"
DETAIL_EXPANDED = "expanded"


def detect_detail_level(question: str) -> str:
    """
    根据用户语言自动判断详细程度（V2.5.3）

    用户包含"详细/展开/深入/完整/一步一步"等词 → expanded
    普通情况 → concise

    Args:
        question: 用户问题

    Returns:
        DETAIL_CONCISE 或 DETAIL_EXPANDED
    """
    if not question:
        return DETAIL_CONCISE
    q = question.lower()
    for kw in _DETAIL_KEYWORDS:
        if kw in q:
            return DETAIL_EXPANDED
    return DETAIL_CONCISE


# ============ 非流式聊天 ============

def chat_with_ai(
    question: str,
    subject_id: Optional[str] = None,
    model: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    image_data: Optional[str] = None,
    image_mime_type: Optional[str] = None,
    conversation_id: Optional[str] = None,
    mode: Optional[str] = None,
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
        conversation_id: 会话ID（V2.5.1，服务端持久化上下文）
        mode: 学习模式（V2.5.1，结构化模式）

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

    # 结构化模式（V2.5.1）：调用对应 Service，持久化结构化消息
    if mode and mode in STRUCTURED_MODES:
        try:
            result = _dispatch_structured(mode, question, subject_id, model)
            message_type = result["message_type"]
            payload = result["payload"]
            content = result["content"]
            model_name = result.get("model") or model or "mimo"

            if conversation_id:
                try:
                    from services.conversation_service import (
                        add_message, update_conversation_title, touch_conversation,
                    )
                    add_message(conversation_id, "user", question,
                                message_type=mode, model=model)
                    update_conversation_title(conversation_id, _make_title(question))
                    add_message(conversation_id, "assistant", content,
                                message_type=message_type, payload=payload,
                                model=model_name, latency=time.time() - start_time)
                    touch_conversation(conversation_id, model=model_name)
                except Exception as e:
                    logger.error(f"写入结构化消息失败: {e}", exc_info=True)


            # V2.6: 学习闭环回调
            if AGENT_AVAILABLE:
                try:
                    if mode == "smart_note":
                        on_note_generated("default_user", subject_id or "", question, payload)
                    elif mode == "quiz":
                        from services.user_profiler import record_interaction
                        record_interaction("default_user", "quiz", subject_id, question, f"生成{len(payload.get('quiz', []))}道题", model=model_name)
                except Exception as e:
                    logger.warning(f"学习闭环回调失败（不影响主流程）: {e}")

            response.update({
                "success": True,
                "answer": content,
                "model": model_name,
                "latency": time.time() - start_time,
                "message_type": message_type,
                "payload": payload,
            })
            return response
        except Exception as e:
            from services.model_service import ModelAPIError
            from services.note_service import NoteServiceError
            from services.kb_guard import KBError
            from services.education_service import EducationServiceError
            if isinstance(e, KBError):
                response["error"] = e.message
            elif isinstance(e, NoteServiceError):
                response["error"] = e.message
            elif isinstance(e, EducationServiceError):
                response["error"] = str(e)
            elif isinstance(e, ModelAPIError):
                response["error"] = str(e)
            else:
                logger.error(f"结构化模式异常: {e}", exc_info=True)
                response["error"] = "生成失败，请稍后重试"
            response["latency"] = time.time() - start_time
            return response

    try:
        # 会话上下文（服务端权威）：持久化用户消息
        if conversation_id:
            try:
                from services.conversation_service import (
                    get_conversation, add_message, get_recent_history,
                    update_conversation_title, touch_conversation,
                )
                conv = get_conversation(conversation_id)
                if not conv:
                    response["error"] = "会话不存在，请刷新后重试"
                    response["latency"] = time.time() - start_time
                    return response
                if conv["subject_id"] != subject_id:
                    response["error"] = "会话学科与当前学科不一致，请切换会话"
                    response["latency"] = time.time() - start_time
                    return response
                add_message(conversation_id, "user", question, model=model)
                update_conversation_title(conversation_id, _make_title(question))
                touch_conversation(conversation_id, model=model)
                # 服务端读取最近历史（优先 DB，不信任前端 history）
                db_history = get_recent_history(conversation_id, max_turns=10)
                history = db_history if db_history else (history or [])
            except Exception as e:
                logger.error(f"会话上下文处理失败: {e}", exc_info=True)

        # 1. RAG检索（通过ai_service公共接口）
        try:
            context_docs = get_rag_context(question, subject_id, _DEFAULT_TOP_K)
            context_used = len(context_docs) > 0
        except AIServiceError as e:
            response["error"] = str(e)
            response["latency"] = time.time() - start_time
            return response

        # 2. 构建上下文 + 提取来源（V2.5.3：context 字符预算）
        if context_used:
            try:
                context = build_context(context_docs, max_chars=3000)
            except Exception as e:
                logger.warning(f"构建上下文失败: {e}")
                context = ""
        else:
            context = ""
        response["sources"] = extract_sources(context_docs) if context_used else []

        # 3. 详细程度判断 + 构造Prompt（V2.5.3：默认简洁，仅明确要求时展开）
        detail_level = detect_detail_level(question)
        system_message = (
            _DEFAULT_SYSTEM_MESSAGE
            if detail_level == DETAIL_CONCISE
            else _get_expanded_system_message()
        )
        if history:
            formatted_history = _format_history(history)
            prompt = get_education_prompt_with_context(question, context, formatted_history)
        else:
            prompt = get_education_prompt_with_context(question, context)

        # 4. 调用LLM（V2.5.3：按详细程度控制输出预算）
        if max_tokens is None:
            max_tokens = 1600 if detail_level == DETAIL_EXPANDED else 700
        answer = call_llm(
            prompt=prompt,
            system_message=system_message,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        response["detail_level"] = detail_level

        # 5. 获取模型信息
        from services.model_service import get_model_config
        model_config = get_model_config(model)
        response["model"] = model_config.get("name", model or "mimo")

        # 6. 持久化 assistant 消息（会话存在时）
        if conversation_id:
            try:
                from services.conversation_service import add_message, touch_conversation
                model_name = response["model"]
                add_message(
                    conversation_id, "assistant", answer,
                    message_type="chat", sources=response.get("sources", []),
                    model=model_name, latency=time.time() - start_time,
                )
                touch_conversation(conversation_id, model=model_name)
            except Exception as e:
                logger.error(f"写入assistant消息失败: {e}", exc_info=True)

        # 7. 返回结果
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
    model: Optional[str] = None,
    image_data: Optional[str] = None,
    image_mime_type: Optional[str] = None,
    conversation_id: Optional[str] = None,
    mode: Optional[str] = None,
) -> Generator[Dict[str, Any], None, None]:
    """
    智能问答流式接口（SSE，真流式）

    V2.5.1 新增：
    - conversation_id: 会话ID。服务端持久化上下文（写 user message → 读 DB 历史 →
      RAG → LLM → 完成后写 assistant message），不再完全信任前端传入 history。
    - mode: 学习模式 chat|smart_note|summary|explanation|advice|quiz。
      结构化模式调用对应 Service 生成结构化结果并持久化（message_type + payload）。

    Args:
        question: 用户问题
        subject_id: 学科ID
        history: 对话历史（前端传入；有 conversation_id 时以服务端 DB 历史为准）
        temperature: 温度参数
        max_tokens: 最大token数
        model: 模型名称（可选，默认使用配置中的默认模型）
        image_data: 图片数据（base64编码，可选）
        image_mime_type: 图片MIME类型（可选）
        conversation_id: 会话ID（可选）
        mode: 学习模式（可选，默认 chat）

    Yields:
        流式事件字典：type=start|delta|done|error
        - start: {type, model, subject_id, context_used}
        - delta: {type, content} 真模型流逐块输出
        - done:  {type, sources, model, elapsed, ttft, full_answer,
                  message_type?, payload?}  结构化模式携带 message_type + payload
        - error: {type, message}
    """
    start_time = time.time()
    first_token_time = None
    mode = mode or "chat"

    # 依赖检查
    if not LLM_AVAILABLE:
        yield {"type": "error", "message": f"LLM模块不可用: {_LLM_IMPORT_ERROR}"}
        return
    if not RAG_AVAILABLE:
        yield {"type": "error", "message": f"RAG模块不可用: {_RAG_IMPORT_ERROR}"}
        return
    if not PROMPTS_AVAILABLE:
        yield {"type": "error", "message": f"提示词模块不可用: {_PROMPTS_IMPORT_ERROR}"}
        return

    # 输入验证
    if not question or not question.strip():
        yield {"type": "error", "message": "问题不能为空"}
        return

    # 会话上下文（服务端权威）
    conv_meta = None
    if conversation_id:
        try:
            from services.conversation_service import (
                get_conversation, add_message, get_recent_history,
                update_conversation_title, touch_conversation,
            )
            conv_meta = get_conversation(conversation_id)
            if not conv_meta:
                yield {"type": "error", "message": "会话不存在，请刷新后重试",
                       "error_type": "invalid_request"}
                return
            if conv_meta["subject_id"] != subject_id:
                yield {"type": "error", "message": "会话学科与当前学科不一致，请切换会话",
                       "error_type": "invalid_request"}
                return
        except Exception as e:
            logger.error(f"加载会话失败: {e}", exc_info=True)
            yield {"type": "error", "message": "会话服务不可用", "error_type": "database_error"}
            return

    try:
        # 结构化学习模式：调用对应 Service，持久化结构化消息
        if mode in STRUCTURED_MODES:
            yield from _handle_structured_mode(
                mode=mode,
                question=question,
                subject_id=subject_id,
                model=model,
                conversation_id=conversation_id,
                start_time=start_time,
            )
            return

        # ===== 普通聊天模式（chat）=====

        # 1. 持久化用户消息（会话存在时）
        if conv_meta:
            try:
                add_message(conversation_id, "user", question, model=model)
                update_conversation_title(conversation_id, _make_title(question))
                touch_conversation(conversation_id, model=model)
            except Exception as e:
                logger.error(f"写入用户消息失败: {e}", exc_info=True)

        # 2. 服务端读取最近历史（优先 DB；无会话时回退前端 history）
        if conv_meta:
            try:
                db_history = get_recent_history(conversation_id, max_turns=10)
                history = db_history if db_history else (history or [])
            except Exception as e:
                logger.warning(f"读取会话历史失败: {e}")

        # 3. RAG检索
        try:
            context_docs = get_rag_context(question, subject_id, _DEFAULT_TOP_K)
            context_used = len(context_docs) > 0
        except AIServiceError as e:
            yield {"type": "error", "message": str(e)}
            return

        # 4. 构建上下文 + 来源（V2.5.3：context 字符预算，保留最相关证据）
        if context_used:
            try:
                context = build_context(context_docs, max_chars=3000)
            except Exception as e:
                logger.warning(f"构建上下文失败: {e}")
                context = ""
        else:
            context = ""
        sources = extract_sources(context_docs) if context_used else []

        # 5. 详细程度判断 + 构造Prompt（V2.5.3：默认简洁，仅明确要求时展开）
        detail_level = detect_detail_level(question)
        system_message = (
            _DEFAULT_SYSTEM_MESSAGE
            if detail_level == DETAIL_CONCISE
            else _get_expanded_system_message()
        )
        if history:
            formatted_history = _format_history(history)
            prompt = get_education_prompt_with_context(question, context, formatted_history)
        else:
            prompt = get_education_prompt_with_context(question, context)

        # 6. 获取Provider（支持请求级模型选择）与真实模型名
        from services.model_service import get_model_provider, get_model_config
        provider = get_model_provider(model)
        provider_config = provider.config
        model_name = provider_config.get("model_name", model or "mimo")

        # 7. 发送start事件
        yield {
            "type": "start",
            "model": model_name,
            "confidence": "high" if context_used else "medium",
            "context_used": context_used,
            "subject_id": subject_id,
            "detail_level": detail_level,
        }

        # 8. 真流式调用LLM（V2.5.3：按详细程度控制输出预算）
        full_answer = ""
        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt},
        ]
        # 普通问答简洁模式：约600~800 tokens；详细模式：约1600
        if max_tokens is None:
            max_tokens = 1600 if detail_level == DETAIL_EXPANDED else 700
        for chunk in _call_llm_stream(provider, messages, temperature=temperature, max_tokens=max_tokens):
            if not chunk:
                continue
            if first_token_time is None:
                first_token_time = time.time()
            full_answer += chunk
            yield {"type": "delta", "content": chunk}

        ttft = first_token_time - start_time if first_token_time else None
        elapsed = time.time() - start_time

        # 9. 持久化 assistant 消息（会话存在时）
        if conv_meta:
            try:
                add_message(
                    conversation_id, "assistant", full_answer,
                    message_type="chat", sources=sources,
                    model=model_name, latency=elapsed,
                )
                touch_conversation(conversation_id, model=model_name)
            except Exception as e:
                logger.error(f"写入assistant消息失败: {e}", exc_info=True)

        # 10. 发送done事件（一次性携带来源与统计）
        yield {
            "type": "done",
            "sources": sources,
            "model": model_name,
            "elapsed": elapsed,
            "ttft": ttft,
            "full_answer": full_answer,
        }

    except AIServiceError as e:
        yield {"type": "error", "message": str(e), "error_type": "service_error"}
    except Exception as e:
        # ModelAPIError 携带结构化错误类型，给出清晰错误而非"未知错误"
        from services.model_service import ModelAPIError
        if isinstance(e, ModelAPIError):
            logger.error(f"流式聊天模型调用失败: error_type={e.error_type} status={e.status_code}")
            yield {
                "type": "error",
                "message": str(e),
                "error_type": e.error_type or "model_error",
            }
        else:
            logger.error(f"流式聊天异常: {e}", exc_info=True)
            yield {"type": "error", "message": f"处理问题时发生错误: {str(e)}",
                   "error_type": "unknown"}


def _handle_structured_mode(
    mode: str,
    question: str,
    subject_id: Optional[str],
    model: Optional[str],
    conversation_id: Optional[str],
    start_time: float,
) -> Generator[Dict[str, Any], None, None]:
    """
    结构化学习模式统一入口（V2.5.1）

    smart_note / quiz → note_service（RAG 证据驱动 + KB 就绪守卫）
    summary / explanation / advice → education_service（纯 LLM）

    生成结果持久化为结构化消息（message_type + payload_json），
    前端据此恢复结构化 Card。
    """
    # 发送start事件
    yield {
        "type": "start",
        "model": model or "mimo",
        "confidence": "high",
        "context_used": False,
        "subject_id": subject_id,
        "mode": mode,
    }

    # 用户消息入库（结构化模式下 content 存问题，message_type 存 mode）
    if conversation_id:
        try:
            from services.conversation_service import (
                add_message, update_conversation_title, touch_conversation,
            )
            add_message(conversation_id, "user", question,
                        message_type=mode, model=model)
            update_conversation_title(conversation_id, _make_title(question))
            touch_conversation(conversation_id, model=model)
        except Exception as e:
            logger.error(f"写入结构化用户消息失败: {e}", exc_info=True)

    try:
        result = _dispatch_structured(mode, question, subject_id, model)
        message_type = result.get("message_type", mode)
        payload = result.get("payload") or {}
        content = result.get("content") or ""
        model_name = result.get("model", model or "mimo")

        # 持久化 assistant 结构化消息
        if conversation_id:
            try:
                from services.conversation_service import add_message, touch_conversation
                add_message(
                    conversation_id, "assistant", content,
                    message_type=message_type, payload=payload,
                    model=model_name, latency=time.time() - start_time,
                )
                touch_conversation(conversation_id, model=model_name)
            except Exception as e:
                logger.error(f"写入结构化assistant消息失败: {e}", exc_info=True)

        # done 事件携带 message_type + payload，前端渲染结构化 Card
        yield {
            "type": "done",
            "sources": [],
            "model": model_name,
            "elapsed": time.time() - start_time,
            "ttft": None,
            "full_answer": content,
            "message_type": message_type,
            "payload": payload,
        }

    except Exception as e:
        from services.model_service import ModelAPIError
        from services.note_service import NoteServiceError
        from services.kb_guard import KBError
        from services.education_service import EducationServiceError

        if isinstance(e, KBError):
            yield {"type": "error", "message": e.message, "error_type": e.error_type}
        elif isinstance(e, NoteServiceError):
            yield {"type": "error", "message": e.message,
                   "error_type": e.error_type or "model_error"}
        elif isinstance(e, EducationServiceError):
            yield {"type": "error", "message": str(e), "error_type": "service_error"}
        elif isinstance(e, ModelAPIError):
            yield {"type": "error", "message": str(e),
                   "error_type": e.error_type or "model_error"}
        else:
            logger.error(f"结构化模式异常: {e}", exc_info=True)
            yield {"type": "error", "message": "生成失败，请稍后重试", "error_type": "unknown"}


def _dispatch_structured(
    mode: str,
    question: str,
    subject_id: Optional[str],
    model: Optional[str],
) -> Dict[str, Any]:
    """
    分发结构化模式到对应 Service

    Returns:
        {"message_type": str, "payload": dict, "content": str, "model": str}
    """
    from services.note_service import generate_smart_note, generate_quiz
    from services.education_service import generate_summary, generate_explanation, generate_advice

    if mode == "smart_note":
        note = generate_smart_note(subject_id=subject_id, topic=question, model=model)
        return {
            "message_type": "smart_note",
            "payload": note,
            "content": note.get("title", "智能笔记"),
            "model": note.get("model") or "",
        }

    if mode == "quiz":
        quiz = generate_quiz(subject_id=subject_id, topic=question, model=model)
        return {
            "message_type": "quiz",
            "payload": quiz,
            "content": f"已生成 {len(quiz.get('quiz', []))} 道自测题",
            "model": quiz.get("model") or "",
        }

    if mode == "summary":
        res = generate_summary(text=question, model=model)
        return {
            "message_type": "summary",
            "payload": {"key_points": res.get("key_points", []),
                        "summary": res.get("summary", "")},
            "content": res.get("summary", "知识点总结"),
            "model": res.get("model") or "",
        }

    if mode == "explanation":
        res = generate_explanation(question=question, model=model)
        return {
            "message_type": "explanation",
            "payload": {"question": res.get("question", question),
                        "explanation": res.get("explanation", "")},
            "content": res.get("explanation", "题目解析"),
            "model": res.get("model") or "",
        }

    # advice
    res = generate_advice(history=[{"question": question}], model=model)
    return {
        "message_type": "advice",
        "payload": {"advice": res.get("advice", "")},
        "content": res.get("advice", "学习建议"),
        "model": res.get("model") or "",
    }


def _get_expanded_system_message() -> str:
    """获取详细模式的 system message（V2.5.3）"""
    try:
        from prompts import get_education_system_prompt_expanded
        return get_education_system_prompt_expanded()
    except Exception:
        return "你是一名智慧教育助手。用户明确要求详细解释，请提供结构化完整回答，包含概念解释、示例说明、总结要点和学习建议。"


def _make_title(question: str, max_len: int = 24) -> str:
    """从用户第一条问题生成会话标题（截取，不调用 LLM）"""
    text = (question or "").strip().replace("\n", " ")
    if not text:
        return "新对话"
    return text[:max_len] + ("…" if len(text) > max_len else "")


# ============ 私有辅助函数（chat_service独有的逻辑） ============

def _call_llm_stream(provider: Any, messages: List[Dict[str, str]], temperature=None, max_tokens=None) -> Generator[str, None, None]:
    """
    真流式调用LLM（chat_service独有，ai_service不提供流式接口）

    Args:
        provider: ModelProvider实例（OpenAICompatibleProvider，支持 chat_stream）
        messages: [{"role": "system"...}, {"role": "user"...}]
        temperature: 温度参数
        max_tokens: 最大token数

    Yields:
        文本片段（供应商流式逐块输出）

    Raises:
        异常交由调用方处理（ModelAPIError 等）
    """
    # 优先使用供应商真流式（stream=True）
    if hasattr(provider, "chat_stream"):
        for chunk in provider.chat_stream(messages, temperature=temperature, max_tokens=max_tokens):
            yield chunk
        return

    # 兜底：Provider 不支持流式时，非流式调用并按块切分（仅当无 chat_stream 方法时）
    result = provider.chat(messages, temperature=temperature, max_tokens=max_tokens)
    content = result.get("answer", "") if isinstance(result, dict) else str(result)
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
        # V2.6: 记录用户交互（Agent自我学习）
        if AGENT_AVAILABLE:
            try:
                record_interaction(
                    user_id="default_user",
                    interaction_type="chat",
                    subject_id=subject_id,
                    question=question,
                    answer=response.get("answer", ""),
                    model_used=response.get("model"),
                    latency=response.get("latency"),
                    )
            except Exception as e:
                logger.warning(f"记录交互失败（不影响主流程）: {e}")

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
