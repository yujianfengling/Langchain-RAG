"""
教师端业务服务（2026-09-26）：课程、班级、成员、课程资料

设计要点：
- 一门课程 = 一位授课教师 + 一个**专属学科**（知识库按学科隔离，天然形成课程资料边界）
- 学生通过班级邀请码加入；加入/退班/移除一律留痕（不物理删除）
- 课程资料复用既有 `knowledge_service` 的上传/删除/重建能力，**不接受客户端传路径**
- 历史全局学科不受影响，也**不会**被自动归属给任何教师
"""

import os
import re
from typing import Any, Dict, List, Optional

from utils.logger import logger

from services import auth_service, teaching_access as access
from services import teaching_data as td
from services.teaching_data import (
    VISIBILITY_STUDENTS, VISIBILITY_TEACHER, TeachingError,
)

# 学科 ID 沿用既有正则约束（中文/英文/数字/下划线/连字符），避免影响 knowledge_service
_SUBJECT_ID_RE = re.compile(r"^[\w一-鿿-]+$")

# 加入班级的失败尝试限制（防暴力猜码）：窗口内失败达到上限即暂时拒绝
JOIN_ATTEMPT_LIMIT = int(os.getenv("JOIN_ATTEMPT_LIMIT", "10"))
JOIN_ATTEMPT_WINDOW_MINUTES = int(os.getenv("JOIN_ATTEMPT_WINDOW_MINUTES", "10"))


def _course_subject_id(course_id: str, title: str) -> str:
    """课程专属学科 ID：course_<短id>（保证唯一、可读、符合既有正则）"""
    return f"course_{course_id.split('_', 1)[-1][:10]}"


def _subject_name(course_title: str) -> str:
    return f"{course_title}（课程知识库）"


# ==================== 课程 ====================

def create_course(user_id: str, title: str, description: Optional[str] = None,
                  subject_name: Optional[str] = None,
                  teacher_invite_code: Optional[str] = None) -> Dict[str, Any]:
    """
    创建课程

    **权限（2026-09-27 修复）**：必须已经是教师（环境白名单 / 邀请码开通 / 管理员授予），
    或本次调用同时携带**有效的教师邀请码**（凭码开通，邀请码会被记一次使用）。

    此前这里会自动把创建者提升为 teacher —— 那意味着任何注册用户都能自助获得
    教师权限（可访问教师工作台、建班级、看学情），属于提权漏洞，已移除。

    同时为该课程创建一个独立的学科，用于承载课程资料的知识库。
    """
    title = (title or "").strip()
    if not title:
        raise TeachingError("课程名称不能为空", "invalid_request")

    role = auth_service.get_role(user_id)
    if role not in (auth_service.ROLE_TEACHER, auth_service.ROLE_ADMIN):
        # 未开通教师身份：只接受**有效邀请码**（受控通道），不允许自助提权
        code = (teacher_invite_code or "").strip()
        if not code:
            raise TeachingError(
                "创建课程需要教师身份。请向管理员或已有教师索取**教师邀请码**，"
                "在设置页开通后再创建课程。", "teacher_required")
        redeemed = auth_service.redeem_role_invite(code, user_id)
        if not redeemed.get("ok"):
            raise TeachingError(f"教师邀请码不可用：{redeemed.get('reason')}",
                                "invalid_invite")
        td.audit(None, user_id, "role.redeem", "user", user_id,
                 {"code": code[:2] + "***", "role": auth_service.ROLE_TEACHER})
        logger.info(f"通过邀请码开通教师身份并创建课程: user={user_id}")

    course_id = td.new_id("course")
    subject_id = _course_subject_id(course_id, title)
    if not _SUBJECT_ID_RE.match(subject_id):  # 防御性检查
        raise TeachingError("课程标识生成失败", "invalid_request")

    # 1) 学科（知识库容器）
    from services import subject_service
    try:
        subject_service.create_subject(subject_id, subject_name or _subject_name(title),
                                       description=f"由课程《{title}》自动创建")
    except subject_service.SubjectServiceError as e:
        raise TeachingError(f"课程知识库创建失败：{e}", "conflict")

    # 2) 课程（含学科归属登记）
    try:
        course = td.create_course(title=title, owner_user_id=user_id,
                                  subject_id=subject_id, description=description)
    except TeachingError:
        # 回滚学科，避免留下无主学科
        try:
            subject_service.delete_subject(subject_id)
        except Exception as e:
            logger.warning(f"回滚学科失败: {e}")
        raise

    # 3) 审计（**不再**在此授予教师角色：身份必须在进入本函数前就已受控开通）
    td.audit(course["id"], user_id, "course.create", "course", course["id"],
             {"title": title, "subject_id": subject_id,
              "role_source": auth_service.teacher_grant_source(user_id)})
    logger.info(f"创建课程: id={course['id']} owner={user_id} subject={subject_id}")
    return {**course, "my_role": "teacher"}


def list_teacher_courses(user_id: str) -> List[Dict[str, Any]]:
    out = []
    for c in td.list_courses_by_owner(user_id):
        classes = td.list_classes(c["id"])
        materials = td.list_materials(c["id"], include_teacher_only=True)
        drafts = td.list_lesson_plans(c["id"], status="draft")
        published = td.list_lesson_plans(c["id"], status="published")
        out.append({
            **c,
            "class_count": len(classes),
            "student_count": sum(int(k.get("member_count") or 0) for k in classes),
            "material_count": len(materials),
            "draft_plan_count": len(drafts),
            "published_plan_count": len(published),
            "published_assignment_count": len(
                td.list_assignments(course_id=c["id"], status="published")),
            "draft_assignment_count": len(
                td.list_assignments(course_id=c["id"], status="draft")),
        })
    return out


def get_course_overview(user_id: str, course_id: str) -> Dict[str, Any]:
    course = access.require_course_teacher(user_id, course_id)
    classes = td.list_classes(course_id)
    materials = td.list_materials(course_id, include_teacher_only=True)
    plans = td.list_lesson_plans(course_id)
    assignments = td.list_assignments(course_id=course_id)
    kb = _kb_status(course["subject_id"])
    return {
        **course,
        "my_role": "teacher",
        "classes": classes,
        "materials": [_material_public(m) for m in materials],
        "lesson_plans": plans,
        "assignments": assignments,
        "knowledge_base": kb,
        "counts": {
            "classes": len(classes),
            "students": sum(int(k.get("member_count") or 0) for k in classes),
            "materials": len(materials),
            "draft_plans": len([p for p in plans if p["status"] == "draft"]),
            "published_plans": len([p for p in plans if p["status"] == "published"]),
            "draft_assignments": len([a for a in assignments if a["status"] == "draft"]),
            "published_assignments": len([a for a in assignments if a["status"] == "published"]),
        },
    }


def _kb_status(subject_id: str) -> Dict[str, Any]:
    """知识库可用状态（供前端明确展示"未构建/可用/失败"）"""
    try:
        from services import knowledge_service
        stats = knowledge_service.get_knowledge_base_stats(subject_id)
        docs = knowledge_service.get_subject_documents(subject_id)
        return {
            "subject_id": subject_id,
            "available": True,
            "stats": stats,
            "document_count": len(docs),
            "error": None,
        }
    except Exception as e:
        return {"subject_id": subject_id, "available": False, "stats": None,
                "document_count": 0, "error": str(e)}


# ==================== 班级与成员 ====================

def create_class(user_id: str, course_id: str, name: str) -> Dict[str, Any]:
    access.require_course_teacher(user_id, course_id)
    klass = td.create_class(course_id, name, created_by=user_id)
    td.audit(course_id, user_id, "class.create", "class", klass["id"], {"name": name})
    return klass


def list_classes(user_id: str, course_id: str) -> List[Dict[str, Any]]:
    access.require_course_teacher(user_id, course_id)
    return td.list_classes(course_id)


def reset_join_code(user_id: str, class_id: str) -> Dict[str, Any]:
    klass = access.require_class_teacher(user_id, class_id)
    new_klass = td.reset_join_code(class_id)
    td.audit(klass["course_id"], user_id, "class.reset_code", "class", class_id, {})
    return new_klass


def revoke_join_code(user_id: str, class_id: str) -> Dict[str, Any]:
    """撤销班级邀请码（班级临时停止加入；可再次轮换出新码）"""
    klass = access.require_class_teacher(user_id, class_id)
    updated = td.revoke_join_code(class_id)
    td.audit(klass["course_id"], user_id, "class.revoke_code", "class", class_id, {})
    return updated


def join_code_status(user_id: str, class_id: str) -> Dict[str, Any]:
    """邀请码状态（教师端展示：是否有效、何时过期、已用次数）"""
    klass = access.require_class_teacher(user_id, class_id)
    reason = td.join_code_invalid_reason(klass)
    return {
        "class_id": class_id,
        "join_code": klass.get("join_code"),
        "valid": reason is None,
        "invalid_reason": reason,
        "expires_at": klass.get("join_code_expires_at"),
        "revoked_at": klass.get("join_code_revoked_at"),
        "used_count": int(klass.get("join_code_uses") or 0),
        "max_uses": int(klass.get("join_code_max_uses") or 0),
    }


def list_members(user_id: str, class_id: str) -> Dict[str, Any]:
    """成员列表（含用户显示名；不暴露密码等敏感字段）"""
    klass = access.require_class_teacher(user_id, class_id)
    members = []
    for m in td.list_members(class_id, status=None):
        user = auth_service.get_user(m["user_id"]) or {}
        members.append({
            "member_id": m["id"],
            "user_id": m["user_id"],
            "display_name": user.get("display_name") or user.get("username") or m["user_id"],
            "username": user.get("username"),
            "status": m["status"],
            "joined_at": m["joined_at"],
            "left_at": m["left_at"],
            "removed_at": m["removed_at"],
        })
    return {"class": klass, "members": members,
            "active_count": len([m for m in members if m["status"] == "active"]),
            "events": td.list_member_events(class_id)}


def remove_member(user_id: str, class_id: str, member_id: str,
                  reason: Optional[str] = None) -> Dict[str, Any]:
    klass = access.require_class_teacher(user_id, class_id)
    member = td.get_member(member_id)
    if not member:
        raise TeachingError("成员记录不存在", "not_found")
    if member.get("class_id") != class_id:
        # 必须在任何状态写入之前验证归属，避免跨班移除已经生效后才报错。
        raise TeachingError("成员不属于该班级", "invalid_request")
    member = td.set_member_status(member_id, "removed", actor_user_id=user_id, reason=reason)
    td.audit(klass["course_id"], user_id, "member.remove", "member", member_id,
             {"user_id": member["user_id"], "reason": reason})
    return member


# ==================== 学生侧：加入 / 我的课程 / 退课 ====================

def join_class_by_code(user_id: str, code: str) -> Dict[str, Any]:
    """
    学生用班级邀请码加入（首版不接入学校统一身份认证）

    有效性由程序判定：存在 / 未撤销 / 未过期 / 未超出可用次数；
    并做**失败尝试频率限制**（防止暴力猜测 6 位码）。
    """
    normalized = (code or "").strip().upper()
    # 1) 频率限制：窗口内失败次数过多则直接拒绝（不再校验码，避免被当作探测工具）
    failures = td.recent_join_failures(user_id, JOIN_ATTEMPT_WINDOW_MINUTES)
    if failures >= JOIN_ATTEMPT_LIMIT:
        td.record_join_attempt(user_id, normalized, False, "rate_limited")
        raise TeachingError(
            f"尝试次数过多，请 {JOIN_ATTEMPT_WINDOW_MINUTES} 分钟后再试"
            f"（如需帮助请联系任课教师）", "too_many_attempts")

    klass = td.get_class_by_join_code(normalized)
    reason = td.join_code_invalid_reason(klass)
    if reason:
        td.record_join_attempt(user_id, normalized, False, reason)
        # 对外统一为"邀请码无效或已失效"，不泄露是哪一步失败（避免枚举）
        raise TeachingError(f"{reason}" if klass else "邀请码无效或已失效", "not_found")

    already = td.is_class_member(klass["id"], user_id)
    member = td.add_member(klass["id"], klass["course_id"], user_id, source="code")
    course = td.get_course(klass["course_id"]) or {}
    td.record_join_attempt(user_id, normalized, True, "joined" if not already else "already")
    if not already:
        td.increment_join_code_uses(klass["id"])
        td.audit(klass["course_id"], user_id, "member.join", "class", klass["id"],
                 {"source": "code"})
        logger.info(f"学生加入班级: user={user_id} class={klass['id']}")
    return {"class": td.get_class(klass["id"]), "course": course,
            "member": member, "already_member": already}


def list_my_courses(user_id: str) -> List[Dict[str, Any]]:
    """学生视角：我加入的课程与班级（含该课程已发布的作业数）"""
    out = []
    for class_id in td.active_class_ids_for_user(user_id):
        klass = td.get_class(class_id)
        if not klass:
            continue
        course = td.get_course(klass["course_id"])
        if not course or course["status"] != "active":
            continue
        my_member = next(
            (m for m in td.list_members(class_id, status="active") if m["user_id"] == user_id),
            None)
        teacher = auth_service.get_user(course["owner_user_id"]) or {}
        assignments = td.list_assignments_for_classes([class_id], status="published")
        out.append({
            "course_id": course["id"],
            "title": course["title"],
            "description": course["description"],
            "subject_id": course["subject_id"],
            "teacher_name": teacher.get("display_name") or teacher.get("username") or "教师",
            "class_id": klass["id"],
            "class_name": klass["name"],
            "joined_at": (my_member or {}).get("joined_at"),
            "assignment_count": len(assignments),
        })
    return out


def leave_class(user_id: str, class_id: str) -> Dict[str, Any]:
    """学生主动退班（留痕；不删除历史作答记录）"""
    klass = td.get_class(class_id)
    if not klass:
        raise TeachingError("班级不存在", "not_found")
    members = [m for m in td.list_members(class_id, status="active") if m["user_id"] == user_id]
    if not members:
        raise TeachingError("你不在该班级中", "forbidden")
    member = td.set_member_status(members[0]["id"], "left", actor_user_id=user_id,
                                  reason="学生主动退班")
    return member


# ==================== 课程资料 ====================

def _material_public(material: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("id", "course_id", "subject_id", "filename", "size_bytes", "status",
            "error_message", "visibility", "uploaded_by", "created_at", "updated_at")
    return {k: material.get(k) for k in keep}


def upload_material(user_id: str, course_id: str, file_content: bytes,
                    filename: str, visibility: str = VISIBILITY_STUDENTS) -> Dict[str, Any]:
    """
    上传课程资料（写入该课程专属学科的知识库）

    安全：只接受**文件内容 + 文件名**，服务端自行决定落盘位置；
    不接受客户端提供的任何服务器路径（避免任意目录构建知识库）。
    """
    course = access.require_course_teacher(user_id, course_id)
    if visibility not in (VISIBILITY_STUDENTS, VISIBILITY_TEACHER):
        raise TeachingError("可见范围只能是 students / teacher_only", "invalid_request")

    from services import knowledge_service
    ok, msg = knowledge_service.validate_filename(filename)
    if not ok:
        raise TeachingError(f"文件名不合法：{msg}", "invalid_request")
    ok, msg = knowledge_service.validate_file_size(file_content)
    if not ok:
        raise TeachingError(msg, "invalid_request")

    try:
        result = knowledge_service.upload_file_to_subject(
            subject_id=course["subject_id"], file_content=file_content, filename=filename)
    except Exception as e:
        td.upsert_material(course_id, course["subject_id"], filename, size_bytes=len(file_content),
                           status="failed", error_message=str(e), visibility=visibility,
                           uploaded_by=user_id)
        td.audit(course_id, user_id, "material.upload_failed", "material", filename,
                 {"error": str(e)[:200]})
        raise TeachingError(f"资料上传失败：{e}", "upload_failed")

    material = td.upsert_material(
        course_id, course["subject_id"], filename,
        size_bytes=int(result.get("size") or len(file_content)),
        status="ready", error_message=None, visibility=visibility, uploaded_by=user_id)
    td.audit(course_id, user_id, "material.upload", "material", material["id"],
             {"filename": filename, "visibility": visibility})
    logger.info(f"课程资料上传: course={course_id} file={filename}")

    # 上传后**必须重建课程知识库**（2026-10-05 修复）：
    # 此前只在"知识库尚未就绪"时重建——若课程已有可用知识库，新上传的资料会写进
    # 学科目录、资料列表也显示"已就绪"，却**从未进入 FAISS/BM25 索引**，表现为
    # "学生基于课程资料提问永远检索不到新资料"。正确做法是每次上传后重建索引；
    # 失败不静默，随响应一起返回原因（前端会提示"已上传但构建失败，可重试"）。
    kb_rebuild = {"attempted": True, "ok": None, "error": None}
    try:
        knowledge_service.rebuild_subject_knowledge(course["subject_id"])
        kb_rebuild["ok"] = True
    except Exception as e:
        kb_rebuild["ok"] = False
        kb_rebuild["error"] = str(e)[:200]
        logger.warning(f"上传后重建知识库失败: course={course_id} err={e}")
    kb = _kb_status(course["subject_id"])
    td.audit(course_id, user_id,
             "material.kb_rebuild_ok" if kb_rebuild["ok"] else "material.kb_rebuild_failed",
             "course", course_id, {"error": kb_rebuild["error"]})
    return {**_material_public(material), "kb": kb, "kb_rebuild": kb_rebuild}


def list_course_materials(user_id: str, course_id: str) -> Dict[str, Any]:
    materials = access.visible_materials(user_id, course_id)
    course = td.get_course(course_id) or {}
    return {"materials": [_material_public(m) for m in materials],
            "knowledge_base": _kb_status(course.get("subject_id", ""))}


def delete_material(user_id: str, course_id: str, material_id: str) -> Dict[str, Any]:
    course = access.require_course_teacher(user_id, course_id)
    material = td.get_material(material_id)
    if not material or material["course_id"] != course_id:
        raise TeachingError("资料不存在", "not_found")

    from services import knowledge_service
    result: Dict[str, Any] = {"deleted_file": False, "rebuild": None}
    try:
        knowledge_service.delete_subject_document(course["subject_id"], material["filename"])
        result["deleted_file"] = True
    except Exception as e:
        # 文件可能已不存在：不阻断记录清理，但要如实告知
        result["file_error"] = str(e)

    td.delete_material(material_id)
    td.audit(course_id, user_id, "material.delete", "material", material_id,
             {"filename": material["filename"]})
    # 删除后重建索引，保证检索与资料清单一致
    try:
        from services import knowledge_service as ks
        result["rebuild"] = ks.rebuild_subject_knowledge(course["subject_id"])
    except Exception as e:
        result["rebuild_error"] = str(e)
    return {**result, "material_id": material_id}


def rebuild_course_knowledge(user_id: str, course_id: str) -> Dict[str, Any]:
    """重建课程知识库（教师可操作；带失败原因）"""
    course = access.require_course_teacher(user_id, course_id)
    from services import knowledge_service
    try:
        result = knowledge_service.rebuild_subject_knowledge(course["subject_id"])
        td.audit(course_id, user_id, "material.rebuild", "course", course_id, {})
        return {"ok": True, "result": result, "kb": _kb_status(course["subject_id"])}
    except Exception as e:
        td.audit(course_id, user_id, "material.rebuild_failed", "course", course_id,
                 {"error": str(e)[:200]})
        return {"ok": False, "error": str(e), "kb": _kb_status(course["subject_id"])}


def set_material_visibility(user_id: str, course_id: str, material_id: str,
                            visibility: str) -> Dict[str, Any]:
    access.require_course_teacher(user_id, course_id)
    material = td.get_material(material_id)
    if not material or material["course_id"] != course_id:
        raise TeachingError("资料不存在", "not_found")
    updated = td.set_material_visibility(material_id, visibility)
    td.audit(course_id, user_id, "material.visibility", "material", material_id,
             {"visibility": visibility})
    return _material_public(updated)


__all__ = [
    "create_course", "list_teacher_courses", "get_course_overview",
    "create_class", "list_classes", "reset_join_code", "revoke_join_code",
    "join_code_status",
    "list_members", "remove_member",
    "join_class_by_code", "list_my_courses", "leave_class",
    "upload_material", "list_course_materials", "delete_material",
    "rebuild_course_knowledge", "set_material_visibility",
]
