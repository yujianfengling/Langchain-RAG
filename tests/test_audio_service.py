# -*- coding: utf-8 -*-
"""MiMo 语音服务测试（V2.5.1 Audio Runtime Fix / V2.5.3 TTS 优化）

全部 Mock API 调用（禁止真实调用 MiMo）。
覆盖：URL / payload / response parse / Base64 decode / 404 401 timeout 映射 /
     TTS 文本清洗 / 后端缓存。
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
from services import audio_service
from services.audio_service import (
    transcribe_audio,
    synthesize_speech,
    prepare_tts_text,
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


def _tts_config(base_url="https://token-plan-cn.xiaomimimo.com/v1"):
    return {"base_url": base_url, "api_key": "test-key", "model_name": "mimo-v2.5-tts"}


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
        # 隔离 TTS 缓存目录，避免前序测试写缓存导致后续上游请求被跳过
        self._orig_cache_dir = audio_service.TTS_CACHE_DIR
        audio_service.TTS_CACHE_DIR = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "test_tts_cache_unit")

    def tearDown(self):
        audio_service.TTS_CACHE_DIR = self._orig_cache_dir
        import shutil
        d = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "test_tts_cache_unit")
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)

    def _patch_asr(self):
        return mock.patch("services.audio_service._get_audio_config",
                          return_value=_config())

    def _patch_tts(self):
        return mock.patch("services.audio_service._get_audio_config",
                          return_value=_tts_config())

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

    def test_tts_url_is_chat_completions(self):
        """TTS 必须走 {base}/chat/completions，而非 /audio/speech"""
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(200, _tts_success_json())) as mock_post, \
             self._patch_tts():
            audio = synthesize_speech("你好")
        self.assertTrue(validate_wav_header(audio))
        called_url = mock_post.call_args[0][0]
        self.assertTrue(called_url.endswith("/v1/chat/completions"), called_url)
        self.assertNotIn("/audio/speech", called_url)

    def test_tts_payload_structure(self):
        """TTS payload：model + messages(assistant) + audio.format/voice"""
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(200, _tts_success_json())) as mock_post, \
             self._patch_tts():
            synthesize_speech("你好")

        body = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        self.assertEqual(body["model"], "mimo-v2.5-tts")
        self.assertEqual(body["messages"][0]["role"], "assistant")
        self.assertEqual(body["messages"][0]["content"], "你好")
        self.assertEqual(body["audio"]["format"], "wav")
        self.assertEqual(body["audio"]["voice"], "mimo_default")

    def test_tts_base64_decode(self):
        """TTS：choices[0].message.audio.data base64 → WAV bytes"""
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(200, _tts_success_json())), \
             self._patch_tts():
            audio = synthesize_speech("你好")
        self.assertTrue(validate_wav_header(audio))

    def test_tts_empty_text_rejected(self):
        with self.assertRaises(AudioServiceError) as ctx:
            synthesize_speech("   ")
        self.assertEqual(ctx.exception.error_type, "empty_text")

    def test_tts_404_mapping(self):
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(404)), \
             self._patch_tts():
            with self.assertRaises(AudioServiceError) as ctx:
                synthesize_speech("你好")
        self.assertEqual(ctx.exception.error_type, "audio_endpoint_not_found")

    def test_tts_401_mapping(self):
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(401)), \
             self._patch_tts():
            with self.assertRaises(AudioServiceError) as ctx:
                synthesize_speech("你好")
        self.assertEqual(ctx.exception.error_type, "invalid_api_key")

    def test_tts_no_audio_data(self):
        """响应中无 audio.data → response_parse_error"""
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(200, {"choices": [{"message": {}}]})), \
             self._patch_tts():
            with self.assertRaises(AudioServiceError) as ctx:
                synthesize_speech("你好")
        self.assertEqual(ctx.exception.error_type, "response_parse_error")

    def test_tts_bad_base64(self):
        """Base64 解码失败 → response_parse_error"""
        json_data = {"choices": [{"message": {"audio": {"data": "!!!not-base64!!!"}}}]}
        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(200, json_data)), \
             self._patch_tts():
            with self.assertRaises(AudioServiceError) as ctx:
                synthesize_speech("你好")
        self.assertEqual(ctx.exception.error_type, "response_parse_error")


def _make_client():
    from api.app import app
    return TestClient(app)


class AudioAPITest(unittest.TestCase):
    """API 层测试（Mock service）"""

    def test_transcribe_endpoint(self):
        client = _make_client()
        with mock.patch("api.routes.audio.transcribe_audio", return_value="识别文本"):
            resp = client.post(
                "/api/audio/transcribe",
                files={"file": ("rec.wav", make_wav_bytes(), "audio/wav")})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["text"], "识别文本")

    def test_transcribe_error_type_mapped(self):
        client = _make_client()
        with mock.patch("api.routes.audio.transcribe_audio",
                        side_effect=AudioServiceError("音频过大", "audio_too_large")):
            resp = client.post(
                "/api/audio/transcribe",
                files={"file": ("rec.wav", make_wav_bytes(), "audio/wav")})
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(resp.json()["error_type"], "audio_too_large")

    def test_transcribe_404_mapped(self):
        """audio_endpoint_not_found → 502"""
        client = _make_client()
        with mock.patch("api.routes.audio.transcribe_audio",
                        side_effect=AudioServiceError("端点不存在", "audio_endpoint_not_found")):
            resp = client.post(
                "/api/audio/transcribe",
                files={"file": ("rec.wav", make_wav_bytes(), "audio/wav")})
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["error_type"], "audio_endpoint_not_found")

    def test_speech_endpoint_returns_wav(self):
        client = _make_client()
        with mock.patch("api.routes.audio.synthesize_speech", return_value=make_wav_bytes()):
            resp = client.post("/api/audio/speech", json={"text": "你好"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "audio/wav")
        self.assertTrue(resp.content.startswith(b"RIFF"))

    def test_speech_empty_text_400(self):
        client = _make_client()
        with mock.patch("api.routes.audio.synthesize_speech",
                        side_effect=AudioServiceError("文本为空", "empty_text")):
            resp = client.post("/api/audio/speech", json={"text": " "})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_type"], "empty_text")


class TtsTextCleaningTest(unittest.TestCase):
    """TTS 朗读文本清洗（V2.5.3）"""

    def test_strips_markdown_and_sources(self):
        raw = """**定义**
二叉树是**每个节点**最多有两个子树的树结构。

```python
class Node: pass
```

参考来源：树.pdf
模型：小米MiMo | 首字响应: 1.2s | 总耗时: 3.4s
公式 \\\\frac{a}{b} 表示分数。
链接 https://example.com 🎉
"""
        clean = prepare_tts_text(raw)
        self.assertNotIn("**", clean)          # Markdown 符号
        self.assertNotIn("```", clean)         # 代码围栏
        self.assertNotIn("class Node", clean)  # 代码内容
        self.assertNotIn("参考来源", clean)     # 来源
        self.assertNotIn("首字响应", clean)     # 耗时信息
        self.assertNotIn("总耗时", clean)
        self.assertNotIn("模型：", clean)
        self.assertNotIn("http", clean)        # URL
        self.assertNotIn("frac", clean)        # LaTeX
        self.assertNotIn("🎉", clean)          # emoji
        self.assertIn("二叉树", clean)          # 核心内容保留

    def test_max_chars_sentence_boundary(self):
        raw = "第一句话。第二句话内容较长需要截断。第三句话。第四句话。"
        clean = prepare_tts_text(raw, max_chars=10)
        # 应在句子边界截断，不超过 max_chars 太多
        self.assertLessEqual(len(clean), 12)
        self.assertTrue(clean.endswith("。") or clean.endswith("…"))

    def test_code_block_replaced(self):
        raw = "讲解如下：\n```\nprint(1)\n```\n以上是代码。"
        clean = prepare_tts_text(raw)
        self.assertIn("代码", clean)
        self.assertNotIn("print", clean)


class TtsCacheTest(unittest.TestCase):
    """TTS 后端缓存（V2.5.3）"""

    def setUp(self):
        self._orig_dir = audio_service.TTS_CACHE_DIR
        # 使用临时缓存目录，避免污染真实缓存
        audio_service.TTS_CACHE_DIR = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "test_tts_cache")

    def tearDown(self):
        audio_service.TTS_CACHE_DIR = self._orig_dir
        import shutil
        d = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "test_tts_cache")
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)

    def test_second_call_uses_cache(self):
        """第二次调用命中缓存，不再请求上游 API"""
        wav = make_wav_bytes()
        json_data = {"choices": [{"message": {"audio": {"data": base64.b64encode(wav).decode("ascii")}}}]}

        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(200, json_data)) as mock_post, \
             mock.patch("services.audio_service._get_audio_config",
                        return_value=_tts_config()):
            audio1 = synthesize_speech("你好，这是一次测试。")
            self.assertTrue(validate_wav_header(audio1))
            # 第一次：1 次上游调用
            self.assertEqual(mock_post.call_count, 1)

        # 第二次：缓存命中，0 次上游调用
        with mock.patch("services.audio_service.requests.post") as mock_post2, \
             mock.patch("services.audio_service._get_audio_config",
                        return_value=_tts_config()):
            audio2 = synthesize_speech("你好，这是一次测试。")
            self.assertTrue(validate_wav_header(audio2))
            self.assertEqual(mock_post2.call_count, 0)

    def test_cache_key_depends_on_text(self):
        """不同文本 → 不同缓存 key（各自请求上游）"""
        wav = make_wav_bytes()
        json_data = {"choices": [{"message": {"audio": {"data": base64.b64encode(wav).decode("ascii")}}}]}

        with mock.patch("services.audio_service.requests.post",
                        return_value=FakeResponse(200, json_data)) as mock_post, \
             mock.patch("services.audio_service._get_audio_config",
                        return_value=_tts_config()):
            synthesize_speech("第一段文本。")
            synthesize_speech("第二段文本。")
            self.assertEqual(mock_post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
