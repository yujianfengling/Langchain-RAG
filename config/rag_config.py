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

    # 最小 chunk 字符数（v2.6）：过滤 PDF/DOCX 抽取产生的单字符与纯元数据碎片。
    # 实测端到端（覆盖率×recall@5）：63.6%(=0) → 87.9%(=60) → 89.4%(=90) → 90.9%(=120)
    # 超过 150 后覆盖率开始下滑（金标 chunk 被切碎丢弃），端到端反而变差。
    chunk_min_length: int = Field(90, validation_alias="CHUNK_MIN_LENGTH")

    # 保留比例下限：低于此值时放宽长度阈值并告警，避免知识库被静默削空
    chunk_min_keep_ratio: float = Field(0.15, validation_alias="CHUNK_MIN_KEEP_RATIO")

    # 检索深度（送入 rerank 前的候选数）
    retrieval_pool: int = Field(10, validation_alias="RAG_RETRIEVAL_POOL")

    # 混合检索开关（v2.6：由环境变量控制，此前是硬编码匿名类）
    use_bm25: bool = Field(True, validation_alias="RAG_USE_BM25")
    use_rerank: bool = Field(True, validation_alias="RAG_USE_RERANK")
    use_semantic_split: bool = Field(True, validation_alias="RAG_USE_SEMANTIC_SPLIT")
    rrf_k: int = Field(60, validation_alias="RRF_K")
    similarity_threshold: float = Field(0.5, validation_alias="SIMILARITY_THRESHOLD")

    # 中文分词模式（v2.6）：char=逐字（旧行为）；word=jieba 词级（实测与 BM25 结合可到 97.0%）
    bm25_tokenizer: str = Field("word", validation_alias="BM25_TOKENIZER")
    
    # Embedding配置
    embedding_model: str = Field("BAAI/bge-small-zh-v1.5", validation_alias="EMBEDDING_MODEL")
    # v2.6：auto 时自动探测 cuda/mps；此前 rag/embedding.py 硬编码 cpu，
    # 导致该配置项完全无效、"上 GPU"拿不到任何收益
    embedding_device: str = Field("auto", validation_alias="EMBEDDING_DEVICE")

    # Embedding加载模式 (auto, offline, online)
    # auto: 显式本地目录 > 本地缓存 > 在线（默认）
    # offline: 仅本地，不访问网络
    # online: 允许在线加载（仍优先本地缓存）
    embedding_load_mode: str = Field("auto", validation_alias="EMBEDDING_LOAD_MODE")
    
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

        # 检查chunk_min_length（v2.6）
        if self.chunk_min_length < 0:
            errors.append(f"CHUNK_MIN_LENGTH无效: {self.chunk_min_length}")
        if self.chunk_min_length >= self.chunk_size:
            errors.append(
                f"CHUNK_MIN_LENGTH ({self.chunk_min_length}) >= CHUNK_SIZE ({self.chunk_size})，"
                f"会导致所有 chunk 都被过滤掉")

        # 检查检索参数
        if self.retrieval_pool <= 0:
            errors.append(f"RAG_RETRIEVAL_POOL无效: {self.retrieval_pool}")
        if self.retrieval_pool < self.top_k:
            errors.append(
                f"RAG_RETRIEVAL_POOL ({self.retrieval_pool}) < RAG_TOP_K ({self.top_k})，"
                f"精排候选不足")

        if not 0.0 <= self.similarity_threshold <= 1.0:
            errors.append(
                f"SIMILARITY_THRESHOLD 应在 [0,1]: {self.similarity_threshold}")

        if self.bm25_tokenizer not in ("char", "word"):
            errors.append(
                f"BM25_TOKENIZER无效: {self.bm25_tokenizer}，支持: char, word")
        
        # 检查embedding_device
        if self.embedding_device not in ["auto", "cpu", "cuda", "mps"]:
            errors.append(
                f"EMBEDDING_DEVICE无效: {self.embedding_device}，支持: auto, cpu, cuda, mps")

        # 检查embedding_load_mode
        if self.embedding_load_mode not in ["auto", "offline", "online"]:
            errors.append(f"EMBEDDING_LOAD_MODE无效: {self.embedding_load_mode}，支持: auto, offline, online")
        
        # 检查isolation_mode
        if self.isolation_mode not in ["directory", "collection"]:
            errors.append(f"RAG_ISOLATION_MODE无效: {self.isolation_mode}，支持: directory, collection")
        
        return errors
    
    def is_valid(self) -> bool:
        """检查配置是否有效"""
        return len(self.validate()) == 0
    
    @property
    def bm25(self):
        """BM25检索配置（v2.6：改为读真实字段，不再硬编码）"""
        cfg = self

        class _BM25:
            enabled = cfg.use_bm25
            index_path = "data/bm25_index"
            tokenizer = cfg.bm25_tokenizer
        return _BM25()
    
    @property
    def rerank(self):
        """重排序配置（v2.6：改为读真实字段）"""
        cfg = self

        class _Rerank:
            enabled = cfg.use_rerank
        return _Rerank()
    
    @property
    def text_splitter(self):
        """文本分割配置（v2.6：改为读真实字段）"""
        cfg = self

        class _TextSplitter:
            use_semantic_split = cfg.use_semantic_split
            chunk_size = cfg.chunk_size
            chunk_overlap = cfg.chunk_overlap
            chunk_min_length = cfg.chunk_min_length
        return _TextSplitter()
    
    @property
    def retrieval(self):
        """检索配置（v2.6：改为读真实字段）"""
        cfg = self

        class _Retrieval:
            rrf_k = cfg.rrf_k
            similarity_threshold = cfg.similarity_threshold
            pool = cfg.retrieval_pool
        return _Retrieval()
    
    def get_masked_config(self) -> Dict[str, Any]:
        """获取配置字典"""
        return {
            "top_k": self.top_k,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "chunk_min_length": self.chunk_min_length,
            "retrieval_pool": self.retrieval_pool,
            "use_bm25": self.use_bm25,
            "use_rerank": self.use_rerank,
            "use_semantic_split": self.use_semantic_split,
            "rrf_k": self.rrf_k,
            "similarity_threshold": self.similarity_threshold,
            "bm25_tokenizer": self.bm25_tokenizer,
            "embedding_model": self.embedding_model,
            "embedding_device": self.embedding_device,
            "embedding_load_mode": self.embedding_load_mode,
            "isolation_mode": self.isolation_mode,
        }


# 公开API
__all__ = [
    "RAGConfig"
]
