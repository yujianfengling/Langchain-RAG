# api/schemas/response_schema.py
# 统一响应格式

from typing import Any, Optional
from pydantic import BaseModel


class SuccessResponse(BaseModel):
    """成功响应格式"""
    success: bool = True
    data: Any = None
    message: str = "ok"


class ErrorResponse(BaseModel):
    """失败响应格式"""
    success: bool = False
    data: Any = None
    message: str


class BaseResponse(BaseModel):
    """基础响应格式"""
    success: bool
    data: Any = None
    message: str