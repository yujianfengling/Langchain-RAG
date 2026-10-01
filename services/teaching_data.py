"""
教师端数据层（2026-09-26）

设计目标：让"教师布置的教学内容"与"学生学习结果"在**同一套既有机制**上联动，
而不是另起一套题库/判分/统计。

库与表：
- 新增独立库 `teaching.db`（课程域）：courses / classes / class_members / invites /
  course_materials / lesson_plans / lesson_plan_versions / assignments /
  assignment_questions / course_audit / subject_owners
- **不改动**既有业务库的表语义，只做幂等加列：
  - `auth.db: users.role`（见 auth_service）
  - `learning.db: learning_tasks.assignment_id`（教师任务 → 作业的溯源）

兼容方案（不静默归属历史资料）：
- 历史全局学科（`data/subjects.json` 里已有的、没有归属记录的学科）保持原样：
  任何登录用户仍可读，写操作沿用既有白名单。
- 教师新建课程时，后端为该课程**新建一个学科**并写入 `subject_owners`；
  只有写了归属记录的学科才受"课程成员可见"约束。
  也就是说：历史资料**不会**被自动分配给任何教师，需要教师显式把资料导入自己的课程。

迁移可重复执行；不删除任何历史数据。
"""

from config.path_config import get_data_dir
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from utils.logger import logger

_DATA_DIR = str(get_data_dir())
DB_PATH = os.path.join(_DATA_DIR, "teaching.db")

# 课程可见范围
VISIBILITY_STUDENTS = "students"      # 课程成员（含学生）可见
VISIBILITY_TEACHER = "teacher_only"   # 仅授课教师可见（草稿资料）

COURSE_STATUS = ("active", "archived")
MEMBER_STATUS = ("active", "left", "removed")
PLAN_STATUS = ("draft", "published")
ASSIGNMENT_STATUS = ("draft", "published", "closed")


class TeachingError(Exception):
    """教师端业务异常（携带 error_type，路由据此映射 HTTP 状态码）"""

    def __init__(self, message: str, error_type: str = "unknown"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


def _get_db() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_class_columns(conn: sqlite3.Connection) -> None:
    """幂等补 classes 的邀请码生命周期列（老库升级用）"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(classes)").fetchall()}
    for name, ddl in (
        ("join_code_expires_at", "TEXT"),
        ("join_code_revoked_at", "TEXT"),
        ("join_code_uses", "INTEGER DEFAULT 0"),
        ("join_code_max_uses", "INTEGER DEFAULT 200"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE classes ADD COLUMN {name} {ddl}")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS join_attempts (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            code TEXT,
            ok INTEGER DEFAULT 0,
            reason TEXT,
            created_at TEXT NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_join_attempts "
                 "ON join_attempts(user_id, created_at)")
    conn.commit()


def _init_db() -> None:
    """建表（幂等）"""
    conn = _get_db()
    try:
        conn.executescript("""
        -- 课程：一门课对应一位授课教师（owner）与一个专属学科（subject_id）
        CREATE TABLE IF NOT EXISTS courses (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            subject_id TEXT NOT NULL UNIQUE,
            owner_user_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_courses_owner ON courses(owner_user_id, status);

        -- 班级：课程下的教学单元，学生通过邀请码加入
        CREATE TABLE IF NOT EXISTS classes (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            name TEXT NOT NULL,
            join_code TEXT NOT NULL UNIQUE,
            join_code_expires_at TEXT,
            join_code_revoked_at TEXT,
            join_code_uses INTEGER DEFAULT 0,
            join_code_max_uses INTEGER DEFAULT 200,
            status TEXT NOT NULL DEFAULT 'active',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_classes_course ON classes(course_id, status);

        -- 班级成员：保留加入/退出/移除记录（不物理删除）
        CREATE TABLE IF NOT EXISTS class_members (
            id TEXT PRIMARY KEY,
            class_id TEXT NOT NULL,
            course_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            joined_at TEXT NOT NULL,
            left_at TEXT,
            removed_at TEXT,
            removed_by TEXT,
            source TEXT DEFAULT 'code',
            UNIQUE(class_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_members_user ON class_members(user_id, status);
        CREATE INDEX IF NOT EXISTS idx_members_class ON class_members(class_id, status);

        -- 加入/退班事件留痕（保留必要记录）
        CREATE TABLE IF NOT EXISTS membership_events (
            id TEXT PRIMARY KEY,
            class_id TEXT,
            course_id TEXT,
            user_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor_user_id TEXT,
            detail_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mem_events_class ON membership_events(class_id, created_at);

        -- 课程资料：一条记录 = 课程学科知识库里的一个文件
        CREATE TABLE IF NOT EXISTS course_materials (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            size_bytes INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ready',
            error_message TEXT,
            visibility TEXT NOT NULL DEFAULT 'students',
            uploaded_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(course_id, filename)
        );
        CREATE INDEX IF NOT EXISTS idx_materials_course ON course_materials(course_id, status);

        -- 教案（草稿/发布）与版本
        CREATE TABLE IF NOT EXISTS lesson_plans (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            class_id TEXT,
            title TEXT NOT NULL,
            topic TEXT,
            hours REAL,
            status TEXT NOT NULL DEFAULT 'draft',
            current_version INTEGER DEFAULT 1,
            published_version INTEGER,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_plans_course ON lesson_plans(course_id, status);

        CREATE TABLE IF NOT EXISTS lesson_plan_versions (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            content_json TEXT NOT NULL,
            evidence_json TEXT DEFAULT '[]',
            ai_generated INTEGER DEFAULT 0,
            ai_model TEXT,
            insufficient_evidence INTEGER DEFAULT 0,
            note TEXT,
            edited_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(plan_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_plan_versions ON lesson_plan_versions(plan_id, version);

        -- 作业/练习：发布后按班级成员物化成学生端 learning_tasks
        CREATE TABLE IF NOT EXISTS assignments (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            class_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            assign_type TEXT NOT NULL DEFAULT 'quiz',
            knowledge_points_json TEXT DEFAULT '[]',
            due_date TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            estimated_minutes INTEGER DEFAULT 30,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT,
            closed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_assign_class ON assignments(class_id, status);
        CREATE INDEX IF NOT EXISTS idx_assign_course ON assignments(course_id, status);

        -- 作业包含的题目：复用既有 quiz_questions（不另建题库）
        CREATE TABLE IF NOT EXISTS assignment_questions (
            id TEXT PRIMARY KEY,
            assignment_id TEXT NOT NULL,
            question_id TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            UNIQUE(assignment_id, question_id)
        );
        CREATE INDEX IF NOT EXISTS idx_assign_q ON assignment_questions(assignment_id, seq);

        -- 课程操作留痕（谁在何时改了什么）
        CREATE TABLE IF NOT EXISTS course_audit (
            id TEXT PRIMARY KEY,
            course_id TEXT,
            actor_user_id TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            detail_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_course ON course_audit(course_id, created_at);

        -- 学科归属登记：**只有登记过的学科**才受课程权限约束
        CREATE TABLE IF NOT EXISTS subject_owners (
            subject_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_subject_owner ON subject_owners(owner_user_id);
        """)
        conn.commit()
        # 邀请码生命周期列 + 加入尝试记录（老库幂等升级）
        _ensure_class_columns(conn)
    finally:
        conn.close()


_init_db()


def _ensure_business_columns() -> Dict[str, Any]:
    """
    给既有业务库**幂等加列**（不改语义、不删数据）。

    - learning.db: learning_tasks.assignment_id（教师作业物化出的任务溯源）

    注意：learning_tasks 属于 `learning.db`（services.learning_service），
    不是 `learning_loop.db`（services.learning_data）——两者都叫"学习库"但职责不同。
    """
    result: Dict[str, Any] = {"added": [], "errors": []}
    try:
        from services.learning_service import _get_db as _learning_db
        conn = _learning_db()
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(learning_tasks)").fetchall()}
            if not cols:
                raise RuntimeError("learning_tasks 表不存在（库路径异常）")
            if "assignment_id" not in cols:
                conn.execute("ALTER TABLE learning_tasks ADD COLUMN assignment_id TEXT")
                result["added"].append("learning_tasks.assignment_id")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_assignment "
                         "ON learning_tasks(assignment_id)")
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # 迁移失败要显式暴露，不静默
        result["errors"].append(f"learning_tasks.assignment_id: {e}")
        logger.warning(f"教师端加列失败: {e}")
    return result


def migrate() -> Dict[str, Any]:
    """执行迁移（可重复运行）：建教师端库 + 幂等补既有库的列"""
    _init_db()
    added = _ensure_business_columns()
    # users.role（auth_service 的 _ensure_role_column 在模块导入时已执行；
    # 这里显式再跑一次，保证"单独执行迁移脚本"也能得到同一结构）
    auth_columns: List[str] = []
    try:
        from services import auth_service
        conn = auth_service._get_db()
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "role" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'student'")
                auth_columns.append("users.role")
            if "role_granted_at" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN role_granted_at TEXT")
                auth_columns.append("users.role_granted_at")
            if "role_source" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN role_source TEXT")
                auth_columns.append("users.role_source")
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        added["errors"].append(f"users.role: {e}")
        logger.warning(f"users.role 迁移失败: {e}")
    return {"teaching_db": DB_PATH, "columns": added, "auth_columns": auth_columns,
            "auth_columns_added": auth_columns}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json_load(raw: Any, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except (ValueError, TypeError):
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# ==================== 课程 ====================

def _row_to_course(row, keys=None) -> Dict[str, Any]:
    keys = keys or row.keys()
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "subject_id": row["subject_id"],
        "owner_user_id": row["owner_user_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_course(title: str, owner_user_id: str, subject_id: str,
                  description: Optional[str] = None) -> Dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise TeachingError("课程名称不能为空", "invalid_request")
    if not subject_id:
        raise TeachingError("课程必须关联一个学科", "invalid_request")
    cid = new_id("course")
    now = _now()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO courses (id, title, description, subject_id, owner_user_id,
                                    status, created_at, updated_at)
               VALUES (?,?,?,?,?, 'active', ?, ?)""",
            (cid, title, description, subject_id, owner_user_id, now, now))
        conn.execute(
            "INSERT INTO subject_owners (subject_id, course_id, owner_user_id, created_at) "
            "VALUES (?,?,?,?)",
            (subject_id, cid, owner_user_id, now))
        conn.commit()
    except sqlite3.IntegrityError as e:
        raise TeachingError(f"课程创建失败（学科已被其他课程占用？）：{e}", "conflict")
    finally:
        conn.close()
    return get_course(cid) or {}


def get_course(course_id: str) -> Optional[Dict[str, Any]]:
    if not course_id:
        return None
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        return _row_to_course(row) if row else None
    finally:
        conn.close()


def list_courses_by_owner(owner_user_id: str, include_archived: bool = False) -> List[Dict[str, Any]]:
    conn = _get_db()
    try:
        q = "SELECT * FROM courses WHERE owner_user_id=?"
        params: List[Any] = [owner_user_id]
        if not include_archived:
            q += " AND status='active'"
        q += " ORDER BY created_at DESC"
        return [_row_to_course(r) for r in conn.execute(q, params).fetchall()]
    finally:
        conn.close()


def update_course(course_id: str, *, title: Optional[str] = None,
                  description: Optional[str] = None,
                  status: Optional[str] = None) -> Dict[str, Any]:
    sets, params = [], []
    if title is not None:
        if not title.strip():
            raise TeachingError("课程名称不能为空", "invalid_request")
        sets.append("title=?"); params.append(title.strip())
    if description is not None:
        sets.append("description=?"); params.append(description)
    if status is not None:
        if status not in COURSE_STATUS:
            raise TeachingError(f"状态只能是 {'/'.join(COURSE_STATUS)}", "invalid_request")
        sets.append("status=?"); params.append(status)
        if status == "archived":
            sets.append("archived_at=?"); params.append(_now())
    if not sets:
        return get_course(course_id) or {}
    sets.append("updated_at=?"); params.append(_now())
    params.append(course_id)
    conn = _get_db()
    try:
        conn.execute(f"UPDATE courses SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    finally:
        conn.close()
    return get_course(course_id) or {}


# ==================== 班级与成员 ====================

# 邀请码默认有效期与可用次数（可按班调整；默认宽松，便于长期教学使用）
JOIN_CODE_TTL_DAYS = int(os.getenv("JOIN_CODE_TTL_DAYS", "180"))
JOIN_CODE_MAX_USES = int(os.getenv("JOIN_CODE_MAX_USES", "200"))


def create_class(course_id: str, name: str, created_by: str,
                 join_code: Optional[str] = None,
                 ttl_days: Optional[int] = None,
                 max_uses: Optional[int] = None) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise TeachingError("班级名称不能为空", "invalid_request")
    code = join_code or _new_join_code()
    cid = new_id("class")
    now = datetime.now()
    ttl = JOIN_CODE_TTL_DAYS if ttl_days is None else int(ttl_days)
    if ttl <= 0 or ttl > 3650:
        raise TeachingError("邀请码有效期应在 1-3650 天之间", "invalid_request")
    uses = JOIN_CODE_MAX_USES if max_uses is None else int(max_uses)
    if uses < 1 or uses > 10000:
        raise TeachingError("邀请码可用次数应在 1-10000 之间", "invalid_request")
    expires = (now + timedelta(days=ttl)).isoformat(timespec="seconds")
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO classes (id, course_id, name, join_code, status, created_by,
                                    created_at, updated_at,
                                    join_code_expires_at, join_code_max_uses, join_code_uses)
               VALUES (?,?,?,?, 'active', ?, ?, ?, ?, ?, 0)""",
            (cid, course_id, name, code, created_by,
             now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"),
             expires, uses))
        conn.commit()
    except sqlite3.IntegrityError:
        raise TeachingError("邀请码冲突，请重试", "conflict")
    finally:
        conn.close()
    return get_class(cid) or {}


def _new_join_code() -> str:
    # 便于口述/输入：6 位大写字母数字，排除易混字符
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(alphabet[uuid.uuid4().int % len(alphabet)] for _ in range(6))


def get_class(class_id: str) -> Optional[Dict[str, Any]]:
    if not class_id:
        return None
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["member_count"] = conn.execute(
            "SELECT COUNT(*) FROM class_members WHERE class_id=? AND status='active'",
            (class_id,)).fetchone()[0]
        return data
    finally:
        conn.close()


def get_class_by_join_code(code: str) -> Optional[Dict[str, Any]]:
    """按邀请码取班级（**不做有效性判断**，有效性见 join_code_invalid_reason）"""
    code = (code or "").strip().upper()
    if not code:
        return None
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM classes WHERE join_code=? AND status='active'", (code,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def join_code_invalid_reason(klass: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    邀请码是否可用（程序判定）：不存在 / 已撤销 / 已过期 / 次数用尽
    返回 None 表示可用。
    """
    if not klass:
        return "邀请码无效"
    if klass.get("join_code_revoked_at"):
        return "邀请码已被教师撤销"
    expires = klass.get("join_code_expires_at")
    if expires:
        try:
            if datetime.fromisoformat(expires) < datetime.now():
                return "邀请码已过期，请向教师索取新的邀请码"
        except ValueError:
            return "邀请码有效期数据异常"
    uses = int(klass.get("join_code_uses") or 0)
    limit = int(klass.get("join_code_max_uses") or JOIN_CODE_MAX_USES)
    if limit > 0 and uses >= limit:
        return "邀请码使用次数已达上限"
    return None


def record_join_attempt(user_id: str, code: Optional[str], ok: bool,
                        reason: Optional[str] = None) -> None:
    """记录加入尝试（用于频率限制与审计，不记录成功加入的明文码以外信息）"""
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO join_attempts (id, user_id, code, ok, reason, created_at)
               VALUES (?,?,?,?,?,?)""",
            (new_id("ja"), user_id, (code or "").strip().upper()[:16],
             1 if ok else 0, reason, _now()))
        conn.commit()
    except Exception as e:
        logger.warning(f"记录加入尝试失败: {e}")
    finally:
        conn.close()


def recent_join_failures(user_id: str, window_minutes: int = 10) -> int:
    """最近 N 分钟内该用户失败的加入尝试次数（用于限速）"""
    from datetime import timedelta as _td
    since = (datetime.now() - _td(minutes=window_minutes)).isoformat(timespec="seconds")
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM join_attempts "
            "WHERE user_id=? AND ok=0 AND created_at >= ?", (user_id, since)).fetchone()
        return int(row["c"] if row else 0)
    finally:
        conn.close()


def increment_join_code_uses(class_id: str) -> None:
    conn = _get_db()
    try:
        conn.execute("UPDATE classes SET join_code_uses = join_code_uses + 1, updated_at=? "
                     "WHERE id=?", (_now(), class_id))
        conn.commit()
    finally:
        conn.close()


def list_classes(course_id: str, include_archived: bool = False) -> List[Dict[str, Any]]:
    conn = _get_db()
    try:
        q = "SELECT * FROM classes WHERE course_id=?"
        params: List[Any] = [course_id]
        if not include_archived:
            q += " AND status='active'"
        q += " ORDER BY created_at DESC"
        out = []
        for r in conn.execute(q, params).fetchall():
            d = dict(r)
            d["member_count"] = conn.execute(
                "SELECT COUNT(*) FROM class_members WHERE class_id=? AND status='active'",
                (d["id"],)).fetchone()[0]
            out.append(d)
        return out
    finally:
        conn.close()


def reset_join_code(class_id: str, ttl_days: Optional[int] = None) -> Dict[str, Any]:
    """
    轮换邀请码：旧码立即失效（含撤销标记与计数重置）
    """
    code = _new_join_code()
    ttl = JOIN_CODE_TTL_DAYS if ttl_days is None else int(ttl_days)
    expires = (datetime.now() + timedelta(days=ttl)).isoformat(timespec="seconds")
    conn = _get_db()
    try:
        conn.execute(
            """UPDATE classes SET join_code=?, join_code_expires_at=?, join_code_revoked_at=NULL,
               join_code_uses=0, updated_at=? WHERE id=?""",
            (code, expires, _now(), class_id))
        conn.commit()
    finally:
        conn.close()
    return get_class(class_id) or {}


def revoke_join_code(class_id: str) -> Dict[str, Any]:
    """单独撤销邀请码（不生成新码：班级临时关闭加入）"""
    conn = _get_db()
    try:
        conn.execute("UPDATE classes SET join_code_revoked_at=?, updated_at=? WHERE id=?",
                     (_now(), _now(), class_id))
        conn.commit()
    finally:
        conn.close()
    return get_class(class_id) or {}


def add_member(class_id: str, course_id: str, user_id: str,
               source: str = "code", actor_user_id: Optional[str] = None) -> Dict[str, Any]:
    """加入班级（重复加入是幂等的；曾退班/被移除可再次加入并留痕）"""
    now = _now()
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM class_members WHERE class_id=? AND user_id=?",
            (class_id, user_id)).fetchone()
        if row and row["status"] == "active":
            return dict(row)
        if row:
            conn.execute(
                """UPDATE class_members SET status='active', joined_at=?, left_at=NULL,
                   removed_at=NULL, removed_by=NULL, source=? WHERE id=?""",
                (now, source, row["id"]))
            mid = row["id"]
        else:
            mid = new_id("mem")
            conn.execute(
                """INSERT INTO class_members
                   (id, class_id, course_id, user_id, status, joined_at, source)
                   VALUES (?,?,?,?, 'active', ?, ?)""",
                (mid, class_id, course_id, user_id, now, source))
        conn.execute(
            """INSERT INTO membership_events
               (id, class_id, course_id, user_id, event_type, actor_user_id, created_at)
               VALUES (?,?,?,?, 'join', ?, ?)""",
            (new_id("mev"), class_id, course_id, user_id, actor_user_id or user_id, now))
        conn.commit()
        return dict(conn.execute("SELECT * FROM class_members WHERE id=?", (mid,)).fetchone())
    finally:
        conn.close()


def set_member_status(member_id: str, status: str, actor_user_id: str,
                      reason: Optional[str] = None) -> Dict[str, Any]:
    if status not in ("left", "removed"):
        raise TeachingError("状态只能是 left/removed", "invalid_request")
    now = _now()
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM class_members WHERE id=?", (member_id,)).fetchone()
        if not row:
            raise TeachingError("成员记录不存在", "not_found")
        if row["status"] != "active":
            return dict(row)
        if status == "left":
            conn.execute("UPDATE class_members SET status='left', left_at=? WHERE id=?",
                         (now, member_id))
        else:
            conn.execute(
                """UPDATE class_members SET status='removed', removed_at=?, removed_by=?
                   WHERE id=?""", (now, actor_user_id, member_id))
        conn.execute(
            """INSERT INTO membership_events
               (id, class_id, course_id, user_id, event_type, actor_user_id, detail_json, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (new_id("mev"), row["class_id"], row["course_id"], row["user_id"], status,
             actor_user_id, _dumps({"reason": reason}), now))
        conn.commit()
        return dict(conn.execute("SELECT * FROM class_members WHERE id=?", (member_id,)).fetchone())
    finally:
        conn.close()


def list_members(class_id: str, status: str = "active") -> List[Dict[str, Any]]:
    conn = _get_db()
    try:
        q = "SELECT * FROM class_members WHERE class_id=?"
        params: List[Any] = [class_id]
        if status:
            q += " AND status=?"
            params.append(status)
        q += " ORDER BY joined_at ASC"
        return [dict(r) for r in conn.execute(q, params).fetchall()]
    finally:
        conn.close()


def list_member_events(class_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM membership_events WHERE class_id=? ORDER BY created_at DESC LIMIT ?",
            (class_id, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = _json_load(d.pop("detail_json", "{}"), {})
            out.append(d)
        return out
    finally:
        conn.close()


def is_class_member(class_id: str, user_id: str) -> bool:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM class_members WHERE class_id=? AND user_id=? AND status='active'",
            (class_id, user_id)).fetchone()
        return row is not None
    finally:
        conn.close()


def active_class_ids_for_user(user_id: str) -> List[str]:
    conn = _get_db()
    try:
        return [r["class_id"] for r in conn.execute(
            "SELECT class_id FROM class_members WHERE user_id=? AND status='active'",
            (user_id,)).fetchall()]
    finally:
        conn.close()


def active_course_ids_for_user(user_id: str) -> List[str]:
    """学生当前在读的课程（用于读权限判定）"""
    conn = _get_db()
    try:
        return [r["course_id"] for r in conn.execute(
            "SELECT DISTINCT course_id FROM class_members WHERE user_id=? AND status='active'",
            (user_id,)).fetchall()]
    finally:
        conn.close()


def active_course_ids_for_teacher(user_id: str) -> List[str]:
    conn = _get_db()
    try:
        return [r["id"] for r in conn.execute(
            "SELECT id FROM courses WHERE owner_user_id=? AND status='active'",
            (user_id,)).fetchall()]
    finally:
        conn.close()


# ==================== 课程资料 ====================

def upsert_material(course_id: str, subject_id: str, filename: str, *,
                    size_bytes: int = 0, status: str = "ready",
                    error_message: Optional[str] = None,
                    visibility: str = VISIBILITY_STUDENTS,
                    uploaded_by: str) -> Dict[str, Any]:
    now = _now()
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM course_materials WHERE course_id=? AND filename=?",
            (course_id, filename)).fetchone()
        if row:
            conn.execute(
                """UPDATE course_materials SET size_bytes=?, status=?, error_message=?,
                   visibility=?, updated_at=? WHERE id=?""",
                (size_bytes, status, error_message, visibility, now, row["id"]))
            mid = row["id"]
        else:
            mid = new_id("mat")
            conn.execute(
                """INSERT INTO course_materials
                   (id, course_id, subject_id, filename, size_bytes, status, error_message,
                    visibility, uploaded_by, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (mid, course_id, subject_id, filename, size_bytes, status, error_message,
                 visibility, uploaded_by, now, now))
        conn.commit()
        return dict(conn.execute("SELECT * FROM course_materials WHERE id=?", (mid,)).fetchone())
    finally:
        conn.close()


def list_materials(course_id: str, include_teacher_only: bool = True) -> List[Dict[str, Any]]:
    conn = _get_db()
    try:
        q = "SELECT * FROM course_materials WHERE course_id=?"
        params: List[Any] = [course_id]
        if not include_teacher_only:
            q += " AND visibility=?"
            params.append(VISIBILITY_STUDENTS)
        q += " ORDER BY created_at DESC"
        return [dict(r) for r in conn.execute(q, params).fetchall()]
    finally:
        conn.close()


def get_material(material_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM course_materials WHERE id=?", (material_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_material(material_id: str) -> bool:
    conn = _get_db()
    try:
        cur = conn.execute("DELETE FROM course_materials WHERE id=?", (material_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_material_visibility(material_id: str, visibility: str) -> Dict[str, Any]:
    if visibility not in (VISIBILITY_STUDENTS, VISIBILITY_TEACHER):
        raise TeachingError("可见范围只能是 students / teacher_only", "invalid_request")
    conn = _get_db()
    try:
        conn.execute("UPDATE course_materials SET visibility=?, updated_at=? WHERE id=?",
                     (visibility, _now(), material_id))
        conn.commit()
    finally:
        conn.close()
    return get_material(material_id) or {}


# ==================== 教案 ====================

def create_lesson_plan(course_id: str, created_by: str, *, title: str,
                       topic: Optional[str] = None, hours: Optional[float] = None,
                       class_id: Optional[str] = None,
                       content: Optional[Dict[str, Any]] = None,
                       evidence: Optional[List[Dict[str, Any]]] = None,
                       ai_generated: bool = False, ai_model: Optional[str] = None,
                       insufficient_evidence: bool = False,
                       note: Optional[str] = None) -> Dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise TeachingError("教案标题不能为空", "invalid_request")
    pid = new_id("plan")
    now = _now()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO lesson_plans (id, course_id, class_id, title, topic, hours,
                                         status, current_version, created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?, 'draft', 1, ?, ?, ?)""",
            (pid, course_id, class_id, title, topic, hours, created_by, now, now))
        _insert_plan_version(conn, pid, 1, content or {}, evidence or [], ai_generated,
                             ai_model, insufficient_evidence, note, created_by)
        conn.commit()
    finally:
        conn.close()
    return get_lesson_plan(pid) or {}


def _insert_plan_version(conn: sqlite3.Connection, plan_id: str, version: int,
                         content: Dict[str, Any], evidence: List[Dict[str, Any]],
                         ai_generated: bool, ai_model: Optional[str],
                         insufficient_evidence: bool, note: Optional[str],
                         edited_by: str) -> str:
    vid = new_id("lpv")
    conn.execute(
        """INSERT INTO lesson_plan_versions
           (id, plan_id, version, content_json, evidence_json, ai_generated, ai_model,
            insufficient_evidence, note, edited_by, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (vid, plan_id, version, _dumps(content), _dumps(evidence),
         1 if ai_generated else 0, ai_model, 1 if insufficient_evidence else 0,
         note, edited_by, _now()))
    return vid


def add_lesson_plan_version(plan_id: str, *, content: Dict[str, Any],
                            evidence: Optional[List[Dict[str, Any]]] = None,
                            ai_generated: bool = False, ai_model: Optional[str] = None,
                            insufficient_evidence: bool = False,
                            note: Optional[str] = None,
                            edited_by: str) -> Dict[str, Any]:
    """
    追加一个新版本（教师修改不会被重新生成覆盖：每次修改都是新版本，旧版本保留）
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM lesson_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise TeachingError("教案不存在", "not_found")
        if row["status"] == "published" and row["published_version"] == row["current_version"]:
            # 发布后继续编辑 → 回到草稿状态（发布版本仍保留在学生可见的 published_version）
            pass
        new_version = int(row["current_version"] or 1) + 1
        _insert_plan_version(conn, plan_id, new_version, content,
                             evidence if evidence is not None else _plan_evidence(conn, plan_id, row["current_version"]),
                             ai_generated, ai_model, insufficient_evidence, note, edited_by)
        conn.execute(
            "UPDATE lesson_plans SET current_version=?, updated_at=? WHERE id=?",
            (new_version, _now(), plan_id))
        conn.commit()
    finally:
        conn.close()
    return get_lesson_plan(plan_id) or {}


def _plan_evidence(conn: sqlite3.Connection, plan_id: str, version: int) -> List[Dict[str, Any]]:
    row = conn.execute(
        "SELECT evidence_json FROM lesson_plan_versions WHERE plan_id=? AND version=?",
        (plan_id, version)).fetchone()
    return _json_load(row["evidence_json"], []) if row else []


def get_lesson_plan(plan_id: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
    if not plan_id:
        return None
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM lesson_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            return None
        plan = dict(row)
        want = version or plan.get("current_version") or 1
        vrow = conn.execute(
            "SELECT * FROM lesson_plan_versions WHERE plan_id=? AND version=?",
            (plan_id, want)).fetchone()
        if not vrow:
            vrow = conn.execute(
                "SELECT * FROM lesson_plan_versions WHERE plan_id=? ORDER BY version DESC LIMIT 1",
                (plan_id,)).fetchone()
        if vrow:
            plan["version"] = vrow["version"]
            plan["content"] = _json_load(vrow["content_json"], {})
            plan["evidence"] = _json_load(vrow["evidence_json"], [])
            plan["ai_generated"] = bool(vrow["ai_generated"])
            plan["ai_model"] = vrow["ai_model"]
            plan["insufficient_evidence"] = bool(vrow["insufficient_evidence"])
            plan["version_note"] = vrow["note"]
            plan["version_created_at"] = vrow["created_at"]
            plan["version_count"] = conn.execute(
                "SELECT COUNT(*) FROM lesson_plan_versions WHERE plan_id=?", (plan_id,)).fetchone()[0]
        return plan
    finally:
        conn.close()


def list_lesson_plans(course_id: str, status: Optional[str] = None,
                      class_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_db()
    try:
        q = "SELECT * FROM lesson_plans WHERE course_id=?"
        params: List[Any] = [course_id]
        if status:
            q += " AND status=?"
            params.append(status)
        if class_id:
            q += " AND class_id=?"
            params.append(class_id)
        q += " ORDER BY updated_at DESC"
        out = []
        for r in conn.execute(q, params).fetchall():
            d = dict(r)
            d["version_count"] = conn.execute(
                "SELECT COUNT(*) FROM lesson_plan_versions WHERE plan_id=?", (d["id"],)).fetchone()[0]
            out.append(d)
        return out
    finally:
        conn.close()


def list_lesson_plan_versions(plan_id: str) -> List[Dict[str, Any]]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM lesson_plan_versions WHERE plan_id=? ORDER BY version DESC",
            (plan_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def publish_lesson_plan(plan_id: str, actor_user_id: str) -> Dict[str, Any]:
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM lesson_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise TeachingError("教案不存在", "not_found")
        now = _now()
        conn.execute(
            """UPDATE lesson_plans SET status='published', published_version=current_version,
               published_at=?, updated_at=? WHERE id=?""",
            (now, now, plan_id))
        conn.commit()
    finally:
        conn.close()
    return get_lesson_plan(plan_id) or {}


def update_lesson_plan_meta(plan_id: str, *, title: Optional[str] = None,
                            topic: Optional[str] = None, hours: Optional[float] = None,
                            class_id: Optional[str] = None) -> Dict[str, Any]:
    sets, params = [], []
    if title is not None:
        if not title.strip():
            raise TeachingError("教案标题不能为空", "invalid_request")
        sets.append("title=?"); params.append(title.strip())
    if topic is not None:
        sets.append("topic=?"); params.append(topic)
    if hours is not None:
        sets.append("hours=?"); params.append(hours)
    if class_id is not None:
        sets.append("class_id=?"); params.append(class_id)
    if sets:
        sets.append("updated_at=?"); params.append(_now()); params.append(plan_id)
        conn = _get_db()
        try:
            conn.execute(f"UPDATE lesson_plans SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
        finally:
            conn.close()
    return get_lesson_plan(plan_id) or {}


# ==================== 作业 / 练习 ====================

def create_assignment(course_id: str, class_id: str, subject_id: str, created_by: str, *,
                      title: str, description: Optional[str] = None,
                      assign_type: str = "quiz",
                      knowledge_points: Optional[List[str]] = None,
                      due_date: Optional[str] = None,
                      estimated_minutes: int = 30,
                      question_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise TeachingError("作业标题不能为空", "invalid_request")
    if assign_type not in ("quiz", "task"):
        raise TeachingError("作业类型只能是 quiz / task", "invalid_request")
    aid = new_id("asg")
    now = _now()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO assignments
               (id, course_id, class_id, subject_id, title, description, assign_type,
                knowledge_points_json, due_date, status, estimated_minutes, created_by,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?, 'draft', ?, ?, ?, ?)""",
            (aid, course_id, class_id, subject_id, title, description, assign_type,
             _dumps(knowledge_points or []), due_date, int(estimated_minutes or 30),
             created_by, now, now))
        for seq, qid in enumerate(question_ids or []):
            conn.execute(
                """INSERT OR IGNORE INTO assignment_questions (id, assignment_id, question_id, seq)
                   VALUES (?,?,?,?)""",
                (new_id("asq"), aid, qid, seq))
        conn.commit()
    finally:
        conn.close()
    return get_assignment(aid) or {}


def set_assignment_questions(assignment_id: str, question_ids: List[str]) -> Dict[str, Any]:
    conn = _get_db()
    try:
        row = conn.execute("SELECT status FROM assignments WHERE id=?",
                           (assignment_id,)).fetchone()
        if not row:
            raise TeachingError("作业不存在", "not_found")
        if row["status"] != "draft":
            raise TeachingError("已发布的作业不能修改题目（请新建一次作业）", "invalid_request")
        conn.execute("DELETE FROM assignment_questions WHERE assignment_id=?", (assignment_id,))
        for seq, qid in enumerate(question_ids):
            conn.execute(
                """INSERT OR IGNORE INTO assignment_questions (id, assignment_id, question_id, seq)
                   VALUES (?,?,?,?)""",
                (new_id("asq"), assignment_id, qid, seq))
        conn.execute("UPDATE assignments SET updated_at=? WHERE id=?",
                     (_now(), assignment_id))
        conn.commit()
    finally:
        conn.close()
    return get_assignment(assignment_id) or {}


def get_assignment(assignment_id: str) -> Optional[Dict[str, Any]]:
    if not assignment_id:
        return None
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM assignments WHERE id=?", (assignment_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["knowledge_points"] = _json_load(d.pop("knowledge_points_json", "[]"), [])
        d["question_ids"] = [r["question_id"] for r in conn.execute(
            "SELECT question_id FROM assignment_questions WHERE assignment_id=? ORDER BY seq",
            (assignment_id,)).fetchall()]
        return d
    finally:
        conn.close()


def list_assignments(course_id: Optional[str] = None, class_id: Optional[str] = None,
                     status: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_db()
    try:
        q = "SELECT * FROM assignments WHERE 1=1"
        params: List[Any] = []
        if course_id:
            q += " AND course_id=?"; params.append(course_id)
        if class_id:
            q += " AND class_id=?"; params.append(class_id)
        if status:
            q += " AND status=?"; params.append(status)
        q += " ORDER BY created_at DESC"
        out = []
        for r in conn.execute(q, params).fetchall():
            d = dict(r)
            d["knowledge_points"] = _json_load(d.pop("knowledge_points_json", "[]"), [])
            d["question_count"] = conn.execute(
                "SELECT COUNT(*) FROM assignment_questions WHERE assignment_id=?",
                (d["id"],)).fetchone()[0]
            out.append(d)
        return out
    finally:
        conn.close()


def list_assignments_for_classes(class_ids: List[str],
                                 status: str = "published") -> List[Dict[str, Any]]:
    if not class_ids:
        return []
    conn = _get_db()
    try:
        marks = ",".join("?" for _ in class_ids)
        q = f"SELECT * FROM assignments WHERE class_id IN ({marks})"
        params: List[Any] = list(class_ids)
        if status:
            q += " AND status=?"
            params.append(status)
        q += " ORDER BY published_at DESC, created_at DESC"
        out = []
        for r in conn.execute(q, params).fetchall():
            d = dict(r)
            d["knowledge_points"] = _json_load(d.pop("knowledge_points_json", "[]"), [])
            d["question_count"] = conn.execute(
                "SELECT COUNT(*) FROM assignment_questions WHERE assignment_id=?",
                (d["id"],)).fetchone()[0]
            out.append(d)
        return out
    finally:
        conn.close()


def mark_assignment_published(assignment_id: str, due_date: Optional[str] = None) -> Dict[str, Any]:
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM assignments WHERE id=?", (assignment_id,)).fetchone()
        if not row:
            raise TeachingError("作业不存在", "not_found")
        if row["status"] == "published":
            return dict(row)
        sets = ["status='published'", "published_at=?", "updated_at=?"]
        params: List[Any] = [_now(), _now()]
        if due_date:
            sets.insert(1, "due_date=?")
            params.insert(0, due_date)
        params.append(assignment_id)
        conn.execute(f"UPDATE assignments SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    finally:
        conn.close()
    return get_assignment(assignment_id) or {}


def close_assignment(assignment_id: str) -> Dict[str, Any]:
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE assignments SET status='closed', closed_at=?, updated_at=? WHERE id=?",
            (_now(), _now(), assignment_id))
        conn.commit()
    finally:
        conn.close()
    return get_assignment(assignment_id) or {}


# ==================== 审计 ====================

def audit(course_id: Optional[str], actor_user_id: Optional[str], action: str,
          target_type: Optional[str] = None, target_id: Optional[str] = None,
          detail: Optional[Dict[str, Any]] = None) -> None:
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO course_audit
               (id, course_id, actor_user_id, action, target_type, target_id,
                detail_json, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (new_id("aud"), course_id, actor_user_id, action, target_type, target_id,
             _dumps(detail or {}), _now()))
        conn.commit()
    except Exception as e:
        logger.warning(f"课程审计写入失败: {e}")
    finally:
        conn.close()


def list_audit(course_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM course_audit WHERE course_id=? ORDER BY created_at DESC LIMIT ?",
            (course_id, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = _json_load(d.pop("detail_json", "{}"), {})
            out.append(d)
        return out
    finally:
        conn.close()


# ==================== 学科归属（兼容既有全局学科） ====================

def subject_owner(subject_id: str) -> Optional[Dict[str, Any]]:
    if not subject_id:
        return None
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM subject_owners WHERE subject_id=?",
                           (subject_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(migrate(), ensure_ascii=False, indent=1))
