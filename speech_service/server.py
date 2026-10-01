"""
本地语音服务（独立进程）

为什么单独起进程（本轮要求"不要把模型推理和全部依赖直接堆进 FastAPI 主进程"）：
- 模型依赖（torch 2.3.1 + funasr + cosyvoice）与主项目的 Python 环境互不干扰
- 模型只加载一次，常驻显存；主后端重启不需要重新加载模型
- 模型崩溃/显存不足不会拖垮文字问答（主后端按 HTTP 健康状态自动降级）

接口：
    GET  /health          引擎状态、设备、显存
    POST /warmup          手动预热（幂等）
    GET  /voices          可用音色
    POST /asr             multipart 音频 → 文本
    POST /tts             文本 → 单段 WAV(base64)
    POST /tts/segments    多段文本 → 逐段 WAV(base64)，**保证顺序**
"""

import base64
import logging
import os
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import config
from .engines import (CosyVoiceEngine, FunASREngine, SpeechEngineError,
                      build_asr_engine, build_tts_engine)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("speech_service")

app = FastAPI(title="本地语音服务", version="1.0.0")

# 推理串行化：同类请求排队（顺序播放本身要求有序），但 **ASR 与 TTS 分开**——
# 否则一段长回答的逐句合成会把语音识别堵在后面，用户体验为"录音后半天没反应"。
# 两类模型各自常驻显存（合计约 3.7GB），互不抢占，可以安全并发。
_ASR_LOCK = threading.Lock()
_TTS_LOCK = threading.Lock()

# 排队等待上限：超过则认为服务忙，返回明确错误（而不是让请求无限等待）
# —— 同步推理必须放到线程池执行，否则会阻塞事件循环、连 /health 都无响应
QUEUE_WAIT_SECONDS = float(os.environ.get("SPEECH_QUEUE_WAIT_SECONDS", "20"))
# TTS 逐句合成会排队（长回答有多句），给它更宽的等待窗口
TTS_QUEUE_WAIT_SECONDS = float(os.environ.get("SPEECH_TTS_QUEUE_WAIT_SECONDS", "90"))


class BusyError(SpeechEngineError):
    def __init__(self):
        super().__init__(
            f"语音服务正忙（等待超过 {QUEUE_WAIT_SECONDS:.0f} 秒），请稍后重试",
            "engine_busy")


def _run_locked(fn, *args, wait: float = None, **kwargs):
    """在线程池中执行推理；按引擎类型排队，并限制等待时间"""
    lock = _ASR_LOCK if fn.__name__ == "transcribe" else _TTS_LOCK
    timeout = TTS_QUEUE_WAIT_SECONDS if lock is _TTS_LOCK else QUEUE_WAIT_SECONDS
    if wait is not None:
        timeout = wait
    acquired = lock.acquire(timeout=timeout)
    if not acquired:
        raise BusyError()
    try:
        return fn(*args, **kwargs)
    finally:
        lock.release()

STATE: Dict[str, Any] = {"asr": None, "tts": None, "started_at": time.time()}


def _init_engines():
    """构造（且只构造一次）ASR / TTS 引擎"""
    if STATE["asr"] is None:
        STATE["asr"] = build_asr_engine(
            config.ASR_ENGINE, model=config.ASR_MODEL, vad_model=config.ASR_VAD_MODEL,
            punc_model=config.ASR_PUNC_MODEL, model_dir=config.ASR_MODEL_DIR)
    if STATE["tts"] is None:
        STATE["tts"] = build_tts_engine(
            config.TTS_ENGINE, model_dir=config.TTS_MODEL_DIR,
            repo_dir=config.COSYVOICE_REPO, default_voice=config.DEFAULT_VOICE,
            fp16=config.TTS_FP16, load_jit=config.TTS_LOAD_JIT,
            load_trt=config.TTS_LOAD_TRT)
    return STATE["asr"], STATE["tts"]


def _warmup(blocking: bool = True) -> Dict[str, Any]:
    asr, tts = _init_engines()
    out: Dict[str, Any] = {}
    for key, engine in (("asr", asr), ("tts", tts)):
        if engine is None:
            out[key] = {"state": "disabled", "ready": False}
            continue
        try:
            engine.ensure_loaded()
        except SpeechEngineError as e:
            logger.warning(f"[warmup] {key} 加载失败：{e.message}")
        out[key] = engine.status()
    return out


@app.on_event("startup")
def _on_startup():
    logger.info("本地语音服务启动：%s", config.describe())
    if config.WARMUP_ON_START:
        threading.Thread(target=lambda: _warmup(blocking=False), daemon=True).start()


def _error(e: SpeechEngineError, status: int = 503):
    return JSONResponse(status_code=status, content={
        "success": False, "message": e.message, "error_type": e.error_type,
    })


def _vram() -> Dict[str, Any]:
    try:
        import torch
        if not torch.cuda.is_available():
            return {"available": False}
        return {
            "available": True,
            "allocated_mb": round(torch.cuda.memory_allocated() / 1024 / 1024, 1),
            "reserved_mb": round(torch.cuda.memory_reserved() / 1024 / 1024, 1),
        }
    except Exception:
        return {"available": False}


@app.get("/health")
def health():
    asr, tts = _init_engines()
    return {
        "status": "ok",
        "config": config.describe(),
        "engines": {
            "asr": asr.status() if asr else {"engine": None, "ready": False},
            "tts": tts.status() if tts else {"engine": None, "ready": False},
        },
        "vram": _vram(),
        "uptime_s": round(time.time() - STATE["started_at"], 1),
    }


@app.post("/warmup")
async def warmup():
    return {"success": True, "engines": await run_in_threadpool(_warmup)}


@app.get("/voices")
async def voices():
    _, tts = _init_engines()
    if tts is None:
        return {"success": True, "voices": [], "reason": "TTS 未启用"}
    try:
        await run_in_threadpool(tts.ensure_loaded)
    except SpeechEngineError as e:
        return _error(e)
    return {"success": True, "voices": tts.list_voices(),
            "default": config.DEFAULT_VOICE, "engine": tts.name}


class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None
    speed: float = 1.0


class SegmentsRequest(BaseModel):
    segments: List[str]
    voice: Optional[str] = None
    speed: float = 1.0


@app.post("/asr")
async def asr(file: UploadFile = File(...), language: str = Form("auto")):
    engine, _ = _init_engines()
    if engine is None:
        return _error(SpeechEngineError("本地 ASR 未启用", "asr_disabled"))
    data = await file.read()
    if not data:
        return _error(SpeechEngineError("音频为空", "audio_invalid_format"), 400)
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        result = await run_in_threadpool(
            _run_locked, engine.transcribe, tmp.name, language=language)
        return {"success": True, **result}
    except SpeechEngineError as e:
        return _error(e)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


@app.post("/tts")
async def tts(req: TTSRequest):
    _, engine = _init_engines()
    if engine is None:
        return _error(SpeechEngineError("本地 TTS 未启用", "tts_disabled"))
    text = (req.text or "").strip()
    if not text:
        return _error(SpeechEngineError("文本为空", "empty_text"), 400)
    if len(text) > config.MAX_TEXT_CHARS:
        return _error(SpeechEngineError(
            f"文本过长（{len(text)} > {config.MAX_TEXT_CHARS}），请按句分段请求",
            "text_too_long"), 400)
    try:
        out = await run_in_threadpool(
            _run_locked, engine.synthesize, text, voice=req.voice, speed=req.speed)
        return {
            "success": True,
            "audio_b64": base64.b64encode(out["audio"]).decode("ascii"),
            "format": "wav",
            "sample_rate": out["sample_rate"],
            "engine": out["engine"],
            "voice": out["voice"],
            "latency_ms": out.get("latency_ms"),
            "duration_s": out.get("duration_s"),
            "chars": len(text),
        }
    except SpeechEngineError as e:
        return _error(e)


@app.post("/tts/segments")
async def tts_segments(req: SegmentsRequest, request: Request):
    """
    逐段合成（顺序由本服务保证）

    - 逐段返回，主后端可以边收边播
    - 客户端断开后停止继续合成（避免白算），已完成的段落照常返回
    """
    _, engine = _init_engines()
    if engine is None:
        return _error(SpeechEngineError("本地 TTS 未启用", "tts_disabled"))
    segments = [s for s in (req.segments or []) if s and s.strip()]
    if not segments:
        return _error(SpeechEngineError("文本为空", "empty_text"), 400)
    if len(segments) > config.MAX_SEGMENTS:
        return _error(SpeechEngineError(
            f"分段过多（{len(segments)} > {config.MAX_SEGMENTS}）", "too_many_segments"), 400)

    items: List[Dict[str, Any]] = []
    cancelled = False
    for seq, text in enumerate(segments):
        if await request.is_disconnected():
            cancelled = True
            logger.info(f"客户端断开，停止合成（已完成 {seq} 段）")
            break
        try:
            out = await run_in_threadpool(
                _run_locked, engine.synthesize, text, voice=req.voice, speed=req.speed)
        except SpeechEngineError as e:
            # 某段失败：如实返回错误，并保留已完成段落
            return JSONResponse(status_code=503, content={
                "success": False, "message": e.message, "error_type": e.error_type,
                "failed_seq": seq, "items": items,
            })
        items.append({
            "seq": seq,
            "text": text,
            "chars": len(text),
            "audio_b64": base64.b64encode(out["audio"]).decode("ascii"),
            "duration_s": out.get("duration_s"),
            "latency_ms": out.get("latency_ms"),
        })
    return {
        "success": True,
        "items": items,
        "cancelled": cancelled,
        "total_segments": len(segments),
        "format": "wav",
        "sample_rate": engine.status().get("sample_rate") or 22050,
        "engine": engine.name,
        "voice": req.voice or config.DEFAULT_VOICE,
    }


def main():
    """直接运行：python -m speech_service.server"""
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
