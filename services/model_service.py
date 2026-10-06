"""
模型服务模块
提供统一的AI模型调用接口，支持多个模型提供商
V2.3.1 Phase 2.4.4: 添加重试机制、可配置超时
"""
import time
import random
import requests
import logging
from typing import Optional, List, Dict, Any
from config.models import get_model_config, get_model_api_key, get_default_model

logger = logging.getLogger(__name__)

# Vision API 重试配置
VISION_MAX_RETRIES = 3
VISION_DEFAULT_TIMEOUT = 60  # 秒
VISION_RETRY_BACKOFF_BASE = 1.0  # 指数退避基数（秒）
VISION_RETRY_BACKOFF_MAX = 10.0  # 最大退避时间（秒）
VISION_RETRY_JITTER = 0.5  # 随机抖动范围（秒）


def _is_retryable_error(exc: Exception) -> bool:
    """
    判断错误是否可重试

    可重试的错误：
    - requests.exceptions.Timeout（超时）
    - requests.exceptions.ConnectionError（网络连接错误）
    - HTTP 429（请求过多）
    - HTTP 5xx（服务端错误）

    不可重试的错误：
    - HTTP 400（请求参数错误）
    - HTTP 401（认证失败）
    - HTTP 403（权限不足）
    - ValueError（参数错误、模型不支持等）
    """
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        status_code = exc.response.status_code if exc.response is not None else 0
        # 429 和 5xx 可重试
        if status_code == 429 or status_code >= 500:
            return True
        # 400, 401, 403 不可重试
        return False
    # 业务错误不可重试
    if isinstance(exc, (ValueError, ModelAPIError)):
        return False
    return False


def _classify_http_error(status_code: int) -> str:
    """
    将HTTP状态码映射为精确的错误类型（用户要求的标准分类）

    - invalid_api_key / unauthorized / forbidden / invalid_request / model_not_found
    - quota_exceeded / rate_limited / server_error / timeout / connection_error
    """
    if status_code == 400:
        return "invalid_request"
    if status_code == 401:
        return "invalid_api_key"
    if status_code == 403:
        return "forbidden"
    if status_code == 404:
        return "model_not_found"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "server_error"
    return f"http_error_{status_code}"


def _get_error_type(exc: Exception) -> str:
    """获取简化的错误类型（用于metadata和日志，不包含敏感信息）"""
    # 优先读取结构化错误
    if isinstance(exc, ModelAPIError):
        return exc.error_type or "unknown_error"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    if isinstance(exc, requests.exceptions.HTTPError):
        status_code = exc.response.status_code if exc.response is not None else 0
        return _classify_http_error(status_code)
    return "unknown_error"


class ModelAPIError(Exception):
    """
    模型API调用结构化异常

    保留HTTP状态码与精确错误类型，避免中间层通过字符串猜测丢失信息。
    """

    def __init__(self, message: str, status_code: int = None, error_type: str = "unknown"):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type

    def __str__(self):
        return self.args[0]

class ModelProvider:
    """模型提供商基类"""
    
    def __init__(self, model_name: str = None):
        self.model_name = model_name or get_default_model()
        self.config = get_model_config(self.model_name)
        self.api_key = get_model_api_key(self.model_name)
        
        if not self.api_key:
            logger.warning(f"模型 {self.model_name} 未配置API密钥")
    
    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """
        调用模型进行对话
        
        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            **kwargs: 其他参数
        
        Returns:
            响应字典，包含 answer, model, latency 等字段
        """
        raise NotImplementedError
    
    def chat_with_tools(self, messages: List[Dict[str, Any]],
                        tools: Optional[List[Dict[str, Any]]] = None,
                        **kwargs) -> Dict[str, Any]:
        """
        带原生工具调用的对话（统一 Agent 运行机制的基础能力）。

        返回结构在 chat() 基础上额外包含 tool_calls / raw_message / finish_reason。
        是否调用工具由模型决定（程序不强制 tool_choice），程序只负责校验与执行。
        """
        raise NotImplementedError(f"模型 {self.model_name} 不支持工具调用")

    def stream_with_tools(self, messages: List[Dict[str, Any]],
                          tools: Optional[List[Dict[str, Any]]] = None, **kwargs):
        """流式 + 工具调用（默认不支持，由具体 Provider 覆盖）。"""
        raise NotImplementedError(f"模型 {self.model_name} 不支持流式工具调用")

    def vision(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        """
        调用模型进行视觉理解（多模态）
        
        Args:
            messages: 多模态消息列表，格式为 [{"role": "user", "content": [{"type": "text", ...}, {"type": "image_url", ...}]}]
            **kwargs: 其他参数
        
        Returns:
            响应字典，包含 answer, model, latency 等字段
        
        Raises:
            NotImplementedError: 如果模型不支持视觉功能
        """
        raise NotImplementedError(f"模型 {self.model_name} 不支持视觉功能")
    
    def _call_openai_compatible_api(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """
        调用OpenAI兼容的API（带重试机制）
        
        Args:
            messages: 消息列表
            **kwargs: 其他参数，支持:
                - timeout: 请求超时时间（秒），默认使用VISION_DEFAULT_TIMEOUT
                - max_retries: 最大重试次数，默认VISION_MAX_RETRIES
                - temperature: 温度参数
                - max_tokens: 最大token数
        
        Returns:
            API响应，包含 answer, model, model_display_name, latency, usage, retries
        """
        if not self.api_key:
            raise ValueError(f"模型 {self.model_name} 未配置API密钥")
        
        timeout = kwargs.get("timeout", VISION_DEFAULT_TIMEOUT)
        max_retries = kwargs.get("max_retries", VISION_MAX_RETRIES)
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.config["model_name"],
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 2048),
        }
        # 原生 tool-calling 支持（2026-10-07 统一 Agent）：调用方把 tools / tool_choice /
        # response_format 等 OpenAI 兼容字段放进 extra_payload，直接并入请求体。
        # 放在这里而不是新写一个 HTTP 客户端，是为了复用既有的超时、重试、错误分类。
        _extra = kwargs.get("extra_payload")
        if _extra:
            payload.update(_extra)

        url = f"{self.config['base_url']}/chat/completions"
        
        last_error = None
        total_latency = 0.0
        
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                # 手动UTF-8编码请求体，避免requests在latin-1下编码中文报错
                import json as _json
                _body = _json.dumps(payload, ensure_ascii=False).encode('utf-8')
                response = requests.post(url, headers=headers, data=_body, timeout=timeout)
                latency = time.time() - start_time
                total_latency += latency
                
                response.raise_for_status()
                data = response.json()
                
                # 解析响应
                if "choices" in data and len(data["choices"]) > 0:
                    _choice = data["choices"][0]
                    _msg = _choice.get("message") or {}
                    # content 在纯 tool_calls 轮里是 None，不能当成错误
                    answer = _msg.get("content") or ""
                    raw_message = _msg
                    finish_reason = _choice.get("finish_reason")
                else:
                    raise ValueError(f"模型 {self.model_name} 返回格式异常")
                
                if attempt > 0:
                    logger.info(
                        f"[Vision] API调用成功 (第{attempt + 1}次尝试), "
                        f"model={self.model_name}, latency={latency:.2f}s, "
                        f"total_latency={total_latency:.2f}s"
                    )
                
                return {
                    "answer": answer,
                    "model": self.model_name,
                    "model_display_name": self.config["name"],
                    "latency": latency,
                    "usage": data.get("usage", {}),
                    "retries": attempt,  # 记录重试次数
                    # 原生 tool-calling：把原始 message 与结束原因透出，调用方据此决定是否继续
                    "raw_message": raw_message,
                    "tool_calls": raw_message.get("tool_calls") or [],
                    "finish_reason": finish_reason,
                }
                
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                    requests.exceptions.HTTPError) as e:
                last_error = e
                error_type = _get_error_type(e)
                status_code = getattr(getattr(e, "response", None), "status_code", None) \
                    if isinstance(e, requests.exceptions.HTTPError) else None

                # 检查是否可重试
                if not _is_retryable_error(e):
                    logger.error(
                        f"[Vision] API调用遇到不可重试错误: {error_type} "
                        f"(http={status_code}), model={self.model_name}, "
                        f"attempt={attempt + 1}/{max_retries}"
                    )
                    # 结构化抛出，保留HTTP状态码与错误类型（不包含供应商响应正文）
                    raise ModelAPIError(
                        f"模型API调用失败: {error_type}", status_code=status_code, error_type=error_type)

                # 最后一次尝试失败，不再等待
                if attempt < max_retries - 1:
                    # 指数退避 + 随机抖动
                    backoff = min(
                        VISION_RETRY_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, VISION_RETRY_JITTER),
                        VISION_RETRY_BACKOFF_MAX
                    )
                    logger.warning(
                        f"[Vision] API调用失败: {error_type} (http={status_code}), "
                        f"model={self.model_name}, attempt={attempt + 1}/{max_retries}, "
                        f"等待{backoff:.1f}s后重试"
                    )
                    time.sleep(backoff)
                else:
                    logger.error(
                        f"[Vision] API调用最终失败: {error_type} (http={status_code}), "
                        f"model={self.model_name}, 已用尽{max_retries}次尝试, "
                        f"total_latency={total_latency:.2f}s"
                    )

            except ValueError as e:
                # 业务错误（返回格式异常等）不重试
                logger.error(f"[Vision] API响应解析失败: {str(e)}, model={self.model_name}")
                raise ModelAPIError(
                    f"模型API响应解析失败", status_code=None, error_type="response_parse_error")

            except Exception as e:
                # 未知错误不重试
                logger.error(f"[Vision] API调用遇到未知错误: {str(e)}, model={self.model_name}")
                raise ModelAPIError(
                    f"模型API调用未知错误", status_code=None, error_type="unknown")

        # 所有重试都失败（保留状态码的结构化异常）
        if isinstance(last_error, requests.exceptions.Timeout):
            raise ModelAPIError(
                f"模型 {self.config['name']} 响应超时（{timeout}秒）",
                status_code=None, error_type="timeout")
        elif isinstance(last_error, requests.exceptions.ConnectionError):
            raise ModelAPIError(
                f"模型 {self.config['name']} 网络连接失败",
                status_code=None, error_type="connection_error")
        elif isinstance(last_error, requests.exceptions.HTTPError):
            status_code = last_error.response.status_code if last_error.response is not None else 0
            raise ModelAPIError(
                f"模型 {self.config['name']} 请求失败({status_code})",
                status_code=status_code, error_type=_classify_http_error(status_code))
        else:
            raise ModelAPIError(
                f"模型 {self.config['name']} 调用失败", status_code=None, error_type="unknown")


class OpenAICompatibleProvider(ModelProvider):
    """OpenAI兼容API提供商（适用于MiMo、DeepSeek、Qwen等）"""
    
    def __init__(self, model_name: str):
        super().__init__(model_name)
    
    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        # 观测与记账放在这一层（而不是 _call_openai_compatible_api）：后者被
        # vision 复用，挂在那里会把图片识别误记成文本模型调用。
        from services import capability_status, usage_ledger
        try:
            result = self._call_openai_compatible_api(messages, **kwargs)
        except Exception as e:
            capability_status.safe_report(
                capability_status.CAP_LLM, False,
                reason=f"{type(e).__name__}: {e}")
            raise
        # 上报真实模型名（self.model_name 是 provider 键，如 deepseek）
        capability_status.safe_report(
            capability_status.CAP_LLM, True,
            reason=f"model={(self.config or {}).get('model_name') or self.model_name}")
        usage_ledger.record(usage_ledger.KIND_LLM)
        return result

    def chat_with_tools(self, messages: List[Dict[str, Any]],
                        tools: Optional[List[Dict[str, Any]]] = None,
                        **kwargs) -> Dict[str, Any]:
        """
        带工具调用的非流式对话（统一 Agent 运行机制）。

        观测/记账与 chat() 一致：这是**一次真实模型调用**，必须计入能力状态与用量账本，
        否则 Agent 的工具决策轮次在预算上不可见。
        """
        from services import capability_status, usage_ledger
        call_kwargs: Dict[str, Any] = {
            "temperature": kwargs.get("temperature", 0.3),
            "max_tokens": kwargs.get("max_tokens", 2048),
        }
        if kwargs.get("timeout"):
            call_kwargs["timeout"] = kwargs["timeout"]
        if tools:
            extra: Dict[str, Any] = {"tools": tools}
            if kwargs.get("tool_choice"):
                extra["tool_choice"] = kwargs["tool_choice"]
            call_kwargs["extra_payload"] = extra
        try:
            result = self._call_openai_compatible_api(messages, **call_kwargs)
        except Exception as e:
            capability_status.safe_report(
                capability_status.CAP_LLM, False,
                reason=f"{type(e).__name__}: {e}")
            raise
        capability_status.safe_report(
            capability_status.CAP_LLM, True,
            reason=f"model={(self.config or {}).get('model_name') or self.model_name}（tools）")
        usage_ledger.record(usage_ledger.KIND_LLM)
        return result

    def stream_with_tools(self, messages: List[Dict[str, Any]],
                          tools: Optional[List[Dict[str, Any]]] = None, **kwargs):
        """
        真流式 + 原生工具调用（统一 Agent 的主路径）。

        OpenAI 兼容的流式协议里，工具调用是**分片到达**的（delta.tool_calls[i].function
        .arguments 逐段拼接），所以必须在生成器内部累积，不能只看单个 chunk。

        Yields:
            {"type": "content", "text": str}      文本增量
            {"type": "end", "content": str, "tool_calls": [...], "finish_reason": str}
                                                  本轮流结束（可能是工具调用，也可能是终答）
        """
        from services import capability_status, usage_ledger
        if not self.api_key:
            raise ModelAPIError("模型未配置API密钥", status_code=None, error_type="invalid_api_key")
        timeout = kwargs.get("timeout", VISION_DEFAULT_TIMEOUT)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.config["model_name"],
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.3),
            "max_tokens": kwargs.get("max_tokens", 2048),
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")
        url = f"{self.config['base_url']}/chat/completions"
        text_parts: List[str] = []
        tool_calls: Dict[int, Dict[str, Any]] = {}
        finish_reason = None
        try:
            import json as _json
            _body = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
            with requests.post(url, headers=headers, data=_body,
                               timeout=timeout, stream=True) as resp:
                if resp.status_code != 200:
                    resp.raise_for_status()
                for raw in resp.iter_lines(decode_unicode=False):
                    if raw is None:
                        continue
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = _json.loads(data_str)
                    except _json.JSONDecodeError:
                        logger.warning(
                            f"[TutorStream] SSE JSON 解析失败: prefix={data_str[:60]!r}")
                        raise ModelAPIError("模型流式响应格式异常(JSON解析失败)",
                                            status_code=200, error_type="response_parse_error")
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    piece = delta.get("content")
                    if piece:
                        text_parts.append(piece)
                        yield {"type": "content", "text": piece}
                    for tc in (delta.get("tool_calls") or []):
                        idx = int(tc.get("index") or 0)
                        slot = tool_calls.setdefault(idx, {"id": None, "type": "function",
                                                           "function": {"name": None,
                                                                        "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
        except GeneratorExit:
            raise
        except ModelAPIError:
            capability_status.safe_report(capability_status.CAP_LLM, False,
                                          reason="stream_with_tools 失败")
            raise
        except Exception as e:
            capability_status.safe_report(capability_status.CAP_LLM, False,
                                          reason=f"{type(e).__name__}: {e}")
            raise

        capability_status.safe_report(
            capability_status.CAP_LLM, True,
            reason=f"model={(self.config or {}).get('model_name') or self.model_name}（tutor）")
        usage_ledger.record(usage_ledger.KIND_LLM)
        ordered = [tool_calls[k] for k in sorted(tool_calls)]
        for i, call in enumerate(ordered):
            if not call.get("id"):
                call["id"] = f"call_{i}"
        yield {"type": "end", "content": "".join(text_parts),
               "tool_calls": ordered, "finish_reason": finish_reason}

    def chat_stream(self, messages: List[Dict[str, str]], **kwargs):
        """
        真流式对话（SSE / OpenAI 兼容 stream=True）

        外面这层包装专门做**观测与记账**：流式对话是聊天的主入口，但它不经过
        call_llm，曾经既不进能力状态、也不进用量账本——聊天消耗的模型调用
        完全不可见（预算是按账本算的，等于聊天不花钱）。
        记账落在"一次调用"层面而不是"一次尝试"：底层有重试循环，按尝试记会虚高。
        """
        from services import capability_status, usage_ledger
        try:
            for chunk in self._iter_stream(messages, **kwargs):
                yield chunk
        except GeneratorExit:
            # 客户端主动断开（用户切页/停止生成）不是模型故障，不能误报成降级
            raise
        except Exception as e:
            capability_status.safe_report(
                capability_status.CAP_LLM, False,
                reason=f"{type(e).__name__}: {e}")
            raise
        else:
            capability_status.safe_report(
                capability_status.CAP_LLM, True,
                reason=f"model={(self.config or {}).get('model_name') or self.model_name}（流式）")
            usage_ledger.record(usage_ledger.KIND_LLM)

    def _iter_stream(self, messages: List[Dict[str, str]], **kwargs):
        """
        真流式对话（SSE / OpenAI 兼容 stream=True）

        逐 chunk 解析模型输出并 yield 文本片段。
        供应商真实流式输出：每个 token/文本块到达即 yield，不是完整响应后逐字播放。

        Yields:
            文本片段 str

        Raises:
            ModelAPIError: 请求失败（结构化错误）
        """
        if not self.api_key:
            raise ModelAPIError("模型未配置API密钥", status_code=None, error_type="invalid_api_key")

        timeout = kwargs.get("timeout", VISION_DEFAULT_TIMEOUT)
        max_retries = kwargs.get("max_retries", VISION_MAX_RETRIES)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.config["model_name"],
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 2048),
            "stream": True,
        }

        url = f"{self.config['base_url']}/chat/completions"

        # debug 日志：仅记录 provider/model/url（不含 Key、不含用户输入）
        logger.debug(
            f"[Chat] stream start: provider={self.model_name} model={self.config.get('model_name')} "
            f"url={url}")

        for attempt in range(max_retries):
            try:
                import json as _json
                _body = _json.dumps(payload, ensure_ascii=False).encode('utf-8')
                with requests.post(url, headers=headers, data=_body,
                                   timeout=timeout, stream=True) as resp:
                    # 非流式错误（4xx/5xx 在 iter_lines 前抛出）
                    if resp.status_code != 200:
                        resp.raise_for_status()

                    first_line_seen = False
                    # 用 bytes 行 + utf-8 解码：避免 decode_unicode 与 bytes delimiter 类型冲突
                    for raw in resp.iter_lines(decode_unicode=False):
                        if raw is None:
                            continue
                        line = raw.decode('utf-8', errors='ignore').strip()
                        if not line:
                            continue
                        # debug 日志：仅记录首行结构（截断，不含完整内容）
                        if not first_line_seen:
                            first_line_seen = True
                            logger.debug(
                                f"[Chat] stream first line: provider={self.model_name} "
                                f"prefix={line[:60]!r}")
                        # OpenAI SSE: data: {json}
                        if line.startswith('data: '):
                            data_str = line[6:].strip()
                            if data_str == '[DONE]':
                                break
                            try:
                                data = _json.loads(data_str)
                                choices = data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        yield content
                            except _json.JSONDecodeError:
                                # 解析失败给明确错误，不静默跳过
                                logger.warning(
                                    f"[Chat] SSE JSON 解析失败: provider={self.model_name} "
                                    f"line_prefix={data_str[:60]!r}")
                                raise ModelAPIError(
                                    "模型流式响应格式异常(JSON解析失败)",
                                    status_code=200, error_type="response_parse_error")
                return  # 成功结束生成器

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                    requests.exceptions.HTTPError) as e:
                error_type = _get_error_type(e)
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                if not _is_retryable_error(e):
                    raise ModelAPIError(f"模型API调用失败: {error_type}",
                                        status_code=status_code, error_type=error_type)
                if attempt < max_retries - 1:
                    backoff = min(
                        VISION_RETRY_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, VISION_RETRY_JITTER),
                        VISION_RETRY_BACKOFF_MAX)
                    logger.warning(f"[Chat] 流式调用失败: {error_type}, 等待{backoff:.1f}s重试")
                    time.sleep(backoff)
                else:
                    raise ModelAPIError(f"模型 {self.config['name']} 流式调用失败: {error_type}",
                                        status_code=status_code, error_type=error_type)
            except ModelAPIError:
                raise
            except Exception as e:
                # 记录真实原因，不再笼统"未知错误"
                logger.error(f"[Chat] 流式调用异常: {type(e).__name__}: {str(e)}, "
                             f"provider={self.model_name}, model={self.config.get('model_name')}")
                raise ModelAPIError(f"模型流式调用失败: {type(e).__name__}",
                                    status_code=None, error_type="stream_error")

    def vision(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        """
        视觉理解（多模态）实现
        直接调用API，让API自己判断是否支持图片输入。
        如果模型不支持，API会返回错误，由调用方处理降级。
        """
        # 不再检查 is_vision 标记，直接尝试调用
        return self._call_openai_compatible_api(messages, **kwargs)


class QwenVisionProvider(ModelProvider):
    """通义千问视觉模型提供商（仅支持视觉功能）"""
    
    def __init__(self, model_name: str = "qwen-vision"):
        super().__init__(model_name)
    
    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """视觉模型不支持文本对话"""
        raise NotImplementedError(f"模型 {self.model_name} 仅支持视觉功能，不支持文本对话")
    
    def vision(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        """
        视觉理解（多模态）实现
        
        Args:
            messages: 多模态消息列表
            **kwargs: 其他参数
        
        Returns:
            响应字典
        
        Raises:
            ValueError: 如果模型不支持视觉功能
        """
        # 检查模型是否支持视觉
        if not self.config.get('is_vision', False):
            raise ValueError(f"模型 {self.model_name} 不是视觉模型，不支持视觉功能")
        
        return self._call_openai_compatible_api(messages, **kwargs)


# 模型提供商映射（所有模型共用同一个 Provider 类）
MODEL_PROVIDERS = {
    "mimo": OpenAICompatibleProvider,
    "deepseek": OpenAICompatibleProvider,
    "qwen": OpenAICompatibleProvider,
    "qwen-vision": QwenVisionProvider,  # 通义千问视觉模型
    "mimo-vision": OpenAICompatibleProvider,  # MiMo视觉模型（已弃用）
}

def get_model_provider(model_name: str = None) -> ModelProvider:
    """
    获取模型提供商实例
    
    Args:
        model_name: 模型名称，默认使用配置中的默认模型
    
    Returns:
        模型提供商实例
    """
    if model_name is None:
        model_name = get_default_model()
    
    if model_name not in MODEL_PROVIDERS:
        raise ValueError(f"不支持的模型: {model_name}，支持的模型: {list(MODEL_PROVIDERS.keys())}")
    
    provider_class = MODEL_PROVIDERS[model_name]
    return provider_class(model_name)


def get_available_models() -> List[Dict[str, str]]:
    """
    获取所有可用的模型列表
    
    Returns:
        模型列表，包含 name 和 display_name
    """
    models = []
    for model_name, provider_class in MODEL_PROVIDERS.items():
        try:
            provider = provider_class(model_name)
            config = provider.config
            models.append({
                "name": model_name,
                "display_name": config["name"],
                "has_api_key": bool(provider.api_key),
            })
        except Exception as e:
            logger.warning(f"获取模型 {model_name} 信息失败: {str(e)}")
            models.append({
                "name": model_name,
                "display_name": model_name,
                "has_api_key": False,
            })
    return models


def chat(model_name: str, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
    """
    统一的对话接口
    
    Args:
        model_name: 模型名称
        messages: 消息列表
        **kwargs: 其他参数
    
    Returns:
        响应字典
    """
    provider = get_model_provider(model_name)
    return provider.chat(messages, **kwargs)


# 兼容旧接口
def get_deepseek_service():
    """获取DeepSeek服务（兼容旧接口）"""
    return get_model_provider("deepseek")