# api/routes/subjects.py
# 学科管理接口路由

from fastapi import APIRouter, HTTPException
from typing import List

from api.schemas.knowledge_schema import SubjectInfo
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
async def list_subjects():
    """
    获取学科列表
    
    从学科服务获取所有学科信息
    """
    try:
        subjects_data = get_subjects()
        
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
async def get_subject_info(subject_id: str):
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
async def create_new_subject(subject_id: str, name: str, description: str = None):
    """
    创建新学科
    
    - **subject_id**: 学科ID
    - **name**: 学科名称
    - **description**: 学科描述（可选）
    """
    try:
        new_subject = create_subject(subject_id, name, description)
        
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
async def delete_existing_subject(subject_id: str):
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