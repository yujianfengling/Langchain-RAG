"""
本地 ASR 引擎：FunASR paraformer-zh（+ fsmn-vad 断句 + ct-punc 标点）

选型理由（详见 docs/LOCAL_SPEECH.md 的实测对比）：
- 中文识别准确率高，且自带标点恢复（学生可直接在输入框里检查修改）
- 模型约 1GB
- 纯 Python 依赖，Windows 上不需要编译（对比 whisper 系列需要额外运行时）

只在本进程内加载一次；首次调用会从 ModelScope 下载模型到 MODELSCOPE_CACHE。
"""

import os
import tempfile
import time
import wave
from typing import Any, Dict, List, Optional

from .base import BaseEngine, SpeechEngineError


class FunASREngine(BaseEngine):
    kind = "asr"
    name = "funasr-paraformer-zh"

    def __init__(self, model: str = "paraformer-zh", vad_model: str = "fsmn-vad",
                 punc_model: str = "ct-punc", device: Optional[str] = None,
                 model_dir: Optional[str] = None):
        super().__init__()
        self.model_name = model
        self.vad_model = vad_model
        self.punc_model = punc_model
        self.model_dir = model_dir or ""
        self._device = device or ""
        if self.model_dir:
            os.environ.setdefault("MODELSCOPE_CACHE", self.model_dir)

    def _describe(self) -> Dict[str, Any]:
        return {
            "model": self.model_name,
            "vad": self.vad_model,
            "punc": self.punc_model,
            "model_dir": self.model_dir,
        }

    def _load(self):
        from funasr import AutoModel  # 依赖缺失则在这里报错（会被归类为 dependency_missing）

        device = self._device or ("cuda:0" if _cuda_ok() else "cpu")
        self.device = device
        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "vad_model": self.vad_model,
            "punc_model": self.punc_model,
            "device": device,
            "disable_update": True,
        }
        try:
            model = AutoModel(**kwargs)
        except Exception as e:
            # 常见：模型未下载 + 网络不可达
            raise SpeechEngineError(
                f"ASR 模型初始化失败（{self.model_name}）：{e}", "asr_init_failed")
        return model

    # ---- 推理 ----

    def transcribe(self, audio_path: str, language: str = "auto") -> Dict[str, Any]:
        """识别音频文件（wav/mp3），返回文本与元信息"""
        self.ensure_loaded()
        started = time.time()
        self.requests += 1
        try:
            wav_path, cleanup = _ensure_wav(audio_path)
            try:
                result = self._model.generate(input=wav_path, batch_size_s=300)
            finally:
                cleanup()
            text = ""
            segments: List[Dict[str, Any]] = []
            if isinstance(result, list) and result:
                first = result[0] or {}
                text = str(first.get("text") or "").strip()
                if first.get("sentences"):
                    segments = [{"text": s.get("text", ""),
                                 "start": s.get("start"), "end": s.get("end")}
                                for s in first["sentences"]]
            self.last_latency_ms = round((time.time() - started) * 1000, 1)
            return {
                "text": text,
                "segments": segments,
                "engine": self.name,
                "model": self.model_name,
                "latency_ms": self.last_latency_ms,
                "duration_s": _wav_duration(wav_path) if os.path.exists(wav_path) else None,
            }
        except SpeechEngineError:
            self.failures += 1
            raise
        except Exception as e:
            self.failures += 1
            raise SpeechEngineError(f"语音识别失败：{e}", "asr_failed")


def _cuda_ok() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _ensure_wav(path: str):
    """
    保证给到模型的是一段可读的音频路径。

    FunASR 能直接吃 wav；非 wav（如 mp3/webm 上传）用 torchaudio 转成 16k 单声道 wav。
    返回 (wav_path, cleanup_fn)。
    """
    if not os.path.exists(path):
        raise SpeechEngineError("音频文件不存在", "audio_invalid_format")

    def _noop():
        return None

    if path.lower().endswith(".wav"):
        return path, _noop

    try:
        import torch  # noqa: F401
        import torchaudio

        waveform, rate = torchaudio.load(path)
        if waveform.dim() == 2 and waveform.size(0) > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        if rate != 16000:
            waveform = torchaudio.functional.resample(waveform, rate, 16000)
            rate = 16000
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        torchaudio.save(tmp.name, waveform, rate)
        return tmp.name, (lambda: os.unlink(tmp.name))
    except Exception as e:
        raise SpeechEngineError(f"音频格式转换失败：{e}", "audio_invalid_format")


def _wav_duration(path: str) -> Optional[float]:
    try:
        with wave.open(path, "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 2)
    except Exception:
        return None
