"""
阶段二专项测试：学习记忆 / 教学个性化 / 计划调整 / Agent 编排

覆盖本轮验收清单（Mock 外部模型，隔离数据目录）：
- 两个不同学习记录的账号得到有依据的不同教学安排
- 无历史记录时不编造画像
- 记忆更正、删除后影响后续回答
- 时间预算与截止日期得到遵守（预算不足时明确报冲突，不生成超时计划）
- 答错引发合理复习；看过答案后的答对不虚增掌握（阶段一已覆盖，这里回归）
- 改期与重新规划不会重复增加任务
- 取消 / 超时 / 工具失败 / 事件重试可恢复
- Agent 工具不能跨用户读取或修改数据
- 模型不可用时基础学习功能正常
"""

import json
import os
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

import asyncio
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures_isolated import IsolatedDataTestCase

SUBJECT = "数据结构"
KP = "二叉树"


_QUIZ_SEQ = {"n": 0}


def _quiz_output():
    """
    每次生成一道**新的**题目（题干带序号）。

    这样同一知识点可以产生多道题，从而形成多次"独立作答"证据；
    真实模型每次生成的题干本来就不会完全相同。
    """
    _QUIZ_SEQ["n"] += 1
    n = _QUIZ_SEQ["n"]
    return json.dumps({"quiz": [
        {"question": f"第 {n} 题：二叉树每个节点最多几个子节点？", "type": "single_choice",
         "options": ["1 个", "2 个", "3 个"], "answer": "B",
         "explanation": "最多两个。", "knowledge_point": KP}]}, ensure_ascii=False)


def _note_output():
    return json.dumps({
        "title": f"{KP}复习笔记",
        "concept": {"content": "二叉树每个节点最多两个子树。"},
        "example": {"content": "前序遍历：根-左-右。"},
        "mistakes": {"content": "容易混淆前序与中序。"},
        "class_tips": {"content": "当前知识库中没有足够的课堂提示证据。"},
        "quiz": [{"question": "最多几个子节点？", "type": "single_choice",
                  "options": ["1 个", "2 个"], "answer": "B",
                  "explanation": "定义。", "knowledge_point": KP}]}, ensure_ascii=False)


def _plan_output():
    return json.dumps({
        "title": "三天复习二叉树", "goal": "三天复习完二叉树",
        "days": [
            {"day": 1, "tasks": [
                {"knowledge_point": KP, "task_type": "READ", "title": "阅读定义",
                 "description": "了解定义", "estimated_minutes": 30},
                {"knowledge_point": KP, "task_type": "QUIZ", "title": "自测",
                 "description": "完成自测", "estimated_minutes": 20}]},
            {"day": 2, "tasks": [
                {"knowledge_point": "树遍历", "task_type": "PRACTICE", "title": "遍历练习",
                 "description": "练习", "estimated_minutes": 45}]},
        ]}, ensure_ascii=False)


class Phase2Base(IsolatedDataTestCase):
    SUBJECTS = (SUBJECT,)

    def setUp(self):
        super().setUp()
        from api.app import app
        self._app = app
        self._clients = []
        self._patch_llm()

    def tearDown(self):
        for c in self._clients:
            try:
                asyncio.get_event_loop().run_until_complete(c.aclose())
            except Exception:
                pass
        super().tearDown()

    def _patch_llm(self):
        patches = [
            mock.patch("services.note_service.call_llm",
                       side_effect=lambda prompt, system_message=None, model=None, **kw:
                       _note_output() if "五段式" in prompt else _quiz_output()),
            mock.patch("services.note_service.retrieve_and_build_context",
                       return_value={"context": "二叉树：每个节点最多有两个子树。",
                                     "sources": [{"id": 1, "file_name": "tree.pdf",
                                                  "page": 3, "type": "text"}],
                                     "context_used": True, "documents": []}),
            mock.patch("services.learning_service.call_llm", return_value=_plan_output()),
            # 普通问答路径（chat_service 直接引用 ai_service.call_llm）
            mock.patch("chat_service.call_llm", return_value="这是关于二叉树的讲解。"),
            mock.patch("services.learning_service.retrieve_and_build_context",
                       return_value={"context": "二叉树与树遍历。", "sources": [],
                                     "context_used": True, "documents": []}),
        ]
        self._llm_patchers = patches
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    # ---------- HTTP ----------

    def new_client(self):
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=self._app),
                              base_url="http://test")
        self._clients.append(c)
        return c

    def req(self, method, url, token=None, client=None, **kw):
        headers = kw.pop("headers", {}) or {}
        if token:
            headers["X-User-Token"] = token
        c = client or (self._clients[0] if self._clients else self.new_client())
        return asyncio.get_event_loop().run_until_complete(
            c.request(method, url, headers=headers, **kw))

    def login(self, username, client=None):
        c = client or self.new_client()
        r = asyncio.get_event_loop().run_until_complete(
            c.post("/api/auth/register",
                   json={"username": username, "password": "test-pass-123"}))
        assert r.status_code == 200, r.text
        return r.json()["data"]["token"], r.json()["data"]["id"]

    def gen_quiz(self, token, topic=KP):
        """生成一组自测题（同一知识点，Mock 会给出新的题干 → 形成新的独立作答）"""
        r = self.req("POST", "/api/chat", token=token,
                     json={"question": topic, "subject_id": SUBJECT, "mode": "quiz"})
        assert r.status_code == 200, r.text
        return (r.json().get("payload") or {}).get("quiz") or []

    def answer(self, token, qid, ans, ctoken):
        return self.req("POST", "/api/quiz/answer", token=token,
                        json={"question_id": qid, "user_answer": ans,
                              "subject_id": SUBJECT,
                              "client_token": ctoken}).json()["data"]

    def make_wrong_attempts(self, token, n=2):
        """
        制造 n 次**独立**答错（同一知识点、不同题目，每题只答一次）。

        这样才算独立证据，能被画像与记忆推断使用。
        """
        for i in range(n):
            quiz = self.gen_quiz(token, topic=KP)
            q = next(x for x in quiz if x["type"] == "single_choice")
            self.answer(token, q["id"], "A", f"wrong-{i}")
        return token


# =====================================================================
# 一、教学个性化（画像真正影响教学）
# =====================================================================

class TestPersonalizedTeaching(Phase2Base):

    def _system_prompt(self, token):
        """从 /api/chat 的 teaching 字段反推教学策略（同时校验两条路径一致）"""
        r = self.req("POST", "/api/chat", token=token,
                     json={"question": f"{KP}是什么", "subject_id": SUBJECT})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json().get("teaching") or {}

    def test_no_history_does_not_fabricate_profile(self):
        """没有学习记录 → 普通教学，且不编造学生水平"""
        token, _ = self.login("p2_empty")
        out = self.req("POST", "/api/chat", token=token,
                       json={"question": "什么是二叉树", "subject_id": SUBJECT})
        teaching = out.json().get("teaching") or {}
        self.assertIn(teaching.get("mode"), ("standard", None))
        sig = teaching.get("signals") or {}
        self.assertIn(sig.get("sample_size") or 0, (0, None))
        reasons = " ".join(teaching.get("reasons") or [])
        self.assertIn("常规教学", reasons)

    def test_novice_with_mistakes_gets_foundation(self):
        """初学 + 有相关错题 → 基础优先教学"""
        token, _ = self.login("p2_novice")
        self.make_wrong_attempts(token, 2)
        teaching = self._system_prompt(token)
        self.assertEqual(teaching.get("mode"), "foundation", teaching)
        sig = teaching.get("signals") or {}
        self.assertGreaterEqual(sig.get("independent_wrong") or 0, 1)
        self.assertTrue(any("基础" in r for r in teaching.get("reasons") or []))

    def test_evidenced_student_gets_advanced(self):
        """已有充分且间隔复现的证据 → 允许综合练习"""
        token, uid = self.login("p2_advanced")
        # 同一知识点连续独立答对 3 次（不同题目 → 都是独立证据）→ 判定已巩固
        for i in (1, 2, 3):
            quiz = self.gen_quiz(token, topic=KP)
            q = next(x for x in quiz if x["type"] == "single_choice")
            self.answer(token, q["id"], "B", f"adv-{i}")

        teaching = self._system_prompt(token)
        self.assertEqual(teaching.get("mode"), "advanced", teaching)
        sig = teaching.get("signals") or {}
        self.assertTrue(sig.get("consolidated"))
        self.assertTrue(any("综合练习" in r for r in teaching.get("reasons") or []))

    def test_two_accounts_get_different_teaching(self):
        """两个学习记录不同的账号得到有依据的不同安排"""
        novice, _ = self.login("p2_diff_novice")
        advanced, _ = self.login("p2_diff_advanced")

        self.make_wrong_attempts(novice, 2)
        for i in range(3):
            quiz = self.gen_quiz(advanced, topic=KP)
            q = next(x for x in quiz if x["type"] == "single_choice")
            self.answer(advanced, q["id"], "B", f"d-adv-{i}")

        t1 = self._system_prompt(novice)
        t2 = self._system_prompt(advanced)
        self.assertNotEqual(t1.get("mode"), t2.get("mode"))
        self.assertEqual(t1.get("mode"), "foundation")
        self.assertEqual(t2.get("mode"), "advanced")

    def test_explicit_preference_is_used(self):
        """学生明确设置的偏好进入教学上下文，且来源标注为显式设置"""
        token, _ = self.login("p2_pref")
        self.req("POST", "/api/agent/preferences", token=token,
                 json={"preference_type": "explanation_style", "preference_value": "多举例"})
        from services import teaching_context
        uid = self._uid(token)
        ctx = teaching_context.build_teaching_context(uid, SUBJECT, question="什么是二叉树")
        self.assertIn("多举例", ctx["prompt_block"])
        self.assertIn("学生明确设置", ctx["prompt_block"])

    def test_prompt_budget_is_bounded(self):
        """上下文预算受控（不能把整份历史拼进提示词）"""
        token, _ = self.login("p2_budget")
        self.make_wrong_attempts(token, 3)
        from services import teaching_context
        ctx = teaching_context.build_teaching_context(self._uid(token), SUBJECT,
                                                     question="二叉树")
        self.assertLessEqual(len(ctx["prompt_block"]), teaching_context.BUDGET["total_chars"])

    def _uid(self, token):
        from services import auth_service
        return auth_service.get_user_id_by_token(token)


# =====================================================================
# 二、可管理的学习记忆
# =====================================================================

class TestLearningMemory(Phase2Base):

    def test_inference_requires_evidence_and_expires(self):
        """推断必须有依据与有效期；一次错误不构成标签"""
        token, uid = self.login("mem_infer")
        # 只答错一次 → 不应产生"需要加强"推断
        quiz = self.gen_quiz(token)
        q = next(x for x in quiz if x["type"] == "single_choice")
        self.answer(token, q["id"], "A", "mem-1")

        items = self.req("GET", "/api/memory", token=token).json()["data"]
        weak = [m for m in items if m["key"].startswith("weak_point:")]
        self.assertEqual(weak, [], "单次答错不应变成能力标签")

        # 同一知识点再独立答错一次（另一道题）→ 达到阈值才推断
        quiz2 = self.gen_quiz(token, topic=KP)
        q2 = next(x for x in quiz2 if x["type"] == "single_choice")
        self.assertNotEqual(q2["id"], q["id"], "应为不同的题目，才算新的独立作答")
        self.answer(token, q2["id"], "A", "mem-2")

        items = self.req("GET", "/api/memory", token=token).json()["data"]
        weak = [m for m in items if m["key"].startswith("weak_point:")]
        self.assertTrue(weak, "达到阈值后应产生推断")
        self.assertEqual(weak[0]["memory_type"], "inference")
        self.assertEqual(weak[0]["source"], "inferred")
        self.assertIsNotNone(weak[0]["expires_at"], "推断必须有有效期")
        self.assertTrue(weak[0]["evidence"], "推断必须有依据")

    def test_correction_text_becomes_effective_memory(self):
        """纠错反馈必须真正生效（此前 `correction_text` 只写库、全仓无人读取）"""
        from services import agent_runtime, learning_memory
        from services.user_profiler import FEEDBACK_TYPES
        token, uid = self.login("mem_correction")
        fb_type = "correction" if "correction" in FEEDBACK_TYPES else "rating"

        # 1) 学生提交纠错文本（走真实接口）
        resp = self.req("POST", "/api/agent/feedback", token=token,
                        json={"feedback_type": fb_type,
                              "correction_text": "答案里的遍历顺序写反了"})
        self.assertIn(resp.status_code, (200, 201), resp.text)

        # 2) 应转成"学生显式偏好"进入学习记忆
        keys = [m["key"] for m in learning_memory.list_memory(uid)]
        self.assertIn("correction_note", keys, "纠错文本没有进入学习记忆")

        # 3) 普通问答与 Agent 都能读到（同一口径）
        effective = learning_memory.get_effective_memory(uid, SUBJECT)
        values = [m["value"] for m in effective["items"] if m["key"] == "correction_note"]
        self.assertTrue(any("遍历顺序写反了" in v for v in values), values)
        state = agent_runtime._tool_get_learning_state(uid, SUBJECT)
        self.assertIn("correction_note", [m["key"] for m in state["memory"]])

        # 4) 后写覆盖先写：始终以最新纠错为准
        self.req("POST", "/api/agent/feedback", token=token,
                 json={"feedback_type": fb_type,
                       "correction_text": "已改正：先序是根左右"})
        effective2 = learning_memory.get_effective_memory(uid, SUBJECT)
        vals2 = [m["value"] for m in effective2["items"] if m["key"] == "correction_note"]
        self.assertEqual(len(vals2), 1, "同一 key 只应有一条生效记忆")
        self.assertIn("先序是根左右", vals2[0])

    def test_agent_learning_state_includes_memory(self):
        """Agent 必须读取学习记忆（与普通问答同口径）——补齐审计缺口 ②

        此前 `agent_runtime._tool_get_learning_state` 只读掌握度/错题/复习/预算，
        **不读 `learning_memory`**：学生在"学习记忆"里写的偏好，普通问答
        （teaching_context）用得到，Agent 却看不到——同一系统两条口径。
        """
        from services import agent_runtime
        token, uid = self.login("mem_agent")
        resp = self.req("POST", "/api/memory", token=token,
                        json={"key": "解释风格", "value": "多用图示", "scope": "global"})
        self.assertIn(resp.status_code, (200, 201), resp.text)

        state = agent_runtime._tool_get_learning_state(uid, SUBJECT)
        self.assertIn("memory", state, "Agent 学习状态缺少 memory 字段")
        keys = [m["key"] for m in state["memory"]]
        self.assertIn("解释风格", keys, "Agent 必须能看到学生的学习记忆")

        # 隔离：另一个学生的状态里不得出现
        _, uid_b = self.login("mem_agent_b")
        state_b = agent_runtime._tool_get_learning_state(uid_b, SUBJECT)
        self.assertNotIn("解释风格", [m["key"] for m in state_b.get("memory", [])],
                         "学习记忆串号了（必须按 user_id 隔离）")

    def test_correct_answer_revokes_inference(self):
        """答对后推断被撤销（不让一次错误永久留存）"""
        token, _ = self.login("mem_revoke")
        for i in (1, 2):                      # 同一知识点两次独立答错
            quiz = self.gen_quiz(token, topic=KP)
            q = next(x for x in quiz if x["type"] == "single_choice")
            self.answer(token, q["id"], "A", f"rev-{i}")

        items = self.req("GET", "/api/memory", token=token).json()["data"]
        self.assertTrue([m for m in items if m["key"].startswith("weak_point:")],
                        "两次独立答错后应产生推断")

        quiz3 = self.gen_quiz(token, topic=KP)
        q3 = next(x for x in quiz3 if x["type"] == "single_choice")
        self.answer(token, q3["id"], "B", "rev-3")     # 答对
        items = self.req("GET", "/api/memory", token=token).json()["data"]
        self.assertEqual([m for m in items if m["key"].startswith("weak_point:")], [],
                         "答对后'需要加强'推断应被撤销")

    def test_correct_and_delete_affect_teaching(self):
        """记忆更正/删除后影响后续回答（教学上下文随之变化）"""
        token, uid = self.login("mem_crud")
        r = self.req("POST", "/api/memory", token=token,
                     json={"key": "explanation_style", "value": "只讲重点"})
        self.assertEqual(r.status_code, 200, r.text)
        mid = r.json()["data"]["id"]

        from services import teaching_context
        ctx = teaching_context.build_teaching_context(uid, SUBJECT, question="二叉树")
        self.assertIn("只讲重点", ctx["prompt_block"])

        # 更正
        self.req("PUT", f"/api/memory/{mid}", token=token,
                 json={"value": "多讲例题"})
        ctx = teaching_context.build_teaching_context(uid, SUBJECT, question="二叉树")
        self.assertIn("多讲例题", ctx["prompt_block"])
        self.assertNotIn("只讲重点", ctx["prompt_block"])

        # 关闭 → 不再参与教学
        self.req("POST", f"/api/memory/{mid}/status", token=token,
                 json={"status": "disabled"})
        ctx = teaching_context.build_teaching_context(uid, SUBJECT, question="二叉树")
        self.assertNotIn("多讲例题", ctx["prompt_block"])

        # 删除 → 仍然不参与，且历史可追溯
        self.req("DELETE", f"/api/memory/{mid}", token=token)
        ctx = teaching_context.build_teaching_context(uid, SUBJECT, question="二叉树")
        self.assertNotIn("多讲例题", ctx["prompt_block"])
        hist = self.req("GET", f"/api/memory/{mid}/history", token=token).json()["data"]
        self.assertTrue(hist)
        actions = {h["action"] for h in hist}
        self.assertIn("correct", actions)
        self.assertIn("delete", actions)

    def test_explicit_preference_shadows_inference(self):
        """显式设置优先于同 key 的推断"""
        token, uid = self.login("mem_priority")
        from services import learning_memory
        learning_memory.set_memory(uid, "pace", "偏慢",
                                   learning_memory.MEMORY_INFERENCE, SUBJECT,
                                   evidence={"overdue_tasks": 3}, ttl_days=10)
        effective = learning_memory.get_effective_memory(uid, SUBJECT)
        self.assertTrue(any(m["key"] == "pace" for m in effective["items"]))

        # 学生显式设置 pace → 推断被覆盖
        learning_memory.set_memory(uid, "pace", "正常",
                                   learning_memory.MEMORY_PREFERENCE, SUBJECT)
        effective = learning_memory.get_effective_memory(uid, SUBJECT)
        pace = [m for m in effective["items"] if m["key"] == "pace"][0]
        self.assertEqual(pace["memory_type"], "preference")
        self.assertEqual(pace["value"], "正常")
        self.assertTrue(effective["shadowed"], "被覆盖的推断应可见（解释用）")

    def test_memory_is_user_scoped(self):
        """记忆不能跨账号读写"""
        token_a, _ = self.login("mem_a")
        token_b, _ = self.login("mem_b")
        mid = self.req("POST", "/api/memory", token=token_a,
                       json={"key": "explanation_style", "value": "A 的设置"}).json()["data"]["id"]

        self.assertEqual(self.req("PUT", f"/api/memory/{mid}", token=token_b,
                                  json={"value": "被改"}).status_code, 403)
        self.assertEqual(self.req("DELETE", f"/api/memory/{mid}", token=token_b).status_code, 403)
        self.assertEqual(self.req("POST", f"/api/memory/{mid}/status", token=token_b,
                                  json={"status": "disabled"}).status_code, 403)
        items_b = self.req("GET", "/api/memory", token=token_b).json()["data"]
        self.assertEqual(items_b, [])

    def test_curriculum_memory_not_created_from_unverified_answers(self):
        """未经核验的回答/点赞不会变成课程权威知识"""
        token, uid = self.login("mem_curriculum")
        from services import learning_memory
        self.assertEqual([m for m in learning_memory.list_memory(uid)
                          if m["memory_type"] == "curriculum"], [])
        # 尝试把推断当课程知识写入 → 必须带依据
        with self.assertRaises(learning_memory.MemoryError):
            learning_memory.set_memory(uid, "k", "v",
                                       learning_memory.MEMORY_INFERENCE, SUBJECT)


# =====================================================================
# 三、计划调整（预算 / 截止 / 冲突 / 版本 / 撤销）
# =====================================================================

class TestPlanAdjustment(Phase2Base):

    def _make_plan(self, token, uid, goal="三天复习完二叉树"):
        r = self.req("POST", "/api/learning/plans/generate", token=token,
                     json={"subject_id": SUBJECT, "goal": goal})
        assert r.status_code == 200, r.text
        return r.json()["data"]

    def test_budget_detects_insufficient_time(self):
        """每天半小时 + 明天截止 → 程序明确报出预算冲突，不生成"看似合理"的安排"""
        token, uid = self.login("pa_budget")
        self._make_plan(token, uid)     # 3 天任务，共 95 分钟

        deadline = (date.today() + timedelta(days=1)).isoformat()
        self.req("PUT", "/api/plan/constraints", token=token,
                 json={"subject_id": SUBJECT, "deadline": deadline, "daily_minutes": 30})

        budget = self.req("GET", "/api/plan/budget", token=token,
                          params={"subject_id": SUBJECT}).json()["data"]
        self.assertFalse(budget["fits"], budget)
        self.assertTrue(budget["conflicts"])
        self.assertIn("分钟", " ".join(budget["conflicts"]))
        self.assertTrue(budget["suggestions"], "预算不足必须给出建议")
        self.assertIn("缩小范围", " ".join(budget["suggestions"]))
        self.assertEqual(budget["daily_minutes"], 30)
        self.assertEqual(budget["deadline"], deadline)

    def test_budget_respects_deadline_and_capacity(self):
        """预算充足时 fits=True，容量按 天 × 每天分钟 计算"""
        token, uid = self.login("pa_fits")
        self._make_plan(token, uid)
        deadline = (date.today() + timedelta(days=10)).isoformat()
        self.req("PUT", "/api/plan/constraints", token=token,
                 json={"subject_id": SUBJECT, "deadline": deadline, "daily_minutes": 60})
        budget = self.req("GET", "/api/plan/budget", token=token,
                          params={"subject_id": SUBJECT}).json()["data"]
        self.assertTrue(budget["fits"], budget)
        self.assertEqual(budget["capacity_minutes"], 11 * 60)

    def test_teacher_deadline_cannot_be_extended(self):
        """教师固定截止时间不能被推后"""
        token, _ = self.login("pa_teacher")
        d1 = (date.today() + timedelta(days=3)).isoformat()
        d2 = (date.today() + timedelta(days=9)).isoformat()
        r = self.req("PUT", "/api/plan/constraints", token=token,
                     json={"subject_id": SUBJECT, "teacher_deadline": d1})
        self.assertEqual(r.status_code, 200, r.text)
        r2 = self.req("PUT", "/api/plan/constraints", token=token,
                      json={"subject_id": SUBJECT, "teacher_deadline": d2})
        self.assertEqual(r2.status_code, 400, r2.text)
        # 生效截止取更严格者
        constraints = self.req("GET", "/api/plan/constraints", token=token,
                               params={"subject_id": SUBJECT}).json()["data"]
        self.assertEqual(constraints["teacher_deadline"], d1)

    def test_impossible_budget_is_refused_not_silently_overbooked(self):
        """
        预算明显不足时：明确给出冲突与"缩小范围"方案，
        **不生成看起来合理但实际超时的排期**。
        """
        token, uid = self.login("pa_impossible")
        self._make_plan(token, uid)      # 共 95 分钟
        deadline = (date.today() + timedelta(days=1)).isoformat()
        self.req("PUT", "/api/plan/constraints", token=token,
                 json={"subject_id": SUBJECT, "daily_minutes": 20, "deadline": deadline})

        adj = self.req("POST", "/api/plan/adjustments/propose", token=token,
                       json={"subject_id": SUBJECT, "idempotency_key": "impossible-1"}).json()["data"]
        kinds = {c["change_type"] for c in adj["changes"]}
        self.assertIn("reduce_scope", kinds, "预算不足应给出缩小范围方案")
        # 任何重排都不得越过截止日期
        for c in adj["changes"]:
            if c["change_type"] == "reschedule":
                self.assertLessEqual(c["to_date"], deadline,
                                     "生成了超出截止日期的排期")

    def test_propose_is_idempotent_and_major_requires_confirmation(self):
        """相同事件不重复创建建议；重大调整需确认后才应用；应用后遵守截止日期"""
        token, uid = self.login("pa_idem")
        self._make_plan(token, uid)
        deadline = (date.today() + timedelta(days=4)).isoformat()
        self.req("PUT", "/api/plan/constraints", token=token,
                 json={"subject_id": SUBJECT, "daily_minutes": 30, "deadline": deadline})

        r1 = self.req("POST", "/api/plan/adjustments/propose", token=token,
                      json={"subject_id": SUBJECT, "idempotency_key": "evt-1"})
        self.assertEqual(r1.status_code, 200, r1.text)
        adj1 = r1.json()["data"]
        self.assertTrue(adj1["changes"])

        # 幂等：同一事件不重复创建建议
        r2 = self.req("POST", "/api/plan/adjustments/propose", token=token,
                      json={"subject_id": SUBJECT, "idempotency_key": "evt-1"})
        adj2 = r2.json()["data"]
        self.assertEqual(adj2["id"], adj1["id"], "同一事件重复生成了新建议")
        self.assertTrue(adj2.get("duplicate"))

        # 重大调整需确认
        apply_url = f"/api/plan/adjustments/{adj1['id']}/apply"
        if adj1["requires_confirmation"]:
            r = self.req("POST", apply_url, token=token, json={"confirm": False})
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["error_type"], "confirmation_required")
        r = self.req("POST", apply_url, token=token, json={"confirm": True})
        self.assertEqual(r.status_code, 200, r.text)
        applied = r.json()["data"]
        self.assertEqual(applied["status"], "APPLIED")
        self.assertTrue(applied["applied_changes"], applied.get("skipped_changes"))

        # 应用后：任务日期不超过截止日期
        tasks = self.req("GET", "/api/student/tasks", token=token,
                         params={"subject_id": SUBJECT}).json()["data"]
        for t in tasks["todo"]:
            self.assertLessEqual(t["planned_date"], deadline)

    def test_apply_does_not_duplicate_tasks_and_respects_manual_edits(self):
        """应用调整不重复增加任务；学生手动改过的日期不被覆盖"""
        token, uid = self.login("pa_manual")
        plan = self._make_plan(token, uid)
        tasks = self.req("GET", "/api/student/tasks", token=token,
                         params={"subject_id": SUBJECT}).json()["data"]["todo"]
        before = len(tasks)
        target = tasks[0]
        # 学生手动改期
        self.req("PUT", f"/api/student/tasks/{target['id']}/schedule", token=token,
                 json={"new_date": "2099-01-01", "reason": "手动改"})

        adj = self.req("POST", "/api/plan/adjustments/propose", token=token,
                       json={"subject_id": SUBJECT,
                             "idempotency_key": "manual-evt"}).json()["data"]
        applied = self.req("POST", f"/api/plan/adjustments/{adj['id']}/apply",
                           token=token, json={"confirm": True}).json()["data"]

        after = self.req("GET", "/api/student/tasks", token=token,
                         params={"subject_id": SUBJECT}).json()["data"]["todo"]
        self.assertEqual(len(after), before, "调整不应增加或减少任务数量")
        kept = [t for t in after if t["id"] == target["id"]][0]
        self.assertEqual(kept["planned_date"], "2099-01-01", "手动改期被系统覆盖了")

    def test_undo_restores_dates(self):
        """撤销入口把日期改回原值，并保留调整记录"""
        token, uid = self.login("pa_undo")
        self._make_plan(token, uid)
        tasks = {t["id"]: t["planned_date"] for t in
                 self.req("GET", "/api/student/tasks", token=token,
                          params={"subject_id": SUBJECT}).json()["data"]["todo"]}
        self.req("PUT", "/api/plan/constraints", token=token,
                 json={"subject_id": SUBJECT, "daily_minutes": 15})

        adj = self.req("POST", "/api/plan/adjustments/propose", token=token,
                       json={"subject_id": SUBJECT, "idempotency_key": "undo-evt"}).json()["data"]
        self.req("POST", f"/api/plan/adjustments/{adj['id']}/apply", token=token,
                 json={"confirm": True})
        r = self.req("POST", f"/api/plan/adjustments/{adj['id']}/undo", token=token)
        self.assertEqual(r.status_code, 200, r.text)
        after = {t["id"]: t["planned_date"] for t in
                 self.req("GET", "/api/student/tasks", token=token,
                          params={"subject_id": SUBJECT}).json()["data"]["todo"]}
        for tid, d in tasks.items():
            self.assertEqual(after.get(tid), d, f"撤销后任务 {tid} 日期未恢复")

        hist = self.req("GET", "/api/plan/adjustments", token=token,
                        params={"subject_id": SUBJECT}).json()["data"]
        self.assertTrue(any(a["status"] == "UNDONE" for a in hist), "撤销记录应保留")

    def test_constraints_are_user_scoped(self):
        token_a, _ = self.login("pa_a")
        token_b, _ = self.login("pa_b")
        self.req("PUT", "/api/plan/constraints", token=token_a,
                 json={"subject_id": SUBJECT, "daily_minutes": 45})
        cb = self.req("GET", "/api/plan/constraints", token=token_b,
                      params={"subject_id": SUBJECT}).json()["data"]
        self.assertFalse(cb["configured"])
        self.assertNotEqual(cb["daily_minutes"], 45)


# =====================================================================
# 四、Agent 编排
# =====================================================================

class TestAgentRuntime(Phase2Base):

    def test_goal_signals_parsed_by_program(self):
        """日期与时长由程序解析（不交给模型）"""
        from services.agent_runtime import _extract_goal_signals
        sig = _extract_goal_signals("我周五要考这一章，每天只有半小时，帮我复习")
        self.assertEqual(sig.get("daily_minutes"), 30)
        self.assertTrue(sig.get("deadline"))
        self.assertTrue(sig.get("wants_plan"))
        # 截止日应在未来 7 天内
        dl = date.fromisoformat(sig["deadline"])
        self.assertLessEqual((dl - date.today()).days, 7)

        sig2 = _extract_goal_signals("3天后考试，每天1小时")
        self.assertEqual(sig2.get("daily_minutes"), 60)
        self.assertEqual((date.fromisoformat(sig2["deadline"]) - date.today()).days, 3)

    def test_run_records_steps_and_respects_limits(self):
        """运行记录包含状态与工具调用摘要；步骤数不超过限额"""
        token, uid = self.login("ar_run")
        r = self.req("POST", "/api/agent/run", token=token,
                     json={"subject_id": SUBJECT, "goal": "每天半小时，帮我复习二叉树",
                           "intent": "review"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertIn(data["status"], ("SUCCEEDED", "FAILED"))
        self.assertLessEqual(data["steps"], 12)
        self.assertTrue(data["steps_detail"])
        tools = [s["tool"] for s in data["steps_detail"]]
        self.assertIn("get_learning_state", tools)
        self.assertIn("explain_plan", tools)
        # 不记录模型内部思维过程（只有工具调用与摘要）
        for s in data["steps_detail"]:
            self.assertNotIn("reasoning", s)
            self.assertNotIn("thought", s)

    def test_run_sets_constraints_from_goal(self):
        """目标里的"周五/半小时"会落到约束里并被预算检查使用"""
        token, uid = self.login("ar_constraints")
        self.req("POST", "/api/agent/run", token=token,
                 json={"subject_id": SUBJECT, "goal": "我3天后考试，每天只有30分钟，帮我复习"})
        c = self.req("GET", "/api/plan/constraints", token=token,
                     params={"subject_id": SUBJECT}).json()["data"]
        self.assertEqual(c["daily_minutes"], 30)
        self.assertEqual((date.fromisoformat(c["deadline"]) - date.today()).days, 3)

    def test_tool_failure_is_recoverable(self):
        """单个工具失败：不影响其他步骤，运行记录标出失败原因"""
        token, uid = self.login("ar_fail")
        with mock.patch("services.note_service.generate_smart_note",
                        side_effect=RuntimeError("模拟模型不可用")):
            r = self.req("POST", "/api/agent/run", token=token,
                         json={"subject_id": SUBJECT, "goal": "帮我复习二叉树"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertEqual(data["status"], "FAILED")
        failed = [s for s in data["steps_detail"] if s["status"] == "FAILED"]
        self.assertTrue(failed, "失败步骤应被记录")
        self.assertIn("模型不可用", failed[0]["error"])
        # 其他步骤仍然完成
        done = [s["tool"] for s in data["steps_detail"] if s["status"] == "DONE"]
        self.assertIn("get_learning_state", done)
        self.assertIn("explain_plan", done)

    def test_cancel_run(self):
        """取消：运行可被置为 CANCELLED，且记录保留"""
        token, uid = self.login("ar_cancel")
        from services import agent_runtime
        run_id = agent_runtime.create_run(uid, SUBJECT, "目标", "study")
        ok = self.req("POST", f"/api/agent/runs/{run_id}/cancel", token=token)
        self.assertEqual(ok.status_code, 200, ok.text)
        run = self.req("GET", f"/api/agent/runs/{run_id}", token=token).json()["data"]
        self.assertTrue(run["cancel_requested"])

        # 再次运行时会立刻取消（cancel_check 生效）
        out = agent_runtime.run_agent(uid, SUBJECT, "目标", "study",
                                      cancel_check=lambda: True)
        self.assertEqual(out["status"], "CANCELLED")

    def test_timeout_stops_run_and_keeps_partial_results(self):
        """
        超时：超过单次运行时限后必须停止，状态置为 TIMEOUT，已完成的步骤结果保留。

        做法：把时限压到 0 秒（等价于"运行开始后即超时"），并断言：
        - 状态是 TIMEOUT（不是 SUCCEEDED，也不抛异常）
        - 已写入的运行记录仍在（第 0 步"读取学习状态"的结果可用于排查/恢复）
        - 首页/计划等基础功能不依赖这次运行，仍可读取
        """
        token, uid = self.login("ar_timeout")
        from services import agent_runtime
        with mock.patch.dict(agent_runtime.LIMITS, {"max_seconds": 0}):
            out = agent_runtime.run_agent(uid, SUBJECT, "每天半小时，帮我复习二叉树", "review")
        self.assertEqual(out["status"], agent_runtime.STATE_TIMEOUT, out)
        self.assertIn("超时", out.get("error") or "")

        # 运行与步骤记录仍然可查（支持失败恢复）
        run = self.req("GET", f"/api/agent/runs/{out['id']}", token=token).json()["data"]
        self.assertEqual(run["status"], agent_runtime.STATE_TIMEOUT)
        self.assertTrue(run["steps_detail"], "超时前完成的步骤应保留")

        # 基础功能不受影响
        r = self.req("GET", "/api/student/tasks", token=token,
                     params={"subject_id": SUBJECT})
        self.assertEqual(r.status_code, 200, r.text)

    def test_run_via_stream_emits_progress(self):
        """SSE 进度流：能看到步骤与最终结果"""
        token, uid = self.login("ar_stream")
        r = self.req("POST", "/api/agent/run/stream", token=token,
                     json={"subject_id": SUBJECT, "goal": "帮我复习二叉树"})
        self.assertEqual(r.status_code, 200, r.text)
        events = []
        for line in r.text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
        types = [e.get("type") for e in events]
        self.assertIn("step", types)
        self.assertIn("done", types)
        steps = [e for e in events if e["type"] == "step"]
        self.assertTrue(any("label" in s for s in steps), "进度里应包含可展示的步骤名")

    def test_runs_are_user_scoped(self):
        """运行记录与取消都不能跨账号"""
        token_a, uid_a = self.login("ar_a")
        token_b, _ = self.login("ar_b")
        r = self.req("POST", "/api/agent/run", token=token_a,
                     json={"subject_id": SUBJECT, "goal": "帮我复习二叉树"})
        run_id = r.json()["data"]["id"]

        self.assertEqual(self.req("GET", f"/api/agent/runs/{run_id}",
                                  token=token_b).status_code, 404)
        self.assertEqual(self.req("POST", f"/api/agent/runs/{run_id}/cancel",
                                  token=token_b).status_code, 409)
        self.assertEqual(self.req("GET", "/api/agent/runs", token=token_b).json()["data"], [])

    def test_tools_use_current_user_only(self):
        """工具只接受后端身份：跨账号数据不会出现在 A 的 Agent 结果里"""
        token_a, uid_a = self.login("ar_tool_a")
        token_b, _ = self.login("ar_tool_b")
        # B 制造学习记录
        self.make_wrong_attempts(token_b, 2)

        r = self.req("POST", "/api/agent/run", token=token_a,
                     json={"subject_id": SUBJECT, "goal": "帮我复习"})
        state = (r.json()["data"].get("results") or {}).get("learning_state") or {}
        self.assertEqual(state.get("wrong_count"), 0, "A 的运行读到了 B 的记录")

    def test_base_features_work_when_model_unavailable(self):
        """模型不可用时：基础功能（计划查看/错题/判分）仍可用，只有 Agent 明确失败"""
        token, uid = self.login("ar_degraded")
        quiz = self.gen_quiz(token)
        q = next(x for x in quiz if x["type"] == "single_choice")

        with mock.patch("services.ai_service.call_llm",
                        side_effect=RuntimeError("模型 API Key 无效")), \
             mock.patch("services.note_service.call_llm",
                        side_effect=RuntimeError("模型 API Key 无效")), \
             mock.patch("services.learning_service.call_llm",
                        side_effect=RuntimeError("模型 API Key 无效")):
            # 1) Agent 明确失败（不静默伪装成功）
            r = self.req("POST", "/api/agent/run", token=token,
                         json={"subject_id": SUBJECT, "goal": "帮我复习二叉树"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["data"]["status"], "FAILED")

            # 2) 判分仍然可用（不依赖模型）
            ans = self.answer(token, q["id"], "A", "degraded-1")
            self.assertFalse(ans["is_correct"])

            # 3) 错题、任务、掌握度仍然可用
            self.assertEqual(self.req("GET", "/api/quiz/wrong", token=token,
                                      params={"subject_id": SUBJECT}).status_code, 200)
            self.assertEqual(self.req("GET", "/api/student/tasks", token=token,
                                      params={"subject_id": SUBJECT}).status_code, 200)
            self.assertEqual(self.req("GET", "/api/student/mastery", token=token,
                                      params={"subject_id": SUBJECT}).status_code, 200)
            # 4) 手动改期仍然可用
            tasks = self.req("GET", "/api/student/tasks", token=token,
                             params={"subject_id": SUBJECT}).json()["data"]["todo"]
            if tasks:
                r2 = self.req("PUT", f"/api/student/tasks/{tasks[0]['id']}/schedule",
                              token=token, json={"new_date": "2099-03-03"})
                self.assertEqual(r2.status_code, 200, r2.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
