"""
统一伴学会话与轮次存储（2026-10-07）

参考 DeepTutor 的 UnifiedContext + turn/events 持久化设计（见 docs/research/
deeptutor-runtime-architecture.md），按本项目体量裁剪为一张“上下文表”+“轮次表”+
“事件表”：

- tutor_sessions：同一用户在同一学科只有一份**统一上下文**（能力、资料范围、当前节点、
  当前会话、学习目标与约束、最近成果）。刷新/重启后据此恢复学习位置。
- tutor_turns：一次提问=一轮，含状态机（RUNNING/SUCCEEDED/FAILED/CANCELLED）、
  工具调用数与模型调用数（预算可审计）。
- tutor_turn_events：流式事件先落库再推送，断线重连时按 after_seq 重放，保证不重不漏。

所有迁移都是 CREATE TABLE IF NOT EXISTS 的加法迁移，不动既有库。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from config.path_config import get_data_dir
from utils.logger import logger

_DATA_DIR = str(get_data_dir())
DB_PATH = os.path.join(_DATA_DIR, "tutor.db")

STATE_RUNNING = "RUNNING"
STATE_SUCCEEDED = "SUCCEEDED"
STATE_FAILED = "FAILED"
STATE_CANCELLED = "CANCELLED"
TERMINAL_STATES = (STATE_SUCCEEDED, STATE_FAILED, STATE_CANCELLED)

# 能力分组（本项目五类伴学能力，命名可按研究结果调整）
ABILITY_CHAT = "chat"          # 资料问答与深入追问
ABILITY_EXPLAIN = "explain"    # 引导学习与知识点讲解
ABILITY_PRACTICE = "practice"  # 练习与检测
ABILITY_MAP = "map"            # 思维导图与知识地图
ABILITY_ORGANIZE = "organize"  # 学习整理：笔记、提纲、学习安排
ABILITIES = (ABILITY_CHAT, ABILITY_EXPLAIN, ABILITY_PRACTICE, ABILITY_MAP, ABILITY_ORGANIZE)

# 引导方式（2026-10-24）：direct 是既有行为，默认值必须保持不变；
# socratic 只改变「怎么讲」，不改变能力、工具权限、判分与掌握度口径。
GUIDANCE_DIRECT = "direct"
GUIDANCE_SOCRATIC = "socratic"
GUIDANCE_MODES = (GUIDANCE_DIRECT, GUIDANCE_SOCRATIC)


def normalize_guidance_mode(value: Any) -> str:
    """非法/缺省值一律回落到 direct，保证旧会话与旧客户端行为不变。"""
    if isinstance(value, str):
        value = value.strip()
        if value in GUIDANCE_MODES:
            return value
    return GUIDANCE_DIRECT

_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS tutor_sessions (
    user_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, subject_id)
)
"""

_TURNS_DDL = """
CREATE TABLE IF NOT EXISTS tutor_turns (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    subject_id TEXT,
    conversation_id TEXT,
    node_id TEXT,
    ability TEXT,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    user_message TEXT,
    answer TEXT,
    error TEXT,
    steps INTEGER DEFAULT 0,
    llm_calls INTEGER DEFAULT 0,
    cancel_requested INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
)
"""

_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS tutor_turn_events (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (turn_id, seq)
)
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _get_db() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(_SESSIONS_DDL)
    conn.execute(_TURNS_DDL)
    conn.execute(_EVENTS_DDL)
    _ensure_columns(conn)
    conn.commit()
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """加法迁移：给已有表补列。

    CREATE TABLE IF NOT EXISTS 只对新库生效，老库不会自动获得新字段，
    所以这里显式补。2026-10-03 加 node_id：历史记录需要按知识点归集，
    否则"点某个知识点看它以前的讲解"做不到。
    """
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tutor_turns)").fetchall()}
        if "node_id" not in cols:
            conn.execute("ALTER TABLE tutor_turns ADD COLUMN node_id TEXT")
            logger.info("tutor_sessions: tutor_turns 已补列 node_id")
    except sqlite3.Error as e:
        logger.warning(f"tutor_sessions 补列失败（忽略）: {e}")


def _loads(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


# =====================================================================
# 统一上下文
# =====================================================================

def default_context(user_id: str, subject_id: str) -> Dict[str, Any]:
    return {
        "user_id": user_id,
        "subject_id": subject_id,
        "course_id": None,
        "role": "student",
        "ability": ABILITY_CHAT,
        "guidance_mode": GUIDANCE_DIRECT,
        "material_scope": {"mode": "all", "selected_doc_ids": []},
        "current_node_id": None,
        "current_map_id": None,
        "conversation_id": None,
        "last_turn_id": None,
        "goal": None,
        "deadline": None,
        "daily_minutes": None,
        # 上下文预算（2026-10-28）：更早轮次的滚动摘要，随 state_json 持久化，零迁移
        "rolling_summary": "",
        "summary_upto": None,
        "updated_at": _now(),
    }


_PATCHABLE = {
    "course_id", "role", "ability", "current_node_id", "current_map_id",
    "conversation_id", "last_turn_id", "goal", "deadline", "daily_minutes",
    "guidance_mode", "rolling_summary", "summary_upto",
}


def load_context(user_id: str, subject_id: str) -> Dict[str, Any]:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT state_json FROM tutor_sessions WHERE user_id=? AND subject_id=?",
            (user_id, subject_id)).fetchone()
    finally:
        conn.close()
    ctx = default_context(user_id, subject_id)
    if row:
        stored = _loads(row["state_json"], {})
        if isinstance(stored, dict):
            # 不覆盖用户/学科这两个权威字段（防止历史脏数据串号）
            stored.pop("user_id", None)
            stored.pop("subject_id", None)
            ctx.update(stored)
    ctx["user_id"] = user_id
    ctx["subject_id"] = subject_id
    # 老会话没有这个字段 / 历史脏数据：统一回落到 direct，绝不因新字段报错。
    ctx["guidance_mode"] = normalize_guidance_mode(ctx.get("guidance_mode"))
    return ctx


def save_context(user_id: str, subject_id: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(ctx or {})
    data["user_id"] = user_id
    data["subject_id"] = subject_id
    data["updated_at"] = _now()
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO tutor_sessions (user_id, subject_id, state_json, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(user_id, subject_id) DO UPDATE SET "
            "state_json=excluded.state_json, updated_at=excluded.updated_at",
            (user_id, subject_id, json.dumps(data, ensure_ascii=False), data["updated_at"]))
        conn.commit()
    finally:
        conn.close()
    return data


def update_context(user_id: str, subject_id: str, /, **patch: Any) -> Dict[str, Any]:
    """只允许白名单字段被外部改写（资料范围用 set_material_scope，避免绕过权限解析）。"""
    ctx = load_context(user_id, subject_id)
    for key, value in (patch or {}).items():
        if key in _PATCHABLE:
            if key == "guidance_mode":
                value = normalize_guidance_mode(value)
            ctx[key] = value
        else:
            logger.warning(f"忽略不可改写的上下文字段: {key}")
    return save_context(user_id, subject_id, ctx)


def set_material_scope(user_id: str, subject_id: str,
                       selected_doc_ids: Optional[List[str]]) -> Dict[str, Any]:
    ctx = load_context(user_id, subject_id)
    ctx["material_scope"] = {
        "mode": "all" if not selected_doc_ids else "selected",
        "selected_doc_ids": list(selected_doc_ids or []),
    }
    return save_context(user_id, subject_id, ctx)


# =====================================================================
# 轮次与事件
# =====================================================================

def create_turn(user_id: str, subject_id: Optional[str], ability: str,
                user_message: str, conversation_id: Optional[str] = None,
                node_id: Optional[str] = None) -> str:
    turn_id = "turn_" + uuid.uuid4().hex[:12]
    now = _now()
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO tutor_turns (id, user_id, subject_id, conversation_id, node_id, ability,"
            " status, user_message, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (turn_id, user_id, subject_id, conversation_id, node_id, ability, STATE_RUNNING,
             (user_message or "")[:4000], now, now))
        conn.commit()
    finally:
        conn.close()
    return turn_id


def append_event(turn_id: str, event_type: str, payload: Dict[str, Any]) -> int:
    """写入事件并返回**单调递增的 seq**（同一 turn 内由数据库唯一约束保证）。"""
    conn = _get_db()
    try:
        # 先占位取 seq：WAL + busy_timeout 下并发安全
        for _ in range(5):
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS m FROM tutor_turn_events WHERE turn_id=?",
                (turn_id,)).fetchone()
            seq = int(row["m"]) + 1
            try:
                conn.execute(
                    "INSERT INTO tutor_turn_events (id, turn_id, seq, type, payload_json, created_at)"
                    " VALUES (?,?,?,?,?,?)",
                    ("evt_" + uuid.uuid4().hex[:12], turn_id, seq, event_type,
                     json.dumps(payload or {}, ensure_ascii=False), _now()))
                conn.commit()
                return seq
            except sqlite3.IntegrityError:
                time.sleep(0.02)
                continue
        raise RuntimeError(f"无法为轮次 {turn_id} 分配事件序号")
    finally:
        conn.close()


def list_events(turn_id: str, after_seq: int = 0) -> List[Dict[str, Any]]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT seq, type, payload_json, created_at FROM tutor_turn_events "
            "WHERE turn_id=? AND seq>? ORDER BY seq", (turn_id, int(after_seq or 0))).fetchall()
    finally:
        conn.close()
    return [{"seq": r["seq"], "type": r["type"],
             "payload": _loads(r["payload_json"], {}), "created_at": r["created_at"]}
            for r in rows]


def finish_turn(turn_id: str, status: str, answer: Optional[str] = None,
                error: Optional[str] = None, steps: Optional[int] = None,
                llm_calls: Optional[int] = None) -> None:
    fields = ["status=?", "updated_at=?", "finished_at=?"]
    values: List[Any] = [status, _now(), _now()]
    if answer is not None:
        fields.append("answer=?")
        values.append(answer)
    if error is not None:
        fields.append("error=?")
        values.append(error)
    if steps is not None:
        fields.append("steps=?")
        values.append(int(steps))
    if llm_calls is not None:
        fields.append("llm_calls=?")
        values.append(int(llm_calls))
    values.append(turn_id)
    conn = _get_db()
    try:
        conn.execute(f"UPDATE tutor_turns SET {', '.join(fields)} WHERE id=?", tuple(values))
        conn.commit()
    finally:
        conn.close()


def get_turn(turn_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM tutor_turns WHERE id=?", (turn_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    data = dict(row)
    if user_id and data.get("user_id") != user_id:
        return None
    data["cancel_requested"] = bool(data.get("cancel_requested"))
    return data


def list_turns(user_id: str, subject_id: Optional[str] = None,
               limit: int = 30, node_id: Optional[str] = None,
               conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出历史轮次。

    node_id 给定时只返回**该知识点**的轮次——学生点某个知识点时，右侧要显示
    它以前讲过什么，而不是整个学科的所有记录。
    conversation_id 给定时只返回该会话的轮次（"继续这个会话"用，2026-10-31）。
    """
    fields = ("id, subject_id, conversation_id, node_id, ability, status, user_message,"
              " answer, created_at, finished_at")
    where = ["user_id=?"]
    args: List[Any] = [user_id]
    if subject_id:
        where.append("subject_id=?")
        args.append(subject_id)
    if node_id:
        where.append("node_id=?")
        args.append(node_id)
    if conversation_id:
        where.append("conversation_id=?")
        args.append(conversation_id)
    args.append(int(limit))
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT " + fields + " FROM tutor_turns WHERE " + " AND ".join(where)
            + " ORDER BY created_at DESC LIMIT ?", tuple(args)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def request_cancel(turn_id: str, user_id: str) -> bool:
    conn = _get_db()
    try:
        row = conn.execute("SELECT user_id, status FROM tutor_turns WHERE id=?", (turn_id,)).fetchone()
        if not row or row["user_id"] != user_id:
            return False
        if row["status"] in TERMINAL_STATES:
            return False
        conn.execute(
            "UPDATE tutor_turns SET cancel_requested=1, updated_at=? WHERE id=?",
            (_now(), turn_id))
        conn.commit()
        return True
    finally:
        conn.close()


def is_cancelled(turn_id: str) -> bool:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT cancel_requested, status FROM tutor_turns WHERE id=?", (turn_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return True
    return bool(row["cancel_requested"]) or row["status"] == STATE_CANCELLED


def list_conversations(user_id: str, subject_id: str,
                       limit: int = 50) -> List[Dict[str, Any]]:
    """按会话聚合历史轮次（2026-10-31，历史抽屉改为"可继续的会话列表"）。

    只聚合 conversation_id 非空的轮次（新逻辑显式管理会话）；
    旧的无会话散轮由调用方单独列出并标为只读——它们可以查看但不能续聊，
    如实标示，不假装支持。

    Returns: [{conversation_id, turns, title, preview, last_at, started_at}]，
             按最近活跃倒序。title/preview 取会话最早一条用户消息。
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT conversation_id, COUNT(*) AS turns,
                      MAX(updated_at) AS last_at, MIN(created_at) AS started_at
               FROM tutor_turns
               WHERE user_id=? AND subject_id=?
                 AND conversation_id IS NOT NULL AND conversation_id != ''
               GROUP BY conversation_id
               ORDER BY last_at DESC LIMIT ?""",
            (user_id, subject_id, int(limit))).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            first = conn.execute(
                """SELECT user_message, answer FROM tutor_turns
                   WHERE user_id=? AND subject_id=? AND conversation_id=?
                   ORDER BY created_at ASC LIMIT 1""",
                (user_id, subject_id, r["conversation_id"])).fetchone()
            title_src = (first["user_message"] if first else "") or (first["answer"] if first else "") or ""
            out.append({
                "conversation_id": r["conversation_id"],
                "turns": int(r["turns"]),
                "last_at": r["last_at"],
                "started_at": r["started_at"],
                "title": title_src.strip()[:60] or "（无标题会话）",
                "preview": title_src.strip()[:120],
            })
        return out
    finally:
        conn.close()


def list_loose_turns(user_id: str, subject_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    """无会话归属的散轮（旧格式：direct 模式早期记录 / 旧 chat 导入）。

    它们只读可查看；列表界面要明确标示"早期记录"，不能伪装成可续聊会话。
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT id, subject_id, node_id, ability, status, user_message,
                      answer, created_at, finished_at
               FROM tutor_turns
               WHERE user_id=? AND subject_id=?
                 AND (conversation_id IS NULL OR conversation_id = '')
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, subject_id, int(limit))).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def delete_turn(turn_id: str, user_id: str) -> bool:
    """删除一条历史轮次（连同它的事件）。

    只允许删自己的记录。
    **只删对话历史**：题库里的题目与作答记录不动——那是学习证据（错题本、复习、
    掌握度都由它推导），不该被一次界面上的删除带走。
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT user_id FROM tutor_turns WHERE id=?", (turn_id,)).fetchone()
        if not row or row["user_id"] != user_id:
            return False
        conn.execute("DELETE FROM tutor_turn_events WHERE turn_id=?", (turn_id,))
        conn.execute("DELETE FROM tutor_turns WHERE id=?", (turn_id,))
        # 这一轮出的题跟着消失（软删）：题属于那次出题，历史删了题还留着说不通。
        # 用软删而不是硬删——作答记录、错题本、复习与掌握度都引用题目，
        # 硬删会让它们变成指向不存在题目的孤儿。
        conn.execute(
            "UPDATE quiz_questions SET deleted_at=? WHERE turn_id=? AND (deleted_at IS NULL OR deleted_at='')",
            (_now(), turn_id))
        conn.commit()
        return True
    finally:
        conn.close()


__all__ = [
    "ABILITY_CHAT", "ABILITY_EXPLAIN", "ABILITY_PRACTICE", "ABILITY_MAP", "ABILITY_ORGANIZE",
    "ABILITIES", "GUIDANCE_DIRECT", "GUIDANCE_SOCRATIC", "GUIDANCE_MODES", "normalize_guidance_mode",
    "STATE_RUNNING", "STATE_SUCCEEDED", "STATE_FAILED", "STATE_CANCELLED",
    "TERMINAL_STATES", "default_context", "load_context", "save_context", "update_context",
    "set_material_scope", "create_turn", "delete_turn", "append_event", "list_events", "finish_turn",
    "get_turn", "list_turns", "list_conversations", "list_loose_turns",
    "request_cancel", "is_cancelled", "DB_PATH",
]
