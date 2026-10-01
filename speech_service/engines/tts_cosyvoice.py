"""
本地 TTS 引擎：CosyVoice-300M-SFT（预置音色）

选型理由（实测对比见 docs/LOCAL_SPEECH.md）：
- 中文自然度、数字/英文混读表现稳定
- 300M 规模，8GB 显存可与 ASR 共存，冷启动可接受
- **自带预置音色**（中文女/中文男/英文女…），首版不依赖任何参考音频，
  不涉及真人音色的授权问题（本轮要求"参考音频或定制音色必须有合法来源"）
- 依赖全为 pip 可装包（含 wetext 文本正则化），Windows 无需编译

调用方式（CosyVoice2 仓库）：
    sys.path.append(<repo>/third_party/Matcha-TTS)
    from cosyvoice.cli.cosyvoice import AutoModel
    model = AutoModel(model_dir=..., load_jit=False, load_trt=False, fp16=False)
    for out in model.inference_sft(text, spk_id, stream=False):
        audio = out["tts_speech"]  # torch.Tensor [1, T]
"""

import io
import os
import sys
import time
from typing import Any, Dict, List, Optional

from .base import BaseEngine, SpeechEngineError


class CosyVoiceEngine(BaseEngine):
    kind = "tts"
    name = "cosyvoice-300m-sft"

    def __init__(self, model_dir: str, repo_dir: str, default_voice: str = "中文女",
                 fp16: bool = False, load_jit: bool = False, load_trt: bool = False,
                 device: Optional[str] = None):
        super().__init__()
        self.model_dir = model_dir
        self.repo_dir = repo_dir
        self.default_voice = default_voice
        self.fp16 = fp16
        self.load_jit = load_jit
        self.load_trt = load_trt
        self._device = device or ""
        self.sample_rate = 22050
        self._voices: List[str] = []

    def _describe(self) -> Dict[str, Any]:
        return {
            "model_dir": self.model_dir,
            "default_voice": self.default_voice,
            "voices": self._voices,
            "sample_rate": self.sample_rate,
            "fp16": self.fp16,
        }

    def _load(self):
        if not os.path.isdir(self.model_dir):
            raise SpeechEngineError(
                f"未找到 TTS 模型目录：{self.model_dir}（请先下载模型，见 speech_service/README.md）",
                "model_missing")
        if not os.path.isdir(self.repo_dir):
            raise SpeechEngineError(
                f"未找到 CosyVoice 代码目录：{self.repo_dir}", "model_missing")

        matcha = os.path.join(self.repo_dir, "third_party", "Matcha-TTS")
        for path in (self.repo_dir, matcha):
            if os.path.isdir(path) and path not in sys.path:
                sys.path.append(path)

        try:
            from cosyvoice.cli.cosyvoice import AutoModel
        except Exception as e:
            raise SpeechEngineError(f"CosyVoice 依赖导入失败：{e}", "dependency_missing")

        device = self._device or ("cuda" if _cuda_ok() else "cpu")
        self.device = device
        try:
            model = AutoModel(model_dir=self.model_dir, load_jit=self.load_jit,
                              load_trt=self.load_trt, fp16=self.fp16)
        except Exception as e:
            raise SpeechEngineError(f"CosyVoice 模型加载失败：{e}", "tts_init_failed")

        try:
            self.sample_rate = int(getattr(model, "sample_rate", 22050))
        except Exception:
            self.sample_rate = 22050
        try:
            self._voices = list(model.list_available_spks())
        except Exception:
            self._voices = []
        if not self._voices:
            self._voices = [self.default_voice]
        return model

    def list_voices(self) -> List[str]:
        if not self._voices:
            try:
                self.ensure_loaded()
            except SpeechEngineError:
                return [self.default_voice]
        return self._voices

    # ---- 推理 ----

    def synthesize(self, text: str, voice: Optional[str] = None, speed: float = 1.0,
                   **kwargs) -> Dict[str, Any]:
        """
        合成单段文本 → WAV 字节

        text 应当已经是"适合朗读"的短段（由主后端 services/tts_text 处理并切分）
        """
        text = (text or "").strip()
        if not text:
            raise SpeechEngineError("待合成文本为空", "empty_text")
        self.ensure_loaded()
        spk = voice or self.default_voice
        if self._voices and spk not in self._voices:
            # 音色不存在时回退到默认音色，并把实际使用的音色返回（不静默换音色）
            spk = self.default_voice if self.default_voice in self._voices else self._voices[0]

        started = time.time()
        self.requests += 1
        try:
            chunks = []
            for out in self._model.inference_sft(text, spk, stream=False, speed=speed):
                speech = out.get("tts_speech")
                if speech is not None:
                    chunks.append(speech)
            if not chunks:
                raise SpeechEngineError("TTS 未返回音频", "tts_empty_audio")
            import torch
            audio = torch.cat([c.reshape(-1) for c in chunks]).unsqueeze(0)
            wav_bytes = _tensor_to_wav_bytes(audio, self.sample_rate)
            self.last_latency_ms = round((time.time() - started) * 1000, 1)
            return {
                "audio": wav_bytes,
                "sample_rate": self.sample_rate,
                "engine": self.name,
                "voice": spk,
                "latency_ms": self.last_latency_ms,
                "duration_s": round(audio.shape[1] / float(self.sample_rate), 2),
            }
        except SpeechEngineError:
            self.failures += 1
            raise
        except Exception as e:
            self.failures += 1
            raise SpeechEngineError(f"语音合成失败：{e}", "tts_failed")


def _cuda_ok() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _tensor_to_wav_bytes(audio, sample_rate: int) -> bytes:
    """torch 张量 → WAV 字节（16-bit PCM，前端可直接播放）"""
    import numpy as np
    import soundfile as sf

    data = audio.detach().cpu().numpy()
    if data.ndim == 2:
        data = data.T  # (T, C)
    data = np.clip(data, -1.0, 1.0)
    buf = io.BytesIO()
    sf.write(buf, data, int(sample_rate), format="WAV", subtype="PCM_16")
    return buf.getvalue()
