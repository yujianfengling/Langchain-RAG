"""白板思维板书存档（2026-11-14）

前端 MindBoard 的导图 + 标注按 (user_id, subject_id, board_key) 存档到
get_data_dir()/tutor.db，换设备/清浏览器缓存不丢。board_key 与前端本地
localStorage 的分段一致（知识点 node_id，未选节点时用知识点名，再退 free）。

同键覆盖式保存：学生自己的板书没有版本树需求，最后一次保存即最新；
本地与云端谁新以前端比 updatedAt 为准，后端只做存取。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, Optional

from config.path_config import get_data_dir

_DATA_DIR = str(get_data_dir())
DB_PATH = os.path.join(_DATA_DIR, "tutor.db")

MAX_PAYLOAD_BYTES = 4_000_000  # 导图 + 标注 + 节点图片，4MB 足够

_DDL = """
CREATE TABLE IF NOT EXISTS mindboard_archives (
    user_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    board_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, subject_id, board_key)
)
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _get_db() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    from services.turn_execution import connect
    conn = connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(_DDL)
    conn.commit()
    return conn


def _normalize_key(board_key: Optional[str]) -> str:
    return (board_key or "").strip() or "free"


def save_board(user_id: str, subject_id: str, board_key: Optional[str],
               payload: Dict[str, Any]) -> Dict[str, Any]:
    """覆盖式保存一版板书；返回落库时间供前端比对新旧。"""
    key = _normalize_key(board_key)
    try:
        raw = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise ValueError(f"板书内容无法序列化: {e}") from e
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("板书内容过大（超过 4MB），请删掉一些节点图片后重试")
    updated_at = _now()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO mindboard_archives
                 (user_id, subject_id, board_key, payload_json, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id, subject_id, board_key) DO UPDATE SET
                 payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
            (user_id, subject_id, key, raw, updated_at))
        conn.commit()
    finally:
        conn.close()
    return {"board_key": key, "updated_at": updated_at}


def load_board(user_id: str, subject_id: str, board_key: Optional[str]) -> Optional[Dict[str, Any]]:
    """取一版板书；没有存档返回 None。"""
    key = _normalize_key(board_key)
    conn = _get_db()
    try:
        row = conn.execute(
            """SELECT payload_json, updated_at FROM mindboard_archives
               WHERE user_id=? AND subject_id=? AND board_key=?""",
            (user_id, subject_id, key)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["updated_at"] = row["updated_at"]
    return payload


__all__ = ["save_board", "load_board", "MAX_PAYLOAD_BYTES"]
