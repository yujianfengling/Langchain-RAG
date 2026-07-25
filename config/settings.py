"""
核心配置模块
统一管理所有配置
"""

from typing import Dict, Any
from .llm_config import LLMConfig
from .rag_config import RAGConfig
from .path_config import PathConfig
from .log_config import LogConfig
from .service_config import ServiceConfig


class Settings:
    """
    核心配置类
    统一管理所有配置模块
    """
    
    def __init__(self):
        """初始化配置"""
        self._llm = LLMConfig()
        self._rag = RAGConfig()
        self._path = PathConfig()
        self._log = LogConfig()
        self._service = ServiceConfig()
        
        # 确保目录存在
        self._path.ensure_directories()
    
    @property
    def llm(self) -> LLMConfig:
        """获取LLM配置"""
        return self._llm
    
    @property
    def rag(self) -> RAGConfig:
        """获取RAG配置"""
        return self._rag
    
    @property
    def path(self) -> PathConfig:
        """获取路径配置"""
        return self._path
    
    @property
    def log(self) -> LogConfig:
        """获取日志配置"""
        return self._log
    
    @property
    def service(self) -> ServiceConfig:
        """获取服务配置"""
        return self._service
    
    # 兼容性属性，保持旧版代码兼容
    @property
    def LOG_LEVEL(self) -> str:
        """兼容性属性：日志级别"""
        return self._log.level
    
    @property
    def LOG_FORMAT(self) -> str:
        """兼容性属性：日志格式"""
        return self._log.format
    
    @property
    def LOG_FILE(self) -> str:
        """兼容性属性：日志文件"""
        return self._log.file
    
    @property
    def LOG_DIR(self):
        """兼容性属性：日志目录"""
        return self._path.log_dir
    
    @property
    def USE_BM25(self) -> bool:
        """兼容性属性：是否使用BM25检索"""
        return self._rag.bm25.enabled
    
    @property
    def USE_RERANK(self) -> bool:
        """兼容性属性：是否使用重排序"""
        return self._rag.rerank.enabled
    
    @property
    def USE_SEMANTIC_SPLIT(self) -> bool:
        """兼容性属性：是否使用语义分割"""
        return self._rag.text_splitter.use_semantic_split
    
    @property
    def RRF_K(self) -> int:
        """兼容性属性：RRF参数k"""
        return self._rag.retrieval.rrf_k
    
    @property
    def SIMILARITY_THRESHOLD(self) -> float:
        """兼容性属性：相似度阈值"""
        return self._rag.retrieval.similarity_threshold
    
    @property
    def VECTOR_STORE_DIR(self):
        """兼容性属性：向量存储目录"""
        return self._path.vector_store_dir
    
    @property
    def LOG_MAX_BYTES(self) -> int:
        """兼容性属性：日志最大字节数"""
        return self._log.max_bytes
    
    @property
    def LOG_BACKUP_COUNT(self) -> int:
        """兼容性属性：日志备份数量"""
        return self._log.backup_count
    
    def validate(self) -> list:
        """
        验证所有配置
        
        Returns:
            错误列表
        """
        errors = []
        
        # 验证LLM配置
        errors.extend(self._llm.validate())
        
        # 验证RAG配置
        errors.extend(self._rag.validate())
        
        # 验证路径配置
        errors.extend(self._path.validate())
        
        # 验证日志配置
        errors.extend(self._log.validate())
        
        # 验证服务配置
        errors.extend(self._service.validate())
        
        return errors
    
    def is_valid(self) -> bool:
        """检查配置是否有效"""
        return len(self.validate()) == 0
    
    def check_config_status(self) -> Dict[str, Any]:
        """
        检查配置状态
        
        Returns:
            配置状态字典
        """
        return {
            "llm_provider": self._llm.provider,
            "deepseek_configured": self._llm.is_provider_configured("deepseek"),
            "ollama_configured": self._llm.is_provider_configured("ollama"),
            "rag_config_valid": self._rag.is_valid(),
            "directories_ready": self._path.project_root.exists(),
            "config_valid": self.is_valid()
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """
        将配置转换为字典（脱敏）
        
        Returns:
            配置字典
        """
        return {
            "llm": self._llm.get_masked_config(),
            "rag": self._rag.get_masked_config(),
            "path": self._path.get_masked_config(),
            "log": self._log.get_masked_config(),
            "service": self._service.get_masked_config()
        }
    
    def __str__(self) -> str:
        """字符串表示"""
        return f"Settings(provider={self._llm.provider}, debug={self._service.debug})"
    
    def __repr__(self) -> str:
        """调试表示"""
        return self.__str__()


# 全局配置实例（单例）
settings = Settings()


# 公开API
__all__ = [
    "Settings",
    "settings"
]
