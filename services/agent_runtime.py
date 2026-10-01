"""
受约束的学习 Agent 运行时（阶段二）

目标场景（用户原话）：
    "我周五要考这一章，每天只有半小时，帮我复习。"
系统需要：取课程资料 → 读真实学习记录 → 证据不足时安排少量诊断 →
给出时间预算内的计划 → 带学生进入讲解与练习 → 根据结果提出调整 → 解释为什么这样安排。

设计约束（对应本轮要求）：
- **复用已有业务服务**提供工具，不另起一套业务逻辑
- 工具只使用**后端当前用户身份**（调用方传入 user_id，工具不接受客户端指定的用户）
- 权限、参数、日期与业务规则由**程序**校验，不交给模型保证
- 可检查的**状态机**流程，限制工具调用次数 / 执行时间 / 失败重试
- 记录运行的**状态、工具调用与结果摘要**（不记录模型内部思维过程）
- 支持取消与失败恢复

"自我学习"在本阶段的含义：更新用户记忆、学习状态与教学策略，
不是在训练模型参数。
"""

from config.path_config import get_data_dir

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from utils.logger import logger

_DATA_DIR = str(get_data_dir())
DB_PATH = os.path.join(_DATA_DIR, "learning.db")

# ---- 运行限额（程序强制，不依赖模型自觉） ----
LIMITS = {
    "max_steps": 12,           # 最多工具调用次数
    "max_seconds": 180,        # 单次运行最长执行时间
    "max_retries_per_step": 1, # 单个工具失败重试次数
    "max_llm_calls": 6,        # 最多模型调用次数
}

# ---- 运行状态机 ----
STATE_PENDING = "PENDING"
STATE_RUNNING = "RUNNING"
STATE_SUCCEEDED = "SUCCEEDED"
STATE_FAILED = "FAILED"
STATE_CANCELLED = "CANCELLED"
STATE_TIMEOUT = "TIMEOUT"

TERMINAL_STATES = (STATE_SUCCEEDED, STATE_FAILED, STATE_CANCELLED, STATE_TIMEOUT)

_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    subject_id TEXT,
    goal TEXT,
    intent TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    steps INTEGER DEFAULT 0,
    llm_calls INTEGER DEFAULT 0,
    plan_json TEXT DEFAULT '{}',
    result_json TEXT DEFAULT '{}',
    error TEXT,
    cancel_requested INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
)
"""

_STEPS_DDL = """
CREATE TABLE IF NOT EXISTS agent_run_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT DEFAULT '{}',
    status TEXT NOT NULL,
    summary TEXT,
    error TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
)
"""


class AgentRuntimeError(Exception):
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
    conn.execute(_RUNS_DDL)
    conn.execute(_STEPS_DDL)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# =====================================================================
# 工具集：每个工具都是"已有业务服务"的薄封装，只接受 user_id 参数
# =====================================================================

def _tool_search_material(user_id: str, subject_id: str, query: str) -> Dict[str, Any]:
    """检索课程资料（RAG），返回片段与来源。只读。"""
    from services.ai_service import retrieve_and_build_context
    ctx = retrieve_and_build_context(query, subject_id, top_k=5)
    return {
        "context_used": ctx.get("context_used", False),
        "evidence_count": len(ctx.get("sources") or []),
        "sources": (ctx.get("sources") or [])[:5],
        "snippet": (ctx.get("context") or "")[:600],
    }


def _tool_get_learning_state(user_id: str, subject_id: str, **_) -> Dict[str, Any]:
    """读取学习状态：掌握度分布、错题、待办任务、待复习、约束与预算。只读。"""
    from services import learning_service, mastery_service, quiz_service, review_service
    from services import plan_adjustment

    summary = mastery_service.summarize_mastery(user_id, subject_id)
    points = mastery_service.list_mastery(user_id, subject_id)
    buckets = learning_service.get_task_buckets(user_id, subject_id)
    wrong = quiz_service.list_wrong_questions(user_id, subject_id)
    reviews = review_service.list_review_tasks(user_id, subject_id, "PENDING")
    budget = plan_adjustment.analyze_plan_budget(user_id, subject_id)

    # 只挑证据相关的知识点，避免把整份画像塞给模型
    weak = [p for p in points if p["mastery_level"] is not None and p["mastery_level"] < 0.4]
    advanced = [p for p in points if p.get("consolidated")]
    return {
        "mastery": {
            "distribution": summary["distribution"],
            "accuracy": summary["accuracy"],
            "accuracy_sample_size": summary["accuracy_sample_size"],
        },
        "weak_points": [{"knowledge_point": p["knowledge_point"],
                         "mastery_level": p["mastery_level"],
                         "sample_size": p["sample_size"]} for p in weak[:5]],
        "consolidated_points": [p["knowledge_point"] for p in advanced[:5]],
        "no_evidence_points": [p["knowledge_point"] for p in points
                               if p["mastery_level"] is None][:8],
        "wrong_count": len(wrong),
        "wrong_points": [{"knowledge_point": w.get("knowledge_point"),
                          "attempt_count": w.get("attempt_count")} for w in wrong[:5]],
        "pending_reviews": [{"knowledge_point_name": r["knowledge_point_name"],
                             "due_date": r["due_date"]} for r in reviews[:5]],
        "today_tasks": len(buckets["today"]),
        "overdue_tasks": len(buckets["overdue"]),
        "budget": {
            "fits": budget["fits"],
            "conflicts": budget["conflicts"],
            "suggestions": budget["suggestions"],
            "daily_minutes": budget["daily_minutes"],
            "deadline": budget["deadline"],
            "remaining_minutes": budget["remaining_minutes"],
            "capacity_minutes": budget["capacity_minutes"],
        },
    }


def _tool_generate_note(user_id: str, subject_id: str, topic: str,
                        model: Optional[str] = None, **_) -> Dict[str, Any]:
    """生成学习资料（五段式笔记）。会调用模型。"""
    from services.note_service import generate_smart_note
    note = generate_smart_note(subject_id=subject_id, topic=topic, model=model)
    return {
        "title": note.get("title"),
        "context_used": note.get("context_used"),
        "evidence_count": note.get("evidence_count"),
        "concept": (note.get("concept") or {}).get("content", "")[:400],
        "quiz_count": len(note.get("quiz") or []),
        "quiz": (note.get("quiz") or [])[:3],
    }


def _tool_generate_diagnostic(user_id: str, subject_id: str, topic: str,
                              count: int = 3, model: Optional[str] = None,
                              **_) -> Dict[str, Any]:
    """
    生成少量诊断题（**不含答案**，答案由后端题库保存，学生在自测中作答）。

    count 由程序限制在 1-5，避免一次生成过多题目。
    """
    from services.note_service import generate_quiz
    count = max(1, min(5, int(count or 3)))
    quiz = generate_quiz(subject_id=subject_id, topic=topic, count=count, model=model)
    items = quiz.get("quiz") or []
    return {
        "question_count": len(items),
        "questions": [{"id": q.get("id"), "question": q.get("question"),
                       "type": q.get("type"), "options": q.get("options")}
                      for q in items],
        "note": "题目已入库，学生提交作答后由后端判分（答案不会提前下发）",
    }


def _tool_analyze_budget(user_id: str, subject_id: str, deadline: Optional[str] = None,
                         daily_minutes: Optional[int] = None, **_) -> Dict[str, Any]:
    """
    设置约束并检查时间预算（日期合法性由程序校验）。

    这是"我周五要考这一章，每天只有半小时"落地的地方：
    截止日期与每日预算写进约束表，随后由程序判定能否完成。
    """
    from services import plan_adjustment

    if deadline or daily_minutes is not None:
        plan_adjustment.set_constraints(
            user_id, subject_id, deadline=deadline, daily_minutes=daily_minutes)
    return plan_adjustment.analyze_plan_budget(user_id, subject_id)


def _tool_create_plan(user_id: str, subject_id: str, goal: str,
                      model: Optional[str] = None, **_) -> Dict[str, Any]:
    """
    生成学习计划（会调用模型 + RAG），并把任务日期约束在截止日期内。

    生成后**用程序检查预算**：超预算时在结果里给出明确冲突与缩减建议。
    """
    from services import learning_service, plan_adjustment
    plan = learning_service.generate_plan(subject_id=subject_id, goal=goal,
                                          model=model, user_id=user_id)
    budget = plan_adjustment.analyze_plan_budget(user_id, subject_id)
    return {
        "plan_id": plan.get("id"),
        "title": plan.get("title"),
        "task_count": len(plan.get("tasks") or []),
        "tasks": [{"title": t.get("title"), "planned_date": t.get("planned_date"),
                   "task_type": t.get("task_type"),
                   "estimated_minutes": t.get("estimated_minutes")}
                  for t in (plan.get("tasks") or [])[:12]],
        "budget_fits": budget["fits"],
        "budget_conflicts": budget["conflicts"],
        "budget_suggestions": budget["suggestions"],
    }


def _tool_schedule_review(user_id: str, subject_id: str, knowledge_point: str, **_) -> Dict[str, Any]:
    """为某个知识点安排复习（复用错题联动，同知识点自动合并）"""
    from services import review_service
    from services.learning_data import ensure_knowledge_point
    kp_id = ensure_knowledge_point(subject_id, knowledge_point, source="agent")
    if not kp_id:
        raise AgentRuntimeError("知识点名称无效", "invalid_request")
    res = review_service.ensure_review_task(
        user_id=user_id, subject_id=subject_id, knowledge_point_id=kp_id,
        knowledge_point_name=knowledge_point,
        reason="学习助手根据当前目标与错题安排")
    return res


def _task_fingerprint(user_id: str, subject_id: str) -> str:
    """
    当前待办任务 + 生效截止的指纹，用作调整建议的幂等键。

    同一批任务 + 同一截止日期 → 同一指纹 → 不会因为重复运行而反复创建建议。
    """
    import hashlib
    from services import learning_service, plan_adjustment

    tasks = [t for t in learning_service.list_tasks(user_id, subject_id, None, None, 1000)
             if t.get("status") != "COMPLETED"]
    keys = sorted(f"{t['id']}@{t.get('planned_date')}@{t.get('estimated_minutes')}"
                  for t in tasks)
    constraints = plan_adjustment.get_constraints(user_id, subject_id)
    raw = "|".join(keys) + f"#deadline={constraints.get('effective_deadline')}"
    return "adjfp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _tool_propose_adjustment(user_id: str, subject_id: str,
                             idempotency_key: Optional[str] = None, **_) -> Dict[str, Any]:
    """
    提出计划调整建议（**只生成方案，不直接改数据**；重大调整需学生确认）。

    幂等：未显式给出 idempotency_key 时，用"当前待办任务 + 生效截止"的指纹，
    因此重复运行学习助手不会反复创建同一批建议。
    """
    from services import plan_adjustment
    key = idempotency_key or _task_fingerprint(user_id, subject_id)
    return plan_adjustment.propose_schedule(
        user_id, subject_id, source="agent", idempotency_key=key)


def _tool_explain_plan(user_id: str, subject_id: str, **_) -> Dict[str, Any]:
    """读取当前计划与约束，用于向学生解释"为什么这样安排"。只读。"""
    from services import learning_service, plan_adjustment
    constraints = plan_adjustment.get_constraints(user_id, subject_id)
    plans = learning_service.list_plans(subject_id, user_id=user_id)
    active = next((p for p in plans if p.get("status") == "ACTIVE"), None)
    budget = plan_adjustment.analyze_plan_budget(user_id, subject_id)
    return {
        "constraints": constraints,
        "goal": (active or {}).get("goal"),
        "plan_title": (active or {}).get("title"),
        "budget": {"fits": budget["fits"], "conflicts": budget["conflicts"],
                   "suggestions": budget["suggestions"],
                   "daily_minutes": budget["daily_minutes"],
                   "deadline": budget["deadline"],
                   "days_left": budget["days_left"],
                   "remaining_minutes": budget["remaining_minutes"],
                   "capacity_minutes": budget["capacity_minutes"]},
    }


# 工具注册表：名称 → (函数, 说明, 是否调用模型, 是否写数据)
TOOLS: Dict[str, Dict[str, Any]] = {
    "search_material": {"fn": _tool_search_material, "llm": False, "write": False,
                        "label": "检索课程资料"},
    "get_learning_state": {"fn": _tool_get_learning_state, "llm": False, "write": False,
                           "label": "查看学习记录"},
    "analyze_budget": {"fn": _tool_analyze_budget, "llm": False, "write": True,
                       "label": "检查时间预算"},
    "generate_note": {"fn": _tool_generate_note, "llm": True, "write": True,
                      "label": "生成讲解资料"},
    "generate_diagnostic": {"fn": _tool_generate_diagnostic, "llm": True, "write": True,
                            "label": "生成诊断题"},
    "create_plan": {"fn": _tool_create_plan, "llm": True, "write": True,
                    "label": "生成学习计划"},
    "schedule_review": {"fn": _tool_schedule_review, "llm": False, "write": True,
                        "label": "安排复习"},
    "propose_adjustment": {"fn": _tool_propose_adjustment, "llm": False, "write": True,
                           "label": "提出调整建议"},
    "explain_plan": {"fn": _tool_explain_plan, "llm": False, "write": False,
                     "label": "解释安排依据"},
}


# =====================================================================
# 运行记录
# =====================================================================

def create_run(user_id: str, subject_id: Optional[str], goal: str,
               intent: str = "study") -> str:
    rid = _new_id("run")
    now = _now()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO agent_runs
               (id, user_id, subject_id, goal, intent, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (rid, user_id, subject_id, goal, intent, STATE_PENDING, now, now))
        conn.commit()
    finally:
        conn.close()
    return rid


def _update_run(run_id: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn = _get_db()
    try:
        conn.execute(f"UPDATE agent_runs SET {sets} WHERE id=?",
                     (*fields.values(), run_id))
        conn.commit()
    finally:
        conn.close()


def record_step(run_id: str, seq: int, tool: str, args: Dict[str, Any],
                status: str, summary: Optional[str] = None,
                error: Optional[str] = None, duration_ms: Optional[int] = None) -> None:
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO agent_run_steps
               (id, run_id, seq, tool, args_json, status, summary, error,
                duration_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (_new_id("step"), run_id, seq, tool,
             json.dumps(_safe_args(args), ensure_ascii=False),
             status, summary, error, duration_ms, _now()))
        conn.commit()
    finally:
        conn.close()


def _safe_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """只保留可展示的参数，避免把大对象写进运行记录"""
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v if not isinstance(v, str) or len(v) <= 300 else v[:300] + "…"
    return out


def request_cancel(run_id: str, user_id: str) -> bool:
    """请求取消一次运行（下次检查点生效，已完成的步骤保留）"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if not row or row["user_id"] != user_id:
            return False
        if row["status"] in TERMINAL_STATES:
            return False
        conn.execute("UPDATE agent_runs SET cancel_requested=1, updated_at=? WHERE id=?",
                     (_now(), run_id))
        conn.commit()
        return True
    finally:
        conn.close()


def _is_cancelled(run_id: str) -> bool:
    conn = _get_db()
    try:
        row = conn.execute("SELECT cancel_requested, status FROM agent_runs WHERE id=?",
                           (run_id,)).fetchone()
        return bool(row and (row["cancel_requested"] or row["status"] == STATE_CANCELLED))
    finally:
        conn.close()


def get_run(run_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """读取运行状态 + 工具调用摘要（不包含模型内部思维过程）"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        if user_id and row["user_id"] != user_id:
            return None
        steps = conn.execute(
            """SELECT seq, tool, args_json, status, summary, error, duration_ms, created_at
               FROM agent_run_steps WHERE run_id=? ORDER BY seq""",
            (run_id,)).fetchall()
        data = dict(row)
        data["cancel_requested"] = bool(data.get("cancel_requested"))
        try:
            data["plan"] = json.loads(data.pop("plan_json") or "{}")
        except (ValueError, TypeError):
            data["plan"] = {}
        try:
            data["result"] = json.loads(data.pop("result_json") or "{}")
        except (ValueError, TypeError):
            data["result"] = {}
        data["steps_detail"] = [
            {**dict(s), "args": _loads(s["args_json"])} for s in steps
        ]
        for s in data["steps_detail"]:
            s.pop("args_json", None)
        return data
    finally:
        conn.close()


def _loads(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text or "{}")
    except (ValueError, TypeError):
        return {}


def list_runs(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT id, subject_id, goal, intent, status, steps, created_at,
                      finished_at, error
               FROM agent_runs WHERE user_id=? ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# =====================================================================
# 确定性编排（不依赖模型决定流程，模型只在工具内部被调用）
# =====================================================================

def plan_steps(goal: str, intent: str, state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    根据目标与当前状态生成**步骤计划**（状态机，可检查、可测试）。

    规则（程序决定，不是模型决定）：
    - 永远先读学习状态（决定后续是否需要诊断）
    - 目标提到考试/截止/时间 → 先设约束并检查预算
    - 完全没有证据的知识点较多 → 先出少量诊断题（不超过 5 道）
    - 有错题/薄弱点 → 安排复习
    - 需要讲解 → 生成资料
    - 最后：预算不合适才提调整建议，并给出解释依据
    """
    steps: List[Dict[str, Any]] = [{"tool": "get_learning_state", "args": {}}]

    text = (goal or "").lower()
    signals = _extract_goal_signals(goal or "")
    if signals.get("deadline") or signals.get("daily_minutes"):
        steps.append({"tool": "analyze_budget",
                      "args": {"deadline": signals.get("deadline"),
                               "daily_minutes": signals.get("daily_minutes")}})

    no_evidence = state.get("no_evidence_points") or []
    weak = state.get("weak_points") or []
    wrong = state.get("wrong_count") or 0

    topic = signals.get("topic") or (weak[0]["knowledge_point"] if weak else None) \
        or (no_evidence[0] if no_evidence else None)

    # 证据不足 → 少量诊断（上限 3 道）
    if no_evidence and not weak:
        steps.append({"tool": "generate_diagnostic",
                      "args": {"topic": topic or goal, "count": 3}})
    # 有薄弱/错题 → 复习
    if weak or wrong:
        kp = weak[0]["knowledge_point"] if weak else topic
        if kp:
            steps.append({"tool": "schedule_review", "args": {"knowledge_point": kp}})
    # 需要讲解
    if topic:
        steps.append({"tool": "generate_note", "args": {"topic": topic}})
    # 有明确目标 → 生成计划
    if intent == "plan" or signals.get("wants_plan"):
        steps.append({"tool": "create_plan", "args": {"goal": goal}})
    # 总是给出解释依据
    steps.append({"tool": "explain_plan", "args": {}})
    # 只在**确实存在冲突或逾期**时才提调整建议（避免无意义的"建议"）
    budget = state.get("budget") or {}
    if (not budget.get("fits", True)) or (state.get("overdue_tasks") or 0) > 0:
        steps.append({"tool": "propose_adjustment", "args": {}})

    return steps[:LIMITS["max_steps"]]


def _extract_goal_signals(goal: str) -> Dict[str, Any]:
    """
    从目标里提取日期/时长/主题（**程序解析**，不交给模型）。

    支持："周五"、"明天"、"后天"、"本周日"、"3天后"、"X月X日"、
    "每天半小时 / 30分钟 / 1小时"。
    """
    import re

    signals: Dict[str, Any] = {}
    text = goal or ""

    # 每天可用时长
    m = re.search(r"(?:每天|每日)?\s*(\d+(?:\.\d+)?)\s*(?:分钟|min)", text)
    if m:
        signals["daily_minutes"] = int(float(m.group(1)))
    else:
        m = re.search(r"(?:每天|每日)?\s*(\d+(?:\.\d+)?)\s*(?:个)?\s*小时", text)
        if m:
            signals["daily_minutes"] = int(float(m.group(1)) * 60)
        elif re.search(r"半小时|半个小时", text):
            signals["daily_minutes"] = 30

    # 截止日期
    today = date.today()
    weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    m = re.search(r"(?:本周|这周|周|星期)([一二三四五六日天])", text)
    if m:
        target = weekday_map[m.group(1)]
        delta = (target - today.weekday()) % 7
        if delta == 0:
            delta = 7  # "周五"在周五当天说 → 指下周
        signals["deadline"] = (today + timedelta(days=delta)).isoformat()
    elif "明天" in text:
        signals["deadline"] = (today + timedelta(days=1)).isoformat()
    elif "后天" in text:
        signals["deadline"] = (today + timedelta(days=2)).isoformat()
    else:
        m = re.search(r"(\d+)\s*天后", text)
        if m:
            signals["deadline"] = (today + timedelta(days=int(m.group(1)))).isoformat()
        else:
            m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)
            if m:
                try:
                    cand = date(today.year, int(m.group(1)), int(m.group(2)))
                    if cand < today:
                        cand = date(today.year + 1, int(m.group(1)), int(m.group(2)))
                    signals["deadline"] = cand.isoformat()
                except ValueError:
                    pass

    # 是否明确要"计划"
    if any(k in text for k in ("计划", "安排", "规划", "帮我复习", "复习完")):
        signals["wants_plan"] = True

    # 主题：去掉时间/时长描述后的名词短语（粗略但可解释）
    topic = re.sub(r"(我|要|得|必须|请|帮我|帮我安排|安排|计划|复习|考|考试|每天|每日|"
                   r"半小时|半个小时|\d+(?:\.\d+)?\s*(?:分钟|min|小时|个)?|周[一二三四五六日天]|"
                   r"星期[一二三四五六日天]|本周|这周|明天|后天|\d+天后|\d{1,2}月\d{1,2}[日号])",
                   " ", text)
    topic = re.sub(r"\s+", " ", topic).strip(" ，,。.、")
    if topic:
        signals["topic"] = topic[:40]
    return signals


def run_agent(user_id: str, subject_id: str, goal: str, intent: str = "study",
              cancel_check: Optional[Callable[[], bool]] = None,
              progress: Optional[Callable[[Dict[str, Any]], None]] = None,
              model: Optional[str] = None) -> Dict[str, Any]:
    """
    执行一次 Agent 运行（同步；由接口层放到线程池里跑）。

    - 每步都有：限额检查、取消检查、超时检查、失败重试
    - 每步结果写入 agent_run_steps（含耗时与摘要）
    - 任一步失败不会中断整轮：记录失败原因，继续能做的步骤（本地降级）
    - 最终状态：SUCCEEDED / FAILED / CANCELLED / TIMEOUT
    """
    run_id = create_run(user_id, subject_id, goal, intent)
    started = time.time()
    steps_done = 0
    llm_calls = 0
    results: Dict[str, Any] = {}
    errors: List[str] = []
    seq = 0

    _update_run(run_id, status=STATE_RUNNING)

    def _emit(tool: str, label: str, status: str, summary: str = "") -> None:
        if progress:
            try:
                progress({"run_id": run_id, "tool": tool, "label": label,
                          "status": status, "summary": summary,
                          "steps_done": steps_done})
            except Exception:
                pass

    try:
        # 第 0 步：读取学习状态（后续计划依赖它）
        seq += 1
        steps_done += 1
        _emit("get_learning_state", TOOLS["get_learning_state"]["label"], "running")
        state = _tool_get_learning_state(user_id, subject_id)
        results["learning_state"] = state
        record_step(run_id, seq, "get_learning_state", {"subject_id": subject_id},
                    "DONE", summary=_summarize_state(state), duration_ms=0)
        _emit("get_learning_state", TOOLS["get_learning_state"]["label"], "done",
              _summarize_state(state))

        steps = plan_steps(goal, intent, state)
        for step in steps:
            if step["tool"] == "get_learning_state":
                continue
            if steps_done >= LIMITS["max_steps"]:
                errors.append(f"达到单次运行工具调用上限（{LIMITS['max_steps']} 次），"
                              f"剩余步骤未执行")
                break
            elapsed = time.time() - started
            if elapsed > LIMITS["max_seconds"]:
                _update_run(run_id, status=STATE_TIMEOUT, steps=steps_done,
                            error=f"执行超时（>{LIMITS['max_seconds']}s）")
                _emit(step["tool"], TOOLS[step["tool"]]["label"], "timeout")
                return {**get_run(run_id, user_id), "results": results}
            if (cancel_check and cancel_check()) or _is_cancelled(run_id):
                _update_run(run_id, status=STATE_CANCELLED, steps=steps_done,
                            error="用户取消")
                _emit(step["tool"], TOOLS[step["tool"]]["label"], "cancelled")
                return {**get_run(run_id, user_id), "results": results}

            tool = step["tool"]
            meta = TOOLS.get(tool)
            if not meta:
                continue
            args = dict(step.get("args") or {})
            # 程序补齐身份与学科：模型不能指定 user_id
            args["user_id"] = user_id
            args["subject_id"] = subject_id
            # 模型由调用方（用户当前选择的模型）决定，工具不自行切换
            if meta["llm"] and model:
                args["model"] = model
            seq += 1
            steps_done += 1
            _emit(tool, meta["label"], "running")

            if meta["llm"] and llm_calls >= LIMITS["max_llm_calls"]:
                errors.append(f"模型调用次数达到上限（{LIMITS['max_llm_calls']} 次），"
                              f"跳过「{meta['label']}」")
                record_step(run_id, seq, tool, args, "SKIPPED",
                            error="模型调用次数达到上限")
                _emit(tool, meta["label"], "skipped", "模型调用次数达到上限")
                continue
            if meta["llm"]:
                llm_calls += 1

            t0 = time.time()
            last_error = None
            for attempt in range(LIMITS["max_retries_per_step"] + 1):
                try:
                    out = meta["fn"](**args)
                    results[tool] = out
                    dur = int((time.time() - t0) * 1000)
                    record_step(run_id, seq, tool, args, "DONE",
                                summary=_summarize_tool(tool, out), duration_ms=dur)
                    _emit(tool, meta["label"], "done", _summarize_tool(tool, out))
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    logger.warning(f"Agent 工具 {tool} 第 {attempt + 1} 次失败: {e}")
                    if attempt < LIMITS["max_retries_per_step"]:
                        time.sleep(0.2)
            if last_error is not None:
                dur = int((time.time() - t0) * 1000)
                msg = f"{type(last_error).__name__}: {last_error}"
                errors.append(f"「{meta['label']}」失败：{msg}")
                record_step(run_id, seq, tool, args, "FAILED", error=msg,
                            duration_ms=dur)
                _emit(tool, meta["label"], "failed", msg)

        status = STATE_SUCCEEDED if not errors else STATE_FAILED
        if errors and results.get("learning_state"):
            # 关键信息已拿到时，仍标记为"部分成功"但状态为 FAILED，
            # 便于前端提示"部分步骤未完成，可用手动操作"
            status = STATE_FAILED
        _update_run(run_id, status=status, steps=steps_done, llm_calls=llm_calls,
                    result_json=json.dumps({"errors": errors}, ensure_ascii=False),
                    error="; ".join(errors) if errors else None,
                    finished_at=_now())
        return {**get_run(run_id, user_id), "results": results}

    except Exception as e:
        logger.error(f"Agent 运行异常: {e}", exc_info=True)
        _update_run(run_id, status=STATE_FAILED, steps=steps_done,
                    error=f"{type(e).__name__}: {e}", finished_at=_now())
        return {**get_run(run_id, user_id), "results": results}


def _summarize_state(state: Dict[str, Any]) -> str:
    m = state.get("mastery") or {}
    dist = m.get("distribution") or {}
    return (f"知识点 {sum(dist.values())} 个（已巩固 {dist.get('strong', 0)}、"
            f"薄弱 {dist.get('weak', 0)}、待评估 {dist.get('pending', 0)}）；"
            f"错题 {state.get('wrong_count', 0)} 道；待复习 {len(state.get('pending_reviews') or [])} 项")


def _summarize_tool(tool: str, out: Dict[str, Any]) -> str:
    if not isinstance(out, dict):
        return str(out)[:120]
    if tool == "analyze_budget":
        return (f"每天 {out.get('daily_minutes')} 分钟，截止 {out.get('deadline')}，"
                f"剩余 {out.get('remaining_minutes')} 分钟 / 可用 "
                f"{out.get('capacity_minutes')} 分钟，"
                + ("预算充足" if out.get("fits") else f"存在 {len(out.get('conflicts') or [])} 项冲突"))
    if tool == "generate_diagnostic":
        return f"生成 {out.get('question_count', 0)} 道诊断题（答案不下发）"
    if tool == "generate_note":
        return f"生成资料《{out.get('title')}》（证据 {out.get('evidence_count', 0)} 条）"
    if tool == "create_plan":
        return (f"生成计划《{out.get('title')}》，{out.get('task_count', 0)} 个任务，"
                + ("预算充足" if out.get("budget_fits") else "预算不足需缩减范围"))
    if tool == "schedule_review":
        return f"复习任务（到期 {out.get('due_date')}，合并次数 {out.get('merged_count')}）"
    if tool == "propose_adjustment":
        return out.get("summary") or "已生成调整建议"
    if tool == "explain_plan":
        b = out.get("budget") or {}
        return (f"当前计划《{out.get('plan_title')}》；每天 {b.get('daily_minutes')} 分钟，"
                f"截止 {b.get('deadline')}")
    return json.dumps(out, ensure_ascii=False)[:150]


__all__ = [
    "AgentRuntimeError",
    "LIMITS",
    "STATE_PENDING",
    "STATE_RUNNING",
    "STATE_SUCCEEDED",
    "STATE_FAILED",
    "STATE_CANCELLED",
    "STATE_TIMEOUT",
    "TERMINAL_STATES",
    "TOOLS",
    "create_run",
    "record_step",
    "request_cancel",
    "get_run",
    "list_runs",
    "plan_steps",
    "run_agent",
]
