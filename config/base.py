"""
配置基类模块
提供统一的配置加载和管理功能
兼容pydantic v1/v2
"""

import os
from pathlib import Path
from typing import Optional, Dict, Any

# 兼容pydantic v1/v2
try:
    from pydantic_settings import BaseSettings
    from pydantic import Field, validator, ConfigDict, AliasChoices
    PYDANTIC_V2 = True
except ImportError:
    try:
        from pydantic import BaseSettings, Field, validator
        PYDANTIC_V2 = False
    except ImportError:
        # 如果没有pydantic，提供简单的替代实现
        class BaseSettings:
            """简单的Settings替代类"""
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)
        
        def Field(default=None, **kwargs):
            return default
        
        def validator(*args, **kwargs):
            def decorator(func):
                return func
            return decorator
        
        PYDANTIC_V2 = False


class BaseConfig(BaseSettings):
    """
    配置基类
    支持.env文件读取和环境变量覆盖
    """
    
    if PYDANTIC_V2:
        # pydantic v2配置
        model_config = ConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore"
        )
    else:
        # pydantic v1配置
        class Config:
            """Pydantic配置"""
            env_file = ".env"
            env_file_encoding = "utf-8"
            case_sensitive = False
            # 允许额外字段
            extra = "ignore"
    
    @classmethod
    def get_env_value(cls, key: str, default=None):
        """
        获取环境变量值
        
        Args:
            key: 环境变量名
            default: 默认值
            
        Returns:
            环境变量值或默认值
        """
        return os.getenv(key, default)
    
    @classmethod
    def get_env_bool(cls, key: str, default: bool = False) -> bool:
        """
        获取布尔类型环境变量
        
        Args:
            key: 环境变量名
            default: 默认值
            
        Returns:
            布尔值
        """
        value = os.getenv(key, str(default)).lower()
        return value in ("true", "1", "yes", "on")
    
    @classmethod
    def get_env_int(cls, key: str, default: int = 0) -> int:
        """
        获取整数类型环境变量
        
        Args:
            key: 环境变量名
            default: 默认值
            
        Returns:
            整数值
        """
        try:
            return int(os.getenv(key, str(default)))
        except (ValueError, TypeError):
            return default
    
    @classmethod
    def get_env_float(cls, key: str, default: float = 0.0) -> float:
        """
        获取浮点数类型环境变量
        
        Args:
            key: 环境变量名
            default: 默认值
            
        Returns:
            浮点数值
        """
        try:
            return float(os.getenv(key, str(default)))
        except (ValueError, TypeError):
            return default
    
    @classmethod
    def get_env_list(cls, key: str, default: list = None, separator: str = ",") -> list:
        """
        获取列表类型环境变量
        
        Args:
            key: 环境变量名
            default: 默认值
            separator: 分隔符
            
        Returns:
            列表
        """
        if default is None:
            default = []
        
        value = os.getenv(key)
        if value is None:
            return default
        
        return [item.strip() for item in value.split(separator) if item.strip()]
    
    def dict(self, **kwargs) -> Dict[str, Any]:
        """
        转换为字典，兼容pydantic v1/v2
        
        Returns:
            配置字典
        """
        if PYDANTIC_V2:
            # pydantic v2
            return self.model_dump(**kwargs)
        else:
            # pydantic v1
            return super().dict(**kwargs)
    
    def json(self, **kwargs) -> str:
        """
        转换为JSON字符串，兼容pydantic v1/v2
        
        Returns:
            JSON字符串
        """
        if PYDANTIC_V2:
            # pydantic v2
            return self.model_dump_json(**kwargs)
        else:
            # pydantic v1
            return super().json(**kwargs)


def mask_sensitive_value(value: str, visible_chars: int = 4) -> str:
    """
    对敏感信息进行脱敏处理
    
    Args:
        value: 原始值
        visible_chars: 可见字符数
        
    Returns:
        脱敏后的字符串
    """
    if not value or len(value) <= visible_chars * 2:
        return "****"
    
    return f"{value[:visible_chars]}****{value[-visible_chars:]}"


def get_project_root() -> Path:
    """
    获取项目根目录
    
    Returns:
        项目根目录路径
    """
    return Path(__file__).parent.parent


# 公开API
__all__ = [
    "BaseConfig",
    "Field",
    "validator",
    "PYDANTIC_V2",
    "mask_sensitive_value",
    "get_project_root"
]
