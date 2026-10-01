"""
对话系统服务（V2.5.1）

Conversation + Message 持久化（SQLite），取代 localStorage 全局会话管理。

设计：
- SQLite 标准库（sqlite3），轻量、无额外依赖，复用 learning_service 基础设施模式
- Conversation: id, subject_id, title, model, created_at, updated_at
- Message: id, conversation_id, role, content, message_type,
  payload_json, sources_json, model, latency, created_at
- message_type: chat | smart_note | summary | explanation | advice | quiz
  payload_json 保留结构化内容（如五段式笔记、自测题），content 保留可读文本摘要

会话隔离：Conversation 绑定 subject_id，切换学科不得沿用其他学科会话。
标题：默认用第一条用户消息截取前 20-30 字符，不额外调用 LLM。
"""

from config.path_config import get_data_dir
import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

from utils.logger import logger

# 消息类型枚举
MESSAGE_TYPES = ("chat", "smart_note", "summary", "explanation", "advice", "quiz")
# 有效角色
ROLES = ("user", "assistant")

# 数据库文件位置（统一数据根目录，支持 EDU_DATA_DIR 覆盖）
_DATA_DIR = str(get_data_dir())
DB_PATH = os.path.join(_DATA_DIR, "chat.db")


class ConversationServiceError(Exception):
    """对话服务异常（携带 error_type）"""

    def __init__(self, message: str, error_type: str = "unknown"):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


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
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '新对话',
            model TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            message_type TEXT NOT NULL DEFAULT 'chat',
            payload_json TEXT,
            sources_json TEXT,
            model TEXT,
            latency REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_conversations_subject ON conversations(subject_id, updated_at);
        """)
        conn.commit()

        # 阶段一补验：会话/消息归属列。
        # 历史库由 learning_data.migrate_legacy() 补列，但新建库（含隔离测试库）
        # 走的是本函数，必须同样补齐，否则会话无法按用户隔离。
        try:
            from services.learning_data import ensure_columns
            ensure_columns(conn, "conversations")
            ensure_columns(conn, "messages")
        except Exception as e:
            logger.warning(f"会话表结构补齐失败: {e}")
    finally:
        conn.close()


_init_db()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_conversation(row) -> Dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "subject_id": row["subject_id"],
        "title": row["title"],
        "model": row["model"],
        # 归属（阶段一补验：会话必须归属用户，接口层据此做对象级校验）
        "user_id": row["user_id"] if "user_id" in keys else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_message(row) -> Dict[str, Any]:
    keys = row.keys()
    msg = {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],
        "content": row["content"],
        "message_type": row["message_type"],
        "model": row["model"],
        "latency": row["latency"],
        "created_at": row["created_at"],
        # 关联的用户交互记录（阶段一，供反馈按钮正确落库）
        "interaction_id": row["interaction_id"] if "interaction_id" in keys else None,
    }
    # 解析结构化负载（失败不阻断，返回 None）
    if row["payload_json"]:
        try:
            msg["payload"] = json.loads(row["payload_json"])
        except (json.JSONDecodeError, ValueError):
            msg["payload"] = None
    if row["sources_json"]:
        try:
            msg["sources"] = json.loads(row["sources_json"])
        except (json.JSONDecodeError, ValueError):
            msg["sources"] = []
    return msg


# ============ Conversation CRUD ============

def create_conversation(subject_id: str, model: Optional[str] = None,
                        title: Optional[str] = None,
                        user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    创建会话（绑定学科 + 归属用户）

    Args:
        subject_id: 学科ID（会话隔离依据）
        model: 模型名（可选）
        title: 标题（可选，默认"新对话"，首条消息后自动更新）
        user_id: 归属用户（阶段一补验：必须由后端身份机制传入）

    Returns:
        会话字典
    """
    if not subject_id or not subject_id.strip():
        raise ConversationServiceError("学科ID不能为空", error_type="invalid_request")

    conv_id = f"conv_{uuid.uuid4().hex[:8]}"
    now = _now()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO conversations
               (id, subject_id, title, model, created_at, updated_at, user_id)
               VALUES (?,?,?,?,?,?,?)""",
            (conv_id, subject_id, title or "新对话", model, now, now, user_id))
        conn.commit()
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
        return _row_to_conversation(row)
    finally:
        conn.close()


def list_conversations(subject_id: Optional[str] = None,
                       user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    列出会话（可按学科过滤，按 updated_at 倒序）。

    user_id 不为空时**只返回该用户的会话**；接口层必须传，
    否则会把他人会话列出来（阶段一补验修复的越权点）。
    """
    conn = _get_db()
    try:
        q = "SELECT * FROM conversations WHERE 1=1"
        p: List[Any] = []
        if user_id:
            q += " AND user_id=?"
            p.append(user_id)
        if subject_id:
            q += " AND subject_id=?"
            p.append(subject_id)
        q += " ORDER BY updated_at DESC"
        rows = conn.execute(q, p).fetchall()
        return [_row_to_conversation(r) for r in rows]
    finally:
        conn.close()


def get_conversation_owner(conversation_id: str) -> Optional[str]:
    """
    只取会话归属（用于校验，不返回内容）。

    兼容历史数据：返回 None 表示"会话不存在"，空字符串表示"存在但未归属"
    （迁移前的历史会话归属为 default_user，正常不会是空串）。
    """
    if not conversation_id:
        return None
    conn = _get_db()
    try:
        row = conn.execute("SELECT user_id FROM conversations WHERE id=?",
                           (conversation_id,)).fetchone()
        if not row:
            return None
        keys = row.keys()
        if "user_id" not in keys:
            return ""
        return row["user_id"] or ""
    finally:
        conn.close()


def get_conversation(conversation_id: str,
                     user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    获取单个会话。

    传入 user_id 时做对象级归属校验：不属于该用户（或历史会话归属为空）
    返回 None，调用方按 404 处理（不暴露"该会话存在但不属于你"）。
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if not row:
            return None
        if user_id:
            keys = row.keys()
            owner = row["user_id"] if "user_id" in keys else None
            if owner != user_id:
                return None
        return _row_to_conversation(row)
    finally:
        conn.close()


def delete_conversation(conversation_id: str, user_id: Optional[str] = None) -> bool:
    """删除会话及其全部消息（级联）；传入 user_id 时仅允许删除本人会话"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if not row:
            return False
        if user_id:
            keys = row.keys()
            owner = row["user_id"] if "user_id" in keys else None
            if owner != user_id:
                return False
        cur = conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_conversation_title(conversation_id: str, title: str,
                              user_id: Optional[str] = None) -> bool:
    """更新会话标题（首条消息后调用）；传入 user_id 时仅允许更新本人会话"""
    conn = _get_db()
    try:
        if user_id:
            row = conn.execute("SELECT * FROM conversations WHERE id=?",
                               (conversation_id,)).fetchone()
            if not row:
                return False
            keys = row.keys()
            owner = row["user_id"] if "user_id" in keys else None
            if owner != user_id:
                return False
        cur = conn.execute(
            "UPDATE conversations SET title=? WHERE id=? AND title='新对话'",
            (title, conversation_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def touch_conversation(conversation_id: str, model: Optional[str] = None,
                       user_id: Optional[str] = None) -> None:
    """刷新会话的 updated_at / model；传入 user_id 时仅允许刷新本人会话"""
    if user_id and get_conversation_owner(conversation_id) != user_id:
        return
    conn = _get_db()
    try:
        if model:
            conn.execute(
                "UPDATE conversations SET updated_at=?, model=? WHERE id=?",
                (_now(), model, conversation_id))
        else:
            conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (_now(), conversation_id))
        conn.commit()
    finally:
        conn.close()


# ============ Message CRUD ============

def add_message(
    conversation_id: str,
    role: str,
    content: str,
    message_type: str = "chat",
    payload: Any = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = None,
    latency: Optional[float] = None,
) -> Dict[str, Any]:
    """
    写入一条消息

    Args:
        conversation_id: 会话ID
        role: user | assistant
        content: 可读文本内容（assistant 为回答；user 为问题）
        message_type: chat | smart_note | summary | explanation | advice | quiz
        payload: 结构化负载（智能笔记/自测题等），序列化为 payload_json
        sources: 参考来源列表，序列化为 sources_json
        model: 模型名
        latency: 耗时（秒）

    Returns:
        消息字典
    """
    if role not in ROLES:
        raise ConversationServiceError(f"无效的角色: {role}", error_type="invalid_request")
    if message_type not in MESSAGE_TYPES:
        message_type = "chat"

    # 校验会话存在
    conv = get_conversation(conversation_id)
    if not conv:
        raise ConversationServiceError("会话不存在", error_type="invalid_request")

    msg_id = f"msg_{uuid.uuid4().hex[:8]}"
    now = _now()
    payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
    sources_json = json.dumps(sources, ensure_ascii=False) if sources is not None else None

    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO messages
               (id, conversation_id, role, content, message_type, payload_json,
                sources_json, model, latency, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (msg_id, conversation_id, role, content, message_type,
             payload_json, sources_json, model, latency, now))
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        return _row_to_message(row)
    finally:
        conn.close()


def attach_interaction_id(message_id: str, interaction_id: str) -> bool:
    """
    将消息关联到用户交互记录（阶段一）。

    修复：此前前端把 message_id 当作 interaction_id 提交反馈，
    导致 user_interactions 关联更新失效。现在由后端回写真实 interaction_id。
    """
    if not message_id or not interaction_id:
        return False
    conn = _get_db()
    try:
        cur = conn.execute(
            "UPDATE messages SET interaction_id=? WHERE id=?", (interaction_id, message_id))
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"关联 interaction_id 失败: {e}")
        return False
    finally:
        conn.close()


def get_message(message_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 获取消息（含 interaction_id）"""
    if not message_id:
        return None
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return _row_to_message(row) if row else None
    finally:
        conn.close()


def resolve_interaction_id(message_id: str) -> Optional[str]:
    """
    由消息 ID 解析真实 interaction_id（供反馈落库使用）。

    兼容历史：若消息未关联（早期数据），返回 None，由调用方决定兜底策略。
    """
    msg = get_message(message_id)
    if not msg:
        return None
    return msg.get("interaction_id")


def list_messages(conversation_id: str,
                  user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    列出会话的全部消息（按创建时间正序）。

    传入 user_id 时校验会话归属：不属于当前用户则返回空列表
    （调用方应先确认会话归属，再决定 404 还是空列表）。
    """
    if user_id and get_conversation_owner(conversation_id) != user_id:
        return []
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at, rowid",
            (conversation_id,)).fetchall()
        return [_row_to_message(r) for r in rows]
    finally:
        conn.close()


def get_recent_history(conversation_id: str, max_turns: int = 10,
                       user_id: Optional[str] = None) -> List[Dict[str, str]]:
    """
    读取会话最近 N 轮历史（用于 LLM 上下文，服务端权威，不信任前端传入 history）

    Args:
        conversation_id: 会话ID
        max_turns: 最多保留的轮数（user+assistant 为一轮）
        user_id: 归属校验（传入时非本人会话直接返回空，避免把他人历史喂给模型）

    Returns:
        [{"role": "user"|"assistant", "content": "..."}]
    """
    if user_id and get_conversation_owner(conversation_id) != user_id:
        return []
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? AND message_type='chat' "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (conversation_id, max_turns * 2)).fetchall()
    finally:
        conn.close()

    history = []
    for r in reversed(rows):
        content = (r["content"] or "").strip()
        if not content:
            continue
        history.append({"role": r["role"], "content": content})
    return history
