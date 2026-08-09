"""
语音服务模块（V2.5.1 Audio Runtime Fix）

MiMo ASR/TTS 均走 OpenAI 兼容 /v1/chat/completions（不是 /audio/* 路径）：

- ASR: mimo-v2.5-asr
    {base}/chat/completions
    {
      "model": "mimo-v2.5-asr",
      "messages": [{"role":"user","content":[{"type":"input_audio","input_audio":{"data":"data:audio/wav;base64,..."}}]}],
      "asr_options": {"language":"auto"}
    }
    识别文本：choices[0].message.content

- TTS: mimo-v2.5-tts
    {base}/chat/completions
    {
      "model": "mimo-v2.5-tts",
      "messages": [{"role":"assistant","content":"要朗读的文本"}],
      "audio": {"format":"wav","voice":"mimo_default"}
    }
    音频：choices[0].message.audio.data（base64）→ decode → WAV bytes

复用 MIMO_API_KEY / MIMO_BASE_URL，URL 规范化保证只拼一次 /chat/completions。
不发送 Windows 路径；不打印 Base64 / API Key / Authorization / 完整音频。

错误类型：
- audio_endpoint_not_found / invalid_api_key / unauthorized / rate_limited
- audio_invalid_format / asr_api_error / tts_api_error
- timeout / connection_error / response_parse_error
"""

import base64
import hashlib
import json
import os
import re
import requests
import logging
import time
from typing import Optional, Dict, Any

from utils.logger import logger

# 音频大小限制（20MB，与知识库上传一致）
MAX_AUDIO_BYTES = 20 * 1024 * 1024

# 支持的音频格式（MiMo-V2.5-ASR 支持 wav/mp3）
SUPPORTED_AUDIO_TYPES = ("wav", "mp3")

# 默认 TTS 音色（第一版使用预置音色，不实现 voice clone / voice design）
DEFAULT_TTS_VOICE = "mimo_default"

# 超时
ASR_TIMEOUT = 30
TTS_TIMEOUT = 30

# TTS 缓存目录与 TTL（V2.5.3）
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TTS_CACHE_DIR = os.path.join(_DATA_DIR, "cache", "tts")
TTS_CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7天
# 缓存容量上限：超过则清除最旧文件（防止无限增长）
TTS_CACHE_MAX_FILES = 200

# HTTP 状态 → 结构化错误类型映射（禁止暴露上游响应正文）
_STATUS_TO_ERROR = {
    400: ("audio_invalid_format", "音频格式或请求参数错误"),
    401: ("invalid_api_key", "语音服务 API Key 无效"),
    403: ("unauthorized", "语音服务访问被拒绝"),
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
    """获取 ASR/TTS 模型配置（复用 MiMo API Key 与 Base URL）"""
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
    语音识别：音频 → 文本（mimo-v2.5-asr，走 /chat/completions）

    Args:
        audio_data: 音频字节（WAV 或 MP3）
        filename: 文件名（用于判断扩展名，可选）

    Returns:
        识别文本

    Raises:
        AudioServiceError: 携带 error_type
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

    config = _get_audio_config("mimo-asr")
    api_key = config.get("api_key")
    if not api_key:
        raise AudioServiceError("语音识别服务未配置 API Key", error_type="asr_api_error")

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
        logger.warning("[ASR] 识别结果为空")
        return ""
    return text


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


# ============ TTS（文本转语音） ============

# Markdown/来源/内部信息清理正则（V2.5.3）
_MD_FENCE_RE = re.compile(r"```[^\n]*[\s\S]*?```|~~~[^\n]*[\s\S]*?~~~")
_MD_INLINE_RE = re.compile(r"[*_`#>\-]")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
# 整行 UI 内部信息：参考来源/模型/首字响应/总耗时/TTFT 等（含前后缀组合）
_SOURCE_LINE_RE = re.compile(
    r"^\s*(?:参考来源|来源|模型|首字响应|总耗时|TTFT|已用|参考|已生成|token)[^。\n]*[:：]?.*$",
    re.M)
# 混在句子里的 UI 内部信息片段
_UI_INFO_FRAG_RE = re.compile(
    r"(?:模型[:：][^，。|;；]*|首字响应[:：][0-9.]+s?|总耗时[:：][0-9.]+s?|TTFT[:：][0-9.]+s?|参考来源[:：][^\n]*)")
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u2B00-\u2BFF]")


def prepare_tts_text(text: str, max_chars: Optional[int] = None) -> str:
    """
    TTS 朗读文本清洗（V2.5.3）

    去除不适宜朗读的内容：
    - Markdown 符号（* _ ` # > -）
    - 代码块（替换为可读提示）
    - 来源标注、模型名称、TTFT、总耗时等 UI 内部信息
    - URL
    - 复杂 LaTeX 公式（不逐字符朗读，替换为可读描述）
    - emoji

    保留原始句子结构，不改变核心语义。

    Args:
        text: 原始回答文本
        max_chars: 最大字符数（可选，超过则在句子边界截断）

    Returns:
        可朗读的干净文本
    """
    if not text:
        return ""

    # 1. 代码块 → 可读提示
    has_code = bool(_MD_FENCE_RE.search(text))
    cleaned = _MD_FENCE_RE.sub("这里包含一段代码，请查看屏幕内容。", text)

    # 2. 复杂 LaTeX（\frac{a}{b} 等）→ 简单描述（含残留反斜杠）
    cleaned = re.sub(r"\\\\[a-zA-Z]+\{[^}]*\}(\{[^}]*\})?", "公式", cleaned)
    cleaned = re.sub(r"\\[a-zA-Z]+\{[^}]*\}(\{[^}]*\})?", "公式", cleaned)

    # 3. 来源/内部信息行
    cleaned = _SOURCE_LINE_RE.sub("", cleaned)

    # 4. 混在句子里的 UI 内部信息片段（模型/耗时等）
    cleaned = _UI_INFO_FRAG_RE.sub("", cleaned)

    # 5. URL
    cleaned = _URL_RE.sub("", cleaned)

    # 6. Markdown 行内符号
    cleaned = _MD_INLINE_RE.sub("", cleaned)

    # 7. emoji
    cleaned = _EMOJI_RE.sub("", cleaned)

    # 8. 压缩多余空白
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    # 代码块提示只追加一次（URL/内部信息移除后句子可能被粘连）
    if has_code and "这里包含一段代码，请查看屏幕内容。" not in cleaned:
        if cleaned:
            cleaned += "。"
        cleaned += "这里包含一段代码，请查看屏幕内容。"

    # 9. 长度限制：在句子边界截断
    if max_chars is not None and len(cleaned) > max_chars:
        cut = cleaned[:max_chars]
        # 优先在句号/问号/感叹号/换行处截断（边界在 30% 之后即可，保证完整句子）
        for sep in ("。", "！", "？", "\n", ".", "!", "?"):
            idx = cut.rfind(sep)
            if idx > max_chars * 0.3:
                cleaned = cut[: idx + 1]
                break
        else:
            cleaned = cut.rsplit("，", 1)[0] if "，" in cut else cut
        cleaned = cleaned.strip()

    return cleaned


def _tts_cache_key(model: str, voice: str, text: str) -> str:
    """TTS 缓存 key：sha256(model + voice + normalized_text)"""
    raw = f"{model}|{voice}|{text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _tts_cache_get(cache_key: str) -> Optional[bytes]:
    """读取 TTS 缓存（TTL 过期视为未命中）"""
    path = os.path.join(TTS_CACHE_DIR, f"{cache_key}.wav")
    try:
        if not os.path.exists(path):
            return None
        if time.time() - os.path.getmtime(path) > TTS_CACHE_TTL_SECONDS:
            return None
        with open(path, "rb") as f:
            return f.read()
    except Exception as e:
        logger.warning(f"[TTS] 缓存读取失败（忽略）: {e}")
        return None


def _tts_cache_set(cache_key: str, audio: bytes) -> None:
    """写入 TTS 缓存（容量超限时清除最旧文件）"""
    try:
        os.makedirs(TTS_CACHE_DIR, exist_ok=True)
        path = os.path.join(TTS_CACHE_DIR, f"{cache_key}.wav")
        # 临时文件 + 原子替换，避免并发写半截
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(audio)
        os.replace(tmp, path)

        # 容量控制：超过上限清除最旧文件
        try:
            files = [
                (os.path.getmtime(os.path.join(TTS_CACHE_DIR, fn)),
                 os.path.join(TTS_CACHE_DIR, fn))
                for fn in os.listdir(TTS_CACHE_DIR) if fn.endswith(".wav")
            ]
            if len(files) > TTS_CACHE_MAX_FILES:
                files.sort()
                for _, old_path in files[: len(files) - TTS_CACHE_MAX_FILES]:
                    try:
                        os.remove(old_path)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"[TTS] 缓存容量清理失败（忽略）: {e}")
    except Exception as e:
        logger.warning(f"[TTS] 缓存写入失败（不影响返回）: {e}")


def synthesize_speech(text: str, voice: Optional[str] = None, style: Optional[str] = None) -> bytes:
    """
    语音合成：文本 → 音频（mimo-v2.5-tts，走 /chat/completions）

    Args:
        text: 待合成文本
        voice: 音色（可选，默认 mimo_default，预置音色）
        style: 风格（可选，结构化预留，不实现 voiceclone/voicedesign）

    Returns:
        WAV 音频字节

    Raises:
        AudioServiceError: 携带 error_type
    """
    if not text or not text.strip():
        raise AudioServiceError("文本内容不能为空", error_type="empty_text")

    # V2.5.3：TTS 朗读文本清洗（去 Markdown/来源/URL/代码块/内部信息）
    clean_text = prepare_tts_text(text)
    if not clean_text:
        raise AudioServiceError("文本清洗后为空，无法合成", error_type="empty_text")
    clean_text = clean_text[:2000]

    voice = voice or DEFAULT_TTS_VOICE

    config = _get_audio_config("mimo-tts")
    api_key = config.get("api_key")
    if not api_key:
        raise AudioServiceError("语音合成服务未配置 API Key", error_type="tts_api_error")

    # V2.5.3：后端缓存（key = sha256(model+voice+clean_text)）
    cache_key = _tts_cache_key(config["model_name"], voice, clean_text)
    cached = _tts_cache_get(cache_key)
    if cached is not None:
        logger.debug(f"[TTS] cache hit: model={config['model_name']} chars={len(clean_text)}")
        return cached

    url = normalize_chat_completions_url(config["base_url"])

    payload = {
        "model": config["model_name"],  # mimo-v2.5-tts
        "messages": [
            {"role": "assistant", "content": clean_text},
        ],
        "audio": {
            "format": "wav",
            "voice": voice,
        },
    }
    if style:
        payload["audio"]["style"] = style

    logger.debug(
        f"[TTS] request: provider=mimo-tts model={config.get('model_name')} "
        f"url={url} voice={voice} chars={len(clean_text)}")

    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=TTS_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        raise AudioServiceError("语音合成超时，请重试", error_type="timeout")
    except requests.exceptions.ConnectionError:
        raise AudioServiceError("语音服务连接失败", error_type="connection_error")

    if resp.status_code != 200:
        _raise_status_error(resp.status_code, "tts_api_error")

    try:
        data = resp.json()
    except ValueError:
        raise AudioServiceError("语音合成响应解析失败", error_type="response_parse_error")

    # 音频：choices[0].message.audio.data（base64）→ decode → WAV bytes
    audio_b64 = _extract_tts_audio_b64(data)
    if not audio_b64:
        raise AudioServiceError("语音合成响应中无音频数据", error_type="response_parse_error")

    try:
        audio_bytes = base64.b64decode(audio_b64)
    except (ValueError, TypeError) as e:
        logger.error(f"[TTS] Base64 解码失败: {type(e).__name__}")
        raise AudioServiceError("语音合成音频解码失败", error_type="response_parse_error")

    if not audio_bytes:
        raise AudioServiceError("语音合成返回空音频", error_type="response_parse_error")

    # V2.5.3：成功才写缓存（失败结果不写入）
    _tts_cache_set(cache_key, audio_bytes)

    return audio_bytes


def _extract_tts_audio_b64(data: Dict[str, Any]) -> str:
    """从 chat/completions 响应提取 TTS 音频 base64（choices[0].message.audio.data）"""
    try:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        audio = message.get("audio") or {}
        b64 = audio.get("data") or ""
        if isinstance(b64, str) and b64.startswith("data:audio"):
            # 兼容 Data URL 前缀：data:audio/wav;base64,...
            b64 = b64.split(",", 1)[-1]
        return b64
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


# 公开API
__all__ = [
    "AudioServiceError",
    "transcribe_audio",
    "synthesize_speech",
    "prepare_tts_text",
    "normalize_chat_completions_url",
    "validate_wav_header",
    "SUPPORTED_AUDIO_TYPES",
]
