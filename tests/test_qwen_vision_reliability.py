"""
Qwen Vision 可靠性测试（P1 修复验收）

覆盖：
- QwenVisionProvider 接口与默认配置
- 请求格式（Base64 Data URL / MIME / 本地路径不发送）
- JSON 与 Markdown 代码块响应解析、非JSON安全降级
- 错误分类（invalid_api_key/forbidden/rate_limited/server_error/timeout/...）
- 重试规则（400/401/403不重试；429/5xx/timeout重试）
- 元数据（provider_used / model_used）
- 缓存（success写入 / failed不写 / 命中不调API）
- 失败隔离（不阻断PDF、不影响Formula/Table）

全部使用 mock，不联网。
"""

import json
import os
import sys
import unittest
from tempfile import TemporaryDirectory
from unittest import mock
from pathlib import Path
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from services import model_service
from services.multimodal_service import (
    MultimodalService,
    parse_vision_json_response,
    MAX_IMAGE_DIMENSION,
)
from services.model_service import (
    ModelAPIError,
    _classify_http_error,
    get_model_provider,
)
from rag.enrichment.multimodal_enricher import (
    MultimodalEnricher,
    QwenVisionProvider as EnricherQwenProvider,
)
from langchain_core.documents import Document


def make_response(status_code: int, content: dict) -> requests.Response:
    """构造 requests.Response（用于 mock）"""
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = json.dumps(content).encode("utf-8")
    resp.url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    return resp


def make_http_error(status_code: int) -> requests.HTTPError:
    """构造带 status_code 的 HTTPError"""
    err = requests.HTTPError(f"{status_code} Client Error")
    resp = requests.Response()
    resp.status_code = status_code
    resp.url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    err.response = resp
    return err


class TestVisionConfig(unittest.TestCase):
    """默认 Provider / Model / Key / URL 配置"""

    def test_default_provider_is_qwen_vision(self):
        provider = get_model_provider("qwen-vision")
        self.assertIsInstance(provider, model_service.QwenVisionProvider)
        self.assertEqual(provider.model_name, "qwen-vision")

    def test_default_model_is_qwen_vl_plus(self):
        provider = get_model_provider("qwen-vision")
        self.assertEqual(provider.config["model_name"], "qwen-vl-plus")
        self.assertTrue(provider.config.get("is_vision", False))

    def test_api_key_read_from_env(self):
        os.environ["DASHSCOPE_API_KEY"] = "sk-test-abc123"
        try:
            from config.models import get_model_api_key
            self.assertEqual(get_model_api_key("qwen-vision"), "sk-test-abc123")
        finally:
            os.environ.pop("DASHSCOPE_API_KEY", None)

    def test_base_url_and_model_config(self):
        """默认 Base URL 与模型配置正确（MODEL_CONFIG 在import时求值，故验证默认值）"""
        provider = get_model_provider("qwen-vision")
        self.assertEqual(provider.config["base_url"], "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(provider.config["model_name"], "qwen-vl-plus")


class TestVisionRequestFormat(unittest.TestCase):
    """请求格式：Base64 Data URL / MIME / 本地路径不发送"""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._img_path = os.path.join(self._tmp.name, "test.png")
        Image.new("RGB", (20, 20), "white").save(self._img_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_mime_types(self):
        ms = MultimodalService.__new__(MultimodalService)
        self.assertEqual(ms._detect_mime_type("a.jpg"), "image/jpeg")
        self.assertEqual(ms._detect_mime_type("a.png"), "image/png")
        self.assertEqual(ms._detect_mime_type("a.webp"), "image/webp")
        self.assertEqual(ms._detect_mime_type("a.bmp"), "image/bmp")
        self.assertEqual(ms._detect_mime_type("a.gif"), "image/gif")

    def test_local_path_not_sent_and_data_url_format(self):
        """本地路径不会直接发送，image_url 为 data:image/png;base64,..."""
        captured = {}

        def fake_post(url, headers=None, data=None, timeout=None, **kw):
            captured["body"] = json.loads(data.decode("utf-8"))
            return make_response(200, {
                "choices": [{"message": {"content": '{"description":"d","ocr_text":"t"}'}}]
            })

        os.environ["DASHSCOPE_API_KEY"] = "sk-test-abc"
        try:
            with mock.patch("services.model_service.requests.post", side_effect=fake_post):
                ms = MultimodalService()
                result = ms.describe_image(self._img_path)
        finally:
            os.environ.pop("DASHSCOPE_API_KEY", None)

        body = captured["body"]
        content = body["messages"][0]["content"]
        types = [c["type"] for c in content]
        self.assertIn("text", types)
        self.assertIn("image_url", types)
        img_url = next(c["image_url"]["url"] for c in content if c["type"] == "image_url")
        self.assertTrue(img_url.startswith("data:image/png;base64,"))
        # 不发送本地路径
        self.assertNotIn("test.png", img_url)
        self.assertNotIn("C:", img_url)
        # 请求包含图片和文本
        text_part = next(c["text"] for c in content if c["type"] == "text")
        self.assertIn("JSON", text_part)
        # model 字段正确
        self.assertEqual(body["model"], "qwen-vl-plus")

    def test_provider_used_model_used_metadata(self):
        """provider_used / model_used 元数据正确"""
        os.environ["DASHSCOPE_API_KEY"] = "sk-test-abc"
        try:
            with mock.patch("services.model_service.requests.post",
                            return_value=make_response(200, {
                                "choices": [{"message": {"content": '{"description":"desc","ocr_text":"ocr"}'}}]
                            })):
                ms = MultimodalService()
                result = ms.process_image_to_document(self._img_path)
                md = result["metadata"]
                self.assertEqual(md["provider_used"], "qwen-vision")
                self.assertEqual(md["model_used"], "qwen-vl-plus")
                self.assertEqual(md["vision_status"], "success")
                self.assertEqual(md["caption"], "desc")
                self.assertEqual(md["ocr_result"], "ocr")
        finally:
            os.environ.pop("DASHSCOPE_API_KEY", None)


class TestVisionJSONParsing(unittest.TestCase):
    """JSON / Markdown 代码块 JSON / 非JSON降级"""

    def test_standard_json(self):
        desc, ocr = parse_vision_json_response('{"description":"图","ocr_text":"MM-TEST-2026"}')
        self.assertEqual(desc, "图")
        self.assertEqual(ocr, "MM-TEST-2026")

    def test_markdown_codeblock_json(self):
        raw = '```json\n{"description":"流程","ocr_text":"文档上传"}\n```'
        desc, ocr = parse_vision_json_response(raw)
        self.assertEqual(desc, "流程")
        self.assertEqual(ocr, "文档上传")

    def test_non_json_degrades_safely(self):
        desc, ocr = parse_vision_json_response("这是一段自然语言描述，不是JSON")
        self.assertEqual(desc, "")
        self.assertEqual(ocr, "")

    def test_empty_returns_empty(self):
        desc, ocr = parse_vision_json_response("")
        self.assertEqual((desc, ocr), ("", ""))


class TestErrorClassification(unittest.TestCase):
    """错误分类与重试规则"""

    def test_classify_http_error(self):
        self.assertEqual(_classify_http_error(400), "invalid_request")
        self.assertEqual(_classify_http_error(401), "invalid_api_key")
        self.assertEqual(_classify_http_error(403), "forbidden")
        self.assertEqual(_classify_http_error(404), "model_not_found")
        self.assertEqual(_classify_http_error(429), "rate_limited")
        self.assertEqual(_classify_http_error(500), "server_error")

    def test_401_maps_to_invalid_api_key_and_no_retry(self):
        provider = get_model_provider("qwen-vision")
        provider.api_key = "sk-test"
        with mock.patch("services.model_service.requests.post",
                        side_effect=make_http_error(401)) as mk:
            with self.assertRaises(ModelAPIError) as ctx:
                provider.vision([{"role": "user", "content": [{"type": "text", "text": "x"}]}])
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.error_type, "invalid_api_key")
        self.assertEqual(mk.call_count, 1)  # 401 不重试

    def test_403_maps_to_forbidden_and_no_retry(self):
        provider = get_model_provider("qwen-vision")
        provider.api_key = "sk-test"
        with mock.patch("services.model_service.requests.post",
                        side_effect=make_http_error(403)) as mk:
            with self.assertRaises(ModelAPIError) as ctx:
                provider.vision([{"role": "user", "content": [{"type": "text", "text": "x"}]}])
        self.assertEqual(ctx.exception.error_type, "forbidden")
        self.assertEqual(mk.call_count, 1)

    def test_400_maps_to_invalid_request_and_no_retry(self):
        provider = get_model_provider("qwen-vision")
        provider.api_key = "sk-test"
        with mock.patch("services.model_service.requests.post",
                        side_effect=make_http_error(400)) as mk:
            with self.assertRaises(ModelAPIError) as ctx:
                provider.vision([{"role": "user", "content": [{"type": "text", "text": "x"}]}])
        self.assertEqual(ctx.exception.error_type, "invalid_request")
        self.assertEqual(mk.call_count, 1)

    def test_429_retries_then_succeeds(self):
        provider = get_model_provider("qwen-vision")
        provider.api_key = "sk-test"
        ok = make_response(200, {"choices": [{"message": {"content": '{"description":"d"}'}}]})
        with mock.patch("services.model_service.requests.post",
                        side_effect=[make_http_error(429), ok]) as mk, \
             mock.patch("services.model_service.time.sleep"):
            res = provider.vision([{"role": "user", "content": [{"type": "text", "text": "x"}]}])
        self.assertEqual(mk.call_count, 2)  # 429 重试一次后成功
        self.assertEqual(res["retries"], 1)

    def test_500_retries(self):
        provider = get_model_provider("qwen-vision")
        provider.api_key = "sk-test"
        ok = make_response(200, {"choices": [{"message": {"content": '{"description":"d"}'}}]})
        with mock.patch("services.model_service.requests.post",
                        side_effect=[make_http_error(500), ok]) as mk, \
             mock.patch("services.model_service.time.sleep"):
            provider.vision([{"role": "user", "content": [{"type": "text", "text": "x"}]}])
        self.assertEqual(mk.call_count, 2)

    def test_500_exhausted_raises_server_error(self):
        provider = get_model_provider("qwen-vision")
        provider.api_key = "sk-test"
        with mock.patch("services.model_service.requests.post",
                        side_effect=make_http_error(500)) as mk, \
             mock.patch("services.model_service.time.sleep"):
            with self.assertRaises(ModelAPIError) as ctx:
                provider.vision([{"role": "user", "content": [{"type": "text", "text": "x"}]}])
        self.assertEqual(ctx.exception.error_type, "server_error")
        self.assertEqual(mk.call_count, 3)  # 3 次尝试

    def test_timeout_retries_then_succeeds(self):
        provider = get_model_provider("qwen-vision")
        provider.api_key = "sk-test"
        ok = make_response(200, {"choices": [{"message": {"content": '{"description":"d"}'}}]})
        with mock.patch("services.model_service.requests.post",
                        side_effect=[requests.exceptions.Timeout(), ok]) as mk, \
             mock.patch("services.model_service.time.sleep"):
            res = provider.vision([{"role": "user", "content": [{"type": "text", "text": "x"}]}])
        self.assertEqual(mk.call_count, 2)
        self.assertEqual(res["retries"], 1)


class TestVisionCache(unittest.TestCase):
    """缓存：success写入 / failed不写 / 命中不调API"""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._img_path = os.path.join(self._tmp.name, "test.png")
        Image.new("RGB", (20, 20), "white").save(self._img_path)
        self._cache_patcher = mock.patch(
            "rag.enrichment.multimodal_enricher.IMAGE_DESCRIPTION_CACHE_DIR",
            Path(self._tmp.name) / "cache")
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_image_doc(self):
        return Document(
            page_content="[图片] | 尺寸: 20x20 | 页码: 1",
            metadata={
                "type": "image", "modality": "image",
                "image_path": self._img_path, "source": self._img_path,
                "file_name": "test.png", "file_type": "png",
            })

    def test_success_writes_cache_and_second_hit_no_api(self):
        ok = make_response(200, {
            "choices": [{"message": {"content": '{"description":"desc","ocr_text":"MM-TEST-2026"}'}}]})
        os.environ["DASHSCOPE_API_KEY"] = "sk-test-abc"
        try:
            with mock.patch("services.model_service.requests.post",
                            return_value=ok) as mk:
                en = MultimodalEnricher()
                doc1 = en._enrich_image_document(self._make_image_doc())
                self.assertEqual(doc1.metadata["vision_status"], "success")
                self.assertEqual(doc1.metadata["provider_used"], "qwen-vision")
                self.assertEqual(doc1.metadata["model_used"], "qwen-vl-plus")
                calls1 = mk.call_count
                self.assertGreaterEqual(calls1, 1)

                # 第二次处理同一图片：缓存命中，不再调用API
                doc2 = en._enrich_image_document(self._make_image_doc())
                self.assertEqual(doc2.metadata["vision_status"], "cached")
                self.assertEqual(doc2.metadata["description"], "desc")
                self.assertEqual(doc2.metadata["ocr_text"], "MM-TEST-2026")
                self.assertEqual(mk.call_count, calls1)  # API调用数不增加
        finally:
            os.environ.pop("DASHSCOPE_API_KEY", None)

    def test_failed_not_cached(self):
        """failed 结果不写缓存"""
        os.environ["DASHSCOPE_API_KEY"] = "sk-test-abc"
        try:
            with mock.patch("services.model_service.requests.post",
                            side_effect=make_http_error(401)):
                en = MultimodalEnricher()
                doc = en._enrich_image_document(self._make_image_doc())
                self.assertEqual(doc.metadata["vision_status"], "failed")
                self.assertEqual(doc.metadata["error_type"] if False else doc.metadata.get("vision_error_type"),
                                 "invalid_api_key")
                # 缓存目录不应产生缓存文件
                cache_dir = Path(self._tmp.name) / "cache"
                self.assertFalse(cache_dir.exists() and any(cache_dir.iterdir()))
        finally:
            os.environ.pop("DASHSCOPE_API_KEY", None)


class TestVisionFailureIsolation(unittest.TestCase):
    """Vision 失败不阻断PDF、不影响 Formula/Table"""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._img_path = os.path.join(self._tmp.name, "test.png")
        Image.new("RGB", (20, 20), "white").save(self._img_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_failure_degrades_without_blocking_pdf(self):
        """Vision失败时 image 文档降级保留，process不抛异常"""
        os.environ["DASHSCOPE_API_KEY"] = "sk-test-abc"
        try:
            with mock.patch("services.model_service.requests.post",
                            side_effect=make_http_error(401)):
                en = MultimodalEnricher()
                doc = Document(
                    page_content="[图片] | 尺寸: 20x20",
                    metadata={"type": "image", "modality": "image",
                              "image_path": self._img_path, "source": self._img_path})
                result = en.enrich([doc])  # enrich 应正常返回，不抛异常
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0].metadata["vision_status"], "failed")
                self.assertEqual(result[0].metadata["model_used"], "qwen-vl-plus")
                self.assertEqual(result[0].metadata["provider_used"], "qwen-vision")
        finally:
            os.environ.pop("DASHSCOPE_API_KEY", None)

    def test_formula_and_table_not_affected(self):
        """Formula/Table 文档增强不受 Vision 影响"""
        os.environ["DASHSCOPE_API_KEY"] = "sk-test-abc"
        try:
            with mock.patch("services.model_service.requests.post",
                            side_effect=make_http_error(401)):
                en = MultimodalEnricher()
                formula = Document(page_content="F = ma",
                                   metadata={"type": "formula", "modality": "formula"})
                table = Document(page_content="模块|状态",
                                 metadata={"type": "table", "modality": "table",
                                           "page_content": {"headers": ["模块"], "rows": [["Chat"]]}})
                results = en.enrich([formula, table])
                self.assertEqual(results[0].metadata["modality"], "formula")
                self.assertEqual(results[1].metadata["modality"], "table")
                # Formula/Table 均不触发 Vision 标记
                self.assertNotIn("vision_status", results[0].metadata)
                self.assertNotIn("vision_status", results[1].metadata)
        finally:
            os.environ.pop("DASHSCOPE_API_KEY", None)


class TestVisionLogSecurity(unittest.TestCase):
    """日志不包含 Key / Base64"""

    def test_logs_do_not_contain_key_or_base64(self):
        """错误路径日志中不应包含 API Key 或 Base64 图片内容"""
        os.environ["DASHSCOPE_API_KEY"] = "sk-super-secret-key-123456"
        self._tmp = TemporaryDirectory()
        img_path = os.path.join(self._tmp.name, "t.png")
        Image.new("RGB", (20, 20), "white").save(img_path)
        import io
        import logging
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.WARNING)
        logger = logging.getLogger("services.multimodal_service")
        old_handlers = list(logger.handlers)
        logger.handlers = [handler]
        logger.setLevel(logging.WARNING)
        try:
            with mock.patch("services.model_service.requests.post",
                            side_effect=make_http_error(401)):
                ms = MultimodalService()
                result = ms.process_image_to_document(img_path)
            text = log_stream.getvalue()
            self.assertNotIn("sk-super-secret-key", text)
            self.assertNotIn("base64,", text)
        finally:
            logger.handlers = old_handlers
            self._tmp.cleanup()
            os.environ.pop("DASHSCOPE_API_KEY", None)


class TestQwenVisionProviderInterface(unittest.TestCase):
    """QwenVisionProvider 实现接口（chat 不支持、vision 可用）"""

    def test_chat_raises_not_implemented(self):
        provider = get_model_provider("qwen-vision")
        with self.assertRaises(NotImplementedError):
            provider.chat([{"role": "user", "content": "hi"}])

    def test_vision_uses_openai_compatible_api(self):
        provider = get_model_provider("qwen-vision")
        provider.api_key = "sk-test"
        ok = make_response(200, {"choices": [{"message": {"content": '{"description":"d"}'}}]})
        with mock.patch("services.model_service.requests.post", return_value=ok) as mk:
            res = provider.vision([{"role": "user", "content": [{"type": "text", "text": "x"}]}])
        self.assertIn("answer", res)
        mk.assert_called_once()


if __name__ == "__main__":
    unittest.main()
