"""
混合检索模块 (Hybrid Retriever)
结合向量检索（语义相似度）和BM25关键词检索（精确匹配）

v2.0 更新：
- 新增BM25关键词检索
- RRF (Reciprocal Rank Fusion) 融合排序
- 检索来源标记（vector / bm25 / hybrid）

v2.1 更新：
- BM25索引持久化到磁盘（pickle）
- 启动时自动从磁盘加载已有BM25索引
"""

import os
import pickle
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from .vector_store import similarity_search, load_vector_store, GlobalFAISS
from .bm25_retriever import BM25Retriever, reciprocal_rank_fusion

logger = logging.getLogger(__name__)

# 全局BM25检索器（按subject_id分组）
_bm25_retrievers: Dict[str, BM25Retriever] = {}

# BM25索引持久化根目录（使用绝对路径）
_BM25_INDEX_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bm25_index")


def _get_bm25_index_path(subject_id: str) -> Path:
    """获取指定学科的BM25索引文件路径"""
    return Path(_BM25_INDEX_DIR) / subject_id / "bm25.pkl"


def _save_bm25_index(subject_id: str) -> None:
    """
    将BM25索引持久化到磁盘
    
    保存内容：
    - documents: 原始Document对象列表
    - tokenized_corpus: 分词后的语料库
    """
    bm25 = _bm25_retrievers.get(subject_id)
    if bm25 is None or bm25.is_empty:
        return
    
    index_path = _get_bm25_index_path(subject_id)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        data = {
            "documents": bm25._documents,
            "tokenized_corpus": bm25._tokenized_corpus,
        }
        with open(index_path, 'wb') as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
        logger.info(f"BM25索引已持久化: {index_path} ({bm25.doc_count}个文档)")
    except Exception as e:
        logger.error(f"BM25索引持久化失败: {e}", exc_info=True)


def _load_bm25_index(subject_id: str) -> bool:
    """
    从磁盘加载BM25索引到内存
    
    Returns:
        是否加载成功
    """
    if subject_id in _bm25_retrievers and not _bm25_retrievers[subject_id].is_empty:
        return True  # 已在内存中
    
    index_path = _get_bm25_index_path(subject_id)
    if not index_path.exists():
        return False
    
    try:
        with open(index_path, 'rb') as f:
            data = pickle.load(f)
        
        documents = data.get("documents", [])
        tokenized_corpus = data.get("tokenized_corpus", [])
        
        if not documents:
            return False
        
        # 直接构造BM25Retriever，跳过add_documents的分词（已有tokenized_corpus）
        retriever = BM25Retriever.__new__(BM25Retriever)
        retriever._documents = documents
        retriever._tokenized_corpus = tokenized_corpus
        retriever._rebuild_index()
        
        _bm25_retrievers[subject_id] = retriever
        logger.info(f"BM25索引已从磁盘加载: {index_path} ({retriever.doc_count}个文档)")
        return True
    except Exception as e:
        logger.error(f"BM25索引加载失败: {e}", exc_info=True)
        return False


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
    
    # 3. 持久化BM25索引到磁盘（重启后可自动加载）
    _save_bm25_index(subject_id)
    
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
    
    # 2. BM25检索（如果内存中没有，尝试从磁盘自动加载）
    if use_bm25:
        if subject_id not in _bm25_retrievers:
            _load_bm25_index(subject_id)
        if subject_id in _bm25_retrievers:
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
    """获取检索器状态（含持久化信息）"""
    # 确保尝试从磁盘加载
    if subject_id not in _bm25_retrievers:
        _load_bm25_index(subject_id)
    
    bm25 = _bm25_retrievers.get(subject_id)
    faiss_store = GlobalFAISS.get(subject_id)
    
    bm25_disk_path = _get_bm25_index_path(subject_id)
    
    return {
        "subject_id": subject_id,
        "vector_store": {
            "available": faiss_store is not None,
            "document_count": faiss_store.count if faiss_store else 0
        },
        "bm25": {
            "available": bm25 is not None and not bm25.is_empty,
            "document_count": bm25.doc_count if bm25 else 0,
            "implementation": bm25.get_status().get('implementation', 'unknown') if bm25 else 'none',
            "persisted_on_disk": bm25_disk_path.exists(),
            "index_path": str(bm25_disk_path),
        },
        "hybrid_enabled": True,
        "rrf_enabled": True
    }


def clear_index(subject_id: str) -> None:
    """清除指定学科的混合索引（内存 + 磁盘）"""
    global _bm25_retrievers
    
    if subject_id in _bm25_retrievers:
        _bm25_retrievers[subject_id].clear()
        del _bm25_retrievers[subject_id]
    
    # 清理磁盘上的BM25索引文件
    index_path = _get_bm25_index_path(subject_id)
    if index_path.exists():
        try:
            index_path.unlink()
            # 清理空的父目录
            parent_dir = index_path.parent
            if parent_dir.exists() and not any(parent_dir.iterdir()):
                parent_dir.rmdir()
            logger.info(f"BM25磁盘索引已删除: {index_path}")
        except Exception as e:
            logger.warning(f"删除BM25磁盘索引失败: {e}")
    
    logger.info(f"混合索引已清除: subject={subject_id}")
