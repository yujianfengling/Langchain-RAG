"""页面级索引（2026-10-24）

图片型课件（扫描 PDF、PPT 截图）的文本层往往极少，纯 chunk 检索会漏掉
图、表、公式与版式。这里给每个 PDF 建立**页面级**索引：

- 每页渲染成 WebP（120 DPI 左右），保留原页面的视觉信息；
- 抽取文本层；没有文本层时再用 OCR（可用则用，不可用不阻断）；
- 页面文本进 SQLite，供 BM25/关键词打分；
- 检索结果带页码、片段与页面图 URL，可回到原页核对。

设计约束：
- 只读 data/subjects/<subject_id> 下的 PDF；不碰课程资料目录；
- 索引是**旁路**，不改变现有 chunk 检索；页面索引不存在时工具如实说明；
- 失败不抛出到问答主链路，最多返回空结果 + 原因。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

from config.path_config import get_data_dir, get_subjects_dir
from utils.logger import logger

_MAX_PAGES_PER_DOC = 400          # 防止超长 PDF 把索引撑爆
_RENDER_DPI = 110


def _db_path() -> Path:
    return get_data_dir() / "page_index.db"


def _images_root() -> Path:
    return get_data_dir() / "page_images"


def images_root() -> Path:
    """页面图根目录（供 API 做路径边界校验）。"""
    return _images_root()


def _subject_key(subject_id: str) -> str:
    return hashlib.sha1(str(subject_id).encode("utf-8")).hexdigest()[:16]


def _doc_key(doc_id: str) -> str:
    return hashlib.sha1(str(doc_id).encode("utf-8")).hexdigest()[:16]


def _conn() -> sqlite3.Connection:
    os.makedirs(get_data_dir(), exist_ok=True)
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pages (
            subject_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            page_no INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            text TEXT NOT NULL DEFAULT '',
            blocks_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            PRIMARY KEY (subject_id, doc_id, page_no)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pages_subject ON pages(subject_id, doc_id)"
    )
    # 老库加法迁移：只补列，不动已有页面数据
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pages)").fetchall()}
    if "blocks_json" not in cols:
        conn.execute("ALTER TABLE pages ADD COLUMN blocks_json TEXT NOT NULL DEFAULT '[]'")
    conn.commit()
    return conn


def _ocr(image) -> str:
    try:
        import pytesseract
    except Exception:
        return ""
    try:
        return pytesseract.image_to_string(image, lang="chi_sim+eng")
    except Exception:
        try:
            return pytesseract.image_to_string(image, lang="eng")
        except Exception as e:
            logger.debug(f"页面 OCR 跳过: {e}")
            return ""


def _page_text(page) -> str:
    try:
        text = (page.get_text("text") or "").strip()
    except Exception:
        text = ""
    return text


def _safe_text(value: Any, limit: int = 200_000) -> str:
    return str(value or "")[:limit]


def build_page_index(subject_id: str, force: bool = False) -> Dict[str, Any]:
    """为学科下的 PDF 建立/更新页面索引。

    force=False 时，页数未变化的文档直接跳过；force=True 时重建该文档页面。
    """
    subject_dir = get_subjects_dir() / str(subject_id)
    if not subject_dir.is_dir():
        return {"ok": True, "subject_id": subject_id, "documents": 0, "pages": 0,
                "note": "该学科没有资料目录"}
    files = sorted(p for p in subject_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in (".pdf", ".pptx", ".ppt"))
    if not files:
        return {"ok": True, "subject_id": subject_id, "documents": 0, "pages": 0,
                "note": "该学科没有 PDF/PPT（页面索引支持 PDF、PPTX、PPT）"}

    from PIL import Image

    conn = _conn()
    total_pages = 0
    skipped = 0
    failed: List[Dict[str, str]] = []
    try:
        for source in files:
            doc_id = "f:" + source.name
            image_dir = _images_root() / _subject_key(subject_id) / _doc_key(doc_id)
            os.makedirs(image_dir, exist_ok=True)
            pdf_path = source
            if source.suffix.lower() in (".pptx", ".ppt"):
                # PPTX/PPT 先转 PDF，再逐页渲染；页码/幻灯片号 1:1 对应。
                try:
                    from services.office_converter import convert
                    converted = convert(str(source), ".pdf")
                    pdf_path = Path(converted["converted_path"])
                except Exception as e:
                    logger.warning(f"页面索引：PPT 转 PDF 失败 {source.name}: {e}")
                    failed.append({"file_name": source.name, "error": str(e)[:200]})
                    continue
            try:
                import fitz
                doc = fitz.open(str(pdf_path))
            except Exception as e:
                logger.warning(f"页面索引：打开文档失败 {source.name}: {e}")
                failed.append({"file_name": source.name, "error": str(e)[:200]})
                continue
            try:
                page_count = min(int(doc.page_count), _MAX_PAGES_PER_DOC)
                existing = conn.execute(
                    "SELECT COUNT(*) AS c FROM pages WHERE subject_id=? AND doc_id=?",
                    (subject_id, doc_id)).fetchone()["c"]
                if not force and existing == page_count and existing > 0:
                    skipped += 1
                    continue
                conn.execute("DELETE FROM pages WHERE subject_id=? AND doc_id=?",
                             (subject_id, doc_id))
                for index in range(page_count):
                    page = doc.load_page(index)
                    page_no = index + 1
                    pix = page.get_pixmap(dpi=_RENDER_DPI, colorspace=fitz.csRGB, alpha=False)
                    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    image_path = image_dir / f"p{page_no}.webp"
                    image.save(str(image_path), "WEBP", quality=80, method=4)

                    text = _page_text(page)
                    if not text:
                        text = _ocr(image)
                    blocks = []
                    scale = _RENDER_DPI / 72.0
                    try:
                        for raw_block in page.get_text("blocks"):
                            if len(raw_block) < 5:
                                continue
                            block_text = str(raw_block[4] or "").strip()
                            if not block_text:
                                continue
                            blocks.append({
                                "bbox": [round(float(raw_block[0]) * scale, 1),
                                         round(float(raw_block[1]) * scale, 1),
                                         round(float(raw_block[2]) * scale, 1),
                                         round(float(raw_block[3]) * scale, 1)],
                                "text": block_text[:300],
                            })
                    except Exception:
                        blocks = []
                    if not blocks:
                        blocks = [{"bbox": [0, 0, pix.width, pix.height],
                                   "text": _safe_text(text, 300)}]
                    conn.execute(
                        """INSERT OR REPLACE INTO pages
                           (subject_id, doc_id, file_name, page_no, image_path,
                            width, height, text, blocks_json, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (subject_id, doc_id, source.name, page_no, str(image_path),
                         pix.width, pix.height, _safe_text(text),
                         json.dumps(blocks, ensure_ascii=False),
                         datetime.now().isoformat(timespec="seconds")),
                    )
                    total_pages += 1
                conn.commit()
            except Exception as e:
                logger.warning(f"页面索引：处理 {source.name} 失败: {e}")
                failed.append({"file_name": source.name, "error": str(e)[:200]})
                conn.rollback()
            finally:
                try:
                    doc.close()
                except Exception:
                    pass
    finally:
        conn.close()

    return {"ok": True, "subject_id": subject_id, "documents": len(files),
            "pages": total_pages, "skipped_documents": skipped, "failed": failed}


def _tokens(query: str) -> List[str]:
    query = (query or "").strip().lower()
    if not query:
        return []
    try:
        import jieba
        raw = [t.strip() for t in jieba.lcut(query)]
    except Exception:
        raw = re.findall(r"[\w\u4e00-\u9fff]+", query)
    seen = set()
    out: List[str] = []
    for token in raw:
        if len(token) < 1 or token in seen:
            continue
        if re.fullmatch(r"[\W_]+", token):
            continue
        seen.add(token)
        out.append(token)
    return out[:12]


def _snippet(text: str, tokens: List[str], width: int = 90) -> str:
    if not text:
        return ""
    lowered = text.lower()
    pos = -1
    for token in tokens:
        pos = lowered.find(token)
        if pos >= 0:
            break
    if pos < 0:
        return text[: width * 2]
    start = max(0, pos - width)
    end = min(len(text), pos + width)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def search_pages(subject_id: str, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """按页检索：返回页码、片段与页面图 URL；没有索引时返回空列表。"""
    tokens = _tokens(query)
    if not tokens:
        return []
    top_k = max(1, min(int(top_k or 5), 20))
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM pages WHERE subject_id=? LIMIT 5000", (subject_id,)).fetchall()
    finally:
        conn.close()
    scored: List[Dict[str, Any]] = []
    for row in rows:
        text = row["text"] or ""
        lowered = text.lower()
        score = sum(lowered.count(token) for token in tokens)
        if score <= 0:
            continue
        scored.append({"row": row, "score": score})
    scored.sort(key=lambda item: (-item["score"], item["row"]["page_no"]))
    out: List[Dict[str, Any]] = []
    for item in scored[:top_k]:
        row = item["row"]
        blocks = []
        if "blocks_json" in row.keys():
            try:
                blocks = json.loads(row["blocks_json"] or "[]")
            except ValueError:
                blocks = []
        best_block = None
        best_score = -1
        for block in blocks:
            block_text = str(block.get("text") or "")
            block_score = sum(block_text.lower().count(token) for token in tokens)
            if block_score > best_score:
                best_score = block_score
                best_block = block
        bbox = (best_block.get("bbox") if best_block else None) or [
            0, 0, row["width"] or 0, row["height"] or 0]
        snippet_source = (best_block.get("text") if best_block else "") or row["text"] or ""
        out.append({
            "doc_id": row["doc_id"],
            "file_name": row["file_name"],
            "page": row["page_no"],
            "snippet": _snippet(snippet_source, tokens),
            "block_text": (best_block.get("text") if best_block else "") or "",
            "bbox": bbox,
            "image_width": row["width"],
            "image_height": row["height"],
            "score": item["score"],
            "page_image_url": page_image_url(subject_id, row["doc_id"], row["page_no"]),
        })
    return out


def page_image_url(subject_id: str, doc_id: str, page_no: int) -> str:
    params = urlencode({"doc_id": doc_id, "page": int(page_no)})
    return f"/api/knowledge/{quote(str(subject_id), safe='')}/pages/image?{params}"


def get_page_image(subject_id: str, doc_id: str, page_no: int) -> Optional[str]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT image_path FROM pages WHERE subject_id=? AND doc_id=? AND page_no=?",
            (subject_id, doc_id, int(page_no))).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    path = Path(row["image_path"])
    return str(path) if path.is_file() else None


def list_document_pages(subject_id: str, doc_id: str) -> Dict[str, Any]:
    """列出某份文档已建好的页面（供资料库 PDF 预览按页取图）。"""
    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT page_no, file_name, width, height FROM pages
               WHERE subject_id=? AND doc_id=? ORDER BY page_no""",
            (subject_id, doc_id)).fetchall()
    finally:
        conn.close()
    return {
        "subject_id": subject_id,
        "doc_id": doc_id,
        "file_name": rows[0]["file_name"] if rows else "",
        "pages": [
            {
                "page": row["page_no"],
                "width": row["width"],
                "height": row["height"],
                "page_image_url": page_image_url(subject_id, doc_id, row["page_no"]),
            }
            for row in rows
        ],
    }


def get_page_index_status(subject_id: str) -> Dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS pages, COUNT(DISTINCT doc_id) AS documents,
                      MAX(created_at) AS updated_at
               FROM pages WHERE subject_id=?""", (subject_id,)).fetchone()
    finally:
        conn.close()
    return {"subject_id": subject_id, "pages": row["pages"] or 0,
            "documents": row["documents"] or 0, "updated_at": row["updated_at"]}


def delete_subject_pages(subject_id: str) -> int:
    """删除某学科的页面索引（学科删除时调用；不删文件也不影响主库）。"""
    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM pages WHERE subject_id=?", (subject_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


__all__ = [
    "build_page_index", "search_pages", "get_page_image", "get_page_index_status",
    "list_document_pages",
    "delete_subject_pages", "page_image_url", "images_root",
]
