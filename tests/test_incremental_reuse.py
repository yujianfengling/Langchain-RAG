# -*- coding: utf-8 -*-
"""增量解析与向量化的回归测试（2026-10-08）。

覆盖：
1) 未变化分块复用向量，0 次模型调用；
2) 分块正文变化 → 只重算该分块；
3) 模型签名变化 → 缓存失效（不能拿旧模型的向量冒充新模型）；
4) 内容哈希 + 解析配置决定解析缓存（同名不同内容不复用）；
5) 同名不同内容按"修改"处理：替换旧版本并留档，不产生第二份资料。
"""
import os

import pytest


class _FakeEmbeddings:
    """可计数的假嵌入模型：记录被编码的文本。"""

    def __init__(self, model_name='fake-model-v1'):
        self.model_name = model_name
        self.calls = 0
        self.texts = []

    def embed_documents(self, texts):
        self.calls += 1
        self.texts.extend(texts)
        return [[float(len(t) % 7), 1.0, 2.0] for t in texts]

    def embed_query(self, text):
        self.calls += 1
        self.texts.append(text)
        return [float(len(text) % 7), 1.0, 2.0]


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    monkeypatch.setenv('EDU_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('EMBEDDING_CACHE', raising=False)
    from rag import embedding_cache
    embedding_cache.reset_cache_for_tests()
    yield embedding_cache
    embedding_cache.reset_cache_for_tests()


def test_unchanged_chunks_reuse_vectors_with_zero_model_calls(cache_env):
    emb = _FakeEmbeddings()
    texts = ['chunk A 内容', 'chunk B 内容', 'chunk C 内容']

    vecs1, stats1 = cache_env.embed_texts_cached(texts, emb)
    assert stats1['embedded_texts'] == 3 and stats1['hits'] == 0
    assert emb.calls == 1

    vecs2, stats2 = cache_env.embed_texts_cached(texts, emb)
    assert stats2['embedded_texts'] == 0, '未变化的分块不应再编码'
    assert stats2['hits'] == 3 and stats2['embed_calls'] == 0
    assert emb.calls == 1, '第二次不得调用模型'
    assert [list(v) for v in vecs1] == [list(v) for v in vecs2], '复用向量必须与原向量一致'


def test_changed_chunk_only_reembeds_that_chunk(cache_env):
    emb = _FakeEmbeddings()
    base = ['chunk A', 'chunk B']
    cache_env.embed_texts_cached(base, emb)
    assert emb.calls == 1

    mixed = ['chunk A', 'chunk B 改了']
    _, stats = cache_env.embed_texts_cached(mixed, emb)
    assert stats['hits'] == 1 and stats['embedded_texts'] == 1, stats
    assert emb.texts[-1] == 'chunk B 改了'


def test_model_signature_change_invalidates_cache(cache_env):
    texts = ['同一段正文']
    emb1 = _FakeEmbeddings('model-A')
    cache_env.embed_texts_cached(texts, emb1)
    emb2 = _FakeEmbeddings('model-B')
    _, stats = cache_env.embed_texts_cached(texts, emb2)
    assert stats['hits'] == 0 and stats['embedded_texts'] == 1, '换模型必须重新编码'


def test_bypass_switch_disables_cache(cache_env, monkeypatch):
    emb = _FakeEmbeddings()
    texts = ['chunk X']
    cache_env.embed_texts_cached(texts, emb)
    monkeypatch.setenv('EMBEDDING_CACHE', '0')
    _, stats = cache_env.embed_texts_cached(texts, emb)
    assert stats['bypassed'] is True and stats['embedded_texts'] == 1
    assert emb.calls == 2


def test_same_name_different_content_replaces_and_archives(tmp_path):
    """同名不同内容 = 修改：替换旧文件并留档，不产生第二份资料。"""
    from services import import_service as svc
    subject = tmp_path / 'subjects' / 'inc'
    subject.mkdir(parents=True)
    (subject / 'a.txt').write_bytes('旧内容'.encode('utf-8'))

    decision = svc._decide_target(subject, 'a.txt', '新内容'.encode('utf-8'))
    assert decision['replace'] is True, decision
    assert decision['name'] == 'a.txt', '修改后仍应写回原文件名'
    assert decision.get('old_sha'), '必须记录旧内容哈希以便留档/追溯'


def test_same_name_same_content_is_skipped(tmp_path):
    from services import import_service as svc
    subject = tmp_path / 'subjects' / 'inc'
    subject.mkdir(parents=True)
    payload = '内容一致'.encode('utf-8')
    (subject / 'a.txt').write_bytes(payload)
    decision = svc._decide_target(subject, 'a.txt', payload)
    assert decision['skip'] is True and not decision.get('replace')
