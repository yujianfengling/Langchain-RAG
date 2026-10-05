"""短资料不应被长度过滤整份丢弃（2026-10-05 回归）

一份 60 字的验收说明只有 1 个分块，低于 CHUNK_MIN_LENGTH；此前会被整份丢弃：
资料列表显示"已就绪"、知识库报 ready，但学生永远检索不到这份资料。
现在按来源文档兜底：每个文件至少保留 1 个有内容的分块。
"""
from langchain_core.documents import Document

from rag.vector_store import filter_documents_by_min_length


def test_short_single_chunk_file_is_kept():
    docs = [
        Document(page_content="这是一份足够长的正常资料内容。" * 20,
                 metadata={"file_name": "long.txt"}),
        Document(page_content="本课程实验控制台口令为 FALCON-3310。",
                 metadata={"file_name": "short.txt"}),
    ]
    kept, stats = filter_documents_by_min_length(docs, min_length=90)
    names = [((d.metadata or {}).get("file_name")) for d in kept]
    assert "short.txt" in names, "短资料应至少保留一个分块"
    assert stats["rescued_sources"] == 1


def test_meta_wrapper_is_still_dropped_even_as_only_chunk():
    docs = [Document(page_content="[图片] | 尺寸: 1052x738 | 页码: 24",
                     metadata={"file_name": "img.txt"})]
    kept, _ = filter_documents_by_min_length(docs, min_length=90)
    assert kept == []
