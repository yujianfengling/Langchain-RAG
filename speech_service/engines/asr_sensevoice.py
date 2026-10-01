"""SenseVoiceSmall（sherpa-onnx int8）ASR 引擎 —— 轻量、按需下载

替代 FunASR `paraformer-zh + fsmn-vad + ct-punc`（模型约 1.19 GB、需 PyTorch/torchaudio）：
本引擎模型 155.5 MB（解压 229 MB），**只需 sherpa-onnx + onnxruntime**，
自带标点与 ITN（`use_itn=True`），无需额外 VAD / 标点模型。

下载时机（需求硬约束）：
- `allow_download=False`（默认，`/health`、`/voices`、启动预热走这条）→ 缺模型直接抛
  `model_missing`，**绝不联网**。
- 只有真正转写时由服务端把 `allow_download` 置 True（首次使用才下载）。
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional
from pathlib import Path

from .base import BaseEngine, EngineState, SpeechEngineError
from .. import model_store


class SenseVoiceEngine(BaseEngine):
    kind = "asr"
    name = "sensevoice-small-int8"

    def __init__(self, model_key: Optional[str] = None,
                 num_threads: Optional[int] = None,
                 language: str = "auto",
                 use_itn: bool = True,
                 allow_download: bool = False):
        super().__init__()
        self.spec = model_store.get_spec(model_key) if model_key else model_store.pick("asr")
        self.num_threads = int(num_threads or os.getenv("SPEECH_NUM_THREADS", "0") or 0)
        self.language = (language or os.getenv("SPEECH_ASR_LANGUAGE", "auto")).strip() or "auto"
        self.use_itn = bool(use_itn if use_itn is not None
                            else (os.getenv("SPEECH_ASR_USE_ITN", "1") not in ("0", "false", "False")))
        # 是否允许在加载时下载模型（由服务端在真实请求路径上临时打开）
        self.allow_download = allow_download
        self.model_dir: Optional[str] = None

    # ---------- 契约 ----------

    def _describe(self) -> Dict[str, Any]:
        st = model_store.state(self.spec)
        return {
            "model": self.spec.key,
            "model_title": self.spec.title,
            "model_version": self.spec.version,
            "model_ready": st["ready"],
            "model_download_mb": st["download_mb"],
            "model_disk_mb": st["disk_mb"],
            "model_license": self.spec.license,
            "num_threads": self.num_threads,
            "language": self.language,
            "use_itn": self.use_itn,
            "model_dir": st["dir"],
        }

    def _load(self):
        import sherpa_onnx

        d = model_store.ensure(self.spec, allow_download=self.allow_download)
        self.model_dir = str(d)
        self.device = "cpu"     # ONNX int8 CPU 推理，无 CUDA 依赖
        t0 = time.time()
        rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(d / "model.int8.onnx"),
            tokens=str(d / "tokens.txt"),
            num_threads=self.num_threads,
            use_itn=self.use_itn,
            language=self.language,
            provider="cpu",
            debug=False,
        )
        self._load_detail = f"{time.time() - t0:.2f}s"
        return rec

    def transcribe(self, audio_path: str, language: str = "auto") -> Dict[str, Any]:
        """转写（契约方法名必须是 transcribe：服务端据此选择 ASR 锁）"""
        self.ensure_loaded()
        self.requests += 1
        started = time.time()
        p = Path(audio_path)
        if not p.is_file():
            self.failures += 1
            raise SpeechEngineError(f"音频文件不存在：{audio_path}", "audio_invalid_format")

        try:
            samples, sample_rate, duration = _read_audio(str(p))
        except Exception as e:
            self.failures += 1
            raise SpeechEngineError(f"音频读取失败：{e}", "audio_invalid_format")

        try:
            stream = self._model.create_stream()
            stream.accept_waveform(sample_rate=sample_rate, waveform=samples)
            self._model.decode_stream(stream)
            text = (stream.result.text or "").strip()
        except Exception as e:
            self.failures += 1
            raise SpeechEngineError(f"识别失败：{e}", "asr_failed")

        latency_ms = round((time.time() - started) * 1000, 1)
        self.last_latency_ms = latency_ms
        return {
            "text": text,
            "segments": [{"text": text, "start": 0.0, "end": duration}],
            "engine": self.name,
            "model": self.spec.key,
            "latency_ms": latency_ms,
            "duration_s": duration,
            "language": getattr(getattr(stream, "result", None), "lang", "") or "",
        }


def _read_audio(path: str):
    """读取音频为 (float32 单声道样本, 采样率, 时长秒)

    - `.wav`：用标准库 `wave` + numpy（无额外依赖，兼容 16-bit PCM）
    - 其它格式（mp3/ogg/flac/webm 等）：用 soundfile（libsndfile 已随轮子分发，
      不需要系统库）
    - 采样率不是 16k 时线性重采样（SenseVoice 要求 16k 输入）
    """
    import numpy as np

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(path)

    if p.suffix.lower() == ".wav":
        import wave as _wave
        with _wave.open(str(p), "rb") as w:
            sr = w.getframerate()
            channels = w.getnchannels()
            width = w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if width != 2:
            raise ValueError(f"仅支持 16-bit PCM WAV（当前 {width * 8}-bit）")
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        mono = data
    else:
        import soundfile as sf
        data, sr = sf.read(str(p), dtype="float32", always_2d=True)
        mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]

    if sr != 16000:
        mono = _resample_linear(mono, sr, 16000)
        sr = 16000
    mono = np.ascontiguousarray(mono, dtype=np.float32)
    return mono, sr, round(len(mono) / sr, 3)


def _resample_linear(samples, src_rate: int, dst_rate: int):
    """线性重采样（够用且无额外依赖；SenseVoice 要求 16k）"""
    import numpy as np
    if src_rate == dst_rate or len(samples) == 0:
        return samples
    n_out = int(round(len(samples) * dst_rate / src_rate))
    x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)