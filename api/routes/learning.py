# api/routes/learning.py
# 学习计划接口路由（V2.5 Task D）

from typing import Optional
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.schemas.response_schema import SuccessResponse
from api.exceptions import ValidationError
from services.learning_service import (
    LearningServiceError,
    list_plans,
    get_plan,
    delete_plan,
    update_task_status,
    generate_plan,
)
from services.kb_guard import KBError

router = APIRouter()


class GeneratePlanRequest(BaseModel):
    """生成学习计划请求"""
    subject_id: str
    goal: str
    model: Optional[str] = None


class UpdateTaskStatusRequest(BaseModel):
    """更新任务状态请求"""
    status: str


@router.post("/learning/plans/generate")
async def generate_learning_plan(request: GeneratePlanRequest):
    """
    AI 生成学习计划（保存 LearningPlan + LearningTask）

    - **subject_id**: 学科ID
    - **goal**: 学习目标，如"三天复习完数据结构中的树和图"
    """
    try:
        # V2.5.2：generate_plan 为同步阻塞（RAG + LLM），移出事件循环
        plan = await run_in_threadpool(
            generate_plan,
            subject_id=request.subject_id,
            goal=request.goal,
            model=request.model,
        )
        return SuccessResponse(data=plan, message="学习计划生成成功")
    except KBError as e:
        # 学科不存在 / 知识库未构建 → 409，明确提示，不调 LLM
        return JSONResponse(
            status_code=409,
            content={"success": False, "data": None,
                     "message": e.message, "error_type": e.error_type},
        )
    except LearningServiceError as e:
        return JSONResponse(
            status_code=503 if e.error_type == "model_api_error" else 500,
            content={"success": False, "data": None,
                     "message": e.message, "error_type": e.error_type},
        )


@router.get("/learning/plans")
async def get_learning_plans(subject_id: Optional[str] = None):
    """获取学习计划列表（可按学科过滤）"""
    try:
        plans = list_plans(subject_id=subject_id)
        # 无历史计划 → 200 + []
        return SuccessResponse(data=plans, message="获取学习计划成功")
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": "加载学习计划失败", "error_type": "database_error"},
        )


@router.get("/learning/plans/{plan_id}")
async def get_learning_plan(plan_id: str):
    """获取单个学习计划（含任务列表）"""
    try:
        plan = get_plan(plan_id)
        if not plan:
            return JSONResponse(
                status_code=404,
                content={"success": False, "data": None,
                         "message": "学习计划不存在", "error_type": "invalid_request"},
            )
        return SuccessResponse(data=plan, message="获取学习计划成功")
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": "获取学习计划失败", "error_type": "database_error"},
        )


@router.delete("/learning/plans/{plan_id}")
async def remove_learning_plan(plan_id: str):
    """删除学习计划"""
    try:
        ok = delete_plan(plan_id)
        if not ok:
            return JSONResponse(
                status_code=404,
                content={"success": False, "data": None,
                         "message": "学习计划不存在", "error_type": "invalid_request"},
            )
        return SuccessResponse(data=None, message="学习计划已删除")
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": "删除学习计划失败", "error_type": "database_error"},
        )


@router.put("/learning/tasks/{task_id}/status")
async def change_task_status(task_id: str, request: UpdateTaskStatusRequest):
    """更新任务状态（TODO / IN_PROGRESS / COMPLETED）"""
    try:
        task = update_task_status(task_id, request.status)
        if not task:
            return JSONResponse(
                status_code=404,
                content={"success": False, "data": None,
                         "message": "任务不存在", "error_type": "invalid_request"},
            )
        return SuccessResponse(data=task, message="任务状态已更新")
    except LearningServiceError as e:
        return JSONResponse(
            status_code=400,
            content={"success": False, "data": None,
                     "message": e.message, "error_type": "invalid_request"},
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": "更新任务状态失败", "error_type": "database_error"},
        )
