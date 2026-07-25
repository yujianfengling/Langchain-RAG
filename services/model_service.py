# services/model_service.py
# 模型信息管理业务服务层

from typing import Dict, Any, Optional

from config import settings, get_llm_config
from llm import get_llm, get_llm_provider_info, check_llm_availability


class ModelServiceError(Exception):
    """模型服务异常"""
    pass


def get_current_model() -> Dict[str, Any]:
    """
    获取当前配置的模型信息
    
    Returns:
        当前模型信息字典
    """
    try:
        # 获取LLM配置
        llm_config = get_llm_config()
        
        # 获取提供商信息
        provider_info = get_llm_provider_info()
        
        return {
            "provider": settings.llm.provider,
            "model": llm_config.get("model", llm_config.get("model_name", "unknown")),
            "config": {
                "temperature": llm_config.get("temperature"),
                "max_tokens": llm_config.get("max_tokens")
            },
            "provider_info": provider_info
        }
    except Exception as e:
        raise ModelServiceError(f"获取当前模型信息失败: {str(e)}")


def get_available_models() -> Dict[str, Any]:
    """
    获取可用模型信息
    
    Returns:
        包含models列表的字典，每个模型包含provider、model、available字段
    """
    try:
        from llm import SUPPORTED_PROVIDERS, check_llm_availability
        
        models = []
        for provider in SUPPORTED_PROVIDERS:
            try:
                # 检查提供商可用性
                availability = check_llm_availability(provider)
                # 获取提供商配置
                if provider == "deepseek":
                    from llm.deepseek import get_deepseek_config
                    config = get_deepseek_config()
                    model_name = config.get("model", "deepseek-chat")
                elif provider == "ollama":
                    from llm.ollama import get_ollama_config
                    config = get_ollama_config()
                    model_name = config.get("model", "llama2")
                elif provider == "qwen":
                    from llm.qwen import get_qwen_config
                    config = get_qwen_config()
                    model_name = config.get("model", "qwen2.5:7b")
                else:
                    model_name = "unknown"
                
                models.append({
                    "provider": provider,
                    "model": model_name,
                    "available": availability.get("available", False),
                    "error": availability.get("error")
                })
            except Exception as e:
                models.append({
                    "provider": provider,
                    "model": "unknown",
                    "available": False,
                    "error": str(e)
                })
        
        return {"models": models}
    except Exception as e:
        raise ModelServiceError(f"获取可用模型信息失败: {str(e)}")


def check_model_status(provider: Optional[str] = None) -> Dict[str, Any]:
    """
    检查模型状态
    
    Args:
        provider: LLM提供商名称，如果为None则检查当前配置的提供商
        
    Returns:
        模型状态信息字典
    """
    try:
        # 检查LLM可用性
        availability = check_llm_availability(provider)
        
        # 获取当前模型信息
        current_model = get_current_model()
        
        return {
            "available": availability.get("available", False),
            "provider": availability.get("provider", settings.llm.provider),
            "error": availability.get("error"),
            "current_model": current_model,
            "check_time": "now"  # 可以添加时间戳
        }
    except Exception as e:
        raise ModelServiceError(f"检查模型状态失败: {str(e)}")


def switch_model(provider: str) -> Dict[str, Any]:
    """
    切换模型提供商
    
    Args:
        provider: 要切换到的提供商名称
        
    Returns:
        切换结果字典
        
    Raises:
        ModelServiceError: 切换失败
    """
    try:
        from llm import SUPPORTED_PROVIDERS
        
        # 验证提供商是否支持
        provider = provider.lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise ModelServiceError(f"不支持的提供商: {provider}。支持的提供商: {SUPPORTED_PROVIDERS}")
        
        # 检查提供商可用性
        availability = check_llm_availability(provider)
        if not availability.get("available", False):
            error_msg = availability.get("error", "提供商不可用")
            raise ModelServiceError(f"无法切换到 {provider}: {error_msg}")
        
        # 修改配置：更新环境变量和settings对象
        import os
        os.environ["LLM_PROVIDER"] = provider
        
        # 直接修改settings对象的llm.provider
        settings.llm.provider = provider
        
        return {
            "success": True,
            "message": f"模型切换成功，当前提供商: {provider}",
            "provider": provider
        }
    except ModelServiceError:
        raise
    except Exception as e:
        raise ModelServiceError(f"切换模型失败: {str(e)}")


# 公开API
__all__ = [
    "get_current_model",
    "get_available_models",
    "check_model_status",
    "switch_model",
    "ModelServiceError"
]