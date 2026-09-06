# api/routes/agent.py
# 自我学习AGENT接口路由（V2.6）

from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.schemas.response_schema import SuccessResponse
from api.exceptions import ValidationError
from services.proactive_learner import generate_recommendations, get_knowledge_gaps, generate_learning_path, get_learning_stats
from services.learning_loop import on_quiz_answered, get_learning_progress_summary
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
    user_id: str = "default_user"
    interaction_id: Optional[str] = None
    feedback_type: str = "rating"
    feedback_score: Optional[int] = None
    feedback_detail: Optional[str] = None
    correction_text: Optional[str] = None


class KnowledgeLevelRequest(BaseModel):
    user_id: str = "default_user"
    subject_id: str
    level: float


class LearningStyleRequest(BaseModel):
    user_id: str = "default_user"
    style: str


class LearningPreferenceRequest(BaseModel):
    user_id: str = "default_user"
    preference_type: str
    preference_value: str


class RecordInteractionRequest(BaseModel):
    user_id: str = "default_user"
    interaction_type: str
    subject_id: Optional[str] = None
    question: Optional[str] = None
    answer: Optional[str] = None
    context_used: Optional[str] = None
    model_used: Optional[str] = None
    latency: Optional[float] = None


# ============ 用户画像接口 ============

@router.get("/agent/profile/{user_id}")
async def api_get_profile(user_id: str):
    """获取用户画像"""
    try:
        profile = await run_in_threadpool(get_or_create_profile, user_id)
        return SuccessResponse(data=profile, message="获取用户画像成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


@router.post("/agent/knowledge-level")
async def api_update_knowledge_level(request: KnowledgeLevelRequest):
    """更新用户知识水平"""
    if not 0 <= request.level <= 1:
        raise ValidationError("知识水平必须在0-1之间")
    try:
        await run_in_threadpool(update_knowledge_level, request.user_id, request.subject_id, request.level)
        return SuccessResponse(data=None, message="知识水平更新成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


@router.post("/agent/learning-style")
async def api_update_learning_style(request: LearningStyleRequest):
    """更新学习风格"""
    try:
        await run_in_threadpool(update_learning_style, request.user_id, request.style)
        return SuccessResponse(data=None, message="学习风格更新成功")
    except UserProfileError as e:
        return JSONResponse(status_code=400, content={"success": False, "message": e.message})


# ============ 反馈接口 ============

@router.post("/agent/feedback")
async def api_submit_feedback(request: FeedbackRequest):
    """提交用户反馈"""
    try:
        fid = await run_in_threadpool(
            submit_feedback,
            request.user_id,
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
async def api_feedback_stats(user_id: str):
    """获取反馈统计"""
    try:
        stats = await run_in_threadpool(get_user_feedback_stats, user_id)
        return SuccessResponse(data=stats, message="获取反馈统计成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


# ============ 知识点掌握接口 ============

@router.get("/agent/weak-points/{user_id}/{subject_id}")
async def api_weak_points(user_id: str, subject_id: str, threshold: float = 0.4):
    """获取薄弱知识点"""
    try:
        points = await run_in_threadpool(get_weak_points, user_id, subject_id, threshold)
        return SuccessResponse(data=points, message="获取薄弱知识点成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


@router.get("/agent/strong-points/{user_id}/{subject_id}")
async def api_strong_points(user_id: str, subject_id: str, threshold: float = 0.7):
    """获取强项知识点"""
    try:
        points = await run_in_threadpool(get_strong_points, user_id, subject_id, threshold)
        return SuccessResponse(data=points, message="获取强项知识点成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


# ============ 学习偏好接口 ============

@router.post("/agent/preferences")
async def api_set_preference(request: LearningPreferenceRequest):
    """设置学习偏好"""
    try:
        await run_in_threadpool(set_learning_preference, request.user_id, request.preference_type, request.preference_value)
        return SuccessResponse(data=None, message="学习偏好设置成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


@router.get("/agent/preferences/{user_id}")
async def api_get_preferences(user_id: str):
    """获取学习偏好"""
    try:
        prefs = await run_in_threadpool(get_learning_preferences, user_id)
        return SuccessResponse(data=prefs, message="获取学习偏好成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


# ============ 分析接口 ============

@router.get("/agent/analysis/{user_id}/{subject_id}")
async def api_analyze_performance(user_id: str, subject_id: str):
    """分析用户表现"""
    try:
        analysis = await run_in_threadpool(analyze_user_performance, user_id, subject_id)
        return SuccessResponse(data=analysis, message="用户表现分析成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})


# ============ 交互记录接口 ============

@router.post("/agent/interactions")
async def api_record_interaction(request: RecordInteractionRequest):
    """记录用户交互"""
    try:
        iid = await run_in_threadpool(
            record_interaction,
            request.user_id,
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
async def api_get_interactions(user_id: str, subject_id: Optional[str] = None, interaction_type: Optional[str] = None, limit: int = 50):
    """获取用户交互记录"""
    try:
        interactions = await run_in_threadpool(get_user_interactions, user_id, subject_id, interaction_type, limit)
        return SuccessResponse(data=interactions, message="获取交互记录成功")
    except UserProfileError as e:
        return JSONResponse(status_code=500, content={"success": False, "message": e.message})



# ============ 主动学习推荐接口 ============

@router.get("/agent/recommendations/{user_id}")
async def api_get_recommendations(user_id: str, subject_id: Optional[str] = None, current_topic: Optional[str] = None):
    """获取个性化学习推荐"""
    try:
        result = await run_in_threadpool(generate_recommendations, user_id, subject_id, current_topic)
        return SuccessResponse(data=result, message="获取学习推荐成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取推荐失败: {str(e)}"})


@router.get("/agent/knowledge-gaps/{user_id}/{subject_id}")
async def api_get_knowledge_gaps(user_id: str, subject_id: str):
    """获取知识盲点"""
    try:
        gaps = await run_in_threadpool(get_knowledge_gaps, user_id, subject_id)
        return SuccessResponse(data=gaps, message="获取知识盲点成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取知识盲点失败: {str(e)}"})



# ============ 测验答案提交接口 ============

class QuizAnswerRequest(BaseModel):
    user_id: str = "default_user"
    subject_id: str
    knowledge_point: str
    is_correct: bool
    mastery_delta: float = 0.1


@router.post("/agent/quiz-answer")
async def api_submit_quiz_answer(request: QuizAnswerRequest):
    """提交测验答案，更新知识点掌握度"""
    try:
        result = await run_in_threadpool(
            on_quiz_answered,
            request.user_id,
            request.subject_id,
            request.knowledge_point,
            request.is_correct,
        )
        return SuccessResponse(data=result, message="测验答案提交成功")
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})



# ============ 学习进度摘要接口 ============

@router.get("/agent/progress-summary/{user_id}/{subject_id}")
async def api_get_progress_summary(user_id: str, subject_id: str):
    """获取学习进度摘要"""
    try:
        summary = await run_in_threadpool(get_learning_progress_summary, user_id, subject_id)
        return SuccessResponse(data=summary, message="获取学习进度成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取学习进度失败: {str(e)}"})



# ============ 学习路径接口 ============

@router.get("/agent/learning-path/{user_id}/{subject_id}")
async def api_get_learning_path(user_id: str, subject_id: str):
    """获取个性化学习路径"""
    try:
        path = await run_in_threadpool(generate_learning_path, user_id, subject_id)
        return SuccessResponse(data=path, message="获取学习路径成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取学习路径失败: {str(e)}"})


@router.get("/agent/learning-stats/{user_id}/{subject_id}")
async def api_get_learning_stats(user_id: str, subject_id: str):
    """获取学习统计信息"""
    try:
        stats = await run_in_threadpool(get_learning_stats, user_id, subject_id)
        return SuccessResponse(data=stats, message="获取学习统计成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取学习统计失败: {str(e)}"})



# ============ 知识提取接口 ============

class ExtractKnowledgeRequest(BaseModel):
    user_id: str = "default_user"
    subject_id: str
    question: str
    answer: str
    feedback_score: int = 0


@router.post("/agent/extract-knowledge")
async def api_extract_knowledge(request: ExtractKnowledgeRequest):
    """从问答对中提取知识"""
    try:
        kid = await run_in_threadpool(
            extract_knowledge_from_qa,
            request.user_id,
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
async def api_validate_knowledge(knowledge_id: str):
    """验证提取的知识"""
    try:
        result = await run_in_threadpool(validate_knowledge, knowledge_id)
        return SuccessResponse(data=result, message="知识验证完成")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"知识验证失败: {str(e)}"})


@router.post("/agent/add-knowledge-to-kb/{knowledge_id}")
async def api_add_knowledge_to_kb(knowledge_id: str, subject_id: str):
    """将验证通过的知识添加到知识库"""
    try:
        success = await run_in_threadpool(add_validated_knowledge_to_kb, knowledge_id, subject_id)
        if success:
            return SuccessResponse(data=None, message="知识已添加到知识库")
        else:
            return JSONResponse(status_code=400, content={"success": False, "message": "添加失败，知识可能未验证"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"添加失败: {str(e)}"})


@router.get("/agent/pending-knowledge")
async def api_get_pending_knowledge(subject_id: Optional[str] = None, limit: int = 10):
    """获取待验证的知识"""
    try:
        knowledge_list = await run_in_threadpool(get_pending_knowledge, subject_id, limit)
        return SuccessResponse(data=knowledge_list, message="获取待验证知识成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取失败: {str(e)}"})


@router.get("/agent/knowledge-extraction-stats")
async def api_get_extraction_stats(subject_id: Optional[str] = None):
    """获取知识提取统计"""
    try:
        stats = await run_in_threadpool(get_knowledge_extraction_stats, subject_id)
        return SuccessResponse(data=stats, message="获取统计成功")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"获取统计失败: {str(e)}"})
