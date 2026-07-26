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
_VECTOR_STORE_BASE = os.path.join(_PROJECT_ROOT, "data", "vector_store")
_SUBJECTS_DATA_BASE = os.path.join(_PROJECT_ROOT, "data", "subjects")


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
        get_retriever
    )
    LOCAL_MODULES_AVAILABLE = True
except ImportError as e:
    LOCAL_MODULES_AVAILABLE = False
    IMPORT_ERROR = str(e)

# 导入混合检索模块
try:
    from .hybrid_retriever import build_index as build_hybrid_index, retrieve as hybrid_retrieve
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
    chunk_size: int = 500,
    chunk_overlap: int = 50
) -> Any:
    """
    构建学科知识库（向量索引 + BM25索引）
    
    Args:
        documents: Document对象列表或字符串列表
        subject_id: 学科ID（用于隔离知识库）
        chunk_size: 文本块大小（默认500）
        chunk_overlap: 文本块重叠大小（默认50）
        
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
    
    try:
        # 读取配置
        use_semantic = settings.USE_SEMANTIC_SPLIT if CONFIG_AVAILABLE else True
        
        # 1. 文本分割
        split_docs = split_documents(
            documents, chunk_size, chunk_overlap,
            use_semantic=use_semantic
        )
        
        logger.info(f"文本分割完成: {len(documents)}个文档 → {len(split_docs)}个chunk")
        
        # 2. 创建向量数据库（按学科目录隔离，使用绝对路径）
        persist_dir = os.path.join(_VECTOR_STORE_BASE, _safe_dir_name(subject_id))
        
        # 获取嵌入模型
        embeddings = get_embedding_model()
        
        vector_store = create_vector_store(
            docs=split_docs,
            embeddings=embeddings,
            collection_name=subject_id,
            persist_dir=persist_dir
        )
        
        # 3. 构建BM25索引（如果可用）
        if HYBRID_AVAILABLE:
            try:
                build_hybrid_index(
                    subject_id=subject_id,
                    documents=split_docs
                )
                logger.info(f"BM25索引构建完成: subject={subject_id}")
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
        
        # 加载向量数据库
        vector_store = load_vector_store(
            collection_name=subject_id,
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
    
    使用embedding相似度对文档重新排序，提升相关文档排名。
    轻量级实现，不引入大型rerank模型。
    
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
    
    try:
        # 获取embedding模型
        embedding_model = get_embedding_model()
        
        # 计算问题的embedding
        query_embedding = embedding_model.embed_query(question)
        
        # 计算每个文档的embedding并与问题计算相似度
        scored_docs = []
        for doc in documents:
            doc_text = doc.page_content if hasattr(doc, 'page_content') else str(doc)
            doc_embedding = embedding_model.embed_query(doc_text)
            
            # 余弦相似度计算
            similarity = _cosine_similarity(query_embedding, doc_embedding)
            scored_docs.append((doc, similarity))
        
        # 按相似度降序排序
        scored_docs.sort(key=lambda x: x[1], reverse=True)
        
        # 返回top_k并更新metadata中的rerank_score
        result = []
        for doc, score in scored_docs[:top_k]:
            if hasattr(doc, 'metadata'):
                doc.metadata['rerank_score'] = float(score)
            result.append(doc)
        
        logger.debug(f"Rerank完成: {len(documents)}个文档 → {len(result)}个结果")
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
    top_k: int = 5
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
    threshold = settings.SIMILARITY_THRESHOLD if CONFIG_AVAILABLE else 0.3
    
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
            
            # Rerank重排序
            if use_rerank and len(documents) > top_k:
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


def build_context(documents: List[Document]) -> str:
    """
    将文档列表转换为LLM上下文字符串
    
    Args:
        documents: Document对象列表
        
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
        
        for i, doc in enumerate(documents, 1):
            # 获取文档内容
            content = doc.page_content.strip()
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
            context_parts.append(context_part)
        
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
                subjects.append(subject_dir.name)
    except Exception:
        pass
    
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
        except Exception:
            pass
        
        return info
        
    except Exception:
        return info


def check_rag_service_status() -> Dict[str, Any]:
    """
    检查RAG服务模块的状态
    
    Returns:
        状态信息字典
    """
    status = {
        'local_modules_available': LOCAL_MODULES_AVAILABLE,
        'langchain_available': LANGCHAIN_AVAILABLE,
        'hybrid_available': HYBRID_AVAILABLE,
        'config_available': CONFIG_AVAILABLE,
        'available_subjects': list_available_subjects(),
        'vector_store_base': _VECTOR_STORE_BASE,
        'version': '2.0',
        'features': {
            'semantic_split': CONFIG_AVAILABLE and settings.USE_SEMANTIC_SPLIT,
            'bm25_retrieval': HYBRID_AVAILABLE and CONFIG_AVAILABLE and settings.USE_BM25,
            'rerank': CONFIG_AVAILABLE and settings.USE_RERANK,
            'rrf_fusion': HYBRID_AVAILABLE,
        }
    }
    
    if not LOCAL_MODULES_AVAILABLE:
        status['import_error'] = IMPORT_ERROR
    
    if CONFIG_AVAILABLE:
        status['rag_config'] = settings.rag.get_masked_config()
    
    return status