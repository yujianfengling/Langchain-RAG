# -*- coding: utf-8 -*-
"""索引"版本化构建 + 原子发布"的回归测试（2026-10-08）。

背景（已实测）：旧的 _build_index_with_rollback 是「备份 → 删除线上索引 → 重建」，
删除与重建之间（大库可达数分钟）线上索引为空，学生检索不到资料；进程若在此窗口
被杀，回滚不会执行，线上索引永久为空（sub-kg5yy0t4 曾为 0 文件）。

本文件锁定三条：
1. 构建期间旧版本仍然可用（用"构建很慢"的受控替身，在构建中查检索接口）；
2. 构建失败/中断只清理 staging，线上旧版本完全不受影响；
3. FAISS / BM25 / meta.json 属于同一次构建（build_id + 条目数严格一致）。

注：BM25 在 df >= N/2 时对唯一 token 会给出非正 IDF（rank_bm25 行为），故每个
版本用四份资料、查询各自独有的 token，保证命中来自真实打分而非测试巧合。
"""

import hashlib
import os
import pickle
import threading

import pytest
from langchain_core.documents import Document

import rag.embedding as emb
import rag.hybrid_retriever as hr
import rag.rag_service as rs
from services import knowledge_service as ks
from rag.hybrid_retriever import read_bm25_meta
from rag.vector_store import GlobalFAISS, is_index_sidecar, read_index_meta

SUBJECT = "sub-atomic-publish"

# 注：rank_bm25 的 IDF = log((N-df+0.5)/(df+0.5))，只有 df < N/2 才为正；
# 因此每个版本放 4 份资料、查询只出现在 1 份里的 token，保证打分 > 0。
OLD_DOCS = [
    "ZZOLDALPHA 二叉树的先序遍历是根左右，后序遍历是左右根",
    "ZZOLDBETA 冒泡排序每次比较相邻两个元素",
    "ZZOLDGAMMA 图的深度优先遍历使用栈",
    "ZZOLDDELTA 单链表插入节点需要修改两次指针",
]
NEW_DOCS = [
    "ZZNEWALPHA 哈夫曼树是带权路径最短的二叉树",
    "ZZNEWBETA 快速排序平均时间复杂度为 O(n log n)",
    "ZZNEWGAMMA 最小生成树可用 Kruskal 算法",
    "ZZNEWDELTA 拓扑排序适用于有向无环图",
]


class _FakeEmbeddings:
    """确定性假嵌入：避免加载真实 HF 模型（慢且吃内存），维度固定。"""

    dim = 16

    def _vec(self, text):
        h = hashlib.sha256(text.encode("utf-8")).digest()
        return [b / 255.0 for b in h[: self.dim]]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    _clear_memory_caches()
    fake = _FakeEmbeddings()
    # 构建路径用的是 rag_service 顶部导入的符号；检索路径用的是函数内动态导入
    monkeypatch.setattr(rs, "get_embedding_model", lambda: fake)
    monkeypatch.setattr(emb, "get_embedding_model", lambda: fake)
    yield tmp_path
    _clear_memory_caches()


def _clear_memory_caches():
    GlobalFAISS.clear_all()
    hr._bm25_retrievers.clear()


def _long(text, n=12):
    return (text + "。") * n


def _docs(texts, source="demo.txt"):
    return [Document(page_content=_long(t), metadata={"source": source})
            for t in texts]


def _paths(tmp_path):
    vs = tmp_path / "vector_store" / SUBJECT
    bm = tmp_path / "bm25_index" / SUBJECT
    return vs, bm


def _retrieve(query):
    return hr.retrieve(query, SUBJECT, top_k=3, use_vector=False, use_bm25=True)


def _live_vector_documents(vs):
    with open(vs / "index.pkl", "rb") as f:
        return pickle.load(f)["documents"]


def _hits(query):
    return "\n".join(h["content"] for h in _retrieve(query))


def test_old_index_is_served_while_staging_builds(env, monkeypatch):
    """1) 构建进行中，检索必须仍命中旧版本内容（不再是空窗）。"""
    tmp_path = env
    ks.rebuild_subject_knowledge_from_documents(SUBJECT, _docs(OLD_DOCS))
    vs, bm = _paths(tmp_path)
    assert (vs / "index.faiss").exists()
    assert (bm / "bm25.pkl").exists()
    assert "ZZOLDALPHA" in _hits("ZZOLDALPHA"), "初始旧版本本应可检索"

    real_build = ks.build_knowledge_base
    started = threading.Event()
    release = threading.Event()

    def slow_build(**kwargs):
        started.set()
        assert release.wait(20), "测试超时：慢构建未被放行"
        return real_build(**kwargs)

    monkeypatch.setattr(ks, "build_knowledge_base", slow_build)
    errors = []

    def run():
        try:
            ks.rebuild_subject_knowledge_from_documents(SUBJECT, _docs(NEW_DOCS))
        except Exception as e:  # pragma: no cover - 仅记录，交给主线程断言
            errors.append(e)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(20), "构建未开始"

    # —— 构建进行中：线上目录保持原样，旧内容仍可检索 ——
    assert (vs / "index.faiss").exists(), "构建期间线上 FAISS 被删/被挪走"
    live_docs = _live_vector_documents(vs)
    assert any("ZZOLDALPHA" in d.page_content for d in live_docs)
    assert not any("ZZNEWALPHA" in d.page_content for d in live_docs)
    assert "ZZOLDALPHA" in _hits("ZZOLDALPHA"), "构建期间检索不到旧版本内容"

    release.set()
    thread.join(60)
    assert not errors, errors

    # —— 构建完成：新版本上线，旧版本转存为 .prev，staging/.old 不残留 ——
    assert "ZZNEWALPHA" in _hits("ZZNEWALPHA"), "构建完成后新版本不可检索"
    assert not (vs.parent / f"{SUBJECT}.building").exists()
    assert not (vs.parent / f"{SUBJECT}.old").exists()
    assert (vs.parent / f"{SUBJECT}.prev").exists()


def test_failed_build_keeps_old_and_cleans_staging(env, monkeypatch):
    """2) 构建中途抛错 → 线上旧版本不变、staging 被清理、.old 不残留。"""
    tmp_path = env
    ks.rebuild_subject_knowledge_from_documents(SUBJECT, _docs(OLD_DOCS))
    vs, bm = _paths(tmp_path)
    before = sorted(p.name for p in vs.rglob("*") if p.is_file())

    def boom(**kwargs):
        # 模拟"已写出 staging 文件后进程崩溃"
        from pathlib import Path
        staging = Path(kwargs["persist_dir"])
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "partial.bin").write_bytes(b"x")
        raise RuntimeError("boom")

    monkeypatch.setattr(ks, "build_knowledge_base", boom)
    with pytest.raises(ks.KnowledgeServiceError):
        ks.rebuild_subject_knowledge_from_documents(SUBJECT, _docs(NEW_DOCS))

    assert sorted(p.name for p in vs.rglob("*") if p.is_file()) == before, \
        "构建失败竟改动了线上索引"
    assert not (vs.parent / f"{SUBJECT}.building").exists(), "staging 未清理"
    assert not (bm.parent / f"{SUBJECT}.building").exists(), "bm25 staging 未清理"
    assert not (vs.parent / f"{SUBJECT}.old").exists(), ".old 残留"
    assert "ZZOLDALPHA" in _hits("ZZOLDALPHA"), "失败后旧版本仍应可用"


def test_faiss_bm25_meta_are_same_version_after_publish(env):
    """3) 切换后 FAISS 块数 / BM25 条目数 / meta.documents 必须一致且同 build_id。"""
    tmp_path = env
    r1 = ks.rebuild_subject_knowledge_from_documents(SUBJECT, _docs(OLD_DOCS))
    r2 = ks.rebuild_subject_knowledge_from_documents(SUBJECT, _docs(NEW_DOCS))

    vs, bm = _paths(tmp_path)
    meta = read_index_meta(str(vs))
    bmeta = read_bm25_meta(str(bm))
    assert meta and bmeta

    faiss_count = ks._count_faiss_vectors(vs)
    with open(bm / "bm25.pkl", "rb") as f:
        bm25_count = len(pickle.load(f)["documents"])

    assert faiss_count > 0
    assert meta["documents"] == faiss_count == bm25_count, \
        f"版本不一致: meta={meta['documents']} faiss={faiss_count} bm25={bm25_count}"
    assert meta["build_id"] == bmeta["build_id"] == r2["build_id"]
    assert r1["build_id"] != r2["build_id"]

    # .prev 是"上一次有效版本"，其 meta 属于第一次构建
    prev_meta = read_index_meta(str(vs.parent / f"{SUBJECT}.prev"))
    assert prev_meta and prev_meta["build_id"] == r1["build_id"]


def test_rebuild_with_same_documents_is_idempotent(env):
    """4) 同样的资料重建两次，条目数稳定、内容仍可检索（幂等）。"""
    r1 = ks.rebuild_subject_knowledge_from_documents(SUBJECT, _docs(OLD_DOCS))
    r2 = ks.rebuild_subject_knowledge_from_documents(SUBJECT, _docs(OLD_DOCS))

    vs, _bm = _paths(env)
    meta = read_index_meta(str(vs))
    assert r1["chunk_count"] == r2["chunk_count"] == meta["documents"]
    assert ks._count_faiss_vectors(vs) == meta["documents"]
    assert "ZZOLDALPHA" in _hits("ZZOLDALPHA")


def test_index_sidecars_are_not_subjects():
    """旁挂目录（staging/old/prev/bak）不得被当成学科。"""
    assert is_index_sidecar("sub-x.building")
    assert is_index_sidecar("sub-x.old")
    assert is_index_sidecar("sub-x.prev")
    assert is_index_sidecar("sub-x.bak")
    assert not is_index_sidecar("sub-x")


def _write_files(dir_path, n=2):
    import os
    os.makedirs(dir_path, exist_ok=True)
    for i in range(n):
        with open(os.path.join(str(dir_path), f"f{i}.bin"), "wb") as fh:
            fh.write(b"x" * 16)


def test_orphan_recovery_restores_prev_when_live_empty(monkeypatch, tmp_path):
    """线上为空 + 只有 .prev（上一次有效版本备份）→ 恢复线上并消费 .prev。"""
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    vs = tmp_path / "vector_store"
    live = vs / "sub-demo"
    live.mkdir(parents=True)
    _write_files(vs / "sub-demo.prev", 2)

    fixed = ks.restore_orphaned_index_backups()

    assert fixed == 1, fixed
    assert (live / "f0.bin").exists(), "未从 .prev 恢复"
    assert not (vs / "sub-demo.prev").exists(), ".prev 应被消费"


def test_orphan_recovery_prefers_old_and_keeps_prev(monkeypatch, tmp_path):
    """发布被中断（线上空 + .old 完整）→ 优先用 .old 恢复；.prev 作为备份保留。"""
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    vs = tmp_path / "vector_store"
    live = vs / "sub-demo"
    live.mkdir(parents=True)
    _write_files(vs / "sub-demo.old", 3)
    _write_files(vs / "sub-demo.prev", 2)

    fixed = ks.restore_orphaned_index_backups()

    assert fixed == 1, fixed
    assert (live / "f0.bin").exists(), "未从 .old 恢复"
    assert not (vs / "sub-demo.old").exists(), ".old 应被消费"
    assert (vs / "sub-demo.prev").exists(), "有效 .prev 备份应保留"


def test_orphan_recovery_keeps_prev_when_live_complete(monkeypatch, tmp_path):
    """.prev 是有意保留的备份：线上完整时不得删除它。"""
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    vs = tmp_path / "vector_store"
    _write_files(vs / "sub-ok", 2)
    _write_files(vs / "sub-ok.prev", 2)

    fixed = ks.restore_orphaned_index_backups()

    assert fixed == 0
    assert (vs / "sub-ok" / "f0.bin").exists()
    assert (vs / "sub-ok.prev" / "f0.bin").exists(), ".prev 备份被误删"


def test_recover_index_sidecars_promotes_old(monkeypatch, tmp_path):
    """建库前的自愈：线上为空 + .old 有内容 → .old 提升为线上。"""
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    live = tmp_path / "vector_store" / "sub-demo"
    old = tmp_path / "vector_store" / "sub-demo.old"
    prev = tmp_path / "vector_store" / "sub-demo.prev"
    live.mkdir(parents=True)
    _write_files(old, 2)

    ks._recover_index_sidecars("sub-demo", live, old, prev)

    assert (live / "f0.bin").exists()
    assert not old.exists()


def test_publish_pair_rolls_back_on_second_rename(tmp_path, monkeypatch):
    """成对发布中第二次 rename 失败 → 两个旧版本都放回线上，不出现"新 FAISS+旧 BM25"。"""
    vs = tmp_path / "v"
    bs = tmp_path / "b"
    live_v, old_v, prev_v, staging_v = vs / "s", vs / "s.old", vs / "s.prev", vs / "s.building"
    live_b, old_b, prev_b, staging_b = bs / "s", bs / "s.old", bs / "s.prev", bs / "s.building"
    _write_files(live_v, 2)
    _write_files(live_b, 2)
    _write_files(staging_v, 2)
    _write_files(staging_b, 2)

    real_replace = os.replace

    def flaky(src, dst):
        if str(src) == str(staging_b):
            raise OSError("simulated disk failure")
        return real_replace(src, dst)

    monkeypatch.setattr(ks.os, "replace", flaky)
    with pytest.raises(OSError):
        ks._publish_index_pair(live_v, staging_v, old_v, prev_v,
                               live_b, staging_b, old_b, prev_b)

    assert (live_v / "f0.bin").exists(), "向量旧版本未放回"
    assert (live_b / "f0.bin").exists(), "BM25 旧版本未放回"
    assert not staging_v.exists() and not staging_b.exists(), "staging 未清理"
