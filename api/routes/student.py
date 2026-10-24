# api/routes/student.py
# 学生端学习入口接口（阶段一）

from typing import Optional
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.schemas.response_schema import SuccessResponse
from api.deps import require_current_user
from services import (knowledge_tracing, learning_service, mastery_service,
                      quiz_service, review_service)
from services import learning_events
from services.learning_service import LearningServiceError

router = APIRouter()


class RescheduleRequest(BaseModel):
    new_date: str
    reason: Optional[str] = None


class CompleteTaskRequest(BaseModel):
    actual_minutes: Optional[int] = None


def _learning_error(e: LearningServiceError):
    status = {
        "invalid_request": 400,
        "forbidden": 403,
        "quiz_attempt_required": 409,
    }.get(e.error_type, 500)
    return JSONResponse(
        status_code=status,
        content={"success": False, "data": None,
                 "message": e.message, "error_type": e.error_type},
    )


@router.get("/student/tasks")
async def student_tasks(subject_id: Optional[str] = None,
                        bucket: Optional[str] = None,
                        user_id: str = Depends(require_current_user)):
    """
    任务视图：bucket ∈ today | todo | completed | overdue（不传返回四类聚合）
    """
    if bucket:
        data = await run_in_threadpool(
            learning_service.list_tasks, user_id, subject_id, bucket)
        return SuccessResponse(data=data, message="获取任务成功")
    data = await run_in_threadpool(learning_service.get_task_buckets, user_id, subject_id)
    return SuccessResponse(data=data, message="获取任务清单成功")


@router.post("/student/tasks/{task_id}/complete")
async def complete_task(task_id: str,
                        request: CompleteTaskRequest,
                        user_id: str = Depends(require_current_user)):
    """
    完成任务（幂等）

    - 只记录学习行为，不改变掌握度
    - 测验任务需先有真实作答（区分"完成"与"通过"）
    """
    try:
        data = await run_in_threadpool(
            learning_service.complete_task, user_id, task_id, request.actual_minutes)
        return SuccessResponse(data=data, message="任务已完成")
    except LearningServiceError as e:
        return _learning_error(e)


@router.put("/student/tasks/{task_id}/schedule")
async def reschedule_task(task_id: str,
                          request: RescheduleRequest,
                          user_id: str = Depends(require_current_user)):
    """调整任务计划日期（保留原日期与调整记录）"""
    try:
        data = await run_in_threadpool(
            learning_service.reschedule_task, user_id, task_id,
            request.new_date, request.reason)
        return SuccessResponse(data=data, message="任务日期已调整")
    except LearningServiceError as e:
        return _learning_error(e)


@router.get("/student/tasks/{task_id}/adjustments")
async def task_adjustments(task_id: str,
                           user_id: str = Depends(require_current_user)):
    """查看任务改期历史（仅本人任务；非本人返回空列表）"""
    data = await run_in_threadpool(
        learning_service.list_task_adjustments, task_id, user_id)
    return SuccessResponse(data=data, message="获取改期记录成功")


@router.get("/student/mastery")
async def student_mastery(subject_id: str,
                          user_id: str = Depends(require_current_user)):
    """知识点掌握情况（无证据显示待评估，附计算依据）"""
    data = await run_in_threadpool(mastery_service.summarize_mastery, user_id, subject_id)
    items = await run_in_threadpool(mastery_service.list_mastery, user_id, subject_id)
    return SuccessResponse(data={"summary": data, "points": items},
                           message="获取掌握情况成功")


@router.get("/student/knowledge-tracing")
async def student_knowledge_tracing(subject_id: str,
                                    knowledge_point: Optional[str] = None,
                                    user_id: str = Depends(require_current_user)):
    """BKT 影子估计：按知识点返回已掌握概率、样本量与置信度（不改现有掌握度）。"""
    data = await run_in_threadpool(
        knowledge_tracing.estimate_user, subject_id, user_id, knowledge_point)
    return SuccessResponse(data=data, message="获取 BKT 估计成功")


@router.get("/student/home")
async def student_home(subject_id: Optional[str] = None,
                       user_id: str = Depends(require_current_user)):
    """
    学生首页聚合：今天要完成的任务、待复习、最近错题、最近学习位置、学习统计
    """
    buckets = await run_in_threadpool(learning_service.get_task_buckets, user_id, subject_id)
    reviews = await run_in_threadpool(
        review_service.list_review_tasks, user_id, subject_id, "PENDING")
    wrong = await run_in_threadpool(quiz_service.list_wrong_questions, user_id, subject_id)
    recent = await run_in_threadpool(quiz_service.list_attempts, user_id, subject_id, None, 5)
    stats = await run_in_threadpool(quiz_service.get_quiz_stats, user_id, subject_id)

    # 最近学习位置：最近一次作答涉及的知识点（可继续学习）
    last_position = None
    if recent:
        last_position = {
            "knowledge_point": recent[0].get("knowledge_point_name"),
            "knowledge_point_id": recent[0].get("knowledge_point_id"),
            "question_id": recent[0].get("question_id"),
            "at": recent[0].get("created_at"),
        }

    return SuccessResponse(data={
        "today_tasks": buckets["today"],
        "overdue_tasks": buckets["overdue"],
        "todo_count": buckets["counts"]["todo"],
        "pending_reviews": reviews,
        "wrong_questions": wrong[:10],
        "wrong_count": len(wrong),
        "recent_attempts": recent,
        "quiz_stats": stats,
        "last_position": last_position,
    }, message="获取学习首页成功")


@router.get("/student/learning-events")
async def list_learning_events(subject_id: Optional[str] = None,
                               status: Optional[str] = None,
                               limit: int = 50,
                               user_id: str = Depends(require_current_user)):
    """
    学习事件流水（阶段一：联动过程可追溯）

    用于确认"作答 → 画像 → 错题 → 复习 → 计划"每一步是否真的执行成功。
    """
    data = await run_in_threadpool(
        learning_events.list_events, user_id, subject_id, status, limit)
    return SuccessResponse(data=data, message="获取学习事件成功")


@router.post("/student/learning-events/retry")
async def retry_learning_events(limit: int = 50,
                                user_id: str = Depends(require_current_user)):
    """
    重试失败的联动事件（仅限本人事件）

    联动失败不会丢核心数据（作答已保存），这里做补偿重试。
    """
    data = await run_in_threadpool(
        learning_events.retry_failed_events, limit, user_id)
    return SuccessResponse(data=data, message="失败事件重试完成")


# ==================== 我的课程（教师端联动，2026-09-26） ====================

class JoinClassRequest(BaseModel):
    code: str


@router.get("/student/courses")
async def my_courses(user_id: str = Depends(require_current_user)):
    """我加入的课程与班级（教师发布的作业从这里进入）"""
    from services import teaching_service
    from services.teaching_data import TeachingError
    try:
        data = await run_in_threadpool(teaching_service.list_my_courses, user_id)
        return SuccessResponse(data={"courses": data}, message="获取我的课程成功")
    except TeachingError as e:
        return _student_teaching_error(e)


@router.post("/student/courses/join")
async def join_course(request: JoinClassRequest,
                      user_id: str = Depends(require_current_user)):
    """用班级邀请码加入课程"""
    from services import teaching_service
    from services.teaching_data import TeachingError
    try:
        data = await run_in_threadpool(
            teaching_service.join_class_by_code, user_id, request.code)
        return SuccessResponse(data=data, message="加入成功")
    except TeachingError as e:
        return _student_teaching_error(e)


@router.post("/student/classes/{class_id}/leave")
async def leave_class(class_id: str, user_id: str = Depends(require_current_user)):
    """退出班级（保留历史记录）"""
    from services import teaching_service
    from services.teaching_data import TeachingError
    try:
        data = await run_in_threadpool(teaching_service.leave_class, user_id, class_id)
        return SuccessResponse(data=data, message="已退出班级")
    except TeachingError as e:
        return _student_teaching_error(e)


@router.get("/student/assignments")
async def my_assignments(course_id: Optional[str] = None,
                         class_id: Optional[str] = None,
                         user_id: str = Depends(require_current_user)):
    """老师布置给我的作业/练习（只含已发布，不含答案）"""
    from services import assignment_service
    from services.teaching_data import TeachingError
    try:
        data = await run_in_threadpool(
            assignment_service.list_student_assignments, user_id, course_id, class_id)
        return SuccessResponse(data={"assignments": data}, message="获取作业成功")
    except TeachingError as e:
        return _student_teaching_error(e)


@router.get("/student/assignments/{assignment_id}")
async def assignment_detail(assignment_id: str,
                            user_id: str = Depends(require_current_user)):
    """打开一次作业（含题目，不含标准答案）"""
    from services import assignment_service
    from services.teaching_data import TeachingError
    try:
        data = await run_in_threadpool(
            assignment_service.assignment_for_student, user_id, assignment_id)
        return SuccessResponse(data=data, message="获取作业成功")
    except TeachingError as e:
        return _student_teaching_error(e)


@router.post("/student/assignments/{assignment_id}/complete")
async def complete_assignment(assignment_id: str,
                              user_id: str = Depends(require_current_user)):
    """标记作业完成（题目都作答过才允许）"""
    from services import assignment_service
    from services.teaching_data import TeachingError
    try:
        data = await run_in_threadpool(
            assignment_service.complete_assignment_task, user_id, assignment_id)
        return SuccessResponse(data=data, message="作业已完成")
    except TeachingError as e:
        return _student_teaching_error(e)


@router.get("/student/courses/{course_id}/lesson-plans")
async def course_lesson_plans(course_id: str,
                              user_id: str = Depends(require_current_user)):
    """本课程**已发布**的教案（草稿不可见）"""
    from services import lesson_plan_service
    from services.teaching_data import TeachingError
    try:
        data = await run_in_threadpool(
            lesson_plan_service.student_visible_plans, user_id, course_id)
        return SuccessResponse(data={"lesson_plans": data}, message="获取教案成功")
    except TeachingError as e:
        return _student_teaching_error(e)


@router.get("/student/courses/{course_id}/materials")
async def course_materials(course_id: str,
                           user_id: str = Depends(require_current_user)):
    """本课程对**学生可见**的资料（仅教师可见的资料不会返回）"""
    from services import teaching_service
    from services.teaching_data import TeachingError
    try:
        data = await run_in_threadpool(
            teaching_service.list_course_materials, user_id, course_id)
        return SuccessResponse(data=data, message="获取课程资料成功")
    except TeachingError as e:
        return _student_teaching_error(e)


def _student_teaching_error(e) -> JSONResponse:
    """教师端错误 → HTTP 状态码（与学生端保持一致的响应结构）"""
    status = {
        "invalid_request": 400, "not_found": 404, "forbidden": 403,
        "conflict": 409, "insufficient_evidence": 422,
    }.get(getattr(e, "error_type", "unknown"), 500)
    return JSONResponse(status_code=status, content={
        "success": False, "data": None,
        "message": getattr(e, "message", str(e)),
        "error_type": getattr(e, "error_type", "unknown")})
