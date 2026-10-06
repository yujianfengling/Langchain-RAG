"""资料范围过滤（2026-10-06）：回答依据必须真的限定在所选资料内。

此前前端只能改文案，检索仍是全量；这里锁定 services.ai_service.get_rag_context
的 source_filter 行为：只保留所选来源，且扩大候选池后再截断。
"""
from unittest import mock

from services import ai_service


class _Doc:
    def __init__(self, source, text="内容"):
        self.metadata = {"source": source}
        self.page_content = text


def _fake_retrieve(docs):
    def _inner(question=None, subject_id=None, top_k=5, min_score=None):
        return list(docs)[:top_k]
    return _inner


def test_source_filter_limits_to_selected_files():
    docs = [_Doc("演示讲义.txt"), _Doc("验收图片资料.png"), _Doc("images/演示讲义.txt")]
    with mock.patch.object(ai_service, "_load_rag", return_value=True), \
         mock.patch.object(ai_service, "retrieve_context", side_effect=_fake_retrieve(docs)), \
         mock.patch("services.teaching_access.filter_visible_documents", side_effect=lambda u, s, d: d):
        all_docs = ai_service.get_rag_context("问题", "subj", top_k=5, viewer_user_id="u1")
        assert len(all_docs) == 3

        picked = ai_service.get_rag_context(
            "问题", "subj", top_k=5, viewer_user_id="u1", source_filter=["演示讲义.txt"])
        names = [d.metadata["source"] for d in picked]
        assert names == ["演示讲义.txt", "images/演示讲义.txt"], names

        none = ai_service.get_rag_context(
            "问题", "subj", top_k=5, viewer_user_id="u1", source_filter=["不存在的文件.txt"])
        assert none == []
