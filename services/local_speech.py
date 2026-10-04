"""
本地语音服务客户端（2026-09-26）

职责：把"本地语音服务（独立进程）"包装成主后端可用的接口。

设计（对应本轮要求）：
- 模型推理在主进程之外（这里是 HTTP 调用），主项目的 Python 环境不需要装 torch/funasr
- 健康状态带缓存，模型不可用时文字问答不受影响（调用方按 available=false 降级）
- 失败原因如实透出（模型缺失 / 依赖缺失 / 显存不足 / 服务未启动 …），不伪装成功
- 不做"用文本模型假装语音"的兜底

配置（环境变量）：
    SPEECH_BACKEND       local | mimo | auto   （默认 auto：本地优先，本地不可用再试 MiMo）
    LOCAL_SPEECH_URL     默认 http://127.0.0.1:8100
    LOCAL_SPEECH_TIMEOUT 单次请求超时（秒），默认 120
"""

import base64
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from services import capability_status
from utils.logger import logger

DEFAULT_URL = "http://127.0.0.1:8100"
HEALTH_TTL_SECONDS = 10
# 失败/超时结果的缓存时间（短）：避免一次瞬时超时把服务判死一段时间
FAILURE_TTL_SECONDS = 2
REQUEST_TIMEOUT = float(os.environ.get("LOCAL_SPEECH_TIMEOUT", "120"))
# /health 超时：模型合成时 GIL 争用可能变慢，给足余量
HEALTH_TIMEOUT = float(os.environ.get("LOCAL_SPEECH_HEALTH_TIMEOUT", "15"))


class LocalSpeechError(Exception):
    """本地语音服务异常（error_type 与 audio_service 的错误类型对齐）"""

    def __init__(self, message: str, error_type: str = "speech_unavailable"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


# 健康状态缓存：{ "ts": float, "data": {...} }
_HEALTH_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}


def base_url() -> str:
    return (os.environ.get("LOCAL_SPEECH_URL") or DEFAULT_URL).rstrip("/")


def backend_mode() -> str:
    """语音后端选择：local / mimo / auto（默认 auto）"""
    return (os.environ.get("SPEECH_BACKEND") or "auto").strip().lower()


def _port_open(timeout: float = 0.35) -> bool:
    """极短超时的本机端口探测

    为什么需要：Windows 上本机 TCP 连接被拒可能耗时约 2 s（实测
    `socket.create_connection(("127.0.0.1", 8100))` = 2.05 s，与 urllib/代理无关）。
    健康检查每次都走这条失败路径，会让 `/api/health` 多花 2 s。
    这里先用极短超时探一次，连不上立即按"未启动"返回。

    返回 True 时仍会走正常的 HTTP 请求（监听中的 loopback 连接是毫秒级）。
    """
    try:
        parsed = urllib.parse.urlparse(base_url())
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
    except Exception:
        return True          # 解析不了就不拦，交给 _http 原逻辑
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _http(method: str, path: str, body: Optional[dict] = None,
          timeout: float = REQUEST_TIMEOUT) -> Dict[str, Any]:
    url = base_url() + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ok_payload = json.loads(resp.read().decode("utf-8"))
        capability_status.safe_report(capability_status.CAP_SPEECH, True, reason=path)
        return ok_payload
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"message": raw[:200]}
        capability_status.safe_report(
            capability_status.CAP_SPEECH, False,
            reason=f"本地语音服务 HTTP {e.code}: {payload.get('message')}")
        raise LocalSpeechError(
            payload.get("message") or f"本地语音服务返回 {e.code}",
            payload.get("error_type") or "speech_error")
    except urllib.error.URLError as e:
        capability_status.safe_report(
            capability_status.CAP_SPEECH, False,
            reason=f"本地语音服务未启动或无法连接（{base_url()}）：{e.reason}")
        raise LocalSpeechError(
            f"本地语音服务未启动或无法连接（{base_url()}）：{e.reason}",
            "speech_unavailable")
    except TimeoutError:
        capability_status.safe_report(capability_status.CAP_SPEECH, False,
                                      reason="本地语音服务响应超时")
        raise LocalSpeechError("本地语音服务响应超时", "timeout")


def health(force: bool = False) -> Dict[str, Any]:
    """
    查询本地语音服务状态（带缓存）

    Returns:
        {"running": bool, "asr": {...}, "vram": {...}, "reason": str}
    """
    now = time.time()
    if not force and _HEALTH_CACHE["data"] is not None:
        # 成功结果缓存久一些；失败只短暂缓存——模型合成时 /health 可能瞬时超时，
        # 不能因此把服务判死一段时间（否则会误触发 MiMo 回退）
        ttl = (HEALTH_TTL_SECONDS if not _HEALTH_CACHE["data"].get("reason")
               else FAILURE_TTL_SECONDS)
        if now - _HEALTH_CACHE["ts"] < ttl:
            return _HEALTH_CACHE["data"]

    result: Dict[str, Any] = {
        "running": False, "asr": {"ready": False},
        "vram": {"available": False}, "reason": "",
    }
    # 启动性能修复（2026-10-03，实测驱动）：端口没开就立刻返回，
    # 避免 Windows 上约 2 s 的"连接被拒"等待（文案与 _http 的失败分支一致）。
    if not _port_open():
        reason = f"本地语音服务未启动或无法连接（{base_url()}）：连接被拒绝"
        # 与 _http 的失败分支保持一致：必须上报降级，否则能力状态会停留在"未调用"
        try:
            capability_status.safe_report(capability_status.CAP_SPEECH, False, reason=reason)
        except Exception:
            pass
        result["reason"] = reason
        _HEALTH_CACHE.update(ts=now, data=result)
        return result
    try:
        data = _http("GET", "/health", timeout=HEALTH_TIMEOUT)
        result["running"] = True
        result["asr"] = (data.get("engines") or {}).get("asr") or {"ready": False}
        result["vram"] = data.get("vram") or {"available": False}
        result["config"] = data.get("config") or {}
        # 轻量模型清单（体积/是否已下载/下载进度）——供前端在"首次使用前"
        # 明确告诉用户需要下载多少 MB，而不是只报"不可用"
        result["models"] = data.get("models") or {}
        result["models_path_warning"] = data.get("models_path_warning")
        result["download"] = data.get("download") or {}
    except LocalSpeechError as e:
        result["reason"] = e.message
    except Exception as e:  # 任何异常都不应影响文字问答
        result["reason"] = f"本地语音服务状态未知：{e}"

    _HEALTH_CACHE.update(ts=now, data=result)
    return result


def reset_health_cache() -> None:
    _HEALTH_CACHE.update(ts=0.0, data=None)


def is_enabled() -> bool:
    """本地语音是否启用（含健康判断）"""
    mode = backend_mode()
    if mode == "mimo":
        return False
    if mode == "local":
        return True
    return True  # auto：本地优先，不可用时由调用方回退


def asr_available() -> bool:
    h = health()
    return bool(h.get("running") and (h.get("asr") or {}).get("ready"))


def warmup() -> Dict[str, Any]:
    """手动预热（模型加载可能较慢）"""
    reset_health_cache()
    return _http("POST", "/warmup", timeout=max(REQUEST_TIMEOUT, 600))


def transcribe(audio_bytes: bytes, filename: str = "audio.wav") -> Dict[str, Any]:
    """
    本地 ASR（multipart 上传）

    Returns:
        {"text": str, "engine": str, "latency_ms": float, "duration_s": float}
    """
    boundary = "----eduspeech" + base64.urlsafe_b64encode(
        os.urandom(12)).decode("ascii").strip("=")
    parts: List[bytes] = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode())
    parts.append(audio_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    payload = b"".join(parts)

    req = urllib.request.Request(
        base_url() + "/asr", data=payload, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            payload_json = json.loads(raw)
        except Exception:
            payload_json = {"message": raw[:200]}
        raise LocalSpeechError(payload_json.get("message") or f"本地识别失败（{e.code}）",
                               payload_json.get("error_type") or "asr_failed")
    except urllib.error.URLError as e:
        raise LocalSpeechError(f"本地语音服务未启动（{e.reason}）", "speech_unavailable")
    if not data.get("success"):
        raise LocalSpeechError(data.get("message") or "本地识别失败",
                               data.get("error_type") or "asr_failed")
    return data


def ensure_available(what: str = "speech") -> None:
    """给调用方用：不可用时抛带原因的异常"""
    h = health()
    if not h.get("running"):
        raise LocalSpeechError(
            h.get("reason") or "本地语音服务未启动（请运行 python -m speech_service.server）",
            "speech_unavailable")
    engine = h.get(what) or {}
    if not engine.get("ready"):
        reason = engine.get("error") or f"{what} 引擎未就绪（state={engine.get('state')}）"
        raise LocalSpeechError(reason, engine.get("error_type") or "engine_not_ready")


__all__ = [
    "LocalSpeechError",
    "backend_mode",
    "base_url",
    "health",
    "reset_health_cache",
    "is_enabled",
    "asr_available",
    "warmup",
    "transcribe",
    "ensure_available",
]
