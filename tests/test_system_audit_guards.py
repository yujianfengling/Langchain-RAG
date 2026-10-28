"""2026-10-25 全系统审计：学科访问、Agent 检索与跨班成员操作回归。"""

from unittest import mock

from tests.fixtures_isolated import register_and_login
from tests.test_trial_permission_guards import GuardTestCase, LEGACY_SUBJECT, SECRET


class ScopedGenerationGuardTests(GuardTestCase):
    """所有会读取课程资料或调用模型的生成入口都必须先校验学科权限。"""

    def test_learning_plan_rejects_non_member_before_generation(self):
        with mock.patch("api.routes.learning.generate_plan") as generate:
            response = self.client.post(
                "/api/learning/plans/generate",
                headers=self.h(self.t_student_out),
                json={"subject_id": self.subject_id, "goal": "复习本章"},
            )
        self.assertEqual(response.status_code, 403, response.text)
        generate.assert_not_called()

    def test_learning_plan_still_allows_member_and_legacy_subject(self):
        for token, subject_id in (
            (self.t_student_in, self.subject_id),
            (self.t_student_out, LEGACY_SUBJECT),
        ):
            with self.subTest(subject_id=subject_id):
                with mock.patch("api.routes.learning.generate_plan", return_value={"id": "plan_1"}):
                    response = self.client.post(
                        "/api/learning/plans/generate",
                        headers=self.h(token),
                        json={"subject_id": subject_id, "goal": "复习本章"},
                    )
                self.assertEqual(response.status_code, 200, response.text)

    def test_agent_sync_and_stream_reject_non_member_before_runtime(self):
        for path in ("/api/agent/run", "/api/agent/run/stream"):
            with self.subTest(path=path):
                with mock.patch("services.agent_runtime.run_agent") as run:
                    response = self.client.post(
                        path,
                        headers=self.h(self.t_student_out),
                        json={"subject_id": self.subject_id, "goal": "安排复习"},
                    )
                self.assertEqual(response.status_code, 403, response.text)
                run.assert_not_called()

    def test_agent_sync_and_stream_still_allow_members(self):
        result = {"run_id": "run_1", "status": "completed", "steps": []}
        for path in ("/api/agent/run", "/api/agent/run/stream"):
            with self.subTest(path=path):
                with mock.patch("services.agent_runtime.run_agent", return_value=result):
                    response = self.client.post(
                        path,
                        headers=self.h(self.t_student_in),
                        json={"subject_id": self.subject_id, "goal": "安排复习"},
                    )
                self.assertEqual(response.status_code, 200, response.text)

    def test_question_list_rejects_non_member_and_allows_member(self):
        with mock.patch("services.quiz_service.list_questions_for_point", return_value=[]) as query:
            denied = self.client.get(
                "/api/quiz/questions",
                headers=self.h(self.t_student_out),
                params={"subject_id": self.subject_id, "knowledge_point": "树"},
            )
            allowed = self.client.get(
                "/api/quiz/questions",
                headers=self.h(self.t_student_in),
                params={"subject_id": self.subject_id, "knowledge_point": "树"},
            )
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        query.assert_called_once()


class RetrievalIdentityTests(GuardTestCase):
    def setUp(self):
        super().setUp()
        from services import auth_service

        self.teacher_id = auth_service.get_user_id_by_token(self.t_teacher)
        self.member_id = auth_service.get_user_id_by_token(self.t_student_in)
        self.outsider_id = auth_service.get_user_id_by_token(self.t_student_out)

    def test_non_member_documents_fail_closed_even_if_route_guard_is_missed(self):
        from services import teaching_access

        documents = [self.visible_doc(), self.secret_doc(material_id=self.to_material["id"])]
        self.assertEqual(
            teaching_access.filter_visible_documents(self.outsider_id, self.subject_id, documents),
            [],
        )

    def test_agent_retrieval_passes_viewer_and_keeps_teacher_draft_for_teacher(self):
        from services import agent_runtime

        with mock.patch(
            "services.ai_service.retrieve_context",
            return_value=[self.secret_doc(material_id=self.to_material["id"])],
        ):
            result = agent_runtime._tool_search_material(
                self.teacher_id, self.subject_id, "课程重点",
            )
        self.assertIn(SECRET, result["snippet"])

    def test_agent_retrieval_does_not_expose_teacher_draft_to_student(self):
        from services import agent_runtime

        with mock.patch(
            "services.ai_service.retrieve_context",
            return_value=[self.secret_doc(material_id=self.to_material["id"])],
        ):
            result = agent_runtime._tool_search_material(
                self.member_id, self.subject_id, "课程重点",
            )
        self.assertNotIn(SECRET, result["snippet"])


class CrossClassMemberRemovalTests(GuardTestCase):
    TEACHER_USERS = "pg_teacher,pg_other_teacher"

    def test_teacher_cannot_remove_member_from_another_teachers_class(self):
        other_token = register_and_login("pg_other_teacher")
        victim_token = register_and_login("pg_victim")
        other_course = self.client.post(
            "/api/teacher/courses",
            headers=self.h(other_token),
            json={"title": "另一教师的课程"},
        ).json()["data"]
        other_class = self.client.post(
            f"/api/teacher/courses/{other_course['id']}/classes",
            headers=self.h(other_token),
            json={"name": "另一教师的班级"},
        ).json()["data"]
        joined = self.client.post(
            "/api/student/courses/join",
            headers=self.h(victim_token),
            json={"code": other_class["join_code"]},
        )
        self.assertEqual(joined.status_code, 200, joined.text)
        target = self.td.list_members(other_class["id"])[0]

        attacked = self.client.post(
            f"/api/teacher/classes/{self.class_id}/members/{target['id']}/remove",
            headers=self.h(self.t_teacher),
        )
        self.assertEqual(attacked.status_code, 400, attacked.text)
        unchanged = self.td.list_members(other_class["id"], status=None)[0]
        self.assertEqual(unchanged["status"], "active")
        self.assertIsNone(unchanged["removed_at"])


class UploadPathTests(GuardTestCase):
    def test_path_like_filenames_are_rejected_not_silently_normalized(self):
        for filename in ("../../outside.txt", "..\\..\\outside.txt", "folder/lesson.md"):
            with self.subTest(filename=filename):
                response = self.client.post(
                    f"/api/teacher/courses/{self.course_id}/materials",
                    headers=self.h(self.t_teacher),
                    files={"file": (filename, b"audit", "text/plain")},
                )
                self.assertEqual(response.status_code, 400, response.text)
