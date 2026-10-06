# api/routes/subjects.py
# 学科管理接口路由

from fastapi import APIRouter, Depends, HTTPException
from typing import List

from api.schemas.knowledge_schema import SubjectInfo
from api.deps import (
    require_current_user,
    require_subject_read, require_subject_write,
)
from api.schemas.response_schema import SuccessResponse
from services.subject_service import (
    get_subjects,
    get_subject,
    create_subject,
    delete_subject,
    SubjectServiceError,
)

router = APIRouter()


@router.get("/subjects", response_model=List[SubjectInfo])
async def list_subjects(user_id: str = Depends(require_current_user)):
    """
    获取学科列表
    
    从学科服务获取所有学科信息
    """
    try:
        subjects_data = get_subjects()
        
        # 课程资料边界：课程学科只对本课程教师/在班学生可见（历史全局学科不受影响）
        from services import teaching_access
        subjects_data = [s for s in subjects_data
                         if teaching_access.can_read_subject(user_id, s.get("id", ""))]

        # 转换为SubjectInfo列表
        subjects = []
        for subject in subjects_data:
            subjects.append(SubjectInfo(
                subject_id=subject.get("id", ""),
                name=subject.get("name", ""),
                description=subject.get("description"),
            ))
        
        return subjects
    except SubjectServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取学科列表失败: {str(e)}")


@router.get("/subjects/{subject_id}", response_model=SubjectInfo)
async def get_subject_info(subject_id: str,
                           user_id: str = Depends(require_subject_read)):
    """
    获取单个学科信息
    
    - **subject_id**: 学科ID
    """
    try:
        subject = get_subject(subject_id)
        
        return SubjectInfo(
            subject_id=subject.get("id", ""),
            name=subject.get("name", ""),
            description=subject.get("description"),
        )
    except SubjectServiceError as e:
        if "不存在" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取学科信息失败: {str(e)}")


@router.post("/subjects", response_model=SubjectInfo)
async def create_new_subject(subject_id: str, name: str, description: str = None,
                             user_id: str = Depends(require_current_user)):
    """
    创建新学科（个人学科，2026-10-07）

    产品要求"创建学科就能开始"：任何登录用户都可以创建**自己的**学科并上传资料。
    权限边界（不让个人学科变成绕过课程权限的入口）：
    - 已归属课程的学科：一律拒绝，由课程管理维护；
    - 已存在的个人学科：只有创建者本人或管理员可以重申（幂等返回）；
    - 历史全局学科（无 owner）：沿用既有管理白名单，学生不能借创建接口抢占。

    - **subject_id**: 学科ID
    - **name**: 学科名称
    - **description**: 学科描述（可选）
    """
    from services import teaching_access, auth_service
    from api.deps import knowledge_admins

    bound = teaching_access.subject_scope(subject_id)
    if bound["scope"] == "owned":
        raise HTTPException(status_code=403,
                            detail="该学科属于某门课程，请通过课程管理维护")

    existing = None
    try:
        existing = get_subject(subject_id)
    except SubjectServiceError:
        existing = None
    if existing is not None:
        owner = existing.get("owner_user_id")
        if owner and (owner == user_id or auth_service.is_admin(user_id)):
            return SubjectInfo(subject_id=existing.get("id", ""),
                               name=existing.get("name", ""),
                               description=existing.get("description"))
        if owner:
            raise HTTPException(status_code=403, detail="该学科已存在且不属于你")
        admins = knowledge_admins()
        username = (auth_service.get_user(user_id) or {}).get("username", "").lower()
        if admins and username not in admins:
            raise HTTPException(status_code=403, detail="该学科属于历史全局学科，需管理员维护")
        raise HTTPException(status_code=409, detail=f"学科ID已存在: {subject_id}")

    try:
        new_subject = create_subject(subject_id, name, description, owner_user_id=user_id)
        
        return SubjectInfo(
            subject_id=new_subject.get("id", ""),
            name=new_subject.get("name", ""),
            description=new_subject.get("description"),
        )
    except SubjectServiceError as e:
        if "已存在" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建学科失败: {str(e)}")


@router.delete("/subjects/{subject_id}")
async def delete_existing_subject(subject_id: str,
                                  user_id: str = Depends(require_subject_write)):
    """
    删除学科
    
    - **subject_id**: 学科ID
    """
    try:
        result = delete_subject(subject_id)
        
        return SuccessResponse(
            data=result,
            message=f"学科 {subject_id} 已删除",
        )
    except SubjectServiceError as e:
        if "不存在" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除学科失败: {str(e)}")