"""
最小用户身份服务（阶段一）

目标：
- 学习记录必须有真实用户归属，后端从统一机制取 user_id，不信任请求体传入
- 提供本轮所需的最小注册 / 登录 / 会话令牌
- 历史 default_user 数据可显式认领，不静默分配给任意账号

安全说明：
- 口令使用 PBKDF2-HMAC-SHA256 + 随机盐存储，不保存明文
- 会话令牌为随机串，服务端存储并校验有效期 / 撤销状态
- 本模块不涉及真实密钥；演示用种子账号的口令在文档中标注，仅用于本地验收
"""

from config.path_config import get_data_dir
import hashlib
import hmac
import os
import re
import secrets
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from utils.logger import logger
from services.learning_data import LEGACY_OWNER, _DATA_DIR, _BUSINESS_DBS

_DATA_DIR = str(get_data_dir())
DB_PATH = os.path.join(_DATA_DIR, "auth.db")

# 会话默认有效期
SESSION_TTL_HOURS = int(os.getenv("AUTH_SESSION_TTL_HOURS", "72"))
# PBKDF2 迭代次数
_PBKDF2_ITERATIONS = 120_000

# 演示用种子账号（口令仅用于本地验收，可在 .env 覆盖）
LEGACY_USERNAME = LEGACY_OWNER
LEGACY_DEMO_PASSWORD = os.getenv("LEGACY_DEMO_PASSWORD", "legacy-demo-pass")

_USERNAME_RE = re.compile(r"^[\w一-鿿.-]{3,32}$")


class AuthError(Exception):
    """身份服务异常（携带 error_type）"""

    def __init__(self, message: str, error_type: str = "auth_error"):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


def _get_db() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ==================== 角色（教师端，2026-09-26） ====================
#
# 设计原则：
# 1. 角色**只由后端**判定，前端菜单只是展示；所有越权判断都在服务端做。
# 2. 新账号与历史账号默认 student，**不静默提升**任何人。
# 3. teacher 只能由**受控通道**开通（环境白名单 / 管理员 / 教师邀请码），
#    详见下方"教师开通"一节；**创建课程不再自动授予教师身份**（2026-09-27 修复）。
# 4. admin 只能来自环境变量白名单（ADMIN_USERS），不能自助获取。

ROLE_STUDENT = "student"
ROLE_TEACHER = "teacher"
ROLE_ADMIN = "admin"
ROLES = (ROLE_STUDENT, ROLE_TEACHER, ROLE_ADMIN)


def is_admin(user_id: Optional[str]) -> bool:
    """管理员：仅环境变量白名单（ADMIN_USERS，逗号分隔用户名）"""
    if not user_id:
        return False
    raw = (os.getenv("ADMIN_USERS") or "").strip()
    if not raw:
        return False
    names = {n.strip() for n in raw.split(",") if n.strip()}
    if not names:
        return False
    user = get_user(user_id)
    return bool(user) and user.get("username") in names


def get_role(user_id: Optional[str]) -> str:
    """
    取角色：管理员白名单 > 教师白名单(TEACHER_USERS) > users.role > student

    白名单只影响"判定结果"，不写库（部署时用环境变量指定初始教师即可）。
    """
    if is_admin(user_id):
        return ROLE_ADMIN
    user = get_user(user_id) if user_id else None
    if not user:
        return ROLE_STUDENT
    if (user.get("username") or "").lower() in _teacher_users_from_env():
        return ROLE_TEACHER
    role = user.get("role") or ROLE_STUDENT
    return role if role in ROLES else ROLE_STUDENT


def has_role(user_id: Optional[str], *roles: str) -> bool:
    return get_role(user_id) in roles


def grant_role(user_id: str, role: str, *, reason: str = "admin") -> Dict[str, Any]:
    """
    授予角色（只升不降，防止误把教师降级导致课程"无主"）。

    **调用者必须先完成授权校验**（管理员 / 有效教师邀请码），本函数只负责落库：
    - 授予原因写入 role_granted_at + role_source，便于审计
    - admin 不通过本函数授予（只来自 ADMIN_USERS 白名单）

    role_source 取值：`env`（环境变量白名单）/ `invite`（教师邀请码）/ `admin`（管理员操作）
    """
    if role not in (ROLE_STUDENT, ROLE_TEACHER):
        raise AuthError("不支持的角色", "invalid_role")
    if not get_user(user_id):
        raise AuthError("用户不存在", "not_found")
    current = get_role(user_id)
    if current == ROLE_ADMIN:
        return get_user(user_id)
    if current == ROLE_TEACHER and role == ROLE_STUDENT:
        return get_user(user_id)  # 不降级
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE users SET role=?, role_granted_at=?, role_source=?, updated_at=? WHERE id=?",
            (role, _now(), reason, _now(), user_id))
        conn.commit()
    finally:
        conn.close()
    logger.info(f"授予角色: user={user_id} role={role} source={reason}")
    user = get_user(user_id)
    return user or {}


# ==================== 教师开通（受控流程，2026-09-27） ====================
#
# 背景（修复）：此前"创建课程"会自动把创建者提升为 teacher，
# 意味着**任何注册用户都能自助获得教师权限**（实测可拿到教师工作台、建班级、
# 访问教师端全部能力）。这不是"教师端设计"，而是一个提权漏洞。
#
# 现在的开通方式（三选一，都必须由既有权威方发起）：
#   1) 环境变量白名单 `TEACHER_USERS`（逗号分隔用户名）—— 部署时的初始教师
#   2) 管理员（`ADMIN_USERS` 白名单）把某个账号开通为教师
#   3) **教师邀请码**：管理员或已是教师的人生成一次性邀请码，用户凭码自助开通
# 普通学生**不再**因为创建课程而获得教师身份。

TEACHER_INVITE_TTL_DAYS = int(os.getenv("TEACHER_INVITE_TTL_DAYS", "30"))

# ---- 邀请码签发政策（2026-09-27 收紧，防止教师身份无控制扩散）----
# 默认**只有管理员**可以签发教师邀请码；教师签发必须显式开启，且受配额与上限约束。
TEACHER_CAN_INVITE = (os.getenv("TEACHER_CAN_INVITE", "0") or "0").strip() not in (
    "0", "false", "False", "")
# 教师签发的邀请码：有效期上限（天）与可用次数上限
TEACHER_ISSUE_MAX_TTL_DAYS = int(os.getenv("TEACHER_ISSUE_MAX_TTL_DAYS", "7"))
TEACHER_ISSUE_MAX_USES = int(os.getenv("TEACHER_ISSUE_MAX_USES", "1"))
# 教师可同时持有的"未用完且未撤销"邀请码数量上限
TEACHER_ISSUE_QUOTA = int(os.getenv("TEACHER_ISSUE_QUOTA", "2"))
# 管理员签发的硬上限（无论谁签发都不能突破）
ADMIN_ISSUE_MAX_TTL_DAYS = 365
ADMIN_ISSUE_MAX_USES = 200


def _teacher_users_from_env() -> set:
    raw = (os.getenv("TEACHER_USERS") or "").strip()
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def _ensure_invite_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS role_invites (
        code TEXT PRIMARY KEY,
        role TEXT NOT NULL DEFAULT 'teacher',
        created_by TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        max_uses INTEGER DEFAULT 1,
        used_count INTEGER DEFAULT 0,
        revoked_at TEXT,
        note TEXT
    );
    CREATE TABLE IF NOT EXISTS role_invite_uses (
        id TEXT PRIMARY KEY,
        code TEXT NOT NULL,
        user_id TEXT NOT NULL,
        used_at TEXT NOT NULL,
        UNIQUE(code, user_id)
    );
    """)
    conn.commit()


def invite_policy(issuer_user_id: str) -> Dict[str, Any]:
    """
    当前签发者能签发的邀请码上限（供接口展示与校验，程序判定）

    默认策略：只有管理员能签发；教师签发需 `TEACHER_CAN_INVITE=1`，
    且有效期 ≤ TEACHER_ISSUE_MAX_TTL_DAYS、次数 ≤ TEACHER_ISSUE_MAX_USES、
    未用尽的邀请码 ≤ TEACHER_ISSUE_QUOTA 个。
    """
    admin = is_admin(issuer_user_id)
    return {
        "is_admin": admin,
        "can_issue": bool(admin or TEACHER_CAN_INVITE),
        "max_ttl_days": (ADMIN_ISSUE_MAX_TTL_DAYS if admin
                         else min(TEACHER_ISSUE_MAX_TTL_DAYS, ADMIN_ISSUE_MAX_TTL_DAYS)),
        "max_uses": ADMIN_ISSUE_MAX_USES if admin else min(TEACHER_ISSUE_MAX_USES,
                                                           ADMIN_ISSUE_MAX_USES),
        "quota": None if admin else TEACHER_ISSUE_QUOTA,
        "reason": ("管理员" if admin else
                   ("教师可签发（已开启 TEACHER_CAN_INVITE）" if TEACHER_CAN_INVITE
                    else "默认仅管理员可签发教师邀请码")),
    }


def _active_invites_by(created_by: str) -> int:
    """该签发者名下"未用完且未撤销且未过期"的邀请码数量"""
    conn = _get_db()
    try:
        _ensure_invite_table(conn)
        rows = conn.execute(
            "SELECT * FROM role_invites WHERE created_by=? AND revoked_at IS NULL",
            (created_by,)).fetchall()
    finally:
        conn.close()
    n = 0
    for r in rows:
        d = dict(r)
        if int(d.get("used_count") or 0) < int(d.get("max_uses") or 1):
            expires = d.get("expires_at")
            if expires:
                try:
                    if datetime.fromisoformat(expires) < datetime.now():
                        continue
                except ValueError:
                    pass
            n += 1
    return n


def create_role_invite(created_by: str, *, role: str = ROLE_TEACHER, ttl_days: Optional[int] = None,
                       max_uses: int = 1, note: Optional[str] = None) -> Dict[str, Any]:
    """
    生成教师开通邀请码

    **权限与配额在这里强制**（不依赖路由层）：
    - 默认仅管理员可签发；教师签发需显式开启 `TEACHER_CAN_INVITE=1`
    - 教师签发受有效期/次数/配额上限约束（见 invite_policy）
    - 管理员签发的硬上限为 365 天 / 200 次
    """
    import secrets as _secrets
    if role != ROLE_TEACHER:
        raise AuthError("目前只支持生成教师邀请码", "invalid_role")

    policy = invite_policy(created_by)
    if not policy["can_issue"]:
        raise AuthError(
            "教师邀请码默认仅管理员可签发。如需允许教师签发，"
            "请由管理员配置环境变量 TEACHER_CAN_INVITE=1（并注意配额）。",
            "forbidden")

    # 未显式指定有效期时，取"全局默认"与"当前身份上限"的较小值：
    # 教师身份的默认有效期（全局 30 天）若高于其上限，应当按上限收紧，而不是报错。
    ttl = (min(TEACHER_INVITE_TTL_DAYS, policy["max_ttl_days"])
           if ttl_days is None else int(ttl_days))
    if ttl <= 0 or ttl > ADMIN_ISSUE_MAX_TTL_DAYS:
        raise AuthError(f"邀请码有效期应在 1-{ADMIN_ISSUE_MAX_TTL_DAYS} 天之间",
                        "invalid_request")
    if ttl > policy["max_ttl_days"]:
        raise AuthError(
            f"当前身份可签发的有效期上限为 {policy['max_ttl_days']} 天"
            f"（你请求了 {ttl} 天）", "forbidden")
    if max_uses < 1 or max_uses > ADMIN_ISSUE_MAX_USES:
        raise AuthError(f"邀请码可用次数应在 1-{ADMIN_ISSUE_MAX_USES} 之间",
                        "invalid_request")
    if max_uses > policy["max_uses"]:
        raise AuthError(
            f"当前身份可签发的可用次数上限为 {policy['max_uses']}（你请求了 {max_uses}）",
            "forbidden")
    if policy["quota"] is not None:
        active = _active_invites_by(created_by)
        if active >= policy["quota"]:
            raise AuthError(
                f"你名下还有 {active} 个未使用的邀请码（上限 {policy['quota']} 个）。"
                "请先等对方使用或撤销后再签发；大额需求请联系管理员。",
                "quota_exceeded")
    code = "T-" + _secrets.token_hex(4).upper()  # 形如 T-1A2B3C4D，便于区分学生班级码
    now = datetime.now()
    conn = _get_db()
    try:
        _ensure_invite_table(conn)
        conn.execute(
            """INSERT INTO role_invites (code, role, created_by, created_at, expires_at,
                                         max_uses, used_count, note)
               VALUES (?,?,?,?,?,?,0,?)""",
            (code, role, created_by, now.isoformat(timespec="seconds"),
             (now + timedelta(days=ttl)).isoformat(timespec="seconds"), max_uses, note))
        conn.commit()
    finally:
        conn.close()
    logger.info(f"生成教师邀请码: code={code} by={created_by} ttl={ttl}天 uses={max_uses}")
    return {"code": code, "role": role, "expires_at":
            (now + timedelta(days=ttl)).isoformat(timespec="seconds"),
            "max_uses": max_uses, "used_count": 0, "note": note}


def get_role_invite(code: str) -> Optional[Dict[str, Any]]:
    if not code:
        return None
    conn = _get_db()
    try:
        _ensure_invite_table(conn)
        row = conn.execute("SELECT * FROM role_invites WHERE code=?",
                           (code.strip().upper(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_role_invites(limit: int = 50, actor_user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    邀请码列表：管理员看全部；其他账号只看**自己签发的**
    （避免教师互相看到/撤销对方发出的邀请码）
    """
    conn = _get_db()
    try:
        _ensure_invite_table(conn)
        if actor_user_id and not is_admin(actor_user_id):
            rows = conn.execute(
                "SELECT * FROM role_invites WHERE created_by=? ORDER BY created_at DESC "
                "LIMIT ?", (actor_user_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM role_invites ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def revoke_role_invite(code: str, actor_user_id: Optional[str] = None) -> Dict[str, Any]:
    """撤销邀请码：管理员可撤销任意一个；其他人只能撤销**自己签发的**"""
    conn = _get_db()
    try:
        _ensure_invite_table(conn)
        row = conn.execute("SELECT * FROM role_invites WHERE code=?",
                           (code.strip().upper(),)).fetchone()
        if not row:
            raise AuthError("邀请码不存在", "not_found")
        if (actor_user_id and not is_admin(actor_user_id)
                and row["created_by"] != actor_user_id):
            raise AuthError("只能撤销自己签发的邀请码（如需撤销他人的请联系管理员）",
                            "forbidden")
        conn.execute("UPDATE role_invites SET revoked_at=? WHERE code=?",
                     (_now(), row["code"]))
        conn.commit()
        return dict(conn.execute("SELECT * FROM role_invites WHERE code=?",
                                 (row["code"],)).fetchone())
    finally:
        conn.close()


def invite_invalid_reason(invite: Optional[Dict[str, Any]], user_id: str) -> Optional[str]:
    """返回邀请码不可用的原因；None 表示可用（程序判定，不依赖前端）"""
    if not invite:
        return "邀请码不存在"
    if invite.get("role") != ROLE_TEACHER:
        return "邀请码类型不正确"
    if invite.get("revoked_at"):
        return "邀请码已被撤销"
    expires_at = invite.get("expires_at")
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at) < datetime.now():
                return "邀请码已过期"
        except ValueError:
            return "邀请码有效期数据异常"
    # 先判断"本人是否用过"：同一用户重复提交时给出更准确的原因
    # （否则会笼统地报"次数已用完"，用户不知道是自己已经用过了）
    conn = _get_db()
    try:
        _ensure_invite_table(conn)
        row = conn.execute("SELECT 1 FROM role_invite_uses WHERE code=? AND user_id=?",
                           (invite["code"], user_id)).fetchone()
        if row:
            return "你已经使用过这个邀请码"
    finally:
        conn.close()
    if int(invite.get("used_count") or 0) >= int(invite.get("max_uses") or 1):
        return "邀请码可用次数已用完"
    return None


def redeem_role_invite(code: str, user_id: str) -> Dict[str, Any]:
    """
    用教师邀请码开通教师身份（一次性/限量、可过期、可撤销）

    返回：{ok, role, reason?}
    """
    invite = get_role_invite(code)
    reason = invite_invalid_reason(invite, user_id)
    if reason:
        return {"ok": False, "reason": reason}
    conn = _get_db()
    try:
        _ensure_invite_table(conn)
        conn.execute(
            "INSERT INTO role_invite_uses (id, code, user_id, used_at) VALUES (?,?,?,?)",
            (f"riu_{uuid.uuid4().hex[:10]}", invite["code"], user_id, _now()))
        conn.execute(
            "UPDATE role_invites SET used_count = used_count + 1 WHERE code=?",
            (invite["code"],))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return {"ok": False, "reason": "你已经使用过这个邀请码"}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    user = grant_role(user_id, ROLE_TEACHER, reason="invite")
    logger.info(f"教师邀请码已使用: code={invite['code']} user={user_id}")
    return {"ok": True, "role": ROLE_TEACHER, "invite_code": invite["code"],
            "user": user}


def teacher_grant_source(user_id: str) -> str:
    """
    教师身份的来源（用于展示"你是怎么成为教师的"）：
    env / invite / admin / course(历史遗留) / none

    判定依据是**生效角色**（get_role），否则环境白名单开通的教师会被误报成 none。
    """
    if is_admin(user_id):
        return "admin"
    if get_role(user_id) != ROLE_TEACHER:
        return "none"
    user = get_user(user_id) or {}
    if (user.get("username") or "").lower() in _teacher_users_from_env():
        return "env"
    return user.get("role_source") or "unknown"


def _init_db() -> None:
    conn = _get_db()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            is_legacy INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked INTEGER DEFAULT 0,
            last_seen_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        """)
        conn.commit()
        # 教师端（2026-09-26）：用户角色。
        # 默认 'student'：**不把任何历史账号/新账号静默提升为教师或管理员**，
        # 教师身份只能由受控通道开通（见"教师开通"一节），默认 student。
        _ensure_role_column(conn)
    finally:
        conn.close()


def _ensure_role_column(conn: sqlite3.Connection) -> None:
    """幂等补 role/role_granted_at 列（老库与新库得到同一结构）"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "role" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'student'")
    if "role_granted_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN role_granted_at TEXT")
    if "role_source" not in cols:
        # 教师身份来源：env / invite / admin / course(历史遗留，建课时自动授予的那批)
        conn.execute("ALTER TABLE users ADD COLUMN role_source TEXT")
    conn.commit()


_init_db()


# ==================== 口令与令牌 ====================

def _hash_password(password: str, salt: Optional[str] = None) -> tuple:
    if not salt:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt.encode("utf-8"), _PBKDF2_ITERATIONS)
    return dk.hex(), salt


def _verify_password(password: str, expected_hash: str, salt: str) -> bool:
    try:
        candidate, _ = _hash_password(password, salt)
        return hmac.compare_digest(candidate, expected_hash)
    except Exception:
        return False


# ==================== 用户 ====================

def _row_to_user(row, include_secret: bool = False) -> Dict[str, Any]:
    keys = row.keys()
    user = {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"] or row["username"],
        "is_legacy": bool(row["is_legacy"]),
        "status": row["status"],
        "created_at": row["created_at"],
        # 角色：由后端判定，前端仅用于展示（权限一律在服务端校验）
        "role": (row["role"] if "role" in keys and row["role"] else ROLE_STUDENT),
        "role_granted_at": row["role_granted_at"] if "role_granted_at" in keys else None,
        "role_source": row["role_source"] if "role_source" in keys else None,
    }
    if include_secret:
        user["password_hash"] = row["password_hash"]
        user["password_salt"] = row["password_salt"]
    return user


def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return _row_to_user(row) if row else None
    finally:
        conn.close()


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    if not username:
        return None
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return _row_to_user(row, include_secret=True) if row else None
    finally:
        conn.close()


def register_user(username: str, password: str, display_name: Optional[str] = None) -> Dict[str, Any]:
    """注册新用户（用户名为唯一标识）"""
    username = (username or "").strip()
    if not _USERNAME_RE.match(username):
        raise AuthError("用户名需为 3-32 位字母/数字/下划线/中文/点/连字符", "invalid_request")
    if not password or len(password) < 6:
        raise AuthError("口令至少 6 位", "invalid_request")
    if username == LEGACY_USERNAME:
        raise AuthError("该用户名为历史数据保留账号，请使用其他用户名", "invalid_request")

    pwd_hash, salt = _hash_password(password)
    now = _now()
    uid = f"user_{uuid.uuid4().hex[:12]}"
    conn = _get_db()
    try:
        try:
            conn.execute(
                """INSERT INTO users
                   (id, username, display_name, password_hash, password_salt,
                    is_legacy, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,0,'active',?,?)""",
                (uid, username, (display_name or username), pwd_hash, salt, now, now))
            conn.commit()
        except sqlite3.IntegrityError:
            raise AuthError("用户名已存在", "conflict")
        return {"id": uid, "username": username,
                "display_name": display_name or username, "is_legacy": False}
    finally:
        conn.close()


def authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    """校验用户名口令，成功返回用户信息，失败返回 None"""
    user = get_user_by_username(username)
    if not user:
        return None
    if user.get("status") != "active":
        return None
    if not _verify_password(password, user["password_hash"], user["password_salt"]):
        return None
    return {k: v for k, v in user.items() if k not in ("password_hash", "password_salt")}


def create_session(user_id: str, ttl_hours: Optional[int] = None) -> Dict[str, Any]:
    """创建会话令牌"""
    if not get_user(user_id):
        raise AuthError("用户不存在", "not_found")
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    expires = now + timedelta(hours=ttl_hours or SESSION_TTL_HOURS)
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at, revoked) VALUES (?,?,?,?,0)",
            (token, user_id, now.isoformat(timespec="seconds"),
             expires.isoformat(timespec="seconds")))
        conn.commit()
        return {"token": token, "expires_at": expires.isoformat(timespec="seconds")}
    finally:
        conn.close()


def get_user_id_by_token(token: str) -> Optional[str]:
    """按令牌取用户 ID（校验有效期与撤销状态）"""
    if not token:
        return None
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
        if not row or row["revoked"]:
            return None
        try:
            if datetime.fromisoformat(row["expires_at"]) < datetime.now():
                return None
        except ValueError:
            return None
        conn.execute("UPDATE sessions SET last_seen_at=? WHERE token=?", (_now(), token))
        conn.commit()
        return row["user_id"]
    finally:
        conn.close()


def revoke_session(token: str) -> bool:
    """撤销会话令牌"""
    if not token:
        return False
    conn = _get_db()
    try:
        cur = conn.execute("UPDATE sessions SET revoked=1 WHERE token=?", (token,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ==================== 历史数据认领 ====================

def list_legacy_claims() -> list:
    """列出历史 default_user 数据待认领清单"""
    from services.learning_data import _get_db as _loop_db
    conn = _loop_db()
    try:
        rows = conn.execute("SELECT * FROM legacy_data_claims ORDER BY data_scope").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def claim_legacy_data(user_id: str) -> Dict[str, Any]:
    """
    显式认领历史 default_user 数据。

    规则：
    - 仅当尚无其他用户认领时可认领（幂等：本人重复认领返回已认领）
    - 认领前备份涉及的业务数据库
    - 归属改写 default_user → 认领者；原数据不删除

    Returns:
        {"claimed": bool, "moved": {...}, "backup": path}
    """
    from services.learning_data import _get_db as _loop_db

    user = get_user(user_id)
    if not user:
        raise AuthError("用户不存在", "not_found")
    if user["username"] == LEGACY_USERNAME:
        raise AuthError("历史保留账号本身即数据归属，无需认领", "invalid_request")

    loop = _loop_db()
    try:
        rows = loop.execute(
            "SELECT * FROM legacy_data_claims WHERE owner_label=?", (LEGACY_OWNER,)).fetchall()
        if not rows:
            return {"claimed": False, "moved": {}, "backup": None,
                    "message": "没有可认领的历史数据"}
        claimed_by = {r["claimed_by"] for r in rows if r["status"] == "CLAIMED"}
        if claimed_by and user_id not in claimed_by:
            raise AuthError("历史数据已被其他账号认领", "conflict")
        if claimed_by and user_id in claimed_by:
            return {"claimed": True, "moved": {}, "backup": None, "message": "您已认领过历史数据"}
    finally:
        loop.close()

    # 备份
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(_DATA_DIR, "backups", f"claim-{stamp}")
    os.makedirs(backup_dir, exist_ok=True)
    for src in _BUSINESS_DBS:
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(backup_dir, os.path.basename(src)))

    moved: Dict[str, int] = {}

    def _reassign(db_path: str, table: str) -> None:
        if not os.path.exists(db_path):
            return
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(
                f"UPDATE {table} SET user_id=? WHERE user_id=?", (user_id, LEGACY_OWNER))
            conn.commit()
            if cur.rowcount:
                moved[f"{os.path.basename(db_path)}:{table}"] = cur.rowcount
        except sqlite3.Error as e:
            logger.warning(f"认领改写失败 {table}: {e}")
        finally:
            conn.close()

    _reassign(os.path.join(_DATA_DIR, "learning.db"), "learning_plans")
    _reassign(os.path.join(_DATA_DIR, "learning.db"), "learning_tasks")
    _reassign(os.path.join(_DATA_DIR, "chat.db"), "conversations")
    for table in ("user_profiles", "user_interactions", "user_feedback",
                  "knowledge_mastery", "learning_preferences"):
        _reassign(os.path.join(_DATA_DIR, "user_agent.db"), table)

    loop = _loop_db()
    try:
        loop.execute(
            "UPDATE legacy_data_claims SET status='CLAIMED', claimed_by=?, claimed_at=? "
            "WHERE owner_label=?",
            (user_id, _now(), LEGACY_OWNER))
        loop.commit()
    finally:
        loop.close()

    logger.info(f"历史数据认领完成: user={user_id}, moved={moved}")
    return {"claimed": True, "moved": moved, "backup": backup_dir,
            "message": "历史数据已认领至当前账号"}


def _seed_legacy_account() -> None:
    """
    确保存在历史保留账号（用于承接 default_user 历史数据）。

    口令来自 LEGACY_DEMO_PASSWORD（本地演示用），首次创建后不覆盖已有口令。
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE username=?", (LEGACY_USERNAME,)).fetchone()
        if row:
            return
        pwd_hash, salt = _hash_password(LEGACY_DEMO_PASSWORD)
        now = _now()
        conn.execute(
            """INSERT INTO users
               (id, username, display_name, password_hash, password_salt,
                is_legacy, status, created_at, updated_at)
               VALUES (?,?,?,?,?,1,'active',?,?)""",
            (LEGACY_USERNAME, LEGACY_USERNAME, "历史数据账号", pwd_hash, salt, now, now))
        conn.commit()
        logger.info("已创建历史保留账号 default_user（供历史数据归属与认领）")
    finally:
        conn.close()


_seed_legacy_account()


__all__ = [
    "AuthError",
    "DB_PATH",
    "LEGACY_USERNAME",
    "LEGACY_DEMO_PASSWORD",
    "register_user",
    "authenticate",
    "create_session",
    "get_user_id_by_token",
    "revoke_session",
    "get_user",
    "get_user_by_username",
    "list_legacy_claims",
    "claim_legacy_data",
]
