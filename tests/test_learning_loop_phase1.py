"""
阶段一《学生学习闭环》验收测试

覆盖主链路（全部通过 HTTP 接口层，Mock 外部模型）：
    登录 → 生成自测题（入库）→ 提交作答（后端权威判分）→ 保存作答与错题
    → 更新掌握度（证据驱动）→ 自动生成复习任务（合并去重）→ 首页/计划同步展示

同时验证关键约束：
- 用户身份来自后端机制：请求体传入的 user_id 一律忽略，跨用户访问 403，写接口未登录 401
- 不接受前端传入 is_correct（判分只依据题库标准答案）
- 幂等：同一 client_token 重复提交只计一次；同一知识点多次答错只产生一条待复习任务
- 「查看答案后作答」不计入独立掌握证据
- 学习行为（完成任务）不改变掌握度
- 测验任务未作答不能直接标记完成

说明：外部模型（LLM/RAG）使用 Mock；本文件验证的是业务闭环与数据一致性，
不等于真实模型效果验证。
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import asyncio
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import learning_data, auth_service
from api import deps as api_deps

SUBJECT = "数据结构"
KP = "二叉树"


def _quiz_llm_output():
    """模拟模型返回的自测题 JSON（含 3 种题型，覆盖客观判分与待自评两条路径）"""
    return json.dumps({
        "quiz": [
            {"question": "二叉树的每个节点最多有几个子节点？",
             "type": "single_choice", "options": ["1 个", "2 个", "3 个"],
             "answer": "B", "explanation": "二叉树定义：每个节点最多两个子树。",
             "knowledge_point": KP},
            {"question": "二叉树是树的一种。",
             "type": "judgement", "options": ["正确", "错误"],
             "answer": "正确", "explanation": "二叉树属于树结构。",
             "knowledge_point": KP},
            {"question": "请简述二叉树前序遍历的顺序。",
             "type": "short_answer", "options": [],
             "answer": "根-左-右", "explanation": "前序遍历先访问根节点。",
             "knowledge_point": KP},
        ]
    }, ensure_ascii=False)


def _note_llm_output():
    """模拟模型返回的五段式笔记 JSON"""
    return json.dumps({
        "title": "二叉树复习笔记",
        "concept": {"content": "二叉树每个节点最多两个子树。"},
        "example": {"content": "前序遍历：根-左-右。"},
        "mistakes": {"content": "容易混淆前序与中序。"},
        "class_tips": {"content": "当前知识库中没有足够的课堂提示证据。"},
        "quiz": [
            {"question": "二叉树最多几个子节点？", "type": "single_choice",
             "options": ["1 个", "2 个"], "answer": "B",
             "explanation": "定义决定。", "knowledge_point": KP},
        ],
    }, ensure_ascii=False)


def _plan_llm_output():
    """模拟模型返回的学习计划 JSON"""
    return json.dumps({
        "title": "三天复习二叉树",
        "goal": "三天复习完二叉树",
        "days": [
            {"day": 1, "tasks": [
                {"knowledge_point": KP, "task_type": "READ",
                 "title": "阅读二叉树定义", "description": "了解定义",
                 "estimated_minutes": 30},
                {"knowledge_point": KP, "task_type": "QUIZ",
                 "title": "二叉树自测", "description": "完成自测题",
                 "estimated_minutes": 20},
            ]},
        ],
    }, ensure_ascii=False)


class Phase1LoopTestBase(unittest.TestCase):
    """隔离运行：把学习闭环相关的四套库都指向临时文件"""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="phase1_")
        cls._patchers = [
            mock.patch.object(learning_data, "DB_PATH",
                              os.path.join(cls._tmpdir, "learning_loop.db")),
            mock.patch.object(learning_data, "_DATA_DIR", cls._tmpdir),
            mock.patch("services.learning_service.DB_PATH",
                       os.path.join(cls._tmpdir, "learning.db")),
            mock.patch.object(auth_service, "DB_PATH",
                              os.path.join(cls._tmpdir, "auth.db")),
            mock.patch.object(auth_service, "_DATA_DIR", cls._tmpdir),
            mock.patch("services.conversation_service.DB_PATH",
                       os.path.join(cls._tmpdir, "chat.db")),
            mock.patch("services.mastery_service._AGENT_DB",
                       os.path.join(cls._tmpdir, "user_agent.db")),
            mock.patch("services.user_profiler.DB_PATH",
                       os.path.join(cls._tmpdir, "user_agent.db")),
        ]
        for p in cls._patchers:
            p.start()
        # 建表（隔离库需要与生产一致的结构）
        learning_data._init_db()
        from services import learning_service, conversation_service, user_profiler
        learning_service._init_db()
        auth_service._init_db()
        user_profiler._init_db()
        conversation_service._init_db()

        # 模型与知识库外部依赖全部 Mock（不调用真实模型/向量库）
        cls._llm_patchers = [
            mock.patch("services.note_service.call_llm",
                       side_effect=lambda prompt, system_message=None, model=None, **kw:
                       _note_llm_output() if "五段式" in prompt else _quiz_llm_output()),
            mock.patch("services.note_service.retrieve_and_build_context",
                       return_value={
                           "context": "二叉树：每个节点最多有两个子树。前序遍历：根-左-右。",
                           "sources": [{"id": 1, "file_name": "tree.pdf", "page": 3, "type": "text"}],
                           "context_used": True, "documents": [],
                       }),
            mock.patch("services.note_service.require_kb_ready", return_value=None),
            mock.patch("services.learning_service.require_kb_ready", return_value=None),
            mock.patch("services.learning_service.retrieve_and_build_context",
                       return_value={
                           "context": "二叉树：每个节点最多有两个子树。",
                           "sources": [], "context_used": True, "documents": [],
                       }),
            mock.patch("services.learning_service.call_llm",
                       return_value=_plan_llm_output()),
            # 简答题 AI 判分：缺省判为答对（判分理由用于前端展示）
            mock.patch("services.quiz_service.call_llm",
                       side_effect=lambda prompt, system_message=None, model=None, **kw:
                       json.dumps({"is_correct": True, "reason": "要点齐全"},
                                  ensure_ascii=False)),
        ]
        for p in cls._llm_patchers:
            p.start()

        # 测试库的默认归属账号（未登录请求会回退到它）
        auth_service._init_db()

    @classmethod
    def tearDownClass(cls):
        for p in cls._llm_patchers:
            p.stop()
        for p in cls._patchers:
            p.stop()
        import shutil
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        from api.app import app
        self._app = app
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test")
        # 自己建事件循环，不依赖"环境里正好有一个"。
        # 同一 pytest 会话里别的测试文件可能调用过 asyncio.run()（例如
        # tests/test_teacher_module.py），它会把默认事件循环置空，导致这里
        # 报 RuntimeError: There is no current event loop（实测：两个文件
        # 单独跑都通过，一起跑就 13 个用例连环失败）。
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def tearDown(self):
        self._loop.run_until_complete(self._client.aclose())
        self._loop.close()
        asyncio.set_event_loop(None)

    # ---------- HTTP 辅助 ----------

    def _req(self, method, url, token=None, **kw):
        headers = kw.pop("headers", {}) or {}
        if token:
            headers["X-User-Token"] = token
        return asyncio.get_event_loop().run_until_complete(
            self._client.request(method, url, headers=headers, **kw))

    def _register(self, username):
        r = self._req("POST", "/api/auth/register",
                      json={"username": username, "password": "test-pass-123"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertIn("token", data)
        return data["token"], data["id"]

    def _generate_quiz(self, token, subject=SUBJECT, topic=KP):
        r = self._req("POST", "/api/chat", token=token,
                      json={"question": topic, "subject_id": subject, "mode": "quiz"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body.get("success"), body)
        quiz = (body.get("payload") or {}).get("quiz") or []
        self.assertTrue(quiz, f"未生成题目: {body}")
        return quiz


class TestIdentityAndOwnership(Phase1LoopTestBase):
    """身份与归属：不信任请求体 user_id"""

    def test_write_endpoints_require_login(self):
        r = self._req("POST", "/api/quiz/answer",
                      json={"question_id": "q_x", "user_answer": "A"})
        self.assertEqual(r.status_code, 401, r.text)

        r = self._req("GET", "/api/student/home")
        self.assertEqual(r.status_code, 401, r.text)

    def test_request_body_user_id_is_ignored(self):
        token, uid = self._register("stu_body_id")
        quiz = self._generate_quiz(token)
        q = quiz[0]

        r = self._req("POST", "/api/quiz/answer", token=token, json={
            "question_id": q["id"], "user_answer": "B",
            "user_id": "someone_else",           # 应被忽略
            "client_token": "tok-body-id-1",
        })
        self.assertEqual(r.status_code, 200, r.text)

        # 作答只归属令牌对应的用户
        mine = self._req("GET", "/api/quiz/attempts", token=token).json()["data"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["user_id"], uid)

        # 另一个账号看不到这条作答
        other_token, _ = self._register("stu_other_view")
        theirs = self._req("GET", "/api/quiz/attempts", token=other_token).json()["data"]
        self.assertEqual(theirs, [])

    def test_cross_user_path_access_forbidden(self):
        token, _ = self._register("stu_cross_a")
        other_token, other_uid = self._register("stu_cross_b")

        # 访问他人真实账号的画像数据 → 403
        r = self._req("GET", f"/api/agent/profile/{other_uid}", token=token)
        self.assertEqual(r.status_code, 403, r.text)

        # me 保留字 → 读取自己
        r = self._req("GET", "/api/agent/profile/me", token=token)
        self.assertEqual(r.status_code, 200, r.text)

    def test_plans_are_scoped_to_current_user(self):
        token_a, uid_a = self._register("stu_plan_a")
        r = self._req("POST", "/api/learning/plans/generate", token=token_a,
                      json={"subject_id": SUBJECT, "goal": "三天复习完二叉树"})
        self.assertEqual(r.status_code, 200, r.text)
        plan_id = r.json()["data"]["id"]

        # A 能看到自己的计划
        plans_a = self._req("GET", "/api/learning/plans", token=token_a,
                            params={"subject_id": SUBJECT}).json()["data"]
        self.assertIn(plan_id, [p["id"] for p in plans_a])

        # B 看不到 A 的计划，也不能读取/删除
        token_b, _ = self._register("stu_plan_b")
        plans_b = self._req("GET", "/api/learning/plans", token=token_b,
                            params={"subject_id": SUBJECT}).json()["data"]
        self.assertNotIn(plan_id, [p["id"] for p in plans_b])

        r = self._req("GET", f"/api/learning/plans/{plan_id}", token=token_b)
        self.assertEqual(r.status_code, 404, r.text)

        r = self._req("DELETE", f"/api/learning/plans/{plan_id}", token=token_b)
        self.assertEqual(r.status_code, 404, r.text)

        # 计划内的任务也归属 A
        tasks_a = self._req("GET", "/api/student/tasks", token=token_a,
                            params={"subject_id": SUBJECT}).json()["data"]
        tasks_b = self._req("GET", "/api/student/tasks", token=token_b,
                            params={"subject_id": SUBJECT}).json()["data"]
        self.assertTrue(tasks_a["todo"])
        self.assertEqual(tasks_b["todo"], [])

    def test_self_test_does_not_accept_client_is_correct(self):
        token, _ = self._register("stu_no_client_grade")
        quiz = self._generate_quiz(token)
        choice = next(q for q in quiz if q["type"] == "single_choice")
        correct_id = "B"

        # 前端谎报 is_correct=True，但答案是错的 → 后端仍判错
        r = self._req("POST", "/api/quiz/answer", token=token, json={
            "question_id": choice["id"], "user_answer": "A",
            "is_correct": True, "client_token": "tok-lie-1",
        })
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertFalse(data["is_correct"])
        self.assertEqual(data["correct_option_id"], correct_id)


class TestSelfTestGrading(Phase1LoopTestBase):
    """真实自测：题库持久化、无答案下发、判分与自评"""

    def test_generated_quiz_hides_answers_and_exposes_ids(self):
        token, _ = self._register("stu_quiz_shape")
        quiz = self._generate_quiz(token)
        self.assertGreaterEqual(len(quiz), 3)
        for q in quiz:
            self.assertIn("id", q)
            self.assertTrue(q["id"].startswith("q_"))
            self.assertTrue(q.get("server_persisted"))
            # 关键：不下发标准答案与解析
            self.assertNotIn("answer", q)
            self.assertNotIn("explanation", q)
        # 客观题选项带稳定 ID
        choice = next(q for q in quiz if q["type"] == "single_choice")
        self.assertEqual([o["id"] for o in choice["options"]], ["A", "B", "C"])

    def test_judgement_and_short_answer_paths(self):
        token, _ = self._register("stu_quiz_types")
        quiz = self._generate_quiz(token)

        judge = next(q for q in quiz if q["type"] == "judgement")
        r = self._req("POST", "/api/quiz/answer", token=token, json={
            "question_id": judge["id"], "user_answer": "T", "client_token": "tok-j-1"})
        self.assertTrue(r.json()["data"]["is_correct"])

        short = next(q for q in quiz if q["type"] == "short_answer")
        r = self._req("POST", "/api/quiz/answer", token=token, json={
            "question_id": short["id"], "user_answer": "根左右", "client_token": "tok-s-1"})
        data = r.json()["data"]
        # 简答题有参考答案时由 AI 判分：记为 ai_graded，与客观题的 graded 区分开
        self.assertEqual(data["grade_status"], "ai_graded")
        self.assertTrue(data["is_correct"])
        self.assertEqual(data["ai_feedback"], "要点齐全")

        # 用户仍可用「我答对了 / 我答错了」覆盖 AI 判定
        r = self._req("POST", "/api/quiz/self-evaluate", token=token, json={
            "attempt_id": data["attempt_id"], "is_correct": False})
        self.assertEqual(r.json()["data"]["grade_status"], "self_evaluated")
        self.assertFalse(r.json()["data"]["is_correct"])

    def test_reveal_downgrades_mastery_evidence(self):
        token, _ = self._register("stu_reveal")
        quiz = self._generate_quiz(token)
        choice = next(q for q in quiz if q["type"] == "single_choice")

        # 先查看答案（后端记录）
        r = self._req("POST", f"/api/quiz/reveal/{choice['id']}", token=token)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["correct_option_id"], "B")

        # 再看答案后答对：记录为 after_reveal，且不计入独立掌握证据
        r = self._req("POST", "/api/quiz/answer", token=token, json={
            "question_id": choice["id"], "user_answer": "B", "client_token": "tok-rev-1"})
        data = r.json()["data"]
        self.assertTrue(data["is_correct"])
        self.assertEqual(data["attempt_kind"], "after_reveal")

        mastery = self._req("GET", "/api/student/mastery",
                            token=token, params={"subject_id": SUBJECT}).json()["data"]
        point = next((p for p in mastery["points"] if p["knowledge_point"] == KP), None)
        self.assertIsNotNone(point)
        self.assertEqual(point["mastery_status"], "pending")
        self.assertIsNone(point["mastery_level"])
        self.assertEqual(point["evidence_count"], 0)


class TestLearningLoopMainline(Phase1LoopTestBase):
    """验收主链路：计划 → 自测 → 判分 → 错题 → 掌握度 → 复习 → 首页/计划同步"""

    def test_full_loop(self):
        token, uid = self._register("stu_mainline")
        quiz = self._generate_quiz(token)

        # 1) 初始掌握度：无证据 → 待评估（不给默认分数）
        mastery = self._req("GET", "/api/student/mastery",
                            token=token, params={"subject_id": SUBJECT}).json()["data"]
        point = next((p for p in mastery["points"] if p["knowledge_point"] == KP), None)
        self.assertIsNotNone(point)
        self.assertEqual(point["mastery_status"], "pending")
        self.assertIsNone(point["mastery_level"])
        kp_id = point["knowledge_point_id"]
        self.assertTrue(kp_id)

        # 2) 答错 → 后端判错，保存作答
        choices = [q for q in quiz if q["type"] in ("single_choice",)]
        wrong_q = choices[0]
        r = self._req("POST", "/api/quiz/answer", token=token, json={
            "question_id": wrong_q["id"], "user_answer": "A",
            "subject_id": SUBJECT, "client_token": "tok-loop-1"})
        self.assertEqual(r.status_code, 200, r.text)
        attempt = r.json()["data"]
        self.assertFalse(attempt["is_correct"])
        self.assertEqual(attempt["attempt_kind"], "independent")
        attempt_id = attempt["attempt_id"]

        # 3) 幂等：同一 client_token 重复提交不重复计分
        r2 = self._req("POST", "/api/quiz/answer", token=token, json={
            "question_id": wrong_q["id"], "user_answer": "A", "client_token": "tok-loop-1"})
        self.assertEqual(r2.json()["data"]["attempt_id"], attempt_id)
        attempts = self._req("GET", "/api/quiz/attempts",
                             token=token, params={"subject_id": SUBJECT}).json()["data"]
        self.assertEqual(len(attempts), 1, "幂等失效：重复计分")

        # 4) 错题本：最近一次为错 → 进入错题本
        wrong_book = self._req("GET", "/api/quiz/wrong",
                               token=token, params={"subject_id": SUBJECT}).json()["data"]
        self.assertEqual(len(wrong_book), 1)
        self.assertEqual(wrong_book[0]["question_id"], wrong_q["id"])

        # 5) 掌握度更新：1 次独立证据（错） → 暂估 0.3333，行为计数为 0
        mastery = self._req("GET", "/api/student/mastery",
                            token=token, params={"subject_id": SUBJECT}).json()["data"]
        point = next(p for p in mastery["points"] if p["knowledge_point"] == KP)
        self.assertEqual(point["mastery_status"], "estimated")
        self.assertEqual(point["evidence_count"], 1)
        self.assertEqual(point["independent_wrong_count"], 1)
        self.assertEqual(point["behavior_count"], 0)
        self.assertAlmostEqual(point["mastery_level"], 0.3333, places=3)
        self.assertIn(kp_id, [point["knowledge_point_id"]])

        # 6) 复习任务自动生成（可执行：带到期日期）
        reviews = self._req("GET", "/api/quiz/reviews", token=token,
                            params={"subject_id": SUBJECT, "status": "PENDING"}).json()["data"]
        self.assertEqual(len(reviews), 1)
        review = reviews[0]
        self.assertEqual(review["knowledge_point_id"], kp_id)
        self.assertTrue(review["due_date"])

        # 7) 同一知识点再次答错 → 复习任务合并，不重复堆积
        r = self._req("POST", "/api/quiz/answer", token=token, json={
            "question_id": wrong_q["id"], "user_answer": "A", "client_token": "tok-loop-2"})
        self.assertFalse(r.json()["data"]["is_correct"])
        self.assertEqual(r.json()["data"]["attempt_kind"], "review")
        reviews2 = self._req("GET", "/api/quiz/reviews", token=token,
                             params={"subject_id": SUBJECT, "status": "PENDING"}).json()["data"]
        self.assertEqual(len(reviews2), 1, "复习任务未合并去重")
        self.assertGreaterEqual(reviews2[0]["merged_count"], 2)

        # 8) 复习任务进入学习计划（可执行日程）
        tasks = self._req("GET", "/api/student/tasks", token=token,
                          params={"subject_id": SUBJECT}).json()["data"]
        auto = [t for t in tasks["todo"] if t.get("source") == "auto_review"]
        self.assertEqual(len(auto), 1)
        self.assertEqual(auto[0]["knowledge_point_id"], kp_id)
        self.assertTrue(auto[0]["planned_date"])

        # 9) 完成测验任务需先有真实作答：这里已有作答 → 允许完成
        r = self._req("POST", f"/api/student/tasks/{auto[0]['id']}/complete",
                      token=token, json={"actual_minutes": 10})
        self.assertEqual(r.status_code, 200, r.text)

        # 10) 完成任务只记录行为，不改变掌握度（行为/证据分离）
        mastery = self._req("GET", "/api/student/mastery",
                            token=token, params={"subject_id": SUBJECT}).json()["data"]
        point = next(p for p in mastery["points"] if p["knowledge_point"] == KP)
        self.assertEqual(point["behavior_count"], 1)
        # 只有第 1 次是独立作答；第 2 次是复习重做，单独计（不冒充独立证据）
        self.assertEqual(point["evidence_count"], 1, "复习重做不应计入独立证据数")
        self.assertEqual(point["review_wrong_count"], 1)
        bd = point["evidence_breakdown"]
        self.assertEqual(bd["independent_wrong"], 1)
        self.assertEqual(bd["review_wrong"], 1)
        # 加权估计：独立(0对1错)*1.0 + 复习(0对1错)*0.5 → (0+1)/(1.5+2)
        self.assertAlmostEqual(point["mastery_level"], 0.2857, places=3)
        self.assertTrue(point["mastery_is_estimate"], "掌握度必须标注为估计")
        self.assertEqual(point["sample_size"], 1, "需给出独立作答样本量")
        self.assertFalse(point["consolidated"], "尚无间隔复现，不应认定长期掌握")
        self.assertEqual(point["consolidation_status"], "unverified")

        # 11) 重做答对 → 移出错题本 + 关闭待复习任务（历史保留）
        r = self._req("POST", "/api/quiz/answer", token=token, json={
            "question_id": wrong_q["id"], "user_answer": "B", "client_token": "tok-loop-3"})
        self.assertTrue(r.json()["data"]["is_correct"])

        wrong_book = self._req("GET", "/api/quiz/wrong",
                               token=token, params={"subject_id": SUBJECT}).json()["data"]
        self.assertEqual(wrong_book, [], "答对后应移出错题本")

        # 复习答对关闭当前待办，但"一次答对 ≠ 长期掌握"：应安排延后复测
        pending = self._req("GET", "/api/quiz/reviews", token=token,
                            params={"subject_id": SUBJECT, "status": "PENDING"}).json()["data"]
        self.assertEqual(len(pending), 1, "未巩固的知识点应安排延后复测")
        self.assertIn("复测", pending[0]["reason"] or "")
        all_reviews = self._req("GET", "/api/quiz/reviews", token=token,
                                params={"subject_id": SUBJECT}).json()["data"]
        done = [r for r in all_reviews if r["status"] == "DONE"]
        self.assertEqual(len(done), 1, "原复习任务应关闭且历史保留")

        # 作答历史完整保留（3 次）
        attempts = self._req("GET", "/api/quiz/attempts",
                             token=token, params={"subject_id": SUBJECT}).json()["data"]
        self.assertEqual(len(attempts), 3)
        mastery = self._req("GET", "/api/student/mastery",
                            token=token, params={"subject_id": SUBJECT}).json()["data"]
        point = next(p for p in mastery["points"] if p["knowledge_point"] == KP)
        # 独立证据仍为 1 次；复习重做单独计（1 对 1 错）
        self.assertEqual(point["evidence_count"], 1)
        self.assertEqual(point["independent_correct_count"], 0)
        self.assertEqual(point["independent_wrong_count"], 1)
        self.assertEqual(point["review_correct_count"], 1)
        self.assertEqual(point["review_wrong_count"], 1)
        # 加权估计：独立(0对1错)*1.0 + 复习(1对1错)*0.5 → (0.5+1)/(2.0+2)
        self.assertAlmostEqual(point["mastery_level"], 0.375, places=3)
        self.assertFalse(point["consolidated"], "仅一次复习答对，不应认定长期掌握")

        # 12) 首页同步展示：统计、错题数、最近位置
        home = self._req("GET", "/api/student/home",
                         token=token, params={"subject_id": SUBJECT}).json()["data"]
        self.assertEqual(home["quiz_stats"]["total_attempts"], 3)
        self.assertEqual(home["quiz_stats"]["correct"], 1)
        self.assertEqual(home["quiz_stats"]["wrong"], 2)
        self.assertAlmostEqual(home["quiz_stats"]["accuracy"], 0.3333, places=3)
        self.assertEqual(home["wrong_count"], 0)
        # 待复习 = 刚安排的延后复测（而不是"什么都没安排"）
        self.assertEqual(len(home["pending_reviews"]), 1)
        self.assertIn("延后复测", home["pending_reviews"][0]["reason"] or "")
        # 首页错题列表不再下发答案（答案隔离）
        self.assertEqual(home["wrong_questions"], [])
        self.assertEqual(home["last_position"]["knowledge_point"], KP)

        # 13) 事件联动可追溯（作答事件全部 DONE）
        events = self._req("GET", "/api/student/learning-events", token=token,
                           params={"subject_id": SUBJECT}).json()["data"]
        types = {e["event_type"] for e in events}
        self.assertIn("quiz_attempt_recorded", types)
        self.assertIn("plan_task_completed", types)
        self.assertIn("quiz_question_generated", types)
        self.assertTrue(all(e["status"] != "FAILED" for e in events),
                        [e for e in events if e["status"] == "FAILED"])

    def test_quiz_task_cannot_complete_without_attempt(self):
        token, uid = self._register("stu_quiz_gate")
        # 构造一个 QUIZ 类型任务（模拟计划生成结果）
        task_id = self._make_task(uid, "QUIZ", KP)

        r = self._req("POST", f"/api/student/tasks/{task_id}/complete",
                      token=token, json={"actual_minutes": 5})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["error_type"], "quiz_attempt_required")

        # 真实作答后允许完成（"完成"与"通过"区分开）
        quiz = self._generate_quiz(token, topic=KP)
        q = quiz[0]
        r = self._req("POST", "/api/quiz/answer", token=token, json={
            "question_id": q["id"], "user_answer": "A",
            "subject_id": SUBJECT, "client_token": "tok-gate-1"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self._req("POST", f"/api/student/tasks/{task_id}/complete",
                      token=token, json={"actual_minutes": 5})
        self.assertEqual(r.status_code, 200, r.text)

    def test_reschedule_keeps_history(self):
        token, uid = self._register("stu_reschedule")
        from services import learning_service
        task_id = learning_service.create_auto_review_task(
            user_id=uid, subject_id=SUBJECT,
            knowledge_point_id="kp_resched", knowledge_point_name="图遍历",
            planned_date="2020-01-01", reason="单测构造",
        )
        r = self._req("PUT", f"/api/student/tasks/{task_id}/schedule",
                      token=token, json={"new_date": "2099-06-01", "reason": "调整"})
        self.assertEqual(r.status_code, 200, r.text)
        task = r.json()["data"]
        self.assertEqual(task["planned_date"], "2099-06-01")
        self.assertEqual(task["defer_count"], 1)
        self.assertEqual(task["previous_planned_date"], "2020-01-01")

        records = self._req("GET", f"/api/student/tasks/{task_id}/adjustments",
                            token=token).json()["data"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["old_date"], "2020-01-01")
        self.assertEqual(records[0]["new_date"], "2099-06-01")

        # 改期后该任务出现在逾期桶之外（不再是逾期）
        buckets = self._req("GET", "/api/student/tasks", token=token,
                            params={"subject_id": SUBJECT}).json()["data"]
        self.assertNotIn(task_id, [t["id"] for t in buckets["overdue"]])
        self.assertIn(task_id, [t["id"] for t in buckets["todo"]])

    def test_task_buckets_split_today_and_overdue(self):
        token, uid = self._register("stu_buckets")
        from services import learning_service
        from datetime import date, timedelta
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        t_today = learning_service.create_auto_review_task(
            user_id=uid, subject_id=SUBJECT, knowledge_point_id="kp_today",
            knowledge_point_name="今日知识点", planned_date=today, reason="单测")
        t_over = learning_service.create_auto_review_task(
            user_id=uid, subject_id=SUBJECT, knowledge_point_id="kp_over",
            knowledge_point_name="逾期知识点", planned_date=yesterday, reason="单测")

        buckets = self._req("GET", "/api/student/tasks", token=token,
                            params={"subject_id": SUBJECT}).json()["data"]
        self.assertIn(t_today, [t["id"] for t in buckets["today"]])
        self.assertIn(t_over, [t["id"] for t in buckets["overdue"]])
        # todo 是全部未完成（含 today 与 overdue）
        todo_ids = [t["id"] for t in buckets["todo"]]
        self.assertIn(t_today, todo_ids)
        self.assertIn(t_over, todo_ids)
        self.assertEqual(buckets["counts"]["overdue"], len(buckets["overdue"]))

    # ---------- 工具 ----------

    def _make_task(self, user_id, task_type, kp_name, planned_date="2099-01-01"):
        """直接落库一个指定类型的任务（模拟计划生成/错题联动的结果）"""
        from services import learning_service as ls
        from services.learning_data import _now, _new_id, ensure_knowledge_point
        plan_id = ls._ensure_active_plan(ls._get_db(), user_id, SUBJECT)
        kp_id = ensure_knowledge_point(SUBJECT, kp_name, source="test")
        task_id = _new_id("task")
        conn = ls._get_db()
        try:
            conn.execute(
                """INSERT INTO learning_tasks
                   (id, plan_id, knowledge_point, knowledge_point_id, task_type, title,
                    description, priority, estimated_minutes, status, created_at,
                    user_id, subject_id, planned_date, source, original_planned_date)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, plan_id, kp_name, kp_id, task_type, f"{kp_name}任务",
                 "单测构造", 3, 20, "TODO", _now(), user_id, SUBJECT,
                 planned_date, "manual", planned_date))
            conn.commit()
        finally:
            conn.close()
        return task_id

    def _user_id_of(self, token):
        return self._req("GET", "/api/auth/me", token=token).json()["data"]["id"]


class TestNoteLoopLinkage(Phase1LoopTestBase):
    """笔记生成 → 学习事件记录（归属真实用户，不写死 default_user）"""

    def test_smart_note_records_event_for_current_user(self):
        token, uid = self._register("stu_note")
        r = self._req("POST", "/api/chat", token=token, json={
            "question": KP, "subject_id": SUBJECT, "mode": "smart_note"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body.get("success"), body)
        note = body.get("payload") or {}
        self.assertEqual(note.get("title"), "二叉树复习笔记")
        # 笔记中的自测题同样不下发答案
        for q in note.get("quiz") or []:
            self.assertNotIn("answer", q)

        events = self._req("GET", "/api/student/learning-events", token=token,
                           params={"subject_id": SUBJECT}).json()["data"]
        note_events = [e for e in events if e["event_type"] == "note_generated"]
        self.assertEqual(len(note_events), 1)
        self.assertEqual(note_events[0]["status"], "DONE")

        # 事件与交互记录归属当前用户（不是 default_user）
        interactions = self._req("GET", "/api/agent/interactions/me",
                                 token=token, params={"limit": 50}).json()["data"]
        self.assertTrue(any(i["interaction_type"] == "note" for i in interactions))
        for i in interactions:
            self.assertEqual(i["user_id"], uid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
