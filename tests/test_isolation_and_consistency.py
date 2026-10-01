"""
访问隔离对抗 + 教学闭环一致性测试（2026-09-27）

覆盖本轮第一步要求：
1) 教师专属资料、学生个人问答/错题/成绩/学习记录在**接口层面**的隔离
   （"换一个用户 ID""退出班级后继续访问""直接请求接口"）
2) 核心功能真正联动：教师发布资料与作业 → 学生学习/问答/提交 → 判分与错题
   → 掌握度与复习任务 → 教师端班级学情；
   并验证**重复提交 / 修改作业**时统计不重复计数、口径与原始记录一致。

用 1 教师 + 2 学生的隔离数据完整走通；不写入正式数据。
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures_isolated import IsolatedDataTestCase, register_and_login

SUBJECT = "数据结构"


class IsolationBase(IsolatedDataTestCase):
    SUBJECTS = (SUBJECT,)
    TEACHER_USERS = "iso_teacher"
    ADMIN_USERS = "iso_admin"

    def setUp(self):
        super().setUp()
        from api.app import app
        from fastapi.testclient import TestClient
        from services import auth_service, teaching_service as ts, teaching_data as td
        from services import quiz_service, learning_service, mastery_service
        self.auth, self.ts, self.td = auth_service, ts, td
        self.quiz, self.learning, self.mastery = quiz_service, learning_service, mastery_service
        self._env = mock.patch.dict(os.environ, {
            "TEACHER_USERS": self.TEACHER_USERS, "ADMIN_USERS": self.ADMIN_USERS,
        })
        self._env.start()
        self.addCleanup(self._env.stop)
        self.client = TestClient(app)

        self.t_teacher = register_and_login("iso_teacher")
        self.t_s1 = register_and_login("iso_stu1")
        self.t_s2 = register_and_login("iso_stu2")
        self.s1 = auth_service.get_user_id_by_token(self.t_s1)
        self.s2 = auth_service.get_user_id_by_token(self.t_s2)

    def h(self, token):
        return {"X-User-Token": token}

    def setup_course(self):
        """1 教师 + 2 学生的课程/班级/资料/作业（走真实接口）"""
        course = self.client.post("/api/teacher/courses", headers=self.h(self.t_teacher),
                                  json={"title": "隔离与联动测试课"}).json()["data"]
        klass = self.client.post(f"/api/teacher/courses/{course['id']}/classes",
                                 headers=self.h(self.t_teacher),
                                 json={"name": "一班"}).json()["data"]
        for token in (self.t_s1, self.t_s2):
            self.client.post("/api/student/courses/join", headers=self.h(token),
                             json={"code": klass["join_code"]})
        self.client.post(
            f"/api/teacher/courses/{course['id']}/materials?visibility=students",
            headers=self.h(self.t_teacher),
            files={"file": ("讲义.txt", "前序遍历顺序是根左右。".encode(), "text/plain")})
        self.client.post(
            f"/api/teacher/courses/{course['id']}/materials?visibility=teacher_only",
            headers=self.h(self.t_teacher),
            files={"file": ("教师草稿.txt", b"draft", "text/plain")})

        qs = self.quiz.persist_questions(
            subject_id=course["subject_id"], knowledge_point_name="二叉树遍历",
            questions=[{"type": "single_choice", "question": f"前序遍历第{i+1}题？",
                        "options": ["根左右", "左根右"], "answer": "根左右",
                        "explanation": "前序：根左右"} for i in range(3)],
            source="test")
        assignment = self.td.create_assignment(
            course["id"], klass["id"], course["subject_id"], self.s1, title="遍历练习",
            due_date="2026-12-31", question_ids=[q["id"] for q in qs])
        self.client.post(f"/api/teacher/assignments/{assignment['id']}/publish",
                         headers=self.h(self.t_teacher))
        return course, klass, assignment, [q["id"] for q in qs]

    def answer(self, token, question_id, option="A", client_token=None):
        return self.client.post("/api/quiz/answer", headers=self.h(token),
                                json={"question_id": question_id, "user_answer": option,
                                      "client_token": client_token})


class TestPersonalRecordIsolation(IsolationBase):
    """"换一个用户 ID / 直接请求接口"都不能拿到别人的个人记录"""

    def setUp(self):
        super().setUp()
        self.course, self.klass, self.assignment, self.qids = self.setup_course()
        # 学生 1 留下：作答、错题、会话、记忆、计划约束
        self.answer(self.t_s1, self.qids[0], "B", client_token="iso-1")   # 答错
        self.answer(self.t_s1, self.qids[1], "A", client_token="iso-2")   # 答对
        conv = self.client.post("/api/conversations", headers=self.h(self.t_s1),
                                json={"title": "我的私人会话",
                                      "subject_id": self.course["subject_id"]}).json()["data"]
        self.s1_conv = conv["id"]
        self.client.post(f"/api/conversations/{self.s1_conv}/messages",
                         headers=self.h(self.t_s1),
                         json={"role": "user", "content": "这是我的私人提问"})
        self.client.put("/api/plan/constraints", headers=self.h(self.t_s1),
                        json={"subject_id": self.course["subject_id"], "daily_minutes": 45})

    def test_attempts_and_wrong_are_per_user(self):
        own = self.client.get("/api/quiz/attempts", headers=self.h(self.t_s1)).json()["data"]
        other = self.client.get("/api/quiz/attempts", headers=self.h(self.t_s2)).json()["data"]
        self.assertEqual(len(own), 2)
        self.assertEqual(len(other), 0, "学生 2 不应看到学生 1 的作答")

    def test_path_user_id_cannot_switch_identity(self):
        """把路径里的 user_id 换成别人：要么 403，要么只返回自己的数据"""
        for url in (f"/api/agent/profile/{self.s1}",
                    f"/api/agent/progress-summary/{self.s1}/{self.course['subject_id']}",
                    f"/api/agent/learning-path/{self.s1}/{self.course['subject_id']}",
                    f"/api/agent/interactions/{self.s1}",
                    f"/api/agent/weak-points/{self.s1}/{self.course['subject_id']}"):
            resp = self.client.get(url, headers=self.h(self.t_s2))
            self.assertEqual(resp.status_code, 403, f"{url} → {resp.status_code}")

    def test_query_user_id_is_ignored(self):
        """查询串里的 user_id 不影响返回内容（后端只认令牌）"""
        sid = self.course["subject_id"]
        for url in ("/api/student/tasks", "/api/quiz/attempts", "/api/quiz/stats",
                    f"/api/student/mastery?subject_id={sid}"):
            sep = "&" if "?" in url else "?"
            resp = self.client.get(f"{url}{sep}user_id={self.s1}", headers=self.h(self.t_s2))
            self.assertEqual(resp.status_code, 200, resp.text)
            blob = str(resp.json())
            self.assertNotIn(self.s1, blob, f"{url} 返回了他人身份的数据")

    def test_cannot_read_other_conversation(self):
        for url in (f"/api/conversations/{self.s1_conv}",
                    f"/api/conversations/{self.s1_conv}/messages"):
            resp = self.client.get(url, headers=self.h(self.t_s2))
            self.assertIn(resp.status_code, (403, 404), f"{url} → {resp.status_code}")

    def test_cannot_delete_other_conversation(self):
        resp = self.client.delete(f"/api/conversations/{self.s1_conv}",
                                  headers=self.h(self.t_s2))
        self.assertIn(resp.status_code, (403, 404))
        still = self.client.get(f"/api/conversations/{self.s1_conv}",
                                headers=self.h(self.t_s1))
        self.assertEqual(still.status_code, 200, "本人会话不应被他人删除")

    def test_teacher_cannot_read_student_chat_content(self):
        """教师不能逐条读取学生私人对话（教师端只用汇总数据）"""
        for url in (f"/api/conversations/{self.s1_conv}",
                    f"/api/conversations/{self.s1_conv}/messages"):
            resp = self.client.get(url, headers=self.h(self.t_teacher))
            self.assertIn(resp.status_code, (403, 404), f"{url} → {resp.status_code}")

    def test_mastery_and_records_are_per_user(self):
        url = f"/api/student/mastery?subject_id={self.course['subject_id']}"
        m1 = self.client.get(url, headers=self.h(self.t_s1)).json()["data"]
        m2 = self.client.get(url, headers=self.h(self.t_s2)).json()["data"]
        self.assertTrue(m1["points"], "学生 1 应有掌握度证据")
        self.assertEqual(m2["points"], [], "学生 2 没有任何证据，不应显示他人数据")

    def test_plan_constraints_are_per_user(self):
        c1 = self.client.get(f"/api/plan/constraints?subject_id={self.course['subject_id']}",
                             headers=self.h(self.t_s1)).json()["data"]
        c2 = self.client.get(f"/api/plan/constraints?subject_id={self.course['subject_id']}",
                             headers=self.h(self.t_s2)).json()["data"]
        self.assertEqual(c1["daily_minutes"], 45)
        self.assertNotEqual(c2["daily_minutes"], 45, "学生 2 不应继承学生 1 的计划约束")

    def test_unauthenticated_personal_write_rejected(self):
        """不带令牌直接请求个人数据写接口 → 401（不能落到历史账号）"""
        resp = self.client.post("/api/quiz/answer",
                                json={"question_id": self.qids[0], "user_answer": "A"})
        self.assertEqual(resp.status_code, 401, resp.text)
        resp = self.client.post("/api/student/courses/join", json={"code": "ABC123"})
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_outsider_cannot_answer_course_questions_via_assignment(self):
        """未入班学生拿不到该课程作业（也就无从作答）"""
        outsider = register_and_login("iso_outsider")
        resp = self.client.get(f"/api/student/assignments/{self.assignment['id']}",
                               headers=self.h(outsider))
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_teacher_material_not_visible_to_students(self):
        docs = self.client.get(
            f"/api/knowledge/{self.course['subject_id']}/documents",
            headers=self.h(self.t_s1)).json()
        names = [d["name"] for d in docs["documents"]]
        self.assertIn("讲义.txt", names)
        self.assertNotIn("教师草稿.txt", names)


class TestLearningLoopConsistency(IsolationBase):
    """教师发布 → 学生作答 → 判分/错题/掌握/复习 → 教师学情，全链路一致"""

    def setUp(self):
        super().setUp()
        self.course, self.klass, self.assignment, self.qids = self.setup_course()

    def test_full_chain_is_linked(self):
        from services import review_service, class_analytics

        # 学生 1：1 对 2 错；学生 2：全对
        self.answer(self.t_s1, self.qids[0], "A", client_token="c-1")   # 对
        self.answer(self.t_s1, self.qids[1], "B", client_token="c-2")   # 错
        self.answer(self.t_s1, self.qids[2], "B", client_token="c-3")   # 错
        for i, qid in enumerate(self.qids):
            self.answer(self.t_s2, qid, "A", client_token=f"d-{i}")     # 全对

        # 1) 作业任务进入学生的今日任务
        tasks = self.client.get("/api/student/tasks", headers=self.h(self.t_s1)).json()["data"]
        todo = tasks.get("todo", tasks if isinstance(tasks, list) else [])
        self.assertTrue(any(t.get("assignment_id") == self.assignment["id"] for t in todo),
                        "教师发布的作业没有进入学生任务列表")

        # 2) 错题本：学生 1 有 2 道错题，学生 2 没有
        w1 = self.client.get("/api/quiz/wrong", headers=self.h(self.t_s1)).json()["data"]
        w2 = self.client.get("/api/quiz/wrong", headers=self.h(self.t_s2)).json()["data"]
        self.assertEqual(len(w1), 2)
        self.assertEqual(len(w2), 0)

        # 3) 复习任务：错题触发的待复习任务
        reviews = review_service.list_review_tasks(self.s1, self.course["subject_id"], "PENDING")
        self.assertTrue(reviews, "答错没有生成复习任务")

        # 4) 掌握度：有证据（2 对 2 错），不是"待评估"空壳
        m = self.client.get(f"/api/student/mastery?subject_id={self.course['subject_id']}",
                            headers=self.h(self.t_s1)).json()["data"]
        self.assertTrue(m["points"])
        self.assertTrue(any(p.get("evidence_count") or p.get("correct_count") for p in m["points"]))

        # 5) 教师端学情与原始记录一致
        stats = class_analytics.class_overview(self.s1 and self.teacher_uid(), self.klass["id"])
        self.assertEqual(stats["participation"]["student_count"], 2)
        self.assertEqual(stats["participation"]["answered_student_count"], 2)
        self.assertEqual(stats["participation"]["attempt_count"], 6)
        self.assertEqual(stats["participation"]["distinct_question_count"], 6)
        kp = stats["knowledge_points"][0]
        self.assertEqual(kp["attempt_count"], 6)
        self.assertEqual(kp["wrong_count"], 2)
        self.assertEqual(kp["basis"], "每题最新一次作答")

    def teacher_uid(self):
        from services import auth_service
        return auth_service.get_user_id_by_token(self.t_teacher)

    def test_repeat_submission_does_not_inflate_statistics(self):
        """
        重复提交 / 重做：原始计数增加，但"掌握判定"按每题最新一次，不重复计数
        """
        from services import class_analytics
        tid = self.teacher_uid()

        self.answer(self.t_s1, self.qids[0], "B", client_token="r-1")  # 第一次答错
        stats1 = class_analytics.class_overview(tid, self.klass["id"])
        kp1 = stats1["knowledge_points"][0]
        self.assertEqual(kp1["attempt_count"], 1)
        self.assertEqual(kp1["wrong_count"], 1)

        # 同一题重做（新 token，属于合法重做）并答对
        self.answer(self.t_s1, self.qids[0], "A", client_token="r-2")
        stats2 = class_analytics.class_overview(tid, self.klass["id"])
        kp2 = stats2["knowledge_points"][0]
        self.assertEqual(stats2["participation"]["attempt_count"], 2, "原始作答次数应增加")
        self.assertEqual(stats2["participation"]["distinct_question_count"], 1,
                         "去重后仍是同一道题")
        self.assertEqual(kp2["attempt_count"], 1, "知识点统计应按每题最新一次，不重复计数")
        self.assertEqual(kp2["wrong_count"], 0, "重做后答对，不应再算作错题")
        self.assertEqual(kp2["raw_attempt_count"], 2, "原始计数应保留以便核对")
        self.assertEqual(kp2["raw_wrong_count"], 1)

    def test_same_client_token_is_idempotent(self):
        """同一次提交重复发送（网络重试）：只记一次，不重复计分"""
        from services import class_analytics
        tid = self.teacher_uid()
        first = self.answer(self.t_s1, self.qids[0], "A", client_token="same-token")
        second = self.answer(self.t_s1, self.qids[0], "A", client_token="same-token")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        stats = class_analytics.class_overview(tid, self.klass["id"])
        self.assertEqual(stats["participation"]["attempt_count"], 1,
                         "同一 client_token 被记了两次（幂等失效）")

    def test_assignment_stats_match_raw_records(self):
        """作业完成情况统计与原始记录一致"""
        from services import assignment_service
        tid = self.teacher_uid()
        self.answer(self.t_s1, self.qids[0], "A", client_token="a-1")
        self.answer(self.t_s2, self.qids[0], "A", client_token="b-1")
        listing = self.client.get(
            f"/api/teacher/courses/{self.course['id']}/assignments",
            headers=self.h(self.t_teacher)).json()["data"]["assignments"]
        item = [a for a in listing if a["id"] == self.assignment["id"]][0]
        self.assertEqual(item["publish_stats"]["student_count"], 2)
        self.assertEqual(item["publish_stats"]["answered_student_count"], 2)
        # 与数据库原始记录核对
        conn = self.quiz._get_db()
        try:
            marks = ",".join("?" for _ in self.qids)
            n = conn.execute(
                f"SELECT COUNT(DISTINCT user_id) FROM quiz_attempts "
                f"WHERE question_id IN ({marks})", list(self.qids)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(item["publish_stats"]["answered_student_count"], n)

    def test_completed_assignment_requires_all_questions(self):
        from services.teaching_data import TeachingError
        from services import assignment_service
        with self.assertRaises(TeachingError):
            assignment_service.complete_assignment_task(self.s1, self.assignment["id"])
        for i, qid in enumerate(self.qids):
            self.answer(self.t_s1, qid, "A", client_token=f"fin-{i}")
        result = assignment_service.complete_assignment_task(self.s1, self.assignment["id"])
        self.assertEqual(result["completed_tasks"], 1)

    def test_material_upload_flows_into_knowledge_base(self):
        """教师上传资料 → 重建 → 资料真的进了该课程知识库"""
        resp = self.client.post(f"/api/teacher/courses/{self.course['id']}/knowledge/rebuild",
                                headers=self.h(self.t_teacher))
        data = resp.json()["data"]
        self.assertTrue(data["ok"], data)
        self.assertGreaterEqual(data["kb"]["document_count"], 1)
        self.assertEqual(data["result"]["subject_id"], self.course["subject_id"])

    def test_statistics_follow_new_answers(self):
        """学情随作答实时更新（页面显示与后端结果一致）"""
        from services import class_analytics
        tid = self.teacher_uid()
        before = class_analytics.class_overview(tid, self.klass["id"])
        self.assertEqual(before["participation"]["attempt_count"], 0)
        self.answer(self.t_s1, self.qids[0], "A", client_token="live-1")
        after = class_analytics.class_overview(tid, self.klass["id"])
        self.assertEqual(after["participation"]["attempt_count"], 1)
        self.assertEqual(after["participation"]["answered_student_count"], 1)

    def test_analytics_ignores_other_courses_data(self):
        """别的课程/学科的作答不会混入本班学情"""
        from services import class_analytics
        tid = self.teacher_uid()
        other = self.quiz.persist_questions(
            subject_id=SUBJECT, knowledge_point_name="别的学科知识点",
            questions=[{"type": "single_choice", "question": "外部题目",
                        "options": ["甲", "乙"], "answer": "甲"}], source="test")
        self.answer(self.t_s1, other[0]["id"], "A", client_token="out-1")
        stats = class_analytics.class_overview(tid, self.klass["id"])
        self.assertEqual(stats["participation"]["attempt_count"], 0,
                         "其他学科的作答混进了本课程学情")


if __name__ == "__main__":
    unittest.main()
