# api/routes/auth.py
# 最小身份接口（阶段一）

from typing import Optional
from fastapi import APIRouter, Depends, File, Header, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.schemas.response_schema import SuccessResponse
from api.deps import require_current_user
from services import auth_service
from services.auth_service import AuthError

router = APIRouter()


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class ProfileUpdate(BaseModel):
    display_name: str


def _auth_error(e: AuthError):
    status = {
        "invalid_request": 400,
        "conflict": 409,
        "not_found": 404,
    }.get(e.error_type, 400)
    return JSONResponse(
        status_code=status,
        content={"success": False, "data": None,
                 "message": e.message, "error_type": e.error_type},
    )


@router.post("/auth/register")
async def register(request: RegisterRequest):
    """注册新用户"""
    try:
        user = await run_in_threadpool(
            auth_service.register_user, request.username, request.password, request.display_name)
        session = await run_in_threadpool(auth_service.create_session, user["id"])
        effective_role = await run_in_threadpool(auth_service.get_role, user["id"])
        return SuccessResponse(
            data={**user, "role": effective_role, **session}, message="注册成功")
    except AuthError as e:
        return _auth_error(e)


@router.post("/auth/login")
async def login(request: LoginRequest):
    """登录并颁发会话令牌"""
    try:
        user = await run_in_threadpool(
            auth_service.authenticate, request.username, request.password)
        if not user:
            return JSONResponse(
                status_code=401,
                content={"success": False, "data": None,
                         "message": "用户名或口令不正确", "error_type": "invalid_credentials"},
            )
        session = await run_in_threadpool(auth_service.create_session, user["id"])
        # 返回**生效角色**（环境白名单 > 库内角色），与 /auth/me 和权限判定一致：
        # 只回库内 role 会出现"登录显示学生、请求却按教师放行"，导致教师入口不显示。
        effective_role = await run_in_threadpool(auth_service.get_role, user["id"])
        return SuccessResponse(
            data={**user, "role": effective_role, **session}, message="登录成功")
    except AuthError as e:
        return _auth_error(e)


@router.post("/auth/logout")
async def logout(
    authorization: Optional[str] = Header(default=None),
    x_user_token: Optional[str] = Header(default=None),
):
    """退出登录（撤销当前令牌）"""
    token = x_user_token or (authorization.split(None, 1)[1] if authorization and " " in authorization else authorization)
    if token:
        await run_in_threadpool(auth_service.revoke_session, token.strip())
    return SuccessResponse(data=None, message="已退出登录")


@router.get("/auth/me")
async def me(user_id: str = Depends(require_current_user)):
    """
    获取当前登录用户

    role 返回的是**生效角色**（环境白名单 > 库内角色），与后端权限判定完全一致：
    只按库内 role 字段返回会导致"白名单教师界面显示学生、却拥有教师权限"的不一致。
    """
    user = await run_in_threadpool(auth_service.get_user, user_id)
    if not user:
        return JSONResponse(
            status_code=404,
            content={"success": False, "data": None,
                     "message": "用户不存在", "error_type": "not_found"},
        )
    effective_role = await run_in_threadpool(auth_service.get_role, user_id)
    source = await run_in_threadpool(auth_service.teacher_grant_source, user_id)
    return SuccessResponse(data={**user, "role": effective_role, "role_source": source},
                           message="获取当前用户成功")


@router.patch("/auth/me")
async def update_me(body: ProfileUpdate, user_id: str = Depends(require_current_user)):
    """修改展示名。登录名不能在这里改。"""
    try:
        user = await run_in_threadpool(
            auth_service.update_display_name, user_id, body.display_name)
        effective_role = await run_in_threadpool(auth_service.get_role, user_id)
        return SuccessResponse(
            data={**user, "role": effective_role}, message="姓名已更新")
    except AuthError as e:
        return _auth_error(e)


@router.post("/auth/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user_id: str = Depends(require_current_user),
):
    """上传头像。只收图片，服务端会缩小后再保存。"""
    data = await file.read()
    try:
        user = await run_in_threadpool(
            auth_service.save_avatar, user_id, data, file.filename or "")
        effective_role = await run_in_threadpool(auth_service.get_role, user_id)
        return SuccessResponse(
            data={**user, "role": effective_role}, message="头像已更新")
    except AuthError as e:
        return _auth_error(e)


@router.get("/auth/avatar")
async def download_avatar(user_id: str = Depends(require_current_user)):
    """当前用户的头像。没有头像时返回 404，界面回退到姓名首字。"""
    path = await run_in_threadpool(auth_service.avatar_file, user_id)
    if not path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "data": None,
                     "message": "还没有头像", "error_type": "not_found"},
        )
    return FileResponse(path, media_type="image/webp")


@router.get("/auth/legacy-claims")
async def legacy_claims(user_id: str = Depends(require_current_user)):
    """查看历史 default_user 数据待认领清单"""
    claims = await run_in_threadpool(auth_service.list_legacy_claims)
    return SuccessResponse(data=claims, message="获取历史数据清单成功")


@router.post("/auth/claim-legacy")
async def claim_legacy(user_id: str = Depends(require_current_user)):
    """显式认领历史 default_user 数据到当前账号"""
    try:
        result = await run_in_threadpool(auth_service.claim_legacy_data, user_id)
        return SuccessResponse(data=result, message=result.get("message", "认领完成"))
    except AuthError as e:
        return _auth_error(e)
