"""作业题目的"渠道 vs 依据来源"分离回归（2026-10-04）

修复前的真实缺陷：教师作业出题把**渠道**写进了 `quiz_questions.source`
（写死 "teacher_assignment"），于是
  - 题目真实的**依据来源**（课程知识库/网络资料/AI 通用知识）被覆盖，界面显示不出来；
  - 前端来源标签表里根本没有 `teacher_assignment` 这个键，等于显示不出任何依据；
  - 也分不清"教师布置"与"自己练"。
现在：`source` 只表示依据来源，`channel` 表示渠道，两者分别下发与展示。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_teacher_module import TeacherBase  # noqa: E402


class AssignmentQuestionSourceTests(TeacherBase):

    def _make_question(self):
        from services import quiz_service
        course = self.make_course()
        klass = self.make_class(course)
        self.join(klass, self.student_a)
        public = quiz_service.persist_questions(
            subject_id=course["subject_id"], knowledge_point_name="二叉树遍历",
            questions=[{
                "type": "single_choice",
                "question": "中序遍历的顺序是？",
                "options": ["左根右", "根左右", "左右根", "根右左"],
                "answer": "左根右",
                "explanation": "中序=左根右",
            }], source="knowledge_base")
        return course, klass, public[0]["id"]

    def test_assignment_payload_separates_channel_and_source(self):
        from services import assignment_service
        course, klass, qid = self._make_question()
        self.td.create_assignment(
            course_id=course["id"], class_id=klass["id"], subject_id=course["subject_id"],
            created_by=self.teacher_a, title="来源标注测试", description="",
            due_date=None, question_ids=[qid])

        items = assignment_service.list_teacher_assignments(self.teacher_a, course["id"])
        self.assertTrue(items, "应能列出教师作业")
        questions = items[0].get("questions") or []
        self.assertTrue(questions, "作业应带出题目")
        q = questions[0]
        # 依据来源保留（这是"题目按什么命制"）
        self.assertEqual(q.get("source"), "knowledge_base",
                         "题目依据来源必须保留，不能被渠道覆盖")
        # 渠道单独表达
        self.assertEqual(q.get("channel"), "teacher_assignment")
        self.assertEqual(q.get("channel_label"), "教师作业")

    def test_question_source_is_not_channel(self):
        """直接核对持久化层：题目 source 不应等于渠道值（修复前的写死值）"""
        from services import quiz_service
        _course, _klass, qid = self._make_question()
        row = quiz_service.get_question_admin(qid) or {}
        self.assertNotEqual(row.get("source"), "teacher_assignment",
                            "quiz_questions.source 不应被写成渠道名")

    def test_student_view_also_carries_channel_and_source(self):
        from services import assignment_service
        course, klass, qid = self._make_question()
        asg = self.td.create_assignment(
            course_id=course["id"], class_id=klass["id"], subject_id=course["subject_id"],
            created_by=self.teacher_a, title="学生视图来源测试", description="",
            due_date=None, question_ids=[qid])
        # 发布（学生需要已发布的作业）；发布接口在 assignment_service 里
        assignment_service.publish(self.teacher_a, asg["id"])
        detail = assignment_service.assignment_for_student(self.student_a, asg["id"])
        q = (detail.get("questions") or [{}])[0]
        self.assertEqual(q.get("channel"), "teacher_assignment",
                         "学生视图也要能区分渠道")
        self.assertEqual(q.get("source"), "knowledge_base",
                         "学生视图的依据来源不能被渠道覆盖")


if __name__ == "__main__":
    unittest.main(verbosity=2)
