"""
可管理的学习记忆（阶段二）

区分三类信息（**不能混为一谈**）：

| 类型 | memory_type | 来源 | 说明 |
|---|---|---|---|
| 用户明确表达的偏好/约束 | `preference` | explicit_user | 学生自己设置，优先级最高 |
| 根据学习记录推断的暂时判断 | `inference` | inferred | 有依据、有样本量、**有有效期**，可被否定 |
| 教师/课程资料确认的知识 | `curriculum` | teacher / curriculum | 只有经确认才写，未核验内容不得进入 |

设计约束（对应本轮要求）：
- 记忆保存来源、时间、适用范围（scope）、状态
- 学生可查看 / 更正 / 删除 / 关闭个性化记忆
- **显式设置优先于推断**：同 key 存在生效的显式偏好时，推断值不参与教学
- 新的有效信息替代过时信息（旧值进历史表，不物理删除）
- **不把一次错误永久保存成能力标签**：推断必须有最小样本量与有效期
- 未经核验的回答、点赞、用户纠正都**不能**直接成为课程权威知识
"""
from config.path_config import get_data_dir

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from utils.logger import logger

# 记忆库与用户画像同库（user_agent.db）
_DATA_DIR = str(get_data_dir())
DB_PATH = os.path.join(_DATA_DIR, "user_agent.db")

# 记忆类型
MEMORY_PREFERENCE = "preference"      # 学生显式表达
MEMORY_INFERENCE = "inference"        # 系统推断（暂时判断）
MEMORY_CURRICULUM = "curriculum"      # 教师/课程资料确认

MEMORY_TYPES = (MEMORY_PREFERENCE, MEMORY_INFERENCE, MEMORY_CURRICULUM)

# 来源
SOURCE_EXPLICIT_USER = "explicit_user"
SOURCE_INFERRED = "inferred"
SOURCE_TEACHER = "teacher"
SOURCE_CURRICULUM = "curriculum"

# 状态
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"          # 学生关闭个性化（保留记录）
STATUS_SUPERSEDED = "superseded"      # 被新值替代
STATUS_DELETED = "deleted"            # 学生删除（软删除，保留可追溯）

# 全局范围（不限学科）
SCOPE_GLOBAL = "global"

# 推断规则（集中配置，可解释、可调）
INFERENCE_RULES = {
    # 至少这么多次独立答错、且没有独立答对，才推断"需要加强"
    "weak_min_wrong": 2,
    "weak_max_correct": 0,
    # 推断有效期（天）——过期即不再影响教学，避免"一次错误变成永久标签"
    "weak_ttl_days": 14,
    # 连续逾期任务数达到该值 → 推断"节奏偏慢"
    "slow_pace_min_overdue": 2,
    "slow_pace_ttl_days": 21,
    # 知识点掌握（暂估）高于该值且已巩固 → 推断"可进入综合练习"
    "advanced_mastery": 0.7,
    "advanced_ttl_days": 30,
}


class MemoryError(Exception):
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
    conn.execute(_MEMORY_DDL)
    conn.execute(_HISTORY_DDL)
    conn.commit()
    return conn


_MEMORY_DDL = """
CREATE TABLE IF NOT EXISTS learning_memory (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    memory_type TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL,
    status TEXT NOT NULL DEFAULT 'active',
    evidence_json TEXT DEFAULT '{}',
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
)
"""

_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS learning_memory_history (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    action TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    created_at TEXT NOT NULL
)
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_id(prefix: str = "mem") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _row_to_memory(row) -> Dict[str, Any]:
    keys = row.keys()
    try:
        evidence = json.loads(row["evidence_json"] or "{}")
    except (ValueError, TypeError):
        evidence = {}
    item = {
        "id": row["id"],
        "user_id": row["user_id"],
        "scope": row["scope"],
        "memory_type": row["memory_type"],
        "key": row["key"],
        "value": row["value"],
        "source": row["source"],
        "confidence": row["confidence"],
        "status": row["status"],
        "evidence": evidence,
        "note": row["note"] if "note" in keys else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "expires_at": row["expires_at"],
    }
    item["type_label"] = {
        MEMORY_PREFERENCE: "你的明确设置",
        MEMORY_INFERENCE: "系统根据记录的暂时判断",
        MEMORY_CURRICULUM: "课程资料确认",
    }.get(item["memory_type"], item["memory_type"])
    item["is_expired"] = _is_expired(item["expires_at"])
    return item


def _is_expired(expires_at: Optional[str]) -> bool:
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(str(expires_at)[:19]) < datetime.now()
    except ValueError:
        return False


def _log_history(conn, memory_id: str, user_id: str, action: str,
                 old_value: Optional[str], new_value: Optional[str],
                 reason: Optional[str] = None) -> None:
    conn.execute(
        """INSERT INTO learning_memory_history
           (id, memory_id, user_id, action, old_value, new_value, reason, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (_new_id("memh"), memory_id, user_id, action, old_value, new_value,
         reason, _now()))


# ==================== 写入 ====================

def set_memory(
    user_id: str,
    key: str,
    value: str,
    memory_type: str = MEMORY_PREFERENCE,
    scope: str = SCOPE_GLOBAL,
    source: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    confidence: Optional[float] = None,
    ttl_days: Optional[int] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """
    写入/更新一条记忆（同 user+scope+type+key 唯一，新值替代旧值）。

    - 显式偏好：source=explicit_user，无有效期
    - 推断：source=inferred，建议给 ttl_days（过期自动不再参与教学）
    - 旧值写入历史表，可追溯谁在什么时候改了什么
    """
    if not user_id or not key or value is None:
        raise MemoryError("缺少必要参数", "invalid_request")
    if memory_type not in MEMORY_TYPES:
        raise MemoryError(f"未知记忆类型: {memory_type}", "invalid_request")

    if source is None:
        source = {
            MEMORY_PREFERENCE: SOURCE_EXPLICIT_USER,
            MEMORY_INFERENCE: SOURCE_INFERRED,
            MEMORY_CURRICULUM: SOURCE_CURRICULUM,
        }[memory_type]

    if memory_type == MEMORY_INFERENCE and not evidence:
        # 推断必须带依据，否则就是凭空贴标签
        raise MemoryError("推断类记忆必须提供依据（evidence）", "invalid_request")

    expires_at = None
    if ttl_days:
        expires_at = (datetime.now() + timedelta(days=ttl_days)).isoformat(timespec="seconds")

    now = _now()
    conn = _get_db()
    try:
        existing = conn.execute(
            """SELECT * FROM learning_memory
               WHERE user_id=? AND scope=? AND memory_type=? AND key=?""",
            (user_id, scope, memory_type, key)).fetchone()

        if existing:
            if existing["value"] == str(value) and existing["status"] == STATUS_ACTIVE:
                # 值未变化：仅刷新有效期/依据，不产生历史噪音
                conn.execute(
                    """UPDATE learning_memory
                       SET evidence_json=?, confidence=?, expires_at=?, updated_at=?
                       WHERE id=?""",
                    (json.dumps(evidence or {}, ensure_ascii=False), confidence,
                     expires_at, now, existing["id"]))
                conn.commit()
                return _row_to_memory(conn.execute(
                    "SELECT * FROM learning_memory WHERE id=?", (existing["id"],)).fetchone())

            _log_history(conn, existing["id"], user_id, "update",
                         existing["value"], str(value), note)
            conn.execute(
                """UPDATE learning_memory
                   SET value=?, source=?, confidence=?, status=?, evidence_json=?,
                       note=?, updated_at=?, expires_at=?
                   WHERE id=?""",
                (str(value), source, confidence, STATUS_ACTIVE,
                 json.dumps(evidence or {}, ensure_ascii=False), note, now, expires_at,
                 existing["id"]))
            conn.commit()
            return _row_to_memory(conn.execute(
                "SELECT * FROM learning_memory WHERE id=?", (existing["id"],)).fetchone())

        mid = _new_id()
        conn.execute(
            """INSERT INTO learning_memory
               (id, user_id, scope, memory_type, key, value, source, confidence,
                status, evidence_json, note, created_at, updated_at, expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mid, user_id, scope, memory_type, key, str(value), source, confidence,
             STATUS_ACTIVE, json.dumps(evidence or {}, ensure_ascii=False), note,
             now, now, expires_at))
        _log_history(conn, mid, user_id, "create", None, str(value), note)
        conn.commit()
        logger.info(f"写入学习记忆: user={user_id}, type={memory_type}, key={key}")
        return _row_to_memory(conn.execute(
            "SELECT * FROM learning_memory WHERE id=?", (mid,)).fetchone())
    finally:
        conn.close()


def update_memory(memory_id: str, user_id: str, value: str,
                  reason: Optional[str] = None) -> Dict[str, Any]:
    """学生更正记忆（属于'显式更正'，会转为显式来源并长期有效）"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM learning_memory WHERE id=?",
                           (memory_id,)).fetchone()
        if not row:
            raise MemoryError("记忆不存在", "not_found")
        if row["user_id"] != user_id:
            raise MemoryError("无权修改他人的记忆", "forbidden")
        _log_history(conn, memory_id, user_id, "correct", row["value"], str(value),
                     reason or "学生手动更正")
        conn.execute(
            """UPDATE learning_memory
               SET value=?, source=?, memory_type=?, status=?, confidence=1.0,
                   expires_at=NULL, note=?, updated_at=?
               WHERE id=?""",
            (str(value), SOURCE_EXPLICIT_USER, MEMORY_PREFERENCE, STATUS_ACTIVE,
             reason or "学生手动更正", _now(), memory_id))
        conn.commit()
        return _row_to_memory(conn.execute("SELECT * FROM learning_memory WHERE id=?",
                                           (memory_id,)).fetchone())
    finally:
        conn.close()


def set_status(memory_id: str, user_id: str, status: str,
               reason: Optional[str] = None) -> Dict[str, Any]:
    """关闭 / 重新启用一条记忆（关闭后不再参与教学，记录保留）"""
    if status not in (STATUS_ACTIVE, STATUS_DISABLED):
        raise MemoryError("只支持启用/关闭", "invalid_request")
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM learning_memory WHERE id=?",
                           (memory_id,)).fetchone()
        if not row:
            raise MemoryError("记忆不存在", "not_found")
        if row["user_id"] != user_id:
            raise MemoryError("无权修改他人的记忆", "forbidden")
        _log_history(conn, memory_id, user_id,
                     "enable" if status == STATUS_ACTIVE else "disable",
                     row["status"], status, reason)
        conn.execute("UPDATE learning_memory SET status=?, updated_at=? WHERE id=?",
                     (status, _now(), memory_id))
        conn.commit()
        return _row_to_memory(conn.execute("SELECT * FROM learning_memory WHERE id=?",
                                           (memory_id,)).fetchone())
    finally:
        conn.close()


def delete_memory(memory_id: str, user_id: str,
                  reason: Optional[str] = None) -> bool:
    """删除记忆（软删除：状态置 deleted + 写历史，保证可追溯且立即不再参与教学）"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM learning_memory WHERE id=?",
                           (memory_id,)).fetchone()
        if not row:
            raise MemoryError("记忆不存在", "not_found")
        if row["user_id"] != user_id:
            raise MemoryError("无权删除他人的记忆", "forbidden")
        _log_history(conn, memory_id, user_id, "delete", row["value"], None,
                     reason or "学生删除")
        conn.execute("UPDATE learning_memory SET status=?, updated_at=? WHERE id=?",
                     (STATUS_DELETED, _now(), memory_id))
        conn.commit()
        return True
    finally:
        conn.close()


def clear_user_memory(user_id: str, scope: Optional[str] = None) -> int:
    """关闭该用户的全部个性化记忆（保留记录，可追溯）"""
    conn = _get_db()
    try:
        q = ("UPDATE learning_memory SET status=?, updated_at=? "
             "WHERE user_id=? AND status=?")
        p: List[Any] = [STATUS_DISABLED, _now(), user_id, STATUS_ACTIVE]
        if scope:
            q += " AND scope=?"
            p.append(scope)
        cur = conn.execute(q, p)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ==================== 读取 ====================

def list_memory(user_id: str, include_inactive: bool = False,
                scope: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出记忆（默认只返回生效中的）"""
    conn = _get_db()
    try:
        q = "SELECT * FROM learning_memory WHERE user_id=?"
        p: List[Any] = [user_id]
        if not include_inactive:
            q += " AND status=?"
            p.append(STATUS_ACTIVE)
        if scope:
            q += " AND scope=?"
            p.append(scope)
        q += " ORDER BY memory_type, scope, key"
        return [_row_to_memory(r) for r in conn.execute(q, p).fetchall()]
    finally:
        conn.close()


def list_history(memory_id: str, user_id: str) -> List[Dict[str, Any]]:
    """查看某条记忆的变更历史"""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM learning_memory_history
               WHERE memory_id=? AND user_id=? ORDER BY created_at DESC""",
            (memory_id, user_id)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_effective_memory(user_id: str, subject_id: Optional[str] = None) -> Dict[str, Any]:
    """
    计算"真正会参与教学"的记忆集合。

    规则：
    - 只取 status=active 且未过期
    - scope 为 global 或与当前学科匹配
    - **显式偏好覆盖同 key 的推断**（显式优先于推断）
    - 被关闭/删除/过期的推断自动失效，不需要学生逐个清理
    """
    items = list_memory(user_id, include_inactive=False)
    effective: Dict[str, Dict[str, Any]] = {}
    shadowed: List[Dict[str, Any]] = []

    for it in items:
        if it["scope"] not in (SCOPE_GLOBAL, subject_id or SCOPE_GLOBAL):
            continue
        if it["is_expired"]:
            continue
        slot = f"{it['scope']}:{it['key']}"
        current = effective.get(slot)
        if current is None:
            effective[slot] = it
            continue
        # 显式偏好优先；其余按更新时间取新
        if it["memory_type"] == MEMORY_PREFERENCE and current["memory_type"] != MEMORY_PREFERENCE:
            shadowed.append(current)
            effective[slot] = it
        elif it["memory_type"] == current["memory_type"]:
            if it["updated_at"] > current["updated_at"]:
                shadowed.append(current)
                effective[slot] = it
        else:
            shadowed.append(it)

    return {
        "items": list(effective.values()),
        "shadowed": shadowed,
        "count": len(effective),
    }


# ==================== 推断（从真实记录生成，不凭空造） ====================

def infer_from_learning_record(user_id: str, subject_id: str) -> List[Dict[str, Any]]:
    """
    根据**真实作答与任务记录**生成推断类记忆。

    只做三条有明确依据的推断，且都带样本量与有效期：
    1. 需要加强的知识点：独立答错 >= 2 次且没有独立答对
    2. 节奏偏慢：逾期任务 >= 2 个
    3. 可进入综合练习：已巩固且掌握估计 >= 0.7

    不做的事：不因为"错过一次"就贴标签；不推断性格 / 能力 / 学习风格。
    """
    from services import mastery_service, learning_service

    created: List[Dict[str, Any]] = []
    try:
        points = mastery_service.list_mastery(user_id, subject_id)
    except Exception as e:
        logger.warning(f"读取掌握度失败，跳过推断: {e}")
        points = []

    for p in points:
        kp = p["knowledge_point"]
        bd = p.get("evidence_breakdown") or {}
        wrong = bd.get("independent_wrong", 0)
        correct = bd.get("independent_correct", 0)
        sample = correct + wrong

        if wrong >= INFERENCE_RULES["weak_min_wrong"] and correct <= INFERENCE_RULES["weak_max_correct"]:
            created.append(set_memory(
                user_id=user_id, key=f"weak_point:{p.get('knowledge_point_id') or kp}",
                value="需要加强", memory_type=MEMORY_INFERENCE, scope=subject_id,
                evidence={"knowledge_point": kp, "independent_wrong": wrong,
                          "independent_correct": correct, "sample_size": sample},
                confidence=min(0.9, 0.5 + 0.1 * wrong),
                ttl_days=INFERENCE_RULES["weak_ttl_days"],
                note=f"最近 {sample} 次独立作答中有 {wrong} 次答错（会自动过期，"
                     f"答对后即消除）"))

        if (p.get("consolidated") and p.get("mastery_level") is not None
                and p["mastery_level"] >= INFERENCE_RULES["advanced_mastery"]):
            created.append(set_memory(
                user_id=user_id, key=f"advanced_point:{p.get('knowledge_point_id') or kp}",
                value="可进入综合练习", memory_type=MEMORY_INFERENCE, scope=subject_id,
                evidence={"knowledge_point": kp, "mastery_level": p["mastery_level"],
                          "sample_size": sample, "consolidated": True},
                confidence=0.8,
                ttl_days=INFERENCE_RULES["advanced_ttl_days"],
                note=f"已通过间隔复现（独立作答样本 {sample} 次）"))

    # 节奏偏慢：逾期任务数量
    try:
        buckets = learning_service.get_task_buckets(user_id, subject_id)
        overdue = len(buckets.get("overdue") or [])
        if overdue >= INFERENCE_RULES["slow_pace_min_overdue"]:
            created.append(set_memory(
                user_id=user_id, key="pace", value="偏慢",
                memory_type=MEMORY_INFERENCE, scope=subject_id,
                evidence={"overdue_tasks": overdue},
                confidence=0.6, ttl_days=INFERENCE_RULES["slow_pace_ttl_days"],
                note=f"当前有 {overdue} 个逾期任务；后续排期会留出更多缓冲"))
    except Exception as e:
        logger.warning(f"读取任务视图失败，跳过节奏推断: {e}")

    return created


def revise_inference_after_evidence(user_id: str, subject_id: str,
                                    knowledge_point_id: str) -> Optional[str]:
    """
    知识点已答对时，撤销"需要加强"的推断（**不让一次错误变成永久标签**）。

    Returns: 被撤销的 memory_id，未撤销返回 None
    """
    if not knowledge_point_id:
        return None
    key = f"weak_point:{knowledge_point_id}"
    conn = _get_db()
    try:
        row = conn.execute(
            """SELECT * FROM learning_memory
               WHERE user_id=? AND scope=? AND memory_type=? AND key=? AND status=?""",
            (user_id, subject_id, MEMORY_INFERENCE, key, STATUS_ACTIVE)).fetchone()
        if not row:
            return None
        _log_history(conn, row["id"], user_id, "revise", row["value"], None,
                     "该知识点已独立答对，撤销'需要加强'判断")
        conn.execute("UPDATE learning_memory SET status=?, updated_at=? WHERE id=?",
                     (STATUS_SUPERSEDED, _now(), row["id"]))
        conn.commit()
        logger.info(f"撤销推断: user={user_id}, kp_id={knowledge_point_id}")
        return row["id"]
    finally:
        conn.close()


__all__ = [
    "MemoryError",
    "MEMORY_PREFERENCE",
    "MEMORY_INFERENCE",
    "MEMORY_CURRICULUM",
    "MEMORY_TYPES",
    "SOURCE_EXPLICIT_USER",
    "SOURCE_INFERRED",
    "SOURCE_TEACHER",
    "SOURCE_CURRICULUM",
    "STATUS_ACTIVE",
    "STATUS_DISABLED",
    "STATUS_SUPERSEDED",
    "STATUS_DELETED",
    "SCOPE_GLOBAL",
    "INFERENCE_RULES",
    "set_memory",
    "update_memory",
    "set_status",
    "delete_memory",
    "clear_user_memory",
    "list_memory",
    "list_history",
    "get_effective_memory",
    "infer_from_learning_record",
    "revise_inference_after_evidence",
]
