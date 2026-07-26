"""
配置中心模块
统一管理项目配置

配置优先级：环境变量 > .env 文件 > config.json > 默认值
"""

import os
from pathlib import Path

# 尝试加载 .env 文件（如果存在）
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)  # override=False: 环境变量优先
except ImportError:
    pass  # python-dotenv 未安装时跳过

from .settings import Settings, settings
from .llm_config import LLMConfig
from .rag_config import RAGConfig
from .path_config import PathConfig
from .log_config import LogConfig
from .service_config import ServiceConfig

# 应用版本
APP_VERSION = "2.1.0"

# 全局超时配置（秒）
LLM_REQUEST_TIMEOUT: int = int(os.getenv("LLM_REQUEST_TIMEOUT", "120"))
RAG_REQUEST_TIMEOUT: int = int(os.getenv("RAG_REQUEST_TIMEOUT", "60"))

# 向量存储目录（兼容旧代码）
VECTOR_STORE_DIR = settings.path.vector_store_dir


def get_settings() -> Settings:
    """
    获取全局配置实例
    
    Returns:
        Settings实例
    """
    return settings


def get_llm_config(provider: str = None) -> dict:
    """
    获取LLM配置（脱敏）
    
    Args:
        provider: LLM提供商名称
        
    Returns:
        LLM配置字典
    """
    return settings.llm.get_provider_config(provider)


def get_rag_config() -> dict:
    """
    获取RAG配置
    
    Returns:
        RAG配置字典
    """
    return settings.rag.get_masked_config()


def get_path_config() -> dict:
    """
    获取路径配置
    
    Returns:
        路径配置字典
    """
    return settings.path.get_masked_config()


def get_log_config() -> dict:
    """
    获取日志配置
    
    Returns:
        日志配置字典
    """
    return settings.log.get_masked_config()


def get_service_config() -> dict:
    """
    获取服务配置
    
    Returns:
        服务配置字典
    """
    return settings.service.get_masked_config()


def validate_config() -> dict:
    """
    验证配置
    
    Returns:
        验证结果字典
    """
    errors = settings.validate()
    return {
        "valid": len(errors) == 0,
        "errors": errors
    }


def check_config_status() -> dict:
    """
    检查配置状态
    
    Returns:
        配置状态字典
    """
    return settings.check_config_status()


def init_config() -> dict:
    """
    初始化配置
    
    Returns:
        初始化结果
    """
    # 确保目录存在
    settings.path.ensure_directories()
    
    # 验证配置
    validation = validate_config()
    
    return {
        "initialized": True,
        "validation": validation,
        "status": check_config_status(),
        "config": settings.to_dict()
    }


# 兼容旧版本的导入方式
# 保持 from config import settings 可用
__all__ = [
    # 核心配置
    "Settings",
    "settings",
    
    # 配置类
    "LLMConfig",
    "RAGConfig",
    "PathConfig",
    "LogConfig",
    "ServiceConfig",
    
    # 便捷函数
    "get_settings",
    "get_llm_config",
    "get_rag_config",
    "get_path_config",
    "get_log_config",
    "get_service_config",
    "validate_config",
    "check_config_status",
    "init_config",
    
    # 常量
    "APP_VERSION",
    "LLM_REQUEST_TIMEOUT",
    "RAG_REQUEST_TIMEOUT",
    "VECTOR_STORE_DIR",
]
