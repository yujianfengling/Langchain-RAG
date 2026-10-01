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
        
        # 列表中的元素应该是字符串
        for subject in subjects:
            self.assertIsInstance(subject, str)
    
    def test_build_knowledge_base(self):
        """
        测试构建知识库
        """
        # 这个测试需要实际的embedding模型，跳过如果没有
        try:
            from rag.embedding import get_embedding_model
            get_embedding_model()
        except Exception:
            self.skipTest("Embedding模型不可用")
        
        try:
            from langchain_core.documents import Document
            docs = [Document(page_content="测试文档内容", metadata={"source": "test.txt"})]
            result = build_knowledge_base(docs, self.test_subject_id)
            self.assertTrue(result)
        except Exception as e:
            self.fail(f"构建知识库失败: {e}")
    
    def test_retrieve_context_empty_question(self):
        """
        测试空问题检索
        """
        # 空问题应抛出ValueError
        with self.assertRaises(ValueError):
            retrieve_context("")
    
    def test_build_context_empty_docs(self):
        """
        测试空文档构建上下文
        """
        # 空文档列表应抛出ValueError
        with self.assertRaises(ValueError):
            build_context([])
    
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
        # 检查格式包含来源信息
        self.assertIn("来源", result)
    
    def test_delete_knowledge_base_nonexistent(self):
        """
        测试删除不存在的知识库
        """
        # 删除不存在的知识库应抛出RuntimeError
        with self.assertRaises(RuntimeError):
            delete_knowledge_base("nonexistent_subject")


if __name__ == "__main__":
    unittest.main()

class TestTeachingRetrievalFallback(unittest.TestCase):
    """
    教师侧检索降级（2026-09-27 合并后回归的固化测试）

    背景：课程资料入库时会做分块清洗，去掉 `[来源: 文件名]` 这类元信息包装。清洗本身是对的，
    但会削掉与**课程标题**的字面重合——而教师界面上不填知识点时默认查询词就是课程标题，
    于是严格阈值下检索为空，出题直接报"课程资料为空"，把"资料在库里"误判成"没资料"。

    行为约定（不可回退）：
      1) 严格阈值有命中 → 不降级（relaxed=False），结果与原来一致；
      2) 严格阈值为空、放宽后能命中 → 降级一次并**如实标记 relaxed=True**；
      3) 放宽后仍为空（知识库真的没有）→ 保持为空且 relaxed=False，
         调用方（assignment_service / lesson_plan_service）照旧报"资料为空"。
    """

    def setUp(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from services import ai_service
        self.ai_service = ai_service

    def test_returns_strict_result_when_not_empty(self):
        """严格阈值已命中时不做第二次检索（避免无谓放宽）"""
        calls = []

        def fake(question, subject_id=None, top_k=5, min_score=None):
            calls.append(min_score)
            return {"context": "命中内容", "documents": [1], "sources": [], "context_used": True}

        orig = self.ai_service.retrieve_and_build_context
        self.ai_service.retrieve_and_build_context = fake
        try:
            r = self.ai_service.retrieve_for_teaching("q", "sub")
        finally:
            self.ai_service.retrieve_and_build_context = orig

        self.assertEqual(calls, [None], "严格检索已有结果，不应再放宽")
        self.assertFalse(r["relaxed"])
        self.assertEqual(r["context"], "命中内容")

    def test_relaxes_once_when_strict_is_empty(self):
        """严格为空 → 放宽重试一次，并标记 relaxed=True"""
        calls = []

        def fake(question, subject_id=None, top_k=5, min_score=None):
            calls.append(min_score)
            if min_score is None:
                return {"context": "", "documents": [], "sources": [], "context_used": False}
            return {"context": "放宽后命中", "documents": [1], "sources": [],
                    "context_used": True}

        orig = self.ai_service.retrieve_and_build_context
        self.ai_service.retrieve_and_build_context = fake
        try:
            r = self.ai_service.retrieve_for_teaching("q", "sub")
        finally:
            self.ai_service.retrieve_and_build_context = orig

        self.assertEqual(calls, [None, 0.0], "应先用配置阈值、再用 0.0 各检索一次")
        self.assertTrue(r["relaxed"], "降级必须如实上报，不能伪装成严格命中")
        self.assertEqual(r["context"], "放宽后命中")

    def test_stays_empty_when_kb_really_empty(self):
        """放宽后仍为空：保持为空且不标记降级（调用方据此报"资料为空"）"""

        def fake(question, subject_id=None, top_k=5, min_score=None):
            return {"context": "", "documents": [], "sources": [], "context_used": False}

        orig = self.ai_service.retrieve_and_build_context
        self.ai_service.retrieve_and_build_context = fake
        try:
            r = self.ai_service.retrieve_for_teaching("q", "sub")
        finally:
            self.ai_service.retrieve_and_build_context = orig

        self.assertEqual(r["context"], "")
        self.assertFalse(r["relaxed"], "没有资料时不能标记为已降级命中")
        self.assertFalse(r["context_used"])

    def test_min_score_overrides_configured_threshold(self):
        """min_score 显式传入时覆盖配置阈值；不传时仍用配置阈值（学生问答行为不变）"""
        from config.settings import settings
        from rag import rag_service

        seen = []
        orig_hybrid = getattr(rag_service, "hybrid_retrieve", None)
        orig_available = getattr(rag_service, "HYBRID_AVAILABLE", False)
        if orig_hybrid is None:
            self.skipTest("当前环境未启用混合检索")

        def fake_hybrid(**kwargs):
            seen.append(kwargs.get("threshold"))
            return []

        rag_service.hybrid_retrieve = fake_hybrid
        rag_service.HYBRID_AVAILABLE = True
        try:
            rag_service.retrieve_context("q", "sub", top_k=3)
            rag_service.retrieve_context("q", "sub", top_k=3, min_score=0.0)
        finally:
            rag_service.hybrid_retrieve = orig_hybrid
            rag_service.HYBRID_AVAILABLE = orig_available

        self.assertEqual(len(seen), 2, "两次检索都应由 hybrid_retrieve 承担")
        self.assertAlmostEqual(seen[0], float(settings.SIMILARITY_THRESHOLD), places=6,
                               msg="不传 min_score 时必须沿用配置阈值")
        self.assertEqual(seen[1], 0.0, "传入 min_score 时必须覆盖配置阈值")
