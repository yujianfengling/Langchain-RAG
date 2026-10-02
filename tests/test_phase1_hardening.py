"""
阶段一补验专项测试

覆盖本轮要求核查/修复的四类问题：

1. 答案真隔离：出题响应 / SSE / 历史消息 / 笔记 payload 都不含标准答案；
   「查看答案」由后端持久化，换客户端或伪造请求都不能把"看过答案后的答对"
   重新算成独立掌握证据。
2. 复习完成语义：after_reveal 答对不关闭待办；自评不冒充客观判分；
   一次独立答对可关闭当前复习任务但不认定长期掌握；未巩固则安排延后复测。
3. 对象级权限：跨账号替换资源 ID / 路径 user_id / 请求参数无法读写他人数据；
   课程公共资料与个人资料区分；受保护文件下载需要身份。
4. 事件重试跨库幂等：部分步骤成功后失败，重试不重复写入（作答证据不重复累加、
   复习任务不重复创建/不重复合并）。

全部在隔离夹具（临时 data/ 与临时库）中运行，Mock 外部模型，不触碰正式数据。
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import asyncio
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures_isolated import IsolatedDataTestCase

SUBJECT = "数据结构"
KP = "二叉树"
# 未注册（不存在）的学科，用于验证"知识库未就绪"路径
MISSING_SUBJECT = "不存在的学科"


def _quiz_llm_output():
    return json.dumps({
        "quiz": [
            {"question": "二叉树的每个节点最多有几个子节点？",
             "type": "single_choice", "options": ["1 个", "2 个", "3 个"],
             "answer": "B", "explanation": "二叉树定义：每个节点最多两个子树。",
             "knowledge_point": KP},
            {"question": "请简述二叉树前序遍历的顺序。",
             "type": "short_answer", "options": [],
             "answer": "根-左-右", "explanation": "前序：根-左-右。",
             "knowledge_point": KP},
        ]
    }, ensure_ascii=False)


def _note_llm_output():
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


class HardeningTestBase(IsolatedDataTestCase):
    SUBJECTS = (SUBJECT,)

    def setUp(self):
        super().setUp()
        from api.app import app
        self._app = app
        self._patch_llm()
        self._clients = []

    def tearDown(self):
        for c in self._clients:
            try:
                asyncio.get_event_loop().run_until_complete(c.aclose())
            except Exception:
                pass
        super().tearDown()

    def _patch_llm(self):
        self._llm_patch = mock.patch(
            "services.note_service.call_llm",
            side_effect=lambda prompt, system_message=None, model=None, **kw:
            _note_llm_output() if "五段式" in prompt else _quiz_llm_output())
        self._llm_patch.start()
        self._rag_patch = mock.patch(
            "services.note_service.retrieve_and_build_context",
            return_value={"context": "二叉树：每个节点最多有两个子树。前序遍历：根-左-右。",
                          "sources": [{"id": 1, "file_name": "tree.pdf", "page": 3,
                                       "type": "text"}],
                          "context_used": True, "documents": []})
        self._rag_patch.start()
        # 简答题 AI 判分（缺省判为答对）
        self._short_patch = mock.patch(
            "services.quiz_service.call_llm",
            side_effect=lambda prompt, system_message=None, model=None, **kw:
            json.dumps({"is_correct": True, "reason": "要点齐全"}, ensure_ascii=False))
        self._short_patch.start()
        self.addCleanup(self._llm_patch.stop)
        self.addCleanup(self._rag_patch.stop)
        self.addCleanup(self._short_patch.stop)

    # ---------- HTTP 辅助（每个 client 独立，用于模拟"换客户端"） ----------

    def new_client(self):
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=self._app),
                              base_url="http://test")
        self._clients.append(c)
        return c

    def req(self, method, url, token=None, client=None, **kw):
        headers = kw.pop("headers", {}) or {}
        if token:
            headers["X-User-Token"] = token
        c = client or self._clients[0] if self._clients else self.new_client()
        return asyncio.get_event_loop().run_until_complete(
            c.request(method, url, headers=headers, **kw))

    def login(self, username, client=None):
        c = client or self.new_client()
        if c not in self._clients:
            self._clients.append(c)
        r = asyncio.get_event_loop().run_until_complete(
            c.post("/api/auth/register",
                   json={"username": username, "password": "test-pass-123"}))
        assert r.status_code == 200, r.text
        return r.json()["data"]["token"], r.json()["data"]["id"]

    def gen_quiz(self, token, client=None, subject=SUBJECT, topic=KP):
        r = self.req("POST", "/api/chat", token=token, client=client,
                     json={"question": topic, "subject_id": subject, "mode": "quiz"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("success"), body
        return (body.get("payload") or {}).get("quiz") or []


# =====================================================================
# 一、答案真隔离
# =====================================================================

class TestAnswerIsolation(HardeningTestBase):
    ANSWER_KEYS = ("answer", "explanation", "correct_option_id", "correct_option_text",
                   "reference_answer")

    def _assert_no_answer(self, obj, path=""):
        """递归断言：结构里不出现任何答案字段"""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in self.ANSWER_KEYS and v not in (None, "", []):
                    self.fail(f"响应泄露答案字段 {path}.{k} = {v!r}")
                self._assert_no_answer(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                self._assert_no_answer(v, f"{path}[{i}]")

    def test_quiz_payload_has_no_answers(self):
        token, _ = self.login("iso_quiz")
        quiz = self.gen_quiz(token)
        self.assertTrue(quiz)
        self._assert_no_answer(quiz, "quiz")

    def test_note_payload_has_no_answers(self):
        token, _ = self.login("iso_note")
        r = self.req("POST", "/api/chat", token=token,
                     json={"question": KP, "subject_id": SUBJECT, "mode": "smart_note"})
        self.assertTrue(r.json().get("success"), r.text)
        payload = r.json().get("payload") or {}
        self.assertTrue(payload.get("quiz"))
        self._assert_no_answer(payload.get("quiz"), "note.quiz")

    def test_sse_events_have_no_answers(self):
        """SSE 的 done 事件也不能夹带答案"""
        token, _ = self.login("iso_sse")
        conv = self.req("POST", "/api/conversations", token=token,
                        json={"subject_id": SUBJECT}).json()["data"]["id"]
        body = self.req("POST", "/api/chat/stream", token=token,
                        json={"question": KP, "subject_id": SUBJECT,
                              "conversation_id": conv, "mode": "quiz"})
        self.assertEqual(body.status_code, 200, body.text)
        events = []
        for line in body.text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
        self.assertTrue(any(e.get("type") == "done" for e in events), events[:2])
        for e in events:
            self._assert_no_answer(e, f"sse.{e.get('type')}")

    def test_history_messages_have_no_answers(self):
        """历史消息（服务端持久化 payload）也不能含答案"""
        token, _ = self.login("iso_history")
        conv = self.req("POST", "/api/conversations", token=token,
                        json={"subject_id": SUBJECT}).json()["data"]["id"]
        self.req("POST", "/api/chat", token=token,
                 json={"question": KP, "subject_id": SUBJECT,
                       "conversation_id": conv, "mode": "quiz"})
        msgs = self.req("GET", f"/api/conversations/{conv}/messages", token=token).json()["data"]
        self.assertTrue(msgs)
        for m in msgs:
            self._assert_no_answer(m.get("payload"), f"msg[{m['id']}]")

    def test_wrong_book_hides_answers_until_explicitly_revealed(self):
        token, _ = self.login("iso_wrong")
        quiz = self.gen_quiz(token)
        choice = next(q for q in quiz if q["type"] == "single_choice")

        # 先答错（此时前端已通过提交响应拿到答案，这是允许的）
        r = self.req("POST", "/api/quiz/answer", token=token,
                     json={"question_id": choice["id"], "user_answer": "A",
                           "client_token": "iso-wrong-1"})
        self.assertFalse(r.json()["data"]["is_correct"])

        # 错题列表：默认不带答案
        wrong = self.req("GET", "/api/quiz/wrong", token=token,
                         params={"subject_id": SUBJECT}).json()["data"]
        self.assertEqual(len(wrong), 1)
        self.assertFalse(wrong[0]["revealed"])
        self.assertFalse(wrong[0]["answers_included"])
        self._assert_no_answer(wrong, "wrong_list")

        # 首页同样不带答案
        home = self.req("GET", "/api/student/home", token=token,
                        params={"subject_id": SUBJECT}).json()["data"]
        self._assert_no_answer(home.get("wrong_questions"), "home.wrong_questions")

    def test_reveal_is_server_side_and_survives_new_client(self):
        """
        查看答案的状态由后端保存：换一个客户端（新连接、同一账号）后，
        该题的作答仍被判定为 after_reveal，不计入独立掌握证据。
        """
        token, _ = self.login("iso_reveal")
        client_a = self.new_client()
        quiz = self.gen_quiz(token, client=client_a)
        choice = next(q for q in quiz if q["type"] == "single_choice")

        # 客户端 A：从错题解析入口查看答案
        self.req("POST", "/api/quiz/answer", token=token, client=client_a,
                 json={"question_id": choice["id"], "user_answer": "A",
                       "client_token": "iso-rev-1"})
        detail = self.req("GET", f"/api/quiz/wrong/{choice['id']}", token=token,
                          client=client_a)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertTrue(detail.json()["data"]["reveal_recorded"])
        self.assertEqual(detail.json()["data"]["correct_option_id"], "B")

        # 客户端 B：全新连接、同一账号 → 再答对，必须仍是 after_reveal
        client_b = self.new_client()
        r = self.req("POST", "/api/quiz/answer", token=token, client=client_b,
                     json={"question_id": choice["id"], "user_answer": "B",
                           "client_token": "iso-rev-2"})
        data = r.json()["data"]
        self.assertTrue(data["is_correct"])
        self.assertEqual(data["attempt_kind"], "after_reveal",
                         "换客户端后查看答案状态丢失，说明状态没存在后端")

        mastery = self.req("GET", "/api/student/mastery", token=token,
                           params={"subject_id": SUBJECT}).json()["data"]
        point = next(p for p in mastery["points"] if p["knowledge_point"] == KP)
        self.assertEqual(point["evidence_count"], 1, "只有第一次答错是独立证据")
        self.assertEqual(point["excluded_count"], 1, "看答案后的作答应计入排除数")

    def test_forged_request_cannot_fake_independence(self):
        """伪造请求体（revealed / attempt_kind / is_correct）一律无效"""
        token, _ = self.login("iso_forge")
        quiz = self.gen_quiz(token)
        choice = next(q for q in quiz if q["type"] == "single_choice")

        # 先真实查看答案
        self.req("POST", f"/api/quiz/reveal/{choice['id']}", token=token)

        r = self.req("POST", "/api/quiz/answer", token=token, json={
            "question_id": choice["id"], "user_answer": "B",
            "client_token": "iso-forge-1",
            # 以下都是伪造字段，后端必须忽略
            "revealed": False, "revealed_answer": False,
            "attempt_kind": "independent", "is_correct": True,
        })
        data = r.json()["data"]
        self.assertTrue(data["is_correct"], "答案本身确实正确")
        self.assertEqual(data["attempt_kind"], "after_reveal", "伪造 attempt_kind 生效了")

        mastery = self.req("GET", "/api/student/mastery", token=token,
                           params={"subject_id": SUBJECT}).json()["data"]
        point = next(p for p in mastery["points"] if p["knowledge_point"] == KP)
        self.assertEqual(point["evidence_count"], 0, "看答案后答对被算成独立证据")

    def test_unpersisted_question_cannot_be_graded(self):
        """未入库题目不能提交判分（不能凭空判对错）"""
        token, _ = self.login("iso_unpersisted")
        r = self.req("POST", "/api/quiz/answer", token=token,
                     json={"question_id": "q_not_exists", "user_answer": "A"})
        self.assertEqual(r.status_code, 404, r.text)

    def test_invalid_option_is_rejected_not_counted_as_wrong(self):
        """
        非法选项必须被拒绝（参数校验由程序执行），不能悄悄记成"答错"。

        否则伪造一次 Z/空值提交就能污染错题本与掌握度证据。
        """
        token, _ = self.login("iso_badopt")
        quiz = self.gen_quiz(token)
        choice = next(q for q in quiz if q["type"] == "single_choice")

        before = self.req("GET", "/api/student/mastery", token=token,
                          params={"subject_id": SUBJECT}).json()["data"]
        before_n = sum(p["evidence_count"] for p in before["points"])

        r = self.req("POST", "/api/quiz/answer", token=token, json={
            "question_id": choice["id"], "user_answer": "Z",
            "client_token": "iso-badopt-1"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json().get("error_type"), "invalid_request", r.text)

        after = self.req("GET", "/api/student/mastery", token=token,
                         params={"subject_id": SUBJECT}).json()["data"]
        after_n = sum(p["evidence_count"] for p in after["points"])
        self.assertEqual(after_n, before_n, "非法选项被计入了作答证据")


# =====================================================================
# 二、复习完成语义
# =====================================================================

class TestReviewSemantics(HardeningTestBase):

    def _answer(self, token, qid, answer, ctoken):
        return self.req("POST", "/api/quiz/answer", token=token,
                        json={"question_id": qid, "user_answer": answer,
                              "subject_id": SUBJECT, "client_token": ctoken}).json()["data"]

    def test_after_reveal_correct_does_not_close_review(self):
        """看过答案后答对：不关闭待办复习，也不产生独立证据"""
        token, _ = self.login("rvw_reveal")
        quiz = self.gen_quiz(token)
        choice = next(q for q in quiz if q["type"] == "single_choice")

        self._answer(token, choice["id"], "A", "rvw-1")           # 独立答错 → 建待办
        pending = self.req("GET", "/api/quiz/reviews", token=token,
                           params={"subject_id": SUBJECT, "status": "PENDING"}).json()["data"]
        self.assertEqual(len(pending), 1)

        self.req("POST", f"/api/quiz/reveal/{choice['id']}", token=token)   # 看答案
        data = self._answer(token, choice["id"], "B", "rvw-2")    # 看答案后答对
        self.assertEqual(data["attempt_kind"], "after_reveal")

        still = self.req("GET", "/api/quiz/reviews", token=token,
                         params={"subject_id": SUBJECT, "status": "PENDING"}).json()["data"]
        self.assertEqual(len(still), 1, "看答案后的答对不应关闭复习待办")

    def test_independent_correct_closes_review_and_schedules_recheck(self):
        """独立/复习答对可关闭当前待办，但未巩固 → 安排延后复测"""
        token, _ = self.login("rvw_close")
        quiz = self.gen_quiz(token)
        choice = next(q for q in quiz if q["type"] == "single_choice")

        self._answer(token, choice["id"], "A", "rvw2-1")           # 答错 → 待办
        data = self._answer(token, choice["id"], "B", "rvw2-2")    # 复习答对
        self.assertEqual(data["attempt_kind"], "review")

        done = [r for r in self.req("GET", "/api/quiz/reviews", token=token,
                                    params={"subject_id": SUBJECT}).json()["data"]
                if r["status"] == "DONE"]
        self.assertEqual(len(done), 1, "复习答对应关闭待办")
        pending = self.req("GET", "/api/quiz/reviews", token=token,
                           params={"subject_id": SUBJECT, "status": "PENDING"}).json()["data"]
        self.assertEqual(len(pending), 1, "未巩固应安排延后复测")
        self.assertIn("延后复测", pending[0]["reason"] or "")

        mastery = self.req("GET", "/api/student/mastery", token=token,
                           params={"subject_id": SUBJECT}).json()["data"]
        point = next(p for p in mastery["points"] if p["knowledge_point"] == KP)
        self.assertFalse(point["consolidated"], "一次复习答对不等于长期掌握")
        self.assertEqual(point["consolidation_status"], "unverified")

    def test_ai_graded_short_answer_is_evidence_and_self_eval_can_override(self):
        """简答题：AI 判分计入证据；用户自评可覆盖判定，但不重复计入证据

        变更说明（2026-10-02）：简答题原先一律 pending_self_eval（不进掌握证据）。
        现改为有参考答案/解析时由 AI 判分（ai_graded），并保留
        「我答对了 / 我答错了」两个按钮让用户覆盖。
        变更说明（2026-10-02 晚）：自评"答错"现在会**重算掌握度**（单向从严）。
        此前只改写作答记录、掌握度停在 AI 的结论，造成"掌握度与作答记录打架"；
        现在按作答记录重放：AI 的"对"被撤回，记为"错"。同一次作答仍只算一条证据。
        """
        token, _ = self.login("rvw_selfeval")
        quiz = self.gen_quiz(token)
        short = next(q for q in quiz if q["type"] == "short_answer")

        data = self._answer(token, short["id"], "根左右", "rvw3-1")
        self.assertEqual(data["grade_status"], "ai_graded")
        self.assertTrue(data["is_correct"])

        mastery = self.req("GET", "/api/student/mastery", token=token,
                           params={"subject_id": SUBJECT}).json()["data"]
        point = next(p for p in mastery["points"] if p["knowledge_point"] == KP)
        self.assertEqual(point["evidence_count"], 1, "AI 判分应作为一条证据")

        # 用户推翻 AI 判定：作答记录改为"错"，但不追加第二条证据
        r = self.req("POST", "/api/quiz/self-evaluate", token=token,
                     json={"attempt_id": data["attempt_id"], "is_correct": False})
        self.assertEqual(r.json()["data"]["grade_status"], "self_evaluated")

        mastery2 = self.req("GET", "/api/student/mastery", token=token,
                            params={"subject_id": SUBJECT}).json()["data"]
        point2 = next(p for p in mastery2["points"] if p["knowledge_point"] == KP)
        self.assertEqual(point2["evidence_count"], 1, "同一次作答不该被记成两条证据")
        self.assertEqual(point2["independent_correct_count"], 0, "AI 的'对'没有被撤回")
        self.assertEqual(point2["independent_wrong_count"], 1, "改判的'错'没有计入")

        wrong = self.req("GET", "/api/quiz/wrong", token=token,
                         params={"subject_id": SUBJECT}).json()["data"]
        self.assertTrue(any(w["question_id"] == short["id"] for w in wrong),
                        "覆盖为答错后该题应进入错题本")

    def test_self_eval_correct_cannot_erase_wrong_evidence(self):
        """单向从严：AI 判错后学生点「我答对了」，撤不掉那条错误证据

        这就是自评不能"完全生效"的原因：否则点一下就能把自己洗白。
        """
        token, _ = self.login("rvw_strict_right")
        quiz = self.gen_quiz(token)
        short = next(q for q in quiz if q["type"] == "short_answer")

        # 让 AI 判这道题"错"
        with mock.patch("services.quiz_service.call_llm",
                        side_effect=lambda *a, **k: json.dumps(
                            {"is_correct": False, "reason": "要点缺失"},
                            ensure_ascii=False)):
            data = self._answer(token, short["id"], "不知道", "rvw-strict-r1")
        self.assertEqual(data["grade_status"], "ai_graded")
        self.assertFalse(data["is_correct"])

        before = self.req("GET", "/api/student/mastery", token=token,
                          params={"subject_id": SUBJECT}).json()["data"]
        p1 = next(p for p in before["points"] if p["knowledge_point"] == KP)
        self.assertEqual(p1["independent_wrong_count"], 1)

        # 学生自称答对
        self.req("POST", "/api/quiz/self-evaluate", token=token,
                 json={"attempt_id": data["attempt_id"], "is_correct": True})

        after = self.req("GET", "/api/student/mastery", token=token,
                         params={"subject_id": SUBJECT}).json()["data"]
        p2 = next(p for p in after["points"] if p["knowledge_point"] == KP)
        self.assertEqual(p2["independent_wrong_count"], 1,
                         "自评'答对'把错误证据抹掉了——会给刷分留口子")

    def test_self_eval_wrong_creates_review_task(self):
        """自评"答错"要补触发复习安排（此前自评不参与联动）"""
        token, _ = self.login("rvw_selfeval_wrong")
        quiz = self.gen_quiz(token)
        short = next(q for q in quiz if q["type"] == "short_answer")

        with mock.patch("services.quiz_service.call_llm",
                        side_effect=RuntimeError("model down")):
            data = self._answer(token, short["id"], "不知道", "rvw-se-w1")
        self.assertEqual(data["grade_status"], "pending_self_eval")

        self.req("POST", "/api/quiz/self-evaluate", token=token,
                 json={"attempt_id": data["attempt_id"], "is_correct": False})

        pending = self.req("GET", "/api/quiz/reviews", token=token,
                           params={"subject_id": SUBJECT,
                                   "status": "PENDING"}).json()["data"]
        self.assertTrue(any(r["knowledge_point_name"] == KP for r in pending),
                        "自评答错后应生成该知识点的待办复习")

    def test_self_eval_correct_cannot_close_review(self):
        """自评"答对"不能关闭待办复习（自评不证明掌握）"""
        token, _ = self.login("rvw_selfeval_right")
        quiz = self.gen_quiz(token)
        choice = next(q for q in quiz if q["type"] == "single_choice")
        short = next(q for q in quiz if q["type"] == "short_answer")

        # 先独立答错客观题 → 生成待办复习
        wrong_option = next(o["id"] for o in choice["options"] if o["id"] != "B")
        data1 = self._answer(token, choice["id"], wrong_option, "rvw-se-c1")
        self.assertFalse(data1["is_correct"])
        pending = self.req("GET", "/api/quiz/reviews", token=token,
                           params={"subject_id": SUBJECT,
                                   "status": "PENDING"}).json()["data"]
        self.assertEqual(len(pending), 1, "独立答错应生成待办复习")

        # 简答题自评"答对"：不得关闭该待办
        with mock.patch("services.quiz_service.call_llm",
                        side_effect=RuntimeError("model down")):
            data2 = self._answer(token, short["id"], "根左右", "rvw-se-c2")
        self.assertEqual(data2["grade_status"], "pending_self_eval")
        self.req("POST", "/api/quiz/self-evaluate", token=token,
                 json={"attempt_id": data2["attempt_id"], "is_correct": True})

        pending2 = self.req("GET", "/api/quiz/reviews", token=token,
                            params={"subject_id": SUBJECT,
                                    "status": "PENDING"}).json()["data"]
        self.assertEqual(len(pending2), 1, "自评答对不该关闭待办复习")

    def test_short_answer_falls_back_to_self_eval_when_ai_unavailable(self):
        """AI 判分不可用时降级为待自评，不伪造判分"""
        token, _ = self.login("rvw_selfeval_fb")
        quiz = self.gen_quiz(token)
        short = next(q for q in quiz if q["type"] == "short_answer")

        with mock.patch("services.quiz_service.call_llm",
                        side_effect=RuntimeError("model down")):
            data = self._answer(token, short["id"], "根左右", "rvw3-fb-1")

        self.assertEqual(data["grade_status"], "pending_self_eval")
        self.assertIsNone(data["is_correct"])
        self.assertIsNone(data.get("ai_feedback"))

    def test_consolidation_requires_spaced_repetition(self):
        """间隔复现后才认定长期掌握（构造首次答对在 2 天前）"""
        token, uid = self.login("rvw_consolidate")
        quiz = self.gen_quiz(token)
        choice = next(q for q in quiz if q["type"] == "single_choice")
        self._answer(token, choice["id"], "B", "rvw4-1")   # 独立答对

        from services import mastery_service
        from services.learning_data import _now
        from datetime import datetime, timedelta
        # 把"首次答对时间"回拨 2 天，模拟间隔复现
        conn = mastery_service._get_db()
        try:
            past = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
            conn.execute("UPDATE knowledge_mastery SET first_correct_at=?, updated_at=? "
                         "WHERE user_id=? AND subject_id=?",
                         (past, past, uid, SUBJECT))
            conn.commit()
        finally:
            conn.close()

        # 第二次独立答对（同一题重做，kind=review，但也提供 correct 时间）
        self._answer(token, choice["id"], "B", "rvw4-2")

        mastery = self.req("GET", "/api/student/mastery", token=token,
                           params={"subject_id": SUBJECT}).json()["data"]
        point = next(p for p in mastery["points"] if p["knowledge_point"] == KP)
        self.assertEqual(point["evidence_count"], 1, "独立证据只有一次")
        self.assertTrue(point["sample_size"] >= 1)
        self.assertTrue(point["mastery_is_estimate"], "掌握度必须标注为估计值")
        self.assertIn("估计", point["reason"] or "")

    def test_mastery_exposes_sample_size_and_basis(self):
        """掌握度必须展示样本量与计算依据（不是只有一个百分比）"""
        token, _ = self.login("rvw_basis")
        quiz = self.gen_quiz(token)
        choice = next(q for q in quiz if q["type"] == "single_choice")
        self._answer(token, choice["id"], "A", "rvw5-1")

        mastery = self.req("GET", "/api/student/mastery", token=token,
                           params={"subject_id": SUBJECT}).json()["data"]
        point = next(p for p in mastery["points"] if p["knowledge_point"] == KP)
        self.assertEqual(point["sample_size"], 1)
        bd = point["evidence_breakdown"]
        self.assertEqual(bd["independent_wrong"], 1)
        self.assertEqual(bd["review_correct"], 0)
        self.assertIn("rules", point)
        self.assertIn("independent_correct", bd)


# =====================================================================
# 三、对象级权限（跨账号）
# =====================================================================

class TestObjectLevelPermissions(HardeningTestBase):

    def test_cross_account_conversation_access_denied(self):
        token_a, _ = self.login("perm_conv_a")
        token_b, _ = self.login("perm_conv_b")

        conv_a = self.req("POST", "/api/conversations", token=token_a,
                          json={"subject_id": SUBJECT}).json()["data"]["id"]

        # B 看不到 A 的会话（列表隐藏 + 直接按 ID 访问 404）
        ids_b = [c["id"] for c in self.req("GET", "/api/conversations",
                                          token=token_b).json()["data"]]
        self.assertNotIn(conv_a, ids_b)
        self.assertEqual(self.req("GET", f"/api/conversations/{conv_a}",
                                  token=token_b).status_code, 404)
        self.assertEqual(self.req("GET", f"/api/conversations/{conv_a}/messages",
                                  token=token_b).status_code, 404)
        self.assertEqual(self.req("DELETE", f"/api/conversations/{conv_a}",
                                  token=token_b).status_code, 404)
        # A 的会话仍然存在
        self.assertEqual(self.req("GET", f"/api/conversations/{conv_a}",
                                  token=token_a).status_code, 200)

    def test_cross_account_cannot_write_into_others_conversation(self):
        """借他人 conversation_id 发消息 → 不允许写入（也读不到历史）"""
        token_a, _ = self.login("perm_chat_a")
        token_b, _ = self.login("perm_chat_b")
        conv_a = self.req("POST", "/api/conversations", token=token_a,
                          json={"subject_id": SUBJECT}).json()["data"]["id"]

        with mock.patch("chat_service.get_rag_context", return_value=[]), \
             mock.patch("chat_service._call_llm_stream",
                        return_value=iter(["不该被写进别人会话"])), \
             mock.patch("services.adaptive_retriever.get_personalized_system_prompt",
                        side_effect=lambda uid, sid, base, *a, **k: base):
            r = self.req("POST", "/api/chat/stream", token=token_b,
                         json={"question": "入侵", "subject_id": SUBJECT,
                               "conversation_id": conv_a})

        self.assertEqual(r.status_code, 200, r.text)
        msgs = self.req("GET", f"/api/conversations/{conv_a}/messages",
                        token=token_a).json()["data"]
        self.assertEqual(msgs, [], "他人请求被写进了 A 的会话")

    def test_cross_account_quiz_attempt_with_foreign_source_task(self):
        """提交作答时带上他人任务 ID → 拒绝（不能把作答挂到他人任务）"""
        token_a, _ = self.login("perm_task_a")
        token_b, _ = self.login("perm_task_b")
        quiz = self.gen_quiz(token_a)
        q = quiz[0]

        from services import learning_service
        task_a = learning_service.create_auto_review_task(
            user_id=self._uid(token_a), subject_id=SUBJECT,
            knowledge_point_id="kp_perm", knowledge_point_name="权限知识点",
            planned_date="2099-01-01", reason="单测构造")

        r = self.req("POST", "/api/quiz/answer", token=token_b,
                     json={"question_id": q["id"], "user_answer": "A",
                           "source_task_id": task_a, "client_token": "perm-1"})
        self.assertEqual(r.status_code, 403, r.text)

    def test_cross_account_plan_and_task_access_denied(self):
        token_a, _ = self.login("perm_plan_a")
        token_b, _ = self.login("perm_plan_b")
        from services import learning_service
        uid_a = self._uid(token_a)
        plan = learning_service._save_plan(
            {"id": None, "subject_id": SUBJECT, "title": "A 的计划",
             "goal": "g", "user_id": uid_a}, [])
        plan_id = plan["id"]
        task_a = learning_service.create_auto_review_task(
            user_id=uid_a, subject_id=SUBJECT, knowledge_point_id="kp_p2",
            knowledge_point_name="权限知识点2", planned_date="2099-01-01",
            reason="单测构造")

        self.assertEqual(self.req("GET", f"/api/learning/plans/{plan_id}",
                                  token=token_b).status_code, 404)
        self.assertEqual(self.req("DELETE", f"/api/learning/plans/{plan_id}",
                                  token=token_b).status_code, 404)
        # 任务：改期 / 完成 / 改期历史 都不可越权
        self.assertEqual(self.req("PUT", f"/api/student/tasks/{task_a}/schedule",
                                  token=token_b,
                                  json={"new_date": "2099-02-02"}).status_code, 403)
        self.assertEqual(self.req("POST", f"/api/student/tasks/{task_a}/complete",
                                  token=token_b, json={}).status_code, 403)
        adj = self.req("GET", f"/api/student/tasks/{task_a}/adjustments",
                       token=token_b).json()["data"]
        self.assertEqual(adj, [], "可读取他人任务的改期历史")

    def test_cross_account_feedback_cannot_overwrite_others_interaction(self):
        token_a, uid_a = self.login("perm_fb_a")
        token_b, _ = self.login("perm_fb_b")

        from services import user_profiler
        iid = user_profiler.record_interaction(
            user_id=uid_a, interaction_type="chat", subject_id=SUBJECT,
            question="A 的问题", answer="A 的回答")

        # B 提交反馈并试图关联 A 的交互
        self.req("POST", "/api/agent/feedback", token=token_b,
                 json={"interaction_id": iid, "feedback_type": "rating",
                       "feedback_score": -1})

        conn = user_profiler._get_db()
        try:
            row = conn.execute("SELECT feedback_score FROM user_interactions WHERE id=?",
                               (iid,)).fetchone()
        finally:
            conn.close()
        self.assertIn(row["feedback_score"], (0, None),
                      "他人反馈覆写了 A 的交互评分")

    def test_cross_account_cannot_read_others_mastery_or_events(self):
        token_a, _ = self.login("perm_m_a")
        token_b, _ = self.login("perm_m_b")
        quiz = self.gen_quiz(token_a)
        self.req("POST", "/api/quiz/answer", token=token_a,
                 json={"question_id": quiz[0]["id"], "user_answer": "A",
                       "subject_id": SUBJECT, "client_token": "perm-m-1"})

        mastery_b = self.req("GET", "/api/student/mastery", token=token_b,
                             params={"subject_id": SUBJECT}).json()["data"]
        self.assertEqual(mastery_b["points"], [], "B 看到了 A 的掌握度")

        wrong_b = self.req("GET", "/api/quiz/wrong", token=token_b,
                           params={"subject_id": SUBJECT}).json()["data"]
        self.assertEqual(wrong_b, [])

        events_b = self.req("GET", "/api/student/learning-events", token=token_b,
                            params={"subject_id": SUBJECT}).json()["data"]
        self.assertEqual(events_b, [])

        home_b = self.req("GET", "/api/student/home", token=token_b,
                          params={"subject_id": SUBJECT}).json()["data"]
        self.assertEqual(home_b["quiz_stats"]["total_attempts"], 0)

    def test_event_retry_is_scoped_to_current_user(self):
        """事件重试不能重放他人事件"""
        from services import learning_events
        token_a, uid_a = self.login("perm_ev_a")
        token_b, _ = self.login("perm_ev_b")

        # 造一个属于 A 的失败事件
        calls = {"n": 0}

        def _bad_handler(ctx):
            calls["n"] += 1
            raise RuntimeError("模拟处理器失败")

        learning_events.register_handler("perm_test_event", _bad_handler)
        learning_events.emit_event("perm_test_event", "perm-evt-1", user_id=uid_a,
                                   subject_id=SUBJECT, payload={})

        r = self.req("POST", "/api/student/learning-events/retry", token=token_b)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["retried"], 0, "B 重试了 A 的事件")
        before = calls["n"]
        self.req("POST", "/api/student/learning-events/retry", token=token_a)
        self.assertGreater(calls["n"], before, "A 自己应能重试自己的失败事件")

    def test_write_endpoints_require_login_cross_check(self):
        """未登录写入类接口一律 401（逐项确认，不只看学生端）"""
        checks = [
            ("POST", "/api/quiz/answer", {"question_id": "q", "user_answer": "A"}),
            ("POST", "/api/conversations", {"subject_id": SUBJECT}),
            ("GET", "/api/conversations", None),
            ("GET", "/api/student/home", None),
            ("GET", "/api/quiz/wrong", None),
            ("POST", "/api/student/learning-events/retry", None),
            ("GET", "/api/knowledge", None),
            ("POST", "/api/subjects", None),   # 未登录 → 401（先于参数校验）
        ]
        for method, url, body in checks:
            kw = {"json": body} if body else {}
            r = self.req(method, url, **kw)
            self.assertEqual(r.status_code, 401, f"{method} {url} 未要求登录: {r.text}")

    def test_course_material_download_requires_identity(self):
        """受保护文件下载入口必须校验身份（课程公共资料也要登录）"""
        from services.knowledge_service import _resolve_path
        d = _resolve_path(f"data/subjects/{SUBJECT}")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "lesson.md"), "w", encoding="utf-8") as f:
            f.write("# 课程资料")

        r = self.req("GET", f"/api/knowledge/{SUBJECT}/documents/lesson.md/file")
        self.assertEqual(r.status_code, 401, "未登录即可下载课程文件")

        token, _ = self.login("perm_dl")
        r2 = self.req("GET", f"/api/knowledge/{SUBJECT}/documents/lesson.md/file",
                      token=token)
        self.assertEqual(r2.status_code, 200, r2.text)

    def test_knowledge_build_rejects_path_outside_controlled_roots(self):
        """建库接口不能扫描受控目录之外的服务器路径"""
        token, _ = self.login("perm_build")
        r = self.req("POST", "/api/knowledge/build", token=token,
                     json={"subject_id": SUBJECT, "path": "C:/Windows"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("受控目录", r.text)

    def test_knowledge_admin_allowlist_enforced_when_configured(self):
        """
        配置 KNOWLEDGE_ADMIN_USERS 后，非白名单账号不能管理课程资料。
        未配置时为"任一登录用户可管理"，该分支由上一条用例（允许登录用户）覆盖。
        """
        token, _ = self.login("perm_admin_normal")
        with mock.patch.dict(os.environ, {"KNOWLEDGE_ADMIN_USERS": "someone_else"}):
            r = self.req("POST", "/api/subjects", token=token,
                         params={"subject_id": "白名单测试学科", "name": "白名单测试学科"})
            self.assertEqual(r.status_code, 403, r.text)
        # 未配置白名单 → 放行（单用户/开发模式）
        r2 = self.req("POST", "/api/subjects", token=token,
                      params={"subject_id": "新学科", "name": "新学科"})
        self.assertEqual(r2.status_code, 200, r2.text)

    # ---------- 工具 ----------

    def _uid(self, token):
        from services import auth_service
        return auth_service.get_user_id_by_token(token)


# =====================================================================
# 四、数据隔离（测试/验收不写正式库）
# =====================================================================

class TestDataIsolation(HardeningTestBase):
    """
    补验5：自动测试与真实模型验收必须使用独立数据目录。

    `/health` 暴露 data_scope，验收脚本在写入任何数据前据此拒绝在正式目录上运行。
    """

    def test_health_reports_isolated_data_scope(self):
        """隔离环境（EDU_DATA_DIR 指向临时目录）→ isolated=true"""
        r = self.req("GET", "/health")
        self.assertEqual(r.status_code, 200, r.text)
        scope = r.json()["data"]["data_scope"]
        self.assertTrue(scope["isolated"], scope)
        self.assertEqual(scope["dir_name"], os.path.basename(str(self.data_dir)),
                         "报告的数据目录与隔离目录不一致")

    def test_health_reports_production_scope_when_not_isolated(self):
        """未设置 EDU_DATA_DIR（正式目录）→ isolated=false，脚本据此拦截"""
        with mock.patch.dict(os.environ, {"EDU_DATA_DIR": ""}):
            with mock.patch("config.path_config.get_data_dir",
                            return_value=Path(self.data_dir).parent / "data"):
                scope = self.req("GET", "/health").json()["data"]["data_scope"]
        self.assertFalse(scope["isolated"], scope)


# =====================================================================
# 五、事件重试跨库幂等
# =====================================================================

class TestEventRetryIdempotency(HardeningTestBase):
    """
    模拟"部分步骤成功后失败，再次重试"：
    作答事件的第一步（掌握度，user_agent.db）与第二步（复习任务，learning_loop.db）
    分属不同数据库，重试时必须各自幂等。
    """

    def _emit_answer_event(self, attempt_id, user_id, kp_id="kp_retry"):
        from services.learning_events import emit_event
        return emit_event(
            event_type="quiz_attempt_recorded",
            idempotency_key=f"quiz_attempt:{attempt_id}",
            user_id=user_id,
            subject_id=SUBJECT,
            payload={"attempt_id": attempt_id, "question_id": "q_retry",
                     "knowledge_point": KP, "knowledge_point_id": kp_id,
                     "is_correct": False, "attempt_kind": "independent",
                     "source_task_id": None},
        )

    def _evidence_count(self, user_id):
        from services import mastery_service
        conn = mastery_service._get_db()
        try:
            row = conn.execute(
                "SELECT evidence_count, independent_wrong_count FROM knowledge_mastery "
                "WHERE user_id=? AND subject_id=?", (user_id, SUBJECT)).fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()

    def _review_rows(self, user_id):
        from services import review_service
        return review_service.list_review_tasks(user_id, SUBJECT, "PENDING")

    def test_step2_failure_then_retry_does_not_double_count(self):
        token, uid = self.login("retry_a")
        from services import review_service
        from services.learning_events import emit_event, retry_failed_events

        # 第一次：掌握度写入成功，复习任务创建失败 → 事件 FAILED
        with mock.patch.object(review_service, "ensure_review_task",
                               side_effect=review_service.ReviewServiceError("模拟复习联动失败")):
            res = self._emit_answer_event("att_retry_1", uid)
        self.assertEqual(res["status"], "FAILED", res)

        ev1 = self._evidence_count(uid)
        self.assertEqual(ev1.get("evidence_count"), 1, "第一步应已提交")
        self.assertEqual(self._review_rows(uid), [], "第二步应未创建")

        # 重试：第一步必须跳过（不重复累加），第二步创建成功
        out = retry_failed_events(limit=50, user_id=uid)
        self.assertGreaterEqual(out["retried"], 1)
        ev2 = self._evidence_count(uid)
        self.assertEqual(ev2.get("evidence_count"), 1,
                         f"重试后掌握证据被重复累加: {ev1} -> {ev2}")
        self.assertEqual(ev2.get("independent_wrong_count"), 1)

        reviews = self._review_rows(uid)
        self.assertEqual(len(reviews), 1, "重试后应恰好有一条待办复习")
        self.assertEqual(reviews[0]["merged_count"], 1, "重试导致 merged_count 被重复递增")

    def test_repeat_emit_same_attempt_is_idempotent(self):
        """同一作答重复投递（断网重发 / 重试）不重复写入"""
        token, uid = self.login("retry_b")
        r1 = self._emit_answer_event("att_retry_2", uid)
        r2 = self._emit_answer_event("att_retry_2", uid)

        self.assertEqual(r1["status"], "DONE")
        self.assertTrue(r2["duplicated"], "第二次应被判定为重复事件")
        ev = self._evidence_count(uid)
        self.assertEqual(ev.get("evidence_count"), 1)
        reviews = self._review_rows(uid)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["merged_count"], 1)

    def test_repeated_wrong_answers_merge_once_per_attempt(self):
        """同一知识点多次答错：每次合并一次，重复投递不再累加"""
        token, uid = self.login("retry_c")
        from services import mastery_service
        self._emit_answer_event("att_retry_3", uid)
        reviews = self._review_rows(uid)
        self.assertEqual(reviews[0]["merged_count"], 1)

        self._emit_answer_event("att_retry_4", uid)      # 新的作答 → 合并一次
        reviews = self._review_rows(uid)
        self.assertEqual(len(reviews), 1, "同一知识点不应产生第二条待办")
        self.assertEqual(reviews[0]["merged_count"], 2)

        self._emit_answer_event("att_retry_4", uid)      # 重复投递同一作答
        reviews = self._review_rows(uid)
        self.assertEqual(reviews[0]["merged_count"], 2, "重复投递又累加了合并计数")

        ev = self._evidence_count(uid)
        self.assertEqual(ev.get("evidence_count"), 2, "两次真实作答应为两条证据")

    def test_mastery_step_failure_then_retry_creates_single_review(self):
        """反向：复习任务先成功、掌握度失败 → 重试不重复建复习任务"""
        token, uid = self.login("retry_d")
        from services import mastery_service

        original = mastery_service.record_evidence
        state = {"fail": True}

        def _flaky(*args, **kwargs):
            if state["fail"]:
                state["fail"] = False
                raise mastery_service.MasteryServiceError("模拟画像写入失败")
            return original(*args, **kwargs)

        # 两个步骤是独立处理器：画像步骤失败不影响复习步骤；
        # 事件整体标记 FAILED，重试时只补跑失败的那一步。

        with mock.patch("services.mastery_service.record_evidence", side_effect=_flaky):
            res = self._emit_answer_event("att_retry_5", uid)
        self.assertEqual(res["status"], "FAILED", res)

        reviews = self._review_rows(uid)
        self.assertEqual(len(reviews), 1, "复习任务已创建")
        merged_before = reviews[0]["merged_count"]

        from services.learning_events import retry_failed_events
        retry_failed_events(limit=50, user_id=uid)

        reviews2 = self._review_rows(uid)
        self.assertEqual(len(reviews2), 1, "重试创建了重复复习任务")
        self.assertEqual(reviews2[0]["merged_count"], merged_before,
                         "重试重复递增了 merged_count")

        # 掌握度这一步最终应写入且只写一次
        ev = self._evidence_count(uid)
        self.assertEqual(ev.get("evidence_count"), 1, f"重试后掌握证据异常: {ev}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
