"""
模型服务模块
提供统一的AI模型调用接口，支持多个模型提供商
"""
import time
import requests
import logging
from typing import Optional, List, Dict, Any
from config.models import get_model_config, get_model_api_key, get_default_model

logger = logging.getLogger(__name__)

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
        调用OpenAI兼容的API
        
        Args:
            messages: 消息列表
            **kwargs: 其他参数
        
        Returns:
            API响应
        """
        if not self.api_key:
            raise ValueError(f"模型 {self.model_name} 未配置API密钥")
        
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
        
        try:
            start_time = time.time()
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            latency = time.time() - start_time
            
            response.raise_for_status()
            data = response.json()
            
            # 解析响应
            if "choices" in data and len(data["choices"]) > 0:
                answer = data["choices"][0]["message"]["content"]
            else:
                raise ValueError(f"模型 {self.model_name} 返回格式异常")
            
            return {
                "answer": answer,
                "model": self.model_name,
                "model_display_name": self.config["name"],
                "latency": latency,
                "usage": data.get("usage", {}),
            }
            
        except requests.exceptions.Timeout:
            logger.error(f"调用模型 {self.model_name} API超时（30秒）")
            raise ValueError(f"模型 {self.config['name']} 响应超时，请稍后重试")
        except requests.exceptions.RequestException as e:
            logger.error(f"调用模型 {self.model_name} API失败: {str(e)}")
            raise ValueError(f"模型 {self.config['name']} 调用失败: {str(e)}")
        except Exception as e:
            logger.error(f"处理模型 {self.model_name} 响应失败: {str(e)}")
            raise


class OpenAICompatibleProvider(ModelProvider):
    """OpenAI兼容API提供商（适用于MiMo、DeepSeek、Qwen等）"""
    
    def __init__(self, model_name: str):
        super().__init__(model_name)
    
    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        return self._call_openai_compatible_api(messages, **kwargs)
    
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