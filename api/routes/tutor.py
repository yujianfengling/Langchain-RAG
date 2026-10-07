# api/routes/tutor.py
# 统一伴学接口（2026-10-07 统一伴学改造）
#
# 一个入口 + 多种能力：同一学习会话内完成 提问 → 讲解 → 地图 → 节点练习 → 成果保存。
# 会话与轮次的事件全部落库，刷新/重连可用 after_seq 重放，取消有明确状态。

import asyncio
import json
import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from api.deps import require_current_user, require_subject_read
from api.schemas.tutor_schema import (
    ArtifactCreate, ArtifactUpdate, MapGenerateRequest, NodeStatusRequest,
    PracticeAnswerRequest, TutorSessionUpdate, TutorStreamRequest,
)
from services import (artifacts_service, auth_service, knowledge_map, material_scope,
                      tutor_agent, tutor_session)
from utils.logger import logger

router = APIRouter()


# =====================================================================
# 统一运行入口（SSE）
# =====================================================================

def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _tutor_stream_async(request: TutorStreamRequest, user_id: str, role: str):
    """
    同步生成器 → asyncio.Queue → SSE。

    取消传播与既有 chat_stream 一致：客户端断开 → 生成器被取消 → finally 置 stop_event
    → worker 关闭上游生成器，不再产生 token。
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    stop_event = threading.Event()

    def _safe_put(item):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception:
            pass

    def _worker():
        gen = None
        try:
            gen = tutor_agent.stream_turn(
                user_id, request.subject_id, request.message,
                ability=request.ability or tutor_session.ABILITY_CHAT,
                selected_doc_ids=request.doc_ids,
                node_id=request.node_id,
                conversation_id=request.conversation_id,
                model=request.model,
                role=role,
                cancel_check=stop_event.is_set,
            )
            for event in gen:
                if stop_event.is_set():
                    return
                _safe_put(event)
            _safe_put(None)
        except Exception as e:
            logger.error(f"[TutorStream] worker 异常: {type(e).__name__}: {e}", exc_info=True)
            _safe_put({"type": "error", "message": "运行失败，请稍后重试"})
            _safe_put(None)
        finally:
            try:
                if gen is not None:
                    gen.close()
            except Exception:
                pass

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if event is None:
                break
            yield _sse(event)
    finally:
        stop_event.set()
        thread.join(timeout=2.0)


@router.post("/tutor/stream")
async def tutor_stream(request: TutorStreamRequest,
                       user_id: str = Depends(require_current_user)):
    """统一伴学：一轮提问，流式返回阶段、工具调用、引用、成果与最终回答。"""
    if not (request.message or "").strip():
        raise HTTPException(status_code=400, detail="请输入内容")
    await require_subject_read(request.subject_id, user_id)
    role = auth_service.get_role(user_id)
    return StreamingResponse(
        _tutor_stream_async(request, user_id, role),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


# =====================================================================
# 统一上下文与会话
# =====================================================================

@router.get("/tutor/session")
async def get_session(subject_id: str = Query(...),
                      user_id: str = Depends(require_current_user)):
    await require_subject_read(subject_id, user_id)
    context = tutor_session.load_context(user_id, subject_id)
    scope = material_scope.build_scope(
        user_id, subject_id, (context.get("material_scope") or {}).get("selected_doc_ids"))
    materials = material_scope.list_scope_materials(user_id, subject_id)
    return {
        "success": True,
        "data": {
            "context": context,
            "scope": material_scope.scope_to_public(scope),
            "materials": [
                {"doc_id": m["doc_id"], "file_name": m["file_name"], "status": m.get("status"),
                 "source": m.get("source"), "material_id": m.get("material_id")}
                for m in materials
            ],
            "abilities": [
                {"key": tutor_session.ABILITY_CHAT, "label": "资料问答"},
                {"key": tutor_session.ABILITY_EXPLAIN, "label": "引导讲解"},
                {"key": tutor_session.ABILITY_PRACTICE, "label": "练习检测"},
                {"key": tutor_session.ABILITY_MAP, "label": "知识地图"},
                {"key": tutor_session.ABILITY_ORGANIZE, "label": "学习整理"},
            ],
        },
    }


@router.put("/tutor/session")
async def put_session(request: TutorSessionUpdate,
                      user_id: str = Depends(require_current_user)):
    await require_subject_read(request.subject_id, user_id)
    patch = {}
    for field in ("ability", "node_id", "conversation_id", "goal", "deadline", "daily_minutes"):
        value = getattr(request, field)
        if value is not None:
            patch[field] = value
    if request.doc_ids is not None:
        tutor_session.set_material_scope(user_id, request.subject_id, request.doc_ids)
    if patch:
        tutor_session.update_context(user_id, request.subject_id, **patch)
    context = tutor_session.load_context(user_id, request.subject_id)
    scope = material_scope.build_scope(
        user_id, request.subject_id, (context.get("material_scope") or {}).get("selected_doc_ids"))
    return {"success": True, "data": {"context": context,
                                      "scope": material_scope.scope_to_public(scope)}}


@router.get("/tutor/turns")
async def list_turns(subject_id: Optional[str] = None, limit: int = 30,
                     node_id: Optional[str] = None,
                     user_id: str = Depends(require_current_user)):
    if subject_id:
        await require_subject_read(subject_id, user_id)
    # node_id 给定时只返回该知识点的轮次：学生点某个知识点，右侧要显示它以前讲过什么
    return {"success": True,
            "data": {"turns": tutor_session.list_turns(user_id, subject_id, limit,
                                                       node_id=node_id)}}


@router.delete("/tutor/turns/{turn_id}")
async def delete_turn(turn_id: str,
                      user_id: str = Depends(require_current_user)):
    """
    删除一条历史轮次。

    **只删对话历史**；题库里的题目与作答记录不受影响——错题本、复习安排与掌握度
    都由作答记录推导，不能因为清理历史就被抹掉。
    """
    ok = await run_in_threadpool(tutor_session.delete_turn, turn_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在或无权删除")
    return {"success": True, "data": {"deleted": turn_id}}


@router.get("/tutor/turns/{turn_id}")
async def get_turn(turn_id: str, after_seq: int = 0,
                   user_id: str = Depends(require_current_user)):
    turn = tutor_session.get_turn(turn_id, user_id)
    if not turn:
        raise HTTPException(status_code=404, detail="轮次不存在")
    return {"success": True,
            "data": {"turn": turn, "events": tutor_session.list_events(turn_id, after_seq)}}


@router.post("/tutor/turns/{turn_id}/cancel")
async def cancel_turn(turn_id: str, user_id: str = Depends(require_current_user)):
    ok = tutor_session.request_cancel(turn_id, user_id)
    return {"success": ok, "data": {"cancelled": ok}}


# =====================================================================
# 知识地图与节点
# =====================================================================

def _current_scope(user_id: str, subject_id: str, doc_ids):
    return material_scope.build_scope(user_id, subject_id, doc_ids)


@router.get("/tutor/map")
async def get_map(subject_id: str = Query(...),
                  user_id: str = Depends(require_current_user)):
    await require_subject_read(subject_id, user_id)
    context = tutor_session.load_context(user_id, subject_id)
    scope = material_scope.build_scope(
        user_id, subject_id, (context.get("material_scope") or {}).get("selected_doc_ids"))
    data = knowledge_map.map_with_progress(user_id, subject_id)
    stale = bool(data and data.get("source_signature") != scope.get("signature"))
    return {"success": True, "data": {"map": data, "scope": material_scope.scope_to_public(scope),
                                      "stale": stale,
                                      "status_labels": knowledge_map.STATUS_LABELS}}


@router.post("/tutor/map/generate")
async def generate_map(request: MapGenerateRequest,
                       user_id: str = Depends(require_current_user)):
    await require_subject_read(request.subject_id, user_id)
    scope = _current_scope(user_id, request.subject_id, request.doc_ids)
    subject_name = ""
    try:
        from services import subject_service
        subject_name = (subject_service.get_subject(request.subject_id) or {}).get("name") or ""
    except Exception:
        subject_name = ""
    data = await run_in_threadpool(
        knowledge_map.generate_map, user_id, request.subject_id, scope,
        subject_name, request.model, request.force)
    return {"success": True, "data": {"map": data, "scope": material_scope.scope_to_public(scope)}}


@router.get("/tutor/map/node/{node_id}")
async def get_map_node(node_id: str, subject_id: str = Query(...),
                       user_id: str = Depends(require_current_user)):
    await require_subject_read(subject_id, user_id)
    node = knowledge_map.get_node(subject_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="知识点不存在，地图可能已重建")
    progress = knowledge_map.list_progress(user_id, subject_id)
    artifacts = artifacts_service.list_artifacts(user_id, subject_id, None, None, limit=100)
    node["status"] = progress.get(node_id, {}).get("status", knowledge_map.STATUS_NEW)
    node["status_label"] = knowledge_map.STATUS_LABELS.get(node["status"], node["status"])
    return {"success": True, "data": {
        "node": node,
        "artifacts": [a for a in artifacts if a.get("node_id") == node_id],
        "status_labels": knowledge_map.STATUS_LABELS,
    }}


@router.post("/tutor/map/node/status")
async def set_node_status(request: NodeStatusRequest,
                          user_id: str = Depends(require_current_user)):
    await require_subject_read(request.subject_id, user_id)
    # 权威规则：passed 只能由检测结果推出，不接受直接设置。
    #
    # 例外（2026-10-03）：**学生自己上传资料的个人学科**属于自学场景，
    # 由学生自己判断"我已通过"是合理的；而老师提供的课程资料，
    # 自己声明通过就不算数了——成绩与掌握度必须可信。
    if request.status == knowledge_map.STATUS_PASSED:
        from services import teaching_access
        scope = teaching_access.subject_scope(request.subject_id)
        kind = scope.get("scope")
        # 本人创建的个人学科
        self_owned = (kind == "personal" and scope.get("owner_user_id") == user_id)
        # 没有被任何课程认领的学科（legacy）：从学生视角就是"自己整理的知识库"，
        # 没有老师参与，自己确认学会了是合理的。
        # 注意：这里曾只看 personal，导致「材料力学（个人资料）」这类
        # 无归属记录的学科被误判成老师提供的资料而拒绝（2026-10-03 修正）。
        unsupervised = (kind == "legacy")
        if not (self_owned or unsupervised):
            raise HTTPException(
                status_code=400,
                detail="这门课的资料由老师提供，是否通过要以检测结果为准，不能自己标记。"
                       "（只有你自己整理、没有绑定课程的内容，才可以手动标记为通过）")
    try:
        result = knowledge_map.set_node_status(user_id, request.subject_id,
                                               request.node_id, request.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": result}


# =====================================================================
# 练习作答（判分与掌握度由程序完成）
# =====================================================================

@router.post("/tutor/practice/answer")
async def practice_answer(request: PracticeAnswerRequest,
                          user_id: str = Depends(require_current_user)):
    await require_subject_read(request.subject_id, user_id)
    from services import quiz_service
    try:
        result = await run_in_threadpool(
            quiz_service.submit_attempt, user_id, request.question_id, request.answer,
            request.client_token, request.subject_id, None, request.conversation_id,
            request.duration_sec)
    except quiz_service.QuizServiceError as e:
        raise HTTPException(status_code=400, detail=e.message)
    node_update = None
    if request.node_id:
        node_update = _sync_node_from_attempt(user_id, request.subject_id,
                                              request.node_id, result)
    return {"success": True, "data": {"attempt": result, "node": node_update}}


def _sync_node_from_attempt(user_id: str, subject_id: str, node_id: str,
                            attempt: dict) -> Optional[dict]:
    """
    用真实作答结果更新节点状态。

    - 独立作答正确且掌握度达到阈值 → passed（这是唯一能写入 passed 的地方）
    - 独立作答错误 → 已经 passed 过则 needs_review，否则 practicing
    """
    try:
        node = knowledge_map.get_node(subject_id, node_id)
        if not node:
            return None
        kp = node.get("label")
        from services import mastery_service
        mastery = mastery_service.get_mastery(user_id, subject_id, kp) if kp else {}
        latest = (mastery or {}).get("latest_view") or {}
        level = latest.get("latest_mastery_level")
        if level is None:
            level = (mastery or {}).get("mastery_level")
        correct = bool(attempt.get("is_correct"))
        independent = not attempt.get("after_reveal") and not attempt.get("used_hint")
        prev_status = knowledge_map.list_progress(user_id, subject_id).get(node_id, {}).get(
            "status", knowledge_map.STATUS_NEW)
        status = prev_status
        if correct and independent and level is not None and level >= 0.9:
            status = knowledge_map.STATUS_PASSED
        elif not correct:
            status = (knowledge_map.STATUS_REVIEW
                      if prev_status == knowledge_map.STATUS_PASSED
                      else knowledge_map.STATUS_PRACTICING)
        elif prev_status == knowledge_map.STATUS_NEW:
            status = knowledge_map.STATUS_PRACTICING
        if status != prev_status:
            knowledge_map.set_node_status(user_id, subject_id, node_id, status)
        return {"node_id": node_id, "status": status,
                "status_label": knowledge_map.STATUS_LABELS.get(status, status),
                "mastery_level": level}
    except Exception as e:
        logger.warning(f"根据作答更新节点状态失败: {e}")
        return None


# =====================================================================
# 学习成果
# =====================================================================

@router.get("/tutor/artifacts")
async def list_artifacts(subject_id: Optional[str] = None, kind: Optional[str] = None,
                         user_id: str = Depends(require_current_user)):
    if subject_id:
        await require_subject_read(subject_id, user_id)
    context = tutor_session.load_context(user_id, subject_id) if subject_id else {}
    scope = material_scope.build_scope(
        user_id, subject_id,
        (context.get("material_scope") or {}).get("selected_doc_ids")) if subject_id else {}
    items = await run_in_threadpool(
        artifacts_service.list_artifacts, user_id, subject_id, kind,
        scope.get("signature"), 200)
    return {"success": True, "data": {"artifacts": items}}


@router.post("/tutor/artifacts")
async def create_artifact(request: ArtifactCreate,
                          user_id: str = Depends(require_current_user)):
    await require_subject_read(request.subject_id, user_id)
    scope = _current_scope(user_id, request.subject_id, request.doc_ids)
    artifact = artifacts_service.create_artifact(
        user_id, request.subject_id, request.kind, request.title, request.content,
        node_id=request.node_id,
        source_scope={"signature": scope.get("signature"), "label": scope.get("label"),
                      "selected": material_scope.scope_to_public(scope).get("selected")},
        source_signature=scope.get("signature"), origin="user")
    return {"success": True, "data": {"artifact": artifact}}


@router.put("/tutor/artifacts/{artifact_id}")
async def update_artifact(artifact_id: str, request: ArtifactUpdate,
                          user_id: str = Depends(require_current_user)):
    artifact = artifacts_service.update_artifact(
        user_id, artifact_id, title=request.title, content=request.content)
    if not artifact:
        raise HTTPException(status_code=404, detail="成果不存在")
    return {"success": True, "data": {"artifact": artifact}}


@router.delete("/tutor/artifacts/{artifact_id}")
async def delete_artifact(artifact_id: str,
                          user_id: str = Depends(require_current_user)):
    ok = artifacts_service.delete_artifact(user_id, artifact_id)
    return {"success": ok, "data": {"deleted": ok}}
