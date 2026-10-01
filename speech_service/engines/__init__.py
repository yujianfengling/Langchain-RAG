"""
本地语音引擎（ASR / TTS）

设计约束（本轮要求）：
- 模型推理只在本服务（独立进程）里做，不在 FastAPI 主进程内
- 引擎只加载一次，可预热、可查询状态、可失败降级（失败时给出可理解原因）
- 引擎异常统一包装成 SpeechEngineError，避免把底层栈直接抛给业务侧
"""

from .base import SpeechEngineError, EngineState, BaseEngine
from .asr_funasr import FunASREngine
from .tts_cosyvoice import CosyVoiceEngine

__all__ = [
    "SpeechEngineError",
    "EngineState",
    "BaseEngine",
    "FunASREngine",
    "CosyVoiceEngine",
    "build_asr_engine",
    "build_tts_engine",
]


def build_asr_engine(name: str, **kwargs):
    """按配置构造 ASR 引擎（未知/未安装时返回 mock 或 None）"""
    name = (name or "none").lower()
    if name == "funasr":
        return FunASREngine(**kwargs)
    if name == "mock":
        from .base import MockASREngine
        return MockASREngine()
    return None


def build_tts_engine(name: str, **kwargs):
    """按配置构造 TTS 引擎（CosyVoice 需要模型目录等参数）"""
    name = (name or "none").lower()
    if name == "cosyvoice":
        return CosyVoiceEngine(**kwargs)
    if name == "mock":
        from .base import MockTTSEngine
        return MockTTSEngine()
    return None
