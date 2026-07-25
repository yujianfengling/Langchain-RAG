# api/dependencies.py
# FastAPI依赖注入

from chat_service import chat_with_ai, chat_stream
from llm import get_llm, get_llm_provider_info, check_llm_availability
from config import settings


def get_chat_service():
    """获取聊天服务函数"""
    return {
        "chat_with_ai": chat_with_ai,
        "chat_stream": chat_stream,
    }


def get_llm_service():
    """获取LLM服务函数"""
    return {
        "get_llm": get_llm,
        "get_llm_provider_info": get_llm_provider_info,
        "check_llm_availability": check_llm_availability,
    }


def get_settings():
    """获取配置设置"""
    return settings