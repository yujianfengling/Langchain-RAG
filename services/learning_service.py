"""
学习计划基础系统（V2.5 Task D）

LearningPlan + LearningTask 持久化（SQLite），为 V2.6 主动学习 Agent 提供数据基础。

设计：
- SQLite 标准库（sqlite3），轻量、无额外依赖
- LearningPlan: id, subject_id, title, goal, start_date, end_date, status, created_at, updated_at
- LearningTask: id, plan_id, knowledge_point, task_type(READ/REVIEW/QUIZ/PRACTICE),
  title, description, priority, estimated_minutes, status(TODO/IN_PROGRESS/COMPLETED),
  completed_at, actual_minutes, created_at
- AI 生成计划：用户目标 → RAG 知识点 → LLM 结构化生成 → 保存

V2.6 预留：status/completed_at/estimated_minutes/actual_minutes 已具备，供 Execute/Reflect/Replan 使用。
"""

from config.path_config import get_data_dir
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, date
from typing import List, Dict, Any, Optional

from utils.logger import logger
from services.ai_service import call_llm, AIServiceError, retrieve_and_build_context
from services.kb_guard import require_kb_ready, KBError


class LearningServiceError(Exception):
    """学习计划服务异常（携带 error_type）"""

    def __init__(self, message: str, error_type: str = "unknown"):
        self.error_type = error_type
        self.message = message
        super().__init__(message)

# 数据库文件位置（统一数据根目录，支持 EDU_DATA_DIR 覆盖）
_DATA_DIR = str(get_data_dir())
DB_PATH = os.path.join(_DATA_DIR, "learning.db")

# 枚举
TASK_TYPES = ("READ", "REVIEW", "QUIZ", "PRACTICE")
TASK_STATUSES = ("TODO", "IN_PROGRESS", "COMPLETED")
PLAN_STATUSES = ("ACTIVE", "COMPLETED", "ARCHIVED")

# 计划生成 Prompt（严格 JSON）
PLAN_PROMPT = """你是一名学习规划助手。请基于用户的学习目标与知识库知识点，生成一份分天的学习计划。

## 用户目标
{goal}

## 知识库相关知识点（供参考，可补充但不超过目标范围）
{context}

## 输出要求（严格 JSON，不要输出任何其他内容）
{{
  "title": "计划标题",
  "goal": "目标复述",
  "days": [
    {{
      "day": 1,
      "tasks": [
        {{
          "knowledge_point": "知识点",
          "task_type": "READ | REVIEW | QUIZ | PRACTICE",
          "title": "任务标题",
          "description": "任务描述",
          "estimated_minutes": 30
        }}
      ]
    }}
  ]
}}

## 规则
1. 任务总数控制在 5-12 个之间，分布在 1-5 天内。
2. 每天 1-3 个任务，task_type 覆盖 READ/REVIEW/QUIZ/PRACTICE 中至少两种。
3. 任务基于知识库知识点，尽量贴近用户目标。
4. estimated_minutes 为合理的分钟数估计。
"""




def _get_db() -> sqlite3.Connection:
    """获取数据库连接（每次新建，thread-safe）"""
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # V2.5.2：并发写等待（默认5s），避免 database is locked
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init_db() -> None:
    """初始化表结构（幂等）"""
    conn = _get_db()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS learning_plans (
            id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            title TEXT NOT NULL,
            goal TEXT,
            start_date TEXT,
            end_date TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learning_tasks (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            knowledge_point TEXT,
            task_type TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            priority INTEGER DEFAULT 3,
            estimated_minutes INTEGER DEFAULT 30,
            status TEXT NOT NULL DEFAULT 'TODO',
            completed_at TEXT,
            actual_minutes INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (plan_id) REFERENCES learning_plans(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_plan ON learning_tasks(plan_id);
        -- 任务改期留痕（与 learning_tasks 同库；不在 learning_loop.db 中创建）
        CREATE TABLE IF NOT EXISTS task_adjustments (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            user_id TEXT,
            old_date TEXT,
            new_date TEXT,
            reason TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_adjust_task ON task_adjustments(task_id, created_at);
        """)
        conn.commit()

        # 阶段一新增列（用户归属/学科/知识点稳定 ID/计划日期等）：
        # 与 learning_data 共用列定义，保证"新建库"和"历史库迁移"得到同一结构，
        # 避免依赖应用启动流程（脚本/测试/独立调用同样正确）。
        try:
            from services.learning_data import ensure_columns
            ensure_columns(conn, "learning_tasks")
            ensure_columns(conn, "learning_plans")
        except Exception as e:
            logger.warning(f"学习计划表结构补齐失败: {e}")
    finally:
        conn.close()


_init_db()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_plan(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "subject_id": row["subject_id"],
        "title": row["title"],
        "goal": row["goal"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_task(row) -> Dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "plan_id": row["plan_id"],
        "subject_id": row["subject_id"] if "subject_id" in keys else None,
        "user_id": row["user_id"] if "user_id" in keys else None,
        "knowledge_point": row["knowledge_point"],
        "knowledge_point_id": row["knowledge_point_id"] if "knowledge_point_id" in keys else None,
        "task_type": row["task_type"],
        "title": row["title"],
        "description": row["description"],
        "priority": row["priority"],
        "estimated_minutes": row["estimated_minutes"],
        "status": row["status"],
        "planned_date": row["planned_date"] if "planned_date" in keys else None,
        "source": row["source"] if "source" in keys else "manual",
        # 教师布置的作业任务：指回作业，便于学生端"去练习"与教师端统计
        "assignment_id": row["assignment_id"] if "assignment_id" in keys else None,
        "source_attempt_id": row["source_attempt_id"] if "source_attempt_id" in keys else None,
        "defer_count": row["defer_count"] if "defer_count" in keys else 0,
        "original_planned_date": (row["original_planned_date"]
                                  if "original_planned_date" in keys else None),
        "completed_at": row["completed_at"],
        "actual_minutes": row["actual_minutes"],
        "created_at": row["created_at"],
    }


# ============ CRUD ============

def list_plans(subject_id: Optional[str] = None,
               user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出学习计划（可按学科过滤；传入 user_id 时只返回该用户的计划）

    每个计划附带 tasks 列表（与 LearningPlan 前端类型契约一致；
    列表接口无 tasks 会导致前端对 plan.tasks 迭代崩溃）。

    归属说明（阶段一）：计划与任务必须归属用户，接口层必须传 user_id，
    否则会把他人的计划展示给当前用户。
    """
    conn = _get_db()
    try:
        q = "SELECT * FROM learning_plans WHERE 1=1"
        p: List[Any] = []
        if user_id:
            q += " AND user_id=?"
            p.append(user_id)
        if subject_id:
            q += " AND subject_id=?"
            p.append(subject_id)
        q += " ORDER BY created_at DESC"
        rows = conn.execute(q, p).fetchall()

        plans = []
        for r in rows:
            plan = _row_to_plan(r)
            tasks = conn.execute(
                "SELECT * FROM learning_tasks WHERE plan_id=? ORDER BY priority, created_at",
                (r["id"],)).fetchall()
            plan["tasks"] = [_row_to_task(t) for t in tasks]
            plans.append(plan)
        return plans
    finally:
        conn.close()


def get_plan(plan_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """获取计划（含任务列表）

    传入 user_id 时校验归属：不属于该用户则返回 None（调用方按 404 处理，
    避免暴露"该计划存在但不属于你"）。
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM learning_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            return None
        # fail-closed：归属为空或不等于当前用户，一律视为不可见
        if user_id and row["user_id"] != user_id:
            return None
        plan = _row_to_plan(row)
        tasks = conn.execute(
            "SELECT * FROM learning_tasks WHERE plan_id=? ORDER BY priority, created_at",
            (plan_id,)).fetchall()
        plan["tasks"] = [_row_to_task(t) for t in tasks]
        return plan
    finally:
        conn.close()


def delete_plan(plan_id: str, user_id: Optional[str] = None) -> bool:
    """删除计划及其任务（传入 user_id 时仅允许删除本人计划）"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT user_id FROM learning_plans WHERE id=?",
                           (plan_id,)).fetchone()
        if not row:
            return False
        # fail-closed：归属为空或不等于当前用户，一律不可删
        if user_id and row["user_id"] != user_id:
            return False
        cur = conn.execute("DELETE FROM learning_plans WHERE id=?", (plan_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_task_status(task_id: str, status: str,
                       user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    更新任务状态（TODO / IN_PROGRESS / COMPLETED）

    过渡接口：只改状态，不记录学习行为（学生端请用 complete_task）。
    user_id 用于归属校验，不匹配返回 None（调用方按 404 处理）。

    阶段一补验：该接口不允许把 **QUIZ 任务**直接置为 COMPLETED
    （否则会绕过"测验任务需先有真实作答"的校验）。
    学生端请使用 POST /student/tasks/{id}/complete。
    """
    if status not in TASK_STATUSES:
        raise LearningServiceError(f"无效的任务状态: {status}")

    if status == "COMPLETED":
        task = get_task(task_id)
        if task and task.get("task_type") == "QUIZ":
            raise LearningServiceError(
                "测验任务请通过「完成」按钮（会校验是否已有真实作答）",
                error_type="quiz_attempt_required")

    conn = _get_db()
    try:
        if user_id:
            row = conn.execute("SELECT user_id FROM learning_tasks WHERE id=?",
                               (task_id,)).fetchone()
            if not row:
                return None
            # fail-closed：归属为空也不放行
            if row["user_id"] != user_id:
                return None
        completed_at = _now() if status == "COMPLETED" else None
        cur = conn.execute(
            "UPDATE learning_tasks SET status=?, completed_at=? WHERE id=?",
            (status, completed_at, task_id))
        if cur.rowcount == 0:
            return None
        conn.commit()
        row = conn.execute("SELECT * FROM learning_tasks WHERE id=?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None
    finally:
        conn.close()


def _set_plan_status(plan_id: str, status: str) -> None:
    """更新计划状态（草案/生效等）"""
    conn = _get_db()
    try:
        conn.execute("UPDATE learning_plans SET status=?, updated_at=? WHERE id=?",
                     (status, _now(), plan_id))
        conn.commit()
    finally:
        conn.close()


def _save_plan(plan: Dict[str, Any], tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    保存计划 + 任务（事务）

    归属：优先使用 plan["user_id"]（由接口层从后端身份机制注入）。
    无用户上下文时落到历史账号 default_user（与历史数据迁移的归属标签一致），
    并记录警告——不静默把计划分配给任意真实账号。
    """
    from datetime import timedelta
    from services.learning_data import LEGACY_OWNER

    now = _now()
    plan_id = plan.get("id") or f"plan_{uuid.uuid4().hex[:8]}"
    subject_id = plan.get("subject_id", "")
    user_id = plan.get("user_id")
    if not user_id:
        user_id = LEGACY_OWNER
        logger.warning("保存学习计划时未提供 user_id，已归入历史账号 default_user")
    start_date = plan.get("start_date") or date.today().isoformat()

    # 计算 end_date：按天分组的最后一天
    day_count = max((t.get("day", 1) for t in tasks), default=1)
    end_date = plan.get("end_date") or (
        date.fromisoformat(start_date) + timedelta(days=day_count - 1)).isoformat()

    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO learning_plans
               (id, subject_id, title, goal, start_date, end_date, status, created_at, updated_at,
                user_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (plan_id, subject_id, plan.get("title", "学习计划"),
             plan.get("goal", ""), start_date, end_date,
             plan.get("status", "ACTIVE"), now, now, user_id))

        base = date.fromisoformat(start_date)
        for t in tasks:
            task_id = f"task_{uuid.uuid4().hex[:8]}"
            kp_name = t.get("knowledge_point")
            kp_id = None
            if subject_id and kp_name:
                try:
                    from services.learning_data import ensure_knowledge_point
                    kp_id = ensure_knowledge_point(subject_id, kp_name, source="plan")
                except Exception:
                    kp_id = None
            planned = t.get("planned_date") or (
                base + timedelta(days=max(0, int(t.get("day", 1)) - 1))).isoformat()
            conn.execute(
                """INSERT INTO learning_tasks
                   (id, plan_id, subject_id, user_id, knowledge_point, knowledge_point_id,
                    task_type, title, description, priority, estimated_minutes, status,
                    planned_date, original_planned_date, source, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, plan_id, subject_id, user_id, kp_name, kp_id,
                 t.get("task_type", "READ"), t.get("title", ""), t.get("description", ""),
                 t.get("priority", 3), t.get("estimated_minutes", 30),
                 t.get("status", "TODO"), planned, planned,
                 t.get("source", "manual"), now))

        conn.commit()
        return get_plan(plan_id)
    finally:
        conn.close()


# ============ AI 生成计划 ============

def _parse_plan_json(text: str) -> Dict[str, Any]:
    """解析计划 JSON（兼容 markdown/解释文字）"""
    if not text:
        return {}
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    m = re.search(r'\{[\s\S]*\}', stripped)
    if m:
        try:
            data = json.loads(m.group())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass
    logger.warning(f"学习计划 JSON 解析失败: {text[:120]}")
    return {}


def generate_plan(subject_id: str, goal: str, model: Optional[str] = None,
                  user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    AI 生成学习计划并持久化

    流程：目标 → RAG 知识点 → LLM 结构化生成 → 保存 LearningPlan + LearningTask

    Args:
        subject_id: 学科ID
        goal: 学习目标（如"三天复习完数据结构中的树和图"）
        model: 模型名（可选）

    Returns:
        保存后的计划（含 tasks）

    Raises:
        LearningServiceError
        KBError: 学科不存在 / 知识库未构建
    """
    if not subject_id or not subject_id.strip():
        raise LearningServiceError("学科ID不能为空", error_type="invalid_request")
    if not goal or not goal.strip():
        raise LearningServiceError("学习目标不能为空", error_type="invalid_request")

    try:
        # 0. 知识库就绪守卫（快速失败，不调 RAG/LLM）
        require_kb_ready(subject_id)

        # 1. RAG 检索知识点证据（用于 LLM 生成贴近知识库的计划）
        context = ""
        try:
            ctx = retrieve_and_build_context(goal, subject_id, top_k=6,
                                             viewer_user_id=user_id)
            context = ctx.get("context", "")[:6000]
        except Exception:
            context = ""  # 检索失败不阻断计划生成

        # 2. 构造 Prompt
        prompt = PLAN_PROMPT.format(goal=goal.strip(), context=context or "（知识库中未检索到相关知识点）")

        # 3. 调用 LLM
        answer = call_llm(prompt, system_message="你是智慧教育学习规划助手。", model=model)
        raw = _parse_plan_json(answer)
        if not raw:
            raise LearningServiceError("学习计划生成失败：模型输出无法解析",
                                       error_type="structured_output_error")

        # 4. 规范化任务
        tasks = []
        for day in raw.get("days", []):
            day_num = int(day.get("day", 1))
            for t in day.get("tasks", []):
                task_type = str(t.get("task_type", "READ")).upper()
                if task_type not in TASK_TYPES:
                    task_type = "READ"
                try:
                    est = int(t.get("estimated_minutes", 30))
                except (TypeError, ValueError):
                    est = 30
                tasks.append({
                    "day": day_num,
                    "knowledge_point": str(t.get("knowledge_point", "")).strip(),
                    "task_type": task_type,
                    "title": str(t.get("title", "")).strip(),
                    "description": str(t.get("description", "")).strip(),
                    "priority": day_num,  # 优先级=天数顺序（简单策略）
                    "estimated_minutes": max(5, min(est, 240)),
                    "status": "TODO",
                })

        if not tasks:
            raise LearningServiceError("学习计划生成失败：未生成任何任务",
                                       error_type="structured_output_error")

        # 5. 保存
        plan = {
            "subject_id": subject_id,
            "user_id": user_id or "default_user",
            "title": str(raw.get("title") or "学习计划"),
            "goal": str(raw.get("goal") or goal.strip()),
        }
        saved = _save_plan(plan, tasks)
        # 生效前校验每日预算与教师固定截止（2026-10-05）：
        # 可调整任务重排、教师固定任务保留；仍不满足则标记为**草案（待确认）**，
        # 不把不满足约束的方案当作已生效计划。
        try:
            from services import plan_adjustment
            owner = user_id or saved.get("user_id")
            constraints = plan_adjustment.get_constraints(owner, subject_id)
            # 只在学生**确实配置过**每日预算/截止时校验；未配置时不预设约束
            if constraints.get("configured"):
                budget = plan_adjustment.fit_plan_to_budget(owner, subject_id, saved["id"])
                if not budget.get("fits"):
                    _set_plan_status(saved["id"], "DRAFT")
                    saved["status"] = "DRAFT"
                    logger.info(f"计划未满足预算约束，标记为草案: plan={saved['id']} "
                                f"conflicts={budget.get('conflicts')}")
                saved["budget"] = budget
        except Exception as e:
            logger.warning(f"计划预算校验失败（保留原状态）: {e}")
        logger.info(f"学习计划已生成并保存: plan={saved['id']}, tasks={len(tasks)}")
        return saved

    except KBError:
        raise
    except AIServiceError:
        raise LearningServiceError("AI生成服务暂时不可用，请稍后重试", error_type="model_api_error")
    except LearningServiceError:
        raise
    except Exception as e:
        logger.error(f"学习计划异常: {e}", exc_info=True)
        raise LearningServiceError("学习计划生成失败", error_type="unknown")


# ==================== 计划可执行：任务查询 / 改期 / 完成 / 自动复习 ====================

# 任务视图：
# - today     : 计划日期为今天且未完成
# - overdue   : 计划日期早于今天且未完成
# - todo      : 所有未完成（含 today/overdue）
# - completed : 已完成
TASK_BUCKETS = ("today", "todo", "completed", "overdue")


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """获取单个任务"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM learning_tasks WHERE id=?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None
    finally:
        conn.close()


def list_tasks(
    user_id: Optional[str] = None,
    subject_id: Optional[str] = None,
    bucket: Optional[str] = None,
    plan_id: Optional[str] = None,
    limit: int = 300,
) -> List[Dict[str, Any]]:
    """
    按用户/学科/视图查询任务。

    bucket ∈ TASK_BUCKETS；不传则返回全部（按计划日期、优先级排序）。
    """
    today = date.today().isoformat()
    conn = _get_db()
    try:
        q = "SELECT * FROM learning_tasks WHERE 1=1"
        p: List[Any] = []
        if user_id:
            q += " AND user_id=?"
            p.append(user_id)
        if subject_id:
            q += " AND subject_id=?"
            p.append(subject_id)
        if plan_id:
            q += " AND plan_id=?"
            p.append(plan_id)
        if bucket == "today":
            q += " AND status!='COMPLETED' AND planned_date=?"
            p.append(today)
        elif bucket == "overdue":
            q += " AND status!='COMPLETED' AND planned_date IS NOT NULL AND planned_date<?"
            p.append(today)
        elif bucket == "todo":
            q += " AND status!='COMPLETED'"
        elif bucket == "completed":
            q += " AND status='COMPLETED'"
        q += " ORDER BY planned_date IS NULL, planned_date, priority, created_at LIMIT ?"
        p.append(limit)
        return [_row_to_task(r) for r in conn.execute(q, p).fetchall()]
    finally:
        conn.close()


def _draft_plan_ids(user_id: str, subject_id: Optional[str] = None) -> set:
    """草案（DRAFT）计划 id 集合：其任务不进入首页/Agent 的生效任务视图"""
    conn = _get_db()
    try:
        q = "SELECT id FROM learning_plans WHERE user_id=? AND status='DRAFT'"
        p: List[Any] = [user_id]
        if subject_id:
            q += " AND subject_id=?"
            p.append(subject_id)
        return {r["id"] for r in conn.execute(q, p).fetchall()}
    except sqlite3.Error:
        return set()
    finally:
        conn.close()


def get_task_buckets(user_id: str, subject_id: Optional[str] = None,
                     completed_limit: int = 100) -> Dict[str, Any]:
    """
    一次返回 today / todo / completed / overdue 四个视图 + 计数。

    实现说明：单次查询后在内存分组（此前对每个视图各查一次库，
    首页聚合会因此产生 7 次往返）。分组口径与 list_tasks 的 bucket 完全一致：
    - today     计划日期=今天 且未完成
    - overdue   计划日期<今天 且未完成
    - todo      全部未完成（含 today 与 overdue）
    - completed 已完成
    """
    today = date.today().isoformat()
    draft_plans = _draft_plan_ids(user_id, subject_id)
    # 草案（待确认）计划不作为生效任务进入首页；确认/应用后才出现
    all_tasks = [t for t in list_tasks(user_id, subject_id, bucket=None, limit=1000)
                 if t.get("plan_id") not in draft_plans]

    todo, completed, today_tasks, overdue = [], [], [], []
    for t in all_tasks:
        if t.get("status") == "COMPLETED":
            completed.append(t)
            continue
        todo.append(t)
        planned = t.get("planned_date")
        if planned == today:
            today_tasks.append(t)
        elif planned and planned < today:
            overdue.append(t)

    return {
        "today": today_tasks,
        "todo": todo,
        "overdue": overdue,
        "completed": completed[:completed_limit],
        "counts": {
            "today": len(today_tasks),
            "todo": len(todo),
            "overdue": len(overdue),
            "completed": len(completed),
        },
    }


def _ensure_active_plan(conn: sqlite3.Connection, user_id: str, subject_id: str) -> str:
    """获取或创建该用户在该学科下的活跃计划（用于承载自动复习任务）"""
    row = conn.execute(
        "SELECT id FROM learning_plans WHERE user_id=? AND subject_id=? AND status='ACTIVE' "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id, subject_id)).fetchone()
    if row:
        return row["id"]
    plan_id = f"plan_{uuid.uuid4().hex[:8]}"
    now = _now()
    today = date.today().isoformat()
    conn.execute(
        """INSERT INTO learning_plans
           (id, subject_id, title, goal, start_date, end_date, status, created_at, updated_at, user_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (plan_id, subject_id, "复习计划", "系统根据错题自动安排的复习", today, today,
         "ACTIVE", now, now, user_id))
    conn.commit()
    return plan_id


def find_reusable_review_task(
    user_id: str, subject_id: str, knowledge_point_id: str
) -> Optional[Dict[str, Any]]:
    """查找可复用的待办自动复习任务（同知识点合并，避免重复新增）"""
    rows = list_tasks(user_id, subject_id, "todo", limit=500)
    for t in rows:
        if (t.get("source") == "auto_review"
                and t.get("knowledge_point_id") == knowledge_point_id):
            return t
    return None


def create_auto_review_task(
    user_id: str,
    subject_id: str,
    knowledge_point_id: str,
    knowledge_point_name: str,
    planned_date: str,
    reason: str,
    source_attempt_id: Optional[str] = None,
) -> Optional[str]:
    """
    为错题创建（或复用）自动复习学习任务，使复习进入可执行日程。

    Returns:
        任务 ID；无可复用时为新建任务 ID，可复用时为既有任务 ID
    """
    if not user_id or not subject_id or not knowledge_point_id:
        return None
    reusable = find_reusable_review_task(user_id, subject_id, knowledge_point_id)
    conn = _get_db()
    try:
        if reusable:
            # 复用：刷新原因与日期（保留原计划日期的更早者）
            new_date = min(reusable["planned_date"] or planned_date, planned_date)
            conn.execute(
                "UPDATE learning_tasks SET planned_date=?, description=?, source_attempt_id=? "
                "WHERE id=?",
                (new_date, reason, source_attempt_id or reusable.get("source_attempt_id"),
                 reusable["id"]))
            conn.commit()
            logger.info(f"复用自动复习任务: task={reusable['id']}, kp={knowledge_point_name}")
            return reusable["id"]

        plan_id = _ensure_active_plan(conn, user_id, subject_id)
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        now = _now()
        conn.execute(
            """INSERT INTO learning_tasks
               (id, plan_id, subject_id, user_id, knowledge_point, knowledge_point_id,
                task_type, title, description, priority, estimated_minutes, status,
                planned_date, original_planned_date, source, source_attempt_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_id, plan_id, subject_id, user_id, knowledge_point_name, knowledge_point_id,
             "REVIEW", f"复习：{knowledge_point_name}", reason, 1, 15, "TODO",
             planned_date, planned_date, "auto_review", source_attempt_id, now))
        conn.commit()
        logger.info(f"创建自动复习任务: task={task_id}, kp={knowledge_point_name}, "
                    f"due={planned_date}, reason={reason}")
        return task_id
    except Exception as e:
        logger.warning(f"创建自动复习任务失败: {e}")
        return None
    finally:
        conn.close()


def create_teacher_task(
    user_id: str,
    subject_id: str,
    *,
    title: str,
    description: Optional[str] = None,
    task_type: str = "QUIZ",
    knowledge_point: Optional[str] = None,
    knowledge_point_id: Optional[str] = None,
    planned_date: Optional[str] = None,
    estimated_minutes: int = 30,
    source_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Optional[str]:
    """
    教师发布作业时，为学生物化一个学习任务（进入既有"今日任务"闭环）。

    - source='teacher_assignment'：与 auto_review / manual 区分，便于统计与展示
    - assignment_id：对应作业 ID（溯源"这个任务从哪来"）
    - **幂等**：同一 (user, 作业) 只创建一次（重复发布不会给学生堆重复任务）
      —— assignment_id 已存在时直接返回既有任务 ID

    Returns:
        任务 ID；创建失败返回 None（调用方需如实报告失败，不静默跳过）
    """
    if not user_id or not subject_id or not title:
        return None
    conn = _get_db()
    try:
        if source_id:
            row = conn.execute(
                """SELECT id FROM learning_tasks
                   WHERE user_id=? AND subject_id=? AND source='teacher_assignment'
                     AND assignment_id=?""",
                (user_id, subject_id, source_id)).fetchone()
            if row:
                logger.info(f"教师任务已存在，跳过: task={row['id']} assignment={source_id}")
                return row["id"]
        plan_id = _ensure_active_plan(conn, user_id, subject_id)
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        now = _now()
        due = planned_date or date.today().isoformat()
        conn.execute(
            """INSERT INTO learning_tasks
               (id, plan_id, subject_id, user_id, knowledge_point, knowledge_point_id,
                task_type, title, description, priority, estimated_minutes, status,
                planned_date, original_planned_date, source, assignment_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_id, plan_id, subject_id, user_id, knowledge_point, knowledge_point_id,
             task_type, title, description, 1, int(estimated_minutes or 30), "TODO",
             due, due, "teacher_assignment", source_id, now))
        conn.commit()
        logger.info(f"创建教师任务: task={task_id} user={user_id} assignment={source_id}")
        return task_id
    except Exception as e:
        logger.warning(f"创建教师任务失败: {e}")
        return None
    finally:
        conn.close()


def find_teacher_tasks(user_id: str, source_id: str) -> List[Dict[str, Any]]:
    """查某次作业为该学生创建的任务（用于教师端查看完成情况）"""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM learning_tasks
               WHERE user_id=? AND source='teacher_assignment' AND assignment_id=?""",
            (user_id, source_id)).fetchall()
        return [_row_to_task(r) for r in rows]
    finally:
        conn.close()


def reschedule_task(
    user_id: str,
    task_id: str,
    new_date: str,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    调整任务计划日期（保留调整记录；不删除原日期）。

    仅允许调整本人任务；系统自动重排不得覆盖教师/用户设定的日期——
    本接口为用户显式改期，因此会记录 defer_count 与调整历史。
    """
    task = get_task(task_id)
    if not task:
        raise LearningServiceError("任务不存在", error_type="invalid_request")
    # fail-closed：归属为空或不等于当前用户都不允许改期
    if user_id and task.get("user_id") != user_id:
        raise LearningServiceError("无权修改其他用户的任务", error_type="forbidden")
    if not new_date:
        raise LearningServiceError("新日期不能为空", error_type="invalid_request")
    try:
        date.fromisoformat(new_date)
    except ValueError:
        raise LearningServiceError("日期格式应为 YYYY-MM-DD", error_type="invalid_request")

    conn = _get_db()
    try:
        conn.execute(
            """UPDATE learning_tasks
               SET planned_date=?, defer_count=COALESCE(defer_count,0)+1
               WHERE id=?""",
            (new_date, task_id))
        conn.execute(
            """INSERT INTO task_adjustments
               (id, task_id, user_id, old_date, new_date, reason, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (f"adj_{uuid.uuid4().hex[:10]}", task_id, user_id,
             task.get("planned_date"), new_date, reason or "用户手动调整", _now()))
        conn.commit()
        logger.info(f"任务改期: task={task_id}, {task.get('planned_date')} -> {new_date}")
    finally:
        conn.close()

    # 返回更新后的任务本身（前端直接使用），并附带原日期便于提示
    updated = get_task(task_id) or {}
    updated["previous_planned_date"] = task.get("planned_date")
    return updated


def list_task_adjustments(task_id: str,
                          user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    查看任务的改期历史（旧日期不被抹掉）。

    阶段一补验：改期历史是个人数据，传入 user_id 时校验任务归属，
    非本人任务返回空列表（不泄露他人改期记录）。
    """
    if user_id:
        task = get_task(task_id)
        if not task or task.get("user_id") != user_id:
            return []
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM task_adjustments WHERE task_id=? ORDER BY created_at DESC",
            (task_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def complete_task(
    user_id: str,
    task_id: str,
    actual_minutes: Optional[int] = None,
    require_quiz_attempt: bool = True,
) -> Dict[str, Any]:
    """
    完成任务（幂等）：记录学习行为，不直接等同于"测验通过"。

    - 重复完成不会重复记行为（事件幂等键）
    - QUIZ 类型任务需先有至少一次真实作答，否则拒绝（区分"完成"与"通过"）
    """
    task = get_task(task_id)
    if not task:
        raise LearningServiceError("任务不存在", error_type="invalid_request")
    # fail-closed：归属为空或不等于当前用户都不允许操作
    if user_id and task.get("user_id") != user_id:
        raise LearningServiceError("无权操作其他用户的任务", error_type="forbidden")

    kp_id = task.get("knowledge_point_id")
    quiz_info = None
    if task["task_type"] == "QUIZ" and require_quiz_attempt:
        try:
            from services.quiz_service import list_attempts
            attempts = [a for a in list_attempts(user_id, task.get("subject_id"), limit=200)
                        if (not kp_id or a.get("knowledge_point_id") == kp_id)]
            if not attempts:
                raise LearningServiceError(
                    "该任务为测验任务，请先完成一次自测作答后再标记完成",
                    error_type="quiz_attempt_required")
            graded = [a for a in attempts if a.get("is_correct") is not None]
            correct = [a for a in graded if a.get("is_correct")]
            quiz_info = {
                "attempt_count": len(attempts),
                "graded_count": len(graded),
                "correct_count": len(correct),
                "passed": bool(graded) and len(correct) > 0,
            }
        except LearningServiceError:
            raise
        except Exception as e:
            logger.warning(f"检查测验作答失败（放行任务完成）: {e}")

    already = task["status"] == "COMPLETED"
    if not already:
        conn = _get_db()
        try:
            conn.execute(
                "UPDATE learning_tasks SET status='COMPLETED', completed_at=?, actual_minutes=? "
                "WHERE id=? AND status!='COMPLETED'",
                (_now(), actual_minutes, task_id))
            conn.commit()
        finally:
            conn.close()

    # 记录学习行为（幂等：重复完成不重复计数；且不改变掌握度）
    behavior_recorded = False
    if not already:
        try:
            from services.learning_events import emit_event
            res = emit_event(
                event_type="plan_task_completed",
                idempotency_key=f"task_completed:{task_id}",
                user_id=user_id,
                subject_id=task.get("subject_id"),
                payload={
                    "task_id": task_id,
                    "knowledge_point": task.get("knowledge_point"),
                    "knowledge_point_id": kp_id,
                    "task_type": task["task_type"],
                    "actual_minutes": actual_minutes,
                    "source": task.get("source"),
                },
            )
            behavior_recorded = res.get("status") == "DONE" and not res.get("duplicated")
        except Exception as e:
            logger.warning(f"任务完成联动失败（任务状态已保存）: {e}")

    return {
        "task": get_task(task_id),
        "already_completed": already,
        "behavior_recorded": behavior_recorded,
        "quiz_info": quiz_info,
        "note": "任务完成只记录学习行为，掌握度仅由独立作答证据决定",
    }
