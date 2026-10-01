"""
教师端权限判定（2026-09-26）

原则：
1. **一切以下都以后端判定为准**，前端菜单仅用于展示。
2. 课程资源（课程、班级、资料、教案、作业、学情）只有三种人能碰：
   - 授课教师（`courses.owner_user_id`）
   - 该课程在班学生（`class_members.status='active'`）——只读，且只读"发给学生"的内容
   - 管理员（`ADMIN_USERS` 白名单）
3. 历史全局学科（没有归属记录的）保持既有行为：登录即可读，写沿用既有白名单；
   **不会**被自动算作某位教师的课程。

本模块只做判定与抛出 `TeachingError`，不负责 HTTP 映射（由路由层统一转换）。
"""

from typing import Any, Dict, List, Optional

from services import auth_service
from services import teaching_data as td
from services.teaching_data import (
    VISIBILITY_STUDENTS, VISIBILITY_TEACHER, TeachingError,
)


# ==================== 学科归属 ====================

def subject_scope(subject_id: str) -> Dict[str, Any]:
    """
    判断学科是否已被课程"认领"：
    - owned:  有归属记录（受课程权限约束）
    - legacy: 历史全局学科（无归属记录，保持既有行为）
    """
    owner = td.subject_owner(subject_id)
    if owner:
        return {"scope": "owned", "subject_id": subject_id,
                "course_id": owner["course_id"], "owner_user_id": owner["owner_user_id"]}
    return {"scope": "legacy", "subject_id": subject_id, "course_id": None, "owner_user_id": None}


def is_admin(user_id: Optional[str]) -> bool:
    return auth_service.is_admin(user_id)


def can_read_subject(user_id: Optional[str], subject_id: str) -> bool:
    """读学科（资料清单/下载/图谱/统计）"""
    bound = subject_scope(subject_id)
    if bound["scope"] == "legacy":
        return True  # 历史资料：登录用户可读（与既有行为一致）
    if is_admin(user_id):
        return True
    if user_id and user_id == bound["owner_user_id"]:
        return True
    if user_id and bound["course_id"] in td.active_course_ids_for_user(user_id):
        return True
    return False


def can_write_subject(user_id: Optional[str], subject_id: str) -> bool:
    """写学科（上传/删除资料、重建索引、生成图谱）"""
    bound = subject_scope(subject_id)
    if bound["scope"] == "legacy":
        return False  # 由调用方回退到既有 knowledge admin 白名单
    if is_admin(user_id):
        return True
    return bool(user_id) and user_id == bound["owner_user_id"]


def require_subject_read(user_id: Optional[str], subject_id: str) -> None:
    if not can_read_subject(user_id, subject_id):
        raise TeachingError("你没有权限访问该课程的资料", "forbidden")


def require_subject_write(user_id: Optional[str], subject_id: str) -> bool:
    """
    返回 True 表示"已按课程归属校验通过"；
    返回 False 表示该学科是历史全局学科（调用方应回退既有 admin 白名单）。
    不通过时直接抛 forbidden。
    """
    bound = subject_scope(subject_id)
    if bound["scope"] == "legacy":
        return False
    if not can_write_subject(user_id, subject_id):
        raise TeachingError("只有本课程的授课教师可以修改该课程资料", "forbidden")
    return True


# ==================== 课程 / 班级 ====================

def get_course_or_404(course_id: str) -> Dict[str, Any]:
    course = td.get_course(course_id)
    if not course:
        raise TeachingError("课程不存在", "not_found")
    return course


def is_course_teacher(user_id: Optional[str], course_id: str) -> bool:
    if not user_id:
        return False
    course = td.get_course(course_id)
    if not course:
        return False
    if course["owner_user_id"] == user_id:
        return True
    return is_admin(user_id)


def require_course_teacher(user_id: Optional[str], course_id: str) -> Dict[str, Any]:
    """只有授课教师（或管理员）能管理课程"""
    course = get_course_or_404(course_id)
    if not is_course_teacher(user_id, course_id):
        raise TeachingError("你不是该课程的授课教师，无权操作", "forbidden")
    return course


def require_course_read(user_id: Optional[str], course_id: str) -> Dict[str, Any]:
    """教师或本课程在班学生可读"""
    course = get_course_or_404(course_id)
    if is_course_teacher(user_id, course_id):
        return course
    if user_id and course_id in td.active_course_ids_for_user(user_id):
        return course
    raise TeachingError("你不在该课程中，无权查看", "forbidden")


def require_class_teacher(user_id: Optional[str], class_id: str) -> Dict[str, Any]:
    klass = td.get_class(class_id)
    if not klass:
        raise TeachingError("班级不存在", "not_found")
    require_course_teacher(user_id, klass["course_id"])
    return klass


def require_class_read(user_id: Optional[str], class_id: str) -> Dict[str, Any]:
    klass = td.get_class(class_id)
    if not klass:
        raise TeachingError("班级不存在", "not_found")
    if is_course_teacher(user_id, klass["course_id"]):
        return klass
    if user_id and td.is_class_member(class_id, user_id):
        return klass
    raise TeachingError("你不在该班级中，无权查看", "forbidden")


def require_class_member(user_id: Optional[str], class_id: str) -> Dict[str, Any]:
    """学生视角：必须在班"""
    klass = td.get_class(class_id)
    if not klass:
        raise TeachingError("班级不存在", "not_found")
    if not user_id or not td.is_class_member(class_id, user_id):
        raise TeachingError("你尚未加入该班级", "forbidden")
    return klass


def student_class_ids(user_id: str) -> List[str]:
    return td.active_class_ids_for_user(user_id)


def teacher_course_ids(user_id: str) -> List[str]:
    return td.active_course_ids_for_teacher(user_id)


# ==================== 资料可见性 ====================

def visible_materials(user_id: Optional[str], course_id: str) -> List[Dict[str, Any]]:
    """
    资料可见范围：教师可见全部（含 teacher_only 草稿）；
    学生只看 visibility='students' 且状态可用的资料。
    """
    if is_course_teacher(user_id, course_id):
        return td.list_materials(course_id, include_teacher_only=True)
    if user_id and course_id in td.active_course_ids_for_user(user_id):
        return [m for m in td.list_materials(course_id, include_teacher_only=False)]
    raise TeachingError("你不在该课程中，无权查看资料", "forbidden")


def can_use_material_in_teaching(user_id: Optional[str], course_id: str,
                                material: Dict[str, Any]) -> bool:
    """教师能否在教学（教案/作业）中引用该资料"""
    if not is_course_teacher(user_id, course_id):
        return False
    return material.get("course_id") == course_id


def student_visible_document_names(user_id: Optional[str], subject_id: str) -> Optional[set]:
    """
    某学科下"该用户可见的文件名"集合；返回 None 表示不做文件名级过滤。

    为什么需要：既有 `GET /knowledge/{sid}/documents` 与文档下载接口只按"学科可读"判断，
    而课程资料里有 `teacher_only`（教师草稿）——在班学生本来能读该学科，
    于是会把教师的草稿资料也列出来/下载到。这里补上**文件级**过滤：
    - 授课教师 / 管理员 → None（全部可见）
    - 在班学生 → 只允许 visibility='students' 的文件
    - 历史全局学科（无课程归属）→ None（保持既有行为，不改变原有语义）
    """
    bound = subject_scope(subject_id)
    if bound["scope"] == "legacy":
        return None
    if is_course_teacher(user_id, bound["course_id"]) or is_admin(user_id):
        return None
    if not user_id or bound["course_id"] not in td.active_course_ids_for_user(user_id):
        # 无权限者的可见集合为空（读权限校验会先拦下，这里兜底）
        return set()
    return {m["filename"] for m in td.list_materials(bound["course_id"],
                                                     include_teacher_only=False)}
