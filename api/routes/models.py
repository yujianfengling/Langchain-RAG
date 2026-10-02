# api/routes/models.py
# 模型管理接口路由

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.schemas.response_schema import SuccessResponse
from services.model_service import (
    get_model_provider,
    get_available_models,
    get_model_config,
    get_default_model,
    MODEL_PROVIDERS,
)
from config.models import get_model_api_key

router = APIRouter()


@router.get("/models")
async def get_models():
    """
    获取当前模型信息
    
    返回当前配置的LLM模型信息
    """
    try:
        default_model = get_default_model()
        config = get_model_config(default_model)
        
        return SuccessResponse(
            data={
                "provider": default_model,
                "model": config.get("model_name", default_model),
                "display_name": config.get("name", default_model),
                "base_url": config.get("base_url", ""),
                "has_api_key": bool(get_model_api_key(default_model)),
            },
            message="ok",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取模型信息失败: {str(e)}")


@router.get("/models/status")
async def get_model_status():
    """
    获取模型状态
    
    检查LLM服务是否可用
    """
    try:
        # 检查所有模型的状态
        models_status = []
        for model_name in MODEL_PROVIDERS.keys():
            try:
                provider = get_model_provider(model_name)
                config = provider.config
                api_key = get_model_api_key(model_name)
                models_status.append({
                    "name": model_name,
                    "display_name": config.get("name", model_name),
                    "has_api_key": bool(api_key),
                    "available": bool(api_key),
                })
            except Exception as e:
                models_status.append({
                    "name": model_name,
                    "display_name": model_name,
                    "has_api_key": False,
                    "available": False,
                    "error": str(e),
                })
        
        return SuccessResponse(
            data={
                "default_model": get_default_model(),
                "models": models_status,
            },
            message="ok",
        )
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取可用模型信息失败: {str(e)}")


class SwitchModelRequest(BaseModel):
    model: str


@router.post("/models/switch")
async def switch_model_provider(request: SwitchModelRequest):
    """
    切换模型提供商
    
    请求体:
        {"model": "deepseek"}

    说明：仅影响本次调用方的后续请求（由各请求的 model 参数决定），
    不改动后端默认文本模型（默认 DeepSeek，见 config/models.DEFAULT_MODEL）
    
    返回:
        {"success": true, "message": "模型切换成功"}
    """
    try:
        # 验证模型是否支持
        if request.model not in MODEL_PROVIDERS:
            raise HTTPException(
                status_code=400, 
                detail=f"不支持的模型: {request.model}，支持的模型: {list(MODEL_PROVIDERS.keys())}"
            )
        
        # 获取模型配置
        config = get_model_config(request.model)
        api_key = get_model_api_key(request.model)

        display = config.get("name", request.model)
        # 文案诚实化（2026-10-02）：本接口**不改动后端默认模型**，只校验该提供方
        # 是否已配置可用。此前返回"模型切换成功"，而实际上什么都没切
        # （前端选择器刷新后仍显示旧模型），属于会误导人的说法。
        return SuccessResponse(
            data={
                "model": request.model,
                "display_name": display,
                "has_api_key": bool(api_key),
                "applied_to_default": False,
                "message": f"已选择 {display}（不改动后端默认模型）",
            },
            message=f"已选择 {display}（不改动后端默认模型）",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"切换模型失败: {str(e)}")