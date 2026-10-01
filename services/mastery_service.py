"""
学习画像与掌握度（阶段一修正版）

严格区分三类信息：

1. 学习行为（behavior）：看过 / 读过 / 完成了任务
   → 只累加行为计数与最近时间，**不影响掌握度**
2. 作答证据（evidence）：是否独立答对、作答次数、何时复测
   → 只有独立作答 / 复习重做计入证据；"看答案后再答"不计入独立证据
3. 掌握估计（mastery）：依据证据计算的**暂时判断**
   → 证据不足时给出"待评估"，不把默认数值包装成精确掌握率

规则集中在本模块顶部配置，便于解释与调整。
"""

from config.path_config import get_data_dir
import json
import os
import sqlite3
from typing import Dict, Any, List, Optional

from utils.logger import logger
from services.learning_data import (
    _now,
    _new_id,
    ensure_knowledge_point,
    MASTERY_PENDING,
    MASTERY_ESTIMATED,
)

# knowledge_mastery 位于用户画像库（user_agent.db），与学习闭环库分离
# 数据根目录统一来自 config.path_config（支持 EDU_DATA_DIR 覆盖）
_AGENT_DB = os.path.join(str(get_data_dir()), "user_agent.db")


def _get_db() -> sqlite3.Connection:
    """
    获取用户画像库连接（knowledge_mastery 所在库）。

    连接后立即补齐阶段一新增列与步骤幂等台账，保证历史库/隔离测试库
    都能拿到同一结构（不依赖应用启动顺序）。
    """
    os.makedirs(os.path.dirname(_AGENT_DB), exist_ok=True)
    conn = sqlite3.connect(_AGENT_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        from services.learning_data import ensure_columns
        ensure_columns(conn, "knowledge_mastery")
        from services.idempotency import ensure_ledger
        ensure_ledger(conn)
        conn.commit()
    except Exception as e:  # 结构补齐失败不应让业务直接崩，交给后续 SQL 报错
        logger.warning(f"掌握度表结构补齐失败（继续尝试）: {e}")
    return conn

# ============ 掌握度规则（集中配置，可解释） ============
MASTERY_RULES = {
    # 加权拉普拉斯平滑：
    #   mastery = (加权正确 + PRIOR_CORRECT) / (加权证据 + PRIOR_TOTAL)
    # 权重：独立作答 1.0；复习重做 0.5（不是独立表现，只是"能重做对"）
    "prior_correct": 1.0,
    "prior_total": 2.0,
    "weight_independent": 1.0,
    "weight_review": 0.5,
    # 至少需要的独立证据数量，低于此值显示"待评估"
    "min_evidence": 1,
    "strong_threshold": 0.7,
    "weak_threshold": 0.4,
    # 只有独立作答计入"掌握"分子分母；复习重做按上面的权重折算
    "independent_kinds": ("independent",),
    "review_kinds": ("review",),
    # 看过答案后的作答、待自评/自评的作答：只留痕，不计证据
    "excluded_kinds": ("after_reveal",),
    "behavior_types": ("read", "review_read", "task_completed", "note_viewed"),
    # ---- 长期巩固（"一次答对 ≠ 长期掌握"）----
    # 需要至少这么多次独立答对，且最早/最晚答对间隔达到 min_span_days
    "consolidated_min_correct": 2,
    "consolidated_min_span_days": 1,
    # 或者独立答对次数达到该值即视为巩固（密集练习的兜底）
    "consolidated_fast_correct": 3,
}

# 巩固状态
CONSOLIDATION_UNVERIFIED = "unverified"   # 未巩固（缺少间隔复现）
CONSOLIDATION_CONSOLIDATED = "consolidated"  # 已巩固


class MasteryServiceError(Exception):
    def __init__(self, message: str, error_type: str = "unknown"):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


def _ensure_row(conn: sqlite3.Connection, user_id: str, subject_id: str,
                kp_name: str, kp_id: Optional[str]) -> Optional[sqlite3.Row]:
    """确保掌握度行存在（不存在则以'待评估'创建）"""
    row = conn.execute(
        "SELECT * FROM knowledge_mastery WHERE user_id=? AND subject_id=? AND knowledge_point=?",
        (user_id, subject_id, kp_name)).fetchone()
    if row:
        # 回填知识点稳定 ID（历史数据可能缺失）
        if kp_id and not row["knowledge_point_id"]:
            conn.execute("UPDATE knowledge_mastery SET knowledge_point_id=? WHERE id=?",
                         (kp_id, row["id"]))
            conn.commit()
        return conn.execute("SELECT * FROM knowledge_mastery WHERE id=?", (row["id"],)).fetchone()

    now = _now()
    kp_id = kp_id or ensure_knowledge_point(subject_id, kp_name, source="mastery")
    conn.execute(
        """INSERT INTO knowledge_mastery
           (id, user_id, subject_id, knowledge_point, knowledge_point_id, mastery_level,
            test_count, correct_count, last_tested_at, created_at, updated_at,
            mastery_status, evidence_count, independent_correct_count,
            independent_wrong_count, mastery_reason, behavior_count,
            review_correct_count, review_wrong_count, excluded_count,
            consolidation_status, recheck_count)
           VALUES (?,?,?,?,?,NULL,0,0,NULL,?,?,?,0,0,0,?,0,0,0,0,?,0)""",
        (_new_id("km"), user_id, subject_id, kp_name, kp_id, now, now,
         MASTERY_PENDING, "尚无独立作答证据，待评估", CONSOLIDATION_UNVERIFIED))
    conn.commit()
    return conn.execute(
        "SELECT * FROM knowledge_mastery WHERE user_id=? AND subject_id=? AND knowledge_point=?",
        (user_id, subject_id, kp_name)).fetchone()


def ensure_point_tracked(user_id: str, subject_id: str, knowledge_point_name: str) -> Dict[str, Any]:
    """
    把知识点纳入学习范围（生成自测题/笔记时调用）。

    只创建"待评估"记录：mastery_level 保持 NULL、证据数为 0，
    不写入任何默认分数（避免伪造学情）。学生因此能看到
    "该知识点已进入学习范围，但尚未评估"。
    """
    if not user_id or not subject_id or not knowledge_point_name:
        return {"tracked": False, "reason": "缺少必要参数"}
    conn = _get_db()
    try:
        kp_id = ensure_knowledge_point(subject_id, knowledge_point_name, source="mastery")
        row = _ensure_row(conn, user_id, subject_id, knowledge_point_name, kp_id)
        return {"tracked": True, "knowledge_point_id": row["knowledge_point_id"],
                "mastery_status": row["mastery_status"], "mastery_level": row["mastery_level"]}
    except sqlite3.Error as e:
        logger.error(f"纳入学习范围失败: {e}", exc_info=True)
        raise MasteryServiceError("纳入学习范围失败", "database_error")
    finally:
        conn.close()


def record_behavior(
    user_id: str,
    subject_id: str,
    knowledge_point_name: str,
    behavior_type: str = "task_completed",
    source_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    记录学习行为（阅读/完成任务等）。

    重要：行为**不改变掌握度**，只累加行为计数，避免"读完即掌握"的误判。
    """
    if not user_id or not subject_id or not knowledge_point_name:
        return {"recorded": False, "reason": "缺少必要参数"}
    conn = _get_db()
    try:
        kp_id = ensure_knowledge_point(subject_id, knowledge_point_name, source="mastery")
        row = _ensure_row(conn, user_id, subject_id, knowledge_point_name, kp_id)
        conn.execute(
            """UPDATE knowledge_mastery
               SET behavior_count = COALESCE(behavior_count,0) + 1,
                   last_behavior_at = ?, updated_at = ?
               WHERE id = ?""",
            (_now(), _now(), row["id"]))
        conn.commit()
        logger.info(f"记录学习行为: user={user_id}, kp={knowledge_point_name}, type={behavior_type}")
        return {"recorded": True, "behavior_type": behavior_type, "source_id": source_id,
                "mastery_unchanged": True}
    except sqlite3.Error as e:
        logger.error(f"记录学习行为失败: {e}", exc_info=True)
        raise MasteryServiceError("记录学习行为失败", "database_error")
    finally:
        conn.close()


def _compute_level(ind_correct: int, ind_wrong: int,
                   rev_correct: int, rev_wrong: int) -> Optional[float]:
    """
    加权平滑估计（可解释）：

        mastery = (加权正确 + 1) / (加权证据 + 2)
        独立作答权重 1.0；复习重做权重 0.5

    只有独立证据时才返回数值；否则返回 None（待评估，不给默认分）。
    """
    if ind_correct + ind_wrong <= 0:
        return None
    w_ind = MASTERY_RULES["weight_independent"]
    w_rev = MASTERY_RULES["weight_review"]
    weighted_correct = ind_correct * w_ind + rev_correct * w_rev
    weighted_evidence = (ind_correct + ind_wrong) * w_ind + (rev_correct + rev_wrong) * w_rev
    return round(
        (weighted_correct + MASTERY_RULES["prior_correct"])
        / (weighted_evidence + MASTERY_RULES["prior_total"]), 4)


def _span_days(first_iso: Optional[str], last_iso: Optional[str]) -> Optional[int]:
    """两次时间点的间隔天数（无法解析返回 None）"""
    if not first_iso or not last_iso:
        return None
    from datetime import datetime
    try:
        a = datetime.fromisoformat(str(first_iso)[:19])
        b = datetime.fromisoformat(str(last_iso)[:19])
    except ValueError:
        return None
    return abs((b - a).days)


def _consolidation(ind_correct: int, first_correct_at: Optional[str],
                   last_correct_at: Optional[str]) -> str:
    """
    长期巩固判定：**一次独立答对不等于长期掌握**，需要间隔复现。

    - 独立答对次数 >= consolidated_fast_correct → 已巩固
    - 或 独立答对 >= consolidated_min_correct 且 最早/最晚答对间隔 >= consolidated_min_span_days
    - 否则 unverified（未巩固）
    """
    if ind_correct >= MASTERY_RULES["consolidated_fast_correct"]:
        return CONSOLIDATION_CONSOLIDATED
    if ind_correct >= MASTERY_RULES["consolidated_min_correct"]:
        span = _span_days(first_correct_at, last_correct_at)
        if span is not None and span >= MASTERY_RULES["consolidated_min_span_days"]:
            return CONSOLIDATION_CONSOLIDATED
    return CONSOLIDATION_UNVERIFIED


def record_evidence(
    user_id: str,
    subject_id: str,
    knowledge_point_name: str,
    is_correct: bool,
    attempt_kind: str = "independent",
    source_attempt_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    记录作答证据并重算掌握估计。

    证据口径（阶段一补验修正）：

    | 作答性质     | 掌握估计 | 说明 |
    |---|---|---|
    | independent  | 权重 1.0 | 首次独立作答，唯一的"独立证据" |
    | review       | 权重 0.5 | 复习重做：能做对，但不是独立表现 |
    | after_reveal | 不计入   | 看过答案后作答，只留痕 |
    | 待自评/自评  | 不计入   | 自评不冒充客观判分（由调用方不传证据） |

    幂等：以 source_attempt_id 记账（同一作答重复记录直接跳过，
    保证事件重试不会重复累加掌握度）。

    Returns:
        {"counted", "mastery_status", "mastery_level", "consolidation_status",
         "evidence_count", "reason", "already_counted"(可选)}
    """
    if not user_id or not subject_id or not knowledge_point_name:
        return {"counted": False, "reason": "缺少必要参数"}

    independent = attempt_kind in MASTERY_RULES["independent_kinds"]
    review = attempt_kind in MASTERY_RULES["review_kinds"]
    conn = _get_db()
    try:
        kp_id = ensure_knowledge_point(subject_id, knowledge_point_name, source="mastery")
        row = _ensure_row(conn, user_id, subject_id, knowledge_point_name, kp_id)
        now = _now()

        # ---- 步骤级幂等：同一作答只计一次（跨库重试安全） ----
        if source_attempt_id:
            from services.idempotency import begin_step
            if not begin_step(conn, "mastery.record_evidence",
                              f"{user_id}:{source_attempt_id}", created_at=now):
                conn.rollback()
                return {"counted": False, "already_counted": True,
                        "reason": "该作答已计入掌握证据，跳过重复累加",
                        "mastery_status": row["mastery_status"],
                        "mastery_level": row["mastery_level"]}

        behavior_count = row["behavior_count"] or 0
        evidence_count = row["evidence_count"] or 0
        ind_correct = row["independent_correct_count"] or 0
        ind_wrong = row["independent_wrong_count"] or 0
        rev_correct = row["review_correct_count"] or 0
        rev_wrong = row["review_wrong_count"] or 0
        excluded = row["excluded_count"] or 0
        first_correct_at = row["first_correct_at"]
        last_correct_at = row["last_correct_at"]

        if not (independent or review):
            # 看过答案 / 待自评：只留痕，不计任何掌握证据
            excluded += 1
            reason = (f"本次作答（{attempt_kind}）不计入掌握证据："
                      f"已排除 {excluded} 次；独立证据仍为 {ind_correct + ind_wrong} 次")
            conn.execute(
                """UPDATE knowledge_mastery
                   SET excluded_count=?, mastery_reason=?, last_evidence_at=?, updated_at=?
                   WHERE id=?""",
                (excluded, reason, now, now, row["id"]))
            conn.commit()
            return {"counted": False, "mastery_status": row["mastery_status"],
                    "mastery_level": row["mastery_level"],
                    "consolidation_status": row["consolidation_status"],
                    "excluded_count": excluded, "reason": reason}

        if independent:
            evidence_count += 1
            if is_correct:
                ind_correct += 1
                last_correct_at = now
                if not first_correct_at:
                    first_correct_at = now
            else:
                ind_wrong += 1
        else:
            if is_correct:
                rev_correct += 1
            else:
                rev_wrong += 1

        level = _compute_level(ind_correct, ind_wrong, rev_correct, rev_wrong)
        status = MASTERY_ESTIMATED if level is not None else MASTERY_PENDING
        consolidation = _consolidation(ind_correct, first_correct_at, last_correct_at)

        reason = (
            f"掌握估计（暂估值，非长期掌握结论）：基于 {ind_correct + ind_wrong} 次独立作答"
            f"（对 {ind_correct} / 错 {ind_wrong}）"
            + (f"，另有复习重做 对 {rev_correct} / 错 {rev_wrong}（按 0.5 权重折算）"
               if (rev_correct + rev_wrong) else "")
            + (f"；已排除 {excluded} 次（看过答案后作答/自评）" if excluded else "")
            + f"；学习行为 {behavior_count} 次不计入掌握度"
            + ("；已通过间隔复现，判定为已巩固"
               if consolidation == CONSOLIDATION_CONSOLIDATED
               else "；尚缺间隔复现，暂不认定为长期掌握")
        )

        conn.execute(
            """UPDATE knowledge_mastery
               SET mastery_level=?, mastery_status=?, evidence_count=?,
                   independent_correct_count=?, independent_wrong_count=?,
                   review_correct_count=?, review_wrong_count=?, excluded_count=?,
                   first_correct_at=?, last_correct_at=?, consolidation_status=?,
                   test_count=?, correct_count=?, last_tested_at=?, last_evidence_at=?,
                   mastery_reason=?, updated_at=?
               WHERE id=?""",
            (level, status, evidence_count,
             ind_correct, ind_wrong, rev_correct, rev_wrong, excluded,
             first_correct_at, last_correct_at, consolidation,
             evidence_count, ind_correct, now, now, reason, now, row["id"]))
        conn.commit()
        logger.info(f"记录作答证据: user={user_id}, kp={knowledge_point_name}, "
                    f"correct={is_correct}, kind={attempt_kind}, level={level}, "
                    f"consolidation={consolidation}")
        return {"counted": True, "mastery_status": status, "mastery_level": level,
                "consolidation_status": consolidation,
                "consolidated": consolidation == CONSOLIDATION_CONSOLIDATED,
                "evidence_count": evidence_count,
                "independent_correct": ind_correct, "independent_wrong": ind_wrong,
                "review_correct": rev_correct, "review_wrong": rev_wrong,
                "excluded_count": excluded,
                "recheck_count": row["recheck_count"] if "recheck_count" in row.keys() else 0,
                "reason": reason, "source_attempt_id": source_attempt_id}
    except sqlite3.Error as e:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        logger.error(f"记录作答证据失败: {e}", exc_info=True)
        raise MasteryServiceError("记录作答证据失败", "database_error")
    finally:
        conn.close()


def mark_recheck_scheduled(user_id: str, subject_id: str,
                           knowledge_point_name: str) -> Dict[str, Any]:
    """
    记录"已为该知识点安排一次延后复测"（用于上限控制与可解释性）。

    只累加计数，不改变掌握估计。
    """
    if not user_id or not subject_id or not knowledge_point_name:
        return {"updated": False, "reason": "缺少必要参数"}
    conn = _get_db()
    try:
        kp_id = ensure_knowledge_point(subject_id, knowledge_point_name, source="mastery")
        row = _ensure_row(conn, user_id, subject_id, knowledge_point_name, kp_id)
        count = (row["recheck_count"] or 0) + 1
        conn.execute(
            """UPDATE knowledge_mastery
               SET recheck_count=?, mastery_reason=?, updated_at=? WHERE id=?""",
            (count,
             f"{row['mastery_reason'] or ''}；已安排第 {count} 次延后复测".strip("；"),
             _now(), row["id"]))
        conn.commit()
        return {"updated": True, "recheck_count": count}
    except sqlite3.Error as e:
        logger.warning(f"记录延后复测次数失败（不影响复测任务）: {e}")
        return {"updated": False, "reason": str(e)}
    finally:
        conn.close()


def get_mastery(user_id: str, subject_id: str, knowledge_point_name: str) -> Dict[str, Any]:
    """获取单个知识点的掌握信息（无证据时明确返回待评估）"""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_mastery WHERE user_id=? AND subject_id=? AND knowledge_point=?",
            (user_id, subject_id, knowledge_point_name)).fetchone()
        if not row:
            return {
                "knowledge_point": knowledge_point_name,
                "knowledge_point_id": ensure_knowledge_point(subject_id, knowledge_point_name,
                                                              source="mastery"),
                "mastery_status": MASTERY_PENDING,
                "mastery_level": None,
                "evidence_count": 0,
                "independent_correct_count": 0,
                "independent_wrong_count": 0,
                "behavior_count": 0,
                "reason": "尚无独立作答证据，待评估",
                "updated_at": None,
            }
        return _row_to_mastery(row)
    finally:
        conn.close()


def _row_to_mastery(row) -> Dict[str, Any]:
    keys = row.keys()
    level = row["mastery_level"]
    status = row["mastery_status"] or (MASTERY_ESTIMATED if (row["evidence_count"] or 0) > 0
                                       else MASTERY_PENDING)
    if status == MASTERY_PENDING:
        level = None  # 不把默认值当精确掌握率

    ind_correct = row["independent_correct_count"] or 0
    ind_wrong = row["independent_wrong_count"] or 0
    rev_correct = row["review_correct_count"] if "review_correct_count" in keys else 0
    rev_wrong = row["review_wrong_count"] if "review_wrong_count" in keys else 0
    excluded = row["excluded_count"] if "excluded_count" in keys else 0
    consolidation = (row["consolidation_status"] if "consolidation_status" in keys
                     else CONSOLIDATION_UNVERIFIED) or CONSOLIDATION_UNVERIFIED

    return {
        "knowledge_point": row["knowledge_point"],
        "knowledge_point_id": row["knowledge_point_id"],
        "mastery_status": status,
        "mastery_level": level,
        # 明确标注这是"估计"，并给出样本量，避免被当成精确结论
        "mastery_is_estimate": True,
        "sample_size": ind_correct + ind_wrong,
        "evidence_count": row["evidence_count"] or 0,
        "evidence_breakdown": {
            "independent_correct": ind_correct,
            "independent_wrong": ind_wrong,
            "review_correct": rev_correct,
            "review_wrong": rev_wrong,
            "excluded": excluded,
        },
        "independent_correct_count": ind_correct,
        "independent_wrong_count": ind_wrong,
        "review_correct_count": rev_correct,
        "review_wrong_count": rev_wrong,
        "excluded_count": excluded,
        # 长期掌握判定与"独立作答"分开：一次答对不等于长期掌握
        "consolidation_status": consolidation,
        "consolidated": consolidation == CONSOLIDATION_CONSOLIDATED,
        "recheck_count": row["recheck_count"] if "recheck_count" in keys else 0,
        "first_correct_at": row["first_correct_at"] if "first_correct_at" in keys else None,
        "last_correct_at": row["last_correct_at"] if "last_correct_at" in keys else None,
        "behavior_count": row["behavior_count"] or 0,
        "last_evidence_at": row["last_evidence_at"],
        "last_behavior_at": row["last_behavior_at"],
        "reason": row["mastery_reason"],
        "updated_at": row["updated_at"],
        "rules": {
            "weight_independent": MASTERY_RULES["weight_independent"],
            "weight_review": MASTERY_RULES["weight_review"],
            "prior_correct": MASTERY_RULES["prior_correct"],
            "prior_total": MASTERY_RULES["prior_total"],
            "consolidated_min_correct": MASTERY_RULES["consolidated_min_correct"],
            "consolidated_min_span_days": MASTERY_RULES["consolidated_min_span_days"],
        },
    }


def list_mastery(user_id: str, subject_id: str) -> List[Dict[str, Any]]:
    """列出学科下所有知识点的掌握情况（个人统计的唯一数据来源）"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM knowledge_mastery WHERE user_id=? AND subject_id=? "
            "ORDER BY updated_at DESC",
            (user_id, subject_id)).fetchall()
        return [_row_to_mastery(r) for r in rows]
    finally:
        conn.close()


def summarize_mastery(user_id: str, subject_id: str) -> Dict[str, Any]:
    """
    掌握度汇总（个人统计与课程整体统计保持一致的口径）。

    - pending: 待评估（无独立证据）
    - strong / medium / weak: 已有证据的分布
    - accuracy: 独立作答正确率（无证据时为 None）
    """
    items = list_mastery(user_id, subject_id)
    strong = medium = weak = pending = 0
    ev = correct = wrong = 0
    behavior = 0
    for it in items:
        behavior += it["behavior_count"]
        if it["mastery_status"] == MASTERY_PENDING or it["mastery_level"] is None:
            pending += 1
            continue
        ev += it["evidence_count"]
        correct += it["independent_correct_count"]
        wrong += it["independent_wrong_count"]
        if it["mastery_level"] >= MASTERY_RULES["strong_threshold"]:
            strong += 1
        elif it["mastery_level"] < MASTERY_RULES["weak_threshold"]:
            weak += 1
        else:
            medium += 1
    return {
        "user_id": user_id,
        "subject_id": subject_id,
        "total_points": len(items),
        "distribution": {"strong": strong, "medium": medium, "weak": weak, "pending": pending},
        "evidence_total": ev,
        "independent_correct": correct,
        "independent_wrong": wrong,
        "accuracy": round(correct / ev, 4) if ev else None,
        "accuracy_is_estimate": True,
        "accuracy_sample_size": ev,
        "consolidated_points": sum(1 for it in items if it.get("consolidated")),
        "unverified_points": sum(
            1 for it in items
            if it["mastery_level"] is not None and not it.get("consolidated")),
        "excluded_total": sum(it.get("excluded_count") or 0 for it in items),
        "review_correct_total": sum(it.get("review_correct_count") or 0 for it in items),
        "behavior_total": behavior,
        "rules": MASTERY_RULES,
    }


__all__ = [
    "MASTERY_RULES",
    "CONSOLIDATION_UNVERIFIED",
    "CONSOLIDATION_CONSOLIDATED",
    "MasteryServiceError",
    "ensure_point_tracked",
    "mark_recheck_scheduled",
    "record_behavior",
    "record_evidence",
    "get_mastery",
    "list_mastery",
    "summarize_mastery",
]
