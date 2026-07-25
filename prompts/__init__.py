"""
提示词模板模块
提供各种场景的提示词模板
"""

from .education_prompt import (
    get_education_system_prompt,
    get_education_user_prompt,
    get_education_prompt_with_context,
    get_education_prompt_with_history
)

# 公开API
__all__ = [
    "get_education_system_prompt",
    "get_education_user_prompt",
    "get_education_prompt_with_context",
    "get_education_prompt_with_history"
]