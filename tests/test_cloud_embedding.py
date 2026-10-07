# -*- coding: utf-8 -*-
"""云端 Embedding 适配层离线测试（不访问真实 API）。"""
import json

import pytest

from rag.embedding_providers import CloudEmbeddingError, CloudEmbeddings


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload else "")

    def json(self):
        return self._payload


class _FakeSession:
    """记录请求、可注入失败序列的假 session。"""

    def __init__(self, script=None):
        self.calls = []
        self.script = list(script or [])

    def post(self, url, data=None, headers=None, timeout=None):
        body = json.loads(data.decode("utf-8"))
        self.calls.append({"url": url, "body": body, "headers": headers})
        step = self.script.pop(0) if self.script else None
        if callable(step):
            return step(body)
        if step is not None:
            return step
        n = len(body["input"])
        return _Resp(200, {"data": [{"index": i, "embedding": [0.1] * 4} for i in range(n)],
                           "usage": {"prompt_tokens": 7 * n}})


def _client(session, **kw):
    kw.setdefault("api_key", "test-key")
    kw.setdefault("max_rpm", 0)
    kw.setdefault("max_retries", 1)
    kw.setdefault("concurrency", 1)
    return CloudEmbeddings(session=session, **kw)


def test_batches_at_documented_max_rows_of_10():
    session = _FakeSession()
    client = _client(session)
    vectors = client.embed_documents(["t%d" % i for i in range(23)])
    assert len(vectors) == 23
    assert [len(c["body"]["input"]) for c in session.calls] == [10, 10, 3], "每批不得超过官方 10 行上限"


def test_vectors_preserve_input_order_across_batches():
    session = _FakeSession()
    client = _client(session, concurrency=3)
    vectors = client.embed_documents(["a%d" % i for i in range(25)])
    assert len(vectors) == 25, "并发拼装后数量必须与输入一致"


def test_retries_on_429_then_succeeds():
    session = _FakeSession(script=[_Resp(429, text="rate limited")])
    client = _client(session, max_retries=2)
    vectors = client.embed_documents(["x"])
    assert len(vectors) == 1
    assert len(session.calls) == 2, "429 应重试一次后成功"
    assert client.stats["retries"] >= 1


def test_auth_failure_is_explicit_and_not_retried_forever():
    session = _FakeSession(script=[_Resp(401, text="invalid api key")])
    client = _client(session, max_retries=3)
    with pytest.raises(CloudEmbeddingError) as e:
        client.embed_documents(["x"])
    assert "鉴权失败" in str(e.value)
    assert len(session.calls) == 1, "鉴权失败不应反复重试"


def test_missing_key_raises_actionable_error():
    client = CloudEmbeddings(api_key="", session=_FakeSession())
    with pytest.raises(CloudEmbeddingError) as e:
        client.embed_documents(["x"])
    assert "DASHSCOPE_API_KEY" in str(e.value)


def test_budget_pauses_and_reports():
    session = _FakeSession()
    client = _client(session, budget_tokens=5)
    client.embed_documents(["one batch"])          # 消耗 7 token 预算
    with pytest.raises(CloudEmbeddingError) as e:
        client.embed_documents(["another"])
    assert "预算" in str(e.value)
    assert client.stats["budget_exceeded"] is True


def test_long_text_is_truncated_with_warning_not_silently_dropped():
    session = _FakeSession()
    client = _client(session, max_tokens_per_line=10)
    client.embed_documents(["z" * 50])
    assert session.calls[0]["body"]["input"] == ["z" * 10]


def test_usage_tokens_are_accounted():
    session = _FakeSession()
    client = _client(session)
    client.embed_documents(["a", "b"])
    assert client.stats["input_tokens"] == 14, "应按返回的 usage 记账"
    assert client.stats["rows"] == 2


def test_provider_and_signature_separate_from_local_cache():
    from rag.embedding_cache import SCHEMA, _model_signature
    sig = _model_signature(CloudEmbeddings(api_key="k"))
    assert sig.startswith("dashscope/text-embedding-v4/"), sig
    assert SCHEMA == "emb-v2", "签名版本提升后才能保证旧 BGE 向量不被复用"


def test_index_encoder_matches_meta_and_never_mixes():
    from rag.embedding import get_embedding_for_index
    cloud = get_embedding_for_index({"embedding_provider": "dashscope",
                                     "embedding_model": "text-embedding-v4"})
    assert type(cloud).__name__ == "CloudEmbeddings"
    assert getattr(cloud, "dimensions", None) == 1024


def test_index_without_provider_resolves_to_local_for_migration():
    from unittest import mock
    from rag import embedding as emb
    sentinel = object()
    with mock.patch.object(emb, "get_embedding_model", return_value=sentinel) as m:
        out = emb.get_embedding_for_index({"embedding_model": "BAAI/bge-small-zh-v1.5"})
    assert out is sentinel, "旧索引必须解析回本地编码器（迁移期间继续服务）"
    assert m.called