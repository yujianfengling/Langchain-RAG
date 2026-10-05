"""
教学上下文构建（阶段二）

把**真实学习状态**压缩成一小段可用于提示词的信息，供流式与非流式问答统一使用。

要求（对应本轮）：
- 统一处理：学生明确设置的表达偏好、相关知识点的作答证据、最近错误与提示使用、
  当前任务/目标/剩余时间
- **不能把整份历史记录拼进提示词**，要按与当前问题的相关性选取少量信息
- 有明确的上下文预算，超预算按优先级截断
- 画像不足（没有证据）时使用普通教学方式，**不虚构学生水平**

产出：
    {
      "prompt_block": str,       # 给模型的画像说明（可为空字符串）
      "teaching_mode": str,      # foundation | standard | advanced
      "reasons": [str, ...],     # 为什么这样安排（可展示给学生/审计）
      "signals": {...},          # 结构化信号，便于测试与前端展示
      "budget": {...},           # 上下文预算使用情况
    }
"""

import json
from typing import Any, Dict, List, Optional

from utils.logger import logger

# 教学策略
MODE_FOUNDATION = "foundation"   # 基础优先：先讲清概念，配小步练习
MODE_STANDARD = "standard"       # 常规教学
MODE_ADVANCED = "advanced"       # 综合练习与迁移

# 上下文预算（字符数）——避免把画像堆成超长提示词
BUDGET = {
    "total_chars": 900,
    "preferences": 200,
    "evidence": 260,
    "mistakes": 240,
    "task": 200,
    "memory": 240,
}

# 达到"可进阶"所需的独立作答样本与掌握估计
ADVANCED_MIN_SAMPLE = 2
ADVANCED_MASTERY = 0.7
# 达到"基础优先"的独立答错次数
FOUNDATION_MIN_WRONG = 1


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)] + "…"


def _collect_preferences(user_id: str) -> List[str]:
    """学生明确设置的表达/学习偏好（优先级最高）"""
    out: List[str] = []
    try:
        from services.user_profiler import get_learning_preferences
        prefs = get_learning_preferences(user_id) or {}
    except Exception as e:
        logger.debug(f"读取学习偏好失败: {e}")
        return out

    mapping = {
        "detail_level": lambda v: (
            "默认给出更详细的讲解（学生已设置）" if str(v) in ("2", "expanded", "detailed", "详细")
            else "默认简洁回答，必要时再展开（学生已设置）"),
        "explanation_style": lambda v: f"偏好讲解方式：{v}（学生已设置）",
        "pace": lambda v: f"偏好节奏：{v}（学生已设置）",
    }
    for key, fmt in mapping.items():
        if prefs.get(key):
            out.append(fmt(prefs[key]))
    return out


def _collect_memory(user_id: str, subject_id: Optional[str]) -> List[str]:
    """生效中的记忆（含来源标注，让学生/教师知道依据）"""
    out: List[str] = []
    try:
        from services import learning_memory
        effective = learning_memory.get_effective_memory(user_id, subject_id)
    except Exception as e:
        logger.debug(f"读取学习记忆失败: {e}")
        return out

    for it in effective["items"]:
        if it["memory_type"] == learning_memory.MEMORY_PREFERENCE:
            out.append(f"学生设置：{it['key']} = {it['value']}")
        elif it["memory_type"] == learning_memory.MEMORY_INFERENCE:
            kp = (it.get("evidence") or {}).get("knowledge_point")
            label = f"{kp} " if kp else ""
            out.append(f"系统判断（依据：{it.get('note') or '学习记录'}）：{label}{it['value']}")
        elif it["memory_type"] == learning_memory.MEMORY_CURRICULUM:
            out.append(f"课程资料确认：{it['key']} = {it['value']}")
    return out


def _match_knowledge_point(subject_id: str, question: str,
                           user_id: str) -> Optional[Dict[str, Any]]:
    """
    选取与当前问题最相关的知识点（只挑一个，避免把整份画像塞进提示词）。

    匹配方式：知识点名称出现在问题里（含别名），否则退化为"最近作答涉及的知识点"。
    """
    from services import mastery_service, quiz_service
    from services.learning_data import normalize_kp_name

    question_norm = normalize_kp_name(question or "")
    try:
        points = mastery_service.list_mastery(user_id, subject_id)
    except Exception as e:
        logger.debug(f"读取掌握度失败: {e}")
        points = []

    if question_norm:
        for p in points:
            name = normalize_kp_name(p.get("knowledge_point") or "")
            if name and (name in question_norm or question_norm in name):
                return p

    # 退化：最近一次作答涉及的知识点（"继续上次的学习"）
    try:
        recent = quiz_service.list_attempts(user_id, subject_id, None, 5)
    except Exception:
        recent = []
    for a in recent:
        kp_id = a.get("knowledge_point_id")
        if not kp_id:
            continue
        for p in points:
            if p.get("knowledge_point_id") == kp_id:
                return p
    return None


def _recent_mistakes(user_id: str, subject_id: str,
                     knowledge_point_id: Optional[str], limit: int = 3) -> List[Dict[str, Any]]:
    """最近错误（不含答案，避免把答案塞进提示词导致"背答案"）"""
    try:
        from services import quiz_service
        wrong = quiz_service.list_wrong_questions(user_id, subject_id)
    except Exception as e:
        logger.debug(f"读取错题失败: {e}")
        return []

    picked = []
    for w in wrong:
        if knowledge_point_id and w.get("knowledge_point_id") != knowledge_point_id:
            continue
        picked.append({
            "stem": _truncate(w.get("stem") or "", 60),
            "your_answer": _truncate(w.get("user_answer_text") or w.get("user_answer") or "", 40),
            "attempt_count": w.get("attempt_count"),
        })
        if len(picked) >= limit:
            break
    return picked


def _current_task(user_id: str, subject_id: str,
                  knowledge_point_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """当前任务与时间压力（当天到期 / 逾期 / 计划截止）"""
    try:
        from services import learning_service
        buckets = learning_service.get_task_buckets(user_id, subject_id)
    except Exception as e:
        logger.debug(f"读取任务失败: {e}")
        return None

    today = buckets.get("today") or []
    overdue = buckets.get("overdue") or []

    related = None
    for t in today + overdue:
        if knowledge_point_id and t.get("knowledge_point_id") == knowledge_point_id:
            related = t
            break

    plans = []
    try:
        plans = learning_service.list_plans(subject_id, user_id=user_id)
    except Exception:
        plans = []
    active = next((p for p in plans if p.get("status") == "ACTIVE"), None)

    if not (related or today or overdue or active):
        return None
    return {
        "today_count": len(today),
        "overdue_count": len(overdue),
        "related_task": ({"title": related.get("title"),
                          "task_type": related.get("task_type"),
                          "planned_date": related.get("planned_date"),
                          "status": related.get("status")} if related else None),
        "plan_deadline": (active or {}).get("end_date"),
        "plan_goal": (active or {}).get("goal"),
    }


def build_teaching_context(user_id: Optional[str], subject_id: Optional[str],
                           question: str = "",
                           detail_level: str = "concise") -> Dict[str, Any]:
    """
    构建教学上下文（**画像不足时返回空 block，走普通教学**）。
    """
    result: Dict[str, Any] = {
        "prompt_block": "",
        "teaching_mode": MODE_STANDARD,
        "reasons": [],
        "signals": {},
        "budget": dict(BUDGET),
    }
    if not user_id or not subject_id:
        result["reasons"].append("无用户/学科信息，使用普通教学方式")
        return result

    # 隐私保护（2026-10-03，见交付报告"风险 A"）：`/api/chat`、`/api/chat/stream`
    # 在**没有令牌**时会回退到共享的 legacy 账号（api/deps.LEGACY_USER ←
    # auth_service.LEGACY_USERNAME）。若此时仍注入"该账号"的掌握度/记忆/错题/任务，
    # 等于把一个账号的学情泄露给未登录访问者。因此对 legacy 账号**不做个人化**：
    # 问答照常，只是提示词里不带任何个人学习状态。
    try:
        from services.auth_service import LEGACY_USERNAME
        if user_id == LEGACY_USERNAME:
            result["reasons"].append("未登录（legacy 共享账号）→ 不注入个人学习状态")
            return result
    except Exception as e:      # 常量导入失败不应影响教学上下文
        logger.debug(f"legacy 账号判断失败，按普通用户处理: {e}")

    prefs = _collect_preferences(user_id)
    memory = _collect_memory(user_id, subject_id)
    point = _match_knowledge_point(subject_id, question, user_id)

    signals: Dict[str, Any] = {}
    lines: List[str] = []

    # ---- 1) 相关知识点证据 ----
    if point:
        bd = point.get("evidence_breakdown") or {}
        latest = point.get("latest_view") or {}
        # 历史累计（用于复习与趋势）
        hist_wrong = bd.get("independent_wrong", 0)
        hist_correct = bd.get("independent_correct", 0)
        hist_sample = hist_wrong + hist_correct
        # 当前掌握口径与学生面板一致：每题最新一次有效作答（Design C）。
        # 2026-10-05 修复：此前这里读累计 mastery_level，出现"学生刚答错、面板已降到
        # 低分，Agent/讲解却仍按累计高分走"的口径分裂；没有最新证据时回退累计并标注 basis。
        latest_mastery = latest.get("latest_mastery_level")
        latest_wrong = latest.get("latest_independent_wrong")
        latest_correct = latest.get("latest_independent_correct")
        latest_sample = latest.get("latest_evidence_count")
        wrong = latest_wrong if latest_wrong is not None else hist_wrong
        correct = latest_correct if latest_correct is not None else hist_correct
        sample = latest_sample if latest_sample else hist_sample
        # 是否"跨天间隔复现"（而不是同一段时间内连续答对）。
        # 长期掌握只认跨天复现；密集答对（fast path）不能被说成"已通过间隔复现"。
        from services import mastery_service
        first_at, last_at = point.get("first_correct_at"), point.get("last_correct_at")
        spaced_review = mastery_service.is_spaced_consolidation(first_at, last_at)
        span_days = mastery_service._span_days(first_at, last_at)
        signals = {
            "knowledge_point": point.get("knowledge_point"),
            "knowledge_point_id": point.get("knowledge_point_id"),
            # 当前（每题最新有效作答，与学生面板一致）
            "mastery_level": (latest_mastery if latest_mastery is not None
                              else point.get("mastery_level")),
            "mastery_basis": "每题最新一次有效作答（与学生面板一致）",
            "latest_mastery_level": latest_mastery,
            "latest_independent_correct": correct,
            "latest_independent_wrong": wrong,
            "latest_evidence_count": sample,
            # 兼容旧字段：当前口径
            "independent_correct": correct,
            "independent_wrong": wrong,
            "sample_size": sample,
            # 历史累计（勿当作"当前掌握"）
            "history_mastery_level": point.get("mastery_level"),
            "history_independent_correct": hist_correct,
            "history_independent_wrong": hist_wrong,
            "history_sample_size": hist_sample,
            "mastery_status": point.get("mastery_status"),
            "consolidated": point.get("consolidated"),
            "correct_span_days": span_days,
            "spaced_review": spaced_review,
            "review_wrong": bd.get("review_wrong", 0),
        }

    kp_id = (point or {}).get("knowledge_point_id")

    # ---- 2) 最近错误 ----
    mistakes = _recent_mistakes(user_id, subject_id, kp_id)
    signals["recent_mistakes"] = mistakes

    # ---- 3) 当前任务与时间 ----
    task = _current_task(user_id, subject_id, kp_id)
    signals["task"] = task

    # ---- 4) 判定教学策略（可解释，口径 = 每题最新有效作答） ----
    sample = signals.get("sample_size") or 0
    wrong = signals.get("independent_wrong") or 0
    correct = signals.get("independent_correct") or 0
    mastery = signals.get("mastery_level")
    hist_correct = signals.get("history_independent_correct") or 0
    hist_wrong = signals.get("history_independent_wrong") or 0
    hist_sample = signals.get("history_sample_size") or 0

    mode = MODE_STANDARD
    # 判据放在"当前口径"上：刚发生独立答错就进入基础优先（小步练习），
    # 而不是被历史累计的高分掩盖。样本为 0 时不预设水平。
    if sample == 0:
        result["reasons"].append(
            "该知识点尚无作答证据 → 使用常规教学，不预设学生水平")
    elif wrong >= FOUNDATION_MIN_WRONG and (mastery is None or mastery < ADVANCED_MASTERY):
        mode = MODE_FOUNDATION
        result["reasons"].append(
            f"按每题最新有效作答：最近有 {wrong} 次独立答错、{correct} 次独立答对"
            f"（历史累计对 {hist_correct}/错 {hist_wrong}）→ 以基础讲解与小步练习为主")
    elif (sample >= ADVANCED_MIN_SAMPLE and mastery is not None
          and mastery >= ADVANCED_MASTERY and signals.get("consolidated")
          and signals.get("spaced_review")):
        mode = MODE_ADVANCED
        result["reasons"].append(
            f"该知识点当前掌握估计 {round(mastery * 100)}% 且已跨天间隔复现"
            f"（每题最新有效作答 {sample} 次）→ 可进入综合练习与迁移应用")
    elif (signals.get("consolidated") and mastery is not None
          and mastery >= ADVANCED_MASTERY):
        # 密集答对（同一段时间内）不足以判定长期掌握，保持常规教学并如实说明
        result["reasons"].append(
            f"已连续答对（每题最新有效作答 {sample} 次，掌握估计 "
            f"{round(mastery * 100)}%），但尚未跨天间隔复现 → 仍按常规讲解，暂不认定为长期掌握")
    elif mastery is not None:
        result["reasons"].append(
            f"按每题最新有效作答：当前掌握估计 {round(mastery * 100)}%"
            f"（独立作答 {sample} 次，尚未间隔复现）→ 常规讲解 + 针对性练习")
    result["teaching_mode"] = mode

    # ---- 4.1) 一句学生可感知的变化说明（只依据真实记录；证据为 0 时不宣称） ----
    kp_name = signals.get("knowledge_point")
    if kp_name and sample > 0:
        # 说明只引用"知识点 + 真实独立答错次数"：题目的知识点标签由出题环节给出，
        # 可能与知识点语义不完全对应，直接引用题干会造成"说明与题目不匹配"。
        if mode == MODE_FOUNDATION:
            result["adaptation_note"] = (
                f"你上次在「{kp_name}」上有 {wrong} 次独立答错，"
                "这次先用基础讲解并针对这些错题做小步练习。")
        elif mode == MODE_ADVANCED:
            result["adaptation_note"] = (
                f"你在「{kp_name}」上已有 {correct} 次独立答对并通过跨天复现，这次进入综合练习。")
        elif wrong >= 1:
            # 有真实独立答错但掌握估计还不低：仍然针对这些错题做小步复习
            result["adaptation_note"] = (
                f"你上次在「{kp_name}」上有 {wrong} 次独立答错，"
                "这次先复习这些错题，再做同类练习。")
    # 结构化信号随结果一起返回（前端展示与自动化验收都要用）
    result["signals"] = signals

    # ---- 4.5) 记录"本次实际用上了哪些依据" ----
    # 用途：前端展示"为什么这样安排"，以及自动化验收核查"画像是否真的接入了教学"。
    # 注意只记录**被采用**的信息，不记录模型内部推理过程。
    result["applied"] = {
        "teaching_mode": mode,
        "preferences": list(prefs),
        "memory": list(memory),
        "knowledge_point": signals.get("knowledge_point"),
        "recent_mistakes": len(mistakes),
        "has_task_context": bool(task),
    }

    if task and task.get("overdue_count"):
        result["reasons"].append(
            f"有 {task['overdue_count']} 个逾期任务 → 回答会控制篇幅、优先解决当前卡点")
    if detail_level == "expanded":
        result["reasons"].append("本次提问要求详细讲解 → 允许展开推导")

    # ---- 5) 组装提示词块（按预算截断） ----
    if prefs:
        lines.append("【学生明确设置（优先级最高）】\n" + "\n".join(
            f"- {_truncate(p, BUDGET['preferences'] // max(1, len(prefs)))}" for p in prefs[:3]))

    if sample > 0:
        ev = (f"- 相关知识点：{signals.get('knowledge_point')}；"
              f"每题最新有效作答 {sample} 次（对 {correct} / 错 {wrong}）")
        if mastery is not None:
            ev += f"；当前掌握估计 {round(mastery * 100)}%（暂估，非结论）"
            ev += ("；已跨天间隔复现"
                   if (signals.get("consolidated") and signals.get("spaced_review"))
                   else "；尚未跨天间隔复现（连续答对不视为长期掌握）")
        if hist_sample:
            ev += (f"；历史累计独立作答 {hist_sample} 次"
                   f"（对 {hist_correct} / 错 {hist_wrong}，仅用于复习与趋势）")
        lines.append("【该知识点的真实作答证据】\n" + _truncate(ev, BUDGET["evidence"]))

    if mistakes:
        mk = "\n".join(
            f"- 错题（作答 {m['attempt_count']} 次）：{m['stem']}"
            + (f"；学生曾答：{m['your_answer']}" if m.get("your_answer") else "")
            for m in mistakes)
        lines.append("【最近错误（不要直接给答案，先引导）】\n" + _truncate(mk, BUDGET["mistakes"]))

    if task:
        tk = []
        if task.get("related_task"):
            rt = task["related_task"]
            tk.append(f"- 当前相关任务：{rt['title']}（{rt['task_type']}，计划 {rt['planned_date']}）")
        if task.get("today_count"):
            tk.append(f"- 今天还有 {task['today_count']} 个任务待完成")
        if task.get("overdue_count"):
            tk.append(f"- 有 {task['overdue_count']} 个任务已逾期")
        if task.get("plan_deadline"):
            tk.append(f"- 当前计划截止：{task['plan_deadline']}")
        if tk:
            lines.append("【当前进度与时间】\n" + _truncate("\n".join(tk), BUDGET["task"]))

    if memory:
        lines.append("【学习记忆（含来源；可被学生更正或关闭）】\n"
                     + _truncate("\n".join(f"- {m}" for m in memory), BUDGET["memory"]))

    if lines:
        style = {
            MODE_FOUNDATION: ("教学策略：以**基础**为主——先用直白语言解释概念与为什么，"
                              "再给 1 个小步骤示例，最后给 1 个简单检查问题；避免直接跳到综合题。"),
            MODE_ADVANCED: ("教学策略：学生在该知识点已有扎实证据——可以直接进入**综合练习与迁移**，"
                            "（例如多步推理、对比、易错变式），不必重复基础定义。"),
            MODE_STANDARD: "教学策略：常规讲解，先结论后要点；如信息不足不要臆测学生水平。",
        }[mode]
        block = (
            "【学习者画像（真实学习记录，仅供参考；信息不足时按普通教学处理，"
            "不要编造学生的水平或经历）】\n"
            + "\n".join(lines)
            + f"\n{style}"
        )
        result["prompt_block"] = _truncate(block, BUDGET["total_chars"])
        result["budget"]["used_chars"] = len(result["prompt_block"])
    else:
        result["reasons"].append("该学生在该学科暂无可用记录 → 使用普通教学方式")
        result["budget"]["used_chars"] = 0

    return result


__all__ = [
    "MODE_FOUNDATION",
    "MODE_STANDARD",
    "MODE_ADVANCED",
    "BUDGET",
    "build_teaching_context",
]
