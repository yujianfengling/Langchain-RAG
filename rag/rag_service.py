"""
RAG服务模块
负责知识库构建、加载、检索和上下文生成

v2.0 更新：
- 集成混合检索（向量 + BM25）
- RRF融合排序
- Rerank重排序机制
- 可配置RAG参数
"""

import os
import shutil
import logging
from pathlib import Path
from typing import List, Optional, Union, Dict, Any

import base64

logger = logging.getLogger(__name__)

# 项目根目录（绝对路径，用于构建所有数据目录）
_PROJECT_ROOT = str(Path(__file__).parent.parent)
# 数据根目录统一来自 config.path_config（支持 EDU_DATA_DIR 覆盖）
from config.path_config import get_data_dir as _get_data_dir  # noqa: E402
_DATA_ROOT = str(_get_data_dir())
_VECTOR_STORE_BASE = os.path.join(_DATA_ROOT, "vector_store")
_SUBJECTS_DATA_BASE = os.path.join(_DATA_ROOT, "subjects")


def _safe_dir_name(subject_id: str) -> str:
    """
    将学科ID转为文件系统安全的目录名。
    FAISS在Windows上不支持中文路径，因此中文ID需编码为ASCII。
    纯ASCII的ID直接返回。
    """
    try:
        subject_id.encode('ascii')
        return subject_id  # 纯ASCII，直接用
    except UnicodeEncodeError:
        # 中文ID → base64编码（去掉=填充，用_替代/和+）
        encoded = base64.urlsafe_b64encode(subject_id.encode('utf-8')).decode('ascii').rstrip('=')
        return f"_zh_{encoded}"


def _original_subject_id(safe_name: str) -> str:
    """
    将安全目录名还原为原始学科ID（_safe_dir_name 的逆操作）。
    """
    if not safe_name.startswith('_zh_'):
        return safe_name  # 纯ASCII，直接返回
    try:
        encoded = safe_name[4:]  # 去掉 _zh_ 前缀
        # 补回 base64 的 = 填充
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += '=' * padding
        return base64.urlsafe_b64decode(encoded).decode('utf-8')
    except Exception:
        return safe_name  # 解码失败，返回原值

# 导入LangChain Document对象
try:
    from langchain_core.documents import Document
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    # 创建简单的Document替代类
    class Document:
        """LangChain Document的简单替代类"""
        def __init__(self, page_content: str, metadata: Optional[dict] = None):
            self.page_content = page_content
            self.metadata = metadata or {}

# 导入本地模块
try:
    from .document import load_document, load_documents
    from .text_splitter import split_documents
    from .embedding import get_embedding_model
    from .vector_store import (
        create_vector_store,
        load_vector_store,
        add_documents,
        similarity_search,
        get_retriever,
        GlobalFAISS,
        filter_documents_by_min_length,
    )
    LOCAL_MODULES_AVAILABLE = True
except ImportError as e:
    LOCAL_MODULES_AVAILABLE = False
    IMPORT_ERROR = str(e)

# 导入多模态模块
try:
    from .multimodal import MultimodalDocumentConverter
    MULTIMODAL_AVAILABLE = True
except ImportError:
    MULTIMODAL_AVAILABLE = False

# 导入混合检索模块
try:
    from .hybrid_retriever import (
        build_index as build_hybrid_index,
        retrieve as hybrid_retrieve,
        get_retrieval_diagnostics,
    )
    HYBRID_AVAILABLE = True
except ImportError:
    HYBRID_AVAILABLE = False

# 导入配置
try:
    from config import settings
    CONFIG_AVAILABLE = True
except ImportError:
    CONFIG_AVAILABLE = False


def build_knowledge_base(
    documents: List[Union[Document, str]],
    subject_id: str,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    chunk_min_length: Optional[int] = None,
) -> Any:
    """
    构建学科知识库（向量索引 + BM25索引）
    
    Args:
        documents: Document对象列表或字符串列表
        subject_id: 学科ID（用于隔离知识库）
        chunk_size: 文本块大小（None=读配置 CHUNK_SIZE，默认500）
        chunk_overlap: 文本块重叠大小（None=读配置 CHUNK_OVERLAP，默认50）
        chunk_min_length: 最小 chunk 字符数（None=读配置 CHUNK_MIN_LENGTH，默认120）
        
    Returns:
        向量存储实例
        
    Raises:
        ImportError: 缺少必要的依赖模块
        ValueError: 输入参数无效
        RuntimeError: 构建失败
    """
    # 检查依赖
    if not LOCAL_MODULES_AVAILABLE:
        raise ImportError(f"无法导入本地模块: {IMPORT_ERROR}")
    
    # 参数验证
    if not documents:
        raise ValueError("文档列表不能为空")
    
    if not subject_id or not subject_id.strip():
        raise ValueError("学科ID不能为空")

    # v2.6：未显式传参时读配置（此前调用方不传参就直接落到函数默认值，
    # 导致改 .env 的 CHUNK_SIZE 完全不影响实际分块）
    if CONFIG_AVAILABLE:
        if chunk_size is None:
            chunk_size = int(getattr(settings.rag, "chunk_size", 500))
        if chunk_overlap is None:
            chunk_overlap = int(getattr(settings.rag, "chunk_overlap", 50))
        if chunk_min_length is None:
            chunk_min_length = int(getattr(settings.rag, "chunk_min_length", 120))
    chunk_size = 500 if chunk_size is None else chunk_size
    chunk_overlap = 50 if chunk_overlap is None else chunk_overlap
    chunk_min_length = 120 if chunk_min_length is None else chunk_min_length
    
    try:
        # 读取配置
        use_semantic = settings.USE_SEMANTIC_SPLIT if CONFIG_AVAILABLE else True
        
        # 1. 文本分割
        split_docs = split_documents(
            documents, chunk_size, chunk_overlap,
            use_semantic=use_semantic
        )
        
        logger.info(f"文本分割完成: {len(documents)}个文档 → {len(split_docs)}个chunk")

        # 1.5 过滤碎片 chunk（v2.6）：FAISS 与 BM25 共用同一份过滤后文档，
        # 修复此前"向量索引与 BM25 索引条目数不一致"的问题
        split_docs, filter_stats = filter_documents_by_min_length(
            split_docs, chunk_min_length)
        if not split_docs:
            raise ValueError(
                f"过滤后无可用 chunk（min_length={chunk_min_length}，"
                f"原始 {filter_stats['before']} 条全部过短）。"
                f"请检查文档抽取质量，或调低 CHUNK_MIN_LENGTH")
        logger.info(
            f"碎片过滤: {filter_stats.get('before')} → {filter_stats.get('after')} chunk "
            f"(丢弃 {filter_stats.get('dropped', 0)} 条, "
            f"{(filter_stats.get('dropped_ratio') or 0)*100:.1f}%, "
            f"实际阈值={filter_stats.get('min_length')}, "
            f"配置阈值={filter_stats.get('configured_min_length')})")
        
        # 2. 创建向量数据库（按学科目录隔离，使用绝对路径）
        persist_dir = os.path.join(_VECTOR_STORE_BASE, _safe_dir_name(subject_id))
        
        # 获取嵌入模型
        embeddings = get_embedding_model()
        
        vector_store = create_vector_store(
            docs=split_docs,
            embeddings=embeddings,
            collection_name=subject_id,
            persist_dir=persist_dir,
            filter_stats=filter_stats,  # 已在上方过滤，此处只透传统计避免 meta 失真
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        # 写入缓存（键用 safe_name，与检索路径一致）
        GlobalFAISS.put(_safe_dir_name(subject_id), vector_store)
        
        # 3. 构建BM25索引（如果可用）——使用同一份过滤后文档
        if HYBRID_AVAILABLE:
            try:
                build_hybrid_index(
                    subject_id=subject_id,
                    documents=split_docs
                )
                logger.info(f"BM25索引构建完成: subject={subject_id}, "
                            f"文档={len(split_docs)}")
            except Exception as e:
                logger.warning(f"BM25索引构建失败（不影响向量检索）: {str(e)}")
        
        return vector_store
        
    except Exception as e:
        logger.error(f"构建知识库失败: {str(e)}", exc_info=True)
        raise RuntimeError(f"构建知识库失败: {str(e)}")


def load_knowledge_base(subject_id: str) -> Any:
    """
    加载学科知识库
    
    Args:
        subject_id: 学科ID
        
    Returns:
        Chroma向量存储实例
        
    Raises:
        ImportError: 缺少必要的依赖模块
        ValueError: 学科ID为空
        FileNotFoundError: 知识库不存在
        RuntimeError: 加载失败
    """
    # 检查依赖
    if not LOCAL_MODULES_AVAILABLE:
        raise ImportError(f"无法导入本地模块: {IMPORT_ERROR}")
    
    # 参数验证
    if not subject_id or not subject_id.strip():
        raise ValueError("学科ID不能为空")
    
    try:
        # 检查知识库目录是否存在
        persist_dir = os.path.join(_VECTOR_STORE_BASE, _safe_dir_name(subject_id))
        persist_path = Path(persist_dir)
        
        if not persist_path.exists():
            raise FileNotFoundError(f"学科知识库不存在: {subject_id}")
        
        # 获取嵌入模型
        embeddings = get_embedding_model()
        
        # 加载向量数据库（v2.6：统一走 GlobalFAISS 缓存，避免每次检索重读磁盘）
        # 此前直接调 load_vector_store，实测每次请求 21~41ms 全量反序列化
        vector_store = GlobalFAISS.get(
            collection_name=_safe_dir_name(subject_id),
            embeddings=embeddings,
            persist_dir=persist_dir
        )
        
        if vector_store is None:
            raise FileNotFoundError(f"学科知识库加载失败: {subject_id}")
        
        return vector_store
        
    except Exception as e:
        raise RuntimeError(f"加载知识库失败: {str(e)}")


def rerank_documents(
    question: str,
    documents: List[Document],
    top_k: int = 5
) -> List[Document]:
    """
    对检索结果进行重排序（Rerank）

    v2.6 修复（性能 + 有效性）：
    此前实现对**每一篇**文档调用 embed_query 重新编码（实测 8.4ms/条，
    100 候选 = 841ms），而且这些向量在 FAISS 检索时刚刚算过、却被丢弃，
    属于纯重复计算；同时 rerank 只改变排序、不改变召回集合，
    实测开关对 recall@5 的影响恒为 0。

    现在的策略（按优先级）：
    1. 若候选来自检索且已带分数（metadata['retrieval_score']），直接复用
       —— **零额外前向计算**；
    2. 否则用 embed_documents **一次性批量**编码（实测 2ms/条，比逐条快 4 倍以上）。
    
    Args:
        question: 用户问题
        documents: 待重排序的文档列表
        top_k: 返回的文档数量
        
    Returns:
        重排序后的文档列表
    """
    if not documents:
        return []

    if not LOCAL_MODULES_AVAILABLE:
        return documents[:top_k]

    # 策略 1：复用检索阶段已算出的分数（最省）
    scored = []
    reused = 0
    for doc in documents:
        meta = getattr(doc, 'metadata', {}) or {}
        s = meta.get('retrieval_score')
        if s is not None:
            try:
                scored.append((doc, float(s)))
                reused += 1
                continue
            except (TypeError, ValueError):
                pass
        scored.append((doc, None))

    if reused == len(documents):
        scored.sort(key=lambda x: x[1], reverse=True)
        logger.debug(f"Rerank 复用检索分数: {len(documents)} 篇（0 次额外编码）")
        return [d for d, _ in scored[:top_k]]

    # 策略 2：批量编码（比逐条 embed_query 快 4 倍以上）
    try:
        embedding_model = get_embedding_model()
        query_embedding = embedding_model.embed_query(question)

        need = [(i, d) for i, (d, s) in enumerate(scored) if s is None]
        if need:
            texts = [
                (d.page_content if hasattr(d, 'page_content') else str(d))
                for _, d in need
            ]
            # 一次性批量编码，而非 N 次 embed_query
            doc_vectors = embedding_model.embed_documents(texts)
            import numpy as _np
            q = _np.asarray(query_embedding, dtype='float32')
            qn = float(_np.linalg.norm(q)) or 1.0
            M = _np.asarray(doc_vectors, dtype='float32')
            norms = _np.linalg.norm(M, axis=1)
            norms[norms == 0] = 1.0
            sims = (M @ q) / (norms * qn)      # 向量化计算，替代 Python 三重循环
            for (i, _), sim in zip(need, sims):
                scored[i] = (scored[i][0], float(sim))

        scored.sort(key=lambda x: (x[1] is None, -(x[1] or 0.0)))

        result = []
        for doc, score in scored[:top_k]:
            if hasattr(doc, 'metadata'):
                doc.metadata['rerank_score'] = float(score or 0.0)
            result.append(doc)

        logger.debug(
            f"Rerank 批量编码完成: {len(documents)} 篇（复用 {reused} 篇，"
            f"新编码 {len(need)} 篇）→ {len(result)} 个结果")
        return result

    except Exception as e:
        logger.warning(f"Rerank失败，返回原始排序: {str(e)}")
        return documents[:top_k]



def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """计算两个向量的余弦相似度"""
    import math
    
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    
    if norm1 == 0 or norm2 == 0:
        return 0.0
    
    return dot_product / (norm1 * norm2)


def retrieve_context(
    question: str,
    subject_id: Optional[str] = None,
    top_k: int = 5,
    min_score: Optional[float] = None
) -> List[Document]:
    """
    根据问题检索相关上下文（v2.0 混合检索 + Rerank）
    
    检索流程：
    1. 向量检索（语义相似度）
    2. BM25关键词检索（精确匹配）
    3. RRF融合排序
    4. Rerank重排序
    
    Args:
        question: 用户问题
        subject_id: 学科ID（可选，如果为None则搜索所有学科）
        top_k: 返回的最相关文档数量（默认5）
        
    Returns:
        相关文档列表
        
    Raises:
        ImportError: 缺少必要的依赖模块
        ValueError: 问题为空或top_k无效
        RuntimeError: 检索失败
    """
    # 检查依赖
    if not LOCAL_MODULES_AVAILABLE:
        raise ImportError(f"无法导入本地模块: {IMPORT_ERROR}")
    
    # 参数验证
    if not question or not question.strip():
        raise ValueError("问题不能为空")
    
    if top_k <= 0:
        raise ValueError("top_k必须为正整数")
    
    # 读取配置
    use_bm25 = settings.USE_BM25 if CONFIG_AVAILABLE else True
    use_rerank = settings.USE_RERANK if CONFIG_AVAILABLE else True
    rrf_k = settings.RRF_K if CONFIG_AVAILABLE else 60
    # min_score 显式传入时覆盖配置阈值（供"教师侧生成"在检索为空时放宽阈值重试，
    # 见 services.ai_service.retrieve_for_teaching；学生问答仍用配置阈值，行为不变）
    threshold = (float(min_score) if min_score is not None
                 else (settings.SIMILARITY_THRESHOLD if CONFIG_AVAILABLE else 0.3))
    
    try:
        # 使用混合检索（如果可用）
        if HYBRID_AVAILABLE and subject_id:
            hybrid_results = hybrid_retrieve(
                query=question,
                subject_id=subject_id,
                top_k=top_k * 2,  # 多取一些用于rerank
                threshold=threshold,
                use_bm25=use_bm25,
                use_vector=True,
                rrf_k=rrf_k
            )

            # 读取检索诊断：向量检索不可用时明确降级并跳过依赖Embedding的Rerank
            diag = get_retrieval_diagnostics()
            if not diag.get("vector_available", False):
                mode = diag.get("retrieval_mode", "bm25_degraded")
                reason = diag.get("fallback_reason") or "unknown"
                logger.warning(
                    f"检索降级: retrieval_mode={mode} fallback_reason={reason} "
                    f"subject={subject_id}")
                use_rerank = False

            # 转换为Document格式
            documents = []
            for item in hybrid_results:
                # 兼容两种格式：直接返回Document对象或返回字典
                doc = item.get('document')
                if doc:
                    # 将检索类型信息写入metadata
                    if hasattr(doc, 'metadata'):
                        doc.metadata['retrieval_type'] = item.get('retrieval_type', 'unknown')
                        doc.metadata['retrieval_score'] = item.get('score', 0)
                    documents.append(doc)
                elif 'content' in item:
                    # hybrid_retriever返回的是字典格式，转换为Document
                    from langchain_core.documents import Document
                    metadata = item.get('metadata', {})
                    metadata['source'] = item.get('source', '')
                    metadata['section'] = item.get('section', '')
                    metadata['retrieval_type'] = item.get('retrieval_type', 'unknown')
                    metadata['retrieval_score'] = item.get('score', 0)
                    doc = Document(page_content=item['content'], metadata=metadata)
                    documents.append(doc)

            # Rerank重排序（向量检索不可用时跳过，避免依赖Embedding）
            # v2.6：去掉 len(documents) > top_k 的前置条件。阈值过滤后候选数可能
            # 已不足 top_k，此时若跳过精排会形成"既不精排也不足量"的空洞。
            if use_rerank and len(documents) > 1:
                documents = rerank_documents(question, documents, top_k)

            return documents[:top_k]
        
        # Fallback：仅使用向量检索
        if subject_id:
            vector_store = load_knowledge_base(subject_id)
            results = vector_store.similarity_search(question, top_k)
            return results
        else:
            # 搜索所有学科
            results = []
            vector_store_base = Path(_VECTOR_STORE_BASE)
            
            if not vector_store_base.exists():
                return results
            
            embeddings = get_embedding_model()
            
            for subject_dir in vector_store_base.iterdir():
                if subject_dir.is_dir():
                    try:
                        vector_store = load_vector_store(
                            collection_name=subject_dir.name,
                            embeddings=embeddings,
                            persist_dir=str(subject_dir)
                        )
                        if vector_store:
                            subject_results = vector_store.similarity_search(question, top_k)
                            results.extend(subject_results)
                    except Exception:
                        continue
            
            return results[:top_k]
        
    except Exception as e:
        logger.error(f"检索上下文失败: {str(e)}", exc_info=True)
        raise RuntimeError(f"检索上下文失败: {str(e)}")


def build_context(documents: List[Document], max_chars: Optional[int] = None) -> str:
    """
    将文档列表转换为LLM上下文字符串

    Args:
        documents: Document对象列表
        max_chars: 上下文总字符预算（V2.5.3，可选）。文档按相关性排序优先
                  拼接，超过预算时截断当前文档并停止，保留最相关证据。
                  为 None 时不做预算（全量拼接）。

    Returns:
        格式化的上下文字符串

    Raises:
        ValueError: 文档列表为空
    """
    # 参数验证
    if not documents:
        raise ValueError("文档列表不能为空")

    try:
        context_parts = []
        total_chars = 0

        # 清理孤立代理字符（PyMuPDF数学符号提取可能产生），避免污染LLM上下文与JSON序列化
        from .embedding import _sanitize_unicode_text
        for i, doc in enumerate(documents, 1):
            # 获取文档内容
            content = _sanitize_unicode_text(doc.page_content).strip()
            if not content:
                continue

            # 获取元数据
            metadata = doc.metadata if hasattr(doc, 'metadata') else {}
            source = metadata.get('source', f'文档{i}')
            section = metadata.get('section', '')
            retrieval_type = metadata.get('retrieval_type', '')
            rerank_score = metadata.get('rerank_score', None)

            # 构建来源标注
            source_label = f"来源: {source}"
            if section:
                source_label += f" > {section}"
            if retrieval_type:
                source_label += f" [{retrieval_type}]"
            if rerank_score is not None:
                source_label += f" (相关度: {rerank_score:.2f})"

            # 格式化上下文
            context_part = f"[{source_label}]\n{content}"

            # V2.5.3：字符预算控制，避免无意义冗长Context
            if max_chars is not None:
                remaining = max_chars - total_chars
                if remaining <= 0:
                    break  # 预算已用尽，跳过剩余文档
                if len(context_part) > remaining:
                    # 截断当前文档内容（保留来源标注与前半部分），保持句子边界尽量自然
                    cut = remaining
                    # 尽量在句号/换行处截断
                    for sep in ("。", "\n", ".", " "):
                        idx = context_part[:cut].rfind(sep)
                        if idx > 0 and idx > cut * 0.6:
                            cut = idx + len(sep)
                            break
                    content_cut = context_part[:cut].rstrip()
                    if not content_cut.endswith("…"):
                        content_cut += "…"
                    context_part = content_cut
                    context_parts.append(context_part)
                    break

            context_parts.append(context_part)
            total_chars += len(context_part)

        # 组合所有上下文
        context = "\n\n---\n\n".join(context_parts)

        return context

    except Exception as e:
        raise RuntimeError(f"构建上下文失败: {str(e)}")


def get_subject_retriever(subject_id: str, search_kwargs: Optional[Dict[str, Any]] = None) -> Any:
    """
    获取学科的Retriever对象
    
    Args:
        subject_id: 学科ID
        search_kwargs: 搜索参数（默认{"k": 4}）
        
    Returns:
        LangChain Retriever对象
        
    Raises:
        ImportError: 缺少必要的依赖模块
        ValueError: 学科ID为空
        RuntimeError: 获取失败
    """
    # 检查依赖
    if not LOCAL_MODULES_AVAILABLE:
        raise ImportError(f"无法导入本地模块: {IMPORT_ERROR}")
    
    # 参数验证
    if not subject_id or not subject_id.strip():
        raise ValueError("学科ID不能为空")
    
    try:
        # 加载知识库
        vector_store = load_knowledge_base(subject_id)
        
        # 获取Retriever
        retriever = get_retriever(vector_store, search_kwargs=search_kwargs)
        
        return retriever
        
    except Exception as e:
        raise RuntimeError(f"获取Retriever失败: {str(e)}")


def list_available_subjects() -> List[str]:
    """
    列出所有可用的学科知识库
    
    Returns:
        学科ID列表
    """
    subjects = []
    vector_store_base = Path(_VECTOR_STORE_BASE)
    
    if not vector_store_base.exists():
        return subjects
    
    try:
        for subject_dir in vector_store_base.iterdir():
            if subject_dir.is_dir():
                subjects.append(_original_subject_id(subject_dir.name))
    except Exception as e:
        logger.warning(f"遍历知识库目录失败: {e}")
    
    return subjects


def delete_knowledge_base(subject_id: str) -> bool:
    """
    删除学科知识库
    
    Args:
        subject_id: 学科ID
        
    Returns:
        删除是否成功
        
    Raises:
        ValueError: 学科ID为空
        FileNotFoundError: 知识库不存在
        RuntimeError: 删除失败
    """
    # 参数验证
    if not subject_id or not subject_id.strip():
        raise ValueError("学科ID不能为空")
    
    try:
        # 检查知识库目录是否存在
        persist_directory = os.path.join(_VECTOR_STORE_BASE, _safe_dir_name(subject_id))
        persist_path = Path(persist_directory)
        
        if not persist_path.exists():
            raise FileNotFoundError(f"学科知识库不存在: {subject_id}")
        
        # 删除整个学科目录
        shutil.rmtree(persist_path)

        # v2.6 关键修复：让进程内缓存失效。
        # 此前只删磁盘文件，GlobalFAISS._instances 与 _bm25_retrievers 仍持有旧索引，
        # 导致「删除知识库后仍能检索到已删内容」——数据删除语义失效（合规风险）。
        try:
            GlobalFAISS.invalidate(_safe_dir_name(subject_id))
        except Exception as e:
            logger.warning(f"FAISS 缓存失效失败: {e}")
        try:
            if HYBRID_AVAILABLE:
                from .hybrid_retriever import clear_index
                clear_index(subject_id)
        except Exception as e:
            logger.warning(f"BM25 缓存失效失败: {e}")
        
        return True
        
    except Exception as e:
        raise RuntimeError(f"删除知识库失败: {str(e)}")


def rebuild_knowledge_base(
    subject_id: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50
) -> Any:
    """
    重建学科知识库（删除旧知识库并重新构建）
    
    Args:
        subject_id: 学科ID
        chunk_size: 文本块大小（默认500）
        chunk_overlap: 文本块重叠大小（默认50）
        
    Returns:
        新构建的Chroma向量存储实例
        
    Raises:
        ImportError: 缺少必要的依赖模块
        ValueError: 学科ID为空
        FileNotFoundError: 学科资料目录不存在
        RuntimeError: 重建失败
    """
    # 检查依赖
    if not LOCAL_MODULES_AVAILABLE:
        raise ImportError(f"无法导入本地模块: {IMPORT_ERROR}")
    
    # 参数验证
    if not subject_id or not subject_id.strip():
        raise ValueError("学科ID不能为空")
    
    try:
        # 1. 删除旧知识库（如果存在）
        persist_directory = os.path.join(_VECTOR_STORE_BASE, _safe_dir_name(subject_id))
        persist_path = Path(persist_directory)
        
        if persist_path.exists():
            shutil.rmtree(persist_path)
        
        # 2. 查找学科资料目录
        subject_data_dir = Path(os.path.join(_SUBJECTS_DATA_BASE, subject_id))
        if not subject_data_dir.exists():
            raise FileNotFoundError(f"学科资料目录不存在: {subject_data_dir}")
        
        # 3. 加载学科资料
        documents = load_documents(str(subject_data_dir))
        if not documents:
            raise ValueError(f"学科资料目录为空: {subject_data_dir}")
        
        # 4. 重新构建知识库
        vector_store = build_knowledge_base(
            documents=documents,
            subject_id=subject_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
        
        return vector_store
        
    except Exception as e:
        raise RuntimeError(f"重建知识库失败: {str(e)}")


def get_knowledge_base_info(subject_id: str) -> Dict[str, Any]:
    """
    获取学科知识库信息
    
    Args:
        subject_id: 学科ID
        
    Returns:
        知识库信息字典
    """
    info = {
        'subject_id': subject_id,
        'exists': False,
        'document_count': 0,
        'persist_directory': os.path.join(_VECTOR_STORE_BASE, _safe_dir_name(subject_id))
    }
    
    try:
        # 检查目录是否存在
        persist_path = Path(info['persist_directory'])
        if not persist_path.exists():
            return info
        
        info['exists'] = True
        
        # 尝试加载知识库获取详细信息
        try:
            vector_store = load_knowledge_base(subject_id)
            collection_info = vector_store._collection.count() if hasattr(vector_store, '_collection') else 0
            info['document_count'] = collection_info
        except Exception as e:
            logger.debug(f"获取知识库详情失败: {e}")
        
        return info
        
    except Exception:
        return info


def check_rag_service_status() -> Dict[str, Any]:
    """
    检查RAG服务模块的状态（只读，不触发Embedding加载）

    Returns:
        状态信息字典，包含 status 字段（ok/degraded）
    """
    # 只读Embedding状态，绝不触发模型加载
    embedding_state = {}
    try:
        from .embedding import get_embedding_status
        embedding_state = get_embedding_status()
    except Exception:
        embedding_state = {"status": "unknown"}

    status = {
        'status': 'ok' if LOCAL_MODULES_AVAILABLE else 'degraded',
        'local_modules_available': LOCAL_MODULES_AVAILABLE,
        'langchain_available': LANGCHAIN_AVAILABLE,
        'hybrid_available': HYBRID_AVAILABLE,
        'config_available': CONFIG_AVAILABLE,
        'multimodal_available': MULTIMODAL_AVAILABLE,
        'available_subjects': list_available_subjects(),
        'vector_store_base': _VECTOR_STORE_BASE,
        'version': '2.1',
        'embedding_status': embedding_state.get('status', 'unknown'),
        'features': {
            'semantic_split': CONFIG_AVAILABLE and settings.USE_SEMANTIC_SPLIT,
            'bm25_retrieval': HYBRID_AVAILABLE and CONFIG_AVAILABLE and settings.USE_BM25,
            'rerank': CONFIG_AVAILABLE and settings.USE_RERANK,
            'rrf_fusion': HYBRID_AVAILABLE,
            'multimodal': MULTIMODAL_AVAILABLE,
        }
    }

    if not LOCAL_MODULES_AVAILABLE:
        status['import_error'] = IMPORT_ERROR

    if CONFIG_AVAILABLE:
        status['rag_config'] = settings.rag.get_masked_config()

    return status


def get_rag_readiness() -> Dict[str, Any]:
    """
    获取RAG就绪状态（用于 /health/ready）

    不触发Embedding加载、不调用外部API、不返回本地绝对路径与异常堆栈。

    Returns:
        {
            "status": "ok"|"degraded"|"loading"|"unavailable",
            "retrieval_mode": hybrid_ready|vector_only|bm25_degraded|not_loaded|loading|unavailable,
            "embedding": {status, source, model, error_type},
            "faiss": {"available": bool},
            "bm25": {"available": bool},
        }
    """
    from .embedding import get_embedding_status

    emb = get_embedding_status()
    emb_status = emb.get("status", "not_loaded")

    # FAISS可用性：存在至少一个含 index.faiss 的学科索引（不加载模型）
    faiss_available = False
    vector_store_base = Path(_VECTOR_STORE_BASE)
    try:
        if vector_store_base.exists():
            faiss_available = any(
                p.is_dir() and (p / "index.faiss").exists()
                for p in vector_store_base.iterdir()
            )
    except OSError:
        faiss_available = False

    # BM25可用性：存在至少一个含 bm25.pkl 的学科索引
    bm25_available = False
    bm25_base = Path(os.path.join(_PROJECT_ROOT, "data", "bm25_index"))
    try:
        if bm25_base.exists():
            bm25_available = any(
                p.is_dir() and (p / "bm25.pkl").exists()
                for p in bm25_base.iterdir()
            )
    except OSError:
        bm25_available = False

    # retrieval_mode 判定
    if emb_status == "ready":
        if faiss_available and bm25_available:
            retrieval_mode = "hybrid_ready"
        elif faiss_available:
            retrieval_mode = "vector_only"
        elif bm25_available:
            retrieval_mode = "bm25_degraded"
        else:
            retrieval_mode = "unavailable"
    elif emb_status == "loading":
        retrieval_mode = "loading"
    elif emb_status == "failed":
        retrieval_mode = "bm25_degraded" if bm25_available else "unavailable"
    else:  # not_loaded
        retrieval_mode = "not_loaded"

    if retrieval_mode in ("hybrid_ready", "vector_only"):
        overall = "ok"
    elif retrieval_mode in ("bm25_degraded", "not_loaded"):
        overall = "degraded"
    elif retrieval_mode == "loading":
        overall = "loading"
    else:
        overall = "unavailable"

    return {
        "status": overall,
        "retrieval_mode": retrieval_mode,
        "embedding": {
            "status": emb_status,
            "source": emb.get("source"),
            "model": emb.get("model"),
            "error_type": emb.get("error_type"),
        },
        "faiss": {"available": faiss_available},
        "bm25": {"available": bm25_available},
    }