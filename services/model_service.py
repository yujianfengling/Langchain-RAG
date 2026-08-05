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
    # ValueError 等业务错误不可重试
    if isinstance(exc, ValueError):
        return False
    return False


def _get_error_type(exc: Exception) -> str:
    """获取简化的错误类型（用于metadata和日志，不包含敏感信息）"""
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "network_error"
    if isinstance(exc, requests.exceptions.HTTPError):
        status_code = exc.response.status_code if exc.response is not None else 0
        if status_code == 429:
            return "rate_limit"
        if status_code >= 500:
            return f"server_error_{status_code}"
        if status_code == 400:
            return "bad_request"
        if status_code == 401:
            return "auth_error"
        if status_code == 403:
            return "permission_denied"
        return f"http_error_{status_code}"
    return "unknown_error"

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
                
                # 检查是否可重试
                if not _is_retryable_error(e):
                    logger.error(
                        f"[Vision] API调用遇到不可重试错误: {error_type}, "
                        f"model={self.model_name}, attempt={attempt + 1}/{max_retries}"
                    )
                    raise
                
                # 最后一次尝试失败，不再等待
                if attempt < max_retries - 1:
                    # 指数退避 + 随机抖动
                    backoff = min(
                        VISION_RETRY_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, VISION_RETRY_JITTER),
                        VISION_RETRY_BACKOFF_MAX
                    )
                    logger.warning(
                        f"[Vision] API调用失败: {error_type}, "
                        f"model={self.model_name}, attempt={attempt + 1}/{max_retries}, "
                        f"等待{backoff:.1f}s后重试"
                    )
                    time.sleep(backoff)
                else:
                    logger.error(
                        f"[Vision] API调用最终失败: {error_type}, "
                        f"model={self.model_name}, 已用尽{max_retries}次尝试, "
                        f"total_latency={total_latency:.2f}s"
                    )
            
            except ValueError as e:
                # 业务错误（返回格式异常等）不重试
                logger.error(f"[Vision] API响应解析失败: {str(e)}, model={self.model_name}")
                raise
            
            except Exception as e:
                # 未知错误不重试
                logger.error(f"[Vision] API调用遇到未知错误: {str(e)}, model={self.model_name}")
                raise
        
        # 所有重试都失败
        if isinstance(last_error, requests.exceptions.Timeout):
            raise ValueError(f"模型 {self.config['name']} 响应超时（{timeout}秒），已重试{max_retries}次")
        elif isinstance(last_error, requests.exceptions.ConnectionError):
            raise ValueError(f"模型 {self.config['name']} 网络连接失败，已重试{max_retries}次")
        elif isinstance(last_error, requests.exceptions.HTTPError):
            status_code = last_error.response.status_code if last_error.response is not None else 0
            if status_code == 429:
                raise ValueError(f"模型 {self.config['name']} 请求频率超限(429)，已重试{max_retries}次")
            else:
                raise ValueError(f"模型 {self.config['name']} 服务端错误({status_code})，已重试{max_retries}次")
        else:
            raise ValueError(f"模型 {self.config['name']} 调用失败，已重试{max_retries}次")


class OpenAICompatibleProvider(ModelProvider):
    """OpenAI兼容API提供商（适用于MiMo、DeepSeek、Qwen等）"""
    
    def __init__(self, model_name: str):
        super().__init__(model_name)
    
    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        return self._call_openai_compatible_api(messages, **kwargs)
    
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