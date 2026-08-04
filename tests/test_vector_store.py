"""
向量存储测试
测试vector_store模块的功能
"""

import unittest
import sys
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.documents import Document
from rag.vector_store import (
    create_vector_store,
    load_vector_store,
    add_documents,
    similarity_search,
    GlobalFAISS
)


class TestVectorStore(unittest.TestCase):
    """
    向量存储测试类
    """
    
    def setUp(self):
        """
        测试前准备
        """
        self.test_collection_name = "test_collection"
        # 清理全局状态
        GlobalFAISS._instances.clear()
    
    def tearDown(self):
        """
        测试后清理
        """
        # 清理全局状态
        GlobalFAISS._instances.clear()
        # 清理可能的持久化目录
        test_dir = Path("data/vector_store") / self.test_collection_name
        if test_dir.exists():
            shutil.rmtree(test_dir)
    
    def test_create_vector_store(self):
        """
        测试创建向量存储
        """
        # 这个测试需要实际的embedding模型，跳过如果没有
        try:
            from rag.embedding import get_embedding_model
            embeddings = get_embedding_model()
        except Exception:
            self.skipTest("Embedding模型不可用")
        
        try:
            docs = [
                Document(page_content="测试文档内容", metadata={"source": "test.txt"})
            ]
            vector_store = create_vector_store(
                docs=docs,
                embeddings=embeddings,
                collection_name=self.test_collection_name
            )
            
            self.assertIsNotNone(vector_store)
        except Exception as e:
            self.fail(f"创建向量存储失败: {e}")
    
    def test_load_vector_store_nonexistent(self):
        """
        测试加载不存在的向量存储
        """
        # 加载不存在的向量存储应该返回None
        result = load_vector_store(collection_name="nonexistent")
        self.assertIsNone(result)
    
    def test_similarity_search_empty(self):
        """
        测试相似性搜索
        """
        # 这个测试需要实际的embedding模型，跳过如果没有
        try:
            from rag.embedding import get_embedding_model
            embeddings = get_embedding_model()
        except Exception:
            self.skipTest("Embedding模型不可用")
        
        # 创建向量存储
        docs = [
            Document(page_content="测试文档内容", metadata={"source": "test.txt"})
        ]
        create_vector_store(
            docs=docs,
            embeddings=embeddings,
            collection_name=self.test_collection_name
        )
        
        # 搜索应该返回列表
        results = similarity_search(
            collection_name=self.test_collection_name,
            query="测试查询",
            embeddings=embeddings
        )
        self.assertIsInstance(results, list)


if __name__ == "__main__":
    unittest.main()