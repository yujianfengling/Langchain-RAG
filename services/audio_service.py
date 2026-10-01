"""
语音服务模块（2026-10-01：只保留语音识别 ASR；回答朗读 TTS 已整体移除）

**语音链路选择**（SPEECH_BACKEND=auto|local|mimo，默认 auto）：
- local：调用独立进程的本地语音服务（FunASR 或轻量 SenseVoice，见 speech_service/）
- mimo ：保留原有的 MiMo ASR 适配（可选提供方，会员到期时不可用）
- auto ：本地可用时用本地；本地不可用时再尝试 MiMo；都不可用 → 如实报错

本地语音服务不可用（未启动 / 模型缺失 / 显存不足）时：
- 文字问答、学习闭环等功能完全不受影响
- 语音接口返回明确原因（error_type + message），**不会**用文本模型假装完成语音处理

MiMo ASR 走 OpenAI 兼容 /v1/chat/completions（不是 /audio/* 路径）：

- ASR: mimo-v2.5-asr
    {base}/chat/completions
    {
      "model": "mimo-v2.5-asr",
      "messages": [{"role":"user","content":[{"type":"input_audio","input_audio":{"data":"data:audio/wav;base64,..."}}]}],
      "asr_options": {"language":"auto"}
    }
    识别文本：choices[0].message.content

    {base}/chat/completions
    {
          "messages": [{"role":"assistant","content":"要朗读的文本"}],
      "audio": {"format":"wav","voice":"mimo_default"}
    }
    音频：choices[0].message.audio.data（base64）→ decode → WAV bytes

复用 MIMO_API_KEY / MIMO_BASE_URL，URL 规范化保证只拼一次 /chat/completions。
不发送 Windows 路径；不打印 Base64 / API Key / Authorization / 完整音频。

错误类型：
- audio_endpoint_not_found / invalid_api_key / unauthorized / rate_limited
- audio_invalid_format / asr_api_error
- timeout / connection_error / response_parse_error
"""

import base64
import hashlib
import io
import json
import os
import re
import requests
import logging
import time
from typing import Optional, Dict, Any, List

from utils.logger import logger
from services import local_speech

# 音频大小限制（20MB，与知识库上传一致）
MAX_AUDIO_BYTES = 20 * 1024 * 1024

# 支持的音频格式（MiMo-V2.5-ASR 支持 wav/mp3）
SUPPORTED_AUDIO_TYPES = ("wav", "mp3")

# 超时（秒）
ASR_TIMEOUT = 30

# HTTP 状态 → 结构化错误类型映射（禁止暴露上游响应正文）
_STATUS_TO_ERROR = {
    400: ("audio_invalid_format", "音频格式或请求参数错误"),
    # 2026-09-25：语音能力由 MiMo 提供（mimo-v2.5-asr）。
    # MiMo 会员到期后凭证被供应商拒绝，这里把原因写清楚，避免被误解成"网络故障"。
    401: ("invalid_api_key",
          "MiMo 语音凭证被供应商拒绝（会员到期或凭证失效），语音识别/合成当前不可用；"
          "文字学习功能不受影响"),
    403: ("unauthorized", "语音服务访问被拒绝（MiMo 凭证无权限，会员可能已到期）"),
    404: ("audio_endpoint_not_found", "语音服务端点不存在"),
    429: ("rate_limited", "语音服务请求过于频繁，请稍后重试"),
}


class AudioServiceError(Exception):
    """语音服务异常（携带 error_type）"""

    def __init__(self, message: str, error_type: str = "unknown"):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


def _get_audio_config(model_key: str) -> Dict[str, Any]:
    """获取 ASR 模型配置（复用 MiMo API Key 与 Base URL）"""
    from config.models import get_model_config, get_model_api_key
    config = get_model_config(model_key)
    api_key = get_model_api_key(model_key)
    return {**config, "api_key": api_key}


def normalize_chat_completions_url(base_url: str) -> str:
    """
    URL 规范化：保证最终只拼一次 /chat/completions。

    兼容：
    - https://api.xiaomimimo.com/v1        → https://api.xiaomimimo.com/v1/chat/completions
    - https://api.xiaomimimo.com/v1/        → 同上（去尾斜杠）
    - .../chat/completions                  → 原样（幂等，不重复拼接）
    - .../audio/transcriptions 等残留路径   → 剥离 audio 段后拼接
    """
    url = (base_url or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    # 兼容历史错误配置：剥离 /audio/* 残留路径
    if "/audio/" in url:
        url = url.split("/audio/")[0].rstrip("/")
    return f"{url}/chat/completions"


def validate_wav_header(data: bytes) -> bool:
    """校验 WAV 头部是否合法（RIFF....WAVE）"""
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _detect_audio_format(data: bytes) -> Optional[str]:
    """从字节流检测音频格式（wav/mp3）"""
    if validate_wav_header(data):
        return "wav"
    if len(data) >= 3 and data[:3] == b"ID3":
        return "mp3"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "mp3"
    return None


def _raise_status_error(status_code: int, fallback_type: str) -> None:
    """将 HTTP 状态码映射为结构化错误抛出"""
    error_type, message = _STATUS_TO_ERROR.get(
        status_code, (fallback_type, "语音服务异常"))
    logger.error(f"[Audio] 上游请求失败: status={status_code} error_type={error_type}")
    raise AudioServiceError(message, error_type=error_type)


# ============ ASR（语音转文本） ============

def transcribe_audio(audio_data: bytes, filename: Optional[str] = None) -> str:
    """
    语音识别：音频 → 文本

    默认走**本地 ASR**（FunASR paraformer-zh，独立进程）；
    SPEECH_BACKEND=mimo 时走 MiMo（可选提供方）；auto 模式下本地不可用才回退 MiMo。

    Returns:
        识别文本

    Raises:
        AudioServiceError: 携带 error_type（本地不可用时给出具体原因）
    """
    text = transcribe_audio_detailed(audio_data, filename=filename)["text"]
    return text


def transcribe_audio_detailed(audio_data: bytes, filename: Optional[str] = None) -> Dict[str, Any]:
    """
    语音识别（返回文本 + 服务来源信息，便于前端与验收核对"实际用的是什么"）

    Returns:
        {"text": str, "service": "local"|"mimo", "engine": str, "latency_ms": float}
    """
    if not audio_data or len(audio_data) == 0:
        raise AudioServiceError("音频数据为空", error_type="audio_invalid_format")

    if len(audio_data) > MAX_AUDIO_BYTES:
        raise AudioServiceError("音频文件过大（超过20MB）", error_type="audio_too_large")

    # 格式检测（优先扩展名，其次魔数）
    fmt = None
    if filename:
        ext = os.path.splitext(filename)[1].lower().lstrip(".")
        if ext in SUPPORTED_AUDIO_TYPES:
            fmt = ext
    if fmt is None:
        fmt = _detect_audio_format(audio_data)
    if fmt is None:
        raise AudioServiceError(
            "不支持的音频格式，请使用 WAV 或 MP3", error_type="audio_invalid_format")

    # ---------- 本地 ASR（默认路径）----------
    _local_asr_failure = ""
    mode = local_speech.backend_mode()
    if mode in ("local", "auto"):
        try:
            # 直接调用（不做前置健康探测：省一次往返，也避免服务忙时探测瞬时超时导致误判）
            result = local_speech.transcribe(audio_data, filename or f"audio.{fmt}")
            text = (result.get("text") or "").strip()
            if not text:
                raise AudioServiceError("本地识别结果为空，请说得更清楚一些",
                                        error_type="asr_empty_result")
            logger.info(f"[ASR] 本地识别完成：engine={result.get('engine')} "
                        f"chars={len(text)} latency_ms={result.get('latency_ms')}")
            return {"text": text, "service": "local",
                    "engine": result.get("engine") or "local",
                    "latency_ms": result.get("latency_ms"),
                    "duration_s": result.get("duration_s")}
        except local_speech.LocalSpeechError as e:
            if mode == "local":
                # 明确指定本地：不回退，如实报错
                raise AudioServiceError(
                    f"本地语音识别不可用：{e.message}", error_type=e.error_type)
            logger.warning(f"[ASR] 本地不可用，尝试 MiMo 回退：{e.message}")
            _local_asr_failure = e.message
        except AudioServiceError:
            raise

    # ---------- MiMo ASR（可选提供方）----------
    config = _get_audio_config("mimo-asr")
    api_key = config.get("api_key")
    if not api_key:
        raise AudioServiceError(
            "语音识别不可用：本地语音服务未就绪"
            + (f"（{_local_asr_failure}）" if _local_asr_failure else "")
            + "，且未配置 MiMo API Key",
            error_type="asr_api_error")

    url = normalize_chat_completions_url(config["base_url"])

    # Base64 → Data URL（不发送 Windows 路径）
    b64 = base64.b64encode(audio_data).decode("ascii")
    data_url = f"data:audio/{fmt};base64,{b64}"

    payload = {
        "model": config["model_name"],  # mimo-v2.5-asr
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": data_url},
                    }
                ],
            }
        ],
        "asr_options": {"language": "auto"},
    }

    # debug 日志：仅记录 provider/model/url（不打印 Base64 / Key）
    logger.debug(
        f"[ASR] request: provider=mimo-asr model={config.get('model_name')} "
        f"url={url} bytes={len(audio_data)}")

    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=ASR_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        raise AudioServiceError("语音识别超时，请重试", error_type="timeout")
    except requests.exceptions.ConnectionError:
        raise AudioServiceError("语音服务连接失败", error_type="connection_error")

    if resp.status_code != 200:
        _raise_status_error(resp.status_code, "asr_api_error")

    try:
        data = resp.json()
    except ValueError:
        raise AudioServiceError("语音识别响应解析失败", error_type="response_parse_error")

    # 识别文本：choices[0].message.content
    text = _extract_asr_text(data)
    text = text.strip()
    if not text:
        # 不再静默返回空串：调用方需要知道"没识别出来"而不是"识别成空"
        raise AudioServiceError("识别结果为空，请重新录制或说得更清楚一些",
                                error_type="asr_empty_result")
    return {"text": text, "service": "mimo", "engine": config.get("model_name"),
            "latency_ms": None, "duration_s": None}


def _extract_asr_text(data: Dict[str, Any]) -> str:
    """从 chat/completions 响应提取 ASR 文本（choices[0].message.content）"""
    try:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, str):
            return content
        # 兼容 content 为内容数组的情况
        if isinstance(content, list):
            return "".join(
                c.get("text", "") for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            )
        return str(content)
    except (KeyError, IndexError, TypeError):
        return ""


# ==================== 语音能力状态（本地 ASR / MiMo 可选） ====================
#
# 2026-09-26：默认语音链路改为**本地**（funasr 或轻量 SenseVoice，独立进程）。
# 这里分别报告四条链路的状态，便于 /health 与前端准确区分：
#   text / vision（在 api/app.py 里）+ local_asr（这里）+ mimo（可选提供方）
# 本地不可用时：如实给出原因（服务未启动 / 模型缺失 / 显存不足），文字功能不受影响。

_SPEECH_PROBE: Dict[str, Any] = {"ts": 0.0, "available": None, "reason": "", "model": ""}
SPEECH_PROBE_TTL_SECONDS = 600
SPEECH_PROBE_TIMEOUT = 6


def local_speech_status(force: bool = False) -> Dict[str, Any]:
    """本地语音服务状态（只有 ASR；TTS 已移除）"""
    h = local_speech.health(force=force)
    asr = h.get("asr") or {}
    return {
        "service": "local",
        "url": local_speech.base_url(),
        "running": bool(h.get("running")),
        "reason": h.get("reason") or "",
        "asr": {
            "provider": "local",
            "engine": asr.get("engine"),
            "available": bool(asr.get("ready")),
            "state": asr.get("state"),
            "model": asr.get("model"),
            "device": asr.get("device"),
            "load_seconds": asr.get("load_seconds"),
            "error": asr.get("error"),
            "error_type": asr.get("error_type"),
        },
        "vram": h.get("vram") or {"available": False},
        # 轻量模型的体积/下载状态：让前端能提示"需要下载 X MB"而不是只说不可用
        "models": _light_models_summary(h.get("models") or {}),
        "models_path_warning": h.get("models_path_warning"),
        "download": h.get("download") or {},
    }


def _light_models_summary(models: Dict[str, Any]) -> Dict[str, Any]:
    """把语音服务的模型清单压成前端好用的短结构（不含绝对路径）"""
    items = models.get("models") or []
    out = {"ready": [], "missing": [], "required_download_mb": models.get("required_download_mb"),
           "cache_dir": models.get("cache_dir")}
    for m in items:
        row = {"key": m.get("key"), "kind": m.get("kind"), "title": m.get("title"),
               "download_mb": m.get("download_mb"), "disk_mb": m.get("disk_mb"),
               "ready": bool(m.get("ready")), "status": m.get("status"),
               "license": m.get("license")}
        (out["ready"] if row["ready"] else out["missing"]).append(row)
    out["ready_count"] = len(out["ready"])
    out["total"] = len(items)
    return out


def mimo_speech_status(probe: bool = False) -> Dict[str, Any]:
    """MiMo 语音（可选提供方，仅含语音识别 ASR）状态

    probe=True 时做一次真实探测（默认关闭，避免拖慢 /health）
    """
    info = {
        "provider": "mimo",
        "asr_model": _get_audio_config("mimo-asr").get("model_name", ""),
        "available": False,
        "reason": "",
    }
    if not _get_audio_config("mimo-asr").get("api_key"):
        info["reason"] = "未配置 MiMo API Key（MIMO_API_KEY）"
        return info
    if probe:
        # MiMo 语音（TTS）已整体移除，这里不再做真实探测
        info["reason"] = "未探测（MiMo 仅保留 ASR 兼容代码，当前语音链路为本地）"
        return info
    info["reason"] = "未探测（MiMo 为可选提供方，当前默认语音链路为本地）"
    return info


def speech_capability(force: bool = False) -> Dict[str, Any]:
    """
    语音能力汇总（供 /health 与前端展示）

    Returns:
        {
          "backend": "local"|"mimo",
          "asr": {...},
          "local": {...}, "mimo": {...},
          "available": bool, "reason": str
        }
    """
    mode = local_speech.backend_mode()
    local = local_speech_status(force=force)
    # 强制 MiMo 时才做真实探测（避免默认路径打外网）
    mimo = mimo_speech_status(probe=force if mode == "mimo" else False)

    if mode == "mimo":
        chosen, asr = "mimo", None
        available = bool(mimo.get("available"))
        reason = mimo.get("reason") or ("" if available else "MiMo 语音当前不可用")
    else:
        chosen = "local"
        asr = local["asr"]

    if mode != "mimo":
        if local["running"]:
            available = bool(asr and asr.get("available"))
            reason = "" if available else (local["reason"] or
                                           (asr or {}).get("error") or
                                           "本地语音模型尚未就绪（首次使用需要下载/加载模型）")
        else:
            available = False
            reason = local["reason"] or "本地语音服务未启动"

    return {
        "backend": chosen,
        "available": available,
        "reason": reason,
        "asr": asr or {**mimo, "available": False},
        "local": local,
        "mimo": mimo,
        "asr_model": (asr or {}).get("model") or mimo.get("asr_model"),
    }


def warmup_local_speech() -> Dict[str, Any]:
    """预热本地语音模型（模型加载一次后常驻）"""
    try:
        data = local_speech.warmup()
    except local_speech.LocalSpeechError as e:
        raise AudioServiceError(f"本地语音服务不可用：{e.message}", e.error_type)
    local_speech.reset_health_cache()
    return {"engines": data.get("engines") or {}, "status": local_speech_status(force=True)}


def reset_speech_probe() -> None:
    """清空语音能力探测缓存（测试/运维用）"""
    _SPEECH_PROBE.update(ts=0.0, available=None, reason="", model="")


# 公开API
__all__ = [
    "AudioServiceError",
    "speech_capability",
    "local_speech_status",
    "mimo_speech_status",
    "transcribe_audio_detailed",
    "warmup_local_speech",
    "reset_speech_probe",
    "transcribe_audio",
    "normalize_chat_completions_url",
    "validate_wav_header",
    "SUPPORTED_AUDIO_TYPES",
]
