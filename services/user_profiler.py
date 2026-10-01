"""
用户画像与自我学习模块（V2.6 Agent核心）

实现用户建模、反馈收集、知识水平跟踪，为自适应检索和主动学习提供数据基础。
"""

from config.path_config import get_data_dir
import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

from utils.logger import logger

# 数据根目录统一来自 config.path_config（支持 EDU_DATA_DIR 覆盖）
_DATA_DIR = str(get_data_dir())
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

        # 阶段一新增列（知识点稳定 ID / 证据计数 / 掌握状态等）：
        # 与 learning_data 共用列定义，保证"新建库"与"历史库迁移"结构一致，
        # 不依赖应用启动顺序（脚本/测试/独立调用同样正确）。
        try:
            from services.learning_data import ensure_columns
            ensure_columns(conn, "knowledge_mastery")
        except Exception as e:
            logger.warning(f"知识掌握表结构补齐失败: {e}")
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
    """
    提交反馈。

    阶段一补验：`interaction_id` 由客户端传入，必须校验归属，
    否则可以覆写他人交互记录的反馈分（越权写入）。

    - interaction_id 不存在或不属于当前用户 → 不写入反馈分（fail-closed）
    """
    if feedback_type not in FEEDBACK_TYPES:
        raise UserProfileError(f"无效的反馈类型: {feedback_type}")
    if feedback_score is not None and feedback_score not in (1, -1, 0):
        raise UserProfileError("反馈分数必须是 1, -1, 或 0")
    conn = _get_db()
    try:
        owned_interaction = False
        if interaction_id:
            row = conn.execute(
                "SELECT user_id FROM user_interactions WHERE id = ?", (interaction_id,)).fetchone()
            owned_interaction = bool(row) and row["user_id"] == user_id
            if not owned_interaction:
                logger.warning(f"反馈关联的交互不属于当前用户，忽略该关联: "
                               f"user={user_id}, interaction={interaction_id}")
                interaction_id = None

        fid = str(uuid.uuid4())
        now = _now()
        conn.execute("INSERT INTO user_feedback (id, user_id, interaction_id, feedback_type, feedback_score, feedback_detail, correction_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (fid, user_id, interaction_id, feedback_type, feedback_score, feedback_detail, correction_text, now))
        if owned_interaction and interaction_id and feedback_score is not None:
            conn.execute("UPDATE user_interactions SET feedback_score = ? WHERE id = ? AND user_id = ?", (feedback_score, interaction_id, user_id))
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
    """
    [已废弃 · 请勿使用] 旧掌握度写入

    问题：由调用方传入 is_correct 与 delta 直接加减掌握度，与阶段一
    "后端权威判分 + 掌握度只由独立作答证据计算"的口径冲突。
    替代：services/mastery_service.record_evidence / record_behavior。
    """
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
    """
    薄弱知识点（阶段一：基于真实作答证据）

    只返回"已有独立作答证据、且估计值低于阈值"的知识点。
    无证据的知识点属于"待评估"，不混入薄弱列表，也不给默认分数
    （避免"从没测过"被当成"已经掌握"或"薄弱"）。
    """
    try:
        from services.mastery_service import list_mastery
        points = [
            m for m in list_mastery(user_id, subject_id)
            if m["mastery_level"] is not None and m["mastery_level"] < threshold
        ]
        points.sort(key=lambda m: m["mastery_level"])
        return points
    except Exception as e:
        raise UserProfileError(f"获取薄弱知识点失败: {str(e)}")


def get_strong_points(user_id, subject_id, threshold=0.7):
    """强项知识点（阶段一：基于真实作答证据；口径同 get_weak_points）"""
    try:
        from services.mastery_service import list_mastery
        points = [
            m for m in list_mastery(user_id, subject_id)
            if m["mastery_level"] is not None and m["mastery_level"] > threshold
        ]
        points.sort(key=lambda m: m["mastery_level"], reverse=True)
        return points
    except Exception as e:
        raise UserProfileError(f"获取强项知识点失败: {str(e)}")


# ==================== 学习偏好 ====================

def set_learning_preference(user_id, preference_type, preference_value):
    """
    设置学习偏好（学生显式表达）。

    阶段二：同时写入"学习记忆"的 preference 类型，使：
    - 学生可在记忆页面看到并管理自己的显式设置
    - 显式设置优先于系统推断（同 key 的推断不再参与教学）
    """
    conn = _get_db()
    try:
        now = _now()
        conn.execute("INSERT INTO learning_preferences (id, user_id, preference_type, preference_value, created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, preference_type) DO UPDATE SET preference_value = ?", (str(uuid.uuid4()), user_id, preference_type, preference_value, now, preference_value))
        conn.commit()
    except Exception as e:
        raise UserProfileError(f"设置学习偏好失败: {str(e)}")
    finally:
        conn.close()

    # 同步为显式记忆（失败不影响偏好本体，仅记录告警）
    try:
        from services import learning_memory
        learning_memory.set_memory(
            user_id=user_id, key=preference_type, value=str(preference_value),
            memory_type=learning_memory.MEMORY_PREFERENCE,
            scope=learning_memory.SCOPE_GLOBAL,
            note="学生在设置中显式指定")
    except Exception as e:
        logger.warning(f"学习偏好同步到记忆失败（偏好已保存）: {e}")
    return True


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
    """
    用户表现分析

    阶段一修正：掌握度统计只统计"已有独立作答证据"的知识点，
    无证据的记为 pending（待评估），不再用默认值参与平均分。
    """
    conn = _get_db()
    try:
        istat = dict(conn.execute("SELECT COUNT(*) as total_interactions, AVG(feedback_score) as avg_feedback, SUM(CASE WHEN feedback_score = 1 THEN 1 ELSE 0 END) as positive_count, SUM(CASE WHEN feedback_score = -1 THEN 1 ELSE 0 END) as negative_count FROM user_interactions WHERE user_id = ? AND subject_id = ?", (user_id, subject_id)).fetchone())
        ti = istat.get("total_interactions", 0)
        pr = (istat.get("positive_count", 0) / ti) if ti > 0 else 0
        istat["positive_rate"] = round(pr, 4)
    except Exception as e:
        raise UserProfileError(f"分析用户表现失败: {str(e)}")
    finally:
        conn.close()

    try:
        from services.mastery_service import summarize_mastery
        summary = summarize_mastery(user_id, subject_id)
    except Exception as e:
        raise UserProfileError(f"分析用户表现失败: {str(e)}")

    dist = summary["distribution"]
    mstat = {
        "total_points": summary["total_points"],
        "graded_points": dist["strong"] + dist["medium"] + dist["weak"],
        "pending_points": dist["pending"],
        # 无证据时不返回平均掌握度（None 表示"尚不可评估"）
        "avg_mastery": (round(summary["accuracy"], 4) if summary["accuracy"] is not None else None),
        "strong_points": dist["strong"],
        "weak_points": dist["weak"],
    }
    return {
        "interaction_stats": istat,
        "mastery_stats": mstat,
        "mastery_summary": summary,
        "positive_rate": pr,
        "learning_effectiveness": "good" if pr > 0.7 else "needs_improvement",
    }


def list_students() -> List[Dict[str, Any]]:
    """
    列出所有学生及其学习统计摘要（教师端）

    Returns:
        学生列表，每个元素包含 user_id、学习风格、交互次数、知识点数、平均掌握度、最后活跃时间
    """
    conn = _get_db()
    try:
        rows = conn.execute("SELECT user_id, learning_style, updated_at, created_at FROM user_profiles ORDER BY updated_at DESC").fetchall()
        students = []
        for row in rows:
            r = dict(row)
            user_id = r["user_id"]
            # 统计交互次数
            ic = conn.execute("SELECT COUNT(*) as c FROM user_interactions WHERE user_id = ?", (user_id,)).fetchone()
            # 统计知识点掌握
            mc = conn.execute(
                "SELECT COUNT(*) as total, AVG(mastery_level) as avg_mastery, "
                "SUM(CASE WHEN mastery_level >= 0.7 THEN 1 ELSE 0 END) as strong, "
                "SUM(CASE WHEN mastery_level < 0.4 THEN 1 ELSE 0 END) as weak "
                "FROM knowledge_mastery WHERE user_id = ?", (user_id,)).fetchone()
            mc = dict(mc)
            students.append({
                "user_id": user_id,
                "learning_style": r.get("learning_style", "balanced"),
                "interaction_count": ic["c"] if ic else 0,
                "knowledge_point_count": mc.get("total") or 0,
                "average_mastery": round(mc.get("avg_mastery") or 0, 3),
                "strong_count": mc.get("strong") or 0,
                "weak_count": mc.get("weak") or 0,
                "last_active": r.get("updated_at", ""),
                "created_at": r.get("created_at", ""),
            })
        return students
    except Exception as e:
        logger.error(f"列出学生失败: {e}", exc_info=True)
        raise UserProfileError(f"列出学生失败: {str(e)}")
    finally:
        conn.close()


def get_student_subjects(user_id: str) -> List[str]:
    """
    获取学生学过/涉及的所有学科ID（教师端）
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT DISTINCT subject_id FROM knowledge_mastery WHERE user_id = ? AND subject_id IS NOT NULL AND subject_id != ''",
            (user_id,)).fetchall()
        return [r["subject_id"] for r in rows]
    except Exception as e:
        logger.error(f"获取学生学科失败: {e}", exc_info=True)
        return []
    finally:
        conn.close()


__all__ = [
    "UserProfileError", "get_or_create_profile", "update_knowledge_level", "update_learning_style",
    "record_interaction", "get_user_interactions", "submit_feedback", "get_user_feedback_stats",
    "update_knowledge_mastery", "get_weak_points", "get_strong_points",
    "set_learning_preference", "get_learning_preferences", "analyze_user_performance",
    "list_students", "get_student_subjects",
]
