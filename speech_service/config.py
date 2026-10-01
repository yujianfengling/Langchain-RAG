"""
本地语音服务配置（2026-10-01：只保留语音识别 ASR）

全部通过环境变量配置，便于在不同机器上重复部署。
模型目录默认放在仓库外，避免被提交。

注意：回答朗读（TTS）已整体移除——本服务不再有 TTS 引擎、音色与合成相关配置。
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
# funasr（重量级，需 PyTorch）| sensevoice（轻量，sherpa-onnx，按需下载）| mock | none
ASR_ENGINE = _env("SPEECH_ASR_ENGINE", "funasr").lower()
ASR_MODEL = _env("SPEECH_ASR_MODEL", "paraformer-zh")
ASR_VAD_MODEL = _env("SPEECH_ASR_VAD_MODEL", "fsmn-vad")
ASR_PUNC_MODEL = _env("SPEECH_ASR_PUNC_MODEL", "ct-punc")
ASR_MODEL_DIR = _env("SPEECH_ASR_MODEL_DIR",
                     os.path.join(STACK_DIR, "models", "asr"))

# ---------- 轻量 ASR（sherpa-onnx，按需下载，2026-10-01） ----------
# 模型清单/下载/校验见 speech_service/model_store.py；这里只放"选择与参数"。
ASR_MODEL_KEY = _env("SPEECH_ASR_MODEL_KEY", "")
# 模型缓存根目录（**默认用户主目录，避免仓库路径含非 ASCII 字符**）
MODEL_CACHE_DIR = _env("SPEECH_MODEL_CACHE_DIR", "")
# 离线模式：为 1 时即使首次使用也不联网下载，只报"尚未下载"
MODEL_OFFLINE = _env_bool("SPEECH_MODEL_OFFLINE", False)
# ONNX 推理线程数（0 = 由 sherpa 自行决定）
NUM_THREADS = _env_int("SPEECH_NUM_THREADS", 0)
# ASR 语言（auto/zh/en/ja/ko/yue）与 ITN（数字/单位归一化 + 标点）
ASR_LANGUAGE = _env("SPEECH_ASR_LANGUAGE", "auto")
ASR_USE_ITN = _env_bool("SPEECH_ASR_USE_ITN", True)

# ---------- 启动行为 ----------
# 启动时是否预热（加载模型）；关闭则首次请求时懒加载。
# 注意：预热**只加载本地已有模型，从不联网下载**。
WARMUP_ON_START = _env_bool("SPEECH_WARMUP_ON_START", True)


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
        "warmup_on_start": WARMUP_ON_START,
    }