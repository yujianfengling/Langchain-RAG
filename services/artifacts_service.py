"""
学习成果（artifact）服务（2026-10-07 统一伴学改造）

成果类型：思维导图/学习路线、节点笔记、总结提纲、练习与检测结果、学习计划、复习卡片。
参考 NotebookLM 的「资料 → 问答 → 成果」分层与 DeepTutor 的 artifact 组织方式，
本项目落到一张表：

- 成果保留：用户、学科、资料范围、来源版本签名、生成时间、编辑状态与版本号。
- 重新生成不会悄悄覆盖用户修改：已有成果被编辑过时，重新生成会新建一个版本并在旧版本上
  标记 superseded，而不是原地覆盖。
- 资料变化（当前资料范围签名与成果的来源签名不一致）时给出 stale 提示，不自动删除。

存放于 get_data_dir()/tutor.db（与统一会话同库，便于一次事务内保证一致）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from config.path_config import get_data_dir
from utils.logger import logger

_DATA_DIR = str(get_data_dir())
DB_PATH = os.path.join(_DATA_DIR, "tutor.db")

KIND_NOTE = "note"
KIND_MINDMAP = "mindmap"
KIND_OUTLINE = "outline"
KIND_PLAN = "plan"
KIND_PRACTICE = "practice_set"
KIND_REVIEW = "review_cards"
KINDS = (KIND_NOTE, KIND_MINDMAP, KIND_OUTLINE, KIND_PLAN, KIND_PRACTICE, KIND_REVIEW)

KIND_LABELS = {
    KIND_NOTE: "笔记",
    KIND_MINDMAP: "知识地图",
    KIND_OUTLINE: "学习提纲",
    KIND_PLAN: "学习计划",
    KIND_PRACTICE: "练习与检测",
    KIND_REVIEW: "复习卡片",
}

_DDL = """
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT,
    content_json TEXT NOT NULL DEFAULT '{}',
    node_id TEXT,
    source_scope_json TEXT DEFAULT '{}',
    source_signature TEXT,
    origin TEXT DEFAULT 'agent',
    edited INTEGER DEFAULT 0,
    version INTEGER DEFAULT 1,
    superseded_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
    conn.execute(_DDL)
    conn.commit()
    return conn


def _loads(raw: Any, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _row_to_artifact(row: sqlite3.Row, current_signature: Optional[str] = None) -> Dict[str, Any]:
    data = dict(row)
    data["content"] = _loads(data.pop("content_json", "{}"), {})
    data["source_scope"] = _loads(data.pop("source_scope_json", "{}"), {})
    data["edited"] = bool(data.get("edited"))
    data["kind_label"] = KIND_LABELS.get(data.get("kind"), data.get("kind"))
    data["stale"] = bool(
        current_signature and data.get("source_signature")
        and data["source_signature"] != current_signature)
    return data


def create_artifact(user_id: str, subject_id: str, kind: str, title: str,
                    content: Dict[str, Any], *, node_id: Optional[str] = None,
                    source_scope: Optional[Dict[str, Any]] = None,
                    source_signature: Optional[str] = None,
                    origin: str = "agent") -> Dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"未知的成果类型: {kind}")
    artifact_id = "art_" + uuid.uuid4().hex[:12]
    now = _now()
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO artifacts (id, user_id, subject_id, kind, title, content_json,"
            " node_id, source_scope_json, source_signature, origin, edited, version,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,0,1,?,?)",
            (artifact_id, user_id, subject_id, kind, (title or "")[:200],
             json.dumps(content or {}, ensure_ascii=False), node_id,
             json.dumps(source_scope or {}, ensure_ascii=False), source_signature,
             origin, now, now))
        conn.commit()
    finally:
        conn.close()
    return get_artifact(user_id, artifact_id) or {}


def get_artifact(user_id: str, artifact_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM artifacts WHERE id=? AND user_id=?",
                           (artifact_id, user_id)).fetchone()
    finally:
        conn.close()
    return _row_to_artifact(row) if row else None


def list_artifacts(user_id: str, subject_id: Optional[str] = None, kind: Optional[str] = None,
                   current_signature: Optional[str] = None,
                   limit: int = 100) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM artifacts WHERE user_id=? AND superseded_by IS NULL"
    params: List[Any] = [user_id]
    if subject_id:
        sql += " AND subject_id=?"
        params.append(subject_id)
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(int(limit))
    conn = _get_db()
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()
    return [_row_to_artifact(r, current_signature) for r in rows]


def update_artifact(user_id: str, artifact_id: str, *,
                    title: Optional[str] = None,
                    content: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """用户编辑：标记 edited=1（此后重新生成不会原地覆盖）。"""
    current = get_artifact(user_id, artifact_id)
    if not current:
        return None
    fields = ["edited=1", "updated_at=?"]
    values: List[Any] = [_now()]
    if title is not None:
        fields.append("title=?")
        values.append(title[:200])
    if content is not None:
        fields.append("content_json=?")
        values.append(json.dumps(content, ensure_ascii=False))
    values.extend([artifact_id, user_id])
    conn = _get_db()
    try:
        conn.execute(f"UPDATE artifacts SET {', '.join(fields)} WHERE id=? AND user_id=?",
                     tuple(values))
        conn.commit()
    finally:
        conn.close()
    return get_artifact(user_id, artifact_id)


def save_generated(user_id: str, subject_id: str, kind: str, title: str,
                   content: Dict[str, Any], *, node_id: Optional[str] = None,
                   source_scope: Optional[Dict[str, Any]] = None,
                   source_signature: Optional[str] = None,
                   origin: str = "agent") -> Dict[str, Any]:
    """
    Agent 生成成果的落库入口。

    - 已有**未被编辑**的同类同主题成果 → 原地更新并递增版本（用户能看到是重新生成的）。
    - 已有**被编辑**的成果 → 新建版本，旧的标记 superseded_by，绝不覆盖用户修改。
    """
    existing = None
    for item in list_artifacts(user_id, subject_id, kind):
        if item.get("title") == (title or "")[:200] and (
                (node_id and item.get("node_id") == node_id) or (not node_id and not item.get("node_id"))):
            existing = item
            break
    return _save_generated_impl(user_id, subject_id, kind, title, content,
                                node_id, source_scope, source_signature, origin, existing)


def _save_generated_impl(user_id, subject_id, kind, title, content, node_id,
                         source_scope, source_signature, origin, existing) -> Dict[str, Any]:
    if existing and not existing.get("edited"):
        conn = _get_db()
        try:
            conn.execute(
                "UPDATE artifacts SET content_json=?, source_scope_json=?, source_signature=?,"
                " version=version+1, updated_at=? WHERE id=?",
                (json.dumps(content or {}, ensure_ascii=False),
                 json.dumps(source_scope or {}, ensure_ascii=False), source_signature,
                 _now(), existing["id"]))
            conn.commit()
        finally:
            conn.close()
        return get_artifact(user_id, existing["id"]) or {}
    new = create_artifact(user_id, subject_id, kind, title, content, node_id=node_id,
                          source_scope=source_scope, source_signature=source_signature,
                          origin=origin)
    if existing:
        conn = _get_db()
        try:
            conn.execute("UPDATE artifacts SET superseded_by=?, updated_at=? WHERE id=?",
                         (new["id"], _now(), existing["id"]))
            conn.commit()
        finally:
            conn.close()
        logger.info(f"成果 {existing['id']} 已被用户编辑，重新生成写入新版本 {new['id']}")
    return new


def delete_artifact(user_id: str, artifact_id: str) -> bool:
    conn = _get_db()
    try:
        cur = conn.execute("DELETE FROM artifacts WHERE id=? AND user_id=?",
                           (artifact_id, user_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


__all__ = [
    "KINDS", "KIND_LABELS", "KIND_NOTE", "KIND_MINDMAP", "KIND_OUTLINE", "KIND_PLAN",
    "KIND_PRACTICE", "KIND_REVIEW", "create_artifact", "delete_artifact", "get_artifact",
    "list_artifacts", "save_generated", "update_artifact", "DB_PATH",
]
