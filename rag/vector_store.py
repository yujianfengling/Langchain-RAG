"""
向量存储模块
提供基于FAISS的向量存储功能，支持创建、加载、持久化和检索
"""

import os
import pickle
import logging
from typing import List, Optional, Dict, Any
from langchain_core.documents import Document

logger = logging.getLogger(__name__)

# 向量存储目录配置
VECTOR_STORE_DIR = "data/vector_store"


def create_vector_store(
    docs: List[Document],
    embeddings: Any,
    collection_name: str = "default",
    persist_dir: Optional[str] = None
) -> Any:
    """
    创建FAISS向量存储
    
    Args:
        docs: 文档列表
        embeddings: 嵌入模型实例
        collection_name: 集合/学科标识
        persist_dir: 持久化目录路径（可选）
        
    Returns:
        FAISS向量存储实例
        
    Raises:
        RuntimeError: 创建失败时抛出
    """
    if not docs:
        raise RuntimeError("无法从空文档列表创建向量存储")
    
    try:
        import faiss
        import numpy as np
        
        # 确定存储路径
        if persist_dir is None:
            persist_dir = os.path.join(VECTOR_STORE_DIR, collection_name)
        
        os.makedirs(persist_dir, exist_ok=True)
        
        # 获取文档文本
        texts = [doc.page_content for doc in docs]
        metadatas = [doc.metadata for doc in docs]
        
        logger.info(f"正在为 {len(texts)} 个文档创建FAISS索引...")
        
        # 使用嵌入模型生成向量
        if hasattr(embeddings, 'embed_documents'):
            vectors = embeddings.embed_documents(texts)
        elif hasattr(embeddings, 'embed_query'):
            # 单个处理
            vectors = [embeddings.embed_query(text) for text in texts]
        else:
            raise RuntimeError("嵌入模型不支持embed_documents或embed_query方法")
        
        vectors = np.array(vectors, dtype='float32')
        
        # 创建FAISS索引
        dimension = vectors.shape[1]
        index = faiss.IndexFlatL2(dimension)
        index.add(vectors)
        
        # 保存索引文件
        index_path = os.path.join(persist_dir, "index.faiss")
        faiss.write_index(index, index_path)
        
        # 保存文档和元数据
        docs_path = os.path.join(persist_dir, "index.pkl")
        with open(docs_path, 'wb') as f:
            pickle.dump({
                'documents': docs,
                'texts': texts,
                'metadatas': metadatas
            }, f)
        
        logger.info(f"FAISS索引已创建并保存至: {persist_dir}")
        logger.info(f"  - 索引文件: {index_path}")
        logger.info(f"  - 文档文件: {docs_path}")
        
        # 返回包装对象
        return FAISSVectorStore(
            index=index,
            documents=docs,
            embeddings=embeddings,
            persist_dir=persist_dir
        )
        
    except Exception as e:
        raise RuntimeError(f"创建FAISS向量存储失败: {str(e)}")


def load_vector_store(
    collection_name: str = "default",
    embeddings: Any = None,
    persist_dir: Optional[str] = None
) -> Optional[Any]:
    """
    加载FAISS向量存储
    
    Args:
        collection_name: 集合/学科标识
        embeddings: 嵌入模型实例（FAISS检索时需要）
        persist_dir: 持久化目录路径（可选）
        
    Returns:
        FAISS向量存储实例，不存在则返回None
        
    Raises:
        RuntimeError: 加载失败时抛出
    """
    try:
        import faiss
        
        # 确定存储路径
        if persist_dir is None:
            persist_dir = os.path.join(VECTOR_STORE_DIR, collection_name)
        
        index_path = os.path.join(persist_dir, "index.faiss")
        docs_path = os.path.join(persist_dir, "index.pkl")
        
        # 检查文件是否存在
        if not os.path.exists(index_path):
            logger.warning(f"FAISS索引文件不存在: {index_path}")
            return None
        
        if not os.path.exists(docs_path):
            logger.warning(f"文档文件不存在: {docs_path}")
            return None
        
        # 加载FAISS索引
        index = faiss.read_index(index_path)
        
        # 加载文档数据
        with open(docs_path, 'rb') as f:
            data = pickle.load(f)
        
        documents = data.get('documents', [])
        
        logger.info(f"已加载FAISS索引: {persist_dir}, 包含 {len(documents)} 个文档")
        
        return FAISSVectorStore(
            index=index,
            documents=documents,
            embeddings=embeddings,
            persist_dir=persist_dir
        )
        
    except Exception as e:
        raise RuntimeError(f"加载FAISS向量存储失败: {str(e)}")


class FAISSVectorStore:
    """
    FAISS向量存储包装类
    提供统一的检索接口
    """
    
    def __init__(
        self,
        index: Any,
        documents: List[Document],
        embeddings: Any = None,
        persist_dir: Optional[str] = None
    ):
        self.index = index
        self.documents = documents
        self.embeddings = embeddings
        self.persist_dir = persist_dir
    
    def similarity_search(
        self,
        query: str,
        k: int = 4,
        embeddings: Any = None
    ) -> List[Document]:
        """
        相似度检索
        
        Args:
            query: 查询文本
            k: 返回文档数量
            embeddings: 嵌入模型（优先使用参数，其次使用实例属性）
            
        Returns:
            相似文档列表
        """
        try:
            import numpy as np
            
            # 使用提供的嵌入模型或实例属性
            emb = embeddings or self.embeddings
            if emb is None:
                raise RuntimeError("未提供嵌入模型，无法进行检索")
            
            # 生成查询向量
            if hasattr(emb, 'embed_query'):
                query_vector = emb.embed_query(query)
            else:
                raise RuntimeError("嵌入模型不支持embed_query方法")
            
            query_vector = np.array([query_vector], dtype='float32')
            
            # 执行检索
            k = min(k, len(self.documents))
            if k == 0:
                return []
            
            distances, indices = self.index.search(query_vector, k)
            
            # 返回文档
            results = []
            for idx in indices[0]:
                if 0 <= idx < len(self.documents):
                    results.append(self.documents[idx])
            
            return results
            
        except Exception as e:
            logger.error(f"FAISS检索失败: {str(e)}")
            raise RuntimeError(f"FAISS检索失败: {str(e)}")
    
    def count(self) -> int:
        """获取文档数量"""
        return len(self.documents)
    
    def add_documents(
        self,
        docs: List[Document],
        embeddings: Any = None
    ) -> None:
        """
        添加文档到向量存储
        
        Args:
            docs: 文档列表
            embeddings: 嵌入模型
        """
        try:
            import numpy as np
            import faiss
            
            emb = embeddings or self.embeddings
            if emb is None:
                raise RuntimeError("未提供嵌入模型")
            
            # 生成向量
            texts = [doc.page_content for doc in docs]
            if hasattr(emb, 'embed_documents'):
                vectors = emb.embed_documents(texts)
            else:
                vectors = [emb.embed_query(text) for text in texts]
            
            vectors = np.array(vectors, dtype='float32')
            
            # 添加到索引
            self.index.add(vectors)
            self.documents.extend(docs)
            
            # 持久化
            if self.persist_dir:
                self._save()
            
            logger.info(f"已添加 {len(docs)} 个文档，当前总数: {len(self.documents)}")
            
        except Exception as e:
            raise RuntimeError(f"添加文档失败: {str(e)}")
    
    def _save(self) -> None:
        """保存索引和文档到磁盘"""
        try:
            import faiss
            
            if self.persist_dir is None:
                return
            
            os.makedirs(self.persist_dir, exist_ok=True)
            
            # 保存FAISS索引
            index_path = os.path.join(self.persist_dir, "index.faiss")
            faiss.write_index(self.index, index_path)
            
            # 保存文档
            docs_path = os.path.join(self.persist_dir, "index.pkl")
            with open(docs_path, 'wb') as f:
                pickle.dump({
                    'documents': self.documents,
                    'texts': [doc.page_content for doc in self.documents],
                    'metadatas': [doc.metadata for doc in self.documents]
                }, f)
            
            logger.info(f"已保存FAISS索引至: {self.persist_dir}")
            
        except Exception as e:
            logger.error(f"保存FAISS索引失败: {str(e)}")


# 全局FAISS管理器（每个学科独立索引）
class GlobalFAISS:
    """全局FAISS管理器"""
    
    _instances: Dict[str, FAISSVectorStore] = {}
    
    @classmethod
    def get(
        cls,
        collection_name: str = "default",
        embeddings: Any = None,
        persist_dir: Optional[str] = None
    ) -> Optional[FAISSVectorStore]:
        """
        获取或加载FAISS实例
        
        Args:
            collection_name: 集合/学科标识
            embeddings: 嵌入模型实例
            persist_dir: 持久化目录路径（可选）
            
        Returns:
            FAISS向量存储实例，不存在则返回None
        """
        if collection_name in cls._instances:
            return cls._instances[collection_name]
        
        # 尝试从磁盘加载
        store = load_vector_store(collection_name, embeddings, persist_dir)
        if store is not None:
            cls._instances[collection_name] = store
        
        return store
    
    @classmethod
    def create(
        cls,
        docs: List[Document],
        embeddings: Any,
        collection_name: str = "default",
        persist_dir: Optional[str] = None
    ) -> FAISSVectorStore:
        """
        创建新的FAISS实例
        
        Args:
            docs: 文档列表
            embeddings: 嵌入模型实例
            collection_name: 集合/学科标识
            persist_dir: 持久化目录路径（可选）
            
        Returns:
            FAISS向量存储实例
        """
        store = create_vector_store(docs, embeddings, collection_name, persist_dir)
        cls._instances[collection_name] = store
        return store
    
    @classmethod
    def list_collections(cls) -> List[str]:
        """列出所有可用的集合"""
        collections = []
        
        if os.path.exists(VECTOR_STORE_DIR):
            for item in os.listdir(VECTOR_STORE_DIR):
                item_path = os.path.join(VECTOR_STORE_DIR, item)
                if os.path.isdir(item_path):
                    # 检查是否有FAISS索引文件
                    index_path = os.path.join(item_path, "index.faiss")
                    if os.path.exists(index_path):
                        collections.append(item)
        
        return collections
    
    @classmethod
    def delete(cls, collection_name: str) -> bool:
        """
        删除指定的FAISS实例
        
        Args:
            collection_name: 集合/学科标识
            
        Returns:
            是否删除成功
        """
        try:
            if collection_name in cls._instances:
                del cls._instances[collection_name]
            
            # 删除磁盘文件
            persist_dir = os.path.join(VECTOR_STORE_DIR, collection_name)
            if os.path.exists(persist_dir):
                import shutil
                shutil.rmtree(persist_dir)
                logger.info(f"已删除FAISS索引: {persist_dir}")
            
            return True
            
        except Exception as e:
            logger.error(f"删除FAISS索引失败: {str(e)}")
            return False
    
    @classmethod
    def exists(cls, collection_name: str) -> bool:
        """检查集合是否存在"""
        if collection_name in cls._instances:
            return True
        
        index_path = os.path.join(VECTOR_STORE_DIR, collection_name, "index.faiss")
        return os.path.exists(index_path)


# ============ 向后兼容的包装函数 ============

def add_documents(
    collection_name: str,
    docs: List[Document],
    embeddings: Any = None
) -> None:
    """
    向指定集合添加文档（向后兼容函数）
    
    Args:
        collection_name: 集合/学科标识
        docs: 文档列表
        embeddings: 嵌入模型实例
    """
    store = GlobalFAISS.get(collection_name, embeddings)
    if store is None:
        raise RuntimeError(f"集合 {collection_name} 不存在，请先创建")
    store.add_documents(docs, embeddings)


def similarity_search(
    collection_name: str,
    query: str,
    k: int = 4,
    embeddings: Any = None
) -> List[Document]:
    """
    在指定集合中进行相似度检索（向后兼容函数）
    
    Args:
        collection_name: 集合/学科标识
        query: 查询文本
        k: 返回文档数量
        embeddings: 嵌入模型实例
        
    Returns:
        相似文档列表
    """
    store = GlobalFAISS.get(collection_name, embeddings)
    if store is None:
        return []
    return store.similarity_search(query, k, embeddings)


def get_retriever(
    collection_name: str,
    embeddings: Any = None,
    search_kwargs: Optional[Dict[str, Any]] = None
) -> Optional[Any]:
    """
    获取检索器（向后兼容函数）
    
    Args:
        collection_name: 集合/学科标识
        embeddings: 嵌入模型实例
        search_kwargs: 检索参数
        
    Returns:
        检索器对象，如果集合不存在则返回None
    """
    store = GlobalFAISS.get(collection_name, embeddings)
    if store is None:
        return None
    
    # 返回一个简单的检索器包装
    class SimpleRetriever:
        def __init__(self, store, embeddings, search_kwargs):
            self.store = store
            self.embeddings = embeddings
            self.search_kwargs = search_kwargs or {"k": 4}
        
        def invoke(self, query: str) -> List[Document]:
            k = self.search_kwargs.get("k", 4)
            return self.store.similarity_search(query, k, self.embeddings)
        
        def get_relevant_documents(self, query: str) -> List[Document]:
            return self.invoke(query)
    
    return SimpleRetriever(store, embeddings, search_kwargs)


__all__ = [
    'create_vector_store',
    'load_vector_store',
    'add_documents',
    'similarity_search',
    'get_retriever',
    'FAISSVectorStore',
    'GlobalFAISS',
    'VECTOR_STORE_DIR'
]
