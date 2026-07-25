# api/routes/__init__.py
# 路由模块

from .chat import router as chat_router
from .knowledge import router as knowledge_router
from .subjects import router as subjects_router
from .models import router as models_router

__all__ = ["chat_router", "knowledge_router", "subjects_router", "models_router"]