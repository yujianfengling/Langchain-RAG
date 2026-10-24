"""BKT 知识追踪（2026-10-24）

贝叶斯知识追踪（Bayesian Knowledge Tracing）的轻量实现：
每个知识点维护"已掌握概率"，按作答序列更新四个参数：
prior（先验）、learn（学得会）、guess（不会但蒙对）、slip（会但答错）。

定位：**影子模式**。只读作答记录、只输出估计，不改变现有掌握度/判分/复习口径；
等真实试用证明预测更准后，再考虑接入选练习/复习优先级。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.learning_data import _get_db

DEFAULT_PARAMS: Dict[str, float] = {
    "prior": 0.30,
    "learn": 0.15,
    "guess": 0.20,
    "slip": 0.10,
}

# 复习重做不是独立证据：只给一次部分更新，避免"重做同一题"把概率推到 1。
ATTEMPT_WEIGHTS = {"independent": 1.0, "review": 0.5, "after_reveal": 0.0}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def update_probability(p_known: float, correct: bool,
                       params: Optional[Dict[str, float]] = None) -> float:
    """一次观测后的掌握概率（先贝叶斯后验，再加学习转移）。"""
    p = _clamp(p_known)
    cfg = dict(DEFAULT_PARAMS)
    if params:
        cfg.update({k: float(v) for k, v in params.items() if k in cfg})
    guess = _clamp(cfg["guess"])
    slip = _clamp(cfg["slip"])
    learn = _clamp(cfg["learn"])
    if correct:
        numerator = p * (1.0 - slip)
        denominator = numerator + (1.0 - p) * guess
    else:
        numerator = p * slip
        denominator = numerator + (1.0 - p) * (1.0 - guess)
    posterior = numerator / denominator if denominator > 0 else p
    return _clamp(posterior + (1.0 - posterior) * learn)


def estimate_sequence(observations: Iterable[Tuple[bool, float]],
                      params: Optional[Dict[str, float]] = None,
                      prior: Optional[float] = None) -> Dict[str, Any]:
    """按 (是否正确, 权重) 序列估计；权重 <1 时只做部分更新。"""
    cfg = dict(DEFAULT_PARAMS)
    if params:
        cfg.update({k: float(v) for k, v in params.items() if k in cfg})
    p = _clamp(cfg["prior"] if prior is None else prior)
    count = 0
    correct_count = 0
    for correct, weight in observations:
        weight = _clamp(weight)
        if weight <= 0:
            continue
        count += 1
        correct_count += 1 if correct else 0
        updated = update_probability(p, bool(correct), cfg)
        p = _clamp(p + (updated - p) * weight)
    return {
        "p_known": round(p, 4),
        "attempts": count,
        "correct_count": correct_count,
        "confidence": round(min(1.0, count / 10.0), 4),
        "params": cfg,
    }


def estimate_user(subject_id: str, user_id: str,
                  knowledge_point: Optional[str] = None,
                  params: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """按知识点聚合该用户的作答序列，返回影子 BKT 估计。"""
    if not subject_id or not user_id:
        return {"subject_id": subject_id, "points": [], "params": dict(DEFAULT_PARAMS)}
    sql = ("SELECT knowledge_point_name, is_correct, attempt_kind, created_at, rowid "
           "FROM quiz_attempts WHERE user_id=? AND subject_id=? "
           "AND is_correct IS NOT NULL")
    args: List[Any] = [user_id, subject_id]
    if knowledge_point:
        sql += " AND knowledge_point_name=?"
        args.append(knowledge_point)
    sql += " ORDER BY created_at ASC, rowid ASC"
    conn = _get_db()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()

    grouped: Dict[str, List[Any]] = {}
    for row in rows:
        name = (row["knowledge_point_name"] or "").strip()
        if not name or name == "待确认":
            continue
        grouped.setdefault(name, []).append(row)

    points: List[Dict[str, Any]] = []
    for name, items in grouped.items():
        observations = []
        for row in items:
            weight = ATTEMPT_WEIGHTS.get(row["attempt_kind"] or "independent", 0.5)
            observations.append((bool(row["is_correct"]), weight))
        estimated = estimate_sequence(observations, params)
        points.append({
            "knowledge_point": name,
            "p_known": estimated["p_known"],
            "attempts": estimated["attempts"],
            "correct_count": estimated["correct_count"],
            "confidence": estimated["confidence"],
            "last_attempt_at": items[-1]["created_at"],
        })
    points.sort(key=lambda item: (item["p_known"], -item["attempts"]))
    # 选题建议：优先样本足够（>=2 次）的最低掌握点；全都不足时退回最低点。
    recommended = None
    for item in points:
        if item["attempts"] >= 2:
            recommended = item["knowledge_point"]
            break
    if recommended is None and points:
        recommended = points[0]["knowledge_point"]
    return {"subject_id": subject_id, "points": points,
            "recommended": recommended,
            "params": dict(DEFAULT_PARAMS) if not params else {**DEFAULT_PARAMS, **params}}


__all__ = [
    "DEFAULT_PARAMS", "ATTEMPT_WEIGHTS", "update_probability",
    "estimate_sequence", "estimate_user",
]
