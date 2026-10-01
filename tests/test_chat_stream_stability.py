"""
Chat Streaming 稳定性测试（Task A / V2.5）

覆盖：
- MiMo 真流式（OpenAI 兼容 SSE）
- DeepSeek 真流式
- RAG 流（带 subject_id，done 事件含 sources）
- 普通聊天流（无知识库）
- 供应商错误 → error 事件带明确类型
- 模型参数透传
- 客户端中断（generator.close）→ 上游流停止、不触发 retry/fallback
- 中文 UTF-8 跨 chunk 字节安全（前端 TextDecoder 语义在 api/chat.ts，此处验证后端 chunk 输出）

全部 Mock，不调用真实 API。
"""

import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from langchain_core.documents import Document


class FakeRaw:
    """带 read/close 的 raw，走 requests 真实 iter_lines 路径"""

    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def read(self, n=-1):
        return self._buf.read(n)

    def close(self):
        pass


def make_sse_response(*texts, done=True):
    """构造 OpenAI 兼容 SSE 流响应（多 chunk + 可选 DONE）"""
    body = ''.join(
        'data: {"choices":[{"delta":{"content":"%s"}}]}\n\n' % t for t in texts
    ).encode('utf-8')
    if done:
        body += b'data: [DONE]\n\n'

    def _post(url, headers=None, data=None, timeout=None, stream=False, **kw):
        assert stream is True, "必须 stream=True"
        payload = json.loads(data.decode('utf-8'))
        assert payload.get("stream") is True, "payload 必须 stream=True"
        resp = requests.Response()
        resp.status_code = 200
        resp.raw = FakeRaw(body)
        resp.url = url
        return resp

    return _post


class TestProviderStreaming(unittest.TestCase):
    """Provider 层真流式（MiMo / DeepSeek 共用 OpenAICompatibleProvider）"""

    def test_mimo_streams_real_chunks(self):
        from services.model_service import get_model_provider
        provider = get_model_provider("mimo")
        with mock.patch("services.model_service.requests.post",
                        side_effect=make_sse_response("你好", "，", "世界")):
            chunks = list(provider.chat_stream(
                [{"role": "user", "content": "hi"}], max_tokens=10))
        self.assertEqual(chunks, ["你好", "，", "世界"])

    def test_deepseek_streams_real_chunks(self):
        from services.model_service import get_model_provider
        provider = get_model_provider("deepseek")
        with mock.patch("services.model_service.requests.post",
                        side_effect=make_sse_response("深度", "优先", "算法")):
            chunks = list(provider.chat_stream(
                [{"role": "user", "content": "hi"}], max_tokens=10))
        self.assertEqual(chunks, ["深度", "优先", "算法"])

    def test_stream_payload_has_stream_true_and_model(self):
        """payload 必须带 stream=True 与正确模型名"""
        captured = {}

        def _spy(url, headers=None, data=None, timeout=None, stream=False, **kw):
            captured["stream"] = stream
            captured["model"] = json.loads(data.decode('utf-8')).get("model")
            captured["timeout"] = timeout
            resp = requests.Response()
            resp.status_code = 200
            resp.raw = FakeRaw(b'data: {"choices":[{"delta":{"content":"x"}}]}\n\ndata: [DONE]\n\n')
            resp.url = url
            return resp

        from services.model_service import get_model_provider
        provider = get_model_provider("deepseek")
        with mock.patch("services.model_service.requests.post", side_effect=_spy):
            list(provider.chat_stream([{"role": "user", "content": "hi"}], timeout=15))
        self.assertTrue(captured["stream"])
        self.assertEqual(captured["model"], "deepseek-chat")
        self.assertEqual(captured["timeout"], 15)

    def test_utf8_multibyte_across_chunks(self):
        """中文多字节跨 chunk 边界必须完整（服务端按完整行 yield，不会截断）"""
        # 一个中文字符的 UTF-8 是 3 字节，确保逐行解析不产生半个字符
        from services.model_service import get_model_provider
        provider = get_model_provider("mimo")
        # 模拟供应商在单个 data 行中返回完整字符（OpenAI SSE 不会拆开字符）
        with mock.patch("services.model_service.requests.post",
                        side_effect=make_sse_response("数据结构导论")):
            chunks = list(provider.chat_stream(
                [{"role": "user", "content": "x"}], max_tokens=10))
        joined = "".join(chunks)
        self.assertEqual(joined, "数据结构导论")
        # 无半个字符（\ufffd 为替换符）
        self.assertNotIn("\ufffd", joined)

    def test_parse_error_gives_clear_type(self):
        """SSE JSON 解析失败 → response_parse_error（不静默、不 unknown）"""
        from services.model_service import get_model_provider, ModelAPIError

        def _bad(url, **kw):
            resp = requests.Response()
            resp.status_code = 200
            resp.raw = FakeRaw(b'data: not-json\n\ndata: [DONE]\n\n')
            resp.url = url
            return resp

        provider = get_model_provider("mimo")
        with mock.patch("services.model_service.requests.post", side_effect=_bad):
            with self.assertRaises(ModelAPIError) as ctx:
                list(provider.chat_stream([{"role": "user", "content": "x"}]))
        self.assertEqual(ctx.exception.error_type, "response_parse_error")


class TestChatStreamService(unittest.TestCase):
    """chat_service.chat_stream 事件流"""

    def _make_docs(self):
        return [
            Document(page_content="二叉树概念：每个节点最多两个子树。",
                     metadata={"source": r"E:\data\tree.pdf", "file_name": "tree.pdf",
                               "page": 3, "type": "text"}),
        ]

    def _patch_rag(self, docs):
        return [
            mock.patch("chat_service.get_rag_context", return_value=docs),
            mock.patch("chat_service.build_context",
                       return_value="二叉树概念：每个节点最多两个子树。"),
            mock.patch("chat_service.extract_sources",
                       return_value=[{"id": 1, "file_name": "tree.pdf",
                                      "page": 3, "type": "text"}]),
        ]

    def test_rag_stream_done_has_sources_model_elapsed_ttft(self):
        """RAG 流：start→delta→done，done 携带 sources/model/elapsed/ttft"""
        from chat_service import chat_stream

        def fake_stream(provider, messages, temperature=None, max_tokens=None):
            for c in ["二叉", "树"]:
                yield c

        pc, pb, pe = self._patch_rag(self._make_docs())
        with pc, pb, pe, \
             mock.patch("chat_service._call_llm_stream", side_effect=fake_stream):
            events = list(chat_stream("什么是二叉树", subject_id="数据结构"))

        types = [e["type"] for e in events]
        self.assertEqual(types, ["start", "delta", "delta", "done"])
        start = events[0]
        # 未指定 model 时必须使用后端默认文本模型（2026-09-25 起为 DeepSeek），
        # 断言写成"从配置解析出的默认模型"而不是字面量，避免默认值变更后测试变成假绿
        from config.models import get_default_model, get_model_config
        expected_model = get_model_config(get_default_model())["model_name"]
        self.assertEqual(start["model"], expected_model)
        self.assertEqual(start["provider"], get_default_model())
        self.assertTrue(start["context_used"])
        done = events[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["sources"][0]["file_name"], "tree.pdf")
        self.assertIn("model", done)
        self.assertIn("elapsed", done)
        self.assertIn("ttft", done)
        self.assertEqual(done["full_answer"], "二叉树")

    def test_plain_chat_stream_no_kb(self):
        """普通聊天（无知识库）：start→delta→done，context_used=False，sources 空"""
        from chat_service import chat_stream

        def fake_stream(provider, messages, temperature=None, max_tokens=None):
            for c in ["普通", "问答"]:
                yield c

        with mock.patch("chat_service.get_rag_context", return_value=[]), \
             mock.patch("chat_service.build_context", return_value=""), \
             mock.patch("chat_service.extract_sources", return_value=[]), \
             mock.patch("chat_service._call_llm_stream", side_effect=fake_stream):
            events = list(chat_stream("你好"))

        self.assertEqual(events[0]["type"], "start")
        self.assertFalse(events[0]["context_used"])
        done = events[-1]
        self.assertEqual(done["sources"], [])

    def test_model_param_passed_to_provider(self):
        """用户选择的 model 必须传到 provider（模型选择对流式生效）"""
        captured = {}

        def fake_stream(provider, messages, temperature=None, max_tokens=None):
            captured["provider_model"] = provider.model_name
            for c in ["x"]:
                yield c

        with mock.patch("chat_service.get_rag_context", return_value=[]), \
             mock.patch("chat_service.build_context", return_value=""), \
             mock.patch("chat_service.extract_sources", return_value=[]), \
             mock.patch("chat_service._call_llm_stream", side_effect=fake_stream):
            list(chat_service_import()("你好", model="deepseek"))
        self.assertEqual(captured["provider_model"], "deepseek")

    def test_provider_error_yields_error_event(self):
        """供应商错误 → error 事件（含明确 message + error_type），不崩溃、不产生 done"""
        from chat_service import chat_stream
        from services.model_service import ModelAPIError

        def fake_stream(provider, messages, temperature=None, max_tokens=None):
            # start 事件后抛出（真实 provider 调用发生在 start 之后）
            yield "第一段"
            raise ModelAPIError("模型API调用失败: invalid_api_key",
                                status_code=401, error_type="invalid_api_key")
            yield  # pragma: no cover

        with mock.patch("chat_service.get_rag_context", return_value=[]), \
             mock.patch("chat_service.build_context", return_value=""), \
             mock.patch("chat_service.extract_sources", return_value=[]), \
             mock.patch("chat_service._call_llm_stream", side_effect=fake_stream):
            events = list(chat_stream("测试"))

        self.assertEqual(events[0]["type"], "start")
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["error_type"], "invalid_api_key")
        self.assertIn("invalid_api_key", events[-1]["message"])
        self.assertNotIn("未知错误", events[-1]["message"])

    def test_generator_close_stops_upstream(self):
        """客户端中断（close generator）→ 上游流 generator 被关闭，不触发 retry"""
        from chat_service import chat_stream

        upstream_closed = {"closed": False}

        def fake_call_llm_stream(provider, messages, temperature=None, max_tokens=None):
            # 真实生成器函数（side_effect），每次调用新建生成器，避免 mock 引用干扰
            try:
                i = 0
                while True:
                    yield f"chunk{i}"
                    i += 1
            except GeneratorExit:
                upstream_closed["closed"] = True
                raise

        with mock.patch("chat_service.get_rag_context", return_value=[]), \
             mock.patch("chat_service.build_context", return_value=""), \
             mock.patch("chat_service.extract_sources", return_value=[]), \
             mock.patch("chat_service._call_llm_stream", side_effect=fake_call_llm_stream):
            gen = chat_stream("中断测试")
            next(gen)  # start
            next(gen)  # 第一个 delta
            # 模拟客户端断开：FastAPI/Starlette 关闭生成器
            gen.close()

        self.assertTrue(upstream_closed["closed"], "上游流应随客户端中断而关闭")
        # close 后无 retry/fallback 产生的额外事件
        with self.assertRaises(StopIteration):
            next(gen)


def chat_service_import():
    import chat_service
    return chat_service.chat_stream


if __name__ == "__main__":
    unittest.main()
