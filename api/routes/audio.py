# api/routes/audio.py
# 语音接口（2026-10-01：只保留语音识别 ASR；回答朗读 TTS 已整体移除）
#
# 端点：
#   POST /audio/transcribe   录音 → 文本（本地 ASR 优先；语音输入用）
#   POST /audio/warmup       预热本地 ASR 模型（首次加载较慢；不会联网下载）
#   GET  /audio/status       语音能力状态（本地 ASR / MiMo 可选提供方）
#
# 已移除（连同前端朗读入口一起删除）：POST /audio/tts、POST /audio/speech、GET /audio/voices。

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from api.deps import require_current_user
from api.schemas.response_schema import SuccessResponse
from services.audio_service import (
    AudioServiceError,
    local_speech_status,
    mimo_speech_status,
    speech_capability,
    transcribe_audio_detailed,
    warmup_local_speech,
)

router = APIRouter()

# 错误类型 → HTTP 状态码（只保留 ASR 相关）
_AUDIO_STATUS = {
    "audio_invalid_format": 400,
    "audio_too_large": 413,
    "timeout": 504,
    "connection_error": 502,
    "response_parse_error": 502,
    "asr_api_error": 502,
    "asr_failed": 502,
    "asr_empty_result": 422,
    "audio_endpoint_not_found": 502,
    "invalid_api_key": 502,
    "unauthorized": 502,
    "rate_limited": 429,
    # 本地语音服务相关（模型未就绪 / 服务未启动 / 显存等）
    "speech_unavailable": 503,
    "engine_not_ready": 503,
    "engine_loading": 503,
    "engine_busy": 503,
    "model_missing": 503,
    "dependency_missing": 503,
    "cuda_oom": 503,
    "cuda_error": 503,
    "asr_disabled": 503,
    "model_download_failed": 503,
}


def _audio_error(e: AudioServiceError):
    status = _AUDIO_STATUS.get(e.error_type, 500)
    return JSONResponse(
        status_code=status,
        content={"success": False, "data": None,
                 "message": e.message, "error_type": e.error_type},
    )


@router.post("/audio/transcribe")
async def transcribe(file: UploadFile = File(...),
                     _user: str = Depends(require_current_user)):
    """
    语音识别（本地 ASR；SPEECH_BACKEND=mimo 时走 MiMo）

    返回：{"success": true, "data": {"text": ..., "service": "local|mimo", "engine": ...}}
    """
    audio_data = await file.read()
    try:
        result = await run_in_threadpool(transcribe_audio_detailed, audio_data, file.filename)
        return SuccessResponse(data=result, message="语音识别成功")
    except AudioServiceError as e:
        return _audio_error(e)
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": "语音识别失败，请稍后重试", "error_type": "asr_failed"},
        )


@router.post("/audio/warmup")
async def warmup(_user: str = Depends(require_current_user)):
    """预热本地 ASR 模型（只加载本地已有模型，不会联网下载）"""
    try:
        data = await run_in_threadpool(warmup_local_speech)
        return SuccessResponse(data=data, message="预热完成")
    except Exception as e:
        return JSONResponse(status_code=503, content={
            "success": False, "data": None, "error_type": "speech_unavailable",
            "message": f"本地语音服务不可用：{e}"})


@router.get("/audio/status")
async def status(probe_mimo: bool = False, _user: str = Depends(require_current_user)):
    """
    语音能力状态：本地 ASR / MiMo（可选提供方）分别报告（含不可用原因）

    probe_mimo=true 时对 MiMo 做一次探测（默认关闭，避免拖慢请求）
    """
    local = await run_in_threadpool(local_speech_status, probe_mimo)
    mimo = await run_in_threadpool(mimo_speech_status, probe_mimo)
    cap = await run_in_threadpool(speech_capability)
    return SuccessResponse(data={"capability": cap, "local": local, "mimo": mimo},
                           message="获取语音状态成功")