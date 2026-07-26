"""
聊天服务测试
测试chat_service模块的功能
"""

import unittest
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat_service import (
    chat_with_ai,
    chat_stream,
    check_chat_service_status,
    quick_chat
)


class TestChatService(unittest.TestCase):
    """
    聊天服务测试类
    """
    
    def test_check_chat_service_status(self):
        """
        测试聊天服务状态检查
        """
        status = check_chat_service_status()
        
        # 检查返回结构
        self.assertIn("rag_available", status)
        self.assertIn("llm_available", status)
        self.assertIn("prompts_available", status)
        self.assertIn("default_top_k", status)
        
        print(f"聊天服务状态: {status}")
    
    def test_chat_with_ai_empty_question(self):
        """
        测试空问题处理
        """
        response = chat_with_ai("")
        
        self.assertFalse(response["success"])
        self.assertIn("error", response)
        self.assertEqual(response["error"], "问题不能为空")
    
    def test_chat_with_ai_none_question(self):
        """
        测试None问题处理
        """
        response = chat_with_ai(None)
        
        self.assertFalse(response["success"])
        self.assertIn("error", response)
    
    def test_chat_with_ai_response_structure(self):
        """
        测试响应结构
        """
        # 这个测试需要实际的LLM配置，跳过如果没有配置
        if not os.getenv("DEEPSEEK_API_KEY"):
            self.skipTest("DEEPSEEK_API_KEY未设置")
        
        response = chat_with_ai("什么是数学？")
        
        # 检查响应结构
        self.assertIn("success", response)
        self.assertIn("answer", response)
        self.assertIn("sources", response)
        self.assertIn("context_used", response)
        self.assertIn("subject_id", response)
        self.assertIn("model", response)
        self.assertIn("latency", response)
        self.assertIn("error", response)
    
    def test_chat_stream_empty_question(self):
        """
        测试流式接口空问题处理
        """
        events = list(chat_stream(""))
        
        # 应该只有一个错误事件
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("问题不能为空", events[0]["error"])
    
    def test_quick_chat_empty_question(self):
        """
        测试快速问答空问题处理
        """
        with self.assertRaises(RuntimeError) as context:
            quick_chat("")
        
        self.assertIn("问题不能为空", str(context.exception))


if __name__ == "__main__":
    unittest.main()