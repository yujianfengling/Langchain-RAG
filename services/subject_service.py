# services/subject_service.py
# 学科管理业务服务层

import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# 项目根目录（services/ 的上一级）
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# 学科数据文件路径
SUBJECTS_FILE = PROJECT_ROOT / "data" / "subjects.json"


class SubjectServiceError(Exception):
    """学科服务异常"""
    pass


def _load_subjects() -> List[Dict[str, Any]]:
    """
    从文件加载学科数据
    
    Returns:
        学科数据列表
        
    Raises:
        SubjectServiceError: 文件读取失败时抛出
    """
    try:
        if not SUBJECTS_FILE.exists():
            return []
        
        with open(SUBJECTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 确保返回的是列表
        if not isinstance(data, list):
            raise SubjectServiceError("学科数据文件格式错误：应为JSON数组")
        
        return data
    except json.JSONDecodeError as e:
        raise SubjectServiceError(f"学科数据文件JSON格式错误: {str(e)}")
    except Exception as e:
        raise SubjectServiceError(f"读取学科数据失败: {str(e)}")


def _save_subjects(subjects: List[Dict[str, Any]]) -> None:
    """
    将学科数据保存到文件
    
    Args:
        subjects: 学科数据列表
        
    Raises:
        SubjectServiceError: 文件写入失败时抛出
    """
    try:
        # 确保目录存在
        SUBJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        with open(SUBJECTS_FILE, "w", encoding="utf-8") as f:
            json.dump(subjects, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise SubjectServiceError(f"保存学科数据失败: {str(e)}")


def get_subjects() -> List[Dict[str, Any]]:
    """
    获取所有学科列表
    
    Returns:
        学科信息列表
    """
    try:
        return _load_subjects()
    except SubjectServiceError:
        raise
    except Exception as e:
        raise SubjectServiceError(f"获取学科列表失败: {str(e)}")


def get_subject(subject_id: str) -> Dict[str, Any]:
    """
    获取单个学科信息
    
    Args:
        subject_id: 学科ID
        
    Returns:
        学科信息字典
        
    Raises:
        SubjectServiceError: 学科不存在或读取失败时抛出
    """
    try:
        subjects = _load_subjects()
        
        for subject in subjects:
            if subject.get("id") == subject_id:
                return subject
        
        raise SubjectServiceError(f"学科不存在: {subject_id}")
    except SubjectServiceError:
        raise
    except Exception as e:
        raise SubjectServiceError(f"获取学科信息失败: {str(e)}")


def create_subject(subject_id: str, name: str, description: Optional[str] = None) -> Dict[str, Any]:
    """
    创建新学科
    
    Args:
        subject_id: 学科ID
        name: 学科名称
        description: 学科描述（可选）
        
    Returns:
        创建的学科信息
        
    Raises:
        SubjectServiceError: 创建失败时抛出
    """
    try:
        subjects = _load_subjects()
        
        # 检查学科ID是否已存在
        for subject in subjects:
            if subject.get("id") == subject_id:
                raise SubjectServiceError(f"学科ID已存在: {subject_id}")
        
        # 创建新学科
        new_subject = {
            "id": subject_id,
            "name": name,
            "description": description
        }
        
        subjects.append(new_subject)
        _save_subjects(subjects)
        
        return new_subject
    except SubjectServiceError:
        raise
    except Exception as e:
        raise SubjectServiceError(f"创建学科失败: {str(e)}")


def delete_subject(subject_id: str) -> Dict[str, Any]:
    """
    删除学科
    
    Args:
        subject_id: 学科ID
        
    Returns:
        删除结果
        
    Raises:
        SubjectServiceError: 删除失败时抛出
    """
    try:
        subjects = _load_subjects()

        # 查找并删除学科
        for i, subject in enumerate(subjects):
            if subject.get("id") == subject_id:
                deleted_subject = subjects.pop(i)
                _save_subjects(subjects)

                # 联动清理学科关联数据（FAISS/BM25/资料目录），不阻断学科记录删除
                try:
                    from services.knowledge_service import cleanup_subject_data
                    cleanup_subject_data(subject_id)
                except Exception as e:
                    logger.error(f"删除学科关联数据失败: {str(e)}")

                return {
                    "success": True,
                    "deleted_subject": deleted_subject,
                    "message": f"学科 {subject_id} 已删除"
                }

        raise SubjectServiceError(f"学科不存在: {subject_id}")
    except SubjectServiceError:
        raise
    except Exception as e:
        raise SubjectServiceError(f"删除学科失败: {str(e)}")


# 公开API
__all__ = [
    "get_subjects",
    "get_subject",
    "create_subject",
    "delete_subject",
    "SubjectServiceError"
]