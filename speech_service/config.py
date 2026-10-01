"""
本地语音服务配置（2026-09-26）

全部通过环境变量配置，便于在不同机器上重复部署。
模型目录默认放在仓库外（E:/WORKBUDDY/Game/speech_stack），避免被提交。
"""

import os


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = (os.environ.get(name) or "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


# ---------- 服务 ----------
HOST = _env("SPEECH_HOST", "127.0.0.1")
PORT = _env_int("SPEECH_PORT", 8100)

# ---------- 设备 ----------
# auto | cuda | cpu
DEVICE = _env("SPEECH_DEVICE", "auto").lower()

# ---------- 模型根目录 ----------
# 默认与仓库平级，避免模型文件进入 Git
DEFAULT_STACK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "speech_stack"))
STACK_DIR = _env("SPEECH_STACK_DIR", DEFAULT_STACK_DIR)

# ---------- ASR ----------
# funasr | mock | none
ASR_ENGINE = _env("SPEECH_ASR_ENGINE", "funasr").lower()
ASR_MODEL = _env("SPEECH_ASR_MODEL", "paraformer-zh")
ASR_VAD_MODEL = _env("SPEECH_ASR_VAD_MODEL", "fsmn-vad")
ASR_PUNC_MODEL = _env("SPEECH_ASR_PUNC_MODEL", "ct-punc")
ASR_MODEL_DIR = _env("SPEECH_ASR_MODEL_DIR",
                     os.path.join(STACK_DIR, "models", "asr"))
ASR_TIMEOUT_S = _env_int("SPEECH_ASR_TIMEOUT_S", 60)

# ---------- TTS ----------
# cosyvoice | mock | none
TTS_ENGINE = _env("SPEECH_TTS_ENGINE", "cosyvoice").lower()
TTS_MODEL_DIR = _env("SPEECH_TTS_MODEL_DIR",
                     os.path.join(STACK_DIR, "CosyVoice", "pretrained_models",
                                  "CosyVoice-300M-SFT"))
COSYVOICE_REPO = _env("SPEECH_COSYVOICE_REPO",
                      os.path.join(STACK_DIR, "CosyVoice"))
# 首版使用模型自带的预置音色（可安全分发，不涉及真实人声授权）
DEFAULT_VOICE = _env("SPEECH_TTS_VOICE", "中文女")
# fp16 在部分 Windows + cu121 组合下不稳定，默认关闭
TTS_FP16 = _env_bool("SPEECH_TTS_FP16", False)
TTS_LOAD_JIT = _env_bool("SPEECH_TTS_LOAD_JIT", False)
TTS_LOAD_TRT = _env_bool("SPEECH_TTS_LOAD_TRT", False)

# ---------- 启动行为 ----------
# 启动时是否预热（加载模型）；关闭则首次请求时懒加载
WARMUP_ON_START = _env_bool("SPEECH_WARMUP_ON_START", True)

# 单次请求最长文本（防御性上限；正常由主后端按句切分）
MAX_TEXT_CHARS = _env_int("SPEECH_MAX_TEXT_CHARS", 4000)
# 单个请求最多合成多少段
MAX_SEGMENTS = _env_int("SPEECH_MAX_SEGMENTS", 40)


def device() -> str:
    """解析实际使用的设备"""
    if DEVICE in ("cpu", "cuda"):
        return DEVICE
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def describe() -> dict:
    """可读配置（用于 /health 与日志，不包含任何密钥）"""
    return {
        "device": device(),
        "stack_dir": STACK_DIR,
        "asr": {
            "engine": ASR_ENGINE,
            "model": ASR_MODEL,
            "model_dir": ASR_MODEL_DIR,
        },
        "tts": {
            "engine": TTS_ENGINE,
            "model_dir": TTS_MODEL_DIR,
            "default_voice": DEFAULT_VOICE,
            "fp16": TTS_FP16,
        },
        "warmup_on_start": WARMUP_ON_START,
    }
