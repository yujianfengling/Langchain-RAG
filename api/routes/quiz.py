# api/routes/quiz.py
# 自测作答与错题接口（阶段一）

from typing import List, Optional
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.schemas.response_schema import SuccessResponse
from api.deps import require_current_user, require_subject_read
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


class ErrorCauseRequest(BaseModel):
    code: str
    note: Optional[str] = None


class VariantRequest(BaseModel):
    question_id: str
    subject_id: Optional[str] = None


class PaperRequest(BaseModel):
    subject_id: str
    knowledge_points: Optional[List[str]] = None
    count: int = 10
    types: Optional[List[str]] = None
    duration_min: Optional[int] = None


class PaperExportRequest(PaperRequest):
    title: str = "智能组卷"


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


@router.post("/quiz/attempts/{attempt_id}/error-cause")
async def report_error_cause(attempt_id: str, request: ErrorCauseRequest,
                             user_id: str = Depends(require_current_user)):
    """记录学生自报的错因（只允许自己的、答错的作答；不改变判分与掌握度）。"""
    try:
        data = await run_in_threadpool(
            quiz_service.report_error_cause, attempt_id, user_id, request.code, request.note)
        return SuccessResponse(data=data, message="错因已记录")
    except QuizServiceError as e:
        return _quiz_error(e)


@router.post("/quiz/variant")
async def generate_variant(request: VariantRequest,
                           user_id: str = Depends(require_current_user)):
    """基于原题生成一道同知识点的变式题（答案不下发）。"""
    if not request.question_id:
        return JSONResponse(status_code=400, content={
            "success": False, "data": None, "message": "缺少 question_id",
            "error_type": "invalid_request"})
    # 变式题会带着原题的知识点生成：必须先确认当前用户能读这个学科，
    # 不能只凭 question_id 跨学科取内容。
    question = await run_in_threadpool(quiz_service.get_question_admin, request.question_id)
    if not question:
        return _quiz_error(QuizServiceError("题目不存在", "not_found"))
    await require_subject_read(question["subject_id"], user_id)
    try:
        data = await run_in_threadpool(
            quiz_service.generate_variant, user_id, request.question_id, request.subject_id)
        return SuccessResponse(data=data, message="变式题已生成")
    except QuizServiceError as e:
        return _quiz_error(e)


@router.post("/quiz/paper/preview")
async def preview_paper(request: PaperRequest,
                        user_id: str = Depends(require_current_user)):
    """按知识点/题型/时长组一套卷（预览，不含答案；不落库）。"""
    await require_subject_read(request.subject_id, user_id)
    from services import paper_builder
    data = await run_in_threadpool(
        paper_builder.build_preview,
        request.subject_id, request.knowledge_points, request.count,
        request.types, request.duration_min)
    return SuccessResponse(data=data, message="组卷预览成功")


@router.post("/quiz/paper/export")
async def export_paper(request: PaperExportRequest,
                       user_id: str = Depends(require_current_user)):
    """导出 Word 试卷 + 参考答案（仅教师可见答案；预览接口仍然不下发答案）。"""
    await require_subject_read(request.subject_id, user_id)
    from services import paper_builder
    try:
        data = await run_in_threadpool(
            paper_builder.export_docx,
            request.subject_id, request.knowledge_points, request.count,
            request.types, request.duration_min, request.title)
    except ValueError as e:
        return JSONResponse(status_code=400, content={
            "success": False, "data": None, "message": str(e),
            "error_type": "invalid_request"})
    return Response(
        content=data["content"],
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="paper.docx"'},
    )


@router.get("/quiz/attempts")
async def list_attempts(subject_id: Optional[str] = None,
                        question_id: Optional[str] = None,
                        limit: int = 100,
                        user_id: str = Depends(require_current_user)):
    """作答历史（刷新/重启后仍可查看）"""
    data = await run_in_threadpool(
        quiz_service.list_attempts, user_id, subject_id, question_id, limit)
    return SuccessResponse(data=data, message="获取作答历史成功")


@router.get("/quiz/questions")
async def list_questions(subject_id: str, knowledge_point: str, limit: int = 20,
                         user_id: str = Depends(require_current_user)):
    """
    列出某知识点下的题目（含该用户最近一次作答，若有）。

    学生点知识地图里的知识点时，用它把"以前出过的题"重新摆出来。
    **不下发标准答案**，答案仍只在提交作答或显式查看时给出。
    """
    await require_subject_read(subject_id, user_id)
    data = await run_in_threadpool(quiz_service.list_questions_for_point,
                                   subject_id, knowledge_point, user_id, limit)
    return SuccessResponse(data={"questions": data}, message="获取题目成功")


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
