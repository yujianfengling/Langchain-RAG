"""
本地语音服务（独立进程）—— 2026-10-01：只保留**语音识别（ASR）**

为什么单独起进程：模型依赖（funasr 需要 PyTorch；轻量栈只需 sherpa-onnx）与主项目环境互不干扰；
模型只加载一次；模型崩溃不会拖垮文字问答（主后端按 HTTP 健康状态降级）。

接口：
    GET  /health          引擎状态、模型体积/下载状态、设备
    POST /warmup          手动预热（幂等；**只加载本地已有模型，从不下载**）
    POST /asr             multipart 音频 → 文本（语音输入用）
    GET  /models/status   轻量模型体积与下载状态（只读）
    POST /models/download 显式下载轻量模型（带进度）

回答朗读（TTS）已整体移除：不再有 /tts、/tts/segments、/voices，也没有 TTS 引擎与音色配置。
"""

import logging
import os
import tempfile
import threading
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import config
from . import model_store
from .engines import LIGHT_ENGINES, SpeechEngineError, build_asr_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("speech_service")

app = FastAPI(title="本地语音服务（ASR）", version="2.0.0")

# 推理串行化：语音识别请求排队（避免并发压满 CPU/显存）
_ASR_LOCK = threading.Lock()

# 排队等待上限：超过则认为服务忙，返回明确错误（而不是让请求无限等待）
QUEUE_WAIT_SECONDS = float(os.environ.get("SPEECH_QUEUE_WAIT_SECONDS", "20"))


class BusyError(SpeechEngineError):
    def __init__(self):
        super().__init__(
            f"语音服务正忙（等待超过 {QUEUE_WAIT_SECONDS:.0f} 秒），请稍后重试",
            "engine_busy")


def _run_locked(fn, *args, wait: float = None, **kwargs):
    """在线程池中执行推理；串行化并限制等待时间"""
    timeout = QUEUE_WAIT_SECONDS if wait is None else wait
    if not _ASR_LOCK.acquire(timeout=timeout):
        raise BusyError()
    try:
        return fn(*args, **kwargs)
    finally:
        _ASR_LOCK.release()


STATE: Dict[str, Any] = {"asr": None, "started_at": time.time(),
                         "download": {}}


def _is_light(kind: str) -> bool:
    """当前配置的该类型引擎是否属于"按需下载"的轻量栈"""
    name = (config.ASR_ENGINE or "").lower() if kind == "asr" else ""
    return name in LIGHT_ENGINES


def _engine_spec(kind: str):
    """当前该类型引擎对应的模型清单项

    必须先看引擎自己的 `spec`：配置了轻量 ASR 时，kind 的默认模型可能与实际引擎不同，
    用默认值判断会把"已就绪的模型"误判成"未下载"。
    """
    engine = STATE["asr"] if kind == "asr" else None
    spec = getattr(engine, "spec", None)
    if spec is not None:
        return spec
    return model_store.pick(kind)


def _init_engines():
    """构造（且只构造一次）ASR 引擎

    注意：**只构造不加载**。轻量引擎以 `allow_download=False` 构造，
    因此 /health、/warmup 都不会触发下载。
    """
    if STATE["asr"] is None:
        STATE["asr"] = build_asr_engine(
            config.ASR_ENGINE, model=config.ASR_MODEL, vad_model=config.ASR_VAD_MODEL,
            punc_model=config.ASR_PUNC_MODEL, model_dir=config.ASR_MODEL_DIR,
            model_key=config.ASR_MODEL_KEY, num_threads=config.NUM_THREADS,
            # 2026-10-03 修复：这两个值此前只从 env 读进 config、从未传给引擎，
            # 而引擎内部的 "or os.getenv(...)" 回落分支又因默认值非空而不可达，
            # 结果是 SPEECH_ASR_LANGUAGE / SPEECH_ASR_USE_ITN 恒无效。现在显式传入。
            language=config.ASR_LANGUAGE, use_itn=config.ASR_USE_ITN,
            allow_download=False)
    return STATE["asr"]


def _warmup(blocking: bool = True) -> Dict[str, Any]:
    """预热：**只加载本地已有模型，绝不下载**

    轻量栈缺模型时直接给出"尚未下载（需 X MB）"，让前端提示用户去下载，
    而不是在这里静默联网下载（需求硬约束）。
    """
    asr = _init_engines()
    out: Dict[str, Any] = {}
    if asr is None:
        return {"asr": {"state": "disabled", "ready": False}}
    if _is_light("asr"):
        st = model_store.state(_engine_spec("asr"))
        if not st["ready"]:
            out["asr"] = {**asr.status(), "state": "not_downloaded",
                          "error": f"轻量模型尚未下载（需 {st['download_mb']} MB）",
                          "error_type": "model_missing", "model_ready": False}
            return out
    try:
        asr.ensure_loaded()
    except SpeechEngineError as e:
        logger.warning(f"[warmup] asr 加载失败：{e.message}")
    out["asr"] = asr.status()
    return out


@app.on_event("startup")
def _on_startup():
    logger.info("本地语音服务启动（ASR）：%s", config.describe())
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
    asr = _init_engines()
    models = model_store.describe_all()
    warn = model_store.ascii_warning(model_store.cache_root())
    return {
        "status": "ok",
        "config": config.describe(),
        "engines": {
            "asr": asr.status() if asr else {"engine": None, "ready": False},
        },
        # 轻量模型：体积 / 是否已下载 / 磁盘占用（**只读检查，绝不下载**）
        "models": models,
        "models_path_warning": warn,
        "download": STATE["download"],
        "vram": _vram(),
        "uptime_s": round(time.time() - STATE["started_at"], 1),
    }


@app.post("/warmup")
async def warmup():
    return {"success": True, "engines": await run_in_threadpool(_warmup)}


# ============ 轻量模型：状态 / 显式下载（进度可轮询） ============

@app.get("/models/status")
def models_status():
    """模型体积与下载状态（**不下载**）；供设置页显示"需要下载多少 MB" """
    return {"success": True, "data": model_store.describe_all(),
            "download": STATE["download"]}


@app.post("/models/download")
async def models_download(kind: Optional[str] = None):
    """显式下载轻量模型（用户点"下载语音模型"时才走这里）

    `kind`: asr | all（默认：若当前是轻量引擎则下载其 ASR 模型）
    """
    if kind in (None, "", "all", "asr"):
        specs = [_engine_spec("asr")] if _is_light("asr") else []
    else:
        return _error(SpeechEngineError(f"未知 kind：{kind}", "model_missing"), 400)
    if not specs:
        return {"success": True, "message": "当前 ASR 引擎不是轻量栈，无需下载", "items": []}

    def _progress(key):
        def cb(done, total, phase, speed=None):
            STATE["download"][key] = {
                "phase": phase, "done": done, "total": total,
                "percent": round(done / total * 100, 1) if total else None,
                "speed_mbps": round(speed / 1024 / 1024, 2) if speed else None,
                "at": time.strftime("%H:%M:%S"),
            }
        return cb

    items = []
    for spec in specs:
        try:
            path = await run_in_threadpool(
                model_store.ensure, spec, _progress(spec.key), True)
            STATE["download"][spec.key] = {"phase": "done", "percent": 100.0,
                                           "disk_mb": model_store.state(spec)["disk_mb"]}
            items.append({"key": spec.key, "ok": True, "dir": str(path),
                          "disk_mb": model_store.state(spec)["disk_mb"]})
        except model_store.ModelStoreError as e:
            STATE["download"][spec.key] = {"phase": "failed", "error": e.message}
            items.append({"key": spec.key, "ok": False, "error": e.message,
                          "error_type": e.error_type})
    ok = all(i["ok"] for i in items)
    return JSONResponse(status_code=200 if ok else 503,
                        content={"success": ok, "items": items,
                                 "models": model_store.describe_all()})


@app.post("/asr")
async def asr(file: UploadFile = File(...), language: str = Form("auto")):
    """音频 → 文本（语音输入）。首次使用录音时**允许下载** ASR 模型。"""
    engine = _init_engines()
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
        # 首次使用录音时允许下载模型（/health、/warmup 不会走到这里）
        if _is_light("asr"):
            engine.allow_download = True
        try:
            result = await run_in_threadpool(
                _run_locked, engine.transcribe, tmp.name, language=language)
        finally:
            if _is_light("asr"):
                engine.allow_download = False
        return {"success": True, **result}
    except SpeechEngineError as e:
        return _error(e)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def main():
    """直接运行：python -m speech_service.server"""
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()