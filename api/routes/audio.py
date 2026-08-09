# api/routes/audio.py
# MiMo 语音输入输出接口（V2.5.1 Checkpoint D）

from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.schemas.response_schema import SuccessResponse
from services.audio_service import (
    AudioServiceError,
    transcribe_audio,
    synthesize_speech,
)

router = APIRouter()

# ASR 错误类型 → HTTP 状态码映射（V2.5.1 Audio Runtime Fix 结构化错误）
_ASR_STATUS = {
    "audio_invalid_format": 400,
    "audio_too_large": 413,
    "timeout": 504,
    "connection_error": 502,
    "response_parse_error": 502,
    "asr_api_error": 502,
    "audio_endpoint_not_found": 502,
    "invalid_api_key": 502,
    "unauthorized": 502,
    "rate_limited": 429,
}


class SpeechRequest(BaseModel):
    """语音合成请求"""
    text: str
    voice: Optional[str] = None
    style: Optional[str] = None


@router.post("/audio/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """
    语音识别（mimo-v2.5-asr）

    - **file**: WAV 或 MP3 音频文件

    返回：{"success": true, "data": {"text": "识别文本"}}
    """
    audio_data = await file.read()
    try:
        # V2.5.2：transcribe_audio 为同步网络 IO，移出事件循环
        text = await run_in_threadpool(transcribe_audio, audio_data, file.filename)
        return SuccessResponse(data={"text": text}, message="语音识别成功")
    except AudioServiceError as e:
        status_code = _ASR_STATUS.get(e.error_type, 500)
        return JSONResponse(
            status_code=status_code,
            content={"success": False, "data": None,
                     "message": e.message, "error_type": e.error_type},
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"success": False, "data": None,
                     "message": "语音识别失败，请稍后重试", "error_type": "asr_api_error"},
        )


@router.post("/audio/speech")
async def speech(request: SpeechRequest):
    """
    语音合成（mimo-v2.5-tts）

    - **text**: 待合成文本
    - **voice**: 音色（可选，使用预置音色）
    - **style**: 风格（可选，预留）

    返回：audio/wav 音频字节流
    """
    try:
        # V2.5.2：synthesize_speech 为同步网络 IO，移出事件循环
        audio_bytes = await run_in_threadpool(
            synthesize_speech,
            request.text,
            request.voice,
            request.style,
        )
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": 'inline; filename="speech.wav"'},
        )
    except AudioServiceError as e:
        return JSONResponse(
            status_code=400 if e.error_type == "empty_text" else 502,
            content={"success": False, "data": None,
                     "message": e.message, "error_type": e.error_type},
        )
    except Exception:
        return JSONResponse(
            status_code=502,
            content={"success": False, "data": None,
                     "message": "语音合成失败，请稍后重试", "error_type": "tts_api_error"},
        )
