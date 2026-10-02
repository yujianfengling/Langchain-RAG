"""
外部调用账本（成本可见）

为什么需要：一次联网出题 = 1 次搜索 + 1 次模型；预生成 8 个知识点 × 3 题起步；
简答题每次提交还多一次判分。Tavily 与模型都是**按量计费**的，
而项目此前对这两类消耗没有任何计数、限流或预算——一旦失控就是真金白银。

设计取舍：
- 只记**成功的调用**（失败请求大多不计费，宁可少算也不虚报）
- 记账绝不能拖垮主流程：读写全吞异常、只记日志
- 预算超限**只阻断后台预生成**，绝不阻断学生交互
  （学生答题不能被预算卡住；预生成是锦上添花，可以等明天）
- 落盘位置走 get_data_dir()，因此测试的 EDU_DATA_DIR 隔离自动生效
"""

import os
import sqlite3
from datetime import date, datetime
from typing import Any, Dict, Optional

from config.path_config import get_data_dir
from utils.logger import logger

KIND_LLM = "llm"
KIND_WEB_SEARCH = "web_search"
KIND_VISION = "vision"
KIND_SPEECH = "speech"
ALL_KINDS = (KIND_LLM, KIND_WEB_SEARCH, KIND_VISION, KIND_SPEECH)

# 默认当日预算（次）；0 或负数 = 不限制
_DEFAULT_BUDGETS = {
    KIND_LLM: 2000,
    KIND_WEB_SEARCH: 300,
    KIND_VISION: 500,
    KIND_SPEECH: 200,
}
_ENV_KEYS = {
    KIND_LLM: "USAGE_DAILY_BUDGET_LLM",
    KIND_WEB_SEARCH: "USAGE_DAILY_BUDGET_WEB_SEARCH",
    KIND_VISION: "USAGE_DAILY_BUDGET_VISION",
    KIND_SPEECH: "USAGE_DAILY_BUDGET_SPEECH",
}


def _db_path():
    """账本库路径（每次调用现算，保证 EDU_DATA_DIR 覆盖即时生效）"""
    return get_data_dir() / "usage_ledger.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS usage_counters (
               day TEXT NOT NULL,
               kind TEXT NOT NULL,
               subject_id TEXT NOT NULL DEFAULT '',
               count INTEGER NOT NULL DEFAULT 0,
               updated_at TEXT NOT NULL,
               PRIMARY KEY (day, kind, subject_id)
           )"""
    )
    return conn


def record(kind: str, subject_id: Optional[str] = None, count: int = 1) -> None:
    """记一次成功的外部调用。任何异常都只记日志，绝不影响主流程。"""
    if not kind or count <= 0:
        return
    try:
        conn = _connect()
        try:
            conn.execute(
                """INSERT INTO usage_counters (day, kind, subject_id, count, updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(day, kind, subject_id)
                   DO UPDATE SET count = count + excluded.count,
                                 updated_at = excluded.updated_at""",
                (date.today().isoformat(), kind, subject_id or "", int(count),
                 datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"用量记账失败（不影响本次调用）: {e}")


def get_usage(day: Optional[str] = None, subject_id: Optional[str] = None) -> Dict[str, Any]:
    """查询某天的用量（默认今天）"""
    target_day = day or date.today().isoformat()
    try:
        conn = _connect()
        try:
            q = "SELECT kind, subject_id, count FROM usage_counters WHERE day=?"
            params: list = [target_day]
            if subject_id:
                q += " AND subject_id=?"
                params.append(subject_id)
            rows = conn.execute(q, params).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"用量查询失败: {e}")
        return {"day": target_day, "total": 0, "by_kind": {}, "by_subject": {},
                "error": True}

    by_kind: Dict[str, int] = {}
    by_subject: Dict[str, int] = {}
    total = 0
    for kind, subj, n in rows:
        by_kind[kind] = by_kind.get(kind, 0) + int(n)
        key = subj or "-"
        by_subject[key] = by_subject.get(key, 0) + int(n)
        total += int(n)
    return {"day": target_day, "total": total, "by_kind": by_kind, "by_subject": by_subject}


def budget_for(kind: str) -> int:
    """当日预算（次）。0 或负数 = 不限制"""
    try:
        raw = os.getenv(_ENV_KEYS.get(kind, ""), "")
        if raw != "":
            return int(raw)
    except (TypeError, ValueError):
        pass
    return _DEFAULT_BUDGETS.get(kind, 0)


def is_over_budget(kind: str) -> bool:
    """当天该能力是否已用满预算"""
    limit = budget_for(kind)
    if limit <= 0:
        return False
    used = get_usage().get("by_kind", {}).get(kind, 0)
    return used >= limit


def budget_status() -> Dict[str, Any]:
    """当日预算概览（供健康检查 / 管理端展示，不含任何密钥）"""
    usage = get_usage()
    used = usage.get("by_kind", {})
    kinds: Dict[str, Any] = {}
    for kind in ALL_KINDS:
        limit = budget_for(kind)
        used_n = used.get(kind, 0)
        kinds[kind] = {
            "used": used_n,
            "limit": limit,          # 0 = 不限制
            "over": bool(limit > 0 and used_n >= limit),
        }
    return {"day": usage["day"], "total": usage["total"], "kinds": kinds}


__all__ = [
    "KIND_LLM", "KIND_WEB_SEARCH", "KIND_VISION", "KIND_SPEECH", "ALL_KINDS",
    "record", "get_usage", "budget_for", "is_over_budget", "budget_status",
]
