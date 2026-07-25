"""
LLM配置模块
管理DeepSeek和Ollama的配置
"""

from typing import Optional, Dict, Any
from .base import BaseConfig, Field, PYDANTIC_V2


class DeepSeekConfig(BaseConfig):
    """DeepSeek配置"""
    
    api_key: Optional[str] = Field(None, validation_alias="DEEPSEEK_API_KEY")
    model: str = Field("deepseek-chat", validation_alias="DEEPSEEK_MODEL")
    api_base: str = Field("https://api.deepseek.com", validation_alias="DEEPSEEK_API_BASE")
    temperature: float = Field(0.7, validation_alias="DEEPSEEK_TEMPERATURE")
    max_tokens: int = Field(2000, validation_alias="DEEPSEEK_MAX_TOKENS")
    request_timeout: int = Field(120, validation_alias="DEEPSEEK_REQUEST_TIMEOUT")
    
    if PYDANTIC_V2:
        model_config = {
            "env_prefix": "",
            "case_sensitive": False
        }
    else:
        class Config:
            env_prefix = ""
            case_sensitive = False
    
    def is_configured(self) -> bool:
        """检查DeepSeek是否已配置"""
        return bool(self.api_key)
    
    def get_masked_config(self) -> Dict[str, Any]:
        """获取脱敏后的配置"""
        from .base import mask_sensitive_value
        return {
            "api_key": mask_sensitive_value(self.api_key) if self.api_key else None,
            "model": self.model,
            "api_base": self.api_base,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "request_timeout": self.request_timeout
        }


class OllamaConfig(BaseConfig):
    """Ollama配置"""
    
    model: str = Field("llama2", validation_alias="OLLAMA_MODEL")
    base_url: str = Field("http://localhost:11434", validation_alias="OLLAMA_BASE_URL")
    temperature: float = Field(0.7, validation_alias="OLLAMA_TEMPERATURE")
    max_tokens: int = Field(2000, validation_alias="OLLAMA_MAX_TOKENS")
    request_timeout: int = Field(120, validation_alias="OLLAMA_REQUEST_TIMEOUT")
    
    if PYDANTIC_V2:
        model_config = {
            "env_prefix": "",
            "case_sensitive": False
        }
    else:
        class Config:
            env_prefix = ""
            case_sensitive = False
    
    def is_configured(self) -> bool:
        """检查Ollama是否已配置"""
        # Ollama只需要base_url即可
        return bool(self.base_url)
    
    def get_masked_config(self) -> Dict[str, Any]:
        """获取脱敏后的配置"""
        return {
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "request_timeout": self.request_timeout
        }


class QwenConfig(BaseConfig):
    """Qwen配置"""
    
    model: str = Field("qwen2.5:7b", validation_alias="QWEN_MODEL")
    provider: str = Field("ollama", validation_alias="QWEN_PROVIDER")
    base_url: str = Field("http://localhost:11434", validation_alias="QWEN_BASE_URL")
    api_key: Optional[str] = Field(None, validation_alias="QWEN_API_KEY")
    max_tokens: int = Field(2000, validation_alias="QWEN_MAX_TOKENS")
    temperature: float = Field(0.7, validation_alias="QWEN_TEMPERATURE")
    enable_search: bool = Field(True, validation_alias="QWEN_ENABLE_SEARCH")
    request_timeout: int = Field(120, validation_alias="QWEN_REQUEST_TIMEOUT")
    
    if PYDANTIC_V2:
        model_config = {
            "env_prefix": "",
            "case_sensitive": False
        }
    else:
        class Config:
            env_prefix = ""
            case_sensitive = False
    
    def is_configured(self) -> bool:
        """检查Qwen是否已配置"""
        if self.provider == "ollama":
            return bool(self.base_url)
        else:
            return bool(self.api_key)
    
    def get_masked_config(self) -> Dict[str, Any]:
        """获取脱敏后的配置"""
        from .base import mask_sensitive_value
        return {
            "model": self.model,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key": mask_sensitive_value(self.api_key) if self.api_key else None,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "enable_search": self.enable_search,
            "request_timeout": self.request_timeout
        }


class LLMConfig(BaseConfig):
    """LLM配置"""
    
    provider: str = Field("deepseek", validation_alias="LLM_PROVIDER")
    deepseek: DeepSeekConfig = Field(default_factory=DeepSeekConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    qwen: QwenConfig = Field(default_factory=QwenConfig)
    
    if PYDANTIC_V2:
        model_config = {
            "env_prefix": "",
            "case_sensitive": False
        }
    else:
        class Config:
            env_prefix = ""
            case_sensitive = False
    
    def get_provider_config(self, provider: str = None) -> Any:
        """
        获取指定提供商的配置
        
        Args:
            provider: 提供商名称，如果为None则使用当前配置的提供商
            
        Returns:
            提供商配置
        """
        provider = provider or self.provider
        
        if provider == "deepseek":
            return self.deepseek
        elif provider == "ollama":
            return self.ollama
        elif provider == "qwen":
            return self.qwen
        else:
            raise ValueError(f"不支持的LLM提供商: {provider}")
    
    def get_provider_dict(self, provider: str = None) -> Dict[str, Any]:
        """
        获取指定提供商的配置字典（脱敏）
        
        Args:
            provider: 提供商名称
            
        Returns:
            配置字典
        """
        config = self.get_provider_config(provider)
        return config.get_masked_config()
    
    def is_provider_configured(self, provider: str = None) -> bool:
        """
        检查指定提供商是否已配置
        
        Args:
            provider: 提供商名称
            
        Returns:
            是否已配置
        """
        provider = provider or self.provider
        
        if provider == "deepseek":
            return self.deepseek.is_configured()
        elif provider == "ollama":
            return self.ollama.is_configured()
        elif provider == "qwen":
            return self.qwen.is_configured()
        else:
            return False
    
    def validate(self) -> list:
        """
        验证LLM配置
        
        Returns:
            错误列表
        """
        errors = []
        
        # 检查提供商是否支持
        if self.provider not in ["deepseek", "ollama", "qwen"]:
            errors.append(f"不支持的LLM提供商: {self.provider}")
        
        # 检查DeepSeek配置
        if self.provider == "deepseek" and not self.deepseek.is_configured():
            errors.append("DeepSeek API Key未配置")
        
        # 检查温度范围
        if not 0 <= self.deepseek.temperature <= 2:
            errors.append(f"DeepSeek温度参数无效: {self.deepseek.temperature}")
        
        if not 0 <= self.ollama.temperature <= 2:
            errors.append(f"Ollama温度参数无效: {self.ollama.temperature}")
        
        if not 0 <= self.qwen.temperature <= 2:
            errors.append(f"Qwen温度参数无效: {self.qwen.temperature}")
        
        # 检查max_tokens
        if self.deepseek.max_tokens <= 0:
            errors.append(f"DeepSeek max_tokens无效: {self.deepseek.max_tokens}")
        
        if self.ollama.max_tokens <= 0:
            errors.append(f"Ollama max_tokens无效: {self.ollama.max_tokens}")
        
        if self.qwen.max_tokens <= 0:
            errors.append(f"Qwen max_tokens无效: {self.qwen.max_tokens}")
        
        return errors
    
    def get_masked_config(self) -> Dict[str, Any]:
        """获取脱敏后的配置"""
        return {
            "provider": self.provider,
            "deepseek": self.deepseek.get_masked_config(),
            "ollama": self.ollama.get_masked_config(),
            "qwen": self.qwen.get_masked_config()
        }


# 公开API
__all__ = [
    "DeepSeekConfig",
    "OllamaConfig",
    "LLMConfig"
]
