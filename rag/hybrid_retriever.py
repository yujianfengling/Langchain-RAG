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
import json
import pickle
import logging
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional

from .vector_store import similarity_search, load_vector_store, GlobalFAISS
from .bm25_retriever import BM25Retriever, reciprocal_rank_fusion

# 导入路径安全转换（FAISS不支持中文路径）
from .rag_service import _safe_dir_name

logger = logging.getLogger(__name__)

# 全局BM25检索器（按subject_id分组）
_bm25_retrievers: Dict[str, BM25Retriever] = {}

# BM25索引持久化根目录（**运行时解析**，与向量库同源）
#
# 修复（2026-10-01）：此处原先硬编码 `<项目根>/data/bm25_index`，绕过了 `EDU_DATA_DIR`。
# 后果：隔离数据目录下"写入隔离目录、检索读正式目录"，隔离环境验证不出真实检索行为，
# 且会把索引落到正式 data/。与 rag/vector_store.py 采用同一套运行时解析方式。
def _bm25_index_dir() -> str:
    from config.path_config import get_data_dir
    return str(get_data_dir() / "bm25_index")

# BM25 索引 schema 版本（v2.6）：分词方式或结构变更时递增，强制重建
BM25_INDEX_VERSION = 2

# BM25 索引随附的元数据文件（记录本次构建的 build_id / 文档数 / 分词器），
# 供原子发布时与 FAISS 的 meta.json 做"同一版本"校验。
BM25_META_FILE = "bm25_meta.json"


def read_bm25_meta(bm25_dir: str) -> Dict[str, Any]:
    """读取 BM25 索引元数据（不存在或损坏返回空 dict）"""
    path = Path(bm25_dir) / BM25_META_FILE
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"读取 BM25 元数据失败: {e}")
        return {}

# ============ 检索诊断状态（线程安全） ============
# retrieval_mode: hybrid_ready | vector_only | bm25_degraded | unavailable
# fallback_reason: embedding_load_failed | faiss_index_missing | vector_search_failed | None
_retrieval_lock = threading.Lock()
_retrieval_info: Dict[str, Any] = {
    "retrieval_mode": "unavailable",
    "vector_available": False,
    "bm25_available": False,
    "vector_result_count": 0,
    "bm25_result_count": 0,
    "fallback_reason": None,
}


def get_retrieval_diagnostics() -> Dict[str, Any]:
    """
    获取最近一次混合检索的诊断信息（线程安全）

    Returns:
        {
            "retrieval_mode": "hybrid_ready"|"vector_only"|"bm25_degraded"|"unavailable",
            "vector_available": bool,
            "bm25_available": bool,
            "vector_result_count": int,
            "bm25_result_count": int,
            "fallback_reason": str|None,
        }
    """
    with _retrieval_lock:
        return dict(_retrieval_info)


def _reset_retrieval_diagnostics() -> None:
    """重置检索诊断（每次检索前调用）"""
    with _retrieval_lock:
        _retrieval_info.update({
            "retrieval_mode": "unavailable",
            "vector_available": False,
            "bm25_available": False,
            "vector_result_count": 0,
            "bm25_result_count": 0,
            "candidate_count": 0,
            "rerank_available": False,
            "fallback_reason": None,
        })


def _finalize_retrieval_mode() -> None:
    """根据可用性计算最终retrieval_mode"""
    with _retrieval_lock:
        vector_ok = _retrieval_info["vector_available"]
        bm25_ok = _retrieval_info["bm25_available"]
        if vector_ok and bm25_ok:
            _retrieval_info["retrieval_mode"] = "hybrid_ready"
        elif vector_ok and not bm25_ok:
            _retrieval_info["retrieval_mode"] = "vector_only"
        elif not vector_ok and bm25_ok:
            _retrieval_info["retrieval_mode"] = "bm25_degraded"
        else:
            _retrieval_info["retrieval_mode"] = "unavailable"


def _get_bm25_index_path(subject_id: str) -> Path:
    """获取指定学科的BM25索引文件路径（按当前数据根目录运行时解析）"""
    return Path(_bm25_index_dir()) / subject_id / "bm25.pkl"


def _save_bm25_index(
    subject_id: str,
    bm25: Optional[BM25Retriever] = None,
    index_path: Optional[Path] = None,
    build_id: Optional[str] = None,
) -> None:
    """
    将BM25索引持久化到磁盘

    保存内容：
    - documents: 原始Document对象列表
    - tokenized_corpus: 分词后的语料库
    - tokenizer_mode / tokenizer_version: 供加载时校验（v2.6）

    参数（原子发布新增，均为可选，默认行为不变）：
    - bm25: 待持久化的检索器；缺省取内存缓存中的实例（旧调用方式）；
    - index_path: 输出文件路径；缺省用默认 BM25 目录。原子发布传 staging
      目录，使构建期间线上 BM25 索引保持原样；
    - build_id: 本次构建版本号，写入同目录 bm25_meta.json，与 FAISS meta.json
      的 build_id 对齐，确保"同一版本"。
    """
    if bm25 is None:
        bm25 = _bm25_retrievers.get(subject_id)
    if bm25 is None or bm25.is_empty:
        return

    if index_path is None:
        index_path = _get_bm25_index_path(subject_id)
    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        data = {
            "documents": bm25._documents,
            "tokenized_corpus": bm25._tokenized_corpus,
            # v2.6：记录分词模式。换分词器后旧语料与新查询 token 空间不一致，
            # 会导致 BM25 静默失效（词级查询查字级语料，几乎命中不到）
            "tokenizer_mode": _current_tokenizer_mode(),
            "tokenizer_version": BM25_INDEX_VERSION,
        }
        with open(index_path, 'wb') as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)

        # 原子写 bm25_meta.json（与 bm25.pkl 同目录、同一次构建）
        meta_path = index_path.parent / BM25_META_FILE
        tmp_meta = str(meta_path) + ".tmp"
        with open(tmp_meta, "w", encoding="utf-8") as f:
            json.dump({
                "build_id": build_id,
                "documents": bm25.doc_count,
                "tokenizer_mode": data["tokenizer_mode"],
                "tokenizer_version": data["tokenizer_version"],
            }, f, ensure_ascii=False, indent=1)
        os.replace(tmp_meta, meta_path)

        logger.info(f"BM25索引已持久化: {index_path} ({bm25.doc_count}个文档, "
                    f"tokenizer={data['tokenizer_mode']}, build_id={build_id})")
    except Exception as e:
        logger.error(f"BM25索引持久化失败: {e}", exc_info=True)


def _current_tokenizer_mode() -> str:
    """读取当前分词模式（用于索引校验）"""
    try:
        from .bm25_retriever import get_tokenizer_mode
        return get_tokenizer_mode()
    except Exception:
        return "char"


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

        # v2.6：分词器一致性校验。不匹配时明确告警并拒绝加载，
        # 避免"新分词器查询旧语料"导致的静默零命中。
        # 注意：缺少 tokenizer_version 的索引属于 v2.6 之前的字级语料，
        # 必须重建，否则词级查询会在字级语料上几乎命中不到。
        saved_mode = data.get("tokenizer_mode")
        cur_mode = _current_tokenizer_mode()
        saved_ver = data.get("tokenizer_version")
        if saved_ver != BM25_INDEX_VERSION or saved_mode != cur_mode:
            logger.warning(
                f"BM25 索引分词器不匹配/版本过旧，需重建知识库: "
                f"索引(mode={saved_mode}, ver={saved_ver}) vs "
                f"当前(mode={cur_mode}, ver={BM25_INDEX_VERSION}) subject={subject_id}")
            return False
        
        if not documents:
            return False
        
        # 直接构造BM25Retriever，跳过add_documents的分词（已有tokenized_corpus）
        retriever = BM25Retriever.from_persisted(documents, tokenized_corpus)
        
        _bm25_retrievers[subject_id] = retriever
        logger.info(f"BM25索引已从磁盘加载: {index_path} ({retriever.doc_count}个文档)")
        return True
    except Exception as e:
        logger.error(f"BM25索引加载失败: {e}", exc_info=True)
        return False


def build_index(
    subject_id: str,
    documents: List[Any],
    embeddings: Any = None,
    *,
    bm25_dir: Optional[str] = None,
    update_cache: bool = True,
    build_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    为指定学科构建混合检索索引（向量索引 + BM25索引）
    
    Args:
        subject_id: 学科ID
        documents: Document对象列表
        embeddings: Embedding模型（可选）
        bm25_dir: BM25 输出目录（可选）。原子发布传 staging 目录，
                  使构建期间线上 BM25 索引保持原样；缺省用默认目录。
        update_cache: 是否更新进程内 BM25 缓存。原子发布传 False，
                  避免构建中的新版本提前覆盖正在服务的旧版本。
        build_id: 本次构建版本号（写入 bm25_meta.json）。
        
    Returns:
        索引构建结果
    """
    global _bm25_retrievers
    
    # 1. FAISS已由create_vector_store建好并持久化，此处不加载（避免embeddings=None导致后续检索失败）
    
    # 2. 构建BM25索引
    bm25 = BM25Retriever(documents)
    if update_cache:
        _bm25_retrievers[subject_id] = bm25
    
    # 3. 持久化BM25索引到磁盘（重启后可自动加载）
    #    原子发布：写入指定的 staging 目录，线上目录不动、缓存不动
    index_path = (Path(bm25_dir) / "bm25.pkl") if bm25_dir else None
    _save_bm25_index(subject_id, bm25=bm25, index_path=index_path, build_id=build_id)
    
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
    _reset_retrieval_diagnostics()
    from . import reranker
    rerank_on = reranker.enabled()
    candidate_k = max(top_k * 4, 20) if rerank_on else top_k

    # 1. 向量检索（返回 (results, reason)；reason为None表示成功）
    if use_vector:
        vector_results, vector_reason = _vector_search(query, subject_id, candidate_k, threshold)
        if vector_reason is not None:
            # 向量检索不可用：明确记录降级原因，不参与融合
            with _retrieval_lock:
                _retrieval_info["vector_available"] = False
                _retrieval_info["fallback_reason"] = vector_reason
            logger.warning(f"向量检索不可用，降级为关键词检索: reason={vector_reason}")
        else:
            with _retrieval_lock:
                _retrieval_info["vector_available"] = True
                _retrieval_info["vector_result_count"] = len(vector_results)
            result_lists.append(vector_results)
            logger.debug(f"向量检索: {len(vector_results)}个结果")

    # 2. BM25检索（如果内存中没有，尝试从磁盘自动加载）
    if use_bm25:
        if subject_id not in _bm25_retrievers:
            _load_bm25_index(subject_id)
        if subject_id in _bm25_retrievers:
            bm25_results = _bm25_search(query, subject_id, candidate_k)
            result_lists.append(bm25_results)
            with _retrieval_lock:
                _retrieval_info["bm25_available"] = True
                _retrieval_info["bm25_result_count"] = len(bm25_results)
            logger.debug(f"BM25检索: {len(bm25_results)}个结果")
        else:
            with _retrieval_lock:
                _retrieval_info["bm25_available"] = False

    # 3. 结果融合
    if not result_lists:
        _finalize_retrieval_mode()
        return []

    if len(result_lists) == 1:
        # 单路检索：保留候选，交给重排后截断
        fused = result_lists[0]
    else:
        # 多路检索，RRF融合（不先截断，否则重排没有候选空间）
        fused = reciprocal_rank_fusion(result_lists, k=rrf_k)

    with _retrieval_lock:
        _retrieval_info["candidate_count"] = len(fused)
    if rerank_on:
        fused, rerank_used = reranker.rerank(query, fused, top_k)
        with _retrieval_lock:
            _retrieval_info["rerank_available"] = bool(rerank_used)
    else:
        fused = fused[:top_k]

    _finalize_retrieval_mode()

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
            'rerank_score': item.get('rerank_score'),
            'metadata': metadata
        })

    return formatted


def _vector_search(
    query: str,
    subject_id: str,
    top_k: int,
    threshold: float
):
    """
    执行向量检索

    Returns:
        (results, reason)：reason为None表示成功；否则为降级原因
        （embedding_load_failed | faiss_index_missing | vector_search_failed）
    """
    from .embedding import get_embedding_status

    # 获取Embedding模型（失败时快速返回明确原因，不静默）
    try:
        from .embedding import get_embedding_model
        embeddings = get_embedding_model()
    except Exception:
        emb_state = get_embedding_status()
        reason = emb_state.get("error_type") or "embedding_load_failed"
        logger.warning(f"向量检索跳过（Embedding不可用）: reason={reason}")
        return [], reason

    safe_name = _safe_dir_name(subject_id)
    vector_store = GlobalFAISS.get(safe_name, embeddings)
    if vector_store is None:
        return [], "faiss_index_missing"

    try:
        # v2.6：取回真实相似度并按 threshold 过滤。
        # 此前 distances 在 vector_store 内被丢弃，threshold 形同虚设，
        # 毫不相关的 chunk 会一路进入 RRF → rerank → LLM 上下文。
        scored = vector_store.similarity_search(
            query, k=top_k, with_scores=True, score_threshold=threshold)
    except Exception as e:
        logger.error(f"向量检索失败: {str(e)}", exc_info=True)
        return [], "vector_search_failed"

    formatted = []
    for result in scored:
        # with_scores=True 返回 (Document, similarity)
        if isinstance(result, tuple) and len(result) == 2:
            doc, score = result
        else:
            doc, score = result, 0.0

        content = getattr(doc, "page_content", "") or ""
        metadata = getattr(doc, "metadata", {}) or {}
        if content:
            formatted.append({
                'document': _make_simple_doc(content, metadata),
                'score': float(score),
                'retrieval_type': 'vector'
            })

    if not formatted:
        logger.info(f"向量检索无结果通过阈值({threshold}): subject={subject_id}")
    return formatted, None



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
    faiss_store = GlobalFAISS.get(_safe_dir_name(subject_id))
    
    bm25_disk_path = _get_bm25_index_path(subject_id)
    
    return {
        "subject_id": subject_id,
        "vector_store": {
            "available": faiss_store is not None,
            "document_count": faiss_store.count() if faiss_store else 0
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


def invalidate_index(subject_id: str) -> None:
    """仅使内存中的 BM25 缓存失效（**不触碰磁盘索引**）。

    原子发布在切换成功后调用：新版本的磁盘索引已经就位，只需让进程内的旧缓存
    失效；若这里复用 clear_index 会把刚发布的新索引一并删掉。
    """
    global _bm25_retrievers
    _bm25_retrievers.pop(subject_id, None)
    logger.info(f"BM25 内存缓存已失效（磁盘索引保留）: subject={subject_id}")


def clear_index(subject_id: str) -> None:
    """清除指定学科的混合索引（内存 + 磁盘）"""
    global _bm25_retrievers
    
    if subject_id in _bm25_retrievers:
        _bm25_retrievers[subject_id].clear()
        del _bm25_retrievers[subject_id]
    
    # 清理磁盘上的BM25索引文件（含随附的 bm25_meta.json）
    index_path = _get_bm25_index_path(subject_id)
    if index_path.exists():
        try:
            index_path.unlink()
            meta_path = index_path.parent / BM25_META_FILE
            if meta_path.exists():
                meta_path.unlink()
            # 清理空的父目录
            parent_dir = index_path.parent
            if parent_dir.exists() and not any(parent_dir.iterdir()):
                parent_dir.rmdir()
            logger.info(f"BM25磁盘索引已删除: {index_path}")
        except Exception as e:
            logger.warning(f"删除BM25磁盘索引失败: {e}")
    
    logger.info(f"混合索引已清除: subject={subject_id}")
