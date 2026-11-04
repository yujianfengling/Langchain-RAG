"""
教师端 HTTP 接口测试（2026-09-26）

重点验证"**不能只保护新接口**"这条要求：通过真实 HTTP 请求确认
旧的知识库/学科/文档/图谱/知识提取接口对课程资源同样受权限约束，
学生或另一位教师无法绕过。

隔离数据目录；外部模型用 Mock（与真实模型验证分开报告）。
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures_isolated import IsolatedDataTestCase, register_and_login

SUBJECT = "数据结构"


class TeacherApiBase(IsolatedDataTestCase):
    SUBJECTS = (SUBJECT,)

    # 教师身份由环境白名单开通（部署时的合法通道之一）
    TEACHER_USERS = "api_teacher,api_teacher2"
    ADMIN_USERS = "api_admin"

    def setUp(self):
        super().setUp()
        from api.app import app
        from fastapi.testclient import TestClient
        self._env = mock.patch.dict(os.environ, {
            "TEACHER_USERS": self.TEACHER_USERS,
            "ADMIN_USERS": self.ADMIN_USERS,
        })
        self._env.start()
        self.addCleanup(self._env.stop)
        self.client = TestClient(app)
        self.t_teacher = register_and_login("api_teacher")
        self.t_other = register_and_login("api_teacher2")
        self.t_student = register_and_login("api_student")
        self.t_outsider = register_and_login("api_outsider")
        self.t_admin = register_and_login("api_admin")

    # ---------- 辅助 ----------

    def h(self, token):
        return {"X-User-Token": token}

    def create_course(self, token, title="离散数学导论"):
        resp = self.client.post("/api/teacher/courses", headers=self.h(token),
                                json={"title": title, "description": "接口测试课程"})
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["data"]

    def create_class(self, token, course_id, name="周一一班"):
        resp = self.client.post(f"/api/teacher/courses/{course_id}/classes",
                                headers=self.h(token), json={"name": name})
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["data"]

    def join(self, token, code):
        resp = self.client.post("/api/student/courses/join", headers=self.h(token),
                                json={"code": code})
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["data"]

    def upload(self, token, course_id, filename="课程讲义.txt", content=b"hello world"):
        resp = self.client.post(
            f"/api/teacher/courses/{course_id}/materials?visibility=students",
            headers=self.h(token),
            files={"file": (filename, content, "text/plain")})
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["data"]


class TestTeacherApiBasics(TeacherApiBase):

    def test_login_required(self):
        self.assertEqual(self.client.get("/api/teacher/dashboard").status_code, 401)
        self.assertEqual(self.client.get("/api/teacher/courses").status_code, 401)
        self.assertEqual(self.client.post("/api/teacher/courses",
                                          json={"title": "x"}).status_code, 401)

    def test_create_course_and_dashboard(self):
        course = self.create_course(self.t_teacher)
        self.assertTrue(course["subject_id"].startswith("course_"))

        dash = self.client.get("/api/teacher/dashboard", headers=self.h(self.t_teacher)).json()["data"]
        self.assertEqual(len(dash["courses"]), 1)
        self.assertEqual(dash["courses"][0]["id"], course["id"])

        # 另一位教师的课程列表里没有这门课
        other = self.client.get("/api/teacher/dashboard",
                                headers=self.h(self.t_other)).json()["data"]
        self.assertEqual(other["courses"], [])

    def test_role_is_reported_by_auth_me(self):
        before = self.client.get("/api/auth/me", headers=self.h(self.t_student)).json()
        self.assertEqual((before.get("data") or {}).get("role"), "student")
        self.create_course(self.t_teacher)
        after = self.client.get("/api/auth/me", headers=self.h(self.t_teacher)).json()
        self.assertEqual((after.get("data") or {}).get("role"), "teacher")

    def test_course_detail_forbidden_for_other_teacher_and_outsider(self):
        course = self.create_course(self.t_teacher)
        for token in (self.t_other, self.t_student, self.t_outsider):
            resp = self.client.get(f"/api/teacher/courses/{course['id']}",
                                   headers=self.h(token))
            self.assertEqual(resp.status_code, 403, resp.text)
            self.assertIn("授课教师", resp.json()["message"])

    def test_student_join_and_see_course(self):
        course = self.create_course(self.t_teacher)
        klass = self.create_class(self.t_teacher, course["id"])
        self.join(self.t_student, klass["join_code"])

        mine = self.client.get("/api/student/courses",
                               headers=self.h(self.t_student)).json()["data"]["courses"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["title"], course["title"])
        self.assertEqual(mine[0]["class_name"], "周一一班")
        self.assertIn("teacher_name", mine[0])

    def test_bad_join_code_404(self):
        resp = self.client.post("/api/student/courses/join", headers=self.h(self.t_student),
                                json={"code": "AAAAAA"})
        self.assertEqual(resp.status_code, 404)

    def test_materials_upload_and_list(self):
        course = self.create_course(self.t_teacher)
        data = self.upload(self.t_teacher, course["id"])
        self.assertEqual(data["filename"], "课程讲义.txt")
        self.assertIn(data["kb"]["available"], (True, False))

        listing = self.client.get(f"/api/teacher/courses/{course['id']}/materials",
                                  headers=self.h(self.t_teacher)).json()["data"]
        self.assertEqual(len(listing["materials"]), 1)
        self.assertEqual(listing["materials"][0]["visibility"], "students")

    def test_upload_rejects_path_like_filename(self):
        """不接受客户端传服务器路径（只接受文件名）"""
        course = self.create_course(self.t_teacher)
        resp = self.client.post(
            f"/api/teacher/courses/{course['id']}/materials",
            headers=self.h(self.t_teacher),
            files={"file": ("../../etc/passwd", b"x", "text/plain")})
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_student_cannot_upload_material(self):
        course = self.create_course(self.t_teacher)
        klass = self.create_class(self.t_teacher, course["id"])
        self.join(self.t_student, klass["join_code"])
        resp = self.client.post(
            f"/api/teacher/courses/{course['id']}/materials",
            headers=self.h(self.t_student),
            files={"file": ("x.txt", b"x", "text/plain")})
        self.assertEqual(resp.status_code, 403)


class TestLegacyEndpointsCannotBypass(TeacherApiBase):
    """课程资源不能在旧接口上被绕过（本轮要求的关键验收点）"""

    def setUp(self):
        super().setUp()
        self.course = self.create_course(self.t_teacher)
        self.klass = self.create_class(self.t_teacher, self.course["id"])
        self.join(self.t_student, self.klass["join_code"])
        self.material = self.upload(self.t_teacher, self.course["id"])
        self.sid = self.course["subject_id"]

    def test_outsider_cannot_list_documents(self):
        for token, expect in ((self.t_outsider, 403), (self.t_other, 403),
                              (self.t_student, 200), (self.t_teacher, 200)):
            resp = self.client.get(f"/api/knowledge/{self.sid}/documents",
                                   headers=self.h(token))
            self.assertEqual(resp.status_code, expect,
                             f"token={token[:12]} {resp.text[:120]}")

    def test_teacher_only_material_hidden_from_students(self):
        """
        教师"仅自己可见"的资料：在班学生既看不到列表项，也下载不到
        （学科可读 != 每个文件可读）
        """
        resp = self.client.post(
            f"/api/teacher/courses/{self.course['id']}/materials?visibility=teacher_only",
            headers=self.h(self.t_teacher),
            files={"file": ("教师草稿.txt", b"teacher draft", "text/plain")})
        self.assertEqual(resp.status_code, 200, resp.text)

        # 学生：列表里没有教师草稿
        docs = self.client.get(f"/api/knowledge/{self.sid}/documents",
                               headers=self.h(self.t_student)).json()
        names = [d["name"] for d in (docs.get("documents") or [])]
        self.assertIn(self.material["filename"], names)
        self.assertNotIn("教师草稿.txt", names)

        # 学生：下载教师草稿被拒
        url = f"/api/knowledge/{self.sid}/documents/{'教师草稿.txt'}/file"
        self.assertEqual(self.client.get(url, headers=self.h(self.t_student)).status_code, 403)

        # 教师：列表与下载都不受影响
        t_docs = self.client.get(f"/api/knowledge/{self.sid}/documents",
                                 headers=self.h(self.t_teacher)).json()
        t_names = [d["name"] for d in (t_docs.get("documents") or [])]
        self.assertIn("教师草稿.txt", t_names)
        self.assertEqual(self.client.get(url, headers=self.h(self.t_teacher)).status_code, 200)

    def test_in_class_student_can_list_course_documents(self):
        """在班学生可以看本课程的学生可见资料（这是设计预期，不是漏洞）"""
        resp = self.client.get(f"/api/knowledge/{self.sid}/documents",
                               headers=self.h(self.t_student))
        self.assertEqual(resp.status_code, 200, resp.text)
        names = [d["name"] for d in (resp.json().get("documents") or [])]
        self.assertIn(self.material["filename"], names)

    def test_outsider_cannot_download_document_file(self):
        url = f"/api/knowledge/{self.sid}/documents/{self.material['filename']}/file"
        self.assertEqual(self.client.get(url, headers=self.h(self.t_outsider)).status_code, 403)
        self.assertEqual(self.client.get(url, headers=self.h(self.t_other)).status_code, 403)
        # 本课程学生可以下载课程资料
        self.assertIn(self.client.get(url, headers=self.h(self.t_student)).status_code, (200, 404))

    def test_student_cannot_delete_or_rebuild_or_upload(self):
        # 删除文档
        resp = self.client.delete(
            f"/api/knowledge/{self.sid}/documents/{self.material['filename']}",
            headers=self.h(self.t_student))
        self.assertEqual(resp.status_code, 403, resp.text)
        # 重建索引
        resp = self.client.post(f"/api/knowledge/{self.sid}/rebuild",
                                headers=self.h(self.t_student))
        self.assertEqual(resp.status_code, 403, resp.text)
        # 上传（表单 subject_id）
        resp = self.client.post("/api/knowledge/upload", headers=self.h(self.t_student),
                                data={"subject_id": self.sid},
                                files={"file": ("偷偷上传.txt", b"x", "text/plain")})
        self.assertEqual(resp.status_code, 403, resp.text)
        # 旧的上传资料仍未被写入
        docs = self.client.get(f"/api/knowledge/{self.sid}/documents",
                               headers=self.h(self.t_teacher)).json()
        names = [d["name"] for d in (docs.get("documents") or [])]
        self.assertNotIn("偷偷上传.txt", names)

    def test_other_teacher_cannot_manage_course_knowledge(self):
        resp = self.client.post(f"/api/knowledge/{self.sid}/rebuild",
                                headers=self.h(self.t_other))
        self.assertEqual(resp.status_code, 403)
        resp = self.client.delete(f"/api/knowledge/{self.sid}",
                                  headers=self.h(self.t_other))
        self.assertEqual(resp.status_code, 403)

    def test_graph_endpoints_guarded(self):
        resp = self.client.get(f"/api/knowledge/{self.sid}/graph",
                               headers=self.h(self.t_outsider))
        self.assertEqual(resp.status_code, 403, resp.text)
        resp = self.client.post(f"/api/knowledge/{self.sid}/graph/build",
                                headers=self.h(self.t_student))
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_subject_endpoints_guarded(self):
        self.assertEqual(self.client.get(f"/api/subjects/{self.sid}",
                                         headers=self.h(self.t_outsider)).status_code, 403)
        self.assertEqual(self.client.get(f"/api/subjects/{self.sid}",
                                         headers=self.h(self.t_student)).status_code, 200)
        # 不能通过"删除学科"绕过（旧接口）
        self.assertEqual(self.client.delete(f"/api/subjects/{self.sid}",
                                            headers=self.h(self.t_student)).status_code, 403)

    def test_knowledge_list_hides_other_courses(self):
        """
        知识库列表只显示可读学科：外来者看不到课程学科

        这里直接让"列表数据源"同时返回历史学科与课程学科，验证的是**接口的过滤逻辑**
        （而不是这台机器上恰好建过哪些库）。
        """
        fake_list = [
            {"subject_id": SUBJECT, "name": SUBJECT, "status": "ready"},
            {"subject_id": self.sid, "name": "课程知识库", "status": "ready"},
        ]
        with mock.patch("api.routes.knowledge.list_knowledge_bases", return_value=fake_list):
            mine = self.client.get("/api/knowledge", headers=self.h(self.t_teacher)).json()
            ids = [s["subject_id"] for s in mine["subjects"]]
            self.assertIn(SUBJECT, ids)
            self.assertIn(self.sid, ids)

            student = self.client.get("/api/knowledge", headers=self.h(self.t_student)).json()
            ids_student = [s["subject_id"] for s in student["subjects"]]
            self.assertIn(self.sid, ids_student, "本课程学生应能看到课程学科")

            outsider = self.client.get("/api/knowledge", headers=self.h(self.t_outsider)).json()
            ids_out = [s["subject_id"] for s in outsider["subjects"]]
            self.assertNotIn(self.sid, ids_out, "外来者不应看到课程学科")
            self.assertIn(SUBJECT, ids_out, "历史全局学科对所有登录用户仍可见")

    def test_legacy_global_subject_still_works_for_everyone(self):
        """历史全局学科保持既有行为（不被静默归属，也不因本轮收紧而失效）"""
        resp = self.client.get(f"/api/subjects/{SUBJECT}", headers=self.h(self.t_outsider))
        self.assertEqual(resp.status_code, 200, resp.text)


class TestAssignmentApi(TeacherApiBase):

    def setUp(self):
        super().setUp()
        self.course = self.create_course(self.t_teacher)
        self.klass = self.create_class(self.t_teacher, self.course["id"])
        self.join(self.t_student, self.klass["join_code"])
        self._patch_llm()

    def _patch_llm(self):
        """出题用 Mock：返回固定两道单选（与真实模型验证分开）"""
        self.questions = [{
            "type": "single_choice",
            "question": f"前序遍历的顺序是？(第{i + 1}题)",
            "options": ["根左右", "左根右", "左右根"],
            "answer": "根左右",
            "explanation": "前序：根左右",
        } for i in range(2)]
        self._llm = mock.patch("services.ai_service.call_llm",
                               return_value='{"questions": %s}'
                               % __import__("json").dumps(self.questions, ensure_ascii=False))
        self._llm.start()
        self.addCleanup(self._llm.stop)
        self._rag = mock.patch("services.ai_service.retrieve_and_build_context",
                               return_value={"context": "前序遍历顺序为根左右。",
                                             "sources": [{"file_name": "课程讲义.txt",
                                                          "page": 1, "type": "text"}]})
        self._rag.start()
        self.addCleanup(self._rag.stop)

    def test_full_assignment_flow_over_http(self):
        import json
        # 1) 建草稿作业
        resp = self.client.post(
            f"/api/teacher/courses/{self.course['id']}/assignments",
            headers=self.h(self.t_teacher),
            json={"class_id": self.klass["id"], "title": "前序遍历练习",
                  "description": "课后完成", "due_date": "2026-12-31",
                  "knowledge_points": ["前序遍历"]})
        self.assertEqual(resp.status_code, 200, resp.text)
        assignment = resp.json()["data"]
        self.assertEqual(assignment["status"], "draft")

        # 2) AI 出题（Mock）
        resp = self.client.post(
            f"/api/teacher/courses/{self.course['id']}/assignments/generate-questions",
            headers=self.h(self.t_teacher),
            json={"assignment_id": assignment["id"], "knowledge_points": ["前序遍历"],
                  "count": 2})
        self.assertEqual(resp.status_code, 200, resp.text)
        gen = resp.json()["data"]
        self.assertEqual(gen["generated"], 2)
        # 下发的题目不含标准答案
        for q in gen["questions"]:
            self.assertNotIn("correct_option_id", q)
            self.assertNotIn("reference_answer", q)

        # 3) 学生此时看不到（草稿）
        student_list = self.client.get("/api/student/assignments",
                                       headers=self.h(self.t_student)).json()["data"]
        self.assertEqual(student_list["assignments"], [])
        self.assertEqual(self.client.get(f"/api/student/assignments/{assignment['id']}",
                                         headers=self.h(self.t_student)).status_code, 404)

        # 4) 发布
        resp = self.client.post(f"/api/teacher/assignments/{assignment['id']}/publish",
                                headers=self.h(self.t_teacher))
        self.assertEqual(resp.status_code, 200, resp.text)
        published = resp.json()["data"]
        self.assertEqual(len(published["created"]), 1)
        self.assertEqual(published["failed"], [])

        # 5) 学生收到作业与任务
        detail = self.client.get(f"/api/student/assignments/{assignment['id']}",
                                 headers=self.h(self.t_student)).json()["data"]
        self.assertEqual(len(detail["questions"]), 2)
        self.assertEqual(len(detail["my_tasks"]), 1)
        tasks = self.client.get("/api/student/tasks", headers=self.h(self.t_student)).json()["data"]
        todo = tasks["todo"] if isinstance(tasks, dict) else tasks
        titles = [t["title"] for t in (todo or [])]
        self.assertTrue(any("前序遍历练习" in t for t in titles), titles)

        # 6) 学生作答（走既有判分接口，后端权威判分）
        first = detail["questions"][0]
        option_id = first["options"][0]["id"]
        resp = self.client.post("/api/quiz/answer", headers=self.h(self.t_student),
                                json={"question_id": first["id"], "user_answer": option_id,
                                      "client_token": "api-flow-1",
                                      "source_task_id": detail["my_tasks"][0]["id"]})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("is_correct", resp.json().get("data", resp.json()))

        # 6b) B4 回归（2026-10-03）：学生作业**列表**接口必须带出题量与已作答题数。
        #     此前 `td.list_assignments_for_classes` 的返回不含 question_ids，
        #     导致 `_student_attempt_map(user_id, [])` 恒为空 →
        #     学生端"已作答 X / N"永远显示 0（详情接口正常，所以只有列表口径是错的）。
        listed = self.client.get("/api/student/assignments",
                                 headers=self.h(self.t_student)).json()["data"]["assignments"]
        row = next(a for a in listed if a["id"] == assignment["id"])
        self.assertEqual(row["question_count"], 2, "列表必须带出题量（B4）")
        self.assertEqual(row["submitted"], 1, "列表必须带出已作答题数（B4）")
        self.assertEqual(len(row["results"]), 1, "列表必须带出作答结果（B4）")

        # 7) 标记完成（还有一题没答 → 先被拒绝）
        resp = self.client.post(f"/api/student/assignments/{assignment['id']}/complete",
                                headers=self.h(self.t_student))
        self.assertEqual(resp.status_code, 400, resp.text)
        second = detail["questions"][1]
        self.client.post("/api/quiz/answer", headers=self.h(self.t_student),
                         json={"question_id": second["id"],
                               "user_answer": second["options"][0]["id"],
                               "client_token": "api-flow-2"})
        resp = self.client.post(f"/api/student/assignments/{assignment['id']}/complete",
                                headers=self.h(self.t_student))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertGreaterEqual(resp.json()["data"]["completed_tasks"], 1)

        # 8) 学情：样本量与原始记录一致
        stats = self.client.get(f"/api/teacher/classes/{self.klass['id']}/analytics",
                                headers=self.h(self.t_teacher)).json()["data"]
        self.assertEqual(stats["participation"]["attempt_count"], 2)
        self.assertEqual(stats["participation"]["answered_student_count"], 1)
        self.assertIn("quiz_attempts", " ".join(stats["sources"]))
        self.assertIn("range", stats)

        # 9) 作业统计
        listing = self.client.get(
            f"/api/teacher/courses/{self.course['id']}/assignments",
            headers=self.h(self.t_teacher)).json()["data"]["assignments"]
        item = [a for a in listing if a["id"] == assignment["id"]][0]
        self.assertEqual(item["publish_stats"]["answered_student_count"], 1)

    def test_outsider_cannot_read_assignment_or_analytics(self):
        import json
        resp = self.client.post(
            f"/api/teacher/courses/{self.course['id']}/assignments",
            headers=self.h(self.t_teacher),
            json={"class_id": self.klass["id"], "title": "越权测试", "due_date": "2026-12-31"})
        assignment = resp.json()["data"]
        self.client.post(
            f"/api/teacher/courses/{self.course['id']}/assignments/generate-questions",
            headers=self.h(self.t_teacher),
            json={"assignment_id": assignment["id"], "count": 1})
        self.client.post(f"/api/teacher/assignments/{assignment['id']}/publish",
                         headers=self.h(self.t_teacher))

        # 未入班学生：打不开作业
        self.assertEqual(self.client.get(f"/api/student/assignments/{assignment['id']}",
                                         headers=self.h(self.t_outsider)).status_code, 403)
        # 另一位教师：看不到学情
        self.assertEqual(self.client.get(
            f"/api/teacher/classes/{self.klass['id']}/analytics",
            headers=self.h(self.t_other)).status_code, 403)
        # 学生也不能看班级学情（那是教师端能力）
        self.assertEqual(self.client.get(
            f"/api/teacher/classes/{self.klass['id']}/analytics",
            headers=self.h(self.t_student)).status_code, 403)

    def test_analytics_explain_insufficient_sample_skips_model(self):
        """样本不足时不调用模型，如实返回"数据不足" """
        called = {"n": 0}

        def _llm(*a, **kw):
            called["n"] += 1
            return "{}"

        with mock.patch("services.ai_service.call_llm", side_effect=_llm):
            resp = self.client.post(
                f"/api/teacher/classes/{self.klass['id']}/analytics/explain",
                headers=self.h(self.t_teacher))
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertFalse(data["enough_data"])
        self.assertIsNone(data["explanation"])
        self.assertEqual(called["n"], 0, "样本不足时不应调用模型")

    def test_lesson_plan_flow_over_http(self):
        import json
        from langchain_core.documents import Document
        content = {
            "title": "前序遍历", "objectives": ["掌握前序遍历"],
            "key_points": ["根左右"], "difficulties": ["递归实现"],
            "steps": [{"name": "导入", "minutes": 10, "content": "举例"}],
            "examples": [{"question": "顺序？", "answer": "根左右"}],
            "homework": [{"question": "写序列", "answer": "根左右"}],
        }
        # This flow now needs an authorized ready material and actual Document
        # candidates; context-only mocks cannot prove scoped retrieval.
        with mock.patch("services.lesson_plan_service.td.list_materials", return_value=[
                 {"id": "api-test-material", "filename": "课程讲义.txt", "status": "ready"}]), \
             mock.patch("services.ai_service.retrieve_and_build_context", return_value={
                 "context": "前序遍历顺序为根左右。", "sources": [],
                 "documents": [Document(page_content="前序遍历顺序为根左右。",
                                        metadata={"source": "课程讲义.txt", "material_id": "api-test-material"})]}), \
             mock.patch("services.ai_service.call_llm",
                        return_value=json.dumps(content, ensure_ascii=False)):
            resp = self.client.post(
                f"/api/teacher/courses/{self.course['id']}/lesson-plans/generate",
                headers=self.h(self.t_teacher),
                json={"topic": "二叉树前序遍历", "hours": 1,
                      "objectives": "会写前序", "class_id": self.klass["id"]})
        self.assertEqual(resp.status_code, 200, resp.text)
        plan = resp.json()["data"]

        # 学生看不到草稿
        student_plans = self.client.get(
            f"/api/student/courses/{self.course['id']}/lesson-plans",
            headers=self.h(self.t_student)).json()["data"]["lesson_plans"]
        self.assertEqual(student_plans, [])

        # 教师编辑 → 新版本
        edited = dict(plan["content"])
        edited["objectives"] = ["教师修改后的目标"]
        resp = self.client.put(f"/api/teacher/lesson-plans/{plan['id']}",
                               headers=self.h(self.t_teacher),
                               json={"content": edited, "note": "教师修改"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["data"]["version"], 2)

        # 预览与导出
        prev = self.client.get(f"/api/teacher/lesson-plans/{plan['id']}/preview",
                               headers=self.h(self.t_teacher)).json()["data"]
        self.assertIn("教学目标", prev["markdown"])
        exp = self.client.get(f"/api/teacher/lesson-plans/{plan['id']}/export",
                              headers=self.h(self.t_teacher))
        self.assertEqual(exp.status_code, 200)
        self.assertIn("attachment", exp.headers.get("content-disposition", ""))

        # 发布 → 学生可见教师修改后的版本
        resp = self.client.post(f"/api/teacher/lesson-plans/{plan['id']}/publish",
                                headers=self.h(self.t_teacher))
        self.assertEqual(resp.status_code, 200, resp.text)
        student_plans = self.client.get(
            f"/api/student/courses/{self.course['id']}/lesson-plans",
            headers=self.h(self.t_student)).json()["data"]["lesson_plans"]
        self.assertEqual(len(student_plans), 1)
        self.assertEqual(student_plans[0]["content"]["objectives"], ["教师修改后的目标"])

        # 另一位教师不能发布/编辑
        resp = self.client.put(f"/api/teacher/lesson-plans/{plan['id']}",
                               headers=self.h(self.t_other), json={"content": edited})
        self.assertEqual(resp.status_code, 403, resp.text)


class TestTeacherStudentProfile(TeacherApiBase):
    """
    学生画像接口（2026-10-01 合并组员实现时并入）

    组员原实现是**零鉴权 + 返回全体用户画像**的：在 runbook 的 `0.0.0.0` 绑定方式下，
    任何登录用户都能读到所有学生的画像。并入时按"教师身份 + 只看自己班学生"收口，
    这里的用例就是把这个收口固定下来，防止以后被改回去。
    """

    def setUp(self):
        super().setUp()
        self.course = self.create_course(self.t_teacher)
        self.klass = self.create_class(self.t_teacher, self.course["id"])
        self.join(self.t_student, self.klass["join_code"])

    def test_requires_login_and_teacher_role(self):
        """未登录 401；学生/校外用户 403（不能靠"登录了"就拿到学生画像）"""
        self.assertEqual(self.client.get("/api/teacher/students").status_code, 401)
        self.assertEqual(self.client.get("/api/teacher/class/overview").status_code, 401)
        for token in (self.t_student, self.t_outsider):
            self.assertEqual(
                self.client.get("/api/teacher/students",
                                headers=self.h(token)).status_code, 403)
            self.assertEqual(
                self.client.get("/api/teacher/class/overview",
                                headers=self.h(token)).status_code, 403)

    def test_scope_is_limited_to_my_own_classes(self):
        """另一位教师看不到我班上的学生，我也不该看到他班上的学生"""
        mine = self.client.get("/api/teacher/students",
                               headers=self.h(self.t_teacher)).json()["data"]
        rows = {s["username"]: s for s in mine["students"]}
        self.assertIn("api_student", rows, "在班学生必须出现在列表里")
        # user_id 是内部 id（user_xxxxxxxxxxxx），教师认人要用 display_name/username，
        # 前端不能把内部 id 直接摆在"学生"这一列（见 StudentsPanel 的显示名回退）
        self.assertTrue(rows["api_student"]["user_id"].startswith("user_"))
        self.assertEqual(rows["api_student"]["display_name"], "api_student")

        # 另一位教师没有班级 → 列表为空（而不是"全体用户"）
        other = self.client.get("/api/teacher/students",
                                headers=self.h(self.t_other)).json()["data"]
        self.assertEqual(other["students"], [])

        # 他的班级汇总只统计他自己的学生
        agg = self.client.get("/api/teacher/class/overview",
                              headers=self.h(self.t_other)).json()["data"]
        self.assertEqual(agg["total_students"], 0)

    def test_overview_forbidden_for_student_outside_my_classes(self):
        """越权看不在我班上的学生 → 403（不能靠改 URL 读到别人班的学生）"""
        rows = self.client.get("/api/teacher/students",
                               headers=self.h(self.t_teacher)).json()["data"]["students"]
        mine_id = next(r["user_id"] for r in rows if r["username"] == "api_student")

        # 不在我班上的学生（另一个账号）→ 403
        others = self.client.get("/api/teacher/students",
                                 headers=self.h(self.t_other)).json()["data"]["students"]
        self.assertEqual(others, [])
        # 用另一个教师自己的身份去看我班学生 → 也必须 403
        resp = self.client.get(f"/api/teacher/students/{mine_id}/overview",
                               headers=self.h(self.t_other))
        self.assertEqual(resp.status_code, 403, resp.text)
        # 用编造的 id 也是 403（不泄露"这个用户是否存在"）
        fake = self.client.get("/api/teacher/students/user_000000000000/overview",
                               headers=self.h(self.t_teacher))
        self.assertEqual(fake.status_code, 403, fake.text)
        # 在自己班上的学生可以看
        ok = self.client.get(f"/api/teacher/students/{mine_id}/overview",
                             headers=self.h(self.t_teacher))
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_student_without_profile_is_counted_not_dropped(self):
        """在班但还没有画像记录的学生也必须出现在列表里（统计为 0，而不是漏人）"""
        rows = self.client.get("/api/teacher/students",
                               headers=self.h(self.t_teacher)).json()["data"]["students"]
        row = next(r for r in rows if r["username"] == "api_student")
        self.assertEqual(row["interaction_count"], 0)
        self.assertEqual(row["average_mastery"], 0)


if __name__ == "__main__":
    unittest.main()
