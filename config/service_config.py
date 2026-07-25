"""
服务配置模块
管理Web服务相关的配置
"""

from typing import List, Dict, Any
from .base import BaseConfig, Field, PYDANTIC_V2


class ServiceConfig(BaseConfig):
    """服务配置"""
    
    # 服务主机
    host: str = Field("0.0.0.0", validation_alias="SERVICE_HOST")
    
    # 服务端口
    port: int = Field(8000, validation_alias="SERVICE_PORT")
    
    # 调试模式
    debug: bool = Field(False, validation_alias="DEBUG")
    
    # CORS配置
    cors_origins: List[str] = Field(["*"], validation_alias="CORS_ORIGINS")
    
    # 最大请求大小（字节）
    max_request_size: int = Field(10 * 1024 * 1024, validation_alias="MAX_REQUEST_SIZE")  # 10MB
    
    # 工作进程数
    workers: int = Field(1, validation_alias="SERVICE_WORKERS")
    
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
        验证服务配置
        
        Returns:
            错误列表
        """
        errors = []
        
        # 检查端口范围
        if not 1 <= self.port <= 65535:
            errors.append(f"SERVICE_PORT无效: {self.port}，范围: 1-65535")
        
        # 检查max_request_size
        if self.max_request_size <= 0:
            errors.append(f"MAX_REQUEST_SIZE无效: {self.max_request_size}")
        
        # 检查workers
        if self.workers <= 0:
            errors.append(f"SERVICE_WORKERS无效: {self.workers}")
        
        return errors
    
    def is_valid(self) -> bool:
        """检查配置是否有效"""
        return len(self.validate()) == 0
    
    def get_masked_config(self) -> Dict[str, Any]:
        """获取配置字典"""
        return {
            "host": self.host,
            "port": self.port,
            "debug": self.debug,
            "cors_origins": self.cors_origins,
            "max_request_size": self.max_request_size,
            "workers": self.workers
        }


# 公开API
__all__ = [
    "ServiceConfig"
]
