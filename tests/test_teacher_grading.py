"""教师端：单学生作答明细 + 教师改判（2026-10-04）

覆盖负责人明确要求：
  - 教师能看某个学生的逐题作答明细；
  - 改判保留**原判定 / 教师判定 / 理由 / 改判人 / 时间**；
  - 改判后**同步**学情（生效判定）、错题本、掌握度、复习状态；
  - 跨班级 / 非教师越权必须被拒；重复改判保留**首次**的原判定。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_teacher_module import TeacherBase  # noqa: E402

KP = "二叉树遍历"


class TeacherGradingTests(TeacherBase):

    def setUp(self):
        super().setUp()
        from services import quiz_service
        from services.learning_data import _get_db
        self.quiz = quiz_service
        self._get_db = _get_db

        self.course = self.make_course()
        self.klass = self.make_class(self.course)
        self.join(self.klass, self.student_a)
        # 另一位老师的课与班（用于跨班级越权验证）
        self.course_b = self.make_course(owner=self.teacher_b, title="另一位老师的课")
        self.klass_b = self.make_class(self.course_b, owner=self.teacher_b)
        self.join(self.klass_b, self.student_b)

        public = quiz_service.persist_questions(
            subject_id=self.course["subject_id"], knowledge_point_name=KP,
            questions=[{
                "type": "single_choice",
                "question": "前序遍历的顺序是？",
                "options": ["根左右", "左根右", "左右根", "根右左"],
                "answer": "根左右",
                "explanation": "前序=根左右",
            }], source="test")
        self.qid = public[0]["id"]
        # 学生答错（客观题自动判分）
        quiz_service.submit_attempt(self.student_a, self.qid, "B",   # 客观题按选项 ID 作答
                                    client_token="tg-1",
                                    subject_id=self.course["subject_id"])
        row = self._attempt_row()
        self.attempt_id = row["id"]
        self.kp_id = row["knowledge_point_id"] or ""

    # ---------- 辅助 ----------
    def _attempt_row(self, user_id=None):
        conn = self._get_db()
        try:
            return conn.execute(
                "SELECT * FROM quiz_attempts WHERE user_id=? ORDER BY rowid DESC LIMIT 1",
                (user_id or self.student_a,)).fetchone()
        finally:
            conn.close()

    def _detail(self, class_id=None, student_id=None, teacher=None):
        from services import teacher_grading
        return teacher_grading.list_student_attempts(
            teacher or self.teacher_a, class_id or self.klass["id"],
            student_id or self.student_a)

    # ---------- 明细 ----------
    def test_detail_lists_single_student_attempts(self):
        data = self._detail()
        self.assertEqual(data["student"]["user_id"], self.student_a)
        self.assertEqual(data["class"]["id"], self.klass["id"])
        self.assertGreaterEqual(data["summary"]["attempt_count"], 1)
        self.assertEqual(data["summary"]["regraded_count"], 0)
        row = next(a for a in data["attempts"] if a["attempt_id"] == self.attempt_id)
        self.assertTrue(row["stem"], "明细必须带题干（教师要知道学生答的哪道题）")
        self.assertIs(row["effective_is_correct"], False)
        self.assertEqual(row["effective_grade_status"], "graded")
        self.assertIsNone(row["original"], "未改判时不应有原判定")
        self.assertIsNone(row["teacher"], "未改判时不应有教师判定")

    # ---------- 改判：保留三方信息 ----------
    def test_regrade_keeps_original_and_records_teacher(self):
        from services import teacher_grading
        res = teacher_grading.regrade_attempt(
            self.teacher_a, self.klass["id"], self.attempt_id, True, "学生口头复述正确，予以改判")
        row = res["attempt"]
        # 生效判定 = 教师结论（学情/错题/掌握度都读它）
        self.assertIs(row["effective_is_correct"], True)
        self.assertEqual(row["effective_grade_status"], "teacher_regraded")
        # 原判定被固化保留
        self.assertIsNotNone(row["original"])
        self.assertIs(row["original"]["is_correct"], False)
        self.assertEqual(row["original"]["grade_status"], "graded")
        # 教师判定与留痕
        self.assertIs(row["teacher"]["is_correct"], True)
        self.assertEqual(row["teacher"]["reason"], "学生口头复述正确，予以改判")
        self.assertEqual(row["teacher"]["teacher_id"], self.teacher_a)
        self.assertTrue(row["teacher"]["graded_at"])

    def test_repeated_regrade_keeps_first_original(self):
        from services import teacher_grading
        teacher_grading.regrade_attempt(self.teacher_a, self.klass["id"],
                                        self.attempt_id, True, "第一次改判")
        res = teacher_grading.regrade_attempt(self.teacher_a, self.klass["id"],
                                              self.attempt_id, False, "第二次改判")
        row = res["attempt"]
        self.assertIs(row["original"]["is_correct"], False, "原判定必须是**首次**改判前的结论")
        self.assertEqual(row["original"]["grade_status"], "graded")
        self.assertIs(row["effective_is_correct"], False)
        self.assertEqual(row["teacher"]["reason"], "第二次改判", "教师判定取最新一次")
        detail = self._detail()
        self.assertEqual(detail["summary"]["regraded_count"], 1)

    def test_regrade_requires_reason(self):
        from services import teacher_grading
        from services.teaching_data import TeachingError
        with self.assertRaises(TeachingError) as ctx:
            teacher_grading.regrade_attempt(self.teacher_a, self.klass["id"],
                                            self.attempt_id, True, "   ")
        self.assertEqual(ctx.exception.error_type, "invalid_request")

    # ---------- 改判后的联动 ----------
    def test_regrade_syncs_wrong_book_mastery_and_review(self):
        from services import mastery_service, review_service, teacher_grading

        # 前置：该知识点确有待复习任务
        review_service.ensure_review_task(
            self.student_a, self.course["subject_id"], self.kp_id, KP, reason="测试前置")
        pending_before = review_service.list_review_tasks(
            self.student_a, self.course["subject_id"], "PENDING")
        self.assertTrue(pending_before, "前置条件：应存在待复习任务")

        wrong_before = self.quiz.list_wrong_questions(self.student_a, self.course["subject_id"])
        self.assertTrue(any(w["question_id"] == self.qid for w in wrong_before),
                        "前置条件：答错后应进入错题本")

        def mastery_of_point():
            items = mastery_service.list_mastery(self.student_a, self.course["subject_id"])
            return next((m for m in items if (m.get("knowledge_point_name") or "") == KP
                         or (m.get("knowledge_point") or "") == KP), None)

        before = mastery_of_point()

        res = teacher_grading.regrade_attempt(
            self.teacher_a, self.klass["id"], self.attempt_id, True, "课堂核对后确认正确")

        # 1) 错题本随生效判定移除
        wrong_after = self.quiz.list_wrong_questions(self.student_a, self.course["subject_id"])
        self.assertFalse(any(w["question_id"] == self.qid for w in wrong_after),
                         "改判为正确后，该题应移出错题本")
        # 2) 掌握度重算
        self.assertTrue(res["linkage"]["mastery"]["rebuilt"], res["linkage"]["mastery"])
        after = mastery_of_point()
        if before and after:
            b = before.get("mastery_level")
            a = after.get("mastery_level")
            if b is not None and a is not None:
                self.assertGreaterEqual(a, b, "改判为正确后掌握度不应变差")
        # 3) 待复习任务被关闭
        pending_after = review_service.list_review_tasks(
            self.student_a, self.course["subject_id"], "PENDING")
        self.assertLess(len(pending_after), len(pending_before),
                        "改判为正确后应关闭该知识点的待复习任务")
        self.assertEqual(res["linkage"]["review"]["action"], "completed")

    def test_regrade_to_wrong_ensures_review(self):
        from services import review_service, teacher_grading
        # 先改判为正确（并关闭待复习），再改判为错误 → 应重新有待复习
        teacher_grading.regrade_attempt(self.teacher_a, self.klass["id"],
                                        self.attempt_id, True, "先改对")
        review_service.complete_review_tasks(self.student_a, self.course["subject_id"],
                                             self.kp_id, reason="清空")
        res = teacher_grading.regrade_attempt(self.teacher_a, self.klass["id"],
                                              self.attempt_id, False, "复核后确认仍错")
        self.assertEqual(res["linkage"]["review"]["action"], "ensured")
        pending = review_service.list_review_tasks(
            self.student_a, self.course["subject_id"], "PENDING")
        self.assertTrue(pending, "改判为错误后应确保存在待复习任务")
        wrong = self.quiz.list_wrong_questions(self.student_a, self.course["subject_id"])
        self.assertTrue(any(w["question_id"] == self.qid for w in wrong),
                        "改判为错误后应重新进入错题本")

    # ---------- 权限与跨班级 ----------
    def test_non_teacher_cannot_read_or_regrade(self):
        from services import teacher_grading
        from services.teaching_data import TeachingError
        with self.assertRaises(TeachingError):
            teacher_grading.list_student_attempts(self.student_a, self.klass["id"], self.student_a)
        with self.assertRaises(TeachingError):
            teacher_grading.regrade_attempt(self.student_a, self.klass["id"],
                                            self.attempt_id, True, "学生自己改判")

    def test_teacher_of_other_course_cannot_regrade(self):
        from services import teacher_grading
        from services.teaching_data import TeachingError
        # 别的老师用**自己的**班 → 该作答不属于其班
        with self.assertRaises(TeachingError):
            teacher_grading.regrade_attempt(self.teacher_b, self.klass_b["id"],
                                            self.attempt_id, True, "越权改判")
        # 别的老师直接用**我的**班 → 不是课程归属教师
        with self.assertRaises(TeachingError):
            teacher_grading.regrade_attempt(self.teacher_b, self.klass["id"],
                                            self.attempt_id, True, "越权改判")
        # 明细同理
        with self.assertRaises(TeachingError):
            teacher_grading.list_student_attempts(self.teacher_b, self.klass["id"], self.student_a)

    def test_regrade_rejects_student_not_in_class(self):
        """本班教师不能改判"非本班学生"的作答（跨班数据不可越界）"""
        from services import quiz_service, teacher_grading
        from services.teaching_data import TeachingError
        public = quiz_service.persist_questions(
            subject_id=self.course["subject_id"], knowledge_point_name=KP,
            questions=[{"type": "single_choice", "question": "后序遍历的顺序是？",
                        "options": ["左右根", "根左右", "左根右", "根右左"],
                        "answer": "左右根", "explanation": "后序=左右根"}], source="test")
        quiz_service.submit_attempt(self.student_b, public[0]["id"], "B",
                                    client_token="tg-b1",
                                    subject_id=self.course["subject_id"])
        other_attempt = self._attempt_row(user_id=self.student_b)
        with self.assertRaises(TeachingError) as ctx:
            teacher_grading.regrade_attempt(self.teacher_a, self.klass["id"],
                                            other_attempt["id"], True, "改判别班学生")
        self.assertEqual(ctx.exception.error_type, "forbidden")
        # 明细也不能查非本班学生
        with self.assertRaises(TeachingError):
            teacher_grading.list_student_attempts(self.teacher_a, self.klass["id"], self.student_b)


    # ---------- 教师级入口（自动解析班级，前端免选班级）----------
    def test_teacher_level_entry_resolves_class(self):
        from services import teacher_grading
        data = teacher_grading.list_student_attempts_for_teacher(self.teacher_a, self.student_a)
        self.assertEqual(data["class"]["id"], self.klass["id"])
        res = teacher_grading.regrade_attempt_for_teacher(
            self.teacher_a, self.attempt_id, True, "教师级入口改判")
        self.assertIs(res["attempt"]["effective_is_correct"], True)
        self.assertIs(res["attempt"]["original"]["is_correct"], False)
        self.assertEqual(res["attempt"]["teacher"]["reason"], "教师级入口改判")

    def test_teacher_level_entry_rejects_other_teachers_student(self):
        from services import teacher_grading
        from services.teaching_data import TeachingError
        with self.assertRaises(TeachingError):
            teacher_grading.list_student_attempts_for_teacher(self.teacher_b, self.student_a)
        with self.assertRaises(TeachingError):
            teacher_grading.regrade_attempt_for_teacher(
                self.teacher_b, self.attempt_id, True, "越权改判")
        with self.assertRaises(TeachingError):
            teacher_grading.regrade_attempt_for_teacher(
                self.teacher_b, "att_not_exist", True, "不存在的作答")


if __name__ == "__main__":
    unittest.main(verbosity=2)
