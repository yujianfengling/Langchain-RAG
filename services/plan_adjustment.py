"""
学习计划调整（阶段二）

把"计划调整"做成真实功能，而不是让模型随口改日期。约束：

1. 遵守：用户指定的截止日期、每天可用时长、已完成任务、已有复习任务、教师固定截止
2. 时间预算、日期与任务冲突**由程序检查**（不交给模型保证）
3. 目标在预算内无法完成时 → 明确提示冲突 + 给出缩小范围等方案，
   **不生成看似合理但实际超时的计划**
4. 调整展示变更内容与原因，保留版本与撤销入口
5. 重大调整需学生确认后应用；只有显式开启自动调整时才在授权范围内自动执行
6. 相同事件不重复创建调整建议或任务（幂等）

数据落在 learning.db：
- plan_constraints：用户/教师给定的约束（截止日期、每日预算、自动调整授权、教师固定截止）
- plan_adjustments：调整版本（proposal/applied/undone）+ 变更明细 + 原因
"""

from config.path_config import get_data_dir

import json
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from utils.logger import logger

_DATA_DIR = str(get_data_dir())
DB_PATH = os.path.join(_DATA_DIR, "learning.db")

# 每天可用时长（分钟）的默认与边界
DEFAULT_DAILY_MINUTES = 30
MIN_DAILY_MINUTES = 10
MAX_DAILY_MINUTES = 600

# 调整状态
ADJ_PROPOSED = "PROPOSED"
ADJ_APPLIED = "APPLIED"
ADJ_UNDONE = "UNDONE"
ADJ_REJECTED = "REJECTED"

# 变更类型
CHANGE_RESCHEDULE = "reschedule"
CHANGE_REDUCE_SCOPE = "reduce_scope"
CHANGE_DEFER = "defer"

# 视为"重大调整"的阈值（需要学生确认才应用）
MAJOR_DEFER_DAYS = 2
MAJOR_CHANGE_RATIO = 0.3

_CONSTRAINTS_DDL = """
CREATE TABLE IF NOT EXISTS plan_constraints (
    user_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    deadline TEXT,
    daily_minutes INTEGER,
    teacher_deadline TEXT,
    auto_adjust INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, subject_id)
)
"""

_ADJUSTMENTS_DDL = """
CREATE TABLE IF NOT EXISTS plan_adjustments (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    plan_id TEXT,
    source TEXT NOT NULL,
    reason TEXT,
    changes_json TEXT DEFAULT '[]',
    summary TEXT,
    status TEXT NOT NULL DEFAULT 'PROPOSED',
    major INTEGER DEFAULT 0,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    applied_at TEXT,
    undone_at TEXT,
    constraints_json TEXT
)
"""


class PlanAdjustmentError(Exception):
    def __init__(self, message: str, error_type: str = "unknown"):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


def _get_db() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(_CONSTRAINTS_DDL)
    conn.execute(_ADJUSTMENTS_DDL)
    # 迁移：为旧库补 constraints_json（记录方案基于哪一版约束，确认时用于防旧方案）
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(plan_adjustments)").fetchall()}
        if "constraints_json" not in cols:
            conn.execute("ALTER TABLE plan_adjustments ADD COLUMN constraints_json TEXT")
    except sqlite3.Error:
        pass
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


# ==================== 约束 ====================

def set_constraints(user_id: str, subject_id: str,
                    deadline: Optional[str] = None,
                    daily_minutes: Optional[int] = None,
                    teacher_deadline: Optional[str] = None,
                    auto_adjust: Optional[bool] = None) -> Dict[str, Any]:
    """
    设置/更新计划约束。

    - deadline：学生自己的截止日期（"我周五要考这一章"）
    - daily_minutes：每天可用时长（分钟）
    - teacher_deadline：教师/课程固定截止时间（不可被学生改晚）
    - auto_adjust：是否授权系统在授权范围内自动调整
    """
    for label, value in (("学生截止日期", deadline), ("教师截止时间", teacher_deadline)):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise PlanAdjustmentError(f"{label}格式应为 YYYY-MM-DD", "invalid_request")
    if daily_minutes is not None:
        if not (MIN_DAILY_MINUTES <= int(daily_minutes) <= MAX_DAILY_MINUTES):
            raise PlanAdjustmentError(
                f"每天可用时长应在 {MIN_DAILY_MINUTES}-{MAX_DAILY_MINUTES} 分钟之间",
                "invalid_request")

    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM plan_constraints WHERE user_id=? AND subject_id=?",
                           (user_id, subject_id)).fetchone()
        current = dict(row) if row else {}
        # 教师固定截止不能被改晚（只能更早或保持）
        t_deadline = teacher_deadline or current.get("teacher_deadline")
        if current.get("teacher_deadline") and teacher_deadline:
            if teacher_deadline > current["teacher_deadline"]:
                raise PlanAdjustmentError("教师固定截止时间不能被推后", "invalid_request")
        conn.execute(
            """INSERT INTO plan_constraints
               (user_id, subject_id, deadline, daily_minutes, teacher_deadline,
                auto_adjust, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(user_id, subject_id) DO UPDATE SET
                 deadline=excluded.deadline,
                 daily_minutes=excluded.daily_minutes,
                 teacher_deadline=excluded.teacher_deadline,
                 auto_adjust=excluded.auto_adjust,
                 updated_at=excluded.updated_at""",
            (user_id, subject_id,
             deadline if deadline is not None else current.get("deadline"),
             int(daily_minutes) if daily_minutes is not None
             else current.get("daily_minutes") or DEFAULT_DAILY_MINUTES,
             t_deadline,
             1 if (auto_adjust if auto_adjust is not None
                   else current.get("auto_adjust", 0)) else 0,
             _now()))
        conn.commit()
        return get_constraints(user_id, subject_id)
    finally:
        conn.close()


def get_constraints(user_id: str, subject_id: str) -> Dict[str, Any]:
    """读取约束（未设置时返回默认值，不写入）"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM plan_constraints WHERE user_id=? AND subject_id=?",
                          (user_id, subject_id)).fetchone()
    finally:
        conn.close()
    if not row:
        return {"user_id": user_id, "subject_id": subject_id, "deadline": None,
                "daily_minutes": DEFAULT_DAILY_MINUTES, "teacher_deadline": None,
                "auto_adjust": 0, "configured": False}
    data = dict(row)
    data["auto_adjust"] = bool(data.get("auto_adjust"))
    data["configured"] = True
    # 生效截止：教师固定截止优先（更严格者生效）
    effective = None
    for d in (data.get("deadline"), data.get("teacher_deadline")):
        if d and (effective is None or d < effective):
            effective = d
    data["effective_deadline"] = effective
    return data


# ==================== 预算与冲突检查 ====================

def analyze_plan_budget(user_id: str, subject_id: str,
                        plan_id: Optional[str] = None) -> Dict[str, Any]:
    """
    检查当前计划的时间预算与截止约束（**程序判定，不依赖模型**）。

    Returns:
        {
          "daily_minutes", "deadline", "days_left",
          "remaining_tasks", "remaining_minutes", "capacity_minutes",
          "fits": bool, "conflicts": [...], "suggestions": [...]
        }
    """
    from services import learning_service

    constraints = get_constraints(user_id, subject_id)
    daily = constraints.get("daily_minutes") or DEFAULT_DAILY_MINUTES
    deadline = constraints.get("effective_deadline")
    today = _today()

    tasks = learning_service.list_tasks(user_id, subject_id, None, plan_id, limit=1000)
    remaining = [t for t in tasks if t.get("status") != "COMPLETED"]
    remaining_minutes = sum(int(t.get("estimated_minutes") or 0) for t in remaining)

    conflicts: List[str] = []
    suggestions: List[str] = []

    # 1) 截止日期约束
    days_left = None
    if deadline:
        try:
            days_left = (date.fromisoformat(deadline) - date.fromisoformat(today)).days
        except ValueError:
            days_left = None
        if days_left is not None and days_left < 0:
            conflicts.append(f"截止日期 {deadline} 已过（{abs(days_left)} 天前）")
            suggestions.append("请更新截止日期，或明确缩小复习范围")

    # 2) 时间预算
    capacity = None
    if days_left is not None and days_left >= 0:
        # 含今天：days_left 天 + 今天
        usable_days = days_left + 1
        capacity = usable_days * daily
        if remaining_minutes > capacity:
            conflicts.append(
                f"剩余任务预计需要 {remaining_minutes} 分钟，但截止前可用时间只有 "
                f"{capacity} 分钟（{usable_days} 天 × {daily} 分钟/天），"
                f"缺 {remaining_minutes - capacity} 分钟")
            suggestions.append(_reduce_scope_suggestion(remaining, remaining_minutes - capacity))

    # 3) 逾期任务
    overdue = [t for t in remaining
               if t.get("planned_date") and t["planned_date"] < today]
    if overdue:
        conflicts.append(f"有 {len(overdue)} 个任务已逾期（计划日期早于今天）")
        suggestions.append("可将逾期任务重排到最近的可用时间，或合并同知识点的复习任务")

    # 4) 单日超载
    by_date: Dict[str, int] = {}
    for t in remaining:
        d = t.get("planned_date") or "未排期"
        by_date[d] = by_date.get(d, 0) + int(t.get("estimated_minutes") or 0)
    overloaded = {d: m for d, m in by_date.items() if d != "未排期" and m > daily}
    for d, m in sorted(overloaded.items()):
        conflicts.append(f"{d} 安排 {m} 分钟，超过每天可用时长 {daily} 分钟")
    if overloaded:
        suggestions.append("把超出部分顺延到后续日期，或拆分长任务")

    return {
        "daily_minutes": daily,
        "deadline": deadline,
        "teacher_deadline": constraints.get("teacher_deadline"),
        "days_left": days_left,
        "remaining_tasks": len(remaining),
        "remaining_minutes": remaining_minutes,
        "capacity_minutes": capacity,
        "fits": not conflicts,
        "conflicts": conflicts,
        "suggestions": suggestions,
        "overdue_tasks": len(overdue),
    }


def fit_plan_to_budget(user_id: str, subject_id: str,
                       plan_id: Optional[str] = None) -> Dict[str, Any]:
    """
    让计划在**生效前**符合每日预算与截止约束（2026-10-05）。

    规则：
    - 只重排**可调整**任务（非教师固定、未完成）；把超出每日预算的部分顺延到
      后续仍有容量的日期（不超过截止日）；
    - **教师固定任务**（source='teacher_assignment'）保留原日期，绝不擅自延期；
    - 重排后仍无法满足（总量超过截止前容量，或单任务超过单日预算）→ fits=False，
      返回明确冲突，交由"待确认方案"处理，而不是当作已完成。

    Returns:
        {"fits", "daily_minutes", "deadline", "moved": [...], "kept_fixed",
         "conflicts": [...], "fixed_minutes", "movable_minutes"}
    """
    from services import learning_service

    constraints = get_constraints(user_id, subject_id)
    daily = constraints.get("daily_minutes") or DEFAULT_DAILY_MINUTES
    deadline = constraints.get("effective_deadline")
    today = _today()
    today_d = date.fromisoformat(today)

    tasks = learning_service.list_tasks(user_id, subject_id, None, plan_id, limit=1000)
    remaining = [t for t in tasks if t.get("status") != "COMPLETED"]
    fixed = [t for t in remaining if t.get("source") == "teacher_assignment"]
    movable = [t for t in remaining if t.get("source") != "teacher_assignment"]
    fixed_minutes = sum(int(t.get("estimated_minutes") or 0) for t in fixed)
    movable_minutes = sum(int(t.get("estimated_minutes") or 0) for t in movable)

    conflicts: List[str] = []
    moved: List[Dict[str, Any]] = []

    if deadline:
        try:
            last = date.fromisoformat(deadline)
        except ValueError:
            last = None
    else:
        last = None
    if last is None:
        dates = [t.get("planned_date") for t in remaining if t.get("planned_date")]
        last = (date.fromisoformat(max(dates)) if dates
                else today_d + timedelta(days=30))
    if last < today_d:
        return {"fits": False, "daily_minutes": daily, "deadline": last.isoformat(),
                "moved": [], "kept_fixed": len(fixed), "conflicts": [f"截止日期 {last.isoformat()} 已过"],
                "fixed_minutes": fixed_minutes, "movable_minutes": movable_minutes}

    # 固定任务先占用各自日期
    used: Dict[str, int] = {}
    for t in fixed:
        d = t.get("planned_date") or today
        used[d] = used.get(d, 0) + int(t.get("estimated_minutes") or 0)

    # 可调整任务：优先原日期，放不下则顺延到最近的可用日期
    for t in sorted(movable, key=lambda x: (x.get("planned_date") or today,
                                            x.get("priority") or 3)):
        need = int(t.get("estimated_minutes") or 0)
        orig = t.get("planned_date")
        if need > daily:
            conflicts.append(f"「{t.get('title') or t.get('knowledge_point')}」预计 {need} 分钟，"
                             f"超过每天可用 {daily} 分钟（请拆分或缩减）")
        start = today_d
        if orig:
            try:
                od = date.fromisoformat(orig)
                start = od if od > today_d else today_d
            except ValueError:
                start = today_d
        day = start
        placed = None
        while day <= last:
            key = day.isoformat()
            if used.get(key, 0) + need <= daily:
                placed = key
                break
            day += timedelta(days=1)
        if placed is None:
            conflicts.append(f"「{t.get('title') or t.get('knowledge_point')}」"
                             f"（{need} 分钟）在 {last.isoformat()} 前放不下")
            continue
        used[placed] = used.get(placed, 0) + need
        if placed != orig:
            try:
                learning_service.reschedule_task(user_id, t["id"], placed,
                                                 reason="按每日预算自动重排")
                moved.append({"task_id": t["id"], "from": orig, "to": placed})
            except Exception as e:
                logger.debug(f"自动重排失败（保留原日期）: {e}")

    # 总量与截止容量
    days = (last - today_d).days + 1
    capacity = days * daily
    total = fixed_minutes + movable_minutes
    if total > capacity:
        conflicts.append(f"剩余任务共需 {total} 分钟，截止前可用仅 {capacity} 分钟"
                         f"（{days} 天 × {daily} 分钟/天），缺 {total - capacity} 分钟")

    suggestions: List[str] = []
    if conflicts:
        suggestions.append("可按每天可用时长，把超出部分顺延到后续仍有容量的日期")
        if fixed_minutes:
            suggestions.append(f"其中 {fixed_minutes} 分钟来自教师固定任务（保留原日期），"
                               "可调整的空间在可自主安排的任务上")
        if total > capacity:
            suggestions.append("若截止前容量确实不足，请缩小范围或延长截止日期后再确认")
    return {"fits": not conflicts, "daily_minutes": daily, "deadline": last.isoformat(),
            "moved": moved, "kept_fixed": len(fixed), "conflicts": conflicts,
            "suggestions": suggestions,
            "fixed_minutes": fixed_minutes, "movable_minutes": movable_minutes,
            "capacity_minutes": capacity, "total_minutes": total}


def _reduce_scope_suggestion(tasks: List[Dict[str, Any]], missing_minutes: int) -> str:
    """预算不足时给出"缩小范围"的具体建议（按优先级/预计时长挑可削减任务）"""
    ranked = sorted(tasks, key=lambda t: (
        t.get("priority") or 3, int(t.get("estimated_minutes") or 0)))
    picked, acc = [], 0
    for t in ranked:
        # 优先削减"练习/阅读"这类可延后的任务，保留自测与复习
        if t.get("task_type") in ("QUIZ", "REVIEW"):
            continue
        picked.append(t.get("knowledge_point") or t.get("title"))
        acc += int(t.get("estimated_minutes") or 0)
        if acc >= missing_minutes:
            break
    names = "、".join([p for p in picked if p][:5]) or "部分练习任务"
    return (f"建议缩小范围：优先完成自测与复习，把「{names}」推迟到考试后"
            f"（可省下约 {acc} 分钟）")


def _plan_tasks_by_date(user_id: str, subject_id: str,
                        plan_id: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    from services import learning_service
    tasks = learning_service.list_tasks(user_id, subject_id, None, plan_id, limit=1000)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for t in tasks:
        if t.get("status") == "COMPLETED":
            continue
        out.setdefault(t.get("planned_date") or "", []).append(t)
    return out


# ==================== 调整建议 ====================

def propose_schedule(user_id: str, subject_id: str,
                     plan_id: Optional[str] = None,
                     source: str = "auto",
                     idempotency_key: Optional[str] = None,
                     reason: Optional[str] = None) -> Dict[str, Any]:
    """
    生成一份"按预算重排"的调整方案（**只生成方案，不直接改数据**）。

    策略：
    1. 逾期任务：排到今天开始
    2. 其余未完成任务：按原计划日期顺序，填入每日剩余容量
    3. 同知识点的复习任务保持先到期优先（不把复习挤到最后）
    4. 超出截止的部分 → 不排期，明确列为"超出预算、建议缩减"

    幂等：同 idempotency_key 只生成一次（不重复创建建议）。
    """
    from services import learning_service

    if idempotency_key:
        existing = _find_by_key(user_id, idempotency_key)
        if existing:
            existing["duplicate"] = True
            return existing

    constraints = get_constraints(user_id, subject_id)
    daily = constraints.get("daily_minutes") or DEFAULT_DAILY_MINUTES
    deadline = constraints.get("effective_deadline")
    today = date.fromisoformat(_today())

    pending = [t for t in learning_service.list_tasks(user_id, subject_id, None, plan_id,
                                                      limit=1000)
               if t.get("status") != "COMPLETED"]
    if not pending:
        return _save_adjustment(
            user_id, subject_id, plan_id, source,
            reason or "没有未完成任务，无需调整", [], idempotency_key)

    # 排序：逾期/复习优先，其次原计划日期，再次优先级
    def sort_key(t):
        planned = t.get("planned_date") or "9999-12-31"
        overdue = 0 if (t.get("planned_date") and t["planned_date"] < _today()) else 1
        review = 0 if t.get("task_type") == "REVIEW" or t.get("source") == "auto_review" else 1
        return (overdue, review, planned, t.get("priority") or 3)

    ordered = sorted(pending, key=sort_key)

    # 逐日填容量
    day = today
    used: Dict[str, int] = {}
    changes: List[Dict[str, Any]] = []
    overflow: List[Dict[str, Any]] = []

    for t in ordered:
        minutes = int(t.get("estimated_minutes") or 30)
        placed = None
        original = t.get("planned_date")
        # 起点 = max(今天, 原计划日期)：既不排到今天之前，也**不提前**任务；
        # 关键修复（2026-10-05）：原实现把"提前"的任务又计一次原日期容量，
        # 会把任务留在原日期却不再校验容量 → 方案本身超预算（实测 65 分钟/天）。
        start = day
        if original:
            try:
                od = date.fromisoformat(original)
                if od > start:
                    start = od
            except ValueError:
                pass
        cursor = start
        guard = 0
        while guard < 400:
            d = cursor.isoformat()
            if deadline and d > deadline:
                break
            load = used.get(d, 0)
            if load + minutes <= daily:
                placed = d
                used[d] = load + minutes
                break
            cursor = cursor + timedelta(days=1)
            guard += 1

        if placed is None:
            overflow.append(t)
            continue

        if placed != original:
            changes.append({
                "task_id": t["id"],
                "title": t.get("title"),
                "knowledge_point": t.get("knowledge_point"),
                "task_type": t.get("task_type"),
                "change_type": CHANGE_RESCHEDULE,
                "from_date": t.get("planned_date"),
                "to_date": placed,
                "minutes": minutes,
            })

    if overflow:
        saved = sum(int(t.get("estimated_minutes") or 0) for t in overflow)
        changes.append({
            "change_type": CHANGE_REDUCE_SCOPE,
            "from_date": None,
            "to_date": None,
            "task_ids": [t["id"] for t in overflow],
            "titles": [t.get("title") for t in overflow],
            "minutes": saved,
            "note": (f"以下 {len(overflow)} 个任务在截止日期前没有足够时间完成，"
                     f"建议缩减范围或延长截止："
                     + "、".join([str(t.get('title')) for t in overflow[:5]])),
        })

    summary = _summarize(changes, daily, deadline)
    return _save_adjustment(user_id, subject_id, plan_id, source,
                            reason or summary, changes, idempotency_key,
                            constraints=constraints)


def _summarize(changes: List[Dict[str, Any]], daily: int,
               deadline: Optional[str]) -> str:
    moved = [c for c in changes if c["change_type"] == CHANGE_RESCHEDULE]
    reduced = [c for c in changes if c["change_type"] == CHANGE_REDUCE_SCOPE]
    parts = []
    if moved:
        parts.append(f"重排 {len(moved)} 个任务的日期（每天不超过 {daily} 分钟"
                     + (f"，截止 {deadline}" if deadline else "") + "）")
    if reduced:
        parts.append(f"{len(reduced)} 项超出预算，建议缩减范围")
    return "；".join(parts) or "无需调整"


def _is_major(changes: List[Dict[str, Any]], total_pending: int) -> bool:
    """重大调整判定：涉及比例高，或推迟幅度大"""
    moved = [c for c in changes if c["change_type"] == CHANGE_RESCHEDULE]
    if total_pending and len(moved) / max(1, total_pending) >= MAJOR_CHANGE_RATIO:
        return True
    for c in moved:
        try:
            if not c.get("from_date"):
                continue
            delta = (date.fromisoformat(c["to_date"]) - date.fromisoformat(c["from_date"])).days
            if delta >= MAJOR_DEFER_DAYS:
                return True
        except (ValueError, TypeError):
            continue
    return any(c["change_type"] == CHANGE_REDUCE_SCOPE for c in changes)


def _save_adjustment(user_id: str, subject_id: str, plan_id: Optional[str],
                     source: str, reason: str, changes: List[Dict[str, Any]],
                     idempotency_key: Optional[str],
                     constraints: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from services import learning_service
    pending = [t for t in learning_service.list_tasks(user_id, subject_id, None, plan_id,
                                                      limit=1000)
               if t.get("status") != "COMPLETED"]
    major = _is_major(changes, len(pending))

    aid = f"adj_{uuid.uuid4().hex[:10]}"
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO plan_adjustments
               (id, user_id, subject_id, plan_id, source, reason, changes_json,
                summary, status, major, idempotency_key, created_at, constraints_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (aid, user_id, subject_id, plan_id, source, reason,
             json.dumps(changes, ensure_ascii=False), reason,
             ADJ_PROPOSED, 1 if major else 0, idempotency_key, _now(),
             json.dumps(constraints or {}, ensure_ascii=False)))
        conn.commit()
    except sqlite3.IntegrityError:
        # 并发下同 key 已存在 → 返回既有建议
        conn.close()
        existing = _find_by_key(user_id, idempotency_key) if idempotency_key else None
        if existing:
            existing["duplicate"] = True
            return existing
        raise PlanAdjustmentError("调整建议已存在", "conflict")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    logger.info(f"生成计划调整建议: id={aid}, changes={len(changes)}, major={major}")
    return {"id": aid, "user_id": user_id, "subject_id": subject_id, "plan_id": plan_id,
            "source": source, "reason": reason, "changes": changes, "summary": reason,
            "status": ADJ_PROPOSED, "major": major, "created_at": _now(),
            "requires_confirmation": major}


def _find_by_key(user_id: str, key: str) -> Optional[Dict[str, Any]]:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM plan_adjustments WHERE user_id=? AND idempotency_key=?",
            (user_id, key)).fetchone()
        return _row_to_adjustment(row) if row else None
    finally:
        conn.close()


def _row_to_adjustment(row) -> Dict[str, Any]:
    try:
        changes = json.loads(row["changes_json"] or "[]")
    except (ValueError, TypeError):
        changes = []
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "subject_id": row["subject_id"],
        "plan_id": row["plan_id"],
        "source": row["source"],
        "reason": row["reason"],
        "changes": changes,
        "summary": row["summary"],
        "status": row["status"],
        "major": bool(row["major"]),
        "requires_confirmation": bool(row["major"]) and row["status"] == ADJ_PROPOSED,
        "created_at": row["created_at"],
        "applied_at": row["applied_at"],
        "undone_at": row["undone_at"],
        "constraints": (json.loads(row["constraints_json"])
                        if ("constraints_json" in row.keys() and row["constraints_json"]) else {}),
    }


def list_adjustments(user_id: str, subject_id: Optional[str] = None,
                     status: Optional[str] = None,
                     limit: int = 50) -> List[Dict[str, Any]]:
    """查看调整历史（含未应用的建议；前端据此提供"确认/撤销"入口）"""
    conn = _get_db()
    try:
        q = "SELECT * FROM plan_adjustments WHERE user_id=?"
        p: List[Any] = [user_id]
        if subject_id:
            q += " AND subject_id=?"
            p.append(subject_id)
        if status:
            q += " AND status=?"
            p.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"
        p.append(limit)
        return [_row_to_adjustment(r) for r in conn.execute(q, p).fetchall()]
    finally:
        conn.close()


def _get_adjustment(adjustment_id: str, user_id: str) -> Dict[str, Any]:
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM plan_adjustments WHERE id=?",
                           (adjustment_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise PlanAdjustmentError("调整记录不存在", "not_found")
    data = _row_to_adjustment(row)
    if data["user_id"] != user_id:
        raise PlanAdjustmentError("无权操作他人的调整记录", "forbidden")
    return data


def _validate_changes_against_constraints(user_id: str, subject_id: str,
                                           plan_id: Optional[str],
                                           changes: List[Dict[str, Any]]) -> Optional[str]:
    """
    确认时用**当前**约束复核方案是否仍然可行（2026-10-05）。

    约束可能在"提出"之后被改（每日时长/截止），此时不应把旧方案直接应用。
    Returns: 不可行的原因；None 表示可行。
    """
    from services import learning_service
    constraints = get_constraints(user_id, subject_id)
    daily = constraints.get("daily_minutes") or DEFAULT_DAILY_MINUTES
    deadline = constraints.get("effective_deadline")
    tasks = learning_service.list_tasks(user_id, subject_id, None, plan_id, limit=1000)
    new_date = {t["id"]: t.get("planned_date") for t in tasks if t.get("status") != "COMPLETED"}
    for c in changes or []:
        if c.get("change_type") == CHANGE_RESCHEDULE and c.get("task_id"):
            new_date[c["task_id"]] = c.get("to_date") or c.get("from_date")
    by_day: Dict[str, int] = {}
    for t in tasks:
        if t.get("status") == "COMPLETED":
            continue
        d = new_date.get(t["id"]) or t.get("planned_date")
        if not d:
            continue
        if deadline and d > deadline:
            return f"调整后有任务排到 {d}，超过当前截止 {deadline}"
        by_day[d] = by_day.get(d, 0) + int(t.get("estimated_minutes") or 0)
    over = {d: m for d, m in by_day.items() if m > daily}
    if over:
        d, m = sorted(over.items())[0]
        return f"按当前约束（每天 {daily} 分钟），{d} 仍有 {m} 分钟"
    return None


def apply_adjustment(adjustment_id: str, user_id: str,
                     confirm: bool = False) -> Dict[str, Any]:
    """
    应用调整方案（真正修改任务日期）。

    - 重大调整必须 confirm=True（学生确认）才应用
    - 学生的显式改期不会被系统覆盖：只修改"仍在原定日期上"的任务
    - 幂等：已应用/已撤销的不能再应用
    """
    data = _get_adjustment(adjustment_id, user_id)
    if data["status"] == ADJ_APPLIED:
        return {**data, "already_applied": True}
    if data["status"] in (ADJ_UNDONE, ADJ_REJECTED):
        raise PlanAdjustmentError("该调整已撤销/已拒绝，不能再次应用", "invalid_request")
    if data["major"] and not confirm:
        raise PlanAdjustmentError(
            "这是较大范围的调整，需要学生确认后才能应用", "confirmation_required")

    # 确认时核对当前约束与"提出方案时"的快照：约束变了 → 不应用旧方案
    snapshot = data.get("constraints") or {}
    current = get_constraints(user_id, data["subject_id"])
    if snapshot and (snapshot.get("daily_minutes") != current.get("daily_minutes")
                     or snapshot.get("effective_deadline") != current.get("effective_deadline")):
        raise PlanAdjustmentError(
            "约束已变化（每日时长或截止日期），该方案基于旧约束；"
            "请重新生成调整建议后再确认。", "stale_proposal")

    from services import learning_service

    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for c in data["changes"]:
        if c["change_type"] != CHANGE_RESCHEDULE:
            continue
        task = learning_service.get_task(c["task_id"])
        if not task or task.get("user_id") != user_id:
            skipped.append({**c, "skip_reason": "任务不存在或不属于当前用户"})
            continue
        if task.get("status") == "COMPLETED":
            skipped.append({**c, "skip_reason": "任务已完成，日期不再调整"})
            continue
        if task.get("planned_date") != c.get("from_date"):
            # 学生（或教师）已手动改过 → 尊重用户设置，不覆盖
            skipped.append({**c, "skip_reason": "任务日期已被手动修改，保留用户设置"})
            continue
        learning_service.reschedule_task(
            user_id, c["task_id"], c["to_date"], reason=f"应用调整建议（{data['summary']}）")
        applied.append(c)

    conn = _get_db()
    try:
        conn.execute(
            "UPDATE plan_adjustments SET status=?, applied_at=? WHERE id=?",
            (ADJ_APPLIED, _now(), adjustment_id))
        conn.commit()
    finally:
        conn.close()

    # 应用后按当前约束复核剩余冲突：仍有冲突 → 计划保持草案（不作为生效任务进首页）
    from services import learning_service as _ls
    remaining = _validate_changes_against_constraints(
        user_id, data["subject_id"], data.get("plan_id"), [])
    if data.get("plan_id"):
        try:
            _ls._set_plan_status(data["plan_id"], "DRAFT" if remaining else "ACTIVE")
        except Exception as e:
            logger.debug(f"更新计划状态失败: {e}")
    if remaining:
        logger.info(f"调整已部分应用但仍不满足约束: plan={data.get('plan_id')} {remaining}")

    logger.info(f"应用计划调整: id={adjustment_id}, applied={len(applied)}, "
                f"skipped={len(skipped)}")
    return {**_get_adjustment(adjustment_id, user_id),
            "applied_changes": applied, "skipped_changes": skipped,
            "remaining_conflicts": [remaining] if remaining else []}


def undo_adjustment(adjustment_id: str, user_id: str) -> Dict[str, Any]:
    """
    撤销已应用的调整：把任务日期改回调整前的值（保留调整记录，状态置 UNDONE）。
    """
    data = _get_adjustment(adjustment_id, user_id)
    if data["status"] != ADJ_APPLIED:
        raise PlanAdjustmentError("只有已应用的调整可以撤销", "invalid_request")

    from services import learning_service

    reverted: List[Dict[str, Any]] = []
    for c in data["changes"]:
        if c["change_type"] != CHANGE_RESCHEDULE or not c.get("from_date"):
            continue
        task = learning_service.get_task(c["task_id"])
        if not task or task.get("user_id") != user_id:
            continue
        if task.get("status") == "COMPLETED":
            continue
        learning_service.reschedule_task(
            user_id, c["task_id"], c["from_date"],
            reason=f"撤销调整建议（{data['summary']}）")
        reverted.append(c)

    conn = _get_db()
    try:
        conn.execute(
            "UPDATE plan_adjustments SET status=?, undone_at=? WHERE id=?",
            (ADJ_UNDONE, _now(), adjustment_id))
        conn.commit()
    finally:
        conn.close()

    logger.info(f"撤销计划调整: id={adjustment_id}, reverted={len(reverted)}")
    return {**_get_adjustment(adjustment_id, user_id), "reverted_changes": reverted}


def reject_adjustment(adjustment_id: str, user_id: str) -> Dict[str, Any]:
    """拒绝一条调整建议（不改数据）"""
    data = _get_adjustment(adjustment_id, user_id)
    if data["status"] != ADJ_PROPOSED:
        raise PlanAdjustmentError("只有待确认的建议可以拒绝", "invalid_request")
    conn = _get_db()
    try:
        conn.execute("UPDATE plan_adjustments SET status=? WHERE id=?",
                     (ADJ_REJECTED, adjustment_id))
        conn.commit()
    finally:
        conn.close()
    return _get_adjustment(adjustment_id, user_id)


def pending_adjustments(user_id: str, subject_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """待学生确认的调整建议"""
    return list_adjustments(user_id, subject_id, ADJ_PROPOSED)


__all__ = [
    "PlanAdjustmentError",
    "DEFAULT_DAILY_MINUTES",
    "MIN_DAILY_MINUTES",
    "MAX_DAILY_MINUTES",
    "ADJ_PROPOSED",
    "ADJ_APPLIED",
    "ADJ_UNDONE",
    "ADJ_REJECTED",
    "set_constraints",
    "get_constraints",
    "analyze_plan_budget",
    "propose_schedule",
    "list_adjustments",
    "pending_adjustments",
    "apply_adjustment",
    "undo_adjustment",
    "reject_adjustment",
]
