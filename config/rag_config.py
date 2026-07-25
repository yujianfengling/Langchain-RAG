"""
RAG配置模块
管理RAG相关的配置
"""

from typing import Optional, Dict, Any
from .base import BaseConfig, Field, PYDANTIC_V2


class RAGConfig(BaseConfig):
    """RAG配置"""
    
    # 检索配置
    top_k: int = Field(5, validation_alias="RAG_TOP_K")
    
    # 文本分割配置
    chunk_size: int = Field(500, validation_alias="CHUNK_SIZE")
    chunk_overlap: int = Field(50, validation_alias="CHUNK_OVERLAP")
    
    # Embedding配置
    embedding_model: str = Field("BAAI/bge-small-zh-v1.5", validation_alias="EMBEDDING_MODEL")
    embedding_device: str = Field("cpu", validation_alias="EMBEDDING_DEVICE")
    
    # 知识库隔离模式
    # directory: 每个学科独立目录 data/vector_store/{subject_id}/
    # collection: 单个数据库多个collection（不推荐）
    isolation_mode: str = Field("directory", validation_alias="RAG_ISOLATION_MODE")
    
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
        验证RAG配置
        
        Returns:
            错误列表
        """
        errors = []
        
        # 检查top_k
        if self.top_k <= 0:
            errors.append(f"RAG_TOP_K无效: {self.top_k}")
        
        if self.top_k > 20:
            errors.append(f"RAG_TOP_K过大: {self.top_k}，建议不超过20")
        
        # 检查chunk_size
        if self.chunk_size <= 0:
            errors.append(f"CHUNK_SIZE无效: {self.chunk_size}")
        
        if self.chunk_size < 100:
            errors.append(f"CHUNK_SIZE过小: {self.chunk_size}，建议至少100")
        
        if self.chunk_size > 2000:
            errors.append(f"CHUNK_SIZE过大: {self.chunk_size}，建议不超过2000")
        
        # 检查chunk_overlap
        if self.chunk_overlap < 0:
            errors.append(f"CHUNK_OVERLAP无效: {self.chunk_overlap}")
        
        if self.chunk_overlap >= self.chunk_size:
            errors.append(f"CHUNK_OVERLAP ({self.chunk_overlap}) >= CHUNK_SIZE ({self.chunk_size})，可能导致分割问题")
        
        # 检查embedding_device
        if self.embedding_device not in ["cpu", "cuda", "mps"]:
            errors.append(f"EMBEDDING_DEVICE无效: {self.embedding_device}，支持: cpu, cuda, mps")
        
        # 检查isolation_mode
        if self.isolation_mode not in ["directory", "collection"]:
            errors.append(f"RAG_ISOLATION_MODE无效: {self.isolation_mode}，支持: directory, collection")
        
        return errors
    
    def is_valid(self) -> bool:
        """检查配置是否有效"""
        return len(self.validate()) == 0
    
    @property
    def bm25(self):
        """BM25检索配置（兼容属性）"""
        class _BM25:
            enabled = True
            index_path = "data/bm25_index"
        return _BM25()
    
    @property
    def rerank(self):
        """重排序配置（兼容属性）"""
        class _Rerank:
            enabled = False
        return _Rerank()
    
    @property
    def text_splitter(self):
        """文本分割配置（兼容属性）"""
        class _TextSplitter:
            use_semantic_split = False
        return _TextSplitter()
    
    @property
    def retrieval(self):
        """检索配置（兼容属性）"""
        class _Retrieval:
            rrf_k = 60
            similarity_threshold = 0.5
        return _Retrieval()
    
    def get_masked_config(self) -> Dict[str, Any]:
        """获取配置字典"""
        return {
            "top_k": self.top_k,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "embedding_model": self.embedding_model,
            "embedding_device": self.embedding_device,
            "isolation_mode": self.isolation_mode
        }


# 公开API
__all__ = [
    "RAGConfig"
]
