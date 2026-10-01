# api/routes/audio.py
# 语音接口（2026-09-26：本地 ASR/TTS 为主，MiMo 为可选提供方）
#
# 端点：
#   POST /audio/transcribe   录音 → 文本（本地 FunASR 优先）
#   POST /audio/tts          文本 → 逐句音频（本地 CosyVoice 优先，**不丢句**）
#   POST /audio/speech       兼容旧调用：整段文本 → 单个 WAV
#   GET  /audio/voices       可用音色（本地预置音色）
#   POST /audio/warmup       预热本地模型（首次加载较慢）
#   GET  /audio/status       语音能力状态（本地 ASR / 本地 TTS / MiMo 分别报告）

from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.deps import require_current_user
from api.schemas.response_schema import SuccessResponse
from services.audio_service import (
    AudioServiceError,
    list_local_voices,
    local_speech_status,
    mimo_speech_status,
    speech_capability,
    synthesize_segments,
    synthesize_speech,
    transcribe_audio_detailed,
    warmup_local_speech,
)

router = APIRouter()

# 错误类型 → HTTP 状态码（旧调用方依赖这些契约，保持不变并补充本地语音的错误类型）
_AUDIO_STATUS = {
    "audio_invalid_format": 400,
    "audio_too_large": 413,
    "empty_text": 400,
    "text_too_long": 400,
    "too_many_segments": 400,
    "timeout": 504,
    "connection_error": 502,
    "response_parse_error": 502,
    "asr_api_error": 502,
    "asr_failed": 502,
    "asr_empty_result": 422,
    "tts_failed": 502,
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
    "tts_disabled": 503,
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


class SpeechRequest(BaseModel):
    """整段语音合成请求（兼容旧调用）"""
    text: str
    voice: Optional[str] = None
    style: Optional[str] = None


class TtsRequest(BaseModel):
    """逐句语音合成请求（前端朗读主路径）"""
    text: str
    voice: Optional[str] = None
    speed: float = 1.0
    # 朗读长度上限（可选）：超出时在响应里显式返回 truncated / omitted_chars
    max_chars: Optional[int] = None


@router.post("/audio/transcribe")
async def transcribe(file: UploadFile = File(...),
                     _user: str = Depends(require_current_user)):
    """
    语音识别（本地 FunASR paraformer-zh；SPEECH_BACKEND=mimo 时走 MiMo）

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


@router.post("/audio/tts")
async def tts(request: TtsRequest, _user: str = Depends(require_current_user)):
    """
    逐句语音合成：文本 → 每句一段音频（顺序由后端保证）

    前端拿到 items 后按 seq 顺序播放。服务端不做静默截断：
    超出 max_chars 的部分通过 truncated / omitted_chars 如实告知。
    """
    try:
        data = await run_in_threadpool(
            synthesize_segments, request.text, request.voice, request.speed, request.max_chars)
        return SuccessResponse(data=data, message="语音合成成功")
    except AudioServiceError as e:
        return _audio_error(e)
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": "语音合成失败，请稍后重试", "error_type": "tts_failed"},
        )


@router.post("/audio/speech")
async def speech(request: SpeechRequest, _user: str = Depends(require_current_user)):
    """
    整段语音合成（兼容旧调用）：返回 audio/wav 字节流

    长文本请优先用 /audio/tts（逐句，不丢句）；本接口超长时返回 400 text_too_long。
    """
    try:
        audio_bytes = await run_in_threadpool(
            synthesize_speech, request.text, request.voice, request.style)
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": 'inline; filename="speech.wav"'},
        )
    except AudioServiceError as e:
        return _audio_error(e)
    except Exception:
        return JSONResponse(
            status_code=502,
            content={"success": False, "data": None,
                     "message": "语音合成失败，请稍后重试", "error_type": "tts_failed"},
        )


@router.get("/audio/voices")
async def voices(_user: str = Depends(require_current_user)):
    """可用音色（本地为 CosyVoice 预置音色，可安全分发）"""
    try:
        data = await run_in_threadpool(list_local_voices)
        return SuccessResponse(data=data, message="获取音色成功")
    except Exception:
        return SuccessResponse(
            data={"voices": [], "default": "", "reason": "音色列表不可用"},
            message="获取音色失败")


@router.post("/audio/warmup")
async def warmup(_user: str = Depends(require_current_user)):
    """预热本地语音模型（首次加载需要下载/加载，可能耗时较长）"""
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
    语音能力状态：本地 ASR / 本地 TTS / MiMo 分别报告（含不可用原因）

    probe_mimo=true 时对 MiMo 做一次真实探测（默认关闭，避免拖慢请求）
    """
    local = await run_in_threadpool(local_speech_status, probe_mimo)
    mimo = await run_in_threadpool(mimo_speech_status, probe_mimo)
    cap = await run_in_threadpool(speech_capability)
    return SuccessResponse(data={"capability": cap, "local": local, "mimo": mimo},
                           message="获取语音状态成功")
