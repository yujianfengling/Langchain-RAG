"""FSRS 调度器（2026-10-24）

真正的 FSRS 卡片状态：stability / difficulty / reps / lapses / due。
使用 FSRS-5 的公开公式与默认参数（19 个权重）；不依赖第三方包，便于审计与测试。

只负责“这张卡下一次什么时候到期”，不改变现有掌握度/复习任务语义：
- 答错 → rating=1（Again）
- 答对复习 → rating=3（Good）；需要更细时可传 2/4
"""

from __future__ import annotations

import math
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from config.path_config import get_data_dir
from utils.logger import logger

DECAY = -0.5
FACTOR = 19.0 / 81.0
TARGET_RETENTION = 0.9

# FSRS-5 默认权重（19 个）
WEIGHTS = (
    0.40255, 1.18385, 3.173, 15.69105, 7.1949,
    0.5345, 1.4604, 0.0046, 1.54575, 0.1192,
    1.01925, 1.9395, 0.11, 0.29605, 2.2698,
    0.2315, 2.9898, 0.51655, 0.6621,
)

RATING_AGAIN = 1
RATING_HARD = 2
RATING_GOOD = 3
RATING_EASY = 4


def _db_path() -> str:
    return os.path.join(str(get_data_dir()), "fsrs.db")


def _conn() -> sqlite3.Connection:
    os.makedirs(get_data_dir(), exist_ok=True)
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fsrs_cards (
            user_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL,
            stability REAL NOT NULL DEFAULT 0,
            difficulty REAL NOT NULL DEFAULT 0,
            reps INTEGER NOT NULL DEFAULT 0,
            lapses INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'new',
            last_review TEXT,
            due TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, subject_id, knowledge_point_id)
        )"""
    )
    conn.commit()
    return conn


def _now_dt(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def retrievability(elapsed_days: float, stability: float) -> float:
    if stability <= 0:
        return 0.0
    return (1.0 + FACTOR * max(0.0, elapsed_days) / stability) ** DECAY


def interval_days(stability: float) -> int:
    """按目标保持率 0.9 计算间隔天数（至少 1 天）。"""
    if stability <= 0:
        return 1
    raw = stability / FACTOR * (TARGET_RETENTION ** (1.0 / DECAY) - 1.0)
    return max(1, int(round(raw)))


def new_card() -> Dict[str, Any]:
    return {"stability": 0.0, "difficulty": 0.0, "reps": 0, "lapses": 0,
            "state": "new", "last_review": None, "due": None}


def review_card(card: Optional[Dict[str, Any]], rating: int,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """更新一张卡，返回新状态（纯函数，不落库）。"""
    rating = int(max(1, min(4, rating or RATING_GOOD)))
    data = dict(new_card())
    if card:
        data.update({k: card.get(k) for k in data if k in card})
    now_dt = _now_dt(now)
    w = WEIGHTS
    stability = float(data.get("stability") or 0)
    difficulty = float(data.get("difficulty") or 0)
    reps = int(data.get("reps") or 0)
    lapses = int(data.get("lapses") or 0)

    if reps <= 0 or stability <= 0:
        stability = float(w[rating - 1])
        difficulty = _clamp(w[4] - (rating - 3) * w[5], 1.0, 10.0)
        state = "learning" if rating == RATING_AGAIN else "review"
    else:
        last = data.get("last_review")
        try:
            last_dt = datetime.fromisoformat(str(last)) if last else now_dt
        except ValueError:
            last_dt = now_dt
        elapsed = max(0.0, (now_dt - last_dt).total_seconds() / 86400.0)
        r = retrievability(elapsed, stability)

        difficulty = difficulty - w[6] * (rating - 3)
        difficulty = _clamp(w[7] * (w[4] - (4 - 3) * w[5]) + (1 - w[7]) * difficulty,
                            1.0, 10.0)

        if rating == RATING_AGAIN:
            stability = w[11] * (difficulty ** -w[12]) * ((stability + 1) ** w[13] - 1) \
                * math.exp((1 - r) * w[14])
            stability = max(0.1, stability)
            lapses += 1
            state = "relearning"
        else:
            hard_penalty = w[15] if rating == RATING_HARD else 1.0
            easy_bonus = w[16] if rating == RATING_EASY else 1.0
            stability = stability * (
                1 + math.exp(w[8]) * (11 - difficulty) * (stability ** -w[9])
                * (math.exp((1 - r) * w[10]) - 1) * hard_penalty * easy_bonus)
            stability = max(0.1, stability)
            state = "review"
    reps += 1
    days = interval_days(stability)
    due = (now_dt + timedelta(days=days)).date().isoformat()
    return {
        "stability": round(stability, 4),
        "difficulty": round(difficulty, 4),
        "reps": reps,
        "lapses": lapses,
        "state": state,
        "last_review": now_dt.isoformat(timespec="seconds"),
        "due": due,
        "interval_days": days,
    }


def get_card(user_id: str, subject_id: str, knowledge_point_id: str) -> Optional[Dict[str, Any]]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM fsrs_cards WHERE user_id=? AND subject_id=? AND knowledge_point_id=?",
            (user_id, subject_id, knowledge_point_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def next_interval(user_id: str, subject_id: str, knowledge_point_id: str,
                  rating: int = RATING_GOOD,
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """读取卡片 → 更新 → 落库 → 返回下一次间隔与 due。"""
    if not (user_id and subject_id and knowledge_point_id):
        return {"interval_days": 1, "due": None, "state": "error"}
    now_dt = _now_dt(now)
    card = get_card(user_id, subject_id, knowledge_point_id)
    updated = review_card(card, rating, now_dt)
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO fsrs_cards
               (user_id, subject_id, knowledge_point_id, stability, difficulty,
                reps, lapses, state, last_review, due, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id, subject_id, knowledge_point_id) DO UPDATE SET
                 stability=excluded.stability, difficulty=excluded.difficulty,
                 reps=excluded.reps, lapses=excluded.lapses, state=excluded.state,
                 last_review=excluded.last_review, due=excluded.due,
                 updated_at=excluded.updated_at""",
            (user_id, subject_id, knowledge_point_id, updated["stability"],
             updated["difficulty"], updated["reps"], updated["lapses"],
             updated["state"], updated["last_review"], updated["due"],
             now_dt.isoformat(timespec="seconds")),
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.warning(f"FSRS 卡片落库失败（回退固定间隔）: {e}")
        return {"interval_days": 1, "due": None, "state": "error"}
    finally:
        conn.close()
    return updated


__all__ = [
    "WEIGHTS", "RATING_AGAIN", "RATING_HARD", "RATING_GOOD", "RATING_EASY",
    "retrievability", "interval_days", "new_card", "review_card",
    "get_card", "next_interval",
]
