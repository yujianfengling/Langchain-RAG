"""
RAG服务测试
测试rag_service模块的功能
"""

import unittest
import sys
import os
import shutil
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag import (
    build_knowledge_base,
    load_knowledge_base,
    retrieve_context,
    build_context,
    list_available_subjects,
    delete_knowledge_base,
    rebuild_knowledge_base
)


class TestRagService(unittest.TestCase):
    """
    RAG服务测试类
    """
    
    def setUp(self):
        """
        测试前准备
        """
        self.test_subject_id = "test_subject"
        self.test_data_dir = Path("data/subjects") / self.test_subject_id
        
        # 创建测试数据目录
        self.test_data_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建测试文件
        test_file = self.test_data_dir / "test.txt"
        test_file.write_text("这是一个测试文档。数学是研究数量、结构、变化、空间以及信息等概念的一门学科。")
    
    def tearDown(self):
        """
        测试后清理
        """
        # 删除测试数据
        if self.test_data_dir.exists():
            shutil.rmtree(self.test_data_dir)
        
        # 删除测试向量存储
        test_vector_dir = Path("data/vector_store") / self.test_subject_id
        if test_vector_dir.exists():
            shutil.rmtree(test_vector_dir)
    
    def test_list_available_subjects(self):
        """
        测试列出可用学科
        """
        subjects = list_available_subjects()
        
        # 应该返回列表
        self.assertIsInstance(subjects, list)
        
        # 如果有测试数据，应该包含测试学科
        if self.test_data_dir.exists():
            self.assertIn(self.test_subject_id, subjects)
    
    def test_build_knowledge_base(self):
        """
        测试构建知识库
        """
        # 这个测试需要实际的embedding模型，跳过如果没有
        try:
            from rag.embedding import get_embeddings
            get_embeddings()
        except Exception:
            self.skipTest("Embedding模型不可用")
        
        try:
            result = build_knowledge_base(self.test_subject_id)
            self.assertTrue(result)
        except Exception as e:
            self.fail(f"构建知识库失败: {e}")
    
    def test_retrieve_context_empty_question(self):
        """
        测试空问题检索
        """
        # 空问题应该返回空列表
        result = retrieve_context("")
        self.assertEqual(result, [])
    
    def test_build_context_empty_docs(self):
        """
        测试空文档构建上下文
        """
        result = build_context([])
        self.assertEqual(result, "")
    
    def test_build_context_with_docs(self):
        """
        测试有文档构建上下文
        """
        # 创建模拟文档
        class MockDoc:
            def __init__(self, content):
                self.page_content = content
        
        docs = [MockDoc("文档1内容"), MockDoc("文档2内容")]
        result = build_context(docs)
        
        self.assertIn("文档1内容", result)
        self.assertIn("文档2内容", result)
        self.assertIn("知识库内容", result)
    
    def test_delete_knowledge_base_nonexistent(self):
        """
        测试删除不存在的知识库
        """
        result = delete_knowledge_base("nonexistent_subject")
        self.assertTrue(result)  # 应该返回True，即使目录不存在


if __name__ == "__main__":
    unittest.main()