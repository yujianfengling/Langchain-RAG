"""
五段式智能笔记服务测试（V2.5 Task B）

覆盖：
- 证据充足：五段结构完整、quiz 规范化
- 证据不足：明确降级、不编造
- JSON 解析（markdown 包裹 / 解释文字）
- quiz 类型规范化（judgement 选项正确）
- 复用统一 SourceInfo
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.note_service import (
    NoteServiceError,
    generate_smart_note,
    _parse_note_json,
    _normalize_quiz,
)


def _mock_context(evidence=True, sources=None):
    """构造 retrieve_and_build_context 的 mock 返回值"""
    return {
        "context": "二叉树：每个节点最多有两个子树。前序遍历：根-左-右。" if evidence else "",
        "sources": sources or [
            {"id": 1, "file_name": "tree.pdf", "page": 3, "type": "text"},
            {"id": 2, "file_name": "tree.pdf", "page": 5, "type": "text"},
        ],
        "context_used": evidence,
        "documents": [],
    }


class TestParseNoteJson(unittest.TestCase):
    def test_plain_json(self):
        raw = '{"title":"t","concept":{"content":"c"}}'
        data = _parse_note_json(raw)
        self.assertEqual(data["title"], "t")
        self.assertEqual(data["concept"]["content"], "c")

    def test_markdown_wrapped(self):
        raw = '```json\n{"title":"t","quiz":[]}\n```'
        data = _parse_note_json(raw)
        self.assertEqual(data["title"], "t")

    def test_with_explanation_text(self):
        raw = '好的，以下是你需要的笔记：{"concept":{"content":"概念内容"}} 完毕'
        data = _parse_note_json(raw)
        self.assertEqual(data["concept"]["content"], "概念内容")

    def test_garbage_returns_empty(self):
        self.assertEqual(_parse_note_json("这不是JSON"), {})


class TestNormalizeQuiz(unittest.TestCase):
    def test_judgement_options(self):
        raw = [{"question": "二叉树每个节点最多两个子树？", "type": "judgement",
                "answer": "正确", "explanation": "定义", "options": ["正确", "错误"]}]
        quizzes = _normalize_quiz(raw, "二叉树")
        self.assertEqual(len(quizzes), 1)
        self.assertEqual(quizzes[0]["type"], "judgement")
        self.assertEqual(quizzes[0]["options"], ["正确", "错误"])
        self.assertEqual(quizzes[0]["knowledge_point"], "二叉树")
        self.assertIn("id", quizzes[0])

    def test_single_choice_and_short_answer(self):
        raw = [
            {"question": "q1", "type": "single_choice", "options": ["A", "B"], "answer": "A"},
            {"question": "q2", "type": "short_answer", "options": [], "answer": "x"},
        ]
        quizzes = _normalize_quiz(raw, "树")
        self.assertEqual(len(quizzes), 2)
        self.assertEqual(quizzes[0]["type"], "single_choice")
        self.assertEqual(quizzes[1]["type"], "short_answer")

    def test_invalid_entries_skipped(self):
        raw = [{"question": "", "type": "single_choice"}, "not-dict"]
        self.assertEqual(_normalize_quiz(raw, "x"), [])


class TestGenerateSmartNote(unittest.TestCase):
    def test_evidence_sufficient_builds_five_sections(self):
        """证据充足：五段结构完整、quiz 规范化、sources 关联"""
        llm_output = json.dumps({
            "title": "二叉树复习笔记",
            "concept": {"content": "二叉树是每个节点最多两个子树的树结构。"},
            "example": {"content": "前序遍历：根-左-右。"},
            "mistakes": {"content": "注意遍历顺序。"},
            "class_tips": {"content": "课堂提示：重点掌握三种遍历。"},
            "quiz": [
                {"question": "二叉树每个节点最多几个子树？", "type": "single_choice",
                 "options": ["一个", "两个"], "answer": "两个", "explanation": "定义"},
                {"question": "前序遍历顺序？", "type": "short_answer",
                 "options": [], "answer": "根-左-右", "explanation": "遍历"},
            ],
        }, ensure_ascii=False)

        with mock.patch("services.note_service.retrieve_and_build_context",
                        return_value=_mock_context(evidence=True)), \
             mock.patch("services.note_service.call_llm", return_value=llm_output):
            note = generate_smart_note("数据结构", "二叉树")

        self.assertEqual(note["title"], "二叉树复习笔记")
        for key in ("concept", "example", "mistakes", "class_tips"):
            self.assertIn("content", note[key])
            self.assertIn("sources", note[key])
            self.assertGreaterEqual(len(note[key]["sources"]), 1)
        self.assertEqual(len(note["quiz"]), 2)
        self.assertEqual(note["quiz"][0]["knowledge_point"], "二叉树")
        self.assertTrue(note["context_used"])

    def test_evidence_insufficient_degrades_no_fabrication(self):
        """证据不足：明确降级提示，不编造内容"""
        with mock.patch("services.note_service.retrieve_and_build_context",
                        return_value=_mock_context(evidence=False)):
            note = generate_smart_note("数据结构", "不存在的主题")

        self.assertFalse(note["context_used"])
        self.assertIn("没有足够的证据", note["concept"]["content"])
        self.assertIn("没有足够", note["class_tips"]["content"])
        self.assertEqual(note["quiz"], [])

    def test_llm_unparseable_raises(self):
        """模型输出无法解析 → NoteServiceError"""
        with mock.patch("services.note_service.retrieve_and_build_context",
                        return_value=_mock_context(evidence=True)), \
             mock.patch("services.note_service.call_llm", return_value="完全无法解析"):
            with self.assertRaises(NoteServiceError):
                generate_smart_note("数据结构", "二叉树")


if __name__ == "__main__":
    unittest.main()
