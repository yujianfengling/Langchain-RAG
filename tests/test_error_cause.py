"""错因记录（学生自报）回归测试（2026-10-24）

约束：
- 只允许修改**自己的、答错的**作答；
- 只记录学生判断，不改判分/掌握度；
- 错因会随错题列表返回，供“错题诊所”展示。
"""

import uuid

import pytest

from services import quiz_service
from services.learning_data import _get_db, _new_id, _now


def _seed_attempt(is_correct: int = 0, user_id: str | None = None):
    tag = uuid.uuid4().hex[:8]
    user_id = user_id or "err_user_" + tag
    subject = "err_sub_" + tag
    qid = "q_" + tag
    aid = "att_" + tag
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO quiz_questions
               (id, subject_id, knowledge_point_id, knowledge_point_name, question_type,
                stem, options_json, correct_option_id, content_hash, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (qid, subject, "kp_" + tag, "测试知识点", "single_choice",
             "1+1=?", '[{"id":"A","text":"2"},{"id":"B","text":"3"}]', "A",
             "hash_" + tag, _now()),
        )
        conn.execute(
            """INSERT INTO quiz_attempts
               (id, user_id, subject_id, question_id, knowledge_point_id, knowledge_point_name,
                attempt_no, attempt_kind, user_answer, is_correct, grade_status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (aid, user_id, subject, qid, "kp_" + tag, "测试知识点",
             1, "independent", "B" if not is_correct else "A", is_correct, "graded", _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return user_id, subject, qid, aid


def test_report_error_cause_persists_and_returns_label():
    user, _, _, aid = _seed_attempt()
    out = quiz_service.report_error_cause(aid, user, "concept_confusion", "把定义记反了")
    assert out["error_code"] == "concept_confusion"
    assert out["error_label"] == "概念混淆"
    assert out["error_note"] == "把定义记反了"

    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM quiz_attempts WHERE id=?", (aid,)).fetchone()
    finally:
        conn.close()
    assert row["error_code"] == "concept_confusion"
    assert row["error_reported_at"]


def test_report_error_cause_is_overwritable():
    user, _, _, aid = _seed_attempt()
    quiz_service.report_error_cause(aid, user, "misread")
    out = quiz_service.report_error_cause(aid, user, "calculation")
    assert out["error_code"] == "calculation"


def test_report_error_cause_rejects_invalid_code():
    user, _, _, aid = _seed_attempt()
    with pytest.raises(quiz_service.QuizServiceError) as exc:
        quiz_service.report_error_cause(aid, user, "not_a_cause")
    assert exc.value.error_type == "invalid_request"


def test_report_error_cause_rejects_correct_attempt():
    user, _, _, aid = _seed_attempt(is_correct=1)
    with pytest.raises(quiz_service.QuizServiceError) as exc:
        quiz_service.report_error_cause(aid, user, "calculation")
    assert exc.value.error_type == "invalid_request"


def test_report_error_cause_rejects_other_users_attempt():
    _, _, _, aid = _seed_attempt()
    with pytest.raises(quiz_service.QuizServiceError) as exc:
        quiz_service.report_error_cause(aid, "someone_else", "calculation")
    assert exc.value.error_type == "forbidden"


def test_wrong_list_includes_error_cause():
    user, subject, qid, aid = _seed_attempt()
    quiz_service.report_error_cause(aid, user, "step_omission")
    items = quiz_service.list_wrong_questions(user, subject)
    assert len(items) == 1
    assert items[0]["question_id"] == qid
    assert items[0]["error_code"] == "step_omission"
    assert items[0]["error_label"] == "步骤遗漏"
