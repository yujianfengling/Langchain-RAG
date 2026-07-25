# services/__init__.py
# 业务服务层

from .knowledge_service import (
    build_subject_knowledge,
    delete_subject_knowledge,
    list_knowledge_bases,
    get_knowledge_base_stats,
)

from .subject_service import (
    get_subjects,
    get_subject,
    create_subject,
    delete_subject,
)

from .model_service import (
    get_current_model,
    get_available_models,
    check_model_status,
)

__all__ = [
    # 知识库服务
    "build_subject_knowledge",
    "delete_subject_knowledge",
    "list_knowledge_bases",
    "get_knowledge_base_stats",
    
    # 学科服务
    "get_subjects",
    "get_subject",
    "create_subject",
    "delete_subject",
    
    # 模型服务
    "get_current_model",
    "get_available_models",
    "check_model_status",
]