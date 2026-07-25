# api/routes/models.py
# 模型管理接口路由

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.schemas.response_schema import SuccessResponse
from services.model_service import (
    get_current_model,
    get_available_models,
    check_model_status,
    switch_model,
    ModelServiceError,
)

router = APIRouter()


@router.get("/models")
async def get_models():
    """
    获取当前模型信息
    
    返回当前配置的LLM模型信息
    """
    try:
        model_info = get_current_model()
        
        return SuccessResponse(
            data=model_info,
            message="ok",
        )
    except ModelServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取模型信息失败: {str(e)}")


@router.get("/models/status")
async def get_model_status():
    """
    获取模型状态
    
    检查LLM服务是否可用
    """
    try:
        status = check_model_status()
        
        return SuccessResponse(
            data=status,
            message="ok",
        )
    except ModelServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取模型状态失败: {str(e)}")


@router.get("/models/available")
async def get_available_models_info():
    """
    获取可用模型信息
    
    返回所有可用的LLM模型信息
    """
    try:
        available_models = get_available_models()
        
        return SuccessResponse(
            data=available_models,
            message="ok",
        )
    except ModelServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取可用模型信息失败: {str(e)}")


class SwitchModelRequest(BaseModel):
    provider: str


@router.post("/models/switch")
async def switch_model_provider(request: SwitchModelRequest):
    """
    切换模型提供商
    
    请求体:
        {"provider": "ollama"}
    
    返回:
        {"success": true, "message": "模型切换成功"}
    """
    try:
        result = switch_model(request.provider)
        
        return SuccessResponse(
            data=result,
            message=result.get("message", "模型切换成功"),
        )
    except ModelServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"切换模型失败: {str(e)}")