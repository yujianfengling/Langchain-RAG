"""
配置管理模块
统一管理环境变量和系统配置

配置优先级：环境变量 > .env 文件 > config.json > 默认值
"""

import os
import json
from pathlib import Path
from typing import Optional, Dict, Any

# 尝试加载 .env 文件（如果存在）
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)  # override=False: 环境变量优先
except ImportError:
    pass  # python-dotenv 未安装时跳过

# 尝试加载 config.json（如果存在）
_config_json_path = Path(__file__).parent / "config.json"
_config_from_json: Dict[str, Any] = {}
if _config_json_path.exists():
    try:
        with open(_config_json_path, "r", encoding="utf-8") as f:
            _config_from_json = json.load(f)
    except Exception:
        pass

# 应用版本
APP_VERSION = "2.1.0"

# 全局超时配置（秒）
LLM_REQUEST_TIMEOUT: int = int(os.getenv("LLM_REQUEST_TIMEOUT", "120"))
RAG_REQUEST_TIMEOUT: int = int(os.getenv("RAG_REQUEST_TIMEOUT", "60"))


class Settings:
    """
    配置设置类
    集中管理所有配置项，支持环境变量覆盖
    """
    
    # ==================== 路径配置 ====================
    # 项目根目录
    PROJECT_ROOT: Path = Path(__file__).parent
    
    # 数据目录
    DATA_DIR: Path = PROJECT_ROOT / "data"
    
    # 向量存储目录
    VECTOR_STORE_PATH: Path = DATA_DIR / "vector_store"
    
    # 学科资料目录
    SUBJECTS_DATA_PATH: Path = DATA_DIR / "subjects"
    
    # 日志目录
    LOG_DIR: Path = PROJECT_ROOT / "logs"
    
    # ==================== LLM配置 ====================
    # LLM提供商（deepseek, ollama）
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "deepseek")
    
    # DeepSeek配置
    DEEPSEEK_API_KEY: Optional[str] = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    DEEPSEEK_API_BASE: str = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
    DEEPSEEK_TEMPERATURE: float = float(os.getenv("DEEPSEEK_TEMPERATURE", "0.7"))
    DEEPSEEK_MAX_TOKENS: int = int(os.getenv("DEEPSEEK_MAX_TOKENS", "2000"))
    
    # Ollama配置
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama2")
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_TEMPERATURE: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.7"))
    OLLAMA_MAX_TOKENS: int = int(os.getenv("OLLAMA_MAX_TOKENS", "2000"))
    
    # ==================== RAG配置 ====================
    # 默认检索文档数量
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "5"))
    
    # 向量检索相似度阈值（低于此分数的结果将被过滤）
    SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.3"))
    
    # 是否启用BM25关键词检索（混合检索模式）
    USE_BM25: bool = os.getenv("USE_BM25", "true").lower() == "true"
    
    # 是否启用Rerank重排序
    USE_RERANK: bool = os.getenv("USE_RERANK", "true").lower() == "true"
    
    # RRF融合参数（Reciprocal Rank Fusion）
    RRF_K: int = int(os.getenv("RRF_K", "60"))
    
    # 是否使用语义感知切分
    USE_SEMANTIC_SPLIT: bool = os.getenv("USE_SEMANTIC_SPLIT", "true").lower() == "true"
    
    # 文本分割配置
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "500"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "50"))
    
    # Embedding模型配置（默认使用中文优化模型）
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    EMBEDDING_DEVICE: str = os.getenv("EMBEDDING_DEVICE", "cpu")
    
    # ==================== 日志配置 ====================
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_FILE: Optional[str] = os.getenv("LOG_FILE", None)
    
    # ==================== 服务配置 ====================
    # 服务端口（未来Web UI使用）
    SERVICE_PORT: int = int(os.getenv("SERVICE_PORT", "8000"))
    SERVICE_HOST: str = os.getenv("SERVICE_HOST", "0.0.0.0")
    
    # 是否启用调试模式
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    
    # CORS配置
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", "*").split(",")
    
    @classmethod
    def get_llm_config(cls, provider: Optional[str] = None) -> Dict[str, Any]:
        """
        获取LLM配置
        
        Args:
            provider: LLM提供商名称，如果为None则使用当前配置的提供商
            
        Returns:
            LLM配置字典
        """
        provider = provider or cls.LLM_PROVIDER
        
        if provider == "deepseek":
            return {
                "provider": "deepseek",
                "api_key": cls.DEEPSEEK_API_KEY,
                "model": cls.DEEPSEEK_MODEL,
                "api_base": cls.DEEPSEEK_API_BASE,
                "temperature": cls.DEEPSEEK_TEMPERATURE,
                "max_tokens": cls.DEEPSEEK_MAX_TOKENS
            }
        elif provider == "ollama":
            return {
                "provider": "ollama",
                "model": cls.OLLAMA_MODEL,
                "base_url": cls.OLLAMA_BASE_URL,
                "temperature": cls.OLLAMA_TEMPERATURE,
                "max_tokens": cls.OLLAMA_MAX_TOKENS
            }
        else:
            raise ValueError(f"不支持的LLM提供商: {provider}")
    
    @classmethod
    def get_rag_config(cls) -> Dict[str, Any]:
        """
        获取RAG配置
        
        Returns:
            RAG配置字典
        """
        return {
            "top_k": cls.RAG_TOP_K,
            "similarity_threshold": cls.SIMILARITY_THRESHOLD,
            "use_bm25": cls.USE_BM25,
            "use_rerank": cls.USE_RERANK,
            "rrf_k": cls.RRF_K,
            "use_semantic_split": cls.USE_SEMANTIC_SPLIT,
            "chunk_size": cls.CHUNK_SIZE,
            "chunk_overlap": cls.CHUNK_OVERLAP,
            "embedding_model": cls.EMBEDDING_MODEL,
            "embedding_device": cls.EMBEDDING_DEVICE,
            "vector_store_path": str(cls.VECTOR_STORE_PATH),
            "subjects_data_path": str(cls.SUBJECTS_DATA_PATH)
        }
    
    @classmethod
    def get_path_config(cls) -> Dict[str, str]:
        """
        获取路径配置
        
        Returns:
            路径配置字典
        """
        return {
            "project_root": str(cls.PROJECT_ROOT),
            "data_dir": str(cls.DATA_DIR),
            "vector_store_path": str(cls.VECTOR_STORE_PATH),
            "subjects_data_path": str(cls.SUBJECTS_DATA_PATH),
            "log_dir": str(cls.LOG_DIR)
        }
    
    @classmethod
    def ensure_directories(cls) -> None:
        """
        确保必要的目录存在
        """
        directories = [
            cls.DATA_DIR,
            cls.VECTOR_STORE_PATH,
            cls.SUBJECTS_DATA_PATH,
            cls.LOG_DIR
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def validate_config(cls) -> Dict[str, Any]:
        """
        验证配置的有效性
        
        Returns:
            验证结果字典
        """
        validation = {
            "valid": True,
            "errors": [],
            "warnings": []
        }
        
        # 检查DeepSeek API Key
        if cls.LLM_PROVIDER == "deepseek" and not cls.DEEPSEEK_API_KEY:
            validation["warnings"].append("DEEPSEEK_API_KEY未设置，DeepSeek LLM将不可用")
        
        # 检查路径
        if not cls.DATA_DIR.exists():
            validation["warnings"].append(f"数据目录不存在: {cls.DATA_DIR}")
        
        # 检查温度范围
        if not 0 <= cls.DEEPSEEK_TEMPERATURE <= 2:
            validation["errors"].append(f"DeepSeek温度参数无效: {cls.DEEPSEEK_TEMPERATURE}")
            validation["valid"] = False
        
        if not 0 <= cls.OLLAMA_TEMPERATURE <= 2:
            validation["errors"].append(f"Ollama温度参数无效: {cls.OLLAMA_TEMPERATURE}")
            validation["valid"] = False
        
        # 检查max_tokens
        if cls.DEEPSEEK_MAX_TOKENS <= 0:
            validation["errors"].append(f"DeepSeek max_tokens无效: {cls.DEEPSEEK_MAX_TOKENS}")
            validation["valid"] = False
        
        if cls.OLLAMA_MAX_TOKENS <= 0:
            validation["errors"].append(f"Ollama max_tokens无效: {cls.OLLAMA_MAX_TOKENS}")
            validation["valid"] = False
        
        # 检查RAG配置
        if cls.RAG_TOP_K <= 0:
            validation["errors"].append(f"RAG_TOP_K无效: {cls.RAG_TOP_K}")
            validation["valid"] = False
        
        if cls.CHUNK_SIZE <= 0:
            validation["errors"].append(f"CHUNK_SIZE无效: {cls.CHUNK_SIZE}")
            validation["valid"] = False
        
        if cls.CHUNK_OVERLAP < 0:
            validation["errors"].append(f"CHUNK_OVERLAP无效: {cls.CHUNK_OVERLAP}")
            validation["valid"] = False
        
        if cls.CHUNK_OVERLAP >= cls.CHUNK_SIZE:
            validation["warnings"].append("CHUNK_OVERLAP >= CHUNK_SIZE，可能导致分割问题")
        
        return validation
    
    class _LLMNamespace:
        """LLM配置命名空间"""
        def __init__(self, settings_cls):
            self._cls = settings_cls
        @property
        def provider(self) -> str:
            return self._cls.LLM_PROVIDER
    
    class _RAGNamespace:
        """RAG配置命名空间"""
        def __init__(self, settings_cls):
            self._cls = settings_cls
        @property
        def top_k(self) -> int:
            return self._cls.RAG_TOP_K
    
    @property
    def llm(self):
        return self._LLMNamespace(self)
    
    @property
    def rag(self):
        return self._RAGNamespace(self)
    
    @classmethod
    def to_dict(cls) -> Dict[str, Any]:
        """
        将所有配置转换为字典
        
        Returns:
            配置字典
        """
        return {
            "paths": cls.get_path_config(),
            "llm": cls.get_llm_config(),
            "rag": cls.get_rag_config(),
            "log": {
                "level": cls.LOG_LEVEL,
                "format": cls.LOG_FORMAT,
                "file": cls.LOG_FILE
            },
            "service": {
                "port": cls.SERVICE_PORT,
                "host": cls.SERVICE_HOST,
                "debug": cls.DEBUG
            }
        }


# 创建全局配置实例
settings = Settings()


# 便捷函数
def get_settings() -> Settings:
    """
    获取全局配置实例
    
    Returns:
        Settings实例
    """
    return settings


def init_config() -> Dict[str, Any]:
    """
    初始化配置（确保目录存在并验证配置）
    
    Returns:
        初始化结果
    """
    # 确保目录存在
    settings.ensure_directories()
    
    # 验证配置
    validation = settings.validate_config()
    
    return {
        "initialized": True,
        "validation": validation,
        "config": settings.to_dict()
    }


# 公开API
__all__ = [
    "Settings",
    "settings",
    "get_settings",
    "init_config",
    "APP_VERSION",
    "LLM_REQUEST_TIMEOUT",
    "RAG_REQUEST_TIMEOUT",
]