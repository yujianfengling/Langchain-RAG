"""教师端：单学生作答明细与教师改判（2026-10-04）

负责人明确要求：
  - 教师能看**某个学生的逐题作答明细**（此前只有班级聚合，没有个人明细）；
  - 教师能对 AI 判分（尤其简答题）**改判**，并保留**原判定、教师判定、理由和时间**；
  - 改判后**同步**学情、错题本、掌握度、复习状态；跨班级/越权必须被拒。

设计取舍（为什么这样存）：
  - `quiz_attempts.is_correct` / `grade_status` 始终保存**生效判定**：教师改判后即教师结论。
    这样学情（class_analytics）、错题本（list_wrong_questions）、掌握度重放、复习联动等
    **既有读取方无需改动**就反映改判，避免"改了但别处仍按旧结论算"的口径分裂。
  - **原判定**另存 `orig_is_correct` / `orig_grade_status`（首次改判时写入，此后不覆盖），
    教师判定、理由、改判人、改判时间另存 `teacher_*`。三者都保留、都可查看。
  - 改判**必须给理由**（留痕），空理由直接拒绝。
  - 改判后立即同步：掌握度按作答记录重算；错题本/学情随生效判定自动更新；
    改判为"错"→ 建/保留该知识点待复习任务；改判为"对"→ 关闭该知识点待复习任务。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from utils.logger import logger

from services import teaching_access as access
from services import teaching_data as td
from services.learning_data import _get_db, ensure_columns
from services.teaching_data import TeachingError

# 教师改判后的判分状态（学情统计把它计入有效证据，见 class_analytics）
GRADE_TEACHER_REGRADED = "teacher_regraded"
MAX_REASON_LEN = 500


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _conn():
    conn = _get_db()
    # 兼容老库：补齐改判相关列（幂等、无副作用）
    ensure_columns(conn, "quiz_attempts")
    return conn


def _class_subject(klass: Dict[str, Any]) -> str:
    course = td.get_course(klass.get("course_id")) or {}
    return course.get("subject_id") or ""


def _require_student_in_class(class_id: str, student_id: str) -> Dict[str, Any]:
    for m in td.list_members(class_id, status=None):
        if m["user_id"] == student_id:
            if m.get("status") != "active":
                raise TeachingError("该学生已不在本班（已退出或被移出）", "forbidden")
            return m
    raise TeachingError("该学生不在本班，无法查看或改判其作答", "forbidden")


def _row_view(row, question: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把作答行转成教师可读视图（原判定 / 教师判定 / 生效判定 三者并列）"""
    orig_present = row["orig_is_correct"] is not None or row["orig_grade_status"] is not None
    teacher_present = row["teacher_is_correct"] is not None
    q = question if question is not None else {}
    return {
        "attempt_id": row["id"],
        "question_id": row["question_id"],
        "stem": q.get("stem") or q.get("question"),
        "question_type": q.get("type"),
        "knowledge_point_name": row["knowledge_point_name"],
        "user_answer": row["user_answer"],
        "user_answer_text": row["user_answer_text"],
        "attempt_no": row["attempt_no"],
        "attempt_kind": row["attempt_kind"],
        "created_at": row["created_at"],
        # ---- 生效判定（学情/错题/掌握度都按它算）----
        "effective_is_correct": None if row["is_correct"] is None else bool(row["is_correct"]),
        "effective_grade_status": row["grade_status"],
        # ---- 原判定（首次改判前的结论；未改判为 None）----
        "original": ({"is_correct": None if row["orig_is_correct"] is None
                      else bool(row["orig_is_correct"]),
                      "grade_status": row["orig_grade_status"]} if orig_present else None),
        # ---- 教师判定 ----
        "teacher": ({"is_correct": bool(row["teacher_is_correct"]),
                     "reason": row["teacher_reason"],
                     "teacher_id": row["teacher_id"],
                     "graded_at": row["teacher_graded_at"]} if teacher_present else None),
        "ai_feedback": row["ai_feedback"],
        "used_hint": bool(row["used_hint"]),
        "revealed_answer": bool(row["revealed_answer"]),
        "duration_sec": row["duration_sec"],
    }


def list_student_attempts(teacher_id: str, class_id: str, student_id: str,
                          days: int = 30, limit: int = 200) -> Dict[str, Any]:
    """某个学生在**本班**的逐题作答明细（含原判定/教师判定/生效判定）"""
    klass = access.require_class_teacher(teacher_id, class_id)
    member = _require_student_in_class(class_id, student_id)
    subject_id = _class_subject(klass)
    if not subject_id:
        raise TeachingError("该班级未关联课程学科", "not_found")

    try:
        days = max(1, min(int(days), 365))
    except (TypeError, ValueError):
        days = 30
    limit = max(1, min(int(limit or 200), 500))
    start = (datetime.now() - timedelta(days=days)).date().isoformat()

    from services import quiz_service
    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT * FROM quiz_attempts
               WHERE user_id=? AND subject_id=? AND date(created_at) >= date(?)
               ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            (student_id, subject_id, start, limit)).fetchall()
    finally:
        conn.close()

    attempts: List[Dict[str, Any]] = []
    for r in rows:
        q = quiz_service.get_question_public(r["question_id"]) or {}
        attempts.append(_row_view(r, q))

    graded = [a for a in attempts if a["effective_is_correct"] is not None]
    summary = {
        "attempt_count": len(attempts),
        "graded_count": len(graded),
        "correct_count": sum(1 for a in graded if a["effective_is_correct"]),
        "wrong_count": sum(1 for a in graded if not a["effective_is_correct"]),
        "ungraded_count": len(attempts) - len(graded),
        "regraded_count": sum(1 for a in attempts if a["teacher"]),
        "range_days": days,
    }
    user = None
    try:
        from services import auth_service
        u = auth_service.get_user(student_id) or {}
        user = {"user_id": student_id,
                "display_name": u.get("display_name") or u.get("username") or student_id}
    except Exception as e:      # 显示名取不到不影响明细
        logger.debug(f"取学生显示名失败: {e}")
        user = {"user_id": student_id, "display_name": student_id}

    return {"class": {"id": klass["id"], "name": klass.get("name")},
            "subject_id": subject_id,
            "student": user,
            "member": {"joined_at": member.get("joined_at"), "status": member.get("status")},
            "summary": summary,
            "attempts": attempts,
            "sources": ["quiz_attempts（学生作答与判分记录）"]}


def _teacher_classes(teacher_id: str) -> List[Dict[str, Any]]:
    """教师名下所有班级（用于把"教师级"入口解析到具体班级）"""
    from services import teaching_service
    out: List[Dict[str, Any]] = []
    for course in teaching_service.list_teacher_courses(teacher_id) or []:
        try:
            for k in teaching_service.list_classes(teacher_id, course["id"]) or []:
                out.append(k)
        except Exception as e:      # 单个课程异常不影响其它课程
            logger.debug(f"列出课程 {course.get('id')} 的班级失败: {e}")
    return out


def _resolve_class_for_student(teacher_id: str, student_id: str) -> str:
    """学生所在、且属于该教师的班级；找不到则拒绝（避免跨教师越权）"""
    for k in _teacher_classes(teacher_id):
        for m in td.list_members(k["id"], status=None):
            if m["user_id"] == student_id and m.get("status") == "active":
                return k["id"]
    raise TeachingError("该学生不在你名下任何班级中，无法查看其作答", "forbidden")


def _resolve_class_for_attempt(teacher_id: str, attempt_id: str) -> str:
    """该作答对应的学生所在、且属于该教师的班级"""
    conn = _conn()
    try:
        row = conn.execute("SELECT user_id FROM quiz_attempts WHERE id=?", (attempt_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise TeachingError("作答记录不存在", "not_found")
    return _resolve_class_for_student(teacher_id, row["user_id"])


def list_student_attempts_for_teacher(teacher_id: str, student_id: str,
                                      days: int = 30, limit: int = 200) -> Dict[str, Any]:
    """教师级入口：自动解析该学生所在（且属于本教师的）班级"""
    return list_student_attempts(teacher_id, _resolve_class_for_student(teacher_id, student_id),
                                 student_id, days=days, limit=limit)


def regrade_attempt_for_teacher(teacher_id: str, attempt_id: str,
                                is_correct: bool, reason: str) -> Dict[str, Any]:
    """教师级入口：自动解析该作答所属（且属于本教师的）班级"""
    return regrade_attempt(teacher_id, _resolve_class_for_attempt(teacher_id, attempt_id),
                           attempt_id, is_correct, reason)


def regrade_attempt(teacher_id: str, class_id: str, attempt_id: str,
                    is_correct: bool, reason: str) -> Dict[str, Any]:
    """教师改判：保留原判定，写入教师判定/理由/人/时间，并同步掌握度与复习"""
    reason = (reason or "").strip()
    if not reason:
        raise TeachingError("改判必须填写理由（会随记录保留，便于追溯）", "invalid_request")
    if len(reason) > MAX_REASON_LEN:
        raise TeachingError(f"理由过长（最多 {MAX_REASON_LEN} 字）", "invalid_request")

    klass = access.require_class_teacher(teacher_id, class_id)
    subject_id = _class_subject(klass)
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM quiz_attempts WHERE id=?", (attempt_id,)).fetchone()
        if not row:
            raise TeachingError("作答记录不存在", "not_found")
        # 越权边界：该作答必须属于**本班**学生、且学科与班级课程一致
        _require_student_in_class(class_id, row["user_id"])
        if subject_id and row["subject_id"] != subject_id:
            raise TeachingError("该作答不属于本班课程", "forbidden")

        verdict = 1 if is_correct else 0
        orig_is_correct = row["orig_is_correct"]
        orig_grade_status = row["orig_grade_status"]
        if orig_is_correct is None and orig_grade_status is None:
            # 首次改判：把当时的结论固化为"原判定"（此后不再覆盖）
            orig_is_correct = row["is_correct"]
            orig_grade_status = row["grade_status"]

        conn.execute(
            """UPDATE quiz_attempts
               SET is_correct=?, grade_status=?,
                   orig_is_correct=?, orig_grade_status=?,
                   teacher_is_correct=?, teacher_id=?, teacher_reason=?, teacher_graded_at=?
               WHERE id=?""",
            (verdict, GRADE_TEACHER_REGRADED, orig_is_correct, orig_grade_status,
             verdict, teacher_id, reason, _now(), attempt_id))
        conn.commit()
        updated = conn.execute("SELECT * FROM quiz_attempts WHERE id=?", (attempt_id,)).fetchone()
    finally:
        conn.close()

    linkage: Dict[str, Any] = {"mastery": None, "review": None}
    # ---- 掌握度：按作答记录重算（生效判定已写入）----
    try:
        from services import mastery_service
        rebuilt = mastery_service.rebuild_point_from_attempts(
            updated["user_id"], updated["subject_id"], updated["knowledge_point_name"] or "")
        linkage["mastery"] = {"rebuilt": bool(rebuilt.get("rebuilt")),
                              "mastery_level": rebuilt.get("mastery_level"),
                              "mastery_status": rebuilt.get("mastery_status")}
    except Exception as e:
        logger.warning(f"教师改判后的掌握度重算失败（改判已保存）: {e}")
        linkage["mastery"] = {"rebuilt": False, "error": str(e)[:200]}

    # ---- 复习任务：改错→确保有待复习；改对→关闭该知识点待复习 ----
    try:
        from services import review_service
        if not is_correct:
            # 修复（2026-10-04）：这里**不传** source_attempt_id。
            # 原因：`ensure_review_task` 以 `(user, source_attempt_id, due)` 作为
            # **步骤级幂等键**，而该作答在最初答错时可能已经建过复习任务；教师改判
            # 再次 ensure 会被幂等挡下（返回 already_processed），结果是"改判为错，却
            # 没有任何待复习任务"。改判是教师的显式决定，语义是"**确保**存在待复习"，
            # 因此这里不加步骤键；重复改判也不会堆叠（ensure 会对同知识点待办做合并）。
            # 溯源信息保留在 reason 里。
            task = review_service.ensure_review_task(
                updated["user_id"], updated["subject_id"],
                updated["knowledge_point_id"] or "", updated["knowledge_point_name"] or "",
                reason=f"教师改判为错误，需重做（来源作答 {attempt_id}）")
            linkage["review"] = {
                "action": "ensured",
                "review_task_id": (task or {}).get("task_id") or (task or {}).get("id"),
                "created": bool((task or {}).get("created")),
                "merged": bool((task or {}).get("merged")),
                "already_processed": bool((task or {}).get("already_processed")),
            }
        else:
            closed = review_service.complete_review_tasks(
                updated["user_id"], updated["subject_id"],
                updated["knowledge_point_id"] or "",
                reason="教师改判为正确", source_attempt_id=attempt_id)
            linkage["review"] = {"action": "completed", "closed_count": closed}
    except Exception as e:
        logger.warning(f"教师改判后的复习联动失败（改判已保存）: {e}")
        linkage["review"] = {"action": "failed", "error": str(e)[:200]}

    from services import quiz_service
    q = quiz_service.get_question_public(updated["question_id"]) or {}
    logger.info(f"教师改判: attempt={attempt_id} teacher={teacher_id} "
                f"verdict={'正确' if is_correct else '错误'}")
    return {"attempt": _row_view(updated, q), "linkage": linkage,
            "wrong_book_updated": True}
