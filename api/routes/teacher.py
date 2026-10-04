# api/routes/teacher.py
# 教师端接口（2026-09-26）
#
# 权限：**全部由后端校验**（require_current_user + 课程归属/在班判定），
# 前端菜单与路由只影响展示，不构成权限。
#
# 端点分组：
#   工作台      GET  /teacher/dashboard
#   课程        POST /teacher/courses            GET /teacher/courses
#               GET  /teacher/courses/{id}       PATCH /teacher/courses/{id}
#   班级/成员   POST /teacher/courses/{id}/classes
#               GET  /teacher/courses/{id}/classes
#               GET  /teacher/classes/{id}/members
#               POST /teacher/classes/{id}/reset-code（轮换）
#               POST /teacher/classes/{id}/revoke-code（撤销）
#               GET  /teacher/classes/{id}/join-code（状态）
#               POST /teacher/classes/{id}/members/{member_id}/remove
#   课程资料    POST /teacher/courses/{id}/materials（上传，禁止传路径）
#               GET  /teacher/courses/{id}/materials
#               DELETE /teacher/materials/{material_id}
#               POST /teacher/materials/{material_id}/visibility
#               POST /teacher/courses/{id}/knowledge/rebuild
#   教案        POST /teacher/courses/{id}/lesson-plans/generate
#               POST /teacher/courses/{id}/lesson-plans
#               GET  /teacher/courses/{id}/lesson-plans
#               GET  /teacher/lesson-plans/{plan_id}
#               PUT  /teacher/lesson-plans/{plan_id}
#               GET  /teacher/lesson-plans/{plan_id}/versions
#               POST /teacher/lesson-plans/{plan_id}/publish
#               GET  /teacher/lesson-plans/{plan_id}/preview
#               GET  /teacher/lesson-plans/{plan_id}/export
#   作业        POST /teacher/courses/{id}/assignments
#               POST /teacher/courses/{id}/assignments/generate-questions
#               GET  /teacher/courses/{id}/assignments
#               PUT  /teacher/assignments/{assignment_id}
#               POST /teacher/assignments/{assignment_id}/publish
#               POST /teacher/assignments/{assignment_id}/close
#   学情        GET  /teacher/classes/{id}/analytics
#               GET  /teacher/classes/{id}/analytics/knowledge-points/{kp_id}
#               POST /teacher/classes/{id}/analytics/explain
#
# 学生侧（加入课程与作业）在 student.py，见 /student/courses 等。

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from api.deps import require_admin, require_current_user, require_teacher
from api.schemas.response_schema import SuccessResponse
from services.teaching_data import TeachingError

router = APIRouter()

# 错误类型 → HTTP 状态码
_TEACHING_STATUS = {
    "invalid_request": 400,
    "not_found": 404,
    "forbidden": 403,
    "conflict": 409,
    "invalid_role": 400,
    "teacher_required": 403,
    "invalid_invite": 400,
    "too_many_attempts": 429,
    "upload_failed": 502,
    "retrieval_failed": 502,
    "llm_error": 502,
    "parse_error": 502,
    "insufficient_evidence": 422,
    "database_error": 500,
}


def _teach_err(e):
    """身份服务异常 → HTTP（补齐教师开通相关错误码）"""
    status = {"invalid_request": 400, "not_found": 404, "forbidden": 403,
              "quota_exceeded": 429, "invalid_role": 400, "auth_error": 400}.get(
        getattr(e, "error_type", "auth_error"), 400)
    return JSONResponse(status_code=status, content={
        "success": False, "data": None, "message": getattr(e, "message", str(e)),
        "error_type": getattr(e, "error_type", "auth_error")})


def _err(e: TeachingError):
    status = _TEACHING_STATUS.get(e.error_type, 500)
    return JSONResponse(status_code=status, content={
        "success": False, "data": None, "message": e.message,
        "error_type": e.error_type})


# ==================== 请求模型 ====================

class CourseCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    subject_name: Optional[str] = None
    # 未开通教师身份时，必须携带有效的教师邀请码（受控开通，不能自助提权）
    teacher_invite_code: Optional[str] = None


class TeacherInviteCreateRequest(BaseModel):
    """生成教师开通邀请码（管理员或已有教师）"""
    ttl_days: Optional[int] = None
    max_uses: int = 1
    note: Optional[str] = None


class RedeemInviteRequest(BaseModel):
    """用户凭教师邀请码开通教师身份"""
    code: str


class CourseUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


class ClassCreateRequest(BaseModel):
    name: str


class LessonPlanGenerateRequest(BaseModel):
    topic: str
    hours: float = 1.0
    objectives: str = ""
    class_id: Optional[str] = None
    material_ids: Optional[List[str]] = None
    model: Optional[str] = None
    title: Optional[str] = None


class LessonPlanSaveRequest(BaseModel):
    """教师编辑教案内容并保存为新版本"""
    content: dict = Field(default_factory=dict)
    title: Optional[str] = None
    topic: Optional[str] = None
    hours: Optional[float] = None
    class_id: Optional[str] = None
    note: Optional[str] = None


class LessonPlanCreateRequest(BaseModel):
    """教师手写教案（不走 AI）"""
    title: str
    topic: Optional[str] = None
    hours: Optional[float] = None
    class_id: Optional[str] = None
    content: dict = Field(default_factory=dict)


class AssignmentCreateRequest(BaseModel):
    class_id: str
    title: str
    description: Optional[str] = None
    assign_type: str = "quiz"
    knowledge_points: Optional[List[str]] = None
    due_date: Optional[str] = None
    estimated_minutes: int = 30
    question_ids: Optional[List[str]] = None


class AssignmentUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[str] = None
    knowledge_points: Optional[List[str]] = None
    estimated_minutes: Optional[int] = None
    question_ids: Optional[List[str]] = None


class GenerateQuestionsRequest(BaseModel):
    assignment_id: Optional[str] = None
    knowledge_points: Optional[List[str]] = None
    count: int = 3
    model: Optional[str] = None


class VisibilityRequest(BaseModel):
    visibility: str


# ==================== 教师开通（受控流程） ====================

@router.get("/teacher/role")
async def my_role(user_id: str = Depends(require_current_user)):
    """
    当前账号的角色与来源（前端据此展示"如何开通教师身份"）

    - role: student / teacher / admin
    - source: env（环境白名单）/ invite（邀请码）/ admin（管理员授予）/ none
    - can_create_course: 是否已具备建课权限
    """
    from services import auth_service
    role = auth_service.get_role(user_id)
    return SuccessResponse(data={
        "role": role,
        "source": auth_service.teacher_grant_source(user_id),
        "can_create_course": role in (auth_service.ROLE_TEACHER, auth_service.ROLE_ADMIN),
        "policy": ("教师身份需通过邀请码开通；创建课程不再自动授予。"
                   "邀请码默认仅管理员可签发，教师签发需部署方开启 TEACHER_CAN_INVITE=1"),
    }, message="获取角色成功")


@router.post("/teacher/invites")
async def create_teacher_invite(request: TeacherInviteCreateRequest,
                                user_id: str = Depends(require_teacher)):
    """
    生成教师开通邀请码

    **权限（2026-09-27 收紧）**：默认**只有管理员**可以签发；
    教师签发需要部署方显式配置 `TEACHER_CAN_INVITE=1`，且受有效期 / 可用次数 /
    名下未用尽邀请码数量三重上限约束（见 auth_service.invite_policy）。
    目的是避免教师邀请码成为不受控的教师身份扩散渠道。
    """
    from services import auth_service
    from services.teaching_data import TeachingError
    try:
        data = auth_service.create_role_invite(
            user_id, ttl_days=request.ttl_days, max_uses=request.max_uses,
            note=request.note)
        return SuccessResponse(data=data, message="教师邀请码已生成")
    except auth_service.AuthError as e:
        return _teach_err(e)


@router.get("/teacher/invites")
async def list_teacher_invites(limit: int = Query(default=50),
                               user_id: str = Depends(require_teacher)):
    """
    邀请码列表（含已过期/已撤销/已用次数）

    - 管理员：看到**全部**邀请码，可撤销任意一个
    - 教师：只看到**自己签发的**邀请码
    同时返回当前账号的签发权限与配额，供界面据此隐藏/禁用按钮。
    """
    from services import auth_service
    policy = auth_service.invite_policy(user_id)
    return SuccessResponse(data={
        "invites": auth_service.list_role_invites(limit, actor_user_id=user_id),
        "policy": policy,
    }, message="获取邀请码成功")


@router.post("/teacher/invites/{code}/revoke")
async def revoke_teacher_invite(code: str, user_id: str = Depends(require_teacher)):
    """撤销邀请码（立即失效）。管理员可撤销任意一个；教师只能撤销自己签发的。"""
    from services import auth_service
    try:
        return SuccessResponse(data=auth_service.revoke_role_invite(
            code, actor_user_id=user_id), message="邀请码已撤销")
    except auth_service.AuthError as e:
        return _teach_err(e)


@router.post("/teacher/apply-invite")
async def apply_teacher_invite(request: RedeemInviteRequest,
                               user_id: str = Depends(require_current_user)):
    """
    凭教师邀请码开通教师身份（在设置页填写）

    邀请码一次性/限量、可过期、可撤销；同一用户同一码只能用一次。
    """
    from services import auth_service
    result = auth_service.redeem_role_invite(request.code, user_id)
    if not result.get("ok"):
        return JSONResponse(status_code=400, content={
            "success": False, "data": None, "message": f"开通失败：{result.get('reason')}",
            "error_type": "invalid_invite"})
    return SuccessResponse(data=result, message="教师身份已开通，可以创建课程了")


@router.post("/teacher/users/{target_user_id}/grant")
async def admin_grant_teacher(target_user_id: str,
                              user_id: str = Depends(require_admin)):
    """管理员直接把某账号开通为教师（不依赖邀请码）"""
    from services import auth_service
    try:
        # grant_role 的副作用是写角色；返回值这里用不到（role 下面直接从库里读）
        auth_service.grant_role(target_user_id, auth_service.ROLE_TEACHER, reason="admin")
        return SuccessResponse(data={"user_id": target_user_id,
                                     "role": auth_service.get_role(target_user_id)},
                               message="已开通教师身份")
    except auth_service.AuthError as e:
        return _teach_err(e)


# ==================== 工作台 ====================

@router.get("/teacher/dashboard")
async def dashboard(user_id: str = Depends(require_teacher)):
    """教师工作台：我的课程 / 待完成教案 / 已发布任务 / 需关注知识点"""
    from services import class_analytics
    try:
        return SuccessResponse(data=class_analytics.teacher_dashboard(user_id),
                               message="获取工作台成功")
    except TeachingError as e:
        return _err(e)


# ==================== 课程 ====================

@router.post("/teacher/courses")
async def create_course(request: CourseCreateRequest,
                        user_id: str = Depends(require_current_user)):
    """
    创建课程（教师身份由此动作显式授予；同时创建课程专属知识库）
    """
    from services import teaching_service
    try:
        data = teaching_service.create_course(
            user_id, request.title, request.description, request.subject_name,
            teacher_invite_code=request.teacher_invite_code)
        return SuccessResponse(data=data, message="课程创建成功")
    except TeachingError as e:
        return _err(e)


@router.get("/teacher/courses")
async def list_courses(user_id: str = Depends(require_teacher)):
    from services import teaching_service
    return SuccessResponse(data={"courses": teaching_service.list_teacher_courses(user_id)},
                           message="获取课程成功")


@router.get("/teacher/courses/{course_id}")
async def course_detail(course_id: str, user_id: str = Depends(require_current_user)):
    from services import teaching_service
    try:
        return SuccessResponse(data=teaching_service.get_course_overview(user_id, course_id),
                               message="获取课程详情成功")
    except TeachingError as e:
        return _err(e)


@router.patch("/teacher/courses/{course_id}")
async def update_course(course_id: str, request: CourseUpdateRequest,
                        user_id: str = Depends(require_current_user)):
    from services import teaching_access as access
    from services import teaching_data as td
    try:
        access.require_course_teacher(user_id, course_id)
        data = td.update_course(course_id, title=request.title,
                                description=request.description, status=request.status)
        td.audit(course_id, user_id, "course.update", "course", course_id,
                 {"status": request.status})
        return SuccessResponse(data=data, message="课程已更新")
    except TeachingError as e:
        return _err(e)


# ==================== 班级与成员 ====================

@router.post("/teacher/courses/{course_id}/classes")
async def create_class(course_id: str, request: ClassCreateRequest,
                       user_id: str = Depends(require_current_user)):
    from services import teaching_service
    try:
        return SuccessResponse(data=teaching_service.create_class(user_id, course_id, request.name),
                               message="班级创建成功")
    except TeachingError as e:
        return _err(e)


@router.get("/teacher/courses/{course_id}/classes")
async def list_classes(course_id: str, user_id: str = Depends(require_current_user)):
    from services import teaching_service
    try:
        return SuccessResponse(data={"classes": teaching_service.list_classes(user_id, course_id)},
                               message="获取班级成功")
    except TeachingError as e:
        return _err(e)


@router.get("/teacher/classes/{class_id}/members")
async def class_members(class_id: str, user_id: str = Depends(require_current_user)):
    from services import teaching_service
    try:
        return SuccessResponse(data=teaching_service.list_members(user_id, class_id),
                               message="获取成员成功")
    except TeachingError as e:
        return _err(e)


@router.get("/teacher/classes/{class_id}/students/{student_id}/attempts")
async def student_attempts(class_id: str, student_id: str,
                           days: int = 30, limit: int = 200,
                           user_id: str = Depends(require_current_user)):
    """**单学生作答明细**（含原判定 / 教师判定 / 生效判定三者并列）

    教师此前只能看到班级聚合，看不到某个学生逐题怎么答的、AI 判了什么。
    """
    from services import teacher_grading
    try:
        return SuccessResponse(
            data=teacher_grading.list_student_attempts(
                user_id, class_id, student_id, days=days, limit=limit),
            message="获取作答明细成功")
    except TeachingError as e:
        return _err(e)


class RegradeRequest(BaseModel):
    is_correct: bool
    reason: str = Field(..., description="改判理由（必填，会随记录保留）")


@router.post("/teacher/classes/{class_id}/attempts/{attempt_id}/regrade")
async def regrade_attempt(class_id: str, attempt_id: str, request: RegradeRequest,
                          user_id: str = Depends(require_current_user)):
    """**教师改判**：保留原判定，写入教师判定/理由/人/时间，并同步掌握度与复习

    - 生效判定写入 `is_correct`/`grade_status`，因此学情、错题本、掌握度自动跟随；
    - 原判定固化到 `orig_*`（首次改判时写入，此后不覆盖）；
    - 改判为"错"→ 确保该知识点有待复习；改判为"对"→ 关闭待复习任务。
    """
    from services import teacher_grading
    try:
        return SuccessResponse(
            data=teacher_grading.regrade_attempt(
                user_id, class_id, attempt_id, request.is_correct, request.reason),
            message="改判成功")
    except TeachingError as e:
        return _err(e)


@router.get("/teacher/students/{student_id}/attempts")
async def student_attempts_for_teacher(student_id: str, days: int = 30, limit: int = 200,
                                       user_id: str = Depends(require_current_user)):
    """**单学生作答明细**（教师级入口：自动解析该学生所在、且属于本教师的班级）

    与班级版 `/teacher/classes/{class_id}/students/{student_id}/attempts` 等价，
    只是免去前端再选班级；权限校验一致（非本教师班级的学生一律拒绝）。
    """
    from services import teacher_grading
    try:
        return SuccessResponse(
            data=teacher_grading.list_student_attempts_for_teacher(
                user_id, student_id, days=days, limit=limit),
            message="获取作答明细成功")
    except TeachingError as e:
        return _err(e)


@router.post("/teacher/attempts/{attempt_id}/regrade")
async def regrade_attempt_for_teacher(attempt_id: str, request: RegradeRequest,
                                      user_id: str = Depends(require_current_user)):
    """**教师改判**（教师级入口）：保留原判定，写入教师判定/理由/人/时间，并同步掌握度与复习"""
    from services import teacher_grading
    try:
        return SuccessResponse(
            data=teacher_grading.regrade_attempt_for_teacher(
                user_id, attempt_id, request.is_correct, request.reason),
            message="改判成功")
    except TeachingError as e:
        return _err(e)


@router.post("/teacher/classes/{class_id}/reset-code")
async def reset_code(class_id: str, user_id: str = Depends(require_current_user)):
    """重置邀请码（旧码立即失效）"""
    from services import teaching_service
    try:
        return SuccessResponse(data=teaching_service.reset_join_code(user_id, class_id),
                               message="邀请码已重置")
    except TeachingError as e:
        return _err(e)


@router.post("/teacher/classes/{class_id}/revoke-code")
async def revoke_join_code(class_id: str, user_id: str = Depends(require_current_user)):
    """撤销班级邀请码（班级临时停止加入；之后可再轮换出新码）"""
    from services import teaching_service
    try:
        return SuccessResponse(data=teaching_service.revoke_join_code(user_id, class_id),
                               message="邀请码已撤销")
    except TeachingError as e:
        return _err(e)


@router.get("/teacher/classes/{class_id}/join-code")
async def join_code_status(class_id: str, user_id: str = Depends(require_current_user)):
    """邀请码状态：是否有效 / 何时过期 / 已用次数 / 是否被撤销"""
    from services import teaching_service
    try:
        return SuccessResponse(data=teaching_service.join_code_status(user_id, class_id),
                               message="获取邀请码状态成功")
    except TeachingError as e:
        return _err(e)


@router.post("/teacher/classes/{class_id}/members/{member_id}/remove")
async def remove_member(class_id: str, member_id: str,
                        reason: Optional[str] = Query(default=None),
                        user_id: str = Depends(require_current_user)):
    """把学生移出班级（保留记录，不删除历史作答）"""
    from services import teaching_service
    try:
        data = teaching_service.remove_member(user_id, class_id, member_id, reason)
        return SuccessResponse(data=data, message="已移出班级")
    except TeachingError as e:
        return _err(e)


# ==================== 课程资料 ====================

@router.post("/teacher/courses/{course_id}/materials")
async def upload_material(course_id: str,
                          file: UploadFile = File(...),
                          visibility: str = Query(default="students"),
                          user_id: str = Depends(require_current_user)):
    """
    上传课程资料

    只接受文件本身与文件名，**不接受任何服务器路径**；
    文件落在该课程专属学科的资料目录，并进入课程知识库。
    """
    from services import teaching_service
    try:
        content = await file.read()
        data = teaching_service.upload_material(
            user_id, course_id, content, file.filename or "", visibility=visibility)
        return SuccessResponse(data=data, message="资料上传成功")
    except TeachingError as e:
        return _err(e)
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "success": False, "data": None, "message": f"资料上传失败：{e}",
            "error_type": "upload_failed"})


@router.get("/teacher/courses/{course_id}/materials")
async def list_materials(course_id: str, user_id: str = Depends(require_current_user)):
    from services import teaching_service
    try:
        return SuccessResponse(data=teaching_service.list_course_materials(user_id, course_id),
                               message="获取资料成功")
    except TeachingError as e:
        return _err(e)


@router.delete("/teacher/materials/{material_id}")
async def delete_material(material_id: str, course_id: str = Query(...),
                          user_id: str = Depends(require_current_user)):
    from services import teaching_service
    try:
        return SuccessResponse(data=teaching_service.delete_material(user_id, course_id, material_id),
                               message="资料已删除")
    except TeachingError as e:
        return _err(e)


@router.post("/teacher/materials/{material_id}/visibility")
async def set_visibility(material_id: str, request: VisibilityRequest,
                         course_id: str = Query(...),
                         user_id: str = Depends(require_current_user)):
    """设置资料可见范围（学生可见 / 仅教师）"""
    from services import teaching_service
    try:
        data = teaching_service.set_material_visibility(
            user_id, course_id, material_id, request.visibility)
        return SuccessResponse(data=data, message="可见范围已更新")
    except TeachingError as e:
        return _err(e)


@router.post("/teacher/courses/{course_id}/knowledge/rebuild")
async def rebuild_knowledge(course_id: str, user_id: str = Depends(require_current_user)):
    """重建课程知识库（失败会返回原因，不会静默成功）"""
    from services import teaching_service
    try:
        data = teaching_service.rebuild_course_knowledge(user_id, course_id)
        return SuccessResponse(data=data,
                               message="知识库重建完成" if data.get("ok") else "知识库重建失败")
    except TeachingError as e:
        return _err(e)


# ==================== 教案 ====================

@router.post("/teacher/courses/{course_id}/lesson-plans/generate")
async def generate_lesson_plan(course_id: str, request: LessonPlanGenerateRequest,
                               user_id: str = Depends(require_current_user)):
    """AI 生成教案草稿（含依据资料；证据不足时明确标注）"""
    from services import lesson_plan_service
    try:
        data = lesson_plan_service.generate_draft(
            user_id, course_id, topic=request.topic, hours=request.hours,
            objectives=request.objectives, class_id=request.class_id,
            material_ids=request.material_ids, model=request.model, title=request.title)
        return SuccessResponse(data=data, message="教案草稿已生成")
    except TeachingError as e:
        return _err(e)


@router.post("/teacher/courses/{course_id}/lesson-plans")
async def create_lesson_plan(course_id: str, request: LessonPlanCreateRequest,
                             user_id: str = Depends(require_current_user)):
    """教师手写教案（草稿）"""
    from services import teaching_access as access
    from services import teaching_data as td
    try:
        course = access.require_course_teacher(user_id, course_id)
        plan = td.create_lesson_plan(
            course_id, created_by=user_id, title=request.title, topic=request.topic,
            hours=request.hours, class_id=request.class_id,
            content=request.content or {}, evidence=[], ai_generated=False,
            note="教师创建草稿")
        td.audit(course["id"], user_id, "lesson_plan.create", "lesson_plan", plan["id"], {})
        return SuccessResponse(data=plan, message="教案已创建")
    except TeachingError as e:
        return _err(e)


@router.get("/teacher/courses/{course_id}/lesson-plans")
async def list_lesson_plans(course_id: str, status: Optional[str] = Query(default=None),
                            user_id: str = Depends(require_current_user)):
    from services import teaching_access as access
    from services import teaching_data as td
    try:
        access.require_course_teacher(user_id, course_id)
        return SuccessResponse(data={"lesson_plans": td.list_lesson_plans(course_id, status=status)},
                               message="获取教案成功")
    except TeachingError as e:
        return _err(e)


@router.get("/teacher/lesson-plans/{plan_id}")
async def get_lesson_plan(plan_id: str, version: Optional[int] = Query(default=None),
                          user_id: str = Depends(require_current_user)):
    from services import lesson_plan_service
    from services import teaching_access as access
    from services import teaching_data as td
    try:
        plan = lesson_plan_service.get_plan_or_404(plan_id)
        access.require_course_teacher(user_id, plan["course_id"])
        return SuccessResponse(data=td.get_lesson_plan(plan_id, version=version),
                               message="获取教案成功")
    except TeachingError as e:
        return _err(e)


@router.put("/teacher/lesson-plans/{plan_id}")
async def update_lesson_plan(plan_id: str, request: LessonPlanSaveRequest,
                             user_id: str = Depends(require_current_user)):
    """教师编辑教案 → 保存为**新版本**（不覆盖历史版本）"""
    from services import lesson_plan_service
    try:
        data = lesson_plan_service.update_plan(
            user_id, plan_id, content=request.content, title=request.title,
            topic=request.topic, hours=request.hours, class_id=request.class_id,
            note=request.note)
        return SuccessResponse(data=data, message="教案已保存为新版本")
    except TeachingError as e:
        return _err(e)


@router.get("/teacher/lesson-plans/{plan_id}/versions")
async def lesson_plan_versions(plan_id: str, user_id: str = Depends(require_current_user)):
    from services import lesson_plan_service
    try:
        return SuccessResponse(data=lesson_plan_service.list_versions(user_id, plan_id),
                               message="获取版本成功")
    except TeachingError as e:
        return _err(e)


@router.post("/teacher/lesson-plans/{plan_id}/publish")
async def publish_lesson_plan(plan_id: str, user_id: str = Depends(require_current_user)):
    from services import lesson_plan_service
    try:
        return SuccessResponse(data=lesson_plan_service.publish(user_id, plan_id),
                               message="教案已发布")
    except TeachingError as e:
        return _err(e)


@router.get("/teacher/lesson-plans/{plan_id}/preview")
async def preview_lesson_plan(plan_id: str, version: Optional[int] = Query(default=None),
                              user_id: str = Depends(require_current_user)):
    """预览（返回 Markdown 文本，与导出内容一致）"""
    from services import lesson_plan_service
    from services import teaching_access as access
    from services import teaching_data as td
    try:
        plan = lesson_plan_service.get_plan_or_404(plan_id)
        access.require_course_teacher(user_id, plan["course_id"])
        full = td.get_lesson_plan(plan_id, version=version) or plan
        return SuccessResponse(data={"markdown": lesson_plan_service.to_markdown(full),
                                     "plan": full}, message="预览成功")
    except TeachingError as e:
        return _err(e)


@router.get("/teacher/lesson-plans/{plan_id}/export")
async def export_lesson_plan(plan_id: str, version: Optional[int] = Query(default=None),
                             user_id: str = Depends(require_current_user)):
    """导出 Markdown 文件"""
    from services import lesson_plan_service
    from services import teaching_access as access
    from services import teaching_data as td
    try:
        plan = lesson_plan_service.get_plan_or_404(plan_id)
        access.require_course_teacher(user_id, plan["course_id"])
        full = td.get_lesson_plan(plan_id, version=version) or plan
        md = lesson_plan_service.to_markdown(full)
        filename = f"lesson-plan-{plan_id}.md"
        return PlainTextResponse(
            content=md, media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except TeachingError as e:
        return _err(e)


# ==================== 作业 / 练习 ====================

@router.post("/teacher/courses/{course_id}/assignments")
async def create_assignment(course_id: str, request: AssignmentCreateRequest,
                            user_id: str = Depends(require_current_user)):
    from services import assignment_service
    try:
        data = assignment_service.create_assignment(
            user_id, course_id, class_id=request.class_id, title=request.title,
            description=request.description, assign_type=request.assign_type,
            knowledge_points=request.knowledge_points, due_date=request.due_date,
            estimated_minutes=request.estimated_minutes, question_ids=request.question_ids)
        return SuccessResponse(data=data, message="作业已创建（草稿）")
    except TeachingError as e:
        return _err(e)


@router.post("/teacher/courses/{course_id}/assignments/generate-questions")
async def generate_questions(course_id: str, request: GenerateQuestionsRequest,
                             user_id: str = Depends(require_current_user)):
    """AI 依据课程资料出题（可挂到草稿作业）"""
    from services import assignment_service
    try:
        data = assignment_service.generate_questions(
            user_id, course_id, assignment_id=request.assignment_id,
            knowledge_points=request.knowledge_points, count=request.count,
            model=request.model)
        return SuccessResponse(data=data, message=f"已生成 {data['generated']} 道题")
    except TeachingError as e:
        return _err(e)


@router.get("/teacher/courses/{course_id}/assignments")
async def list_assignments(course_id: str, class_id: Optional[str] = Query(default=None),
                           user_id: str = Depends(require_current_user)):
    from services import assignment_service
    try:
        return SuccessResponse(
            data={"assignments": assignment_service.list_teacher_assignments(
                user_id, course_id, class_id=class_id)},
            message="获取作业成功")
    except TeachingError as e:
        return _err(e)


@router.put("/teacher/assignments/{assignment_id}")
async def update_assignment(assignment_id: str, request: AssignmentUpdateRequest,
                            user_id: str = Depends(require_current_user)):
    from services import assignment_service
    try:
        data = assignment_service.update_assignment(
            user_id, assignment_id, title=request.title, description=request.description,
            due_date=request.due_date, knowledge_points=request.knowledge_points,
            estimated_minutes=request.estimated_minutes, question_ids=request.question_ids)
        return SuccessResponse(data=data, message="作业已更新")
    except TeachingError as e:
        return _err(e)


@router.post("/teacher/assignments/{assignment_id}/publish")
async def publish_assignment(assignment_id: str, user_id: str = Depends(require_current_user)):
    """发布作业：为在班学生生成任务并写入教师固定截止时间"""
    from services import assignment_service
    try:
        data = assignment_service.publish(user_id, assignment_id)
        msg = ("该作业此前已发布" if data.get("already_published")
               else f"已发布给 {len(data.get('created', []))} 名学生")
        return SuccessResponse(data=data, message=msg)
    except TeachingError as e:
        return _err(e)


@router.post("/teacher/assignments/{assignment_id}/close")
async def close_assignment(assignment_id: str, user_id: str = Depends(require_current_user)):
    from services import assignment_service
    try:
        return SuccessResponse(data=assignment_service.close_assignment(user_id, assignment_id),
                               message="作业已关闭")
    except TeachingError as e:
        return _err(e)


# ==================== 学情 ====================

@router.get("/teacher/classes/{class_id}/analytics")
async def class_analytics_overview(class_id: str, days: int = Query(default=30),
                                   start: Optional[str] = Query(default=None),
                                   end: Optional[str] = Query(default=None),
                                   user_id: str = Depends(require_current_user)):
    """班级学情（程序统计；样本不足时 enough_data=false）"""
    from services import class_analytics
    try:
        return SuccessResponse(
            data=class_analytics.class_overview(user_id, class_id, days=days,
                                                start=start, end=end),
            message="获取学情成功")
    except TeachingError as e:
        return _err(e)


@router.get("/teacher/classes/{class_id}/analytics/knowledge-points/{kp_id}")
async def knowledge_point_analytics(class_id: str, kp_id: str,
                                    days: int = Query(default=30),
                                    user_id: str = Depends(require_current_user)):
    """知识点下钻：错题（题干与选项，不含答案）+ 相关课程资料 + 建议动作"""
    from services import class_analytics
    try:
        return SuccessResponse(
            data=class_analytics.knowledge_point_detail(user_id, class_id, kp_id, days=days),
            message="获取知识点详情成功")
    except TeachingError as e:
        return _err(e)


@router.post("/teacher/classes/{class_id}/analytics/explain")
async def explain_analytics(class_id: str, days: int = Query(default=30),
                            model: Optional[str] = Query(default=None),
                            user_id: str = Depends(require_current_user)):
    """AI 解释学情并给教学建议（仅基于程序算出的汇总数字；样本不足时不调用模型）"""
    from services import class_analytics
    try:
        return SuccessResponse(
            data=class_analytics.explain_with_ai(user_id, class_id, days=days, model=model),
            message="已生成解读")
    except TeachingError as e:
        return _err(e)


# ==================== 学生画像（并入组员实现，2026-09-27 合并）====================
#
# 来源：组员提交 e503490「教师端（破烂版」提供 3 个接口（学生画像列表 / 单个学生总览 / 班级汇总）。
# 合并时的**必要改动**：原实现没有任何身份校验、且返回**全体用户**的画像；
# 这里统一改为 require_teacher + 只能看**自己名下班级**的学生，避免局域网内匿名读取全体学生数据。
# 计算逻辑沿用组员的 user_profiler / proactive_learner 调用，响应字段名保持不变（前端可直接复用）。

def _my_students(teacher_user_id: str) -> dict:
    """
    我名下各班级的**在班学生**：{user_id: {class/course 信息}}

    以**班级成员**为准，而不是以"有没有画像记录"为准——
    否则学生还没用过 AI 时，班里明明有人，列表却是空的（实测踩到）。
    """
    from services import teaching_service as ts
    from services import teaching_data as td

    out: dict = {}
    for course in ts.list_teacher_courses(teacher_user_id):
        for klass in td.list_classes(course["id"]):
            for m in td.list_members(klass["id"], status="active"):
                out.setdefault(m["user_id"], {
                    "class_id": klass["id"], "class_name": klass["name"],
                    "course_id": course["id"], "course_title": course["title"],
                })
    return out


def _my_student_ids(teacher_user_id: str) -> set:
    """在班学生 user_id 集合（用于把学生画像限制在自己的班里）"""
    return set(_my_students(teacher_user_id).keys())


def _student_row(user_id: str, profile: dict, ctx: dict) -> dict:
    """班级成员 + 画像统计合并成一行（没有画像记录时统计为 0，而不是把人漏掉）"""
    from services import auth_service
    u = auth_service.get_user(user_id) or {}
    return {
        "user_id": user_id,
        "display_name": u.get("display_name") or u.get("username") or user_id,
        "username": u.get("username"),
        "class_name": ctx.get("class_name"),
        "course_title": ctx.get("course_title"),
        "learning_style": (profile or {}).get("learning_style") or "",
        "interaction_count": int((profile or {}).get("interaction_count") or 0),
        "knowledge_point_count": int((profile or {}).get("knowledge_point_count") or 0),
        "average_mastery": float((profile or {}).get("average_mastery") or 0),
        "strong_count": int((profile or {}).get("strong_count") or 0),
        "weak_count": int((profile or {}).get("weak_count") or 0),
        "last_active": (profile or {}).get("last_active") or "",
        "has_profile": bool(profile),
    }


@router.get("/teacher/students")
async def teacher_list_students(user_id: str = Depends(require_teacher)):
    """
    学生画像列表（**仅我名下班级的在班学生**）

    与 `/teacher/classes/{id}/analytics` 的区别：这里看的是"每个学生个人"的画像
    （学习风格、交互次数、掌握度分布、强项/弱项），用于个别辅导；班级层面的统计看学情分析。
    """
    from services.user_profiler import UserProfileError, list_students
    try:
        mine = _my_students(user_id)
        profiles = {s.get("user_id"): s for s in list_students()}
        students = [_student_row(uid, profiles.get(uid), ctx) for uid, ctx in mine.items()]
        # 有画像记录的排前面，其次按薄弱点多的优先（个别辅导更有针对性）
        students.sort(key=lambda s: (not s["has_profile"], -s["weak_count"]))
        return SuccessResponse(
            data={"students": students, "scope": "自己名下班级的在班学生",
                  "total": len(students),
                  "note": "统计为 0 表示该生还没有 AI 作答/交互记录，不代表没有学习行为"},
            message="ok")
    except UserProfileError as e:
        return JSONResponse(status_code=400, content={
            "success": False, "data": None, "message": str(e),
            "error_type": "invalid_request"})


@router.get("/teacher/students/{target_user_id}/overview")
async def teacher_student_overview(target_user_id: str,
                                   subject_id: Optional[str] = Query(default=None),
                                   user_id: str = Depends(require_teacher)):
    """单个学生的画像总览（弱项 / 强项 / 学习统计 / 知识点缺口 / 反馈统计）"""
    from services.user_profiler import (
        UserProfileError, get_student_subjects, get_weak_points, get_strong_points,
        get_user_feedback_stats,
    )
    from services.proactive_learner import get_learning_stats, get_knowledge_gaps

    if target_user_id not in _my_student_ids(user_id):
        return JSONResponse(status_code=403, content={
            "success": False, "data": None,
            "message": "该学生不在你名下的班级中，无法查看其学情。",
            "error_type": "forbidden"})
    try:
        subjects = get_student_subjects(target_user_id)
        sid = subject_id or (subjects[0] if subjects else None)
        return SuccessResponse(data={
            "user_id": target_user_id,
            "subjects": subjects,
            "current_subject": sid,
            "weak_points": get_weak_points(target_user_id, sid) if sid else [],
            "strong_points": get_strong_points(target_user_id, sid) if sid else [],
            "learning_stats": get_learning_stats(target_user_id, sid) if sid else {},
            "knowledge_gaps": get_knowledge_gaps(target_user_id, sid) if sid else [],
            "feedback": get_user_feedback_stats(target_user_id),
        }, message="ok")
    except UserProfileError as e:
        return JSONResponse(status_code=400, content={
            "success": False, "data": None, "message": str(e),
            "error_type": "invalid_request"})


@router.get("/teacher/class/overview")
async def teacher_class_overview(user_id: str = Depends(require_teacher)):
    """我名下学生的整体汇总（学生数 / 交互 / 知识点 / 强项弱项 / 平均掌握度）"""
    from services.user_profiler import UserProfileError, list_students
    try:
        mine = _my_students(user_id)
        profiles = {s.get("user_id"): s for s in list_students()}
        students = [_student_row(uid, profiles.get(uid), ctx) for uid, ctx in mine.items()]
        total = len(students)
        total_strong = sum(s.get("strong_count", 0) for s in students)
        total_weak = sum(s.get("weak_count", 0) for s in students)
        return SuccessResponse(data={
            "total_students": total,
            "total_interactions": sum(s.get("interaction_count", 0) for s in students),
            "total_knowledge_points": sum(s.get("knowledge_point_count", 0) for s in students),
            "total_weak_points": total_weak,
            "total_strong_points": total_strong,
            "average_mastery": round(
                sum(s.get("average_mastery", 0) for s in students) / total, 3) if total else 0,
            "scope": "我名下班级的在班学生",
        }, message="ok")
    except UserProfileError as e:
        return JSONResponse(status_code=400, content={
            "success": False, "data": None, "message": str(e),
            "error_type": "invalid_request"})
