"""
LLM模块
提供统一的LLM接口，支持多种LLM提供商
根据config.settings.llm.provider自动选择提供商
"""

from typing import Optional, Dict, Any, List, Union
import os
from config import settings

# 支持的LLM提供商
SUPPORTED_PROVIDERS = ["deepseek", "ollama", "qwen"]

def get_llm(
    provider: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    **kwargs
) -> Any:
    """
    获取LLM实例的工厂函数
    
    Args:
        provider: LLM提供商名称，如果为None则从配置读取
        temperature: 温度参数（可选）
        max_tokens: 最大token数（可选）
        **kwargs: 其他配置参数
        
    Returns:
        LLM实例
        
    Raises:
        ValueError: 不支持的提供商
        RuntimeError: LLM初始化失败
    """
    # 获取提供商：优先参数，其次配置，最后环境变量
    if provider is None:
        provider = settings.llm.provider
    
    # 标准化提供商名称
    provider = provider.lower()
    
    # 验证提供商
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"不支持的LLM提供商: {provider}。支持的提供商: {SUPPORTED_PROVIDERS}")
    
    # 根据提供商返回对应的LLM实例
    if provider == "deepseek":
        from .deepseek import create_deepseek_llm
        return create_deepseek_llm(temperature=temperature, max_tokens=max_tokens, **kwargs)
    elif provider == "ollama":
        from .ollama import create_ollama_llm
        return create_ollama_llm(temperature=temperature, max_tokens=max_tokens, **kwargs)
    elif provider == "qwen":
        from .qwen import create_qwen_llm
        return create_qwen_llm(temperature=temperature, max_tokens=max_tokens, **kwargs)
    else:
        raise ValueError(f"未实现的LLM提供商: {provider}")


def get_llm_provider_info() -> Dict[str, Any]:
    """
    获取当前LLM提供商信息
    
    Returns:
        提供商信息字典
    """
    provider = settings.llm.provider
    
    info = {
        "provider": provider,
        "supported_providers": SUPPORTED_PROVIDERS,
        "is_supported": provider in SUPPORTED_PROVIDERS
    }
    
    # 添加提供商特定信息
    if provider == "deepseek":
        from .deepseek import get_deepseek_config
        info.update(get_deepseek_config())
    elif provider == "ollama":
        from .ollama import get_ollama_config
        info.update(get_ollama_config())
    elif provider == "qwen":
        from .qwen import get_qwen_config
        info.update(get_qwen_config())
    
    return info


def check_llm_availability(provider: Optional[str] = None) -> Dict[str, Any]:
    """
    检查LLM可用性
    
    Args:
        provider: LLM提供商名称，如果为None则检查当前配置的提供商
        
    Returns:
        可用性信息字典
    """
    if provider is None:
        provider = settings.llm.provider
    
    # 标准化提供商名称
    provider = provider.lower()
    
    result = {
        "provider": provider,
        "available": False,
        "error": None
    }
    
    try:
        if provider == "deepseek":
            from .deepseek import check_deepseek_availability
            deepseek_result = check_deepseek_availability()
            result.update(deepseek_result)
        elif provider == "ollama":
            from .ollama import check_ollama_availability
            ollama_result = check_ollama_availability()
            result.update(ollama_result)
        elif provider == "qwen":
            from .qwen import check_qwen_availability
            qwen_result = check_qwen_availability()
            result.update(qwen_result)
        else:
            result["error"] = f"不支持的提供商: {provider}"
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