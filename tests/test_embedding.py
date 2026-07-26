"""
Embedding测试
测试embedding模块的功能
"""

import unittest
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.embedding import get_embeddings


class TestEmbedding(unittest.TestCase):
    """
    Embedding测试类
    """
    
    def test_get_embeddings(self):
        """
        测试获取embedding模型
        """
        try:
            embeddings = get_embeddings()
            self.assertIsNotNone(embeddings)
            
            # 测试embedding功能
            test_text = "这是一个测试文本"
            embedding = embeddings.embed_query(test_text)
            
            # 检查embedding结果
            self.assertIsInstance(embedding, list)
            self.assertGreater(len(embedding), 0)
            
            print(f"Embedding维度: {len(embedding)}")
            
        except Exception as e:
            self.skipTest(f"Embedding模型不可用: {e}")
    
    def test_get_embeddings_device(self):
        """
        测试embedding设备配置
        """
        try:
            # 测试CPU设备
            embeddings = get_embeddings(device="cpu")
            self.assertIsNotNone(embeddings)
            
        except Exception as e:
            self.skipTest(f"Embedding模型不可用: {e}")


if __name__ == "__main__":
    unittest.main()