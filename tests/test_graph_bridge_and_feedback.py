"""
知识图谱桥接、轻量 GraphRAG、前置溯因与学情反馈闭环回归测试。

验证范围：
1. graph_bridge：实体对齐、前置知识溯因、学生/班级热力图叠加、1 跳图谱扩展与 Agent 工具查询
2. hybrid_retriever：有知识图谱时启用 1 跳扩展并上报诊断；无图谱时不干扰既有检索
3. teaching_context：薄弱知识点命中前置薄弱时写入 adaptation_note 与 prompt_block
4. student_feedback：错因画像、FSRS 遗忘预警、进步对比、教师改判评语回流
5. class_analytics：错因分布、客观题选项分布（不泄露答案）、学生分层预警、前置溯因、班级图谱叠加
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from config.path_config import get_data_dir
from rag.rag_service import _safe_dir_name
from services import (
    class_analytics,
    fsrs_scheduler,
    graph_bridge,
    learning_handlers,
    quiz_service,
    student_feedback,
    teacher_grading,
    teaching_context,
    teaching_data,
    tutor_tools,
)

# 与应用启动一致：注册学习事件处理器，让作答事件自动联动掌握度/错题/复习
learning_handlers.register_all()


def _write_test_graph(subject_id: str, nodes, edges) -> Path:
    graph_dir = get_data_dir() / "knowledge_graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    path = graph_dir / f"{_safe_dir_name(subject_id)}.json"
    payload = {
        "subject_id": subject_id,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "created_at": datetime.now().isoformat(),
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class TestGraphBridgeAndFeedback(unittest.TestCase):
    def setUp(self):
        self.subject_id = f"sub_gb_{id(self)}"
        self.student_id = f"stu_gb_{id(self)}"
        self.student2_id = f"stu2_gb_{id(self)}"
        self.teacher_id = f"tch_gb_{id(self)}"
        self.graph_nodes = [
            {
                "id": "惯性矩", "label": "惯性矩", "type": "概念", "weight": 3.0,
                "excerpts": [{"kind": "definition", "text": "截面惯性矩衡量截面对弯曲变形的抵抗能力"}],
            },
            {
                "id": "柔度", "label": "柔度", "type": "概念", "weight": 4.0,
                "excerpts": [{"kind": "formula", "text": "λ = μl / i"}],
            },
            {
                "id": "欧拉公式", "label": "欧拉公式", "type": "公式", "weight": 5.0,
                "excerpts": [{"kind": "formula", "text": "F_cr = π²EI / (μl)²"}],
            },
            {
                "id": "压杆稳定", "label": "压杆稳定", "type": "概念", "weight": 4.0,
                "excerpts": [],
            },
        ]
        self.graph_edges = [
            {"source": "柔度", "target": "惯性矩", "relation": "前置条件", "weight": 2.0},
            {"source": "欧拉公式", "target": "柔度", "relation": "前置条件", "weight": 3.0},
            {"source": "欧拉公式", "target": "压杆稳定", "relation": "应用", "weight": 2.0},
        ]
        self.graph_path = _write_test_graph(self.subject_id, self.graph_nodes, self.graph_edges)

    def tearDown(self):
        if self.graph_path.exists():
            self.graph_path.unlink()

    def test_alignment_and_prerequisite_root_cause(self):
        # 欧拉公式薄弱，且前置「柔度」薄弱 → 应识别 prerequisite_gap
        metrics_gap = {
            "欧拉公式应用": {"mastery_level": 0.35, "evidence_count": 2, "wrong_count": 2},
            "柔度计算": {"mastery_level": 0.33, "evidence_count": 2, "wrong_count": 2},
            "惯性矩": {"mastery_level": 0.85, "evidence_count": 3, "wrong_count": 0},
        }
        diag = graph_bridge.diagnose_prerequisites(
            "欧拉公式应用", metrics_gap, subject_id=self.subject_id, mode="student",
        )
        self.assertTrue(diag["has_graph_links"])
        self.assertEqual(diag["root_cause_type"], "prerequisite_gap")
        weak_names = [p["concept"] for p in diag["weak_prerequisites"]]
        self.assertIn("柔度", weak_names)
        self.assertIn("柔度", diag["summary"])

        # 前置均稳固 → 归因为 application_or_error
        metrics_solid = {
            "欧拉公式": {"mastery_level": 0.35, "evidence_count": 2, "wrong_count": 2},
            "柔度": {"mastery_level": 0.85, "evidence_count": 3, "wrong_count": 0},
            "惯性矩": {"mastery_level": 0.88, "evidence_count": 3, "wrong_count": 0},
        }
        diag_solid = graph_bridge.diagnose_prerequisites(
            "欧拉公式", metrics_solid, subject_id=self.subject_id, mode="student",
        )
        self.assertEqual(diag_solid["root_cause_type"], "application_or_error")

    def test_expand_query_and_tutor_graph_tool(self):
        exp = graph_bridge.expand_query_with_graph("为什么细长压杆适用欧拉公式？", self.subject_id)
        self.assertIn("欧拉公式", exp["matched_nodes"])
        self.assertIn("柔度", exp["neighbor_nodes"])
        self.assertTrue(exp["expanded_query"])
        self.assertIn("知识图谱关联", exp["context_block"])

        # Tutor 工具注册与调用
        self.assertIn("query_concept_graph", tutor_tools.TOOLS)
        overview = graph_bridge.query_concept_context(self.subject_id)
        self.assertTrue(overview["exists"])
        self.assertGreaterEqual(len(overview["core_concepts"]), 3)

        detail = graph_bridge.query_concept_context(
            self.subject_id, concept="欧拉公式", user_id=self.student_id,
        )
        self.assertTrue(detail["found"])
        self.assertEqual(detail["concept"], "欧拉公式")
        self.assertTrue(detail["excerpts"])

    def test_hybrid_retriever_graph_expansion(self):
        from rag import hybrid_retriever
        from rag.bm25_retriever import BM25Retriever

        class _Doc:
            def __init__(self, text, meta):
                self.page_content = text
                self.metadata = meta

        docs = [
            _Doc("欧拉公式给出细长压杆临界力表达式。", {"file_name": "ch9.pdf", "page": 1}),
            _Doc("柔度 λ 等于计算长度与惯性半径之比，用于判别压杆类别。", {"file_name": "ch9.pdf", "page": 2}),
            _Doc("截面惯性矩决定弯曲刚度大小。", {"file_name": "ch5.pdf", "page": 4}),
        ]
        hybrid_retriever._bm25_retrievers[self.subject_id] = BM25Retriever(docs)
        try:
            results = hybrid_retriever.retrieve(
                query="欧拉公式是什么",
                subject_id=self.subject_id,
                top_k=3,
                use_vector=False,
                use_bm25=True,
                use_graph=True,
            )
            diag = hybrid_retriever.get_retrieval_diagnostics()
            self.assertTrue(diag["graph_expanded"])
            self.assertIn("欧拉公式", diag["graph_entities"])
            contents = " ".join(r["content"] for r in results)
            self.assertIn("柔度", contents)
        finally:
            hybrid_retriever._bm25_retrievers.pop(self.subject_id, None)

    def test_student_feedback_overlay_and_teaching_context(self):
        # 构造两道题：前置「柔度」答错，目标「欧拉公式」先错后对 + 另一道答错且标错因 + 教师改判
        qs_pre = quiz_service.persist_questions(
            self.subject_id, "柔度",
            [{"type": "single_choice", "question": "柔度 λ 与长度因数 μ 的关系？",
              "options": ["成正比", "成反比", "无关", "平方反比"], "answer": "A", "explanation": "λ=μl/i"}],
        )
        qs_target = quiz_service.persist_questions(
            self.subject_id, "欧拉公式",
            [
                {"type": "single_choice", "question": "欧拉公式适用于哪类压杆？",
                 "options": ["大柔度细长杆", "短粗杆", "中柔度杆", "任意杆"], "answer": "A", "explanation": "细长杆"},
                {"type": "short_answer", "question": "简述欧拉公式的推导假设。",
                 "options": [], "answer": "理想直杆、小挠度线弹性", "explanation": "线弹性小变形"},
            ],
        )
        # 柔度答错
        a_pre = quiz_service.submit_attempt(
            self.student_id, qs_pre[0]["id"], "B",
            client_token=f"tok_pre_{id(self)}", subject_id=self.subject_id,
        )
        quiz_service.report_error_cause(a_pre["attempt_id"], self.student_id, "concept_confusion", "记反了")
        # 欧拉公式第 1 题先错后对（形成性进步：recovered_questions）
        quiz_service.submit_attempt(
            self.student_id, qs_target[0]["id"], "B",
            client_token=f"tok_t1_w_{id(self)}", subject_id=self.subject_id,
        )
        quiz_service.submit_attempt(
            self.student_id, qs_target[0]["id"], "A",
            client_token=f"tok_t1_r_{id(self)}", subject_id=self.subject_id,
        )
        # 欧拉公式第 2 题写简答并由教师改判为错并写评语
        with patch("services.quiz_service.grade_short_answer_with_ai", return_value={"is_correct": True, "reason": "要点基本齐全"}):
            a_sa = quiz_service.submit_attempt(
                self.student_id, qs_target[1]["id"], "直杆受力弯曲",
                client_token=f"tok_t2_{id(self)}", subject_id=self.subject_id,
            )
        # 建课、建班、加学生，方便走真实教师改判和班级学情接口
        course = teaching_data.create_course("材料力学测试课", self.teacher_id, self.subject_id)
        klass = teaching_data.create_class(course["id"], "力学1班", self.teacher_id)
        teaching_data.add_member(klass["id"], course["id"], self.student_id)
        teaching_data.add_member(klass["id"], course["id"], self.student2_id)
        teacher_grading.regrade_attempt(
            self.teacher_id, klass["id"], a_sa["attempt_id"],
            is_correct=False, reason="漏掉了小挠度与线弹性两个关键前提",
        )

        # 写入一张 FSRS 遗忘卡片（6 天前复习、低稳定性 → 低保持率预警）
        kp_pre_id = qs_pre[0]["knowledge_point_id"]
        fsrs_scheduler.next_interval(
            self.student_id, self.subject_id, kp_pre_id,
            rating=fsrs_scheduler.RATING_AGAIN,
            now=datetime.now() - timedelta(days=6),
        )

        # 1) 验证学生反馈摘要
        fb = student_feedback.get_student_feedback_summary(self.student_id, self.subject_id, days=30)
        self.assertTrue(fb["has_knowledge_graph"])
        self.assertEqual(fb["error_cause_total"], 1)
        self.assertEqual(fb["error_causes"][0]["code"], "concept_confusion")
        self.assertGreaterEqual(fb["progress"]["recovered_questions"], 1)
        self.assertEqual(len(fb["teacher_feedback"]), 1)
        self.assertIn("小挠度", fb["teacher_feedback"][0]["teacher_reason"])
        self.assertGreaterEqual(len(fb["forgetting_alerts"]), 1)
        self.assertTrue(fb["root_causes"])

        # 2) 验证学生图谱热力叠加
        overlay = graph_bridge.build_student_graph_overlay(self.student_id, self.subject_id)
        self.assertTrue(overlay["exists"])
        by_label = {n["label"]: n for n in overlay["graph"]["nodes"]}
        self.assertEqual(by_label["柔度"]["learning_state"]["status"], "weak")
        self.assertEqual(by_label["欧拉公式"]["learning_state"]["status"], "weak")
        self.assertEqual(
            by_label["欧拉公式"]["learning_state"]["prerequisite_diagnosis"]["root_cause_type"],
            "prerequisite_gap",
        )

        # 3) 验证 teaching_context 注入前置知识诊断
        tctx = teaching_context.build_teaching_context(
            self.student_id, self.subject_id, question="欧拉公式为什么总是错？",
        )
        self.assertTrue(tctx["applied"].get("prerequisite_gap"))
        self.assertIn("知识图谱前置诊断", tctx["prompt_block"])
        self.assertIn("柔度", tctx.get("adaptation_note") or "")

        # 4) 验证教师端班级学情：选项分布（不含答案）、错因分布、学生分层、前置预警、班级图谱热力
        # 让第 2 名学生也作答，达到样本门槛 (>=5 graded, >=2 students)
        for i in range(3):
            q_extra = quiz_service.persist_questions(
                self.subject_id, "欧拉公式",
                [{"type": "single_choice", "question": f"欧拉公式测试题 {i}",
                  "options": ["选项A", "选项B", "选项C", "选项D"], "answer": "A", "explanation": "解析"}],
            )[0]
            quiz_service.submit_attempt(
                self.student2_id, q_extra["id"], "C",
                client_token=f"tok_s2_{i}_{id(self)}", subject_id=self.subject_id,
            )
        quiz_service.submit_attempt(
            self.student2_id, qs_pre[0]["id"], "B",
            client_token=f"tok_s2_pre_{id(self)}", subject_id=self.subject_id,
        )

        ov = class_analytics.class_overview(self.teacher_id, klass["id"], days=30)
        self.assertTrue(ov["enough_data"])
        self.assertTrue(ov["headline"])
        self.assertTrue(ov["error_causes"])
        self.assertIn("students", ov["student_tiers"])
        self.assertEqual(len(ov["student_tiers"]["students"]), 2)
        # 检查选项分布不泄露正确答案
        wrong_with_opts = [w for w in ov["wrong_questions"] if w.get("option_distribution")]
        self.assertTrue(wrong_with_opts)
        for opt_stat in wrong_with_opts[0]["option_distribution"]:
            self.assertNotIn("is_correct", opt_stat)
            self.assertIn("ratio", opt_stat)

        # 班级图谱热力叠加
        cls_graph = graph_bridge.build_class_graph_overlay(self.teacher_id, klass["id"], days=30)
        self.assertTrue(cls_graph["exists"])
        cls_by_label = {n["label"]: n for n in cls_graph["graph"]["nodes"]}
        self.assertIn("class_state", cls_by_label["欧拉公式"])
        self.assertGreater(cls_by_label["欧拉公式"]["class_state"]["attempt_count"], 0)


class TestFeedbackAndGraphHttpPermissions(unittest.TestCase):
    """验证新增 HTTP 接口的认证与课程权限边界，防止越权读取私密课程图谱与班级学情。"""

    def test_http_routes_and_permission_guards(self):
        import os
        import uuid
        from fastapi.testclient import TestClient
        from api.app import app
        from tests.fixtures_isolated import register_and_login

        tag = uuid.uuid4().hex[:6]
        teacher_name = f"gb_http_t_{tag}"
        student_name = f"gb_http_s_{tag}"
        outsider_name = f"gb_http_o_{tag}"

        env = patch.dict(os.environ, {"TEACHER_USERS": teacher_name})
        env.start()
        self.addCleanup(env.stop)
        client = TestClient(app)

        t_teacher = register_and_login(teacher_name)
        t_student = register_and_login(student_name)
        t_outsider = register_and_login(outsider_name)

        c_resp = client.post(
            "/api/teacher/courses",
            headers={"X-User-Token": t_teacher},
            json={"title": "图谱权限测试课"},
        )
        self.assertEqual(c_resp.status_code, 200, c_resp.text)
        course = c_resp.json()["data"]
        sid = course["subject_id"]

        cls_resp = client.post(
            f"/api/teacher/courses/{course['id']}/classes",
            headers={"X-User-Token": t_teacher},
            json={"name": "权限班"},
        )
        self.assertEqual(cls_resp.status_code, 200, cls_resp.text)
        klass = cls_resp.json()["data"]

        j_resp = client.post(
            "/api/student/courses/join",
            headers={"X-User-Token": t_student},
            json={"code": klass["join_code"]},
        )
        self.assertEqual(j_resp.status_code, 200, j_resp.text)

        gpath = _write_test_graph(
            sid,
            [{"id": "核心概念A", "label": "核心概念A", "type": "概念", "weight": 2.0, "excerpts": []}],
            [],
        )
        self.addCleanup(lambda: gpath.unlink() if gpath.exists() else None)

        # 1) 在班学生可读本人学情反馈与课程图谱
        r_fb = client.get(
            "/api/student/feedback",
            params={"subject_id": sid},
            headers={"X-User-Token": t_student},
        )
        self.assertEqual(r_fb.status_code, 200, r_fb.text)
        self.assertTrue(r_fb.json()["data"]["has_knowledge_graph"])

        r_sg = client.get(
            "/api/student/knowledge-graph",
            params={"subject_id": sid},
            headers={"X-User-Token": t_student},
        )
        self.assertEqual(r_sg.status_code, 200, r_sg.text)
        self.assertTrue(r_sg.json()["data"]["exists"])

        # 2) 未入班的外部学生访问课程图谱/反馈应被 403 拒绝
        r_out = client.get(
            "/api/student/knowledge-graph",
            params={"subject_id": sid},
            headers={"X-User-Token": t_outsider},
        )
        self.assertEqual(r_out.status_code, 403)

        # 3) 教师可读班级图谱热力；学生访问班级图谱热力应被 403 拒绝
        r_tg = client.get(
            f"/api/teacher/classes/{klass['id']}/analytics/graph",
            headers={"X-User-Token": t_teacher},
        )
        self.assertEqual(r_tg.status_code, 200, r_tg.text)
        self.assertTrue(r_tg.json()["data"]["exists"])

        r_tg_stu = client.get(
            f"/api/teacher/classes/{klass['id']}/analytics/graph",
            headers={"X-User-Token": t_student},
        )
        self.assertEqual(r_tg_stu.status_code, 403)


if __name__ == "__main__":
    unittest.main()
