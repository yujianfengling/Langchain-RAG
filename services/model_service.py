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
                    answer = data["choices"][0]["message"]["content"]
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
        return self._call_openai_compatible_api(messages, **kwargs)

    def chat_stream(self, messages: List[Dict[str, str]], **kwargs):
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

        last_error = None
        total_latency = 0.0

        for attempt in range(max_retries):
            try:
                import json as _json
                _body = _json.dumps(payload, ensure_ascii=False).encode('utf-8')
                start_time = time.time()
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
                            except _json.JSONDecodeError as je:
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
                last_error = e
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