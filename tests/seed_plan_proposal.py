"""为「重大计划调整确认流程」的浏览器验收准备隔离数据（2026-10-01）

背景：重大调整的确认流程（前端 confirm:true 路径）需要在真实界面里验证，
但构造"较大调整"需要学生有若干逾期任务。本脚本在**隔离数据目录**里准备：
  教师 / 管理员 / 学生账号 + 课程 + 班级 + 4 个逾期任务 + 一条"较大调整"建议

用法（必须显式指定隔离数据目录）：

    $env:EDU_DATA_DIR="D:/dev/git_text_work_data"
    python tests/seed_plan_proposal.py --out tests/_plan_confirm_seed.json
    node tests/e2e_plan_confirm_flow.js http://localhost:3000 http://127.0.0.1:8000 tests/_plan_confirm_seed.json

随后用**同一个** EDU_DATA_DIR 启动后端（本脚本会把数据写进 teaching/learning 库）。

注意：
- 只写隔离目录；未设置 EDU_DATA_DIR 或指向正式 data/ 时**直接拒绝执行**（退出码 2）。
- 不涉及任何密钥；不调用大模型。
"""

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests._isolation_guard import require_isolated  # noqa: E402

require_isolated("tests/seed_plan_proposal.py",
                 "写入验收用的账号/课程/班级/任务/计划调整建议")

os.chdir(ROOT)

TEACHER = "e2e_confirm_teacher"
ADMIN = "e2e_confirm_admin"
STUDENT = "e2e_confirm_student"
PWD = "e2e-confirm-pass-123"

# 教师/管理员身份走环境白名单（部署时的合法通道之一）；必须在导入服务前设置
os.environ["TEACHER_USERS"] = TEACHER
os.environ["ADMIN_USERS"] = ADMIN

from services import (  # noqa: E402
    auth_service, learning_service, plan_adjustment, teaching_service,
)


def ensure_user(username: str) -> dict:
    user = auth_service.get_user_by_username(username)
    if not user:
        user = auth_service.register_user(username, PWD)
    return user


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="seed.json 输出路径（UTF-8）")
    args = parser.parse_args()

    teacher = ensure_user(TEACHER)
    ensure_user(ADMIN)
    student = ensure_user(STUDENT)

    courses = teaching_service.list_teacher_courses(teacher["id"])
    course = courses[0] if courses else teaching_service.create_course(
        teacher["id"], "确认流程验收课程")
    course_id, subject_id = course["id"], course["subject_id"]

    classes = teaching_service.list_classes(teacher["id"], course_id)
    klass = classes[0] if classes else teaching_service.create_class(
        teacher["id"], course_id, name="确认流程验收班")

    try:
        teaching_service.join_class_by_code(student["id"], klass["join_code"])
    except Exception as e:
        print(f"（学生可能已在班：{e}）", file=sys.stderr)

    # 4 个**已逾期**任务：重排比例 100% → 判定为"较大调整"
    planned = (date.today() - timedelta(days=3)).isoformat()
    task_ids = []
    for i in range(4):
        tid = learning_service.create_teacher_task(
            student["id"], subject_id,
            title=f"验收任务{i + 1}",
            description="用于验证重大调整确认流程",
            task_type="QUIZ",
            knowledge_point="二叉树",
            planned_date=planned,
            estimated_minutes=30,
            source_id=f"e2e_confirm_asg_{i + 1}",
        )
        if tid:
            task_ids.append(tid)

    marker = f"E2E验收{time.strftime('%H%M%S')}"
    adj = plan_adjustment.propose_schedule(
        student["id"], subject_id, source="manual", reason=f"{marker}：逾期任务重排")
    if not adj.get("requires_confirmation"):
        print("未能构造「较大调整」，无法验收确认流程："
              f"{json.dumps(adj, ensure_ascii=False)[:400]}")
        return 3
    if adj.get("status") != "PROPOSED":
        print(f"建议状态异常：{adj.get('status')}")
        return 3

    before = {t["id"]: t.get("planned_date")
              for t in learning_service.list_tasks(student["id"], subject_id, None, None, limit=100)}

    payload = {
        "student_token": auth_service.create_session(student["id"])["token"],
        "teacher_token": auth_service.create_session(teacher["id"])["token"],
        "student_id": student["id"],
        "teacher_id": teacher["id"],
        "subject_id": subject_id,
        "class_id": klass["id"],
        "adjustment_id": adj["id"],
        "marker": marker,
        "changes": adj["changes"],
        "planned_dates_before": before,
        "task_ids": task_ids,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False))
    print(f"已写入 {out}（调整建议 {adj['id']}，{len(adj['changes'])} 项变更，marker={marker}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())