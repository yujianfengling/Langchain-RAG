# api/routes/agent_run.py
# 学习 Agent 运行接口（阶段二）
#
# 特点：
# - 工具只使用后端当前用户身份（不从请求体取 user_id）
# - 提供 SSE 进度流（"正在查看学习记录 / 正在安排复习…"），支持取消
# - 运行记录可查询（状态、工具调用与结果摘要；不含模型内部思维过程）
# - 模型不可用时：/agent/run 会返回明确错误，但计划、错题、判分等基础功能
#   不依赖本接口，仍然可用（见 degraded 说明）

import asyncio
import json
import threading
from typing import Optional
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.deps import require_current_user, require_subject_read
from api.schemas.response_schema import SuccessResponse
from services import agent_runtime
from services.agent_runtime import AgentRuntimeError

router = APIRouter()


class RunRequest(BaseModel):
    subject_id: str
    goal: str
    intent: Optional[str] = "study"     # study | plan | review
    model: Optional[str] = None         # 模型（不传则用后端默认模型）


def _error(message: str, status: int = 400, error_type: str = "invalid_request"):
    return JSONResponse(
        status_code=status,
        content={"success": False, "data": None,
                 "message": message, "error_type": error_type},
    )


@router.post("/agent/run")
async def run_agent_api(request: RunRequest,
                        user_id: str = Depends(require_current_user)):
    """
    执行一次学习 Agent（同步等待结果）

    适用场景："我周五要考这一章，每天只有半小时，帮我复习。"
    """
    if not request.goal or not request.goal.strip():
        return _error("请说明你的学习目标")
    if not request.subject_id:
        return _error("请选择学科")
    await require_subject_read(request.subject_id, user_id)
    try:
        result = await run_in_threadpool(
            agent_runtime.run_agent, user_id, request.subject_id,
            request.goal.strip(), request.intent or "study", None, None,
            request.model)
        return SuccessResponse(data=result, message="学习助手已完成本轮安排")
    except AgentRuntimeError as e:
        return _error(e.message, 400, e.error_type)
    except Exception:
        return _error("学习助手执行失败，请稍后重试或使用手动操作", 500, "runtime_error")


async def _run_stream(user_id: str, subject_id: str, goal: str, intent: str,
                      model: Optional[str] = None):
    """
    以 SSE 推送执行进度（同 Chat 流式的 Queue 模式，避免阻塞事件循环）。

    事件：step（步骤开始/完成/失败/跳过）、done（最终结果）、error
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    stop_event = threading.Event()
    state = {"run_id": None}

    def _put(item) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception:
            pass

    def _progress(evt: dict) -> None:
        if evt.get("run_id"):
            state["run_id"] = evt["run_id"]
        _put({"type": "step", **evt})

    def _worker():
        try:
            result = agent_runtime.run_agent(
                user_id, subject_id, goal, intent,
                cancel_check=lambda: stop_event.is_set() or _cancelled(state["run_id"]),
                progress=_progress, model=model)
            _put({"type": "done", "data": result})
        except Exception as e:
            logger_msg = f"{type(e).__name__}: {e}"
            _put({"type": "error", "message": "学习助手执行失败", "detail": logger_msg})
        finally:
            _put(None)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                # 客户端断开时 stop_event 会在 finally 置位，worker 下一步会取消
                continue
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
    finally:
        stop_event.set()
        thread.join(timeout=3.0)


def _cancelled(run_id: Optional[str]) -> bool:
    """SSE 客户端断开但请求未走取消接口时，也需要让运行停下来"""
    if not run_id:
        return False
    try:
        run = agent_runtime.get_run(run_id)
        return bool(run and run.get("status") == agent_runtime.STATE_CANCELLED)
    except Exception:
        return False


@router.post("/agent/run/stream")
async def run_agent_stream(request: RunRequest,
                           user_id: str = Depends(require_current_user)):
    """
    执行学习 Agent（SSE 进度流）

    学生可以看到"正在查看学习记录 / 正在安排复习…"，断开连接即视为停止。
    """
    if not request.goal or not request.goal.strip():
        return _error("请说明你的学习目标")
    if not request.subject_id:
        return _error("请选择学科")
    await require_subject_read(request.subject_id, user_id)
    return StreamingResponse(
        _run_stream(user_id, request.subject_id, request.goal.strip(),
                    request.intent or "study", request.model),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


@router.get("/agent/runs")
async def list_runs(user_id: str = Depends(require_current_user)):
    """我的学习助手运行记录（状态 + 时间）"""
    data = await run_in_threadpool(agent_runtime.list_runs, user_id)
    return SuccessResponse(data=data, message="获取运行记录成功")


@router.get("/agent/runs/{run_id}")
async def get_run(run_id: str, user_id: str = Depends(require_current_user)):
    """运行详情：状态、工具调用与结果摘要（不含模型内部思维过程）"""
    data = agent_runtime.get_run(run_id, user_id)
    if not data:
        return _error("运行记录不存在", 404, "not_found")
    return SuccessResponse(data=data, message="获取运行详情成功")


@router.post("/agent/runs/{run_id}/cancel")
async def cancel_run(run_id: str, user_id: str = Depends(require_current_user)):
    """取消一次运行（已完成/已取消的运行不可取消）"""
    ok = await run_in_threadpool(agent_runtime.request_cancel, run_id, user_id)
    if not ok:
        return _error("该运行无法取消（可能已结束或不属于当前用户）", 409, "not_cancellable")
    return SuccessResponse(data=agent_runtime.get_run(run_id, user_id),
                           message="已请求取消")


# 供 SSE worker 复用
logger = __import__("logging").getLogger("teaching_assistant")

__all__ = ["router"]
