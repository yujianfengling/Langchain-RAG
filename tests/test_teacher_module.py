"""
教师端专项测试（2026-09-26）

覆盖本轮验收清单（隔离数据目录；外部模型用 Mock，与真实模型验证分开报告）：
- 教师创建课程与班级，学生用邀请码加入
- 教师上传资料、编写并发布教案（草稿不提前公开）
- 教师发布练习 → 学生端收到并可作答（复用既有判分与答案隔离）
- 学情统计随作答更新，样本量与原始记录一致；样本不足显示"数据不足"
- 两位教师互不能修改对方课程；未入班学生不能读资料
- 学生不能通过旧接口绕过课程权限（上传/删除/下载/图谱/学科/知识提取）
- 学生的个人计划不会改动教师固定截止时间
- 历史全局学科保持既有行为（不被静默归属给教师）
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures_isolated import IsolatedDataTestCase

SUBJECT = "数据结构"          # 历史全局学科（无归属）
STUDENT_A = "stu_a"
STUDENT_B = "stu_b"


def _auth_service():
    from services import auth_service
    return auth_service


def _new_user(username: str, role: str = "student") -> str:
    """在隔离库里建用户并返回 user_id"""
    auth = _auth_service()
    user = auth.register_user(username, "test-pass-123")
    if role == "teacher":
        auth.grant_role(user["id"], auth.ROLE_TEACHER)
    return user["id"]


class TeacherBase(IsolatedDataTestCase):
    SUBJECTS = (SUBJECT,)

    def setUp(self):
        super().setUp()
        from services import teaching_service, teaching_data as td
        self.ts = teaching_service
        self.td = td
        self.teacher_a = _new_user("teacher_a", role="teacher")
        self.teacher_b = _new_user("teacher_b", role="teacher")
        self.student_a = _new_user(STUDENT_A)
        self.student_b = _new_user(STUDENT_B)

    def make_course(self, owner=None, title="离散数学导论"):
        owner = owner or self.teacher_a
        return self.ts.create_course(owner, title, "测试课程")

    def make_class(self, course, owner=None):
        return self.ts.create_class(owner or self.teacher_a, course["id"], "周一一班")

    def join(self, klass, user_id):
        self.td.add_member(klass["id"], klass["course_id"], user_id, source="test")

    def upload(self, course, filename="讲义.txt", content=b"hello", owner=None,
               visibility="students"):
        return self.ts.upload_material(owner or self.teacher_a, course["id"],
                                       content, filename, visibility=visibility)


class TestRoleAndOwnership(TeacherBase):

    def test_new_user_is_student_and_creating_course_grants_teacher(self):
        """默认 student；创建课程才显式获得 teacher（不静默提升任何人）"""
        from services import auth_service
        fresh = _new_user("fresh_user")
        self.assertEqual(auth_service.get_role(fresh), auth_service.ROLE_STUDENT)
        self.assertNotEqual(auth_service.get_role(self.student_a), "teacher")
        course = self.make_course()
        self.assertEqual(auth_service.get_role(self.teacher_a), auth_service.ROLE_TEACHER)
        # 课程自带专属学科与归属登记
        self.assertTrue(course["subject_id"].startswith("course_"))
        owner = self.td.subject_owner(course["subject_id"])
        self.assertEqual(owner["owner_user_id"], self.teacher_a)
        self.assertEqual(owner["course_id"], course["id"])

    def test_legacy_subject_not_auto_assigned(self):
        """历史全局学科没有归属记录，不会被算作某位教师的课程"""
        from services import teaching_access
        scope = teaching_access.subject_scope(SUBJECT)
        self.assertEqual(scope["scope"], "legacy")
        self.assertIsNone(scope["owner_user_id"])
        # 教师也读得到，但只是"历史公共资料"，不是他的课程资源
        self.assertTrue(teaching_access.can_read_subject(self.teacher_a, SUBJECT))
        self.assertFalse(teaching_access.can_write_subject(self.teacher_a, SUBJECT))

    def test_other_teacher_cannot_touch_course(self):
        """两位教师互相不能修改对方课程"""
        from services.teaching_data import TeachingError
        course = self.make_course()

        for call in (
            lambda: self.ts.get_course_overview(self.teacher_b, course["id"]),
            lambda: self.ts.create_class(self.teacher_b, course["id"], "蹭课班"),
            lambda: self.ts.upload_material(self.teacher_b, course["id"], b"x", "x.txt"),
            lambda: self.ts.rebuild_course_knowledge(self.teacher_b, course["id"]),
        ):
            with self.assertRaises(TeachingError) as ctx:
                call()
            self.assertEqual(ctx.exception.error_type, "forbidden")

    def test_student_cannot_manage_course(self):
        from services.teaching_data import TeachingError
        course = self.make_course()
        with self.assertRaises(TeachingError) as ctx:
            self.ts.upload_material(self.student_a, course["id"], b"x", "x.txt")
        self.assertEqual(ctx.exception.error_type, "forbidden")

    def test_join_leave_and_membership_records(self):
        """加班/退班留痕；重复加入不产生重复成员"""
        course = self.make_course()
        klass = self.make_class(course)
        result = self.ts.join_class_by_code(self.student_a, klass["join_code"])
        self.assertFalse(result["already_member"])
        again = self.ts.join_class_by_code(self.student_a, klass["join_code"])
        self.assertTrue(again["already_member"])
        members = self.td.list_members(klass["id"], status="active")
        self.assertEqual(len(members), 1)

        self.ts.leave_class(self.student_a, klass["id"])
        self.assertEqual(len(self.td.list_members(klass["id"], status="active")), 0)
        events = self.td.list_member_events(klass["id"])
        types = [e["event_type"] for e in events]
        self.assertIn("join", types)
        self.assertIn("left", types)

    def test_teacher_removal_is_recorded(self):
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        member = self.td.list_members(klass["id"], status="active")[0]
        self.ts.remove_member(self.teacher_a, klass["id"], member["id"], reason="转班")
        after = self.td.list_members(klass["id"], status="removed")
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["removed_by"], self.teacher_a)

    def test_invalid_join_code(self):
        from services.teaching_data import TeachingError
        with self.assertRaises(TeachingError) as ctx:
            self.ts.join_class_by_code(self.student_a, "ZZZZZZ")
        self.assertEqual(ctx.exception.error_type, "not_found")

    def test_non_member_cannot_read_course_or_materials(self):
        from services.teaching_data import TeachingError
        course = self.make_course()
        self.upload(course)
        with self.assertRaises(TeachingError):
            self.ts.list_course_materials(self.student_b, course["id"])
        with self.assertRaises(TeachingError):
            self.ts.get_course_overview(self.student_b, course["id"])

    def test_student_only_sees_student_visible_materials(self):
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        self.upload(course, "公开讲义.txt")
        self.upload(course, "教师草稿.txt", visibility="teacher_only")

        teacher_view = self.ts.list_course_materials(self.teacher_a, course["id"])
        self.assertEqual(len(teacher_view["materials"]), 2)

        student_view = self.ts.list_course_materials(self.student_a, course["id"])
        names = {m["filename"] for m in student_view["materials"]}
        self.assertIn("公开讲义.txt", names)
        self.assertNotIn("教师草稿.txt", names)


class TestLessonPlan(TeacherBase):

    def _fake_plan_json(self):
        return """{
          "title": "二叉树的遍历",
          "objectives": ["掌握前中后序遍历"],
          "key_points": ["遍历顺序"],
          "difficulties": ["递归实现"],
          "steps": [{"name": "导入", "minutes": 10, "content": "举例", "materials": ["讲义.txt"]},
                    {"name": "讲解", "minutes": 30, "content": "演示递归", "materials": []}],
          "examples": [{"question": "前序序列？", "answer": "根左右"}],
          "homework": [{"question": "写中序序列", "answer": "左根右"}],
          "evidence_note": "依据讲义.txt"
        }"""

    def test_generate_edit_version_publish_flow(self):
        from services import lesson_plan_service
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        self.upload(course)

        with mock.patch("services.ai_service.retrieve_and_build_context",
                        return_value={"context": "二叉树内容", "sources": [
                            {"file_name": "讲义.txt", "page": 1, "type": "text"}]}), \
             mock.patch("services.ai_service.call_llm", return_value=self._fake_plan_json()):
            plan = lesson_plan_service.generate_draft(
                self.teacher_a, course["id"], topic="二叉树遍历", hours=1,
                objectives="会写三种遍历", class_id=klass["id"])

        self.assertTrue(plan["ai_generated"])
        self.assertEqual(plan["version"], 1)
        self.assertIn("objectives", plan["content"])
        self.assertEqual(plan["evidence"][0]["file_name"], "讲义.txt")

        # 教师编辑 → 新版本，历史版本保留
        edited = dict(plan["content"])
        edited["objectives"] = ["教师改过的目标"]
        v2 = lesson_plan_service.update_plan(self.teacher_a, plan["id"], content=edited,
                                             note="补充教学目标")
        self.assertEqual(v2["version"], 2)
        versions = lesson_plan_service.list_versions(self.teacher_a, plan["id"])["versions"]
        self.assertEqual(len(versions), 2)
        v1 = [v for v in versions if v["version"] == 1][0]
        self.assertEqual(v1["content"]["objectives"], ["掌握前中后序遍历"])  # 未被覆盖

        # 发布：草稿状态学生不可见 → 发布后可见
        self.assertEqual(lesson_plan_service.student_visible_plans(
            self.student_a, course["id"]), [])
        published = lesson_plan_service.publish(self.teacher_a, plan["id"])
        self.assertEqual(published["status"], "published")
        visible = lesson_plan_service.student_visible_plans(self.student_a, course["id"])
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["content"]["objectives"], ["教师改过的目标"])

    def test_publish_requires_complete_content(self):
        from services import lesson_plan_service
        from services.teaching_data import TeachingError
        course = self.make_course()
        plan = lesson_plan_service.td.create_lesson_plan(
            course["id"], created_by=self.teacher_a, title="空教案",
            content={"objectives": []}, note="测试")
        with self.assertRaises(TeachingError) as ctx:
            lesson_plan_service.publish(self.teacher_a, plan["id"])
        self.assertEqual(ctx.exception.error_type, "invalid_request")

    def test_insufficient_evidence_is_flagged(self):
        from services import lesson_plan_service
        course = self.make_course()
        with mock.patch("services.ai_service.retrieve_and_build_context",
                        return_value={"context": "", "sources": []}), \
             mock.patch("services.ai_service.call_llm", return_value=self._fake_plan_json()):
            plan = lesson_plan_service.generate_draft(
                self.teacher_a, course["id"], topic="没有资料的专题")
        self.assertTrue(plan["insufficient_evidence"],
                        "课程资料为空时必须标记证据不足，不能假装有依据")

    def test_other_teacher_cannot_edit_or_publish(self):
        from services import lesson_plan_service
        from services.teaching_data import TeachingError
        course = self.make_course()
        plan = lesson_plan_service.td.create_lesson_plan(
            course["id"], created_by=self.teacher_a, title="教案",
            content={"objectives": ["a"], "steps": [{"name": "x"}]}, note="t")
        with self.assertRaises(TeachingError) as ctx:
            lesson_plan_service.publish(self.teacher_b, plan["id"])
        self.assertEqual(ctx.exception.error_type, "forbidden")
        with self.assertRaises(TeachingError):
            lesson_plan_service.update_plan(self.teacher_b, plan["id"], content={})

    def test_export_markdown_contains_sections(self):
        from services import lesson_plan_service
        course = self.make_course()
        plan = lesson_plan_service.td.create_lesson_plan(
            course["id"], created_by=self.teacher_a, title="二叉树的遍历",
            topic="遍历", hours=1.0,
            content={"objectives": ["目标1"], "key_points": ["重点1"],
                     "difficulties": ["难点1"],
                     "steps": [{"name": "导入", "minutes": 10, "content": "举例"}],
                     "examples": [{"question": "q", "answer": "a"}],
                     "homework": [{"question": "h", "answer": "ha"}]},
            evidence=[{"file_name": "讲义.txt", "page": 2}], note="t")
        full = lesson_plan_service.td.get_lesson_plan(plan["id"])
        md = lesson_plan_service.to_markdown(full)
        for token in ("# 二叉树的遍历", "教学目标", "教学步骤与时间分配", "课后练习",
                      "依据的课程资料", "讲义.txt"):
            self.assertIn(token, md)


class TestAssignmentFlow(TeacherBase):
    """教师发练习 → 学生作答 → 学情更新（复用既有判分与答案隔离）"""

    def _make_assignment(self, course, klass, questions=2, due=None):
        """用真实题库写入题目（走 quiz_service，与生产一致）"""
        from services import quiz_service
        items = []
        for i in range(questions):
            items.append({
                "type": "single_choice",
                "question": f"二叉树遍历第{i + 1}题：前序遍历的顺序是？",
                "options": ["根左右", "左根右", "左右根", "根右左"],
                "answer": "根左右",
                "explanation": "前序为根左右",
            })
        public = quiz_service.persist_questions(
            subject_id=course["subject_id"], knowledge_point_name="二叉树遍历",
            questions=items, source="test")
        return self.td.create_assignment(
            course_id=course["id"], class_id=klass["id"], subject_id=course["subject_id"],
            created_by=self.teacher_a, title="二叉树练习", description="课后练习",
            due_date=due, question_ids=[q["id"] for q in public])

    def test_publish_creates_student_task_and_deadline(self):
        from services import learning_service, plan_adjustment
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        self.join(klass, self.student_b)
        due = "2026-12-31"
        assignment = self._make_assignment(course, klass, due=due)

        # 草稿阶段：学生看不到
        from services import assignment_service
        self.assertEqual(assignment_service.list_student_assignments(self.student_a), [])

        result = self._publish(assignment)
        self.assertEqual(len(result["created"]), 2)
        self.assertEqual(result["failed"], [])

        # 学生任务进入既有"今日任务"闭环
        tasks = learning_service.list_tasks(self.student_a, course["subject_id"], "todo")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["source"], "teacher_assignment")
        self.assertEqual(tasks[0]["assignment_id"], assignment["id"])

        # 教师固定截止时间写入学生约束
        constraints = plan_adjustment.get_constraints(self.student_a, course["subject_id"])
        self.assertEqual(constraints["teacher_deadline"], due)

        # 重复发布不重复堆任务
        again = self._publish(assignment)
        self.assertTrue(again["already_published"])
        tasks = learning_service.list_tasks(self.student_a, course["subject_id"], "todo")
        self.assertEqual(len(tasks), 1)

    def _publish(self, assignment):
        from services import assignment_service
        return assignment_service.publish(self.teacher_a, assignment["id"])

    def test_draft_assignment_invisible_and_published_visible(self):
        from services import assignment_service
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        assignment = self._make_assignment(course, klass)

        self.assertEqual(assignment_service.list_student_assignments(self.student_a), [])
        from services.teaching_data import TeachingError
        with self.assertRaises(TeachingError) as ctx:
            assignment_service.assignment_for_student(self.student_a, assignment["id"])
        self.assertEqual(ctx.exception.error_type, "not_found")  # 草稿对外一律"不存在"

        self._publish(assignment)
        visible = assignment_service.list_student_assignments(self.student_a)
        self.assertEqual(len(visible), 1)
        # 学生看到的题目不含标准答案
        for q in visible[0]["questions"]:
            self.assertNotIn("correct_option_id", q)
            self.assertNotIn("reference_answer", q)

    def test_non_member_cannot_open_assignment(self):
        from services import assignment_service
        from services.teaching_data import TeachingError
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        assignment = self._make_assignment(course, klass)
        self._publish(assignment)
        with self.assertRaises(TeachingError) as ctx:
            assignment_service.assignment_for_student(self.student_b, assignment["id"])
        self.assertEqual(ctx.exception.error_type, "forbidden")

    def test_analytics_counts_match_raw_records(self):
        """学情统计随作答更新，样本量与原始记录一致"""
        from services import assignment_service, class_analytics, quiz_service
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        self.join(klass, self.student_b)
        assignment = self._make_assignment(course, klass, questions=3)
        self._publish(assignment)

        # 样本不足 → 明确"数据不足"，不给班级结论
        early = class_analytics.class_overview(self.teacher_a, klass["id"])
        self.assertFalse(early["enough_data"])
        self.assertTrue(early["insufficient_reason"])

        # 两名学生作答：1 人全对，1 人错 2 题
        qids = assignment["question_ids"]
        for qid in qids:
            quiz_service.submit_attempt(self.student_a, qid, "A", client_token=f"a{qid}")
        quiz_service.submit_attempt(self.student_b, qids[0], "A", client_token="b1")
        quiz_service.submit_attempt(self.student_b, qids[1], "B", client_token="b2")
        quiz_service.submit_attempt(self.student_b, qids[2], "B", client_token="b3")

        stats = class_analytics.class_overview(self.teacher_a, klass["id"])
        self.assertEqual(stats["participation"]["student_count"], 2)
        self.assertEqual(stats["participation"]["answered_student_count"], 2)
        self.assertEqual(stats["participation"]["attempt_count"], 6)
        self.assertTrue(stats["enough_data"])
        # 与原始记录一致
        total_wrong = sum(k["wrong_count"] for k in stats["knowledge_points"])
        self.assertEqual(total_wrong, 2)
        self.assertTrue(stats["wrong_questions"])
        self.assertIn("quiz_attempts", " ".join(stats["sources"]))

    def test_analytics_requires_teacher(self):
        from services import class_analytics
        from services.teaching_data import TeachingError
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        for who in (self.student_a, self.teacher_b):
            with self.assertRaises(TeachingError) as ctx:
                class_analytics.class_overview(who, klass["id"])
            self.assertEqual(ctx.exception.error_type, "forbidden")

    def test_analytics_does_not_leak_chat_content(self):
        """教师端学情不得包含学生私人聊天原文"""
        from services import class_analytics, conversation_service
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        conv = conversation_service.create_conversation(self.student_a, "和老师的悄悄话")
        conversation_service.add_message(
            conv["id"] if isinstance(conv, dict) else conv,
            "user", "这是我的私人提问：二叉树好难")
        stats = class_analytics.class_overview(self.teacher_a, klass["id"])
        blob = str(stats)
        self.assertNotIn("悄悄话", blob)
        self.assertNotIn("私人提问", blob)

    def test_student_can_complete_after_answering(self):
        from services import assignment_service
        from services.teaching_data import TeachingError
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        assignment = self._make_assignment(course, klass, questions=1)
        self._publish(assignment)

        with self.assertRaises(TeachingError) as ctx:
            assignment_service.complete_assignment_task(self.student_a, assignment["id"])
        self.assertEqual(ctx.exception.error_type, "invalid_request")  # 没作答不能算完成

        from services import quiz_service
        quiz_service.submit_attempt(self.student_a, assignment["question_ids"][0], "A",
                                    client_token="done1")
        result = assignment_service.complete_assignment_task(self.student_a, assignment["id"])
        self.assertEqual(result["completed_tasks"], 1)

    def test_published_assignment_cannot_be_edited(self):
        from services import assignment_service
        from services.teaching_data import TeachingError
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        assignment = self._make_assignment(course, klass)
        self._publish(assignment)
        with self.assertRaises(TeachingError) as ctx:
            assignment_service.update_assignment(self.teacher_a, assignment["id"],
                                                 title="偷偷改标题")
        self.assertEqual(ctx.exception.error_type, "invalid_request")

    def test_questions_from_other_subject_rejected(self):
        """不能把别的学科的题挂到本课程作业上"""
        from services import quiz_service
        from services.teaching_data import TeachingError
        course = self.make_course()
        klass = self.make_class(course)
        foreign = quiz_service.persist_questions(
            subject_id=SUBJECT, knowledge_point_name="越界知识点",
            questions=[{"type": "single_choice", "question": "外来题目",
                        "options": ["A1", "B1"], "answer": "A1"}], source="test")
        # 服务层入口（assignment_service.create_assignment）就会拦住跨学科题目
        from services import assignment_service
        with self.assertRaises(TeachingError) as ctx:
            assignment_service.create_assignment(
                self.teacher_a, course["id"], class_id=klass["id"], title="越界作业",
                question_ids=[foreign[0]["id"]])
        self.assertEqual(ctx.exception.error_type, "forbidden")
        # 作业没有被创建出来
        self.assertEqual(self.td.list_assignments(course_id=course["id"]), [])


class TestDeadlineProtection(TeacherBase):
    """教师固定截止时间不能被学生的个人计划改动"""

    def test_student_cannot_push_teacher_deadline(self):
        from services import plan_adjustment
        from services.plan_adjustment import PlanAdjustmentError
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        assignment = TestAssignmentFlow._make_assignment(self, course, klass, due="2026-10-01")
        TestAssignmentFlow._publish(self, assignment)

        got = plan_adjustment.get_constraints(self.student_a, course["subject_id"])
        self.assertEqual(got["teacher_deadline"], "2026-10-01")
        # 效果截止取更严格者
        self.assertEqual(got["effective_deadline"], "2026-10-01")

        # 学生试图把自己截止改晚：可以（那是他自己的截止）
        plan_adjustment.set_constraints(self.student_a, course["subject_id"],
                                        deadline="2026-12-31")
        after = plan_adjustment.get_constraints(self.student_a, course["subject_id"])
        self.assertEqual(after["effective_deadline"], "2026-10-01")  # 教师截止仍然生效

        # 学生试图直接改教师的固定截止 → 拒绝
        with self.assertRaises(PlanAdjustmentError):
            plan_adjustment.set_constraints(self.student_a, course["subject_id"],
                                            teacher_deadline="2026-12-31")

    def test_agent_adjustment_never_moves_task_past_deadline(self):
        from services import plan_adjustment
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        assignment = TestAssignmentFlow._make_assignment(self, course, klass, due="2026-10-05")
        TestAssignmentFlow._publish(self, assignment)

        proposal = plan_adjustment.propose_schedule(self.student_a, course["subject_id"])
        for change in proposal.get("changes", []):
            if change.get("change_type") == "reschedule" and change.get("to_date"):
                self.assertLessEqual(change["to_date"], "2026-10-05",
                                     "调整方案把任务排到了教师截止时间之后")


class TestLegacyEndpointGuards(TeacherBase):
    """旧接口不能绕过课程权限（教师端建设前的主要缺口）"""

    def setUp(self):
        super().setUp()
        self.course = self.make_course()
        self.klass = self.make_class(self.course)
        self.join(self.klass, self.student_a)
        self.upload(self.course, "课程讲义.txt")

    def test_subject_scope_guard_on_owned_subject(self):
        from services import teaching_access
        sid = self.course["subject_id"]
        # 授课教师与其学生可读；其他教师/学生不可读
        self.assertTrue(teaching_access.can_read_subject(self.teacher_a, sid))
        self.assertTrue(teaching_access.can_read_subject(self.student_a, sid))
        self.assertFalse(teaching_access.can_read_subject(self.teacher_b, sid))
        self.assertFalse(teaching_access.can_read_subject(self.student_b, sid))
        # 写：只有授课教师
        self.assertTrue(teaching_access.can_write_subject(self.teacher_a, sid))
        self.assertFalse(teaching_access.can_write_subject(self.teacher_b, sid))
        self.assertFalse(teaching_access.can_write_subject(self.student_a, sid))

    def test_deps_guards_raise_403(self):
        """api.deps 的学科守卫（供旧接口复用）"""
        import asyncio
        from fastapi import HTTPException
        from api.deps import require_subject_read, require_subject_write
        sid = self.course["subject_id"]

        # 非成员读 → 403
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(require_subject_read(sid, self.student_b))
        self.assertEqual(ctx.exception.status_code, 403)

        # 学生写 → 403（即便 KNOWLEDGE_ADMIN_USERS 未配置也不能放行）
        with mock.patch.dict(os.environ, {"KNOWLEDGE_ADMIN_USERS": ""}):
            with self.assertRaises(HTTPException) as ctx2:
                asyncio.run(require_subject_write(sid, self.student_a))
            self.assertEqual(ctx2.exception.status_code, 403)

        # 授课教师写 → 通过
        self.assertEqual(asyncio.run(require_subject_write(sid, self.teacher_a)),
                         self.teacher_a)
        # 历史全局学科：读放行、写回退既有白名单（未配置白名单时放行）
        self.assertEqual(asyncio.run(require_subject_read(SUBJECT, self.student_b)),
                         self.student_b)
        with mock.patch.dict(os.environ, {"KNOWLEDGE_ADMIN_USERS": ""}):
            self.assertEqual(asyncio.run(require_subject_write(SUBJECT, self.student_b)),
                             self.student_b)
        # 配置白名单后，历史学科的写被收紧
        with mock.patch.dict(os.environ, {"KNOWLEDGE_ADMIN_USERS": "teacher_a"}):
            with self.assertRaises(HTTPException):
                asyncio.run(require_subject_write(SUBJECT, self.student_b))

    def test_guard_can_be_switched_off_for_troubleshooting(self):
        import asyncio
        from api.deps import require_subject_read
        with mock.patch.dict(os.environ, {"TEACHING_SCOPE_GUARD": "0"}):
            self.assertEqual(asyncio.run(require_subject_read(self.course["subject_id"],
                                                              self.student_b)),
                             self.student_b)

    def test_extraction_endpoints_require_course_write(self):
        """知识提取的写入端点受课程权限约束（外部模型用 Mock，条件可控）"""
        from services import knowledge_expander
        sid = self.course["subject_id"]
        fake = '{"knowledge_point": "二叉树遍历", "content": "前序遍历顺序为根左右。", "confidence": 0.9}'
        with mock.patch.object(knowledge_expander, "call_llm", return_value=fake),              mock.patch.object(knowledge_expander, "LLM_AVAILABLE", True):
            kid = knowledge_expander.extract_knowledge_from_qa(
                self.student_a, sid, "什么是前序遍历？",
                "前序遍历先访问根节点，再依次遍历左子树和右子树，因此顺序为根左右。" * 2, 5)
        self.assertTrue(kid, "Mock 模型下应能提取出知识")
        item = knowledge_expander.get_knowledge(kid)
        self.assertEqual(item["subject_id"], sid)

        import asyncio
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            from api.routes.agent import _guard_knowledge_write
            asyncio.run(_guard_knowledge_write(kid, self.student_b))
        from api.routes.agent import _guard_knowledge_write
        asyncio.run(_guard_knowledge_write(kid, self.teacher_a))  # 不抛异常即通过


class TestTeacherDashboard(TeacherBase):

    def test_dashboard_aggregates_own_courses_only(self):
        from services import class_analytics
        mine = self.make_course(title="我的课")
        other = self.make_course(owner=self.teacher_b, title="别人的课")
        klass = self.make_class(mine)
        self.upload(mine)
        # 一份草稿教案 → 待完成
        self.td.create_lesson_plan(mine["id"], created_by=self.teacher_a, title="草稿教案",
                                   content={"objectives": ["a"]}, note="t")

        data = class_analytics.teacher_dashboard(self.teacher_a)
        titles = {c["title"] for c in data["courses"]}
        self.assertIn("我的课", titles)
        self.assertNotIn("别人的课", titles)
        self.assertEqual(data["pending_lesson_plans"][0]["title"], "草稿教案")
        self.assertGreaterEqual(data["courses"][0]["material_count"], 1)
        self.assertEqual(data["courses"][0]["class_count"], 1)
        self.assertEqual(data["role"], "teacher")

    def test_dashboard_empty_for_new_teacher(self):
        from services import class_analytics
        data = class_analytics.teacher_dashboard(self.teacher_b)
        self.assertEqual(data["courses"], [])
        self.assertEqual(data["pending_lesson_plans"], [])
        self.assertEqual(data["focus_knowledge_points"], [])


if __name__ == "__main__":
    unittest.main()
