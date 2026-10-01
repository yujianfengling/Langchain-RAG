"""
步骤级幂等台账（阶段一补验 / 阶段二）

问题：一次业务事件往往要跨多个数据库完成多步写入
（作答 → 画像证据 → 错题 → 复习任务 → 计划任务）。只靠事件本身的
DONE / FAILED 标记不足以保证重试安全：

    第 1 步已提交 → 第 2 步抛错 → 事件标记 FAILED → 重试
    ⇒ 第 1 步会被重复执行（掌握度被重复累加、merged_count 被重复递增）

做法：把 (scope, key) 的台账写入与**该步骤自己的业务写入**放进
同一个数据库、同一个事务里提交：

- 步骤成功 → 台账有记录 → 重试时直接跳过
- 步骤失败 → 事务回滚 → 台账无记录 → 重试时重跑

因此每一步都独立幂等，不需要分布式事务，也不依赖事件最终状态。
台账表在各自数据库中创建（见 ensure_ledger），跨库步骤各自记账。

用法：

    from services.idempotency import step_guard

    conn = _get_db()
    if not begin_step(conn, "mastery.record_evidence", f"{user_id}:{attempt_id}"):
        conn.close()
        return {"counted": False, "reason": "该作答已计入证据"}
    ...业务写入...
    conn.commit()      # 台账与业务写入一起提交

异常路径请调用 `rollback_step(conn)` 或让事务自然回滚（不 commit）。
"""

import sqlite3
from typing import Optional

from utils.logger import logger

# 台账表（各库各自创建同名表）
LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS step_ledger (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scope, key)
)
"""

LEDGER_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_step_ledger_scope ON step_ledger(scope, created_at)"
)


def ensure_ledger(conn: sqlite3.Connection) -> None:
    """确保当前连接所在库存在台账表（幂等）"""
    conn.execute(LEDGER_DDL)
    conn.execute(LEDGER_INDEX_DDL)


def begin_step(conn: sqlite3.Connection, scope: str, key: str,
               created_at: Optional[str] = None) -> bool:
    """
    登记一次步骤执行。

    Returns:
        True  —— 首次执行（调用方应继续执行业务写入，并在同一事务内 commit）
        False —— 该步骤已执行过（调用方应跳过业务写入，避免重复）
    """
    if not scope or not key:
        raise ValueError("步骤幂等需要 scope 与 key")
    ensure_ledger(conn)
    try:
        conn.execute(
            "INSERT INTO step_ledger (scope, key, created_at) VALUES (?,?,?)",
            (scope, key, created_at or _now_stamp()))
        return True
    except sqlite3.IntegrityError:
        return False
    except sqlite3.Error as e:
        logger.error(f"步骤幂等登记失败: scope={scope}, key={key}, err={e}")
        raise


def is_step_done(conn: sqlite3.Connection, scope: str, key: str) -> bool:
    """查询步骤是否已执行（只读；表不存在视为未执行）"""
    try:
        row = conn.execute(
            "SELECT 1 FROM step_ledger WHERE scope=? AND key=?", (scope, key)).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def forget_step(conn: sqlite3.Connection, scope: str, key: str) -> bool:
    """
    撤销某步骤的登记（用于"用户更正/重算"等显式场景，不做自动回滚）。

    Returns: 是否删除了记录
    """
    try:
        cur = conn.execute("DELETE FROM step_ledger WHERE scope=? AND key=?", (scope, key))
        return cur.rowcount > 0
    except sqlite3.Error:
        return False


def _now_stamp() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


__all__ = [
    "LEDGER_DDL",
    "ensure_ledger",
    "begin_step",
    "is_step_done",
    "forget_step",
]
