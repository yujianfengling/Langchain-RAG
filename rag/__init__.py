"""
RAG模块
提供检索增强生成(Retrieval-Augmented Generation)功能

v2.0 更新：
- 混合检索（向量 + BM25）
- RRF融合排序
- 语义感知切分
- Rerank重排序

v2.3.0 更新：
- 架构重构：新增 loaders 目录，统一文档加载器接口
- 支持 TXT、PDF、图片等多种格式
"""

# 版本信息
__version__ = "2.3.0"

# 模块导入
from .document import load_document, load_documents, get_supported_extensions
from .embedding import get_embedding_model, embed_documents, embed_query
from .text_splitter import split_documents, split_text
from .vector_store import (
    create_vector_store,
    load_vector_store,
    add_documents,
    similarity_search,
    get_retriever
)
from .rag_service import (
    build_knowledge_base,
    load_knowledge_base,
    retrieve_context,
    build_context,
    rerank_documents,
    get_subject_retriever,
    list_available_subjects,
    get_knowledge_base_info,
    delete_knowledge_base,
    rebuild_knowledge_base,
    check_rag_service_status
)

# 向后兼容别名（健康检查使用）
check_rag_health = check_rag_service_status

# 混合检索模块（可选导入）
try:
    from .hybrid_retriever import build_index as build_hybrid_index, retrieve as hybrid_retrieve
    from .bm25_retriever import BM25Retriever, reciprocal_rank_fusion
    HYBRID_AVAILABLE = True
except ImportError:
    HYBRID_AVAILABLE = False

# 公开API
__all__ = [
    # 文档加载
    "load_document",
    "load_documents",
    "get_supported_extensions",
    
    # 文本分割
    "split_documents",
    "split_text",
    
    # Embedding
    "get_embedding_model",
    "embed_documents",
    "embed_query",
    
    # 向量存储
    "create_vector_store",
    "load_vector_store",
    "add_documents",
    "similarity_search",
    "get_retriever",
    
    # RAG服务
    "build_knowledge_base",
    "load_knowledge_base",
    "retrieve_context",
    "build_context",
    "rerank_documents",
    "get_subject_retriever",
    "list_available_subjects",
    "get_knowledge_base_info",
    "delete_knowledge_base",
    "rebuild_knowledge_base",
    "check_rag_service_status",
    
    # 混合检索（可选）
    "build_hybrid_index",
    "hybrid_retrieve",
    "BM25Retriever",
    "reciprocal_rank_fusion",
]