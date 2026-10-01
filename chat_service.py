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
# 阶段一说明：学习记录统一走事件层（services.learning_events）并归属真实用户，
# 不再直接调用 learning_loop 的旧回调（那些回调会按"行为"直接抬高掌握度）。
try:
    from services.user_profiler import record_interaction
    from services.adaptive_retriever import get_personalized_system_prompt
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


# ============ 默认文本模型 ============

def _default_text_model() -> str:
    """
    未指定模型时使用的默认文本模型（单一解析入口，见 config/models.get_default_model）

    2026-09-25：MiMo 会员到期，默认文本模型为 DeepSeek；此处不再硬编码任何提供方名。
    """
    from config.models import get_default_model
    return get_default_model()


def _resolved_provider(model: Optional[str] = None) -> str:
    """本次实际使用的提供方键（deepseek / mimo / qwen），与 config 解析同源"""
    from services.ai_service import resolve_model_info
    return resolve_model_info(model).get("provider") or _default_text_model()


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
    user_id: Optional[str] = None,
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
        user_id: 当前用户ID（阶段一：由后端身份机制取得，用于学习记录归属与个性化）

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
        "model": model or _default_text_model(),
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
            result = _dispatch_structured(mode, question, subject_id, model,
                                          user_id=user_id)
            message_type = result["message_type"]
            payload = result["payload"]
            content = result["content"]
            model_name = result.get("model") or model or _default_text_model()

            structured_msg_id = None
            if conversation_id:
                try:
                    from services.conversation_service import (
                        add_message, update_conversation_title, touch_conversation,
                        get_conversation,
                    )
                    # 阶段一补验：只允许写入本人会话（越权则跳过持久化，不影响本次回答）
                    if get_conversation(conversation_id, user_id=user_id):
                        add_message(conversation_id, "user", question,
                                    message_type=mode,
                                    model=model or _default_text_model())
                        update_conversation_title(conversation_id, _make_title(question),
                                                  user_id=user_id)
                        saved = add_message(conversation_id, "assistant", content,
                                            message_type=message_type, payload=payload,
                                            model=model_name, latency=time.time() - start_time)
                        structured_msg_id = saved.get("id") if isinstance(saved, dict) else None
                        touch_conversation(conversation_id, model=model_name, user_id=user_id)
                    else:
                        logger.warning(f"会话 {conversation_id} 不属于当前用户，"
                                       f"跳过结构化消息持久化")
                except Exception as e:
                    logger.error(f"写入结构化消息失败: {e}", exc_info=True)

            # 阶段一：结构化学习事件（归属真实用户；只记录行为，不伪造掌握度）
            _record_structured_interaction(
                user_id=user_id, subject_id=subject_id, mode=mode, question=question,
                payload=payload, model=model_name, conversation_id=conversation_id,
                message_id=structured_msg_id,
            )

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
                # 阶段一补验：对象级归属校验（不属于本人按"不存在"处理）
                conv = get_conversation(conversation_id, user_id=user_id)
                if not conv:
                    response["error"] = "会话不存在，请刷新后重试"
                    response["latency"] = time.time() - start_time
                    return response
                if conv["subject_id"] != subject_id:
                    response["error"] = "会话学科与当前学科不一致，请切换会话"
                    response["latency"] = time.time() - start_time
                    return response
                add_message(conversation_id, "user", question,
                            model=model or _default_text_model())
                update_conversation_title(conversation_id, _make_title(question),
                                          user_id=user_id)
                touch_conversation(conversation_id, model=model, user_id=user_id)
                # 服务端读取最近历史（优先 DB，不信任前端 history）
                db_history = get_recent_history(conversation_id, max_turns=10,
                                                user_id=user_id)
                history = db_history if db_history else (history or [])
            except Exception as e:
                logger.error(f"会话上下文处理失败: {e}", exc_info=True)

        # 1. RAG检索（通过ai_service公共接口）
        try:
            context_docs = get_rag_context(question, subject_id, _DEFAULT_TOP_K,
                                           viewer_user_id=user_id)
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

        # 3. 详细程度判断 + 构造Prompt（简洁优先；阶段二接入真实学习状态）
        detail_level = detect_detail_level(question)
        system_message, teaching_ctx = _personalized_system_message(
            user_id, subject_id, detail_level, question=question)
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
        if teaching_ctx:
            # 透出"为什么这样安排"，前端可展示，也便于验收核查
            response["teaching"] = {
                "mode": teaching_ctx.get("teaching_mode"),
                "reasons": teaching_ctx.get("reasons"),
                "signals": teaching_ctx.get("signals"),
                "applied": teaching_ctx.get("applied"),
            }

        # 5. 获取模型信息
        #    约定：model = 实际调用的模型名（如 deepseek-chat），provider = 提供方键（如 deepseek）；
        #    两者都由后端解析结果决定，保证"显示 = 实际调用"。
        from services.model_service import get_model_config, get_model_provider
        provider_key = model or _default_text_model()
        try:
            resolved_name = get_model_provider(provider_key).config.get("model_name")
        except Exception:
            resolved_name = get_model_config(provider_key).get("model_name")
        response["provider"] = provider_key
        response["provider_display"] = get_model_config(provider_key).get("name", provider_key)
        response["model"] = resolved_name or provider_key

        # 6. 持久化 assistant 消息（会话存在时）
        assistant_msg_id = None
        if conversation_id:
            try:
                from services.conversation_service import add_message, touch_conversation
                model_name = response["model"]
                saved = add_message(
                    conversation_id, "assistant", answer,
                    message_type="chat", sources=response.get("sources", []),
                    model=model_name, latency=time.time() - start_time,
                )
                assistant_msg_id = saved.get("id") if isinstance(saved, dict) else None
                touch_conversation(conversation_id, model=model_name, user_id=user_id)
            except Exception as e:
                logger.error(f"写入assistant消息失败: {e}", exc_info=True)

        # 6b. 记录学习交互（阶段一：与非流式/流式统一走事件层，幂等）
        _record_chat_interaction(
            user_id=user_id, subject_id=subject_id, question=question,
            answer=answer, model=response.get("model"), latency=time.time() - start_time,
            conversation_id=conversation_id, message_id=assistant_msg_id,
        )

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
    user_id: Optional[str] = None,
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
    # V2.5.4：耗时拆解标记（只写开发日志，不打印内容）
    rag_start = None
    rag_end = None
    provider_start = None
    total_output_chars = 0

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
            # 阶段一补验：只允许在本人会话上读写（否则本次回答不落库）
            conv_meta = get_conversation(conversation_id, user_id=user_id)
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
                user_id=user_id,
            )
            return

        # ===== 普通聊天模式（chat）=====

        # 1. 持久化用户消息（会话存在时）
        if conv_meta:
            try:
                add_message(conversation_id, "user", question,
                            model=model or _default_text_model())
                update_conversation_title(conversation_id, _make_title(question),
                                          user_id=user_id)
                touch_conversation(conversation_id, model=model, user_id=user_id)
            except Exception as e:
                logger.error(f"写入用户消息失败: {e}", exc_info=True)

        # 2. 服务端读取最近历史（优先 DB；无会话时回退前端 history）
        if conv_meta:
            try:
                db_history = get_recent_history(conversation_id, max_turns=10,
                                                user_id=user_id)
                history = db_history if db_history else (history or [])
            except Exception as e:
                logger.warning(f"读取会话历史失败: {e}")

        # 3. RAG检索（V2.5.4：记录 rag_start/rag_end）
        rag_start = time.time()
        try:
            context_docs = get_rag_context(question, subject_id, _DEFAULT_TOP_K,
                                           viewer_user_id=user_id)
            context_used = len(context_docs) > 0
        except AIServiceError as e:
            yield {"type": "error", "message": str(e)}
            return
        rag_end = time.time()

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

        # 5. 详细程度判断 + 构造Prompt（简洁优先；阶段二接入真实学习状态）
        detail_level = detect_detail_level(question)
        system_message, teaching_ctx = _personalized_system_message(
            user_id, subject_id, detail_level, question=question)
        if history:
            formatted_history = _format_history(history)
            prompt = get_education_prompt_with_context(question, context, formatted_history)
        else:
            prompt = get_education_prompt_with_context(question, context)

        # 6. 获取Provider（支持请求级模型选择）与真实模型名
        from services.model_service import get_model_provider, get_model_config
        provider = get_model_provider(model)
        provider_config = provider.config
        model_name = provider_config.get("model_name", model or _default_text_model())

        # 7. 发送start事件
        yield {
            "type": "start",
            "model": model_name,
            # 提供方键（deepseek / mimo / qwen），前端据此显示"实际使用的提供方"
            "provider": provider.model_name,
            "confidence": "high" if context_used else "medium",
            "context_used": context_used,
            "subject_id": subject_id,
            "detail_level": detail_level,
        }

        # 8. 真流式调用LLM（V2.5.3：按详细程度控制输出预算）
        provider_start = time.time()
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
            total_output_chars += len(chunk)
            yield {"type": "delta", "content": chunk}

        ttft = first_token_time - start_time if first_token_time else None
        elapsed = time.time() - start_time

        # V2.5.4：耗时拆解日志（只记耗时/模型/字符数，不打印内容）
        rag_ms = (rag_end - rag_start) * 1000 if rag_start and rag_end else None
        ttft_ms = (first_token_time - provider_start) * 1000 if first_token_time and provider_start else None
        generation_ms = (time.time() - first_token_time) * 1000 if first_token_time else None
        total_ms = elapsed * 1000
        logger.info(
            f"[ChatTiming] mode={mode} detail={detail_level} model={model_name} "
            f"rag_ms={rag_ms:.0f} ttft_ms={ttft_ms:.0f} generation_ms={generation_ms:.0f} "
            f"total_ms={total_ms:.0f} output_chars={total_output_chars}"
        )

        # 9. 持久化 assistant 消息（会话存在时）
        assistant_msg_id = None
        if conv_meta:
            try:
                saved = add_message(
                    conversation_id, "assistant", full_answer,
                    message_type="chat", sources=sources,
                    model=model_name, latency=elapsed,
                )
                assistant_msg_id = saved.get("id") if isinstance(saved, dict) else None
                touch_conversation(conversation_id, model=model_name, user_id=user_id)
            except Exception as e:
                logger.error(f"写入assistant消息失败: {e}", exc_info=True)

        # 9b. 记录学习交互（阶段一：流式路径此前漏记，现与非流式统一）
        _record_chat_interaction(
            user_id=user_id, subject_id=subject_id, question=question,
            answer=full_answer, model=model_name, latency=elapsed,
            conversation_id=conversation_id, message_id=assistant_msg_id,
        )

        # 10. 发送done事件（一次性携带来源与统计）
        yield {
            "type": "done",
            "sources": sources,
            "model": model_name,
            "provider": provider.model_name,
            "elapsed": elapsed,
            "ttft": ttft,
            "full_answer": full_answer,
            # 教学策略与依据（前端可展示"为什么这样讲"）
            "teaching": ({
                "mode": teaching_ctx.get("teaching_mode"),
                "reasons": teaching_ctx.get("reasons"),
                "signals": teaching_ctx.get("signals"),
                "applied": teaching_ctx.get("applied"),
            } if teaching_ctx else None),
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
    user_id: Optional[str] = None,
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
        "model": model or _default_text_model(),
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
                        message_type=mode, model=model or _default_text_model())
            update_conversation_title(conversation_id, _make_title(question),
                                      user_id=user_id)
            touch_conversation(conversation_id, model=model, user_id=user_id)
        except Exception as e:
            logger.error(f"写入结构化用户消息失败: {e}", exc_info=True)

    try:
        result = _dispatch_structured(mode, question, subject_id, model,
                                      user_id=user_id)
        message_type = result.get("message_type", mode)
        payload = result.get("payload") or {}
        content = result.get("content") or ""
        model_name = result.get("model") or model or _default_text_model()

        # 持久化 assistant 结构化消息
        structured_msg_id = None
        if conversation_id:
            try:
                from services.conversation_service import add_message, touch_conversation
                saved = add_message(
                    conversation_id, "assistant", content,
                    message_type=message_type, payload=payload,
                    model=model_name, latency=time.time() - start_time,
                )
                structured_msg_id = saved.get("id") if isinstance(saved, dict) else None
                touch_conversation(conversation_id, model=model_name, user_id=user_id)
            except Exception as e:
                logger.error(f"写入结构化assistant消息失败: {e}", exc_info=True)

        # 阶段一：结构化学习事件（流式路径此前完全漏记）
        _record_structured_interaction(
            user_id=user_id, subject_id=subject_id, mode=mode, question=question,
            payload=payload, model=model_name, conversation_id=conversation_id,
            message_id=structured_msg_id,
        )

        # done 事件携带 message_type + payload，前端渲染结构化 Card
        yield {
            "type": "done",
            "sources": [],
            "model": model_name,
            # 结构化模式的实际提供方（未指定时即后端默认文本模型，当前 DeepSeek）
            "provider": _resolved_provider(model),
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
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    分发结构化模式到对应 Service

    Returns:
        {"message_type": str, "payload": dict, "content": str, "model": str}
    """
    from services.note_service import generate_smart_note, generate_quiz
    from services.education_service import generate_summary, generate_explanation, generate_advice

    if mode == "smart_note":
        note = generate_smart_note(subject_id=subject_id, topic=question, model=model,
                                   user_id=user_id)
        return {
            "message_type": "smart_note",
            "payload": note,
            "content": note.get("title", "智能笔记"),
            "model": note.get("model") or "",
        }

    if mode == "quiz":
        quiz = generate_quiz(subject_id=subject_id, topic=question, model=model,
                             user_id=user_id)
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


def _record_structured_interaction(
    user_id: Optional[str],
    subject_id: Optional[str],
    mode: Optional[str],
    question: str,
    payload: Any,
    model: Optional[str] = None,
    conversation_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> None:
    """
    记录结构化模式（智能笔记 / 自测题生成）的学习事件（阶段一）。

    - 归属真实用户（由后端身份机制传入），不再写死 default_user
    - 只记录"生成过"这一行为；掌握度仍只由真实作答证据决定
    - 经事件层幂等（同一消息重复触发不会重复记录）
    """
    if not user_id:
        return
    event_type = {"smart_note": "note_generated",
                  "quiz": "quiz_question_generated"}.get(mode or "")
    if not event_type:
        return
    data = payload if isinstance(payload, dict) else {}
    key = (f"{event_type}:{message_id}" if message_id
           else f"{event_type}:{conversation_id or '-'}:{abs(hash(question))}")
    try:
        from services.learning_events import emit_event
        emit_event(
            event_type=event_type,
            idempotency_key=key,
            user_id=user_id,
            subject_id=subject_id,
            payload={
                "topic": question,
                "title": data.get("title"),
                "model": model,
                "quiz_count": len(data.get("quiz") or []),
            },
        )
    except Exception as e:
        logger.warning(f"记录结构化学习事件失败（不影响生成结果）: {e}")


def _record_chat_interaction(
    user_id: Optional[str],
    subject_id: Optional[str],
    question: str,
    answer: str,
    model: Optional[str],
    latency: Optional[float],
    conversation_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> None:
    """
    记录问答学习交互（阶段一：流式/非流式统一入口）。

    - 归属真实用户（由后端身份机制传入）
    - 通过事件层幂等；失败不影响回答
    - 回写 interaction_id 到会话消息，供反馈按钮正确关联
    """
    if not user_id:
        return
    key = f"chat:{message_id}" if message_id else f"chat:{conversation_id}:{abs(hash(question))}"
    try:
        from services.learning_events import emit_event
        emit_event(
            event_type="chat_interaction",
            idempotency_key=key,
            user_id=user_id,
            subject_id=subject_id,
            payload={
                "question": question,
                "answer": answer,
                "model": model,
                "latency": latency,
                "conversation_id": conversation_id,
                "message_id": message_id,
            },
        )
    except Exception as e:
        logger.warning(f"记录问答交互失败（不影响回答）: {e}")


def _personalized_system_message(user_id: Optional[str], subject_id: Optional[str],
                                 detail_level: str, question: str = "") -> tuple:
    """
    构造 system message：默认简洁；用户要求详细时展开；并按**真实学习状态**调整教学策略。

    画像来源见 services/teaching_context.build_teaching_context：
    - 学生明确设置的表达偏好（优先级最高）
    - 与当前问题相关的知识点作答证据（只取相关的一个知识点，避免堆历史）
    - 最近错误（不含答案）
    - 当前任务/目标/剩余时间
    - 生效中的学习记忆（可被学生更正/关闭）

    画像不足时返回基础提示词，不虚构学生水平。
    返回 (system_message, teaching_context)，便于接口层把"为什么这样安排"透出给前端。
    """
    base = (_DEFAULT_SYSTEM_MESSAGE if detail_level == DETAIL_CONCISE
            else _get_expanded_system_message())
    if not user_id or not subject_id:
        return base, None
    try:
        from services.teaching_context import build_teaching_context
        ctx = build_teaching_context(user_id, subject_id, question=question,
                                    detail_level=detail_level)
        block = ctx.get("prompt_block") or ""
        if not block:
            return base, ctx
        return f"{base}\n\n{block}", ctx
    except Exception as e:
        logger.warning(f"教学上下文构建失败，使用默认提示词: {e}")
        return base, None


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
def quick_chat(question: str, subject_id: Optional[str] = None,
               user_id: Optional[str] = None) -> str:
    """
    快速问答（简化接口，供脚本/调试使用）

    Args:
        question: 用户问题
        subject_id: 学科ID（可选）
        user_id: 用户ID（可选；无用户上下文时不写学习记录，避免误记到他人名下）

    Returns:
        AI回答字符串

    Raises:
        RuntimeError: 问答失败
    """
    response = chat_with_ai(question, subject_id, user_id=user_id)
    if response["success"]:
        # V2.6: 记录用户交互（Agent自我学习）——必须有真实用户归属
        if AGENT_AVAILABLE and user_id:
            try:
                record_interaction(
                    user_id=user_id,
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
