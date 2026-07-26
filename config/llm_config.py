"""
LLM配置模块
管理LLM配置（已简化，使用新的ModelProvider架构）
"""

from typing import Optional, Dict, Any
from .base import BaseConfig, Field, PYDANTIC_V2


class LLMConfig(BaseConfig):
    """LLM配置"""
    
    # 默认模型提供商
    provider: str = Field("mimo", validation_alias="LLM_PROVIDER")
    
    # 模型配置（使用新的ModelProvider架构）
    # 具体配置在 config/models.py 中管理
    
    if PYDANTIC_V2:
        model_config = {
            "env_prefix": "",
            "case_sensitive": False
        }
    else:
        class Config:
            env_prefix = ""
            case_sensitive = False
    
    def get_provider_config(self, provider: str = None) -> Dict[str, Any]:
        """
        获取指定提供商的配置
        
        Args:
            provider: 提供商名称，如果为None则使用当前配置的提供商
            
        Returns:
            提供商配置字典
        """
        provider = provider or self.provider
        
        # 使用新的ModelProvider架构获取配置
        from config.models import get_model_config
        return get_model_config(provider)
    
    def is_provider_configured(self, provider: str = None) -> bool:
        """
        检查指定提供商是否已配置
        
        Args:
            provider: 提供商名称
            
        Returns:
            是否已配置
        """
        provider = provider or self.provider
        
        # 使用新的ModelProvider架构检查配置
        from config.models import get_model_api_key
        api_key = get_model_api_key(provider)
        return bool(api_key)
    
    def validate(self) -> list:
        """
        验证LLM配置
        
        Returns:
            错误列表
        """
        errors = []
        
        # 检查提供商是否支持
        from config.models import MODEL_CONFIG
        if self.provider not in MODEL_CONFIG:
            errors.append(f"不支持的LLM提供商: {self.provider}")
        
        # 检查API密钥是否配置
        if not self.is_provider_configured():
            errors.append(f"模型 {self.provider} 的API密钥未配置")
        
        return errors
    
    def get_masked_config(self) -> Dict[str, Any]:
        """获取脱敏后的配置"""
        from config.models import get_model_config, mask_sensitive_value
        
        config = get_model_config(self.provider)
        masked_config = config.copy()
        
        # 脱敏API密钥
        if "api_key" in masked_config:
            masked_config["api_key"] = mask_sensitive_value(masked_config["api_key"])
        
        return {
            "provider": self.provider,
            "model_config": masked_config
        }


# 公开API
__all__ = [
    "LLMConfig"
]