"""
语音服务模块（2026-09-26：本地 ASR/TTS 为主，MiMo 为可选提供方）

**语音链路选择**（SPEECH_BACKEND=auto|local|mimo，默认 auto）：
- local：调用独立进程的本地语音服务（FunASR + CosyVoice，见 speech_service/）
- mimo ：保留原有的 MiMo ASR/TTS 适配（可选提供方，会员到期时不可用）
- auto ：本地可用时用本地；本地不可用时再尝试 MiMo；都不可用 → 如实报错

本地语音服务不可用（未启动 / 模型缺失 / 显存不足）时：
- 文字问答、学习闭环等功能完全不受影响
- 语音接口返回明确原因（error_type + message），**不会**用文本模型假装完成语音处理

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
from services.tts_text import readable_text, split_sentences

# 音频大小限制（20MB，与知识库上传一致）
MAX_AUDIO_BYTES = 20 * 1024 * 1024

# 支持的音频格式（MiMo-V2.5-ASR 支持 wav/mp3）
SUPPORTED_AUDIO_TYPES = ("wav", "mp3")

# 默认 TTS 音色（第一版使用预置音色，不实现 voice clone / voice design）
DEFAULT_TTS_VOICE = "mimo_default"

# 超时
ASR_TIMEOUT = 30
TTS_TIMEOUT = 30
# MiMo TTS 单次请求的文本上限（超出不再静默截断，而是提示改用逐句接口）
MAX_MIMO_TTS_CHARS = 2000

# TTS 缓存目录与 TTL（V2.5.3）
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TTS_CACHE_DIR = os.path.join(_DATA_DIR, "cache", "tts")
TTS_CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7天
# 缓存容量上限：超过则清除最旧文件（防止无限增长）
TTS_CACHE_MAX_FILES = 200

# HTTP 状态 → 结构化错误类型映射（禁止暴露上游响应正文）
_STATUS_TO_ERROR = {
    400: ("audio_invalid_format", "音频格式或请求参数错误"),
    # 2026-09-25：语音能力由 MiMo 提供（mimo-v2.5-asr / mimo-v2.5-tts）。
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
    #    注意：这是**调用方显式要求**的上限（max_chars 非空）才会触发；
    #    正常朗读路径不再传 max_chars，由 services/tts_text.split_sentences 按句切分且不丢字。
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


def synthesize_speech(text: str, voice: Optional[str] = None, style: Optional[str] = None,
                      timeout: Optional[int] = None) -> bytes:
    """
    语音合成：文本 → 音频（mimo-v2.5-tts，走 /chat/completions）

    Args:
        text: 待合成文本
        voice: 音色（可选，默认 mimo_default，预置音色）
        style: 风格（可选，结构化预留，不实现 voiceclone/voicedesign）
        timeout: 请求超时秒数（可选；能力探测时用较短超时，避免拖慢 /health）

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
    if len(clean_text) > MAX_MIMO_TTS_CHARS:
        # 旧实现是 clean_text[:2000] 静默砍掉后半段 —— 现在明确报错并给出正确用法
        raise AudioServiceError(
            f"文本过长（{len(clean_text)} 字 > {MAX_MIMO_TTS_CHARS} 字上限）："
            f"请改用逐句合成接口 /api/audio/tts（会自动按句切分且不丢字）",
            error_type="text_too_long")

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
    _skip_cache = bool(timeout)  # 能力探测这种短超时调用不走缓存写入

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
            timeout=timeout or TTS_TIMEOUT,
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

    # V2.5.3：成功才写缓存（失败结果不写入；能力探测的短超时调用跳过缓存）
    if not _skip_cache:
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


# ==================== 逐句 TTS（本地优先，MiMo 可选） ====================

def synthesize_segments(text: str, voice: Optional[str] = None, speed: float = 1.0,
                        max_chars: Optional[int] = None) -> Dict[str, Any]:
    """
    把一段回答转成"按句切分的语音"，供前端顺序播放

    流程：
        1. 朗读文本规范化（services/tts_text）：去 Markdown、代码块/公式/表格改为可读提示
        2. 按句子切成短段（**不丢字**；超长句在次级边界续切）
        3. 本地 TTS 逐段合成（本地不可用且允许回退时，用 MiMo 逐段合成）

    Args:
        text: 原始回答
        voice: 音色（本地为 CosyVoice 预置音色；MiMo 为 mimo_default）
        speed: 语速（本地 TTS 支持）
        max_chars: 朗读长度上限；**超过时显式返回 truncated/omitted_chars**，不静默丢弃

    Returns:
        {
          "items": [{"seq", "text", "audio_b64", "duration_s", "latency_ms"}],
          "service": "local"|"mimo", "engine": str, "voice": str, "sample_rate": int,
          "segments_total": int, "truncated": bool, "omitted_chars": int, "hints": [str]
        }

    Raises:
        AudioServiceError: 携带 error_type（不可用时给出具体原因）
    """
    prep = readable_text(text or "", max_chars=max_chars)
    readable = prep["text"]
    if not readable.strip():
        raise AudioServiceError("没有可朗读的内容", error_type="empty_text")

    segments = split_sentences(readable)
    if not segments:
        raise AudioServiceError("没有可朗读的内容", error_type="empty_text")

    local_failure = ""
    mode = local_speech.backend_mode()
    if mode in ("local", "auto"):
        try:
            # 直接调用（同上：不做前置探测）
            data = local_speech.synthesize_segments(segments, voice=voice, speed=speed)
            items = [{"seq": it.get("seq"), "text": it.get("text"),
                      "audio_b64": it.get("audio_b64"),
                      "duration_s": it.get("duration_s"),
                      "latency_ms": it.get("latency_ms")}
                     for it in (data.get("items") or [])]
            logger.info(f"[TTS] 本地合成完成：engine={data.get('engine')} "
                        f"voice={data.get('voice')} segments={len(items)}")
            return {
                "items": items, "service": "local",
                # 实际用于朗读的文本（归一化 + 切分后）——便于核对"读的是什么"
                "speech_text": readable,
                "segments_text": segments,
                "engine": data.get("engine") or "local",
                "voice": data.get("voice") or (voice or ""),
                "sample_rate": data.get("sample_rate") or 22050,
                "segments_total": len(segments),
                "truncated": prep["truncated"], "omitted_chars": prep["omitted_chars"],
                "hints": prep["hints"],
            }
        except local_speech.LocalSpeechError as e:
            if mode == "local":
                raise AudioServiceError(f"本地语音合成不可用：{e.message}",
                                        error_type=e.error_type)
            logger.warning(f"[TTS] 本地不可用，尝试 MiMo 回退：{e.message}")
            local_failure = e.message

    # ---------- MiMo TTS（可选提供方，逐段合成） ----------
    items = []
    sample_rate = None
    for seq, seg in enumerate(segments):
        try:
            wav = synthesize_speech(seg, voice=voice)  # 内部已含 MiMo 的清洗/缓存/错误映射
        except AudioServiceError as e:
            if local_failure:
                # 两条链路都失败：把本地失败原因一并报出，避免误导为"只是 MiMo 的问题"
                raise AudioServiceError(
                    f"语音合成不可用：本地语音服务失败（{local_failure}）；"
                    f"MiMo 回退也失败（{e.message}）",
                    error_type=e.error_type)
            raise
        items.append({"seq": seq, "text": seg, "audio_b64": base64.b64encode(wav).decode("ascii"),
                      "duration_s": None, "latency_ms": None})
    return {
        "items": items, "service": "mimo", "speech_text": readable,
        "segments_text": segments,
        "engine": _get_audio_config("mimo-tts").get("model_name"),
        "voice": voice or DEFAULT_TTS_VOICE, "sample_rate": sample_rate or 24000,
        "segments_total": len(segments),
        "truncated": prep["truncated"], "omitted_chars": prep["omitted_chars"],
        "hints": prep["hints"],
    }


def synthesize_speech_detailed(text: str, voice: Optional[str] = None,
                               max_chars: Optional[int] = None) -> Dict[str, Any]:
    """
    单段合成（保留给"播放一条消息"这类简单场景）：返回合并后的 WAV

    与 synthesize_segments 的区别：这里把逐段音频在服务端拼成一段，
    适合不需要"边播边合成"的调用方。
    """
    result = synthesize_segments(text, voice=voice, max_chars=max_chars)
    merged = concat_wav_base64([it["audio_b64"] for it in result["items"]],
                               result.get("sample_rate") or 22050)
    result["audio_b64"] = merged
    return result


def concat_wav_base64(items: List[str], sample_rate: int) -> str:
    """把多段 WAV(base64) 顺序拼接成一段（保证顺序，不丢句）"""
    import wave

    frames = bytearray()
    params = None
    for b64 in items:
        if not b64:
            continue
        raw = base64.b64decode(b64)
        try:
            with wave.open(io.BytesIO(raw), "rb") as w:
                if params is None:
                    params = w.getparams()
                frames += w.readframes(w.getnframes())
        except Exception as e:
            logger.warning(f"[TTS] 拼接跳过损坏分段：{e}")
    if params is None:
        raise AudioServiceError("没有可拼接的音频", error_type="tts_failed")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(bytes(frames))
    return base64.b64encode(out.getvalue()).decode("ascii")


# ==================== 语音能力状态（本地 ASR / 本地 TTS / MiMo 可选） ====================
#
# 2026-09-26：默认语音链路改为**本地**（FunASR + CosyVoice，独立进程）。
# 这里分别报告四条链路的状态，便于 /health 与前端准确区分：
#   text / vision（在 api/app.py 里）+ local_asr / local_tts（这里）+ mimo（可选提供方）
# 本地不可用时：如实给出原因（服务未启动 / 模型缺失 / 显存不足），文字功能不受影响。

_SPEECH_PROBE: Dict[str, Any] = {"ts": 0.0, "available": None, "reason": "", "model": ""}
SPEECH_PROBE_TTL_SECONDS = 600
SPEECH_PROBE_TIMEOUT = 6


def local_speech_status(force: bool = False) -> Dict[str, Any]:
    """本地语音服务状态（ASR / TTS 分开）"""
    h = local_speech.health(force=force)
    asr = h.get("asr") or {}
    tts = h.get("tts") or {}
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
        "tts": {
            "provider": "local",
            "engine": tts.get("engine"),
            "available": bool(tts.get("ready")),
            "state": tts.get("state"),
            "model_dir": tts.get("model_dir"),
            "voices": tts.get("voices") or [],
            "device": tts.get("device"),
            "load_seconds": tts.get("load_seconds"),
            "error": tts.get("error"),
            "error_type": tts.get("error_type"),
        },
        "vram": h.get("vram") or {"available": False},
    }


def mimo_speech_status(probe: bool = False) -> Dict[str, Any]:
    """MiMo 语音（可选提供方）状态；probe=True 时做一次真实探测（默认关闭，避免拖慢 /health）"""
    info = {
        "provider": "mimo",
        "asr_model": _get_audio_config("mimo-asr").get("model_name", ""),
        "tts_model": _get_audio_config("mimo-tts").get("model_name", ""),
        "available": False,
        "reason": "",
    }
    if not _get_audio_config("mimo-tts").get("api_key"):
        info["reason"] = "未配置 MiMo API Key（MIMO_API_KEY）"
        return info
    if probe:
        # 真实探测一次（成功才算可用；凭证被拒时给出真实原因）
        probed = _probe_mimo_tts(force=True)
        info.update({k: v for k, v in probed.items()
                     if k in ("available", "reason", "provider", "asr_model", "tts_model")})
        return info
    info["reason"] = "未探测（MiMo 为可选提供方，当前默认语音链路为本地）"
    return info


def speech_capability(force: bool = False) -> Dict[str, Any]:
    """
    语音能力汇总（供 /health 与前端展示）

    Returns:
        {
          "backend": "local"|"mimo",
          "asr": {...}, "tts": {...},
          "local": {...}, "mimo": {...},
          "available": bool, "reason": str
        }
    """
    mode = local_speech.backend_mode()
    local = local_speech_status(force=force)
    # 强制 MiMo 时才做真实探测（避免默认路径打外网）
    mimo = mimo_speech_status(probe=force if mode == "mimo" else False)

    if mode == "mimo":
        chosen, asr, tts = "mimo", None, None
        available = bool(mimo.get("available"))
        reason = mimo.get("reason") or ("" if available else "MiMo 语音当前不可用")
    else:
        chosen = "local"
        asr, tts = local["asr"], local["tts"]

    if mode == "mimo":
        pass
    elif local["running"]:
        available = bool(asr and asr.get("available")) and bool(tts and tts.get("available"))
        reason = "" if available else (local["reason"] or
                                       (asr or {}).get("error") or (tts or {}).get("error") or
                                       "本地语音模型尚未就绪（首次使用需要下载/加载模型）")
    else:
        available = False
        reason = local["reason"] or "本地语音服务未启动"

    return {
        "backend": chosen,
        "available": available,
        "reason": reason,
        "asr": asr or {**mimo, "available": False},
        "tts": tts or {**mimo, "available": False},
        "local": local,
        "mimo": mimo,
        "asr_model": (asr or {}).get("model") or mimo.get("asr_model"),
        "tts_model": (tts or {}).get("engine") or mimo.get("tts_model"),
    }


def list_local_voices() -> Dict[str, Any]:
    """本地可用音色（本地不可用时给出原因，不返回假音色）"""
    status = local_speech_status()
    if not status["running"]:
        return {"voices": [], "default": "", "engine": "local",
                "reason": status["reason"] or "本地语音服务未启动"}
    voices = (status["tts"] or {}).get("voices") or []
    if not voices:
        voices_local = local_speech.list_voices()
        voices = voices_local
    return {
        "voices": voices,
        "default": ((status["tts"] or {}).get("engine") and
                    (status["tts"] or {}).get("default_voice")) or
                   (voices[0] if voices else ""),
        "engine": (status["tts"] or {}).get("engine") or "local",
        "available": bool((status["tts"] or {}).get("available")),
        "reason": "" if voices else ((status["tts"] or {}).get("error") or
                                    "本地 TTS 尚未就绪"),
    }


def warmup_local_speech() -> Dict[str, Any]:
    """预热本地语音模型（模型加载一次后常驻）"""
    try:
        data = local_speech.warmup()
    except local_speech.LocalSpeechError as e:
        raise AudioServiceError(f"本地语音服务不可用：{e.message}", e.error_type)
    local_speech.reset_health_cache()
    return {"engines": data.get("engines") or {}, "status": local_speech_status(force=True)}


def _probe_mimo_tts(force: bool = False) -> Dict[str, Any]:
    """
    对 MiMo TTS 做一次真实探测（带缓存）

    返回 {"provider": "mimo", "available": bool, "reason": str, ...}
    说明：这是**可选提供方**的探测；默认语音链路是本地，不会在 /health 里被动调用它。
    """
    import time as _time

    asr_model = _get_audio_config("mimo-asr").get("model_name", "")
    tts_model = _get_audio_config("mimo-tts").get("model_name", "")
    info = {"provider": "mimo", "asr_model": asr_model, "tts_model": tts_model,
            "available": False, "reason": ""}

    api_key = _get_audio_config("mimo-tts").get("api_key")
    if not api_key:
        info["reason"] = "未配置 MiMo API Key（MIMO_API_KEY）"
        return info

    now = _time.time()
    if (not force and _SPEECH_PROBE["available"] is not None
            and now - _SPEECH_PROBE["ts"] < SPEECH_PROBE_TTL_SECONDS):
        info["available"] = bool(_SPEECH_PROBE["available"])
        info["reason"] = _SPEECH_PROBE["reason"]
        return info

    try:
        synthesize_speech("你好", timeout=SPEECH_PROBE_TIMEOUT)
        _SPEECH_PROBE.update(ts=now, available=True, reason="", model=tts_model)
        info["available"] = True
    except AudioServiceError as e:
        reason = e.message
        if e.error_type in ("invalid_api_key", "unauthorized"):
            reason = f"MiMo 语音凭证被供应商拒绝（会员到期/凭证失效）；错误类型 {e.error_type}"
        _SPEECH_PROBE.update(ts=now, available=False, reason=reason, model=tts_model)
        info["reason"] = reason
    except Exception as e:
        reason = f"MiMo 语音探测失败：{e}"
        _SPEECH_PROBE.update(ts=now, available=False, reason=reason, model=tts_model)
        info["reason"] = reason
    return info


def reset_speech_probe() -> None:
    """清空语音能力探测缓存（测试/运维用）"""
    _SPEECH_PROBE.update(ts=0.0, available=None, reason="", model="")


# 公开API
__all__ = [
    "AudioServiceError",
    "speech_capability",
    "local_speech_status",
    "mimo_speech_status",
    "synthesize_segments",
    "synthesize_speech_detailed",
    "transcribe_audio_detailed",
    "concat_wav_base64",
    "list_local_voices",
    "warmup_local_speech",
    "reset_speech_probe",
    "transcribe_audio",
    "synthesize_speech",
    "prepare_tts_text",
    "normalize_chat_completions_url",
    "validate_wav_header",
    "SUPPORTED_AUDIO_TYPES",
]
