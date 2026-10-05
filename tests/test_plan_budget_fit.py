"""计划预算：生效前校验与重排（2026-10-05）

复现场景"每天 30 分钟，却排了 90 分钟"：
- 可调整任务重排到后续可用日期；
- 教师固定任务保留原日期，不擅自延期；
- 仍无法满足（总量超容量 / 单任务超单日预算）→ fits=False 且给出冲突，
  计划标为草案（DRAFT），不当作已生效。
"""
import uuid
from datetime import date, timedelta

from services import learning_service, plan_adjustment

_RUN = uuid.uuid4().hex[:8]
USER = f"user_budget_{_RUN}"
SUBJECT = f"course_budget_{_RUN}"
TODAY = date.today().isoformat()


def _save(tasks):
    return learning_service._save_plan(
        {"subject_id": SUBJECT, "user_id": USER, "title": "预算测试计划"}, tasks)


def test_over_budget_moves_movable_keeps_teacher_and_marks_conflict():
    plan = _save([
        {"day": 1, "knowledge_point": "KP-大", "task_type": "READ", "title": "大任务",
         "priority": 1, "estimated_minutes": 60, "status": "TODO", "source": "manual"},
        {"day": 1, "knowledge_point": "KP-小", "task_type": "REVIEW", "title": "小任务",
         "priority": 2, "estimated_minutes": 30, "status": "TODO", "source": "manual"},
        {"day": 1, "knowledge_point": "KP-教师", "task_type": "QUIZ", "title": "教师任务",
         "priority": 1, "estimated_minutes": 30, "status": "TODO",
         "source": "teacher_assignment", "planned_date": TODAY},
    ])
    plan_adjustment.set_constraints(
        USER, SUBJECT, daily_minutes=30,
        deadline=(date.today() + timedelta(days=2)).isoformat())

    result = plan_adjustment.fit_plan_to_budget(USER, SUBJECT, plan["id"])
    assert result["fits"] is False
    assert result["fixed_minutes"] == 30
    assert result["conflicts"]

    # 教师固定任务保留原日期（不得擅自延期）
    tasks = {t["title"]: t for t in learning_service.list_tasks(USER, SUBJECT, None, plan["id"], 100)}
    assert tasks["教师任务"]["planned_date"] == TODAY

    # 小任务被重排到后续日期（不再堆在第一天）
    assert any(m["task_id"] == tasks["小任务"]["id"] for m in result["moved"])
    assert tasks["小任务"]["planned_date"] != TODAY


def test_plan_status_can_be_marked_draft():
    plan = _save([
        {"day": 1, "knowledge_point": "KP", "task_type": "READ", "title": "任务",
         "priority": 1, "estimated_minutes": 20, "status": "TODO", "source": "manual"},
    ])
    learning_service._set_plan_status(plan["id"], "DRAFT")
    plans = {p["id"]: p for p in learning_service.list_plans(SUBJECT, user_id=USER)}
    assert plans[plan["id"]]["status"] == "DRAFT"
