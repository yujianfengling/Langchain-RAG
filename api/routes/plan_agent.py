# api/routes/plan_agent.py
# 计划约束与调整接口（阶段二）
#
# 说明：时间预算、日期与任务冲突全部由后端程序判定（services/plan_adjustment），
# 调整方案只生成建议，应用（尤其重大调整）需要学生确认；已应用的可以撤销。

from typing import Optional
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.deps import require_current_user
from api.schemas.response_schema import SuccessResponse
from services import plan_adjustment
from services.plan_adjustment import PlanAdjustmentError

router = APIRouter()


class ConstraintsRequest(BaseModel):
    subject_id: str
    deadline: Optional[str] = None          # YYYY-MM-DD
    daily_minutes: Optional[int] = None     # 每天可用时长（分钟）
    teacher_deadline: Optional[str] = None  # 教师/课程固定截止
    auto_adjust: Optional[bool] = None      # 是否授权自动调整


class ProposeRequest(BaseModel):
    subject_id: str
    plan_id: Optional[str] = None
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None   # 相同事件不重复生成建议


class ApplyRequest(BaseModel):
    confirm: bool = False                   # 重大调整需学生确认


def _error(e: PlanAdjustmentError):
    status = {
        "invalid_request": 400,
        "not_found": 404,
        "forbidden": 403,
        "confirmation_required": 409,
        "stale_proposal": 409,      # 约束已变化，旧方案不再可行
        "conflict": 409,
    }.get(e.error_type, 500)
    return JSONResponse(
        status_code=status,
        content={"success": False, "data": None,
                 "message": e.message, "error_type": e.error_type},
    )


@router.get("/plan/constraints")
async def get_constraints(subject_id: str,
                          user_id: str = Depends(require_current_user)):
    """读取计划约束（截止日期 / 每天可用时长 / 教师固定截止 / 自动调整授权）"""
    data = await run_in_threadpool(
        plan_adjustment.get_constraints, user_id, subject_id)
    return SuccessResponse(data=data, message="获取计划约束成功")


@router.put("/plan/constraints")
async def put_constraints(request: ConstraintsRequest,
                          user_id: str = Depends(require_current_user)):
    """设置计划约束（教师固定截止不可被推后）"""
    try:
        data = await run_in_threadpool(
            plan_adjustment.set_constraints, user_id, request.subject_id,
            request.deadline, request.daily_minutes, request.teacher_deadline,
            request.auto_adjust)
        return SuccessResponse(data=data, message="计划约束已保存")
    except PlanAdjustmentError as e:
        return _error(e)


@router.get("/plan/budget")
async def plan_budget(subject_id: str, plan_id: Optional[str] = None,
                      user_id: str = Depends(require_current_user)):
    """
    时间预算与冲突检查（程序判定）

    返回剩余任务/所需时间/可用容量、冲突项与缩小范围建议。
    预算不足时明确给出冲突，而不是生成看起来合理但会超时的安排。
    """
    data = await run_in_threadpool(
        plan_adjustment.analyze_plan_budget, user_id, subject_id, plan_id)
    return SuccessResponse(data=data, message="预算检查完成")


@router.post("/plan/adjustments/propose")
async def propose(request: ProposeRequest,
                  user_id: str = Depends(require_current_user)):
    """
    生成调整建议（只出方案，不改数据）

    - 同 idempotency_key 只生成一次，避免同一事件重复创建建议
    - 重大调整会标记 requires_confirmation=true
    """
    try:
        data = await run_in_threadpool(
            plan_adjustment.propose_schedule, user_id, request.subject_id,
            request.plan_id, "manual", request.idempotency_key, request.reason)
        return SuccessResponse(data=data, message="已生成调整建议")
    except PlanAdjustmentError as e:
        return _error(e)


@router.get("/plan/adjustments")
async def list_adjustments(subject_id: Optional[str] = None,
                           status: Optional[str] = None,
                           user_id: str = Depends(require_current_user)):
    """调整历史与待确认建议（前端据此展示"变更内容+原因+撤销入口"）"""
    data = await run_in_threadpool(
        plan_adjustment.list_adjustments, user_id, subject_id, status)
    return SuccessResponse(data=data, message="获取调整记录成功")


@router.post("/plan/adjustments/{adjustment_id}/apply")
async def apply_adjustment(adjustment_id: str, request: ApplyRequest,
                           user_id: str = Depends(require_current_user)):
    """
    应用调整（会真正修改任务日期）

    - 重大调整必须 confirm=true
    - 已被学生手动改过日期的任务会被跳过（尊重用户设置）
    """
    try:
        data = await run_in_threadpool(
            plan_adjustment.apply_adjustment, adjustment_id, user_id, request.confirm)
        return SuccessResponse(data=data, message="调整已应用")
    except PlanAdjustmentError as e:
        return _error(e)


@router.post("/plan/adjustments/{adjustment_id}/undo")
async def undo_adjustment(adjustment_id: str,
                          user_id: str = Depends(require_current_user)):
    """撤销已应用的调整（任务日期改回原值，记录保留）"""
    try:
        data = await run_in_threadpool(
            plan_adjustment.undo_adjustment, adjustment_id, user_id)
        return SuccessResponse(data=data, message="调整已撤销")
    except PlanAdjustmentError as e:
        return _error(e)


@router.post("/plan/adjustments/{adjustment_id}/reject")
async def reject_adjustment(adjustment_id: str,
                            user_id: str = Depends(require_current_user)):
    """拒绝一条待确认的调整建议"""
    try:
        data = await run_in_threadpool(
            plan_adjustment.reject_adjustment, adjustment_id, user_id)
        return SuccessResponse(data=data, message="已拒绝该建议")
    except PlanAdjustmentError as e:
        return _error(e)
