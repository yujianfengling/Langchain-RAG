# api/routes/memory.py
# 学习记忆接口（阶段二）
#
# 设计原则：
# - 记忆属于个人数据：所有端点只用后端当前用户身份，并校验记忆归属
# - 学生可查看 / 更正 / 删除 / 关闭个性化记忆
# - 区分"学生明确设置"、"系统推断"、"课程资料确认"，并展示来源与依据

from typing import Optional
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.deps import require_current_user
from api.schemas.response_schema import SuccessResponse
from services import learning_memory
from services.learning_memory import MemoryError

router = APIRouter()


class SetMemoryRequest(BaseModel):
    key: str
    value: str
    scope: Optional[str] = None      # 默认 global
    note: Optional[str] = None


class UpdateMemoryRequest(BaseModel):
    value: str
    reason: Optional[str] = None


class StatusRequest(BaseModel):
    status: str                      # active | disabled
    reason: Optional[str] = None


def _memory_error(e: MemoryError):
    status = {"not_found": 404, "forbidden": 403, "invalid_request": 400}.get(
        e.error_type, 500)
    return JSONResponse(
        status_code=status,
        content={"success": False, "data": None,
                 "message": e.message, "error_type": e.error_type},
    )


@router.get("/memory")
async def list_memory(subject_id: Optional[str] = None,
                      include_inactive: bool = False,
                      user_id: str = Depends(require_current_user)):
    """
    查看我的学习记忆（含来源、时间、适用范围、状态与依据）

    - include_inactive=true 时同时返回已关闭/已删除/已失效的记录（便于追溯）
    """
    data = await run_in_threadpool(
        learning_memory.list_memory, user_id, include_inactive, subject_id)
    return SuccessResponse(data=data, message="获取学习记忆成功")


@router.get("/memory/effective")
async def effective_memory(subject_id: Optional[str] = None,
                           user_id: str = Depends(require_current_user)):
    """
    当前**真正参与教学**的记忆（显式偏好覆盖同 key 的推断；过期推断自动失效）

    shadowed 列出被覆盖/被忽略的记录及原因，便于解释。
    """
    data = await run_in_threadpool(
        learning_memory.get_effective_memory, user_id, subject_id)
    return SuccessResponse(data=data, message="获取生效记忆成功")


@router.post("/memory")
async def create_memory(request: SetMemoryRequest,
                        user_id: str = Depends(require_current_user)):
    """
    学生显式设置一条记忆（长期有效，优先于系统推断）
    """
    try:
        data = await run_in_threadpool(
            learning_memory.set_memory, user_id, request.key, request.value,
            learning_memory.MEMORY_PREFERENCE,
            request.scope or learning_memory.SCOPE_GLOBAL,
            None, None, None, None, request.note)
        return SuccessResponse(data=data, message="设置成功")
    except MemoryError as e:
        return _memory_error(e)


@router.put("/memory/{memory_id}")
async def correct_memory(memory_id: str, request: UpdateMemoryRequest,
                         user_id: str = Depends(require_current_user)):
    """更正一条记忆（转为学生显式设置，长期有效）"""
    try:
        data = await run_in_threadpool(
            learning_memory.update_memory, memory_id, user_id,
            request.value, request.reason)
        return SuccessResponse(data=data, message="已更正")
    except MemoryError as e:
        return _memory_error(e)


@router.post("/memory/{memory_id}/status")
async def change_memory_status(memory_id: str, request: StatusRequest,
                               user_id: str = Depends(require_current_user)):
    """关闭 / 重新启用一条记忆（关闭后不再参与教学，记录保留）"""
    try:
        data = await run_in_threadpool(
            learning_memory.set_status, memory_id, user_id,
            request.status, request.reason)
        return SuccessResponse(data=data, message="状态已更新")
    except MemoryError as e:
        return _memory_error(e)


@router.delete("/memory/{memory_id}")
async def remove_memory(memory_id: str, user_id: str = Depends(require_current_user)):
    """删除一条记忆（立即不再参与教学，变更历史保留）"""
    try:
        await run_in_threadpool(learning_memory.delete_memory, memory_id, user_id)
        return SuccessResponse(data=None, message="已删除")
    except MemoryError as e:
        return _memory_error(e)


@router.post("/memory/clear")
async def clear_memory(subject_id: Optional[str] = None,
                       user_id: str = Depends(require_current_user)):
    """关闭全部个性化记忆（可指定学科）"""
    n = await run_in_threadpool(learning_memory.clear_user_memory, user_id, subject_id)
    return SuccessResponse(data={"disabled": n}, message="已关闭个性化记忆")


@router.get("/memory/{memory_id}/history")
async def memory_history(memory_id: str, user_id: str = Depends(require_current_user)):
    """查看某条记忆的变更历史（谁在什么时候改了什么）"""
    data = await run_in_threadpool(
        learning_memory.list_history, memory_id, user_id)
    return SuccessResponse(data=data, message="获取变更历史成功")


@router.post("/memory/infer")
async def refresh_inference(subject_id: str,
                            user_id: str = Depends(require_current_user)):
    """
    根据当前真实学习记录刷新推断类记忆。

    只生成有明确依据（样本量 + 有效期）的判断；不推断性格/能力标签。
    """
    data = await run_in_threadpool(
        learning_memory.infer_from_learning_record, user_id, subject_id)
    return SuccessResponse(data=data, message="已根据学习记录刷新判断")
