# api/routes/teacher.py
# 教师端接口路由

from typing import Optional
from fastapi import APIRouter

from api.schemas.response_schema import SuccessResponse
from api.exceptions import ValidationError
from services.user_profiler import (
    UserProfileError,
    list_students,
    get_student_subjects,
    get_weak_points,
    get_strong_points,
    analyze_user_performance,
    get_user_feedback_stats,
)
from services.proactive_learner import get_learning_stats, get_knowledge_gaps

router = APIRouter()


@router.get("/teacher/students", response_model=SuccessResponse)
async def teacher_list_students():
    """
    教师端：列出所有学生及其学习统计摘要
    """
    try:
        students = list_students()
        return SuccessResponse(data={"students": students}, message="ok")
    except UserProfileError as e:
        raise ValidationError(str(e))


@router.get("/teacher/students/{user_id}/overview", response_model=SuccessResponse)
async def teacher_student_overview(user_id: str, subject_id: Optional[str] = None):
    """
    教师端：单个学生的学情总览（弱项、强项、学习统计、反馈）

    - **user_id**: 学生ID
    - **subject_id**: 学科ID（可选，不传则返回所有学科）
    """
    try:
        subjects = get_student_subjects(user_id)
        if subject_id is None and subjects:
            subject_id = subjects[0]  # 默认取第一个学科

        weak_points = get_weak_points(user_id, subject_id) if subject_id else []
        strong_points = get_strong_points(user_id, subject_id) if subject_id else []
        stats = get_learning_stats(user_id, subject_id) if subject_id else {}
        gaps = get_knowledge_gaps(user_id, subject_id) if subject_id else []
        feedback = get_user_feedback_stats(user_id)

        return SuccessResponse(data={
            "user_id": user_id,
            "subjects": subjects,
            "current_subject": subject_id,
            "weak_points": weak_points,
            "strong_points": strong_points,
            "learning_stats": stats,
            "knowledge_gaps": gaps,
            "feedback": feedback,
        }, message="ok")
    except UserProfileError as e:
        raise ValidationError(str(e))


@router.get("/teacher/class/overview", response_model=SuccessResponse)
async def teacher_class_overview():
    """
    教师端：班级整体学情概览
    """
    try:
        students = list_students()
        total_students = len(students)
        total_interactions = sum(s["interaction_count"] for s in students)
        total_knowledge_points = sum(s["knowledge_point_count"] for s in students)
        total_weak = sum(s["weak_count"] for s in students)
        total_strong = sum(s["strong_count"] for s in students)
        avg_mastery = (sum(s["average_mastery"] for s in students) / total_students) if total_students else 0

        return SuccessResponse(data={
            "total_students": total_students,
            "total_interactions": total_interactions,
            "total_knowledge_points": total_knowledge_points,
            "total_weak_points": total_weak,
            "total_strong_points": total_strong,
            "average_mastery": round(avg_mastery, 3),
        }, message="ok")
    except UserProfileError as e:
        raise ValidationError(str(e))
