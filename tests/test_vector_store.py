"""
向量存储测试
测试vector_store模块的功能
"""

import unittest
import sys
import os
import shutil
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.vector_store import (
    create_vector_store,
    load_vector_store,
    add_documents_to_store,
    similarity_search
)


class TestVectorStore(unittest.TestCase):
    """
    向量存储测试类
    """
    
    def setUp(self):
        """
        测试前准备
        """
        self.test_persist_dir = "data/vector_store/test_vector_store"
        self.test_collection_name = "test_collection"
        
        # 清理测试目录
        if os.path.exists(self.test_persist_dir):
            shutil.rmtree(self.test_persist_dir)
    
    def tearDown(self):
        """
        测试后清理
        """
        if os.path.exists(self.test_persist_dir):
            shutil.rmtree(self.test_persist_dir)
    
    def test_create_vector_store(self):
        """
        测试创建向量存储
        """
        # 这个测试需要实际的embedding模型，跳过如果没有
        try:
            from rag.embedding import get_embeddings
            embeddings = get_embeddings()
        except Exception:
            self.skipTest("Embedding模型不可用")
        
        try:
            vector_store = create_vector_store(
                collection_name=self.test_collection_name,
                persist_directory=self.test_persist_dir
            )
            
            self.assertIsNotNone(vector_store)
            self.assertTrue(os.path.exists(self.test_persist_dir))
        except Exception as e:
            self.fail(f"创建向量存储失败: {e}")
    
    def test_load_vector_store_nonexistent(self):
        """
        测试加载不存在的向量存储
        """
        # 这个测试需要实际的embedding模型，跳过如果没有
        try:
            from rag.embedding import get_embeddings
            embeddings = get_embeddings()
        except Exception:
            self.skipTest("Embedding模型不可用")
        
        # 加载不存在的向量存储应该返回None
        result = load_vector_store(
            collection_name="nonexistent",
            persist_directory="nonexistent_dir"
        )
        self.assertIsNone(result)
    
    def test_similarity_search_empty_store(self):
        """
        测试空向量存储的相似性搜索
        """
        # 这个测试需要实际的embedding模型，跳过如果没有
        try:
            from rag.embedding import get_embeddings
            embeddings = get_embeddings()
        except Exception:
            self.skipTest("Embedding模型不可用")
        
        # 创建空向量存储
        vector_store = create_vector_store(
            collection_name=self.test_collection_name,
            persist_directory=self.test_persist_dir
        )
        
        # 搜索应该返回空列表
        results = similarity_search(vector_store, "测试查询")
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()