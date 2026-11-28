"""
学生端统一学情反馈服务

把分散在 mastery_service / knowledge_tracing / fsrs_scheduler / quiz_service /
teacher_grading / graph_bridge 的真实记录整合成面向学生的可行动反馈：
1. 统一掌握视图（当前掌握 + 巩固状态 + BKT 参考概率 + FSRS 保持率 + 前置溯因）
2. FSRS 遗忘预警（即将遗忘 / 已到期的知识点）
3. 错因画像（聚合 ERROR_CAUSES，给出针对性改进策略）
4. 进步对比（近 7 天 vs 前 7 天、复测转绿知识点、每日趋势）
5. 教师批改/改判回流（把 teacher_reason 和改判结果显式反馈给学生）

原则：所有指标由真实记录计算，证据不足时如实标注，绝不虚构分数。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from services import (
    fsrs_scheduler,
    graph_bridge,
    knowledge_tracing,
    learning_data,
    mastery_service,
    quiz_service,
)
from utils.logger import logger

ERROR_CAUSE_ADVICE: Dict[str, str] = {
    "concept_confusion": "容易混淆相近概念，建议回到知识图谱对比前置与易混概念的定义边界。",
    "misread": "审题阶段容易漏条件或看反问法，建议作答前先划出已知量与限定词。",
    "formula_recall": "公式记忆或适用条件不够稳固，建议结合推导过程和单位量纲做间隔复测。",
    "calculation": "推导思路基本正确但代入计算易错，建议关键中间量分步书写并做量级验算。",
    "step_omission": "容易漏写关键推导或边界讨论步骤，建议对照标准解析补齐检查清单。",
    "expression": "理解要点但专业表述不够规范，建议用教材标准术语重述核心结论。",
}


def _safe_days(days: int) -> int:
    try:
        return max(1, min(int(days or 30), 365))
    except (TypeError, ValueError):
        return 30


def _latest_mastery_tuple(point: Dict[str, Any]):
    latest = point.get("latest_view") or {}
    lvl = latest.get("latest_mastery_level")
    if lvl is not None:
        return (
            float(lvl),
            int(latest.get("latest_evidence_count") or 0),
            int(latest.get("latest_independent_correct") or 0),
            int(latest.get("latest_independent_wrong") or 0),
        )
    raw = point.get("mastery_level")
    return (
        float(raw) if raw is not None else None,
        int(point.get("evidence_count") or 0),
        int(point.get("independent_correct_count") or 0),
        int(point.get("independent_wrong_count") or 0),
    )


def get_student_feedback_summary(
    user_id: str,
    subject_id: str,
    days: int = 30,
) -> Dict[str, Any]:
    """生成学生本学科的统一学情反馈摘要。"""
    days = _safe_days(days)
    today = date.today()
    start_str = (today - timedelta(days=days - 1)).isoformat()
    recent7_start = (today - timedelta(days=6)).isoformat()
    prev7_start = (today - timedelta(days=13)).isoformat()
    prev7_end = (today - timedelta(days=7)).isoformat()
    soon_str = (today + timedelta(days=2)).isoformat()

    # 1) 读取真实作答记录（含错因与教师改判字段）
    conn = learning_data._get_db()
    try:
        learning_data.ensure_columns(conn, "quiz_attempts")
        rows = conn.execute(
            """SELECT a.id, a.question_id, a.knowledge_point_id, a.knowledge_point_name,
                      a.user_answer, a.user_answer_text, a.is_correct, a.grade_status,
                      a.attempt_kind, a.attempt_no, a.error_code, a.error_note,
                      a.orig_is_correct, a.orig_grade_status,
                      a.teacher_is_correct, a.teacher_reason, a.teacher_graded_at,
                      a.created_at, q.stem
               FROM quiz_attempts a
               LEFT JOIN quiz_questions q ON q.id = a.question_id
               WHERE a.user_id=? AND a.subject_id=?
               ORDER BY a.created_at ASC, a.rowid ASC""",
            (user_id, subject_id),
        ).fetchall()
    finally:
        conn.close()

    attempts = [dict(r) for r in rows]
    in_range = [a for a in attempts if str(a.get("created_at") or "")[:10] >= start_str]
    graded_in_range = [a for a in in_range if a.get("is_correct") is not None]

    # 2) 错因聚合（来自学生自报的 ERROR_CAUSES）
    cause_counts: Dict[str, int] = {}
    for a in in_range:
        code = (a.get("error_code") or "").strip()
        if code and code in quiz_service.ERROR_CAUSES:
            cause_counts[code] = cause_counts.get(code, 0) + 1
    total_tagged = sum(cause_counts.values())
    error_causes = [
        {
            "code": code,
            "label": quiz_service.ERROR_CAUSES[code],
            "count": count,
            "ratio": round(count / total_tagged, 3) if total_tagged else 0.0,
            "advice": ERROR_CAUSE_ADVICE.get(code, ""),
        }
        for code, count in sorted(cause_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    # 3) 教师改判与评语回流（不限时间窗口太窄，取最近 20 条有教师判定的记录）
    teacher_feedback: List[Dict[str, Any]] = []
    for a in reversed(attempts):
        if a.get("teacher_is_correct") is None:
            continue
        teacher_feedback.append({
            "attempt_id": a["id"],
            "question_id": a["question_id"],
            "stem": a.get("stem") or "",
            "knowledge_point": a.get("knowledge_point_name") or "未标注知识点",
            "user_answer": a.get("user_answer"),
            "user_answer_text": a.get("user_answer_text"),
            "original_is_correct": (
                None if a.get("orig_is_correct") is None else bool(a["orig_is_correct"])
            ),
            "teacher_is_correct": bool(a["teacher_is_correct"]),
            "teacher_reason": a.get("teacher_reason") or "",
            "teacher_graded_at": a.get("teacher_graded_at") or a.get("created_at"),
        })
        if len(teacher_feedback) >= 20:
            break

    # 4) 进度对比（近 7 天 vs 前 7 天 + 复测转绿 + 每日趋势）
    def _window_stats(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        g = [x for x in items if x.get("is_correct") is not None]
        c = sum(1 for x in g if bool(x["is_correct"]))
        w = len(g) - c
        return {
            "attempts": len(g),
            "correct": c,
            "wrong": w,
            "accuracy": round(c / len(g), 3) if g else None,
        }

    recent7 = _window_stats([a for a in attempts if str(a.get("created_at") or "")[:10] >= recent7_start])
    prev7 = _window_stats([
        a for a in attempts
        if prev7_start <= str(a.get("created_at") or "")[:10] <= prev7_end
    ])
    acc_delta = (
        round(recent7["accuracy"] - prev7["accuracy"], 3)
        if recent7["accuracy"] is not None and prev7["accuracy"] is not None
        else None
    )

    # 统计"曾经答错、最新一次已答对"的题目数与知识点（形成性正反馈）
    by_q: Dict[str, List[Dict[str, Any]]] = {}
    for a in attempts:
        if a.get("is_correct") is None:
            continue
        by_q.setdefault(a["question_id"], []).append(a)
    recovered_questions = 0
    recovered_kps_set = set()
    for hist in by_q.values():
        had_wrong = any(not bool(x["is_correct"]) for x in hist[:-1])
        latest_ok = bool(hist[-1]["is_correct"])
        if had_wrong and latest_ok:
            recovered_questions += 1
            kp_n = (hist[-1].get("knowledge_point_name") or "").strip()
            if kp_n and kp_n != "待确认":
                recovered_kps_set.add(kp_n)

    # 按天趋势（仅含区间内有作答的日期）
    by_day: Dict[str, Dict[str, int]] = {}
    for a in graded_in_range:
        day = str(a.get("created_at") or "")[:10]
        if not day:
            continue
        slot = by_day.setdefault(day, {"attempts": 0, "correct": 0, "wrong": 0})
        slot["attempts"] += 1
        if bool(a["is_correct"]):
            slot["correct"] += 1
        else:
            slot["wrong"] += 1
    daily_trend = [
        {
            "date": day,
            "attempts": v["attempts"],
            "correct": v["correct"],
            "wrong": v["wrong"],
            "accuracy": round(v["correct"] / v["attempts"], 3) if v["attempts"] else None,
        }
        for day, v in sorted(by_day.items())
    ]

    # 5) 融合掌握度 + BKT + FSRS + 知识图谱前置溯因
    mastery_summary = mastery_service.summarize_mastery(user_id, subject_id) or {}
    mastery_points = mastery_service.list_mastery(user_id, subject_id) or []
    bkt_data = knowledge_tracing.estimate_user(subject_id, user_id) or {}
    bkt_by_norm = {
        graph_bridge.normalize_concept(p["knowledge_point"]): p
        for p in (bkt_data.get("points") or [])
        if p.get("knowledge_point")
    }
    fsrs_cards = fsrs_scheduler.list_cards(user_id, subject_id) or []
    fsrs_by_kp_id = {c["knowledge_point_id"]: c for c in fsrs_cards if c.get("knowledge_point_id")}

    kp_name_by_id: Dict[str, str] = {}
    metrics_by_label: Dict[str, Dict[str, Any]] = {}
    for p in mastery_points:
        kp_name = str(p.get("knowledge_point") or "").strip()
        if not kp_name or kp_name == "待确认":
            continue
        if p.get("knowledge_point_id"):
            kp_name_by_id[p["knowledge_point_id"]] = kp_name
        lvl, ev, _, wrong = _latest_mastery_tuple(p)
        bkt = bkt_by_norm.get(graph_bridge.normalize_concept(kp_name))
        metrics_by_label[kp_name] = {
            "mastery_level": lvl,
            "evidence_count": ev,
            "wrong_count": wrong,
            "bkt_p_known": bkt.get("p_known") if bkt else None,
        }

    # 尝试加载图谱与学习路线一次，避免循环重复读盘
    graph = graph_bridge.load_subject_graph(subject_id)
    lmap = None
    try:
        from services import knowledge_map
        lmap = knowledge_map.get_map(subject_id)
    except Exception:
        pass

    unified_points: List[Dict[str, Any]] = []
    root_causes: List[Dict[str, Any]] = []
    for p in mastery_points:
        kp_name = str(p.get("knowledge_point") or "").strip()
        if not kp_name or kp_name == "待确认":
            continue
        lvl, ev, corr, wrong = _latest_mastery_tuple(p)
        kn = graph_bridge.normalize_concept(kp_name)
        bkt = bkt_by_norm.get(kn)
        card = fsrs_by_kp_id.get(p.get("knowledge_point_id") or "")
        ret = card.get("retrievability") if card else None
        due = card.get("due") if card else None
        consolidated = bool(p.get("consolidated"))
        spaced = mastery_service.is_spaced_consolidation(
            p.get("first_correct_at"), p.get("last_correct_at"))

        is_forgetting = (
            (ret is not None and ret < 0.78)
            or (due is not None and due <= soon_str and (lvl is None or lvl >= 0.5))
        )
        if lvl is None or ev <= 0:
            stage, stage_label = "pending", "待评估"
        elif lvl < 0.5 or (wrong > 0 and lvl < 0.65):
            stage, stage_label = "weak", "需优先巩固"
        elif is_forgetting:
            stage, stage_label = "forgetting", "遗忘预警"
        elif lvl >= 0.7 and consolidated and spaced:
            stage, stage_label = "consolidated", "已巩固"
        else:
            stage, stage_label = "developing", "暂过待复测"

        prereq_diag = None
        if stage in ("weak", "forgetting"):
            try:
                diag = graph_bridge.diagnose_prerequisites(
                    kp_name, metrics_by_label,
                    graph=graph, learning_map=lmap, mode="student",
                )
                if diag.get("has_graph_links"):
                    prereq_diag = diag
                    if diag.get("root_cause_type") == "prerequisite_gap":
                        root_causes.append({
                            "knowledge_point": kp_name,
                            "mastery_level": round(lvl, 4) if lvl is not None else None,
                            "wrong_count": wrong,
                            "weak_prerequisites": diag["weak_prerequisites"],
                            "summary": diag["summary"],
                        })
            except Exception as e:
                logger.debug(f"学生反馈前置溯因跳过 kp={kp_name}: {e}")

        unified_points.append({
            "knowledge_point_id": p.get("knowledge_point_id"),
            "knowledge_point": kp_name,
            "stage": stage,
            "stage_label": stage_label,
            "mastery_level": round(lvl, 4) if lvl is not None else None,
            "evidence_count": ev,
            "correct_count": corr,
            "wrong_count": wrong,
            "consolidated": consolidated,
            "spaced_review": spaced,
            "bkt_p_known": bkt.get("p_known") if bkt else None,
            "bkt_confidence": bkt.get("confidence") if bkt else None,
            "retrievability": ret,
            "due_date": due,
            "lapses": int(card.get("lapses") or 0) if card else 0,
            "prerequisite_diagnosis": prereq_diag,
        })

    stage_order = {"weak": 0, "forgetting": 1, "developing": 2, "consolidated": 3, "pending": 4}
    unified_points.sort(
        key=lambda x: (
            stage_order.get(x["stage"], 9),
            x["mastery_level"] if x["mastery_level"] is not None else 2.0,
            -x["wrong_count"],
        )
    )

    # 6) FSRS 遗忘预警列表（即使某知识点还没进 mastery 列表也能从 learning_loop 补齐名字）
    forgetting_alerts: List[Dict[str, Any]] = []
    missing_ids = [
        c["knowledge_point_id"] for c in fsrs_cards
        if c.get("knowledge_point_id") and c["knowledge_point_id"] not in kp_name_by_id
    ]
    if missing_ids:
        conn2 = learning_data._get_db()
        try:
            marks = ",".join("?" for _ in missing_ids)
            for row in conn2.execute(
                f"SELECT id, name FROM knowledge_points WHERE id IN ({marks})",
                missing_ids,
            ).fetchall():
                kp_name_by_id[row["id"]] = row["name"]
        except Exception:
            pass
        finally:
            conn2.close()

    today_str = today.isoformat()
    for card in fsrs_cards:
        ret = card.get("retrievability")
        due = card.get("due")
        if not ((ret is not None and ret < 0.78) or (due and due <= soon_str)):
            continue
        kpid = card.get("knowledge_point_id")
        kp_name = kp_name_by_id.get(kpid) or kpid
        forgetting_alerts.append({
            "knowledge_point_id": kpid,
            "knowledge_point": kp_name,
            "retrievability": ret,
            "due_date": due,
            "overdue": bool(due and due < today_str),
            "lapses": int(card.get("lapses") or 0),
            "reps": int(card.get("reps") or 0),
            "stability": card.get("stability"),
        })
    forgetting_alerts.sort(
        key=lambda x: (
            not x["overdue"],
            x["retrievability"] if x["retrievability"] is not None else 1.0,
            x["due_date"] or "9999-99-99",
        )
    )

    # 7) 确定性一句话诊断与下一步建议（严格基于真实记录）
    def _short_kp(name: str, limit: int = 16) -> str:
        s = str(name or "").strip()
        # 去掉历史工具拼出的"（重点讲：...）"后缀，避免按钮和摘要过长
        if "（重点讲：" in s:
            s = s.split("（重点讲：", 1)[0].strip()
        return s if len(s) <= limit else s[:limit - 1] + "…"

    weak_list = [p for p in unified_points if p["stage"] == "weak"]
    next_action: Optional[Dict[str, Any]] = None
    if root_causes:
        top_rc = root_causes[0]
        first_pre = (top_rc.get("weak_prerequisites") or [{}])[0].get("concept")
        target = first_pre or top_rc["knowledge_point"]
        headline = top_rc["summary"]
        next_action = {
            "kind": "prereq",
            "label": f"先补前置：{_short_kp(target, 12)}",
            "concept": target,
            "reason": f"「{_short_kp(top_rc['knowledge_point'], 24)}」存在前置概念薄弱，先补齐基础效率最高。",
        }
    elif weak_list:
        top_w = weak_list[0]
        pct = round((top_w["mastery_level"] or 0) * 100)
        headline = f"当前最需要巩固的是「{_short_kp(top_w['knowledge_point'], 26)}」（掌握估计 {pct}%，独立错 {top_w['wrong_count']} 次）。"
        next_action = {
            "kind": "practice",
            "label": f"巩固练习：{_short_kp(top_w['knowledge_point'], 12)}",
            "concept": top_w["knowledge_point"],
            "reason": "优先处理当前掌握度最低的薄弱知识点。",
        }
    elif forgetting_alerts:
        top_f = forgetting_alerts[0]
        ret_text = f"（保持率约 {round(top_f['retrievability'] * 100)}%）" if top_f.get("retrievability") is not None else ""
        headline = f"有 {len(forgetting_alerts)} 个知识点进入遗忘预警，建议优先复测「{_short_kp(top_f['knowledge_point'], 24)}」{ret_text}。"
        next_action = {
            "kind": "review",
            "label": f"立即复测：{_short_kp(top_f['knowledge_point'], 12)}",
            "concept": top_f["knowledge_point"],
            "reason": "按 FSRS 记忆曲线及时复测可延长记忆稳定性。",
        }
    elif graded_in_range:
        acc_pct = round((sum(1 for a in graded_in_range if a["is_correct"]) / len(graded_in_range)) * 100)
        headline = f"近 {days} 天已完成 {len(graded_in_range)} 次有效作答，正确率 {acc_pct}%，继续保持间隔复测巩固。"
    else:
        headline = "本学科暂无足够作答证据，去「开始学习」完成几道检测题后即可生成完整学情诊断。"

    return {
        "subject_id": subject_id,
        "days": days,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "headline": headline,
        "next_action": next_action,
        "summary": mastery_summary,
        "knowledge_points": unified_points,
        "root_causes": root_causes[:6],
        "forgetting_alerts": forgetting_alerts[:8],
        "error_causes": error_causes,
        "error_cause_total": total_tagged,
        "progress": {
            "recent_7d": recent7,
            "previous_7d": prev7,
            "accuracy_delta": acc_delta,
            "recovered_questions": recovered_questions,
            "recovered_kps": sorted(recovered_kps_set),
            "daily_trend": daily_trend,
        },
        "teacher_feedback": teacher_feedback,
        "has_knowledge_graph": graph is not None,
    }


__all__ = ["ERROR_CAUSE_ADVICE", "get_student_feedback_summary"]
