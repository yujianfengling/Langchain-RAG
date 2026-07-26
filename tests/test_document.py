"""
文档处理测试
测试document模块的功能
"""

import unittest
import sys
import os
import tempfile
import shutil
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.document import load_documents_from_directory


class TestDocument(unittest.TestCase):
    """
    文档处理测试类
    """
    
    def setUp(self):
        """
        测试前准备
        """
        # 创建临时目录
        self.test_dir = tempfile.mkdtemp()
        
        # 创建测试文件
        self.test_txt_file = os.path.join(self.test_dir, "test.txt")
        with open(self.test_txt_file, "w", encoding="utf-8") as f:
            f.write("这是一个测试文档。数学是研究数量、结构、变化、空间以及信息等概念的一门学科。")
    
    def tearDown(self):
        """
        测试后清理
        """
        shutil.rmtree(self.test_dir)
    
    def test_load_documents_from_directory(self):
        """
        测试从目录加载文档
        """
        documents = load_documents_from_directory(self.test_dir)
        
        # 应该返回列表
        self.assertIsInstance(documents, list)
        
        # 应该有文档
        self.assertGreater(len(documents), 0)
        
        # 检查文档内容
        doc = documents[0]
        self.assertIn("测试文档", doc.page_content)
        
        # 检查metadata
        self.assertIn("source", doc.metadata)
        self.assertIn("file_name", doc.metadata)
        self.assertIn("file_type", doc.metadata)
        
        print(f"加载了 {len(documents)} 个文档")
        print(f"文档metadata: {doc.metadata}")
    
    def test_load_documents_empty_directory(self):
        """
        测试加载空目录
        """
        # 创建空目录
        empty_dir = os.path.join(self.test_dir, "empty")
        os.makedirs(empty_dir)
        
        documents = load_documents_from_directory(empty_dir)
        
        # 应该返回空列表
        self.assertEqual(documents, [])
    
    def test_load_documents_nonexistent_directory(self):
        """
        测试加载不存在的目录
        """
        documents = load_documents_from_directory("nonexistent_dir")
        
        # 应该返回空列表
        self.assertEqual(documents, [])
    
    def test_load_documents_unsupported_format(self):
        """
        测试加载不支持的文件格式
        """
        # 创建不支持格式的文件
        unsupported_file = os.path.join(self.test_dir, "test.xyz")
        with open(unsupported_file, "w", encoding="utf-8") as f:
            f.write("这是一个测试文件")
        
        documents = load_documents_from_directory(self.test_dir)
        
        # 应该只加载支持的格式
        self.assertEqual(len(documents), 1)  # 只有txt文件被加载


if __name__ == "__main__":
    unittest.main()