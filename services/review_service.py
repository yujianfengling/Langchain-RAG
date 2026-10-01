"""
错题复习联动（阶段一）

规则集中在本模块顶部 REVIEW_RULES，便于解释与调整。

核心约束：
- 同一用户 + 学科 + 知识点，只保留一条"待办"复习任务（数据库唯一索引保证）
- 再次答错同一知识点时**合并**到既有待办任务（累加 merged_count、刷新原因），
  不新增重复任务
- 答对后关闭待办复习任务，但保留历史（不删除错误过程）
- 自动生成的复习任务会同步创建一条学习任务（source='auto_review'），
  并记录原因，供计划页展示"为什么多了一条复习"
"""

from datetime import date, timedelta
from typing import Dict, Any, List, Optional

from utils.logger import logger
from services.learning_data import (
    _get_db,
    _now,
    _new_id,
    REVIEW_PENDING,
    REVIEW_DONE,
    REVIEW_CANCELLED,
)

# ============ 复习规则（集中配置） ============
REVIEW_RULES = {
    # 答错后首次复习安排在几天后
    "first_interval_days": 1,
    # 复习仍答错时的最短间隔（避免当天反复堆积）
    "min_interval_days": 1,
    # 是否为自动复习同步创建学习任务（计划页可见）
    "create_plan_task": True,
    # 每个知识点最多保留的待办复习任务数（合并策略下恒为 1）
    "max_pending_per_point": 1,
    # ---- 阶段一补验：复习通过后的延后复测（间隔复现） ----
    # 复习答对可关闭当前待办，但"一次答对不等于长期掌握"：
    # 未巩固的知识点会安排一次延后复测（更长的间隔）
    "recheck_interval_days": 3,
    # 延后复测最多安排次数（避免无限循环）
    "recheck_max_times": 2,
    # 已巩固（间隔复现通过）的知识点不再自动安排复测
    "skip_recheck_when_consolidated": True,
    # 哪些作答性质可以关闭待办复习（独立作答 / 复习重做；
    # 看过答案后作答 after_reveal 不算）
    "closing_kinds": ("independent", "review"),
}


class ReviewServiceError(Exception):
    def __init__(self, message: str, error_type: str = "unknown"):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


def ensure_review_task(
    user_id: str,
    subject_id: str,
    knowledge_point_id: str,
    knowledge_point_name: str,
    source_attempt_id: Optional[str] = None,
    source_task_id: Optional[str] = None,
    reason: Optional[str] = None,
    interval_days: Optional[int] = None,
    recheck: bool = False,
) -> Dict[str, Any]:
    """
    创建或复用一个待办复习任务（同知识点合并，避免重复堆积）。

    幂等（阶段一补验）：以 source_attempt_id 作为步骤级幂等键。
    同一作答重复投递（事件重试 / 断网重发）时不会再次累加 merged_count，
    也不会重复创建计划任务。

    Args:
        interval_days: 到期间隔天数（默认取 REVIEW_RULES.first_interval_days；
                       延后复测用 recheck_interval_days）
        recheck: 是否为"延后复测"任务（用于 reason 与计数）

    Returns:
        {"task_id", "created": bool, "merged": bool, "due_date", "reason",
         "merged_count", "already_processed"(可选)}
    """
    if not user_id or not subject_id or not knowledge_point_id:
        raise ReviewServiceError("缺少必要参数", "invalid_request")

    days = interval_days if interval_days is not None else REVIEW_RULES["first_interval_days"]
    due = (date.today() + timedelta(days=days)).isoformat()
    reason = reason or "最近一次作答错误，需要复习"
    conn = _get_db()
    try:
        # ---- 步骤级幂等：同一作答只联动一次 ----
        if source_attempt_id:
            from services.idempotency import begin_step
            if not begin_step(conn, "review.ensure_task",
                              f"{user_id}:{source_attempt_id}:{due}"):
                conn.rollback()
                existing = conn.execute(
                    """SELECT * FROM review_tasks
                       WHERE user_id=? AND subject_id=? AND knowledge_point_id=?""",
                    (user_id, subject_id, knowledge_point_id)).fetchone()
                return {"task_id": existing["id"] if existing else None,
                        "created": False, "merged": False, "already_processed": True,
                        "due_date": existing["due_date"] if existing else due,
                        "reason": existing["reason"] if existing else reason,
                        "merged_count": (existing["merged_count"] if existing else 0)}

        existing = conn.execute(
            """SELECT * FROM review_tasks
               WHERE user_id=? AND subject_id=? AND knowledge_point_id=? AND status=?""",
            (user_id, subject_id, knowledge_point_id, REVIEW_PENDING)).fetchone()
        if existing:
            merged = (existing["merged_count"] or 1) + 1
            # 合并：累加次数、刷新原因与来源；到期日取更早者（更快复习）
            new_due = min(existing["due_date"], due) if existing["due_date"] else due
            conn.execute(
                """UPDATE review_tasks
                   SET merged_count=?, reason=?, due_date=?, source_attempt_id=?, updated_at=?
                   WHERE id=?""",
                (merged, reason, new_due,
                 source_attempt_id or existing["source_attempt_id"], _now(), existing["id"]))
            conn.commit()
            logger.info(f"复用作答错题复习任务: user={user_id}, kp={knowledge_point_name}, "
                        f"merged={merged}")
            return {"task_id": existing["id"], "created": False, "merged": True,
                    "due_date": new_due, "reason": reason, "merged_count": merged}

        rid = _new_id("rvw")
        now = _now()
        conn.execute(
            """INSERT INTO review_tasks
               (id, user_id, subject_id, knowledge_point_id, knowledge_point_name,
                source_attempt_id, source_task_id, due_date, status, reason, merged_count,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)""",
            (rid, user_id, subject_id, knowledge_point_id, knowledge_point_name,
             source_attempt_id, source_task_id, due, REVIEW_PENDING, reason, now, now))
        conn.commit()
        logger.info(f"创建{'延后复测' if recheck else '错题复习'}任务: "
                    f"user={user_id}, kp={knowledge_point_name}, due={due}")
    except Exception as e:
        logger.error(f"创建复习任务失败: {e}", exc_info=True)
        raise ReviewServiceError("创建复习任务失败", "database_error")
    finally:
        conn.close()

    # 同步创建可执行的学习任务（计划页可见，携带来源原因）
    plan_task_id = None
    if REVIEW_RULES["create_plan_task"]:
        try:
            from services.learning_service import create_auto_review_task
            plan_task_id = create_auto_review_task(
                user_id=user_id,
                subject_id=subject_id,
                knowledge_point_id=knowledge_point_id,
                knowledge_point_name=knowledge_point_name,
                planned_date=due,
                reason=reason,
                source_attempt_id=source_attempt_id,
            )
            if plan_task_id:
                conn = _get_db()
                try:
                    conn.execute("UPDATE review_tasks SET source_task_id=? WHERE id=?",
                                 (plan_task_id, rid))
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            # 计划任务创建失败不影响复习任务本体（可重试）
            logger.warning(f"同步创建复习学习任务失败（复习任务仍已创建）: {e}")

    return {"task_id": rid, "created": True, "merged": False, "due_date": due,
            "reason": reason, "merged_count": 1, "plan_task_id": plan_task_id}


def complete_review_tasks(
    user_id: str,
    subject_id: str,
    knowledge_point_id: str,
    reason: str = "复习已通过",
    attempt_kind: Optional[str] = None,
    source_attempt_id: Optional[str] = None,
) -> int:
    """
    关闭该知识点的待办复习任务（历史保留，不删除错误过程）。

    阶段一补验：只有真实作答证据才能关闭待办：
    - `attempt_kind` 为 after_reveal（看过答案后作答）→ **不关闭**
      （否则"看答案 → 答对 → 复习完成"会形成虚假闭环）
    - `attempt_kind` 为 None（内部调用，如管理员/系统）→ 按原语义关闭

    幂等：以 source_attempt_id 记账，事件重试不会重复关闭/重复写日志。

    Returns:
        关闭的任务条数
    """
    if not user_id or not subject_id or not knowledge_point_id:
        return 0
    if attempt_kind is not None and attempt_kind not in REVIEW_RULES["closing_kinds"]:
        logger.info(f"作答性质 {attempt_kind} 不足以关闭复习任务，保持待办: "
                    f"user={user_id}, kp_id={knowledge_point_id}")
        return 0

    conn = _get_db()
    try:
        if source_attempt_id:
            from services.idempotency import begin_step
            if not begin_step(conn, "review.complete_tasks",
                              f"{user_id}:{knowledge_point_id}:{source_attempt_id}"):
                conn.rollback()
                return 0

        cur = conn.execute(
            """UPDATE review_tasks
               SET status=?, completed_at=?, reason=?, updated_at=?
               WHERE user_id=? AND subject_id=? AND knowledge_point_id=? AND status=?""",
            (REVIEW_DONE, _now(), reason, _now(),
             user_id, subject_id, knowledge_point_id, REVIEW_PENDING))
        conn.commit()
        if cur.rowcount:
            logger.info(f"关闭复习任务: user={user_id}, kp_id={knowledge_point_id}, n={cur.rowcount}")
        return cur.rowcount
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error(f"关闭复习任务失败: {e}", exc_info=True)
        raise ReviewServiceError("关闭复习任务失败", "database_error")
    finally:
        conn.close()


def schedule_recheck(
    user_id: str,
    subject_id: str,
    knowledge_point_id: str,
    knowledge_point_name: str,
    source_attempt_id: Optional[str] = None,
    consolidated: bool = False,
    recheck_times: int = 0,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    安排"延后复测"（阶段一补验）：

    复习答对可以关闭当前待办，但**一次答对不等于长期掌握**。
    对尚未通过间隔复现（consolidated=False）的知识点，按更长的间隔
    再安排一次复测，用间隔复现来验证长期记忆。

    幂等：以 source_attempt_id 记账；同一作答重复投递不会重复建任务。
    上限：recheck_max_times 次后不再自动安排（避免无限循环）。

    Returns:
        {"scheduled": bool, "reason": str, "due_date"?, "task_id"?}
    """
    if not user_id or not subject_id or not knowledge_point_id:
        return {"scheduled": False, "reason": "缺少必要参数"}
    if consolidated and REVIEW_RULES["skip_recheck_when_consolidated"]:
        return {"scheduled": False, "reason": "该知识点已通过间隔复现判定为巩固，不再自动复测"}
    if recheck_times >= REVIEW_RULES["recheck_max_times"]:
        return {"scheduled": False,
                "reason": f"延后复测已达上限（{REVIEW_RULES['recheck_max_times']} 次），"
                          f"后续由学生自行决定是否复习"}

    days = REVIEW_RULES["recheck_interval_days"]
    due = (date.today() + timedelta(days=days)).isoformat()
    text = reason or (f"延后复测：上次复习已通过但尚未间隔复现，"
                      f"{days} 天后再测一次以确认长期记忆")
    try:
        result = ensure_review_task(
            user_id=user_id, subject_id=subject_id,
            knowledge_point_id=knowledge_point_id, knowledge_point_name=knowledge_point_name,
            source_attempt_id=source_attempt_id, reason=text,
            interval_days=days, recheck=True,
        )
    except ReviewServiceError as e:
        logger.warning(f"安排延后复测失败（不影响复习关闭结果）: {e}")
        return {"scheduled": False, "reason": f"安排延后复测失败: {e.message}"}

    if result.get("already_processed"):
        return {"scheduled": False, "already_processed": True,
                "reason": "该作答已处理过，不重复安排复测"}
    return {"scheduled": True, "task_id": result.get("task_id"), "due_date": due,
            "reason": text, "interval_days": days}


def list_review_tasks(
    user_id: str,
    subject_id: Optional[str] = None,
    status: Optional[str] = None,
    due_before: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """查询复习任务（可按状态/到期日过滤）"""
    conn = _get_db()
    try:
        q = "SELECT * FROM review_tasks WHERE user_id=?"
        p: List[Any] = [user_id]
        if subject_id:
            q += " AND subject_id=?"
            p.append(subject_id)
        if status:
            q += " AND status=?"
            p.append(status)
        if due_before:
            q += " AND due_date<=?"
            p.append(due_before)
        q += " ORDER BY due_date ASC LIMIT ?"
        p.append(limit)
        return [dict(r) for r in conn.execute(q, p).fetchall()]
    finally:
        conn.close()


def get_pending_review_for_point(
    user_id: str, subject_id: str, knowledge_point_id: str
) -> Optional[Dict[str, Any]]:
    """查询某知识点当前是否已有待办复习"""
    rows = list_review_tasks(user_id, subject_id, REVIEW_PENDING, limit=500)
    for r in rows:
        if r["knowledge_point_id"] == knowledge_point_id:
            return r
    return None


__all__ = [
    "REVIEW_RULES",
    "ReviewServiceError",
    "ensure_review_task",
    "complete_review_tasks",
    "schedule_recheck",
    "list_review_tasks",
    "get_pending_review_for_point",
]
