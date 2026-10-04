# -*- coding: utf-8 -*-
"""MiMo 语音识别（ASR）服务测试（V2.5.1 Audio Runtime Fix）

全部 Mock API 调用（禁止真实调用 MiMo）。
覆盖：URL / payload / response parse / Base64 decode / 404 401 timeout 映射 /
     ASR 请求/错误契约。
"""

import base64
import json
import os
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from tests.fixtures_isolated import IsolatedDataTestCase, register_and_login
from services import audio_service
from services.audio_service import (
    transcribe_audio,
    AudioServiceError,
    validate_wav_header,
    normalize_chat_completions_url,
)


def make_wav_bytes(seconds: float = 0.1, sample_rate: int = 16000) -> bytes:
    """构造一个最小的合法 WAV 文件字节"""
    num_samples = int(seconds * sample_rate)
    data_size = num_samples * 2  # 16-bit mono
    header = bytearray(44)
    header[0:4] = b"RIFF"
    struct.pack_into("<I", header, 4, 36 + data_size)
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    struct.pack_into("<I", header, 16, 16)
    struct.pack_into("<H", header, 20, 1)   # PCM
    struct.pack_into("<H", header, 22, 1)   # mono
    struct.pack_into("<I", header, 24, sample_rate)
    struct.pack_into("<I", header, 28, sample_rate * 2)
    struct.pack_into("<H", header, 32, 2)
    struct.pack_into("<H", header, 34, 16)
    header[36:40] = b"data"
    struct.pack_into("<I", header, 40, data_size)
    return bytes(header) + bytes(data_size)  # 静音


def _config(base_url="https://token-plan-cn.xiaomimimo.com/v1"):
    """Mock _get_audio_config 返回的配置"""
    return {"base_url": base_url, "api_key": "test-key", "model_name": "mimo-v2.5-asr"}



class FakeResponse:
    """可注入 json 返回值的 mock 响应"""

    def __init__(self, status_code=200, json_data=None, content=b"", text=""):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.text = text

    def json(self):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json


def _asr_success_json(text="二叉树的定义"):
    return {"choices": [{"message": {"content": text}}]}


def _tts_success_json(wav=None):
    wav = wav or make_wav_bytes()
    return {"choices": [{"message": {"audio": {"data": base64.b64encode(wav).decode("ascii")}}}]}


class URLNormalizeTest(unittest.TestCase):
    def test_normalize_basic_v1(self):
        self.assertEqual(
            normalize_chat_completions_url("https://api.xiaomimimo.com/v1"),
            "https://api.xiaomimimo.com/v1/chat/completions")

    def test_normalize_trailing_slash(self):
        self.assertEqual(
            normalize_chat_completions_url("https://api.xiaomimimo.com/v1/"),
            "https://api.xiaomimimo.com/v1/chat/completions")

    def test_normalize_idempotent(self):
        self.assertEqual(
            normalize_chat_completions_url("https://api.xiaomimimo.com/v1/chat/completions"),
            "https://api.xiaomimimo.com/v1/chat/completions")

    def test_normalize_strips_audio_residual(self):
        """兼容历史错误配置：剥离 /audio/* 残留路径"""
        self.assertEqual(
            normalize_chat_completions_url("https://api.xiaomimimo.com/v1/audio/speech"),
            "https://api.xiaomimimo.com/v1/chat/completions")

    def test_no_double_v1(self):
        """不出现 /v1/v1/chat/completions"""
        url = normalize_chat_completions_url("https://api.xiaomimimo.com/v1")
        self.assertNotIn("/v1/v1", url)


class AudioServiceTest(unittest.TestCase):
    """Service 层单元测试（Mock requests）"""

    def setUp(self):
        # 本类用例验证的是 **MiMo 可选提供方（ASR）** 的请求/错误契约：
        # 固定 SPEECH_BACKEND=mimo，避免本机开着本地语音服务时走本地分支。
        # （TTS 缓存夹具已随回答朗读功能一并移除。）
        self._env_patch = mock.patch.dict(os.environ, {"SPEECH_BACKEND": "mimo"})
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def _patch_asr(self):
        return mock.patch("services.audio_service._get_audio_config",
                          return_value=_config())

    # ===== WAV / 格式检测 =====

    def test_validate_wav_header_valid(self):
        self.assertTrue(validate_wav_header(make_wav_bytes()))
        self.assertFalse(validate_wav_header(b"not a wav file...."))

    def test_detect_format_wav(self):
        self.assertEqual(audio_service._detect_audio_format(make_wav_bytes()), "wav")

    # ===== ASR：URL 正确 =====

    def test_asr_url_is_chat_completions(self):
        """ASR 必须走 {base}/chat/completions，而非 /audio/transcriptions"""
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(200, _asr_success_json())) as mock_post, \
             self._patch_asr():
            transcribe_audio(make_wav_bytes(), filename="rec.wav")

        called_url = mock_post.call_args[0][0]
        self.assertTrue(called_url.endswith("/v1/chat/completions"), called_url)
        self.assertNotIn("/audio/transcriptions", called_url)
        self.assertNotIn("/v1/v1", called_url)

    def test_asr_payload_structure(self):
        """ASR payload：model + input_audio Data URL + asr_options"""
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(200, _asr_success_json())) as mock_post, \
             self._patch_asr():
            transcribe_audio(make_wav_bytes(), filename="rec.wav")

        body = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        self.assertEqual(body["model"], "mimo-v2.5-asr")
        self.assertEqual(body["asr_options"], {"language": "auto"})
        content = body["messages"][0]["content"][0]
        self.assertEqual(content["type"], "input_audio")
        data_url = content["input_audio"]["data"]
        self.assertTrue(data_url.startswith("data:audio/wav;base64,"))
        # 不发送 Windows 文件路径
        self.assertNotIn("\\", data_url)

    def test_asr_success_parse(self):
        """ASR：choices[0].message.content 解析"""
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(200, _asr_success_json("二叉树的定义"))), \
             self._patch_asr():
            text = transcribe_audio(make_wav_bytes(), filename="rec.wav")
        self.assertEqual(text, "二叉树的定义")

    def test_asr_content_list_parse(self):
        """ASR：content 为内容数组时也能解析"""
        json_data = {"choices": [{"message": {"content": [{"type": "text", "text": "你好"}]}}]}
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(200, json_data)), \
             self._patch_asr():
            text = transcribe_audio(make_wav_bytes(), filename="rec.wav")
        self.assertEqual(text, "你好")

    # ===== ASR：错误映射 =====

    def test_asr_404_endpoint_not_found(self):
        """上游 404 → audio_endpoint_not_found（MiMo 不存在 /audio/* 路径的典型错误）"""
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(404, None, b"", "openresty 404")), \
             self._patch_asr():
            with self.assertRaises(AudioServiceError) as ctx:
                transcribe_audio(make_wav_bytes(), filename="a.wav")
        self.assertEqual(ctx.exception.error_type, "audio_endpoint_not_found")

    def test_asr_401_mapping(self):
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(401)), \
             self._patch_asr():
            with self.assertRaises(AudioServiceError) as ctx:
                transcribe_audio(make_wav_bytes(), filename="a.wav")
        self.assertEqual(ctx.exception.error_type, "invalid_api_key")

    def test_asr_429_mapping(self):
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(429)), \
             self._patch_asr():
            with self.assertRaises(AudioServiceError) as ctx:
                transcribe_audio(make_wav_bytes(), filename="a.wav")
        self.assertEqual(ctx.exception.error_type, "rate_limited")

    def test_asr_5xx_fallback(self):
        """500 → asr_api_error（fallback）"""
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(500)), \
             self._patch_asr():
            with self.assertRaises(AudioServiceError) as ctx:
                transcribe_audio(make_wav_bytes(), filename="a.wav")
        self.assertEqual(ctx.exception.error_type, "asr_api_error")

    def test_asr_timeout(self):
        import requests as _requests
        with mock.patch("services.audio_service.requests.post",
                        side_effect=_requests.exceptions.Timeout()), \
             self._patch_asr():
            with self.assertRaises(AudioServiceError) as ctx:
                transcribe_audio(make_wav_bytes(), filename="a.wav")
        self.assertEqual(ctx.exception.error_type, "timeout")

    def test_asr_connection_error(self):
        import requests as _requests
        with mock.patch("services.audio_service.requests.post",
                        side_effect=_requests.exceptions.ConnectionError()), \
             self._patch_asr():
            with self.assertRaises(AudioServiceError) as ctx:
                transcribe_audio(make_wav_bytes(), filename="a.wav")
        self.assertEqual(ctx.exception.error_type, "connection_error")

    def test_asr_response_parse_error(self):
        """响应非 JSON → response_parse_error"""
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(200, ValueError("bad json"))), \
             self._patch_asr():
            with self.assertRaises(AudioServiceError) as ctx:
                transcribe_audio(make_wav_bytes(), filename="a.wav")
        self.assertEqual(ctx.exception.error_type, "response_parse_error")

    def test_asr_empty_audio(self):
        with self.assertRaises(AudioServiceError) as ctx:
            transcribe_audio(b"")
        self.assertEqual(ctx.exception.error_type, "audio_invalid_format")

    def test_asr_invalid_format(self):
        with self.assertRaises(AudioServiceError) as ctx:
            transcribe_audio(b"\x00\x01\x02garbage" * 10)
        self.assertEqual(ctx.exception.error_type, "audio_invalid_format")

    # ===== TTS：URL 正确 =====

def _make_client():
    from api.app import app
    return TestClient(app)


class AudioAPITest(IsolatedDataTestCase):
    """
    API 层测试（Mock service）

    说明（2026-09-26）：
    - 语音接口现在需要登录身份（与其余学习数据接口一致），因此走隔离夹具 + 令牌
    - 转写路径改名为 transcribe_audio_detailed（返回 text + service + engine），
      用例同步更新为按新契约 mock，并额外校验"实际使用的语音服务"被如实回报
    """

    SUBJECTS = ("数据结构",)

    def setUp(self):
        super().setUp()
        self.client = _make_client()
        self.token = register_and_login("audio_api_user")

    def _post(self, path, **kw):
        headers = kw.pop("headers", {}) or {}
        headers["X-User-Token"] = self.token
        return self.client.post(path, headers=headers, **kw)

    def test_transcribe_endpoint(self):
        with mock.patch("api.routes.audio.transcribe_audio_detailed",
                        return_value={"text": "识别文本", "service": "local",
                                      "engine": "funasr-paraformer-zh",
                                      "latency_ms": 120.0}):
            resp = self._post("/api/audio/transcribe",
                              files={"file": ("rec.wav", make_wav_bytes(), "audio/wav")})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["text"], "识别文本")
        self.assertEqual(data["service"], "local")
        self.assertEqual(data["engine"], "funasr-paraformer-zh")

    def test_transcribe_error_type_mapped(self):
        with mock.patch("api.routes.audio.transcribe_audio_detailed",
                        side_effect=AudioServiceError("音频过大", "audio_too_large")):
            resp = self._post("/api/audio/transcribe",
                              files={"file": ("rec.wav", make_wav_bytes(), "audio/wav")})
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(resp.json()["error_type"], "audio_too_large")

    def test_transcribe_404_mapped(self):
        """audio_endpoint_not_found → 502"""
        with mock.patch("api.routes.audio.transcribe_audio_detailed",
                        side_effect=AudioServiceError("端点不存在", "audio_endpoint_not_found")):
            resp = self._post("/api/audio/transcribe",
                              files={"file": ("rec.wav", make_wav_bytes(), "audio/wav")})
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["error_type"], "audio_endpoint_not_found")

    def test_local_speech_unavailable_maps_to_503_with_reason(self):
        """本地语音未就绪 → 503 + 可理解原因（文字功能不受影响）"""
        with mock.patch("api.routes.audio.transcribe_audio_detailed",
                        side_effect=AudioServiceError(
                            "本地语音服务未启动（Connection refused）", "speech_unavailable")):
            resp = self._post("/api/audio/transcribe",
                              files={"file": ("rec.wav", make_wav_bytes(), "audio/wav")})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error_type"], "speech_unavailable")
        self.assertIn("未启动", resp.json()["message"])
