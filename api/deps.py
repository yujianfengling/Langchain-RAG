# api/deps.py
# 统一身份依赖（阶段一）
#
# 学习数据接口必须从后端统一机制取 user_id，不信任请求体/路径随意传入的 user_id。

from typing import Optional
from fastapi import Depends, Header, HTTPException

from services import auth_service

# 未登录时的历史保留账号（兼容既有单用户流程；数据可被新账号显式认领）
LEGACY_USER = auth_service.LEGACY_USERNAME


def _extract_token(authorization: Optional[str], x_user_token: Optional[str]) -> Optional[str]:
    if x_user_token:
        return x_user_token.strip()
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return authorization.strip()
    return None


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
    x_user_token: Optional[str] = Header(default=None),
) -> str:
    """
    解析当前用户：带令牌则校验并返回其 user_id；未带令牌回退历史账号。

    用于需要兼容既有流程、但归属必须可信的接口。
    """
    token = _extract_token(authorization, x_user_token)
    if token:
        user_id = auth_service.get_user_id_by_token(token)
        if not user_id:
            raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录")
        return user_id
    return LEGACY_USER


async def require_current_user(
    authorization: Optional[str] = Header(default=None),
    x_user_token: Optional[str] = Header(default=None),
) -> str:
    """必须登录（个人数据写入类新接口使用）"""
    token = _extract_token(authorization, x_user_token)
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    user_id = auth_service.get_user_id_by_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录")
    return user_id


def assert_owner(current_user: str, target_user: Optional[str]) -> None:
    """校验资源归属，防止越权访问其他用户数据"""
    if target_user and target_user != current_user:
        raise HTTPException(status_code=403, detail="无权访问其他用户的数据")


# 路径中表示"当前用户"的保留字（前端统一使用 me）
SELF_ALIASES = ("me", "self", "current")


def knowledge_admins() -> set:
    """
    课程内容（知识库/学科）管理白名单，来自环境变量 KNOWLEDGE_ADMIN_USERS。

    课程资料属于**课程公共资源**，与个人学习数据（作答/错题/计划/会话）区分：
    - 个人数据：对象级归属校验，只有本人可读写
    - 课程资料：所有登录用户可读（含受保护文件的下载），
      但只有白名单内的账号可新增/删除/重建

    未配置白名单时退化为"任一登录用户可管理"（单用户/开发模式），
    并在启动时打印告警，便于部署时收紧。
    """
    import os
    raw = os.getenv("KNOWLEDGE_ADMIN_USERS", "") or ""
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


async def require_knowledge_admin(user_id: str = Depends(require_current_user)) -> str:
    """要求登录，且（若已配置白名单）具备课程内容管理权限"""
    admins = knowledge_admins()
    if not admins:
        return user_id
    user = auth_service.get_user(user_id) or {}
    if (user.get("username") or "").lower() in admins:
        return user_id
    raise HTTPException(status_code=403, detail="无课程内容管理权限")


if not knowledge_admins():
    import logging as _logging
    _logging.getLogger("teaching_assistant").warning(
        "未配置 KNOWLEDGE_ADMIN_USERS：任何登录用户都可管理**历史全局学科**（知识库/学科）。"
        "注意：属于某门课程的学科不受此白名单影响，一律只允许授课教师修改。")


# ==================== 课程归属守卫（教师端，2026-09-26） ====================
#
# 背景：历史学科（data/subjects.json 里没有归属记录的）保持既有行为；
# 一旦某个学科被课程登记（teaching.db: subject_owners），它就是**课程资源**：
#   - 读：授课教师 / 该课程在班学生 / 管理员
#   - 写：仅授课教师 / 管理员（**不再受"未配置白名单即人人可写"的影响**）
# 这样旧接口不会被用来绕过教师权限。

def _subject_guard_enabled() -> bool:
    """开关：默认开启；设为 0 可临时关闭（仅用于排障，会在启动时告警）"""
    import os
    return (os.getenv("TEACHING_SCOPE_GUARD", "1") or "1").strip() not in ("0", "false", "False")


async def require_subject_read(subject_id: str,
                               user_id: str = Depends(require_current_user)) -> str:
    """学科读权限（课程学科 → 教师/在班学生/管理员；历史学科 → 登录即可读）"""
    if not _subject_guard_enabled():
        return user_id
    from services import teaching_access
    from services.teaching_data import TeachingError
    try:
        teaching_access.require_subject_read(user_id, subject_id)
    except TeachingError as e:
        raise HTTPException(status_code=403, detail=e.message)
    return user_id


async def require_subject_write(subject_id: str,
                                user_id: str = Depends(require_current_user)) -> str:
    """
    学科写权限（上传/删除/重建/建图谱）

    课程学科：仅授课教师或管理员；历史学科：回退既有 knowledge admin 白名单。
    """
    if not _subject_guard_enabled():
        return await require_knowledge_admin(user_id)
    from services import teaching_access
    from services.teaching_data import TeachingError
    try:
        handled = teaching_access.require_subject_write(user_id, subject_id)
    except TeachingError as e:
        raise HTTPException(status_code=403, detail=e.message)
    if handled:
        return user_id
    return await require_knowledge_admin(user_id)


async def require_admin(user_id: str = Depends(require_current_user)) -> str:
    """
    管理员（`ADMIN_USERS` 环境变量白名单）

    与 `require_knowledge_admin` 的区别：后者在**未配置**白名单时退化为"任一登录用户"，
    用于兼容历史全局学科的既有流程；管理员身份**不退化**——未配置就没有管理员。
    教师开通这类敏感操作只认 require_admin。
    """
    if not auth_service.is_admin(user_id):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user_id


async def require_teacher(user_id: str = Depends(require_current_user)) -> str:
    """教师或管理员（教师端接口统一用它做入口校验）"""
    role = auth_service.get_role(user_id)
    if role not in (auth_service.ROLE_TEACHER, auth_service.ROLE_ADMIN):
        raise HTTPException(
            status_code=403,
            detail="需要教师身份。请向管理员或已有教师索取教师邀请码，在设置页开通。")
    return user_id


def resolve_user_scope(current_user: str, path_user: Optional[str]) -> str:
    """
    解析"用户域"接口（路径中含 {user_id}）的目标用户。

    历史遗留接口把 user_id 放在路径里（无身份机制时期的产物）。阶段一统一为：
    - 路径参数为空 / 为 me|self|current / 等于当前用户 / 为历史占位账号（default_user）
      → 返回当前用户
    - 路径参数指向其他真实账号 → 403，不越权

    数据一律以返回值为准，路径参数不再决定读取谁的数据。
    """
    normalized = (path_user or "").strip().lower()
    if (not normalized or normalized in SELF_ALIASES
            or path_user == current_user or normalized == LEGACY_USER):
        return current_user
    raise HTTPException(status_code=403, detail="无权访问其他用户的数据")


__all__ = [
    "LEGACY_USER",
    "SELF_ALIASES",
    "get_current_user",
    "require_current_user",
    "require_knowledge_admin",
    "require_admin",
    "require_teacher",
    "knowledge_admins",
    "assert_owner",
    "resolve_user_scope",
]
