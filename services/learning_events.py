"""
统一学习联动事件（阶段一）

用途：把"提交作答、更新画像、记录错题、安排复习、刷新计划"等后续动作
收敛到一个轻量事件层，保证：

- 幂等：同一 idempotency_key 只产生一次业务结果（并发/重试安全）
- 不丢核心数据：核心结果（作答记录）先落库，事件失败不影响已保存的数据
- 可追溯：事件状态、尝试次数、最后错误落库；失败可重试
- 不引入消息中间件/微服务（进程内轻量实现）

事件处理失败只记录并标记 FAILED，不影响调用方返回；由 retry_failed_events 重试。
"""

import json
import sqlite3
from typing import Callable, Dict, List, Any, Optional

from utils.logger import logger
from services.learning_data import _get_db, _now, _new_id

EVENT_PENDING = "PENDING"
EVENT_DONE = "DONE"
EVENT_FAILED = "FAILED"

_HANDLERS: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}


def register_handler(event_type: str, handler: Callable[[Dict[str, Any]], None]) -> None:
    """注册事件处理器（同一事件可注册多个，按注册顺序执行）"""
    _HANDLERS.setdefault(event_type, []).append(handler)


class LearningEventError(Exception):
    """事件层异常"""

    def __init__(self, message: str, error_type: str = "event_error"):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


def emit_event(
    event_type: str,
    idempotency_key: str,
    user_id: Optional[str] = None,
    subject_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    发送事件并同步执行处理器。

    Returns:
        {"event_id", "status", "duplicated", "handler_errors": [...]}
    """
    if not event_type:
        raise LearningEventError("event_type 不能为空", "invalid_request")
    if not idempotency_key:
        raise LearningEventError("idempotency_key 不能为空", "invalid_request")

    payload = payload or {}
    conn = _get_db()
    event_id = None
    duplicated = False
    try:
        existing = conn.execute(
            "SELECT * FROM learning_events WHERE idempotency_key=?",
            (idempotency_key,)).fetchone()
        if existing:
            duplicated = True
            event_id = existing["id"]
            if existing["status"] == EVENT_DONE:
                # 已成功处理过 → 直接返回，不重复产生业务结果
                return {"event_id": event_id, "status": EVENT_DONE,
                        "duplicated": True, "handler_errors": []}
            # FAILED/PENDING → 允许重试
            conn.execute(
                "UPDATE learning_events SET status=?, attempts=attempts+1, updated_at=? WHERE id=?",
                (EVENT_PENDING, _now(), event_id))
            conn.commit()
        else:
            event_id = _new_id("evt")
            conn.execute(
                """INSERT INTO learning_events
                   (id, event_type, idempotency_key, user_id, subject_id, payload_json,
                    status, attempts, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (event_id, event_type, idempotency_key, user_id, subject_id,
                 json.dumps(payload, ensure_ascii=False), EVENT_PENDING, 1, _now(), _now()))
            conn.commit()
    except sqlite3.IntegrityError:
        # 并发插入：另一个请求已创建同 key 事件
        row = conn.execute(
            "SELECT * FROM learning_events WHERE idempotency_key=?",
            (idempotency_key,)).fetchone()
        conn.close()
        return {"event_id": row["id"] if row else None,
                "status": row["status"] if row else EVENT_PENDING,
                "duplicated": True, "handler_errors": []}
    except sqlite3.Error as e:
        logger.error(f"事件写入失败: {e}", exc_info=True)
        raise LearningEventError("事件写入失败", "database_error")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # 执行处理器（失败不影响调用方；记录错误供重试）
    ctx = {"event_id": event_id, "event_type": event_type, "user_id": user_id,
           "subject_id": subject_id, "payload": payload}
    handler_errors: List[str] = []
    for handler in _HANDLERS.get(event_type, []):
        try:
            handler(ctx)
        except Exception as e:
            name = getattr(handler, "__name__", repr(handler))
            logger.warning(f"事件处理器失败 [{event_type}/{name}]: {e}")
            handler_errors.append(f"{name}: {e}")

    _finish(event_id, not handler_errors, "; ".join(handler_errors) or None)
    return {"event_id": event_id, "status": EVENT_DONE if not handler_errors else EVENT_FAILED,
            "duplicated": duplicated, "handler_errors": handler_errors}


def _finish(event_id: str, ok: bool, error: Optional[str]) -> None:
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE learning_events SET status=?, last_error=?, updated_at=? WHERE id=?",
            (EVENT_DONE if ok else EVENT_FAILED, error, _now(), event_id))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"事件状态更新失败: {e}")
    finally:
        conn.close()


def retry_failed_events(limit: int = 50, user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    重试失败事件（供定时任务或接口手动触发）。

    Args:
        limit: 单次最多重试条数
        user_id: 仅重试该用户的事件（接口侧必须传入，避免跨用户重放）
    """
    conn = _get_db()
    try:
        if user_id:
            rows = conn.execute(
                "SELECT * FROM learning_events WHERE status=? AND user_id=? "
                "ORDER BY updated_at LIMIT ?",
                (EVENT_FAILED, user_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM learning_events WHERE status=? ORDER BY updated_at LIMIT ?",
                (EVENT_FAILED, limit)).fetchall()
    finally:
        conn.close()

    retried, succeeded, still_failed = 0, 0, 0
    for r in rows:
        retried += 1
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except ValueError:
            payload = {}
        ctx = {"event_id": r["id"], "event_type": r["event_type"], "user_id": r["user_id"],
               "subject_id": r["subject_id"], "payload": payload}
        errors: List[str] = []
        for handler in _HANDLERS.get(r["event_type"], []):
            try:
                handler(ctx)
            except Exception as e:
                errors.append(f"{getattr(handler, '__name__', 'handler')}: {e}")
        _finish(r["id"], not errors, "; ".join(errors) or None)
        if errors:
            still_failed += 1
        else:
            succeeded += 1
    return {"retried": retried, "succeeded": succeeded, "failed": still_failed}


def get_event(event_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM learning_events WHERE id=?", (event_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _public_event(row) -> Dict[str, Any]:
    """事件对外结构（不返回内部 payload 明细以外的敏感字段）"""
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except ValueError:
        payload = {}
    return {
        "event_id": row["id"],
        "event_type": row["event_type"],
        "subject_id": row["subject_id"],
        "status": row["status"],
        "attempts": row["attempts"],
        "last_error": row["last_error"],
        "payload": payload,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_events(user_id: str, subject_id: Optional[str] = None,
                status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """列出某用户的学习事件（用于可追溯性与失败排查）"""
    conn = _get_db()
    try:
        sql = "SELECT * FROM learning_events WHERE user_id=?"
        p: List[Any] = [user_id]
        if subject_id:
            sql += " AND subject_id=?"
            p.append(subject_id)
        if status:
            sql += " AND status=?"
            p.append(status)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        p.append(limit)
        return [_public_event(r) for r in conn.execute(sql, p).fetchall()]
    finally:
        conn.close()


__all__ = [
    "EVENT_PENDING",
    "EVENT_DONE",
    "EVENT_FAILED",
    "LearningEventError",
    "register_handler",
    "emit_event",
    "retry_failed_events",
    "get_event",
    "list_events",
]
