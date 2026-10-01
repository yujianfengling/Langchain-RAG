"""
语音引擎基类与状态机

状态：not_loaded → loading → ready | failed
失败时保留 `error` 与 `error_type`，供 /health 与业务侧给出可理解提示。
"""

import threading
import time
from typing import Any, Dict, List, Optional


class SpeechEngineError(Exception):
    """引擎异常（带 error_type，供上层映射成可读提示）"""

    def __init__(self, message: str, error_type: str = "engine_error"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


class EngineState:
    NOT_LOADED = "not_loaded"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


class BaseEngine:
    """引擎基类：负责加载状态、耗时统计与异常包装"""

    kind = "base"
    name = "base"

    def __init__(self):
        self.state = EngineState.NOT_LOADED
        self.error: Optional[str] = None
        self.error_type: Optional[str] = None
        self.load_seconds: Optional[float] = None
        self.last_latency_ms: Optional[float] = None
        self.requests = 0
        self.failures = 0
        self.device = ""
        self._lock = threading.Lock()
        self._model = None

    # ---- 子类实现 ----
    def _load(self):
        raise NotImplementedError

    def _describe(self) -> Dict[str, Any]:
        return {}

    # ---- 对外 ----

    def ensure_loaded(self):
        """幂等加载（并发安全）；失败时抛 SpeechEngineError"""
        if self.state == EngineState.READY:
            return
        with self._lock:
            if self.state == EngineState.READY:
                return
            if self.state == EngineState.LOADING:
                # 另一个线程正在加载：等它结束
                raise SpeechEngineError("模型正在加载中，请稍后重试", "engine_loading")
            self.state = EngineState.LOADING
            started = time.time()
            try:
                self._model = self._load()
                self.load_seconds = round(time.time() - started, 2)
                self.state = EngineState.READY
                self.error = None
                self.error_type = None
            except Exception as e:  # 任何加载失败都要能解释
                self.state = EngineState.FAILED
                self.error = str(e)[:500]
                self.error_type = _classify(e)
                self._model = None
                raise SpeechEngineError(
                    f"{self.name} 模型加载失败：{self.error}", self.error_type)

    def status(self) -> Dict[str, Any]:
        info = {
            "kind": self.kind,
            "engine": self.name,
            "state": self.state,
            "ready": self.state == EngineState.READY,
            "load_seconds": self.load_seconds,
            "last_latency_ms": self.last_latency_ms,
            "requests": self.requests,
            "failures": self.failures,
            "device": self.device,
            "error": self.error,
            "error_type": self.error_type,
        }
        info.update(self._describe())
        return info


def _classify(exc: Exception) -> str:
    """把底层异常归类，便于前端给出可理解提示"""
    text = f"{type(exc).__name__}: {exc}".lower()
    if isinstance(exc, ModuleNotFoundError) or "no module named" in text:
        return "dependency_missing"
    if isinstance(exc, FileNotFoundError) or "no such file" in text or "not found" in text:
        return "model_missing"
    if "cuda" in text and ("out of memory" in text or "oom" in text):
        return "cuda_oom"
    if "cuda" in text:
        return "cuda_error"
    if "connection" in text or "http" in text or "download" in text:
        return "model_download_failed"
    return "engine_error"


# ---------- Mock 引擎（供主后端联调 / 无模型环境降级演示） ----------

class MockASREngine(BaseEngine):
    """不做推理，返回固定文本；仅用于链路联调与自动化测试"""

    kind = "asr"
    name = "mock-asr"

    def _load(self):
        self.device = "cpu"
        return object()

    def transcribe(self, audio_path: str, **kwargs) -> Dict[str, Any]:
        self.ensure_loaded()
        self.requests += 1
        return {"text": "这是一段用于联调的识别结果。", "duration_s": 1.0,
                "engine": self.name, "segments": []}


class MockTTSEngine(BaseEngine):
    """生成一段可播放的正弦波 WAV，仅用于链路联调与自动化测试"""

    kind = "tts"
    name = "mock-tts"

    SAMPLE_RATE = 16000

    def _load(self):
        self.device = "cpu"
        return object()

    def list_voices(self) -> List[str]:
        return ["mock-voice"]

    def synthesize(self, text: str, voice: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        self.ensure_loaded()
        self.requests += 1
        import io
        import math
        import struct
        import wave

        # 时长与文本长度成正比（0.1s/字，最少 0.3s）
        seconds = max(0.3, len(text or "") * 0.1)
        rate = self.SAMPLE_RATE
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            frames = bytearray()
            for i in range(int(rate * seconds)):
                value = int(3000 * math.sin(2 * math.pi * 440 * i / rate))
                frames += struct.pack("<h", value)
            w.writeframes(bytes(frames))
        return {"audio": buf.getvalue(), "sample_rate": rate,
                "engine": self.name, "voice": voice or "mock-voice"}
