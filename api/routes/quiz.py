# api/routes/quiz.py
# 自测作答与错题接口（阶段一）

from typing import Optional
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.schemas.response_schema import SuccessResponse
from api.deps import require_current_user
from services import quiz_service, review_service
from services.quiz_service import QuizServiceError

router = APIRouter()


class SubmitAnswerRequest(BaseModel):
    """提交作答请求

    注意：不接受 is_correct —— 判分由服务端依据题库标准答案完成。
    """
    question_id: str
    user_answer: Optional[str] = None
    subject_id: Optional[str] = None
    client_token: Optional[str] = None      # 幂等令牌，重复提交只计一次
    source_task_id: Optional[str] = None    # 来源任务（从计划进入时携带）
    conversation_id: Optional[str] = None
    duration_sec: Optional[int] = None
    used_hint: bool = False


class SelfEvaluateRequest(BaseModel):
    attempt_id: str
    is_correct: bool


def _quiz_error(e: QuizServiceError):
    status = {"not_found": 404, "forbidden": 403, "invalid_request": 400}.get(e.error_type, 500)
    return JSONResponse(
        status_code=status,
        content={"success": False, "data": None,
                 "message": e.message, "error_type": e.error_type},
    )


@router.post("/quiz/answer")
async def submit_answer(request: SubmitAnswerRequest,
                        user_id: str = Depends(require_current_user)):
    """
    提交作答（服务端权威判分）

    前端只提交所选项 ID；对错由后端题库标准答案判定，重复提交幂等。
    """
    if not request.question_id:
        return JSONResponse(status_code=400, content={
            "success": False, "data": None, "message": "缺少 question_id",
            "error_type": "invalid_request"})
    try:
        result = await run_in_threadpool(
            quiz_service.submit_attempt,
            user_id,
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
        return _quiz_error(e)


@router.post("/quiz/reveal/{question_id}")
async def reveal(question_id: str, user_id: str = Depends(require_current_user)):
    """查看答案（此后该题的再次作答不计入独立掌握证据）"""
    try:
        data = await run_in_threadpool(quiz_service.reveal_answer, question_id, user_id)
        return SuccessResponse(data=data, message="已显示答案")
    except QuizServiceError as e:
        return _quiz_error(e)


@router.post("/quiz/self-evaluate")
async def self_evaluate(request: SelfEvaluateRequest,
                        user_id: str = Depends(require_current_user)):
    """简答题自评（明确标记为自评，不伪装客观判分）"""
    try:
        data = await run_in_threadpool(
            quiz_service.self_evaluate, request.attempt_id, user_id, request.is_correct)
        return SuccessResponse(data=data, message="自评已记录")
    except QuizServiceError as e:
        return _quiz_error(e)


@router.get("/quiz/attempts")
async def list_attempts(subject_id: Optional[str] = None,
                        question_id: Optional[str] = None,
                        limit: int = 100,
                        user_id: str = Depends(require_current_user)):
    """作答历史（刷新/重启后仍可查看）"""
    data = await run_in_threadpool(
        quiz_service.list_attempts, user_id, subject_id, question_id, limit)
    return SuccessResponse(data=data, message="获取作答历史成功")


@router.get("/quiz/wrong")
async def list_wrong(subject_id: Optional[str] = None,
                     user_id: str = Depends(require_current_user)):
    """
    错题本（最近一次作答为错；重做答对后移出，历史保留）。

    **默认不下发标准答案与解析**：需要看答案请调用
    `GET /quiz/wrong/{question_id}`（该调用会在后端登记"已查看答案"）。
    已查看过的题目会带上答案，并标记 `revealed=true`。
    """
    data = await run_in_threadpool(quiz_service.list_wrong_questions, user_id, subject_id)
    return SuccessResponse(data=data, message="获取错题成功")


@router.get("/quiz/wrong/{question_id}")
async def wrong_question_detail(question_id: str,
                               user_id: str = Depends(require_current_user)):
    """
    查看某道错题的答案与解析（**会登记"已查看答案"**）。

    登记后该题后续作答记为 after_reveal，不计入独立掌握证据，
    因此"从错题本看到答案再答对"不会被算作真实掌握。
    """
    try:
        data = await run_in_threadpool(
            quiz_service.get_wrong_question_detail, user_id, question_id)
        return SuccessResponse(data=data, message="已显示解析")
    except QuizServiceError as e:
        return _quiz_error(e)


@router.get("/quiz/stats")
async def quiz_stats(subject_id: Optional[str] = None,
                     user_id: str = Depends(require_current_user)):
    """作答统计（仅统计有判分/自评结果的作答）"""
    data = await run_in_threadpool(quiz_service.get_quiz_stats, user_id, subject_id)
    return SuccessResponse(data=data, message="获取作答统计成功")


@router.get("/quiz/reviews")
async def list_reviews(subject_id: Optional[str] = None,
                       status: Optional[str] = None,
                       user_id: str = Depends(require_current_user)):
    """复习安排（含自动新增原因，可追溯）"""
    data = await run_in_threadpool(
        review_service.list_review_tasks, user_id, subject_id, status)
    return SuccessResponse(data=data, message="获取复习安排成功")
