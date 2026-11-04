"""run_pending 批处理（2026-11-04）：同批待增强项合并为一次 enrich_images 调用。

此前逐条串行单张调用，批内去重与并发全部失效；批处理后一次领取
IMPORT_VISION_BATCH_SIZE（默认 8）行，整批一次提交视觉调用。
"""
import hashlib
from unittest.mock import patch

import pytest
from langchain_core.documents import Document
from PIL import Image

from services import import_vision as v


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IMPORT_VISION_BATCH_SIZE", "8")
    source = tmp_path / "subjects" / "s" / "file.txt"
    source.parent.mkdir(parents=True)
    source.write_text("old")
    sha = hashlib.sha256(b"old").hexdigest()
    docs = []
    # 两张内容相同的图（批内去重素材）+ 一张不同的图
    for name, color in (("same1.png", "red"), ("same2.png", "red"), ("diff.png", "blue")):
        p = tmp_path / "deferred_vision" / "j" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 64), color).save(p)
        docs.append(Document(page_content="", metadata={
            "source": "file.txt", "source_content_sha": sha, "image_path": str(p)}))
    with patch.object(v._POOL, "submit"):
        v.enqueue("j", "s", docs)
    return source, docs


def rows():
    with v.connect() as c:
        return [dict(r) for r in c.execute("select * from import_vision_items")]


def test_batch_single_enrich_call(setup):
    source, docs = setup
    published = []

    def enhance(batch):
        assert len(batch) == 3
        return [Document(page_content="图片内容 " * 10, metadata=d.metadata)
                for d in batch]

    def fake_publish(row, doc, owner):
        published.append(row["id"])
        v.update(row["id"], owner, state="succeeded", error=None)

    with patch.object(v, "stopped", return_value=None), \
            patch("rag.enrichment.multimodal_enricher.MultimodalEnricher") as e, \
            patch.object(v, "publish", side_effect=fake_publish):
        e.return_value.enrich_images.side_effect = enhance
        v.run_pending()
    assert e.return_value.enrich_images.call_count == 1
    assert len(published) == 3
    assert all(r["state"] == "succeeded" for r in rows())
    assert all(r["remote_started"] == 1 and r["remote_returned"] == 1 for r in rows())


def test_batch_failure_fails_all(setup):
    source, docs = setup
    with patch.object(v, "stopped", return_value=None), \
            patch("rag.enrichment.multimodal_enricher.MultimodalEnricher") as e:
        e.return_value.enrich_images.side_effect = RuntimeError("provider down")
        v.run_pending()
    assert all(r["state"] == "failed" for r in rows())
    assert all("provider down" in r["error"] for r in rows())
