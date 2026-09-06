"""
用户画像与自我学习模块（V2.6 Agent核心）

实现用户建模、反馈收集、知识水平跟踪，为自适应检索和主动学习提供数据基础。
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

from utils.logger import logger

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(_DATA_DIR, "user_agent.db")

INTERACTION_TYPES = ("chat", "quiz", "note", "plan", "feedback")
FEEDBACK_TYPES = ("rating", "correction", "preference")


class UserProfileError(Exception):
    def __init__(self, message: str, error_type: str = "unknown"):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


def _get_db():
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init_db():
    conn = _get_db()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            id TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL,
            knowledge_level_json TEXT DEFAULT '{}', preferences_json TEXT DEFAULT '{}',
            learning_style TEXT DEFAULT 'balanced', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_interactions (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, interaction_type TEXT NOT NULL,
            subject_id TEXT, question TEXT, answer TEXT, context_used TEXT,
            model_used TEXT, latency REAL, feedback_score INTEGER DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_feedback (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, interaction_id TEXT,
            feedback_type TEXT NOT NULL, feedback_score INTEGER, feedback_detail TEXT,
            correction_text TEXT, created_at TEXT NOT NULL,
            FOREIGN KEY (interaction_id) REFERENCES user_interactions(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS knowledge_mastery (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, subject_id TEXT NOT NULL,
            knowledge_point TEXT NOT NULL, mastery_level REAL DEFAULT 0.5,
            test_count INTEGER DEFAULT 0, correct_count INTEGER DEFAULT 0,
            last_tested_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(user_id, subject_id, knowledge_point)
        );
        CREATE TABLE IF NOT EXISTS learning_preferences (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, preference_type TEXT NOT NULL,
            preference_value TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(user_id, preference_type)
        );
        CREATE INDEX IF NOT EXISTS idx_interactions_user ON user_interactions(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_interactions_subject ON user_interactions(subject_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_feedback_user ON user_feedback(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_mastery_user ON knowledge_mastery(user_id, subject_id);
        CREATE INDEX IF NOT EXISTS idx_preferences_user ON learning_preferences(user_id);
        """)
        conn.commit()
    finally:
        conn.close()

_init_db()

def _now():
    return datetime.now().isoformat(timespec="seconds")

def _row_to_dict(row):
    if row is None: return {}
    return dict(row)


# ==================== 用户画像管理 ====================

def get_or_create_profile(user_id: str) -> Dict[str, Any]:
    conn = _get_db()
    try:
        cursor = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            profile = _row_to_dict(row)
            profile["knowledge_level"] = json.loads(profile.get("knowledge_level_json", "{}"))
            profile["preferences"] = json.loads(profile.get("preferences_json", "{}"))
            return profile
        now = _now()
        pid = str(uuid.uuid4())
        conn.execute("INSERT INTO user_profiles (id, user_id, knowledge_level_json, preferences_json, learning_style, created_at, updated_at) VALUES (?, ?, '{}', '{}', 'balanced', ?, ?)", (pid, user_id, now, now))
        conn.commit()
        return {"id": pid, "user_id": user_id, "knowledge_level": {}, "preferences": {}, "learning_style": "balanced", "created_at": now, "updated_at": now}
    except Exception as e:
        logger.error(f"获取/创建用户画像失败: {e}", exc_info=True)
        raise UserProfileError(f"获取/创建用户画像失败: {str(e)}")
    finally:
        conn.close()


def update_knowledge_level(user_id: str, subject_id: str, level: float) -> bool:
    if not 0 <= level <= 1:
        raise UserProfileError("知识水平必须在0-1之间")
    conn = _get_db()
    try:
        cursor = conn.execute("SELECT knowledge_level_json FROM user_profiles WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            get_or_create_profile(user_id)
            cursor = conn.execute("SELECT knowledge_level_json FROM user_profiles WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
        kl = json.loads(row["knowledge_level_json"] or "{}")
        kl[subject_id] = level
        now = _now()
        conn.execute("UPDATE user_profiles SET knowledge_level_json = ?, updated_at = ? WHERE user_id = ?", (json.dumps(kl), now, user_id))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"更新知识水平失败: {e}", exc_info=True)
        raise UserProfileError(f"更新知识水平失败: {str(e)}")
    finally:
        conn.close()


def update_learning_style(user_id: str, style: str) -> bool:
    valid = ("visual", "auditory", "reading", "kinesthetic", "balanced")
    if style not in valid:
        raise UserProfileError(f"学习风格必须是: {', '.join(valid)}")
    conn = _get_db()
    try:
        now = _now()
        conn.execute("UPDATE user_profiles SET learning_style = ?, updated_at = ? WHERE user_id = ?", (style, now, user_id))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"更新学习风格失败: {e}", exc_info=True)
        raise UserProfileError(f"更新学习风格失败: {str(e)}")
    finally:
        conn.close()


# ==================== 交互记录 ====================

def record_interaction(user_id, interaction_type, subject_id=None, question=None, answer=None, context_used=None, model_used=None, latency=None):
    if interaction_type not in INTERACTION_TYPES:
        raise UserProfileError(f"无效的交互类型: {interaction_type}")
    conn = _get_db()
    try:
        iid = str(uuid.uuid4())
        now = _now()
        conn.execute("INSERT INTO user_interactions (id, user_id, interaction_type, subject_id, question, answer, context_used, model_used, latency, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (iid, user_id, interaction_type, subject_id, question, answer, context_used, model_used, latency, now))
        conn.commit()
        logger.info(f"记录用户交互: {iid}, 类型: {interaction_type}")
        return iid
    except Exception as e:
        logger.error(f"记录交互失败: {e}", exc_info=True)
        raise UserProfileError(f"记录交互失败: {str(e)}")
    finally:
        conn.close()


def get_user_interactions(user_id, subject_id=None, interaction_type=None, limit=50, offset=0):
    conn = _get_db()
    try:
        q = "SELECT * FROM user_interactions WHERE user_id = ?"
        p = [user_id]
        if subject_id: q += " AND subject_id = ?"; p.append(subject_id)
        if interaction_type: q += " AND interaction_type = ?"; p.append(interaction_type)
        q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"; p.extend([limit, offset])
        return [_row_to_dict(r) for r in conn.execute(q, p).fetchall()]
    except Exception as e:
        logger.error(f"获取交互记录失败: {e}", exc_info=True)
        raise UserProfileError(f"获取交互记录失败: {str(e)}")
    finally:
        conn.close()


# ==================== 反馈管理 ====================

def submit_feedback(user_id, interaction_id=None, feedback_type="rating", feedback_score=None, feedback_detail=None, correction_text=None):
    if feedback_type not in FEEDBACK_TYPES:
        raise UserProfileError(f"无效的反馈类型: {feedback_type}")
    if feedback_score is not None and feedback_score not in (1, -1, 0):
        raise UserProfileError("反馈分数必须是 1, -1, 或 0")
    conn = _get_db()
    try:
        fid = str(uuid.uuid4())
        now = _now()
        conn.execute("INSERT INTO user_feedback (id, user_id, interaction_id, feedback_type, feedback_score, feedback_detail, correction_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (fid, user_id, interaction_id, feedback_type, feedback_score, feedback_detail, correction_text, now))
        if interaction_id and feedback_score is not None:
            conn.execute("UPDATE user_interactions SET feedback_score = ? WHERE id = ?", (feedback_score, interaction_id))
        conn.commit()
        logger.info(f"收到用户反馈: {fid}, 类型: {feedback_type}, 分数: {feedback_score}")
        return fid
    except Exception as e:
        logger.error(f"提交反馈失败: {e}", exc_info=True)
        raise UserProfileError(f"提交反馈失败: {str(e)}")
    finally:
        conn.close()


def get_user_feedback_stats(user_id):
    conn = _get_db()
    try:
        total = conn.execute("SELECT COUNT(*) as t FROM user_feedback WHERE user_id = ?", (user_id,)).fetchone()["t"]
        pos = conn.execute("SELECT COUNT(*) as t FROM user_feedback WHERE user_id = ? AND feedback_score = 1", (user_id,)).fetchone()["t"]
        neg = conn.execute("SELECT COUNT(*) as t FROM user_feedback WHERE user_id = ? AND feedback_score = -1", (user_id,)).fetchone()["t"]
        recent = [_row_to_dict(r) for r in conn.execute("SELECT * FROM user_feedback WHERE user_id = ? ORDER BY created_at DESC LIMIT 10", (user_id,)).fetchall()]
        return {"total_feedback": total, "positive_feedback": pos, "negative_feedback": neg, "satisfaction_rate": pos / total if total > 0 else 0, "recent_feedback": recent}
    except Exception as e:
        logger.error(f"获取反馈统计失败: {e}", exc_info=True)
        raise UserProfileError(f"获取反馈统计失败: {str(e)}")
    finally:
        conn.close()


# ==================== 知识点掌握 ====================

def update_knowledge_mastery(user_id, subject_id, knowledge_point, is_correct, mastery_delta=0.1):
    conn = _get_db()
    try:
        now = _now()
        row = conn.execute("SELECT * FROM knowledge_mastery WHERE user_id = ? AND subject_id = ? AND knowledge_point = ?", (user_id, subject_id, knowledge_point)).fetchone()
        if row:
            cm = row["mastery_level"]
            tc = row["test_count"] + 1
            cc = row["correct_count"] + (1 if is_correct else 0)
            nm = min(1.0, cm + mastery_delta) if is_correct else max(0.0, cm - mastery_delta * 0.5)
            conn.execute("UPDATE knowledge_mastery SET mastery_level = ?, test_count = ?, correct_count = ?, last_tested_at = ?, updated_at = ? WHERE id = ?", (nm, tc, cc, now, now, row["id"]))
        else:
            mid = str(uuid.uuid4())
            im = 0.7 if is_correct else 0.3
            conn.execute("INSERT INTO knowledge_mastery (id, user_id, subject_id, knowledge_point, mastery_level, test_count, correct_count, last_tested_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)", (mid, user_id, subject_id, knowledge_point, im, 1 if is_correct else 0, now, now, now))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"更新知识点掌握失败: {e}", exc_info=True)
        raise UserProfileError(f"更新知识点掌握失败: {str(e)}")
    finally:
        conn.close()


def get_weak_points(user_id, subject_id, threshold=0.4):
    conn = _get_db()
    try:
        return [_row_to_dict(r) for r in conn.execute("SELECT * FROM knowledge_mastery WHERE user_id = ? AND subject_id = ? AND mastery_level < ? ORDER BY mastery_level ASC", (user_id, subject_id, threshold)).fetchall()]
    except Exception as e:
        raise UserProfileError(f"获取薄弱知识点失败: {str(e)}")
    finally:
        conn.close()


def get_strong_points(user_id, subject_id, threshold=0.7):
    conn = _get_db()
    try:
        return [_row_to_dict(r) for r in conn.execute("SELECT * FROM knowledge_mastery WHERE user_id = ? AND subject_id = ? AND mastery_level > ? ORDER BY mastery_level DESC", (user_id, subject_id, threshold)).fetchall()]
    except Exception as e:
        raise UserProfileError(f"获取强项知识点失败: {str(e)}")
    finally:
        conn.close()


# ==================== 学习偏好 ====================

def set_learning_preference(user_id, preference_type, preference_value):
    conn = _get_db()
    try:
        now = _now()
        conn.execute("INSERT INTO learning_preferences (id, user_id, preference_type, preference_value, created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, preference_type) DO UPDATE SET preference_value = ?", (str(uuid.uuid4()), user_id, preference_type, preference_value, now, preference_value))
        conn.commit()
        return True
    except Exception as e:
        raise UserProfileError(f"设置学习偏好失败: {str(e)}")
    finally:
        conn.close()


def get_learning_preferences(user_id):
    conn = _get_db()
    try:
        return {r["preference_type"]: r["preference_value"] for r in conn.execute("SELECT preference_type, preference_value FROM learning_preferences WHERE user_id = ?", (user_id,)).fetchall()}
    except Exception as e:
        raise UserProfileError(f"获取学习偏好失败: {str(e)}")
    finally:
        conn.close()


# ==================== 分析功能 ====================

def analyze_user_performance(user_id, subject_id):
    conn = _get_db()
    try:
        istat = dict(conn.execute("SELECT COUNT(*) as total_interactions, AVG(feedback_score) as avg_feedback, SUM(CASE WHEN feedback_score = 1 THEN 1 ELSE 0 END) as positive_count, SUM(CASE WHEN feedback_score = -1 THEN 1 ELSE 0 END) as negative_count FROM user_interactions WHERE user_id = ? AND subject_id = ?", (user_id, subject_id)).fetchone())
        mstat = dict(conn.execute("SELECT COUNT(*) as total_points, AVG(mastery_level) as avg_mastery, SUM(CASE WHEN mastery_level >= 0.7 THEN 1 ELSE 0 END) as strong_points, SUM(CASE WHEN mastery_level < 0.4 THEN 1 ELSE 0 END) as weak_points FROM knowledge_mastery WHERE user_id = ? AND subject_id = ?", (user_id, subject_id)).fetchone())
        ti = istat.get("total_interactions", 0)
        pr = (istat.get("positive_count", 0) / ti) if ti > 0 else 0
        return {"interaction_stats": istat, "mastery_stats": mstat, "positive_rate": pr, "learning_effectiveness": "good" if pr > 0.7 else "needs_improvement"}
    except Exception as e:
        raise UserProfileError(f"分析用户表现失败: {str(e)}")
    finally:
        conn.close()


__all__ = [
    "UserProfileError", "get_or_create_profile", "update_knowledge_level", "update_learning_style",
    "record_interaction", "get_user_interactions", "submit_feedback", "get_user_feedback_stats",
    "update_knowledge_mastery", "get_weak_points", "get_strong_points",
    "set_learning_preference", "get_learning_preferences", "analyze_user_performance",
]
