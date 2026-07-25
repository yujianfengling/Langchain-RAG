"""
混合检索模块 (Hybrid Retriever)
结合向量检索（语义相似度）和BM25关键词检索（精确匹配）

v2.0 更新：
- 新增BM25关键词检索
- RRF (Reciprocal Rank Fusion) 融合排序
- 检索来源标记（vector / bm25 / hybrid）
"""

import logging
from typing import List, Dict, Any, Optional

from .vector_store import similarity_search, load_vector_store, GlobalFAISS
from .bm25_retriever import BM25Retriever, reciprocal_rank_fusion

logger = logging.getLogger(__name__)

# 全局BM25检索器（按subject_id分组）
_bm25_retrievers: Dict[str, BM25Retriever] = {}


def build_index(
    subject_id: str,
    documents: List[Any],
    embeddings: Any = None
) -> Dict[str, Any]:
    """
    为指定学科构建混合检索索引（向量索引 + BM25索引）
    
    Args:
        subject_id: 学科ID
        documents: Document对象列表
        embeddings: Embedding模型（可选）
        
    Returns:
        索引构建结果
    """
    global _bm25_retrievers
    
    # 1. FAISS已由create_vector_store建好并持久化，此处不加载（避免embeddings=None导致后续检索失败）
    
    # 2. 构建BM25索引
    bm25 = BM25Retriever(documents)
    _bm25_retrievers[subject_id] = bm25
    
    logger.info(f"BM25索引构建完成: subject={subject_id}, BM25文档={bm25.doc_count}")
    
    return {
        "subject_id": subject_id,
        "vector_count": len(documents),
        "bm25_count": bm25.doc_count,
        "total_documents": len(documents)
    }


def retrieve(
    query: str,
    subject_id: str = "default",
    top_k: int = 5,
    threshold: float = 0.3,
    use_bm25: bool = True,
    use_vector: bool = True,
    rrf_k: int = 60
) -> List[Dict[str, Any]]:
    """
    执行混合检索：向量检索 + BM25关键词检索，RRF融合排序
    
    Args:
        query: 用户查询
        subject_id: 学科ID
        top_k: 返回结果数量
        threshold: 向量检索相似度阈值
        use_bm25: 是否启用BM25检索
        use_vector: 是否启用向量检索
        rrf_k: RRF参数
        
    Returns:
        检索结果列表，每个元素包含：
        - content: 文本内容
        - source: 来源文件
        - score: 融合分数
        - retrieval_type: "vector" | "bm25" | "hybrid"
    """
    result_lists = []
    
    # 1. 向量检索
    if use_vector:
        vector_results = _vector_search(query, subject_id, top_k, threshold)
        result_lists.append(vector_results)
        logger.debug(f"向量检索: {len(vector_results)}个结果")
    
    # 2. BM25检索
    if use_bm25 and subject_id in _bm25_retrievers:
        bm25_results = _bm25_search(query, subject_id, top_k)
        result_lists.append(bm25_results)
        logger.debug(f"BM25检索: {len(bm25_results)}个结果")
    
    # 3. 结果融合
    if not result_lists:
        return []
    
    if len(result_lists) == 1:
        # 单路检索，直接返回
        fused = result_lists[0][:top_k]
    else:
        # 多路检索，RRF融合
        fused = reciprocal_rank_fusion(result_lists, k=rrf_k)[:top_k]
    
    # 4. 格式化输出
    formatted = []
    for item in fused:
        doc = item.get('document')
        if not doc:
            continue
        
        content = doc.page_content if hasattr(doc, 'page_content') else str(doc)
        metadata = doc.metadata if hasattr(doc, 'metadata') else {}
        
        formatted.append({
            'content': content,
            'source': metadata.get('source', metadata.get('file_name', '未知来源')),
            'section': metadata.get('section', ''),
            'score': item.get('score', 0),
            'retrieval_type': item.get('retrieval_type', 'unknown'),
            'original_scores': item.get('original_scores', {}),
            'metadata': metadata
        })
    
    return formatted


def _vector_search(
    query: str,
    subject_id: str,
    top_k: int,
    threshold: float
) -> List[Dict[str, Any]]:
    """执行向量检索"""
    try:
        from .embedding import get_embedding_model
        embeddings = get_embedding_model()
        vector_store = GlobalFAISS.get(subject_id, embeddings)
        if vector_store is None:
            return []
        results = vector_store.similarity_search(query, k=top_k)
        
        formatted = []
        for i, result in enumerate(results):
            content = ""
            source = ""
            metadata = {}
            
            if isinstance(result, dict):
                content = result.get('content', result.get('text', ''))
                source = result.get('source', result.get('metadata', {}).get('source', ''))
                score = result.get('score', result.get('similarity', 0))
                metadata = result.get('metadata', {})
            elif hasattr(result, 'page_content'):
                content = result.page_content
                source = result.metadata.get('source', result.metadata.get('file_name', ''))
                score = result.metadata.get('score', 0)
                metadata = result.metadata
            elif isinstance(result, tuple) and len(result) >= 2:
                content = result[0]
                score = result[1] if len(result) > 1 else 0
                source = result[2] if len(result) > 2 else ''
                if isinstance(source, dict):
                    metadata = source
                    source = metadata.get('source', metadata.get('file_name', ''))
            
            if content:
                formatted.append({
                    'document': _make_simple_doc(content, metadata),
                    'score': float(score) if score else 0,
                    'retrieval_type': 'vector'
                })
        
        return formatted
        
    except Exception as e:
        logger.error(f"向量检索失败: {str(e)}", exc_info=True)
        return []


def _bm25_search(
    query: str,
    subject_id: str,
    top_k: int
) -> List[Dict[str, Any]]:
    """执行BM25检索"""
    bm25 = _bm25_retrievers.get(subject_id)
    if not bm25 or bm25.is_empty:
        return []
    
    try:
        results = bm25.search(query, top_k=top_k)
        return results
    except Exception as e:
        logger.error(f"BM25检索失败: {str(e)}", exc_info=True)
        return []


def _make_simple_doc(content: str, metadata: dict):
    """创建简单的Document对象"""
    try:
        from langchain_core.documents import Document
        return Document(page_content=content, metadata=metadata)
    except ImportError:
        class SimpleDoc:
            def __init__(self, page_content, metadata):
                self.page_content = page_content
                self.metadata = metadata
        return SimpleDoc(content, metadata)


def get_retriever_status(subject_id: str) -> Dict[str, Any]:
    """获取检索器状态"""
    bm25 = _bm25_retrievers.get(subject_id)
    faiss_store = GlobalFAISS.get(subject_id)
    
    return {
        "subject_id": subject_id,
        "vector_store": {
            "available": faiss_store is not None,
            "document_count": faiss_store.count if faiss_store else 0
        },
        "bm25": {
            "available": bm25 is not None and not bm25.is_empty,
            "document_count": bm25.doc_count if bm25 else 0,
            "implementation": bm25.get_status().get('implementation', 'unknown') if bm25 else 'none'
        },
        "hybrid_enabled": True,
        "rrf_enabled": True
    }


def clear_index(subject_id: str) -> None:
    """清除指定学科的混合索引"""
    global _bm25_retrievers
    
    if subject_id in _bm25_retrievers:
        _bm25_retrievers[subject_id].clear()
        del _bm25_retrievers[subject_id]
    
    logger.info(f"混合索引已清除: subject={subject_id}")
