# services/__init__.py
# 业务服务层
#
# 启动优化（2026-10-03，实测驱动）：本文件此前在**包导入时**就 eager 导入
# knowledge_service / subject_service / model_service，把
# `services.knowledge_service → rag（torch/transformers）` 拉进启动路径
# （实测 sentence_transformers 单独导入就需 7.6s）。
#
# 改为 PEP 562 模块级惰性属性：只有真正访问某个名字时才导入其所在子模块。
# 对外接口（名字与所属模块）与原来完全一致，`__all__` 未变；
# `from services import auth_service` 这类子模块导入照常工作。

import importlib
from typing import Any

# 名字 → 所属子模块（与原来的 eager 导入一一对应）
_NAME_TO_MODULE = {
    # 知识库服务
    "build_subject_knowledge": "knowledge_service",
    "delete_subject_knowledge": "knowledge_service",
    "list_knowledge_bases": "knowledge_service",
    "get_knowledge_base_stats": "knowledge_service",
    # 学科服务
    "get_subjects": "subject_service",
    "get_subject": "subject_service",
    "create_subject": "subject_service",
    "delete_subject": "subject_service",
    # 模型服务
    "get_model_provider": "model_service",
    "get_available_models": "model_service",
    "get_model_config": "model_service",
    "get_default_model": "model_service",
    "MODEL_PROVIDERS": "model_service",
}


def __getattr__(name: str) -> Any:
    """按需导入（PEP 562）：首次访问某名字时才 import 它的子模块

    未知名字抛 AttributeError —— 这样 `from services import <子模块>` 仍然有效
    （Python 的 fromlist 处理会回退为导入子模块）。
    """
    module_name = _NAME_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    globals()[name] = value      # 缓存，后续访问不再走 __getattr__
    return value


def __dir__():
    return sorted(set(list(globals().keys()) + list(_NAME_TO_MODULE.keys())))


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
    "get_model_provider",
    "get_available_models",
    "get_model_config",
    "get_default_model",
    "MODEL_PROVIDERS",
]
