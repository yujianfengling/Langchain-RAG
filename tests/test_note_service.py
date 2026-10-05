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

from tests.fixtures_isolated import IsolatedDataTestCase
from services.note_service import (
    NoteServiceError,
    generate_smart_note,
    generate_quiz,
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
        # 新契约（2026-10-05）：题目**自带**的知识点才被采纳，缺失则待确认；
        # 不再把整批主题套到每道题上（那会把"复杂度题"算进"稳定性"）。
        self.assertIsNone(quizzes[0]["knowledge_point"])
        self.assertIn("id", quizzes[0])
        # 题目自带归属时保留
        declared = _normalize_quiz(
            [{"question": "二叉树每个节点最多两个子树？", "type": "judgement",
              "answer": "正确", "options": ["正确", "错误"],
              "knowledge_point": "二叉树"}], "树")
        self.assertEqual(declared[0]["knowledge_point"], "二叉树")

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


class TestGenerateSmartNote(IsolatedDataTestCase):
    """笔记生成：使用隔离夹具创建所需学科与"已就绪"知识库，不依赖本机正式数据"""

    SUBJECTS = ("数据结构",)

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
                 "options": ["一个", "两个"], "answer": "两个", "explanation": "定义",
                 "knowledge_point": "二叉树"},
                {"question": "前序遍历顺序？", "type": "short_answer",
                 "options": [], "answer": "根-左-右", "explanation": "遍历",
                 "knowledge_point": "二叉树"},
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


class TestExamplePromptContract(unittest.TestCase):
    """典型例题规则契约（2026-10-02）

    用户要求：知识库资料里已有例题答案就直接沿用；只有题目没有答案时，
    由 AI 补出完整解答流程；两者都没有才说明证据不足。

    这里锁定 prompt 里的三条分支，防止以后被改回"没有答案就只说证据不足"。
    """

    def test_prompt_has_three_branches_for_examples(self):
        from services.note_service import NOTE_PROMPT

        self.assertIn("例题处理规则", NOTE_PROMPT)
        # 分支 1：有答案 → 直接沿用
        self.assertIn("直接沿用原文", NOTE_PROMPT)
        # 分支 2：只有题目 → AI 补解答流程，并明确标注哪部分是补充的
        self.assertIn("补出完整", NOTE_PROMPT)
        self.assertIn("（解答由 AI 补充）", NOTE_PROMPT)
        # 分支 3：完全没有例题 → 证据不足
        self.assertIn("当前知识库中没有足够的证据来生成典型例题", NOTE_PROMPT)

    def test_prompt_keeps_fact_boundary_for_other_sections(self):
        """例题可以补解答，但不等于放开整体事实边界"""
        from services.note_service import NOTE_PROMPT

        self.assertIn("不得编造知识库中没有的内容", NOTE_PROMPT)
        self.assertIn("老师说过某句话", NOTE_PROMPT)


_WEB_QUIZ_JSON = json.dumps({
    "quiz": [
        {"question": "欧姆定律的公式是什么？", "type": "single_choice",
         "options": ["U=IR", "U=I/R", "I=UR"], "answer": "A",
         "explanation": "欧姆定律 U=IR。", "knowledge_point": "欧姆定律"},
    ]
}, ensure_ascii=False)

_GENERAL_QUIZ_JSON = json.dumps({
    "quiz": [
        {"question": "电流的国际单位是安培。", "type": "judgement",
         "options": ["正确", "错误"], "answer": "正确",
         "explanation": "国际单位为安培(A)。", "knowledge_point": "电流"},
    ]
}, ensure_ascii=False)


class TestLayeredQuizFallback(IsolatedDataTestCase):
    """知识库证据不足时的两级降级出题（2026-10-02）

    用户诉求：教材版本众多，本地知识库覆盖不到时也要有题可做。
    这里锁定分层顺序 **资料 → 联网 → 通用知识**，以及"绝不冒充你的资料"。
    """

    def test_web_material_is_used_and_attributed(self):
        """知识库没证据 + 联网可用 → 用网络素材出题，并带回来源 URL"""
        with mock.patch("services.note_service.require_kb_ready", return_value=None), \
             mock.patch("services.note_service.retrieve_and_build_context",
                        return_value=_mock_context(evidence=False)), \
             mock.patch("services.web_search_service.is_enabled", return_value=True), \
             mock.patch("services.web_search_service.search_question_material",
                        return_value=[{"title": "欧姆定律练习题",
                                       "url": "https://example.com/ohm",
                                       "content": "1、电阻R=10Ω，电压U=5V，求电流。解：I=U/R=0.5A。" * 5}]), \
             mock.patch("services.note_service.call_llm", return_value=_WEB_QUIZ_JSON):
            res = generate_quiz("物理", "欧姆定律")

        self.assertEqual(res["source"], "web_search")
        self.assertTrue(res["quiz"])
        self.assertEqual(len(res["material_sources"]), 1)
        self.assertEqual(res["material_sources"][0]["url"], "https://example.com/ohm")

    def test_general_knowledge_when_web_unavailable(self):
        """联网不可用 → 用 AI 通用知识出题，标注来源，不谎称来自资料"""
        with mock.patch("services.note_service.require_kb_ready", return_value=None), \
             mock.patch("services.note_service.retrieve_and_build_context",
                        return_value=_mock_context(evidence=False)), \
             mock.patch("services.web_search_service.is_enabled", return_value=False), \
             mock.patch("services.note_service.call_llm", return_value=_GENERAL_QUIZ_JSON):
            res = generate_quiz("物理", "电流")

        self.assertEqual(res["source"], "general_knowledge")
        self.assertEqual(res["material_sources"], [])
        self.assertTrue(res["quiz"])

    def test_knowledge_base_evidence_keeps_priority(self):
        """有知识库证据时优先用资料，且**不联网**"""
        llm_out = json.dumps({
            "quiz": [{"question": "二叉树最多几个子节点？", "type": "single_choice",
                      "options": ["1 个", "2 个", "3 个"], "answer": "B",
                      "explanation": "定义决定。", "knowledge_point": "二叉树"}]
        }, ensure_ascii=False)
        with mock.patch("services.note_service.require_kb_ready", return_value=None), \
             mock.patch("services.note_service.retrieve_and_build_context",
                        return_value=_mock_context(evidence=True)), \
             mock.patch("services.web_search_service.search_question_material") as web, \
             mock.patch("services.note_service.call_llm", return_value=llm_out):
            res = generate_quiz("数据结构", "二叉树")

        self.assertEqual(res["source"], "knowledge_base")
        web.assert_not_called()


if __name__ == "__main__":
    unittest.main()
