"""
本地语音引擎（只有 ASR；回答朗读 TTS 已移除）

设计约束（本轮要求）：
- 模型推理只在本服务（独立进程）里做，不在 FastAPI 主进程内
- 引擎只加载一次，可预热、可查询状态、可失败降级（失败时给出可理解原因）
- 引擎异常统一包装成 SpeechEngineError，避免把底层栈直接抛给业务侧
"""

from .base import SpeechEngineError, EngineState, BaseEngine
from .asr_funasr import FunASREngine
# 轻量栈（sherpa-onnx，无需 PyTorch；模型按需下载）——重依赖仍在 _load() 内，
# 缺少 sherpa-onnx 时只会表现为 dependency_missing，不影响服务启动。
from .asr_sensevoice import SenseVoiceEngine

__all__ = [
    "SpeechEngineError",
    "EngineState",
    "BaseEngine",
    "FunASREngine",
    "SenseVoiceEngine",
    "build_asr_engine",
    "LIGHT_ENGINES",
]

# 需要"按需下载 + 不能自动加载"的轻量引擎（服务端据此决定是否允许联网下载）
LIGHT_ENGINES = ("sensevoice",)


def _filter_kwargs(cls, kwargs: dict) -> dict:
    """只把该引擎 __init__ 真正接受的参数传进去

    服务端会统一传 `model_key`/`num_threads`/`allow_download` 等参数，
    而 FunASR/CosyVoice 等既有引擎并不接受它们——直接透传会让**默认引擎构造失败**。
    这里按签名过滤，对将来新增参数也安全。
    """
    import inspect
    try:
        params = inspect.signature(cls.__init__).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


def build_asr_engine(name: str, **kwargs):
    """按配置构造 ASR 引擎（未知/未安装时返回 mock 或 None）"""
    name = (name or "none").lower()
    if name == "funasr":
        return FunASREngine(**_filter_kwargs(FunASREngine, kwargs))
    if name in ("sensevoice", "sensevoice-int8"):
        return SenseVoiceEngine(
            model_key=kwargs.get("model_key"),
            num_threads=kwargs.get("num_threads"),
            allow_download=kwargs.get("allow_download", False))
    if name == "mock":
        from .base import MockASREngine
        return MockASREngine()
    return None
