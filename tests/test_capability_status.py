"""
外部能力状态测试

这些用例锁定的是"降级必须可见"这个约束——项目历史上多次因为
"功能坏了但只在日志里"而长期无人察觉（见 AGENTS.md）。
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import capability_status as cs


class TestCapabilityStatus(unittest.TestCase):

    def setUp(self):
        cs.reset()

    def test_unknown_capability_is_not_reported_as_healthy(self):
        """还没调用过时 ok=None，不能被误读成"健康" """
        st = cs.get_status(cs.CAP_LLM)
        self.assertIsNone(st["ok"])
        self.assertEqual(st["fail_count"], 0)
        self.assertEqual(cs.degraded(), {})

    def test_failure_is_visible_with_reason(self):
        cs.report(cs.CAP_WEB_SEARCH, False, reason="HTTP 401: invalid api key")
        degraded = cs.degraded()
        self.assertIn(cs.CAP_WEB_SEARCH, degraded)
        self.assertIn("401", degraded[cs.CAP_WEB_SEARCH])
        st = cs.get_status(cs.CAP_WEB_SEARCH)
        self.assertFalse(st["ok"])
        self.assertEqual(st["fail_count"], 1)
        self.assertIsNotNone(st["last_fail_at"])

    def test_recovery_clears_degraded(self):
        cs.report(cs.CAP_LLM, False, reason="timeout")
        self.assertIn(cs.CAP_LLM, cs.degraded())
        cs.report(cs.CAP_LLM, True, reason="ok")
        self.assertNotIn(cs.CAP_LLM, cs.degraded())
        self.assertEqual(cs.get_status(cs.CAP_LLM)["success_count"], 1)

    def test_snapshot_covers_all_capabilities_without_secrets(self):
        cs.report(cs.CAP_LLM, True, reason="model=deepseek-chat")
        snap = cs.snapshot()
        for cap in cs.ALL_CAPABILITIES:
            self.assertIn(cap, snap)
        blob = str(snap)
        self.assertNotIn("api_key", blob.lower())
        self.assertNotIn("tvly-", blob)

    def test_safe_report_never_raises(self):
        with mock.patch.object(cs, "report", side_effect=RuntimeError("boom")):
            cs.safe_report(cs.CAP_LLM, True)  # 不应抛异常


class TestWebSearchReportsCapability(unittest.TestCase):
    """联网搜索失败必须上报为降级，而不是只写日志"""

    def setUp(self):
        cs.reset()
        # 必须用 patch.dict：直接写 os.environ 会污染同进程里的后续测试，
        # 曾经导致 test_default_model_switch 真的去打 Tavily。
        self._env = mock.patch.dict(os.environ, {
            "TAVILY_API_KEY": "tvly-test-key",
            "WEB_SEARCH_ENABLED": "true",
        })
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_http_error_reports_degraded(self):
        from services import web_search_service as ws
        fake = mock.Mock(status_code=401, text="invalid api key")
        with mock.patch("services.web_search_service.requests.post", return_value=fake):
            out = ws.search_question_material("欧姆定律 例题")
        self.assertEqual(out, [])
        st = cs.get_status(cs.CAP_WEB_SEARCH)
        self.assertFalse(st["ok"])
        self.assertIn("401", st["last_error"])

    def test_success_reports_healthy(self):
        from services import web_search_service as ws
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"results": [
            {"title": "题目页", "url": "https://example.com/q",
             "content": "题干内容" * 60},
        ]}
        with mock.patch("services.web_search_service.requests.post", return_value=fake):
            out = ws.search_question_material("欧姆定律 例题")
        self.assertEqual(len(out), 1)
        st = cs.get_status(cs.CAP_WEB_SEARCH)
        self.assertTrue(st["ok"])


class TestVisionAndSpeechReportCapability(unittest.TestCase):
    """视觉与语音的降级同样必须可见。

    这条不是空谈：VISION_MODE=ocr 曾让课件图片永远进不了知识库，
    而当时只有日志，界面上和健康检查里都看不出来。
    """

    def setUp(self):
        cs.reset()

    def test_vision_without_provider_reports_degraded(self):
        from services.multimodal_service import MultimodalService

        # 构造时不做真实探活（否则测试会联网）；只验证"没有可用视觉提供方"这条路径
        with mock.patch.object(MultimodalService, "_ensure_vision_provider",
                               lambda self: None):
            svc = MultimodalService()
        svc.vision_provider = None
        svc.vision_unavailable_reason = "未配置可用的视觉提供方"

        with self.assertRaises(ValueError):
            svc.describe_image("whatever.png")

        st = cs.get_status(cs.CAP_VISION)
        self.assertFalse(st["ok"])
        self.assertIn("视觉提供方", st["last_error"])

    def test_local_speech_failure_reports_degraded(self):
        import urllib.error
        from services import local_speech

        with mock.patch("services.local_speech.urllib.request.urlopen",
                        side_effect=urllib.error.URLError("connection refused")):
            out = local_speech.health(force=True)

        self.assertFalse(out["running"], "连不上时不应报告为运行中")
        st = cs.get_status(cs.CAP_SPEECH)
        self.assertFalse(st["ok"])
        self.assertIn("无法连接", st["last_error"])


class TestProviderReportsLLM(unittest.TestCase):
    """LLM 的上报与记账必须覆盖**流式**。

    此前上报和记账都挂在 call_llm 上，而流式对话（聊天主入口）不经过它，
    结果是：模型挂了能力状态照样显示正常，聊天消耗的调用完全不进账本。
    """

    def setUp(self):
        cs.reset()
        self._tmp = tempfile.mkdtemp(prefix="llm_report_")
        self._env = mock.patch.dict(os.environ, {"EDU_DATA_DIR": self._tmp})
        self._env.start()
        self.addCleanup(self._env.stop)

    def _provider(self):
        from services.model_service import OpenAICompatibleProvider
        # 参数是 provider 键（deepseek），不是模型名（deepseek-chat）
        return OpenAICompatibleProvider("deepseek")

    def test_chat_reports_healthy_and_records_once(self):
        from services import usage_ledger
        p = self._provider()
        with mock.patch.object(p, "_call_openai_compatible_api",
                               return_value={"answer": "hi"}):
            out = p.chat([{"role": "user", "content": "x"}])

        self.assertEqual(out["answer"], "hi")
        self.assertTrue(cs.get_status(cs.CAP_LLM)["ok"])
        self.assertEqual(usage_ledger.get_usage()["by_kind"][usage_ledger.KIND_LLM], 1)

    def test_chat_failure_reports_degraded(self):
        from services import usage_ledger
        p = self._provider()
        with mock.patch.object(p, "_call_openai_compatible_api",
                               side_effect=RuntimeError("model down")):
            with self.assertRaises(RuntimeError):
                p.chat([{"role": "user", "content": "x"}])

        st = cs.get_status(cs.CAP_LLM)
        self.assertFalse(st["ok"])
        self.assertIn("model down", st["last_error"])
        self.assertEqual(usage_ledger.get_usage()["by_kind"].get(usage_ledger.KIND_LLM, 0), 0,
                         "失败的调用不该计入用量")

    def test_stream_reports_and_records_on_completion(self):
        from services import usage_ledger
        p = self._provider()
        with mock.patch.object(p, "_iter_stream", return_value=iter(["你", "好"])):
            chunks = list(p.chat_stream([{"role": "user", "content": "x"}]))

        self.assertEqual(chunks, ["你", "好"])
        self.assertTrue(cs.get_status(cs.CAP_LLM)["ok"])
        self.assertEqual(usage_ledger.get_usage()["by_kind"][usage_ledger.KIND_LLM], 1,
                         "流式调用必须记账（此前完全漏记）")

    def test_stream_failure_reports_degraded(self):
        p = self._provider()

        def boom(*a, **kw):
            raise RuntimeError("upstream 500")
            yield  # pragma: no cover - 让它是 generator

        with mock.patch.object(p, "_iter_stream", side_effect=boom):
            with self.assertRaises(RuntimeError):
                list(p.chat_stream([{"role": "user", "content": "x"}]))

        self.assertFalse(cs.get_status(cs.CAP_LLM)["ok"])

    def test_client_abort_is_not_reported_as_failure(self):
        """客户端中断生成（切页/停止）不是模型故障，不该误报降级、也不该记账"""
        from services import usage_ledger
        p = self._provider()

        def infinite(*_args, **_kwargs):
            while True:
                yield "chunk"

        with mock.patch.object(p, "_iter_stream", side_effect=infinite):
            gen = p.chat_stream([{"role": "user", "content": "x"}])
            next(gen)
            gen.close()

        self.assertIsNone(cs.get_status(cs.CAP_LLM)["ok"],
                          "客户端主动中断被误报成了降级")
        self.assertEqual(usage_ledger.get_usage()["by_kind"].get(usage_ledger.KIND_LLM, 0), 0)


if __name__ == "__main__":
    unittest.main()
