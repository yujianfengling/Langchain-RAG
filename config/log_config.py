"""
日志配置模块
管理日志相关的配置
"""

from typing import Optional, Dict, Any
from .base import BaseConfig, Field, PYDANTIC_V2


class LogConfig(BaseConfig):
    """日志配置"""
    
    # 日志级别
    level: str = Field("INFO", validation_alias="LOG_LEVEL")
    
    # 日志格式
    format: str = Field("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    
    # 日志文件路径
    file: Optional[str] = Field(None, validation_alias="LOG_FILE")
    
    # 日志轮转配置
    max_bytes: int = Field(10 * 1024 * 1024, validation_alias="LOG_MAX_BYTES")  # 10MB
    backup_count: int = Field(5, validation_alias="LOG_BACKUP_COUNT")
    
    # 是否启用控制台输出
    console_output: bool = Field(True, validation_alias="LOG_CONSOLE_OUTPUT")
    
    if PYDANTIC_V2:
        model_config = {
            "env_prefix": "",
            "case_sensitive": False
        }
    else:
        class Config:
            env_prefix = ""
            case_sensitive = False
    
    def validate(self) -> list:
        """
        验证日志配置
        
        Returns:
            错误列表
        """
        errors = []
        
        # 检查日志级别
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.level.upper() not in valid_levels:
            errors.append(f"LOG_LEVEL无效: {self.level}，支持: {', '.join(valid_levels)}")
        
        # 检查max_bytes
        if self.max_bytes <= 0:
            errors.append(f"LOG_MAX_BYTES无效: {self.max_bytes}")
        
        # 检查backup_count
        if self.backup_count < 0:
            errors.append(f"LOG_BACKUP_COUNT无效: {self.backup_count}")
        
        return errors
    
    def is_valid(self) -> bool:
        """检查配置是否有效"""
        return len(self.validate()) == 0
    
    def get_masked_config(self) -> Dict[str, Any]:
        """获取配置字典"""
        return {
            "level": self.level,
            "format": self.format,
            "file": self.file,
            "max_bytes": self.max_bytes,
            "backup_count": self.backup_count,
            "console_output": self.console_output
        }


# 公开API
__all__ = [
    "LogConfig"
]
