"""
学习数据基础层（阶段一）

统一管理"学生学习闭环"所需的新表与历史迁移：

- knowledge_points / knowledge_point_aliases：稳定知识点 ID + 别名（避免同义名称造成重复画像）
- quiz_questions：题库（选项 ID + 服务端权威标准答案）
- quiz_attempts：每次作答（独立作答 / 复习 / 看答案后练习）
- review_tasks：复习任务（错题联动，可合并复用）
- learning_events：统一事件（幂等 + 重试）

注：任务改期留痕表 task_adjustments 与 learning_tasks 同库（learning.db），
由 services/learning_service.py 建表 —— 与本库其他表分属不同数据库，不要在此创建。

设计原则：
- 复用现有 SQLite 基础设施（短连接 + WAL + busy_timeout）
- 迁移可重复执行；执行前备份已有业务数据库；不删除历史资料/知识库/会话
- 表结构补充以"最小必要"为限，历史数据保留
"""

from config.path_config import get_data_dir
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

from utils.logger import logger

# 数据根目录统一来自 config.path_config（可用 EDU_DATA_DIR 覆盖，便于测试/部署隔离）
_DATA_DIR = str(get_data_dir())
DB_PATH = os.path.join(_DATA_DIR, "learning_loop.db")

# 需要迁移的既有业务库（迁移前备份）
_BUSINESS_DBS = (
    os.path.join(_DATA_DIR, "learning.db"),
    os.path.join(_DATA_DIR, "user_agent.db"),
    os.path.join(_DATA_DIR, "chat.db"),
)

BACKUP_DIR = os.path.join(_DATA_DIR, "backups")

# 题型
QUESTION_TYPES = ("single_choice", "judgement", "short_answer")
# 作答类型：独立作答 / 复习重做 / 看过答案后的练习
ATTEMPT_KINDS = ("independent", "review", "after_reveal")
# 判分状态
GRADE_GRADED = "graded"
GRADE_PENDING_SELF = "pending_self_eval"
# 简答题由 AI 判分；用户仍可用"我答对了/我答错了"覆盖为 self_evaluated
GRADE_AI_GRADED = "ai_graded"
# 复习任务状态
REVIEW_PENDING = "PENDING"
REVIEW_DONE = "DONE"
REVIEW_CANCELLED = "CANCELLED"
# 掌握度状态
MASTERY_PENDING = "pending"    # 待评估（无独立作答证据）
MASTERY_ESTIMATED = "estimated"  # 已有证据的暂时判断


def _get_db() -> sqlite3.Connection:
    """获取学习闭环数据库连接"""
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _table_columns(conn: sqlite3.Connection, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _init_db() -> None:
    """创建学习闭环新表（纯新增，幂等）"""
    conn = _get_db()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_points (
            id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            source TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(subject_id, normalized_name)
        );
        CREATE TABLE IF NOT EXISTS knowledge_point_aliases (
            id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(subject_id, normalized_alias)
        );
        CREATE TABLE IF NOT EXISTS quiz_questions (
            id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL,
            knowledge_point_name TEXT NOT NULL,
            question_type TEXT NOT NULL,
            stem TEXT NOT NULL,
            options_json TEXT DEFAULT '[]',
            correct_option_id TEXT,
            reference_answer TEXT,
            explanation TEXT,
            version INTEGER DEFAULT 1,
            source TEXT,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(subject_id, content_hash)
        );
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            question_id TEXT NOT NULL,
            knowledge_point_id TEXT,
            knowledge_point_name TEXT,
            attempt_no INTEGER NOT NULL DEFAULT 1,
            attempt_kind TEXT NOT NULL DEFAULT 'independent',
            user_answer TEXT,
            user_answer_text TEXT,
            correct_option_id TEXT,
            is_correct INTEGER,
            grade_status TEXT NOT NULL DEFAULT 'graded',
            ai_feedback TEXT,
            used_hint INTEGER DEFAULT 0,
            revealed_answer INTEGER DEFAULT 0,
            duration_sec INTEGER,
            source_task_id TEXT,
            conversation_id TEXT,
            client_token TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, client_token)
        );
        CREATE TABLE IF NOT EXISTS review_tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL,
            knowledge_point_name TEXT NOT NULL,
            source_attempt_id TEXT,
            source_task_id TEXT,
            due_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            reason TEXT,
            merged_count INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS learning_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            user_id TEXT,
            subject_id TEXT,
            payload_json TEXT DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'PENDING',
            attempts INTEGER DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kp_subject ON knowledge_points(subject_id);
        CREATE INDEX IF NOT EXISTS idx_kp_alias ON knowledge_point_aliases(subject_id, normalized_alias);
        CREATE INDEX IF NOT EXISTS idx_quiz_q_kp ON quiz_questions(subject_id, knowledge_point_id);
        CREATE INDEX IF NOT EXISTS idx_attempts_user ON quiz_attempts(user_id, subject_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_attempts_question ON quiz_attempts(user_id, question_id);
        CREATE INDEX IF NOT EXISTS idx_attempts_kp ON quiz_attempts(user_id, subject_id, knowledge_point_id);
        CREATE INDEX IF NOT EXISTS idx_review_user ON review_tasks(user_id, subject_id, status, due_date);
        CREATE INDEX IF NOT EXISTS idx_events_status ON learning_events(status, updated_at);
        """)
        # 同一用户+学科+知识点 只允许一条待办复习任务（合并复用，避免重复堆积）
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_review_pending_unique
               ON review_tasks(user_id, subject_id, knowledge_point_id)
               WHERE status = 'PENDING'""")
        # 老库补列：CREATE TABLE IF NOT EXISTS 不会改动已存在的表
        ensure_columns(conn, "quiz_attempts")
        conn.commit()
    finally:
        conn.close()


# ==================== 知识点（稳定 ID + 别名） ====================

# 名称归一化时剔除的常见修饰/标点，避免"二叉树。"与"二叉树"被当成两个知识点
_KP_STRIP_CHARS = " \t\r\n　。．.、，,；;：:！!？?（）()【】[]《》<>\"'“”‘’*-—_/\\|"


def normalize_kp_name(name: str) -> str:
    """知识点名称归一化（用于稳定 ID 匹配与别名去重）"""
    if not name:
        return ""
    text = str(name).strip()
    text = text.strip(_KP_STRIP_CHARS)
    # 折叠内部空白
    text = " ".join(text.split())
    return text.casefold()


def ensure_knowledge_point(
    subject_id: str,
    name: str,
    source: Optional[str] = None,
) -> Optional[str]:
    """
    取得（或创建）知识点的稳定 ID。

    匹配顺序：标准名 → 别名。命中别名时复用同一知识点，
    避免同义名称在画像/错题/复习中造成重复。

    Returns:
        知识点 ID；name 为空时返回 None
    """
    if not subject_id or not name or not str(name).strip():
        return None
    norm = normalize_kp_name(name)
    if not norm:
        return None

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id FROM knowledge_points WHERE subject_id=? AND normalized_name=?",
            (subject_id, norm)).fetchone()
        if row:
            return row["id"]

        alias_row = conn.execute(
            "SELECT knowledge_point_id FROM knowledge_point_aliases "
            "WHERE subject_id=? AND normalized_alias=?",
            (subject_id, norm)).fetchone()
        if alias_row:
            return alias_row["knowledge_point_id"]

        kp_id = _new_id("kp")
        now = _now()
        try:
            conn.execute(
                """INSERT INTO knowledge_points
                   (id, subject_id, name, normalized_name, source, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (kp_id, subject_id, str(name).strip(), norm, source, now, now))
            conn.commit()
            return kp_id
        except sqlite3.IntegrityError:
            # 并发创建：回退查询
            row = conn.execute(
                "SELECT id FROM knowledge_points WHERE subject_id=? AND normalized_name=?",
                (subject_id, norm)).fetchone()
            return row["id"] if row else None
    finally:
        conn.close()


def add_knowledge_point_alias(subject_id: str, kp_id: str, alias: str) -> bool:
    """为知识点登记别名（同名冲突时不覆盖）"""
    if not subject_id or not kp_id or not alias or not str(alias).strip():
        return False
    norm = normalize_kp_name(alias)
    if not norm:
        return False
    conn = _get_db()
    try:
        try:
            conn.execute(
                """INSERT INTO knowledge_point_aliases
                   (id, subject_id, knowledge_point_id, alias, normalized_alias, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (_new_id("kpa"), subject_id, kp_id, str(alias).strip(), norm, _now()))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    finally:
        conn.close()


def get_knowledge_point(kp_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 获取知识点（含别名列表）"""
    if not kp_id:
        return None
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM knowledge_points WHERE id=?", (kp_id,)).fetchone()
        if not row:
            return None
        kp = dict(row)
        kp["aliases"] = [
            r["alias"] for r in conn.execute(
                "SELECT alias FROM knowledge_point_aliases WHERE knowledge_point_id=?",
                (kp_id,)).fetchall()
        ]
        return kp
    finally:
        conn.close()


def get_knowledge_point_by_name(subject_id: str, name: str) -> Optional[Dict[str, Any]]:
    """按名称（含别名）获取知识点"""
    kp_id = ensure_knowledge_point(subject_id, name)
    return get_knowledge_point(kp_id) if kp_id else None


def list_knowledge_points(subject_id: str) -> List[Dict[str, Any]]:
    """列出学科下的知识点"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM knowledge_points WHERE subject_id=? ORDER BY created_at",
            (subject_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==================== 历史迁移（可重复执行 + 备份） ====================

def _pending_migrations() -> Dict[str, List[str]]:
    """检测缺失的列（空字典表示无需迁移）"""
    pending: Dict[str, List[str]] = {}

    learning_db = os.path.join(_DATA_DIR, "learning.db")
    if os.path.exists(learning_db):
        conn = sqlite3.connect(learning_db)
        try:
            if _table_exists(conn, "learning_tasks"):
                cols = _table_columns(conn, "learning_tasks")
                need = [c for c in (
                    "user_id", "subject_id", "knowledge_point_id", "planned_date",
                    "source", "source_attempt_id", "defer_count", "original_planned_date",
                ) if c not in cols]
                if need:
                    pending["learning_tasks"] = need
            if _table_exists(conn, "learning_plans"):
                cols = _table_columns(conn, "learning_plans")
                if "user_id" not in cols:
                    pending["learning_plans"] = ["user_id"]
        finally:
            conn.close()

    agent_db = os.path.join(_DATA_DIR, "user_agent.db")
    if os.path.exists(agent_db):
        conn = sqlite3.connect(agent_db)
        try:
            if _table_exists(conn, "knowledge_mastery"):
                cols = _table_columns(conn, "knowledge_mastery")
                need = [c for c in (
                    "knowledge_point_id", "mastery_status", "evidence_count",
                    "independent_correct_count", "independent_wrong_count",
                    "last_evidence_at", "mastery_reason",
                    "behavior_count", "last_behavior_at",
                ) if c not in cols]
                if need:
                    pending["knowledge_mastery"] = need
        finally:
            conn.close()

    chat_db = os.path.join(_DATA_DIR, "chat.db")
    if os.path.exists(chat_db):
        conn = sqlite3.connect(chat_db)
        try:
            if _table_exists(conn, "conversations"):
                cols = _table_columns(conn, "conversations")
                if "user_id" not in cols:
                    pending["conversations"] = ["user_id"]
            if _table_exists(conn, "messages"):
                cols = _table_columns(conn, "messages")
                if "interaction_id" not in cols:
                    pending["messages"] = ["interaction_id"]
        finally:
            conn.close()

    return pending


def _backup_databases() -> Optional[str]:
    """迁移前备份业务数据库到 data/backups/<timestamp>/"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = os.path.join(BACKUP_DIR, stamp)
    try:
        os.makedirs(target, exist_ok=True)
        copied = []
        for src in _BUSINESS_DBS:
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(target, os.path.basename(src)))
                copied.append(os.path.basename(src))
        if not copied:
            return None
        logger.info(f"业务数据库已备份: {target} -> {copied}")
        return target
    except Exception as e:
        logger.error(f"数据库备份失败: {e}", exc_info=True)
        raise


# 需要补充的列（列名 → 建表 DDL 片段）
_TASK_COLUMNS = {
    "user_id": "TEXT",
    "subject_id": "TEXT",
    "knowledge_point_id": "TEXT",
    "planned_date": "TEXT",
    "source": "TEXT DEFAULT 'manual'",
    "source_attempt_id": "TEXT",
    "defer_count": "INTEGER DEFAULT 0",
    "original_planned_date": "TEXT",
    # 教师端（2026-09-26）：教师布置的作业物化出的任务，指回作业以便溯源与统计
    "assignment_id": "TEXT",
}

_PLAN_COLUMNS = {
    "user_id": "TEXT",
}

_CONVERSATION_COLUMNS = {
    "user_id": "TEXT",
}

_MESSAGE_COLUMNS = {
    # 关联用户交互记录，供反馈按钮正确落库（修复 message_id 被当 interaction_id 的问题）
    "interaction_id": "TEXT",
}

# 历史数据归属标签（保留原数据，供用户显式认领）
LEGACY_OWNER = "default_user"

_MASTERY_COLUMNS = {
    "knowledge_point_id": "TEXT",
    "mastery_status": "TEXT DEFAULT 'pending'",
    "evidence_count": "INTEGER DEFAULT 0",
    "independent_correct_count": "INTEGER DEFAULT 0",
    "independent_wrong_count": "INTEGER DEFAULT 0",
    "last_evidence_at": "TEXT",
    "mastery_reason": "TEXT",
    "behavior_count": "INTEGER DEFAULT 0",
    "last_behavior_at": "TEXT",
    # ---- 阶段一补验：证据分类 + 长期巩固判定 ----
    # 复习重做的对/错（计入权重较低的证据，不代表独立掌握）
    "review_correct_count": "INTEGER DEFAULT 0",
    "review_wrong_count": "INTEGER DEFAULT 0",
    # 看过答案后作答 / 待自评的作答（只留痕，不计任何掌握证据）
    "excluded_count": "INTEGER DEFAULT 0",
    # 首次独立答对时间（与最近一次独立答对比较，判断是否"间隔复现"）
    "first_correct_at": "TEXT",
    "last_correct_at": "TEXT",
    # 长期掌握判定：unverified（未巩固）/ consolidated（已巩固）
    "consolidation_status": "TEXT DEFAULT 'unverified'",
    "recheck_count": "INTEGER DEFAULT 0",
}

# 表名 → 阶段一新增列定义（供 ensure_columns 与 migrate_legacy 共用，避免两处漂移）
# quiz_attempts：AI 判分简答题的评判理由（刷新后仍可查看）
_ATTEMPT_COLUMNS = {
    "ai_feedback": "TEXT",
}

_COLUMN_SETS = {
    "learning_tasks": _TASK_COLUMNS,
    "quiz_attempts": _ATTEMPT_COLUMNS,
    "learning_plans": _PLAN_COLUMNS,
    "conversations": _CONVERSATION_COLUMNS,
    "messages": _MESSAGE_COLUMNS,
    "knowledge_mastery": _MASTERY_COLUMNS,
}


def ensure_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    """
    为指定连接上的表补齐阶段一新增列（幂等，无副作用）。

    与 migrate_legacy 共用列定义，但**不做备份、不做历史归属打标/回填**：
    适用于"调用方自建或自带数据库"的场景（隔离测试库、按需建库等）。
    表不存在时返回空列表（建表由调用方负责）。

    Returns:
        实际新增的列名列表
    """
    definitions = _COLUMN_SETS.get(table)
    if not definitions or not _table_exists(conn, table):
        return []
    existing = _table_columns(conn, table)
    added: List[str] = []
    for col, ddl in definitions.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            added.append(col)
    if added:
        conn.commit()
    return added


def migrate_legacy() -> Dict[str, Any]:
    """
    历史数据迁移（可重复执行，执行前自动备份）

    1. 备份业务数据库
    2. 为 learning_tasks / knowledge_mastery 补充缺失列
    3. 回填知识点稳定 ID
    4. 历史 default_user 数据打标（保留，不删除、不静默分配）

    Returns:
        {"backup": path|None, "altered": [...], "backfilled": int, "legacy_marked": int}
    """
    # 先确保新表存在
    _init_db()

    pending = _pending_migrations()
    result: Dict[str, Any] = {"backup": None, "altered": [], "backfilled": 0, "legacy_marked": 0}
    if not pending:
        return result

    result["backup"] = _backup_databases()

    # ---- learning.db: 补列 + 回填 ----
    learning_db = os.path.join(_DATA_DIR, "learning.db")
    if ("learning_tasks" in pending or "learning_plans" in pending) and os.path.exists(learning_db):
        conn = sqlite3.connect(learning_db)
        conn.row_factory = sqlite3.Row
        try:
            if "learning_tasks" in pending:
                for col in pending["learning_tasks"]:
                    conn.execute(f"ALTER TABLE learning_tasks ADD COLUMN {col} {_TASK_COLUMNS[col]}")
                result["altered"].append(f"learning_tasks(+{len(pending['learning_tasks'])})")
            if "learning_plans" in pending:
                for col in pending["learning_plans"]:
                    conn.execute(f"ALTER TABLE learning_plans ADD COLUMN {col} {_PLAN_COLUMNS[col]}")
                result["altered"].append(f"learning_plans(+{len(pending['learning_plans'])})")
            conn.commit()

            # 历史计划/任务归属标记为待认领（保留原数据，不静默分配）
            if "learning_plans" in pending:
                conn.execute(
                    "UPDATE learning_plans SET user_id=? WHERE user_id IS NULL", (LEGACY_OWNER,))
            # 回填：用户归属 / 知识点稳定 ID / 计划日期
            rows = conn.execute("SELECT * FROM learning_tasks").fetchall()
            for r in rows:
                keys = r.keys()
                updates, params = [], []
                if not r["user_id"]:
                    updates.append("user_id=?")
                    params.append(LEGACY_OWNER)
                kp_id = r["knowledge_point_id"]
                kp_name = r["knowledge_point"]
                subj = conn.execute(
                    "SELECT subject_id FROM learning_plans WHERE id=?",
                    (r["plan_id"],)).fetchone()
                if subj and not r["subject_id"]:
                    updates.append("subject_id=?")
                    params.append(subj["subject_id"])
                if not kp_id and kp_name:
                    if subj:
                        kp_id = ensure_knowledge_point(subj["subject_id"], kp_name, source="migration")
                        if kp_id:
                            updates.append("knowledge_point_id=?")
                            params.append(kp_id)
                if not r["planned_date"] and r["created_at"]:
                    updates.append("planned_date=?")
                    params.append(str(r["created_at"])[:10])
                    updates.append("original_planned_date=?")
                    params.append(str(r["created_at"])[:10])
                if updates:
                    params.append(r["id"])
                    conn.execute(
                        f"UPDATE learning_tasks SET {', '.join(updates)} WHERE id=?", params)
                    result["backfilled"] += 1
            conn.commit()
        finally:
            conn.close()

    # ---- user_agent.db: 补列 + 回填 ----
    agent_db = os.path.join(_DATA_DIR, "user_agent.db")
    if "knowledge_mastery" in pending and os.path.exists(agent_db):
        conn = sqlite3.connect(agent_db)
        conn.row_factory = sqlite3.Row
        try:
            for col in pending["knowledge_mastery"]:
                conn.execute(f"ALTER TABLE knowledge_mastery ADD COLUMN {col} {_MASTERY_COLUMNS[col]}")
            conn.commit()
            result["altered"].append(f"knowledge_mastery(+{len(pending['knowledge_mastery'])})")

            rows = conn.execute("SELECT * FROM knowledge_mastery").fetchall()
            for r in rows:
                kp_id = ensure_knowledge_point(r["subject_id"], r["knowledge_point"], source="migration")
                # 历史掌握度基于旧规则（含任务完成即加分），标为暂估并注明依据，不伪装为精确值
                tc = r["test_count"] or 0
                cc = r["correct_count"] or 0
                status = MASTERY_ESTIMATED if tc > 0 else MASTERY_PENDING
                reason = (
                    f"历史数据迁移：{tc} 次测练（含旧规则下的任务完成加分），"
                    f"仅供参考，需重新作答校准" if tc > 0 else "历史数据迁移：无独立作答证据，待评估"
                )
                conn.execute(
                    """UPDATE knowledge_mastery
                       SET knowledge_point_id=?, mastery_status=?, evidence_count=?,
                           independent_correct_count=?, independent_wrong_count=?,
                           mastery_reason=?
                       WHERE id=?""",
                    (kp_id, status, tc, cc, max(0, tc - cc), reason, r["id"]))
                result["backfilled"] += 1
            conn.commit()
        finally:
            conn.close()

    # ---- chat.db: 会话归属 + 消息关联 ----
    chat_db = os.path.join(_DATA_DIR, "chat.db")
    if ("conversations" in pending or "messages" in pending) and os.path.exists(chat_db):
        conn = sqlite3.connect(chat_db)
        try:
            if "conversations" in pending:
                for col in pending["conversations"]:
                    conn.execute(
                        f"ALTER TABLE conversations ADD COLUMN {col} {_CONVERSATION_COLUMNS[col]}")
                conn.execute(
                    "UPDATE conversations SET user_id=? WHERE user_id IS NULL", (LEGACY_OWNER,))
                result["altered"].append(f"conversations(+{len(pending['conversations'])})")
            if "messages" in pending:
                for col in pending["messages"]:
                    conn.execute(
                        f"ALTER TABLE messages ADD COLUMN {col} {_MESSAGE_COLUMNS[col]}")
                result["altered"].append(f"messages(+{len(pending['messages'])})")
            conn.commit()
        finally:
            conn.close()

    # ---- 历史 default_user 数据打标（保留原数据 + 显式认领方案） ----
    try:
        result["legacy_marked"] = _mark_legacy_owner()
    except Exception as e:
        logger.warning(f"历史数据打标失败（不影响迁移）: {e}")

    logger.info(f"学习数据迁移完成: {result}")
    return result


def _mark_legacy_owner() -> int:
    """
    将历史 default_user 归属标记为"待认领"。

    不删除、不改归属、不静默分配给其他账号；仅登记待认领清单，
    用户登录后可显式认领（见 auth_service.claim_legacy_data）。
    """
    conn = _get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS legacy_data_claims (
                id TEXT PRIMARY KEY,
                owner_label TEXT NOT NULL,
                data_scope TEXT NOT NULL,
                record_count INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'UNCLAIMED',
                claimed_by TEXT,
                created_at TEXT NOT NULL,
                claimed_at TEXT,
                UNIQUE(owner_label, data_scope)
            )
        """)
        counts = {
            "learning.db:learning_plans": _count_rows(os.path.join(_DATA_DIR, "learning.db"),
                                                     "learning_plans"),
            "user_agent.db:user_profiles": _count_rows(os.path.join(_DATA_DIR, "user_agent.db"),
                                                      "user_profiles"),
            "chat.db:conversations": _count_rows(os.path.join(_DATA_DIR, "chat.db"),
                                                "conversations"),
        }
        marked = 0
        for scope, cnt in counts.items():
            if cnt <= 0:
                continue
            try:
                conn.execute(
                    """INSERT INTO legacy_data_claims
                       (id, owner_label, data_scope, record_count, status, created_at)
                       VALUES (?,?,?,?, 'UNCLAIMED', ?)""",
                    (_new_id("claim"), "default_user", scope, cnt, _now()))
                marked += 1
            except sqlite3.IntegrityError:
                conn.execute(
                    "UPDATE legacy_data_claims SET record_count=? WHERE owner_label=? AND data_scope=?",
                    (cnt, "default_user", scope))
        conn.commit()
        return marked
    finally:
        conn.close()


def _count_rows(db_path: str, table: str) -> int:
    if not os.path.exists(db_path):
        return 0
    try:
        conn = sqlite3.connect(db_path)
        try:
            if not _table_exists(conn, table):
                return 0
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


# 导入时创建新表并执行幂等迁移（首次会先备份）
_init_db()
try:
    MIGRATION_RESULT = migrate_legacy()
except Exception as _e:  # 迁移失败不阻断服务启动，但记录错误
    logger.error(f"学习数据迁移失败: {_e}", exc_info=True)
    MIGRATION_RESULT = {"error": str(_e)}


__all__ = [
    "DB_PATH",
    "BACKUP_DIR",
    "QUESTION_TYPES",
    "ATTEMPT_KINDS",
    "GRADE_GRADED",
    "GRADE_PENDING_SELF",
    "GRADE_AI_GRADED",
    "REVIEW_PENDING",
    "REVIEW_DONE",
    "REVIEW_CANCELLED",
    "MASTERY_PENDING",
    "MASTERY_ESTIMATED",
    "normalize_kp_name",
    "ensure_knowledge_point",
    "add_knowledge_point_alias",
    "get_knowledge_point",
    "get_knowledge_point_by_name",
    "list_knowledge_points",
    "migrate_legacy",
    "ensure_columns",
    "_get_db",
    "_now",
    "_new_id",
]
