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

# 数据库文件位置
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
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
        """)
        conn.commit()
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
    return {
        "id": row["id"],
        "plan_id": row["plan_id"],
        "knowledge_point": row["knowledge_point"],
        "task_type": row["task_type"],
        "title": row["title"],
        "description": row["description"],
        "priority": row["priority"],
        "estimated_minutes": row["estimated_minutes"],
        "status": row["status"],
        "completed_at": row["completed_at"],
        "actual_minutes": row["actual_minutes"],
        "created_at": row["created_at"],
    }


# ============ CRUD ============

def list_plans(subject_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出学习计划（可按学科过滤）

    每个计划附带 tasks 列表（与 LearningPlan 前端类型契约一致；
    列表接口无 tasks 会导致前端对 plan.tasks 迭代崩溃）。
    """
    conn = _get_db()
    try:
        if subject_id:
            rows = conn.execute(
                "SELECT * FROM learning_plans WHERE subject_id=? ORDER BY created_at DESC",
                (subject_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM learning_plans ORDER BY created_at DESC").fetchall()

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


def get_plan(plan_id: str) -> Optional[Dict[str, Any]]:
    """获取计划（含任务列表）"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM learning_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            return None
        plan = _row_to_plan(row)
        tasks = conn.execute(
            "SELECT * FROM learning_tasks WHERE plan_id=? ORDER BY priority, created_at",
            (plan_id,)).fetchall()
        plan["tasks"] = [_row_to_task(t) for t in tasks]
        return plan
    finally:
        conn.close()


def delete_plan(plan_id: str) -> bool:
    """删除计划及其任务"""
    conn = _get_db()
    try:
        cur = conn.execute("DELETE FROM learning_plans WHERE id=?", (plan_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_task_status(task_id: str, status: str) -> Optional[Dict[str, Any]]:
    """更新任务状态（TODO / IN_PROGRESS / COMPLETED）"""
    if status not in TASK_STATUSES:
        raise LearningServiceError(f"无效的任务状态: {status}")

    conn = _get_db()
    try:
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


def _save_plan(plan: Dict[str, Any], tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """保存计划 + 任务（事务）"""
    now = _now()
    plan_id = plan.get("id") or f"plan_{uuid.uuid4().hex[:8]}"
    start_date = plan.get("start_date") or date.today().isoformat()

    # 计算 end_date：按天分组的最后一天
    from datetime import timedelta
    day_count = max((t.get("day", 1) for t in tasks), default=1)
    end_date = plan.get("end_date") or (
        date.fromisoformat(start_date) + timedelta(days=day_count - 1)).isoformat()

    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO learning_plans
               (id, subject_id, title, goal, start_date, end_date, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (plan_id, plan.get("subject_id", ""), plan.get("title", "学习计划"),
             plan.get("goal", ""), start_date, end_date,
             plan.get("status", "ACTIVE"), now, now))

        for t in tasks:
            task_id = f"task_{uuid.uuid4().hex[:8]}"
            conn.execute(
                """INSERT INTO learning_tasks
                   (id, plan_id, knowledge_point, task_type, title, description,
                    priority, estimated_minutes, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (task_id, plan_id, t.get("knowledge_point"), t.get("task_type", "READ"),
                 t.get("title", ""), t.get("description", ""),
                 t.get("priority", 3), t.get("estimated_minutes", 30),
                 t.get("status", "TODO"), now))

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


def generate_plan(subject_id: str, goal: str, model: Optional[str] = None) -> Dict[str, Any]:
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
            ctx = retrieve_and_build_context(goal, subject_id, top_k=6)
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
            "title": str(raw.get("title") or "学习计划"),
            "goal": str(raw.get("goal") or goal.strip()),
        }
        saved = _save_plan(plan, tasks)
        logger.info(f"学习计划已生成并保存: plan={saved['id']}, tasks={len(tasks)}")
        return saved

    except KBError:
        raise
    except AIServiceError as e:
        raise LearningServiceError("AI生成服务暂时不可用，请稍后重试", error_type="model_api_error")
    except LearningServiceError:
        raise
    except Exception as e:
        logger.error(f"学习计划异常: {e}", exc_info=True)
        raise LearningServiceError("学习计划生成失败", error_type="unknown")
