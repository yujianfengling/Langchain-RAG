# api/routes/agent.py
# 自我学习AGENT接口路由（V2.6）
#
# 阶段一：学习数据一律归属"后端当前用户"，不再接受请求体传入的 user_id。
# 路径中的 {user_id} 为历史遗留参数，仅作占位（见 api.deps.resolve_user_scope）。
#
# 阶段一补验：知识提取（待验证知识列表 / 校验 / 入库）此前无身份依赖，
# 任何匿名调用者都能查看与改写他人的提取结果，现已全部要求登录。

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.deps import get_current_user, require_current_user, resolve_user_scope
from api.schemas.response_schema import SuccessResponse
from api.exceptions import ValidationError
from services.proactive_learner import generate_recommendations, get_knowledge_gaps, generate_learning_path, get_learning_stats
from services.learning_loop import get_learning_progress_summary
from services.knowledge_expander import extract_knowledge_from_qa, validate_knowledge, add_validated_knowledge_to_kb, get_pending_knowledge, get_knowledge_extraction_stats

from services.user_profiler import (
    UserProfileError,
    get_or_create_profile,
    update_knowledge_level,
    update_learning_style,
    record_interaction,
    get_user_interactions,
    submit_feedback,
    get_user_feedback_stats,
    get_weak_points,
    get_strong_points,
    set_learning_preference,
    get_learning_preferences,
    analyze_user_performance,
)

router = APIRouter()


# ============ 请求模型 ============

class FeedbackRequest(BaseModel):
    interaction_id: Optional[str] = None
    feedback_type: str = "rating"
    feedback_score: Optional[int] = None
    feedback_detail: Optional[str] = None
    correction_text: Optional[str] = None


class KnowledgeLevelRequest(BaseModel):
    subject_id: str
    level: float


class LearningStyleRequest(BaseModel):
    style: str


class LearningPreferenceRequest(BaseModel):
    preference_type: str
    preference_value: str


class RecordInteractionRequest(BaseModel):
    interaction_type: str
    subject_id: Optional[str] = None
    question: Optional[str] = None
    answer: Optional[str] = None
    context_used: Optional[str] = None
    model_used: Optional[str] = None
    latency: Optional[float] = None


# ============ 用户画像接口 ============

@router.get("/agent/profile/{user_id}")
async def api_get_profile(user_id: str, current_user: str = Depends(get_current_user)):
    """获取用户画像"""
    owner = resolve_user_scope(current_user, user_id)
    try:
        profile = await run_in_threadpool(get_or_create_profile, owner)
        return SuccessResponse(data=profile, message="获取用户画像成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


@router.post("/agent/knowledge-level")
async def api_update_knowledge_level(request: KnowledgeLevelRequest,
                                     current_user: str = Depends(get_current_user)):
    """更新用户知识水平"""
    if not 0 <= request.level <= 1:
        raise ValidationError("知识水平必须在0-1之间")
    try:
        await run_in_threadpool(update_knowledge_level, current_user, request.subject_id, request.level)
        return SuccessResponse(data=None, message="知识水平更新成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


@router.post("/agent/learning-style")
async def api_update_learning_style(request: LearningStyleRequest,
                                    current_user: str = Depends(get_current_user)):
    """更新学习风格"""
    try:
        await run_in_threadpool(update_learning_style, current_user, request.style)
        return SuccessResponse(data=None, message="学习风格更新成功")
    except UserProfileError as e:
        return JSONResponse(status_code=400, content={"success": False, "message": e.message})


# ============ 反馈接口 ============

@router.post("/agent/feedback")
async def api_submit_feedback(request: FeedbackRequest,
                             current_user: str = Depends(get_current_user)):
    """提交用户反馈"""
    try:
        fid = await run_in_threadpool(
            submit_feedback,
            current_user,
            request.interaction_id,
            request.feedback_type,
            request.feedback_score,
            request.feedback_detail,
            request.correction_text,
        )
        return SuccessResponse(data={"feedback_id": fid}, message="反馈提交成功")
    except UserProfileError as e:
        return JSONResponse(status_code=400, content={"success": False, "message": e.message})


@router.get("/agent/feedback/stats/{user_id}")
async def api_feedback_stats(user_id: str, current_user: str = Depends(get_current_user)):
    """获取反馈统计"""
    owner = resolve_user_scope(current_user, user_id)
    try:
        stats = await run_in_threadpool(get_user_feedback_stats, owner)
        return SuccessResponse(data=stats, message="获取反馈统计成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


# ============ 知识点掌握接口 ============

@router.get("/agent/weak-points/{user_id}/{subject_id}")
async def api_weak_points(user_id: str, subject_id: str, threshold: float = 0.4,
                          current_user: str = Depends(get_current_user)):
    """获取薄弱知识点"""
    owner = resolve_user_scope(current_user, user_id)
    try:
        points = await run_in_threadpool(get_weak_points, owner, subject_id, threshold)
        return SuccessResponse(data=points, message="获取薄弱知识点成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


@router.get("/agent/strong-points/{user_id}/{subject_id}")
async def api_strong_points(user_id: str, subject_id: str, threshold: float = 0.7,
                           current_user: str = Depends(get_current_user)):
    """获取强项知识点"""
    owner = resolve_user_scope(current_user, user_id)
    try:
        points = await run_in_threadpool(get_strong_points, owner, subject_id, threshold)
        return SuccessResponse(data=points, message="获取强项知识点成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


# ============ 学习偏好接口 ============

@router.post("/agent/preferences")
async def api_set_preference(request: LearningPreferenceRequest,
                            current_user: str = Depends(get_current_user)):
    """设置学习偏好"""
    try:
        await run_in_threadpool(set_learning_preference, current_user,
                                request.preference_type, request.preference_value)
        return SuccessResponse(data=None, message="学习偏好设置成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


@router.get("/agent/preferences/{user_id}")
async def api_get_preferences(user_id: str, current_user: str = Depends(get_current_user)):
    """获取学习偏好"""
    owner = resolve_user_scope(current_user, user_id)
    try:
        prefs = await run_in_threadpool(get_learning_preferences, owner)
        return SuccessResponse(data=prefs, message="获取学习偏好成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


# ============ 分析接口 ============

@router.get("/agent/analysis/{user_id}/{subject_id}")
async def api_analyze_performance(user_id: str, subject_id: str,
                                 current_user: str = Depends(get_current_user)):
    """分析用户表现"""
    owner = resolve_user_scope(current_user, user_id)
    try:
        analysis = await run_in_threadpool(analyze_user_performance, owner, subject_id)
        return SuccessResponse(data=analysis, message="用户表现分析成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


# ============ 交互记录接口 ============

@router.post("/agent/interactions")
async def api_record_interaction(request: RecordInteractionRequest,
                                current_user: str = Depends(get_current_user)):
    """记录用户交互"""
    try:
        iid = await run_in_threadpool(
            record_interaction,
            current_user,
            request.interaction_type,
            request.subject_id,
            request.question,
            request.answer,
            request.context_used,
            request.model_used,
            request.latency,
        )
        return SuccessResponse(data={"interaction_id": iid}, message="交互记录成功")
    except UserProfileError as e:
        return JSONResponse(status_code=400, content={"success": False, "message": e.message})


@router.get("/agent/interactions/{user_id}")
async def api_get_interactions(user_id: str, subject_id: Optional[str] = None, interaction_type: Optional[str] = None, limit: int = 50,
                              current_user: str = Depends(get_current_user)):
    """获取用户交互记录"""
    owner = resolve_user_scope(current_user, user_id)
    try:
        interactions = await run_in_threadpool(get_user_interactions, owner, subject_id, interaction_type, limit)
        return SuccessResponse(data=interactions, message="获取交互记录成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})



# ============ 主动学习推荐接口 ============

@router.get("/agent/recommendations/{user_id}")
async def api_get_recommendations(user_id: str, subject_id: Optional[str] = None, current_topic: Optional[str] = None,
                                 current_user: str = Depends(get_current_user)):
    """获取个性化学习推荐"""
    owner = resolve_user_scope(current_user, user_id)
    try:
        result = await run_in_threadpool(generate_recommendations, owner, subject_id, current_topic)
        return SuccessResponse(data=result, message="获取学习推荐成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取推荐失败: {str(e)}"})


@router.get("/agent/knowledge-gaps/{user_id}/{subject_id}")
async def api_get_knowledge_gaps(user_id: str, subject_id: str,
                                current_user: str = Depends(get_current_user)):
    """获取知识盲点"""
    owner = resolve_user_scope(current_user, user_id)
    try:
        gaps = await run_in_threadpool(get_knowledge_gaps, owner, subject_id)
        return SuccessResponse(data=gaps, message="获取知识盲点成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取知识盲点失败: {str(e)}"})



# ============ 测验答案提交接口 ============

class QuizAnswerRequest(BaseModel):
    """提交测验作答

    阶段一说明：不接受 is_correct / 掌握度增量 —— 判分由服务端依据题库标准答案完成，
    掌握度只能由真实作答证据驱动（见 services/quiz_service.py）。
    """
    question_id: str
    user_answer: Optional[str] = None
    subject_id: Optional[str] = None
    client_token: Optional[str] = None      # 幂等令牌
    source_task_id: Optional[str] = None    # 来源任务（从学习计划进入时携带）
    conversation_id: Optional[str] = None
    duration_sec: Optional[int] = None
    used_hint: bool = False


@router.post("/agent/quiz-answer")
async def api_submit_quiz_answer(request: QuizAnswerRequest,
                                 current_user: str = Depends(get_current_user)):
    """
    提交测验作答（服务端权威判分）

    与 /quiz/answer 等价，保留此路径以兼容既有 Agent 前端调用。
    """
    from services.quiz_service import submit_attempt, QuizServiceError

    try:
        result = await run_in_threadpool(
            submit_attempt,
            current_user,
            request.question_id,
            request.user_answer,
            request.client_token,
            request.subject_id,
            request.source_task_id,
            request.conversation_id,
            request.duration_sec,
            request.used_hint,
        )
        return SuccessResponse(data=result, message="作答已保存")
    except QuizServiceError as e:
        status = {"not_found": 404, "forbidden": 403, "invalid_request": 400}.get(e.error_type, 500)
        return JSONResponse(status_code=status, content={
            "success": False, "data": None,
            "message": e.message, "error_type": e.error_type})


# ============ 学习进度摘要接口 ============

@router.get("/agent/progress-summary/{user_id}/{subject_id}")
async def api_get_progress_summary(user_id: str, subject_id: str,
                                  current_user: str = Depends(get_current_user)):
    """获取学习进度摘要"""
    owner = resolve_user_scope(current_user, user_id)
    try:
        summary = await run_in_threadpool(get_learning_progress_summary, owner, subject_id)
        return SuccessResponse(data=summary, message="获取学习进度成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取学习进度失败: {str(e)}"})



# ============ 学习路径接口 ============

@router.get("/agent/learning-path/{user_id}/{subject_id}")
async def api_get_learning_path(user_id: str, subject_id: str,
                               current_user: str = Depends(get_current_user)):
    """获取个性化学习路径"""
    owner = resolve_user_scope(current_user, user_id)
    try:
        path = await run_in_threadpool(generate_learning_path, owner, subject_id)
        return SuccessResponse(data=path, message="获取学习路径成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取学习路径失败: {str(e)}"})


@router.get("/agent/learning-stats/{user_id}/{subject_id}")
async def api_get_learning_stats(user_id: str, subject_id: str,
                                current_user: str = Depends(get_current_user)):
    """获取学习统计信息"""
    owner = resolve_user_scope(current_user, user_id)
    try:
        stats = await run_in_threadpool(get_learning_stats, owner, subject_id)
        return SuccessResponse(data=stats, message="获取学习统计成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取学习统计失败: {str(e)}"})



# ============ 知识提取接口 ============

class ExtractKnowledgeRequest(BaseModel):
    subject_id: str
    question: str
    answer: str
    feedback_score: int = 0


@router.post("/agent/extract-knowledge")
async def api_extract_knowledge(request: ExtractKnowledgeRequest,
                               current_user: str = Depends(require_current_user)):
    """
    从问答对中提取知识

    教师端补充（2026-09-26）：
    - 必须登录（此前用 get_current_user，未带令牌会回退到历史账号，等价匿名可写）
    - 课程学科只有授课教师能写入提取知识，避免学生把内容写进课程知识库
    """
    try:
        await _guard_subject_write(request.subject_id, current_user)
        kid = await run_in_threadpool(
            extract_knowledge_from_qa,
            current_user,
            request.subject_id,
            request.question,
            request.answer,
            request.feedback_score,
        )
        if kid:
            return SuccessResponse(data={"knowledge_id": kid}, message="知识提取成功")
        else:
            return SuccessResponse(data=None, message="未提取到有价值的知识")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"知识提取失败: {str(e)}"})


@router.post("/agent/validate-knowledge/{knowledge_id}")
async def api_validate_knowledge(knowledge_id: str,
                                 user_id: str = Depends(require_current_user)):
    """验证提取的知识（需登录；课程学科仅授课教师可操作）"""
    try:
        await _guard_knowledge_write(knowledge_id, user_id)
        result = await run_in_threadpool(validate_knowledge, knowledge_id)
        return SuccessResponse(data=result, message="知识验证完成")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"知识验证失败: {str(e)}"})


@router.post("/agent/add-knowledge-to-kb/{knowledge_id}")
async def api_add_knowledge_to_kb(knowledge_id: str, subject_id: str,
                                  user_id: str = Depends(require_current_user)):
    """
    将验证通过的知识添加到知识库（需登录）

    teacher 补充：既要校验知识所属学科，也要校验目标学科（两者都可能是课程学科）
    """
    try:
        await _guard_knowledge_write(knowledge_id, user_id)
        await _guard_subject_write(subject_id, user_id)
        success = await run_in_threadpool(add_validated_knowledge_to_kb, knowledge_id, subject_id)
        if success:
            return SuccessResponse(data=None, message="知识已添加到知识库")
        else:
            return JSONResponse(status_code=400, content={"success": False, "message": "添加失败，知识可能未验证"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"添加失败: {str(e)}"})


@router.get("/agent/pending-knowledge")
async def api_get_pending_knowledge(subject_id: Optional[str] = None, limit: int = 10,
                                    user_id: str = Depends(require_current_user)):
    """
    获取待验证的知识（需登录；课程级公共资料，非个人数据）

    teacher 补充：课程学科只对本课程教师/在班学生可见
    """
    try:
        if subject_id:
            await _guard_subject_read(subject_id, user_id)
        knowledge_list = await run_in_threadpool(get_pending_knowledge, subject_id, limit)
        knowledge_list = await _filter_readable(user_id, knowledge_list)
        return SuccessResponse(data=knowledge_list, message="获取待验证知识成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取失败: {str(e)}"})


@router.get("/agent/knowledge-extraction-stats")
async def api_get_extraction_stats(subject_id: Optional[str] = None,
                                   user_id: str = Depends(require_current_user)):
    """获取知识提取统计（需登录；指定学科时校验读权限）"""
    try:
        if subject_id:
            await _guard_subject_read(subject_id, user_id)
        stats = await run_in_threadpool(get_knowledge_extraction_stats, subject_id)
        return SuccessResponse(data=stats, message="获取统计成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取统计失败: {str(e)}"})


# ==================== 教师端：课程资料边界（2026-09-26） ====================

async def _guard_subject_read(subject_id: str, user_id: str) -> None:
    """非路径参数场景的学科读校验（课程学科仅本课程教师/在班学生可见）"""
    from api.deps import require_subject_read
    await require_subject_read(subject_id, user_id)


async def _guard_subject_write(subject_id: str, user_id: str) -> None:
    """学科写校验（课程学科仅授课教师/管理员；历史学科回退既有白名单）"""
    from api.deps import require_subject_write
    await require_subject_write(subject_id, user_id)


async def _guard_knowledge_write(knowledge_id: str, user_id: str) -> None:
    """按"这条提取知识属于哪个学科"校验写权限（防止跨课程写入）"""
    from api.deps import require_subject_write
    from services.knowledge_expander import get_knowledge
    item = await run_in_threadpool(get_knowledge, knowledge_id)
    if not item:
        raise HTTPException(status_code=404, detail="知识条目不存在")
    subject_id = item.get("subject_id") if isinstance(item, dict) else None
    if subject_id:
        await require_subject_write(subject_id, user_id)


async def _filter_readable(user_id: str, items):
    """过滤出当前用户可读学科的条目"""
    from services import teaching_access
    if not isinstance(items, list):
        return items
    out = []
    for item in items:
        sid = item.get("subject_id") if isinstance(item, dict) else None
        try:
            if not sid or teaching_access.can_read_subject(user_id, sid):
                out.append(item)
        except Exception:
            out.append(item)
    return out
