"""
LLM模块
提供统一的LLM接口，支持多种LLM提供商
使用新的ModelProvider架构，支持MiMo、DeepSeek、Qwen
"""

from typing import Optional, Dict, Any
from config.models import get_model_config, get_model_api_key, get_default_model

# 支持的LLM提供商（与config/models.py中的MODEL_CONFIG保持一致）
SUPPORTED_PROVIDERS = ["mimo", "deepseek", "qwen"]


def get_llm(
    provider: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    **kwargs
) -> Any:
    """
    获取LLM实例的工厂函数（兼容旧接口）
    
    Args:
        provider: LLM提供商名称，如果为None则使用默认模型
        temperature: 温度参数（可选）
        max_tokens: 最大token数（可选）
        **kwargs: 其他配置参数
        
    Returns:
        LLM实例（兼容LangChain接口）
        
    Raises:
        ValueError: 不支持的提供商
        RuntimeError: LLM初始化失败
    """
    # 获取提供商：优先参数，其次默认模型
    if provider is None:
        provider = get_default_model()
    
    # 标准化提供商名称
    provider = provider.lower()
    
    # 验证提供商
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"不支持的LLM提供商: {provider}。支持的提供商: {SUPPORTED_PROVIDERS}")
    
    # 使用新的ModelProvider架构
    from services.model_service import get_model_provider
    
    # 创建兼容LangChain接口的包装器
    class LLMWrapper:
        """将ModelProvider包装为兼容LangChain接口的LLM实例"""
        
        def __init__(self, model_name, temperature=None, max_tokens=None):
            self.model_name = model_name
            self.temperature = temperature
            self.max_tokens = max_tokens
            self.provider = get_model_provider(model_name)
        
        def invoke(self, messages, **kwargs):
            """调用模型（兼容LangChain接口）"""
            # 转换消息格式
            formatted_messages = []
            for msg in messages:
                if hasattr(msg, 'content'):
                    # LangChain消息对象
                    role = "user" if hasattr(msg, 'type') and msg.type == "human" else "system"
                    formatted_messages.append({"role": role, "content": msg.content})
                elif isinstance(msg, dict):
                    # 已经是字典格式
                    formatted_messages.append(msg)
                else:
                    # 字符串
                    formatted_messages.append({"role": "user", "content": str(msg)})
            
            # 调用模型
            result = self.provider.chat(
                formatted_messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            
            # 返回兼容LangChain的响应
            class Response:
                def __init__(self, content):
                    self.content = content
            
            return Response(result.get("answer", ""))
        
        def stream(self, messages, **kwargs):
            """流式调用（简化实现，实际使用时可能需要完善）"""
            # 暂时使用非流式调用模拟流式
            response = self.invoke(messages, **kwargs)
            yield response
    
    return LLMWrapper(provider, temperature, max_tokens)


def get_llm_provider_info() -> Dict[str, Any]:
    """
    获取当前LLM提供商信息
    
    Returns:
        提供商信息字典
    """
    from services.model_service import get_model_config, get_default_model
    
    model_name = get_default_model()
    config = get_model_config(model_name)
    
    return {
        "provider": model_name,
        "model": config.get("model_name", model_name),
        "display_name": config.get("name", model_name),
        "supported_providers": SUPPORTED_PROVIDERS,
        "is_supported": True
    }


def check_llm_availability(provider: Optional[str] = None) -> Dict[str, Any]:
    """
    检查LLM可用性
    
    Args:
        provider: LLM提供商名称，如果为None则检查默认模型
        
    Returns:
        可用性信息字典
    """
    if provider is None:
        provider = get_default_model()
    
    # 标准化提供商名称
    provider = provider.lower()
    
    result = {
        "provider": provider,
        "available": False,
        "error": None
    }
    
    try:
        # 检查是否支持该提供商
        if provider not in SUPPORTED_PROVIDERS:
            result["error"] = f"不支持的提供商: {provider}"
            return result
        
        # 检查API密钥是否配置
        api_key = get_model_api_key(provider)
        if not api_key:
            result["error"] = f"模型 {provider} 未配置API密钥"
            return result
        
        # 获取模型配置
        config = get_model_config(provider)
        result.update({
            "available": True,
            "model": config.get("model_name", provider),
            "display_name": config.get("name", provider),
            "base_url": config.get("base_url", ""),
        })
        
    except Exception as e:
        result["error"] = f"检查{provider}可用性时出错: {str(e)}"
    
    return result


# 兼容别名（api/app.py健康检查使用）
get_model_info = get_llm_provider_info
check_llm_status = check_llm_availability

# 公开API
__all__ = [
    "get_llm",
    "get_llm_provider_info",
    "check_llm_availability",
    "SUPPORTED_PROVIDERS",
    "get_model_info",
    "check_llm_status"
]