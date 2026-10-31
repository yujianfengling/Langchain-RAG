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
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
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


def _doc_id_for_file(name: str) -> str:
    """文件级稳定身份，与 `material_scope.DOC_PREFIX_FILE` 保持一致。"""
    return "f:" + str(name)


def _doc_id_for_material(material_id: str) -> str:
    """课程资料的稳定身份（重建索引/改名后不变），作为文件级身份的别名。"""
    return "m:" + str(material_id)


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
    # 2026-10-30：资料身份别名。课程资料的稳定身份是 m:<material_id>，
    # 而页面索引按磁盘文件名定位（f:<文件名>）。这张表把两者对齐，
    # 前端就不必硬编码 `f:文件名`，改名或重建索引后也仍能找到页面。
    conn.execute(
        """CREATE TABLE IF NOT EXISTS doc_alias (
            subject_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            PRIMARY KEY (subject_id, alias)
        )"""
    )
    # 每份资料的建页结果（真实总页数、覆盖范围、是否被上限截断）。
    # 有了它，状态接口不必为了显示"共 N 页"去重新打开 PDF。
    conn.execute(
        """CREATE TABLE IF NOT EXISTS doc_state (
            subject_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            total_pages INTEGER NOT NULL DEFAULT 0,
            covered_to INTEGER NOT NULL DEFAULT 0,
            truncated INTEGER NOT NULL DEFAULT 0,
            source_sha TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (subject_id, doc_id)
        )"""
    )
    # 老库加法迁移：只补列，不动已有页面数据
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pages)").fetchall()}
    if "blocks_json" not in cols:
        conn.execute("ALTER TABLE pages ADD COLUMN blocks_json TEXT NOT NULL DEFAULT '[]'")
    if "source_sha" not in cols:
        # 2026-10-30：记住建页时的文件哈希，用于“文件被替换就重建该文档的页”
        conn.execute("ALTER TABLE pages ADD COLUMN source_sha TEXT NOT NULL DEFAULT ''")
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


def set_doc_alias(subject_id: str, alias: str, doc_id: str) -> None:
    """登记 资料稳定身份 → 页面索引 doc_id 的别名（如 m:xxx → f:文件名）。"""
    alias = str(alias or "").strip()
    doc_id = str(doc_id or "").strip()
    if not alias or not doc_id or alias == doc_id:
        return
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO doc_alias (subject_id, alias, doc_id) VALUES (?,?,?)",
            (str(subject_id), alias, doc_id))
        conn.commit()
    except Exception as e:
        logger.debug(f"登记页面索引别名失败: {e}")
    finally:
        conn.close()


def resolve_doc_id(subject_id: str, doc_id: str) -> str:
    """把别名（m:xxx）解析成页面索引里的实际 doc_id；解析不到就原样返回。"""
    doc_id = str(doc_id or "").strip()
    if not doc_id or doc_id.startswith("f:"):
        return doc_id
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT doc_id FROM doc_alias WHERE subject_id=? AND alias=?",
            (str(subject_id), doc_id)).fetchone()
        return str(row["doc_id"]) if row else doc_id
    finally:
        conn.close()


def clear_cancel_request(subject_id: str, doc_id: Optional[str] = None) -> None:
    """清除取消账上的记录（2026-10-31 回归修复）。

    为什么需要：删除/替换资料时会记取消账，防止"正在跑的旧任务把已删内容写回来"。
    但同一次导入的后续动作（替换成功后调度重建）**key 相同**，注册时读到旧账
    就被误杀——实测替换后 pages=0 而 doc_state 却显示已覆盖。
    导入方在调度**新版**建页前必须显式清账：新任务读的是新文件，不属于被拦对象。

    2026-10-31 二次修复：删除**最后一份**资料会走清库分支，记的是**学科级通配账**
    （`subject::*`）；随后新导入的任何文档注册都会命中它。所以按 doc_id 清账时
    必须连同通配账一起清——调度某个具体文档的新建页就是新生命周期的开始。
    """
    with _ACTIVE_LOCK:
        if doc_id:
            _CANCEL_REQUESTS.pop(_task_key(subject_id, str(doc_id)), None)
            _CANCEL_REQUESTS.pop(f"{subject_id}::*", None)
        else:
            prefix = f"{subject_id}::"
            for key in [k for k in _CANCEL_REQUESTS if k.startswith(prefix)]:
                _CANCEL_REQUESTS.pop(key, None)
            _CANCEL_REQUESTS.pop(f"{subject_id}::*", None)


def _page_blocks(page: Any, scale: float) -> List[Dict[str, Any]]:
    """取**文本层真实 block 坐标**；没有文本层时返回空列表。

    2026-10-30：不再用整页 bbox 兜底。缓存/OCR 文本没有坐标，
    拿整页框冒充会让前端画出一个"覆盖全页"的假高亮。
    宁可没有 bbox（前端不画框），也不伪造位置。
    """
    blocks: List[Dict[str, Any]] = []
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
        return []
    return blocks


def build_document_page_index(
    subject_id: str,
    source: Path,
    *,
    force: bool = False,
    page_from: int = 1,
    material_id: Optional[str] = None,
    on_page: Optional[Callable[[int, int, int], None]] = None,
    should_abort: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """为**单个文件**建页面索引（2026-10-30）。

    与旧实现的区别：
    - 有文本层就直接用文本层，**不 OCR**；
    - 无文本层时先查导入期缓存的页文本（`PageTextCache`），命中就直接用，
      只为页面图做一次渲染——这是"只 OCR 一遍"的关键；
    - 逐页断点：已建好且文件未变的页直接跳过，中断后重跑不从头再来；
    - 超过页数上限时如实返回 `total_pages / limit / uncovered_from`，不静默截断。
    """
    pdf_path = Path(source)
    file_name = pdf_path.name
    doc_id = _doc_id_for_file(file_name)
    if material_id:
        # 课程资料：把稳定身份（m:<material_id>）登记为本文件的别名
        set_doc_alias(subject_id, _doc_id_for_material(material_id), doc_id)

    # PPT 先转 PDF，页码/幻灯片号 1:1 对应（必须在打开文档之前判断，
    # 否则 fitz 会先把 .pptx 当 PDF 打开而直接失败）
    converted_pdf: Optional[Path] = None
    if pdf_path.suffix.lower() in (".pptx", ".ppt"):
        try:
            from services.office_converter import convert
            converted = convert(str(pdf_path), ".pdf")
            converted_pdf = Path(converted["converted_path"])
        except Exception as e:
            logger.warning(f"页面索引：PPT 转 PDF 失败 {file_name}: {e}")
            return {"ok": False, "file_name": file_name, "doc_id": doc_id,
                    "error": f"PPT 转 PDF 失败: {str(e)[:160]}",
                    "total_pages": 0, "indexed": 0}

    try:
        import fitz
        doc = fitz.open(str(converted_pdf or pdf_path))
    except Exception as e:
        logger.warning(f"页面索引：打开文档失败 {file_name}: {e}")
        return {"ok": False, "file_name": file_name, "doc_id": doc_id,
                "error": str(e)[:200], "total_pages": 0, "indexed": 0}

    total_pages = int(doc.page_count)
    limit = _MAX_PAGES_PER_DOC
    start = max(1, int(page_from or 1))
    # 首次构建按单文档上限分批（超限页通过"继续处理"补齐，不静默截断）；
    # 显式带 page_from 的调用是"继续处理"，一次处理到文档末尾。
    if start > 1:
        end = total_pages
    else:
        end = min(total_pages, limit)

    from PIL import Image
    from rag.loaders import pdf_ocr

    source_sha = pdf_ocr._file_sha(pdf_path)
    scale = _RENDER_DPI / 72.0
    image_dir = _images_root() / _subject_key(subject_id) / _doc_key(doc_id)
    os.makedirs(image_dir, exist_ok=True)

    conn = _conn()
    task_flag = _register_task(subject_id, doc_id)

    def _aborted() -> bool:
        """外部取消 / 删除资料取消 / 源文件已被删掉 都立即停止写入。"""
        if task_flag.is_set():
            return True
        if should_abort and should_abort():
            return True
        if not pdf_path.is_file():
            logger.info(f"页面索引：源文件已不存在，停止写入 {file_name}")
            return True
        return False

    indexed = 0
    skipped = 0
    reused_cache = 0
    ocr_pages = 0
    blocks_pages = 0
    failed_pages: List[int] = []
    try:
        existing: Dict[int, tuple] = {}
        for row in conn.execute(
                "SELECT page_no, image_path, source_sha FROM pages "
                "WHERE subject_id=? AND doc_id=?", (subject_id, doc_id)):
            existing[int(row["page_no"])] = (row["image_path"], row["source_sha"] or "")

        def _page_done(page_no: int) -> bool:
            if force:
                return False
            item = existing.get(page_no)
            if not item:
                return False
            image_path, sha = item
            if sha and sha != source_sha:
                return False
            try:
                path = Path(image_path)
                return path.is_file() and path.stat().st_size > 0
            except OSError:
                return False

        todo = [n for n in range(start, end + 1) if not _page_done(n)]
        skipped = (end - start + 1) - len(todo)

        # 第一遍：文本层优先；无文本层再看导入缓存；都没有才排进 OCR 队列
        page_text: Dict[int, str] = {}
        page_blocks: Dict[int, List[Dict[str, Any]]] = {}
        need_ocr: List[int] = []
        cache = pdf_ocr.PageTextCache(pdf_path) if todo else None
        if todo:
            try:
                probe = fitz.open(str(converted_pdf or pdf_path))
            except Exception:
                probe = doc
            try:
                for page_no in todo:
                    if _aborted():
                        break
                    page = probe.load_page(page_no - 1)
                    text = _page_text(page)
                    if text:
                        page_text[page_no] = text
                        blocks = _page_blocks(page, scale)
                        page_blocks[page_no] = blocks
                        if blocks:
                            blocks_pages += 1
                        continue
                    hit = cache.get(page_no) if cache else None
                    if hit and (hit.get("text") or "").strip():
                        page_text[page_no] = hit["text"]
                        page_blocks[page_no] = []      # 缓存无坐标，不伪造
                        reused_cache += 1
                        continue
                    need_ocr.append(page_no)
            finally:
                if probe is not doc:
                    try:
                        probe.close()
                    except Exception:
                        pass

        if need_ocr and not _aborted():
            ocr_map = pdf_ocr.ocr_pdf_pages(
                pdf_path, pages=need_ocr,
                should_abort=_aborted, engine=None)
            for page_no in need_ocr:
                page_text[page_no] = (ocr_map.get(page_no) or "")
                page_blocks[page_no] = []
                ocr_pages += 1

        # 第二遍：渲染页面图 + 落库（只做一次，不重复渲染）
        done = 0
        for page_no in todo:
            if _aborted():
                break
            try:
                page = doc.load_page(page_no - 1)
                pix = page.get_pixmap(dpi=_RENDER_DPI, colorspace=fitz.csRGB, alpha=False)
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                image_path = image_dir / f"p{page_no}.webp"
                image.save(str(image_path), "WEBP", quality=80, method=4)
                text = page_text.get(page_no, "")
                blocks = page_blocks.get(page_no) or []
                conn.execute(
                    """INSERT OR REPLACE INTO pages
                       (subject_id, doc_id, file_name, page_no, image_path,
                        width, height, text, blocks_json, created_at, source_sha)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (subject_id, doc_id, file_name, page_no, str(image_path),
                     pix.width, pix.height, _safe_text(text),
                     json.dumps(blocks, ensure_ascii=False),
                     datetime.now().isoformat(timespec="seconds"), source_sha),
                )
                indexed += 1
            except Exception as e:
                logger.warning(f"页面索引：第 {page_no} 页失败 {file_name}: {e}")
                failed_pages.append(page_no)
            done += 1
            _report_progress(subject_id, doc_id, file_name, done, len(todo), page_no)
            if on_page:
                try:
                    on_page(done, len(todo), page_no)
                except Exception:
                    pass
            if done % 20 == 0:
                conn.commit()
        conn.commit()
    except Exception as e:
        logger.warning(f"页面索引：处理 {file_name} 失败: {e}")
        conn.rollback()
        return {"ok": False, "file_name": file_name, "doc_id": doc_id,
                "error": str(e)[:200], "total_pages": total_pages,
                "indexed": indexed}
    finally:
        conn.close()
        _unregister_task(subject_id, doc_id)
        _clear_progress(subject_id, doc_id)
        try:
            doc.close()
        except Exception:
            pass

    covered_end = end
    # 被取消的任务不得把"未建出的页"报成已覆盖（2026-10-31）：
    # 实际覆盖 = 起始页之前 + 本次成功写入的页数。
    cancelled = bool(task_flag.is_set())
    if cancelled:
        covered_end = start - 1 + indexed
    # "截断"只指因**单文档上限**未覆盖；被取消造成的缺口不算截断。
    truncated = bool(not cancelled and total_pages > covered_end)
    try:
        state_conn = _conn()
        try:
            state_conn.execute(
                """INSERT OR REPLACE INTO doc_state
                   (subject_id, doc_id, file_name, total_pages, covered_to,
                    truncated, source_sha, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (subject_id, doc_id, file_name, total_pages, covered_end,
                 1 if truncated else 0, source_sha,
                 datetime.now().isoformat(timespec="seconds")))
            state_conn.commit()
        finally:
            state_conn.close()
    except Exception as e:
        logger.debug(f"写入页面索引状态失败: {e}")
    return {
        "ok": True,
        "file_name": file_name,
        "doc_id": doc_id,
        "total_pages": total_pages,
        "limit": limit,
        "page_from": start,
        "covered_to": covered_end,
        "indexed": indexed,
        "skipped": skipped,
        "reused_cache": reused_cache,
        "ocr_pages": ocr_pages,
        "blocks_pages": blocks_pages,
        "failed_pages": failed_pages,
        "cancelled": bool(_aborted()),
        "truncated": truncated,
        "uncovered_from": (covered_end + 1) if total_pages > covered_end else None,
        "uncovered_pages": max(0, total_pages - covered_end),
    }


def build_page_index(subject_id: str, force: bool = False) -> Dict[str, Any]:
    """为学科下的 PDF/PPT 建立/更新页面索引（逐份调用单文档入口）。"""
    subject_dir = get_subjects_dir() / str(subject_id)
    if not subject_dir.is_dir():
        return {"ok": True, "subject_id": subject_id, "documents": 0, "pages": 0,
                "note": "该学科没有资料目录"}
    files = sorted(p for p in subject_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in (".pdf", ".pptx", ".ppt"))
    if not files:
        return {"ok": True, "subject_id": subject_id, "documents": 0, "pages": 0,
                "note": "该学科没有 PDF/PPT（页面索引支持 PDF、PPTX、PPT）"}

    total_pages = 0
    skipped = 0
    reused = 0
    ocr_pages = 0
    truncated_docs: List[Dict[str, Any]] = []
    failed: List[Dict[str, str]] = []
    for source in files:
        result = build_document_page_index(subject_id, source, force=force)
        if not result.get("ok"):
            failed.append({"file_name": source.name,
                           "error": str(result.get("error") or "")[:200]})
            continue
        total_pages += int(result.get("indexed") or 0)
        skipped += int(result.get("skipped") or 0)
        reused += int(result.get("reused_cache") or 0)
        ocr_pages += int(result.get("ocr_pages") or 0)
        if result.get("truncated"):
            truncated_docs.append({
                "file_name": result["file_name"],
                "total_pages": result["total_pages"],
                "covered_to": result["covered_to"],
                "uncovered_from": result["uncovered_from"],
                "uncovered_pages": result["uncovered_pages"],
            })

    return {"ok": True, "subject_id": subject_id, "documents": len(files),
            "pages": total_pages,
            # 2026-10-30 语义变化：旧"页数未变整份跳过的文档数"已不存在，
            # 现在两个名字都指"页级断点跳过、无需重建的页数总和"。
            "skipped": skipped, "skipped_pages": skipped,
            "reused_cache_pages": reused, "ocr_pages": ocr_pages,
            "truncated": truncated_docs, "failed": failed}


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
        # 没有文本层 block 就没有坐标。整页框不是"高亮"，会让前端画一个大方框，
        # 所以这里返回 None，由前端决定"不高亮"（2026-10-30）。
        bbox = (best_block.get("bbox") if best_block else None) or None
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
        doc_id = resolve_doc_id(subject_id, doc_id)
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
        doc_id = resolve_doc_id(subject_id, doc_id)
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


def list_page_texts(subject_id: str, doc_id: str) -> List[Dict[str, Any]]:
    """按页取出已建好的页面文本（0 基页码），供章节大纲解析复用。

    2026-10-30：学习路线要"展示真实覆盖"，就得先知道这份资料到底有哪些章。
    页面索引里已经有逐页文本（含扫描件 OCR 结果），直接读它比重开一次
    几百 MB 的 PDF 便宜得多，也不会再触发一次 OCR。
    """
    conn = _conn()
    try:
        doc_id = str(doc_id or "").strip()
        # 允许直接传文件名：页面索引的文档身份是 `f:<文件名>`，
        # 调用方（章节大纲解析）手上通常只有资料清单里的文件名。
        if doc_id and ":" not in doc_id:
            doc_id = _doc_id_for_file(doc_id)
        doc_id = resolve_doc_id(subject_id, doc_id)
        rows = conn.execute(
            """SELECT page_no, file_name, text FROM pages
               WHERE subject_id=? AND doc_id=? ORDER BY page_no""",
            (subject_id, doc_id)).fetchall()
    finally:
        conn.close()
    return [{"page": int(row["page_no"]), "file_name": row["file_name"],
             "text": row["text"] or ""} for row in rows]


def get_page_index_status(subject_id: str) -> Dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS pages, COUNT(DISTINCT doc_id) AS documents,
                      MAX(created_at) AS updated_at
               FROM pages WHERE subject_id=?""", (subject_id,)).fetchone()
        docs = []
        for doc_row in conn.execute(
                """SELECT d.doc_id, d.file_name, d.total_pages, d.covered_to,
                          d.truncated, d.updated_at,
                          (SELECT COUNT(*) FROM pages p
                            WHERE p.subject_id=d.subject_id AND p.doc_id=d.doc_id)
                            AS indexed_pages
                   FROM doc_state d WHERE d.subject_id=?
                   ORDER BY d.file_name""", (subject_id,)):
            total = int(doc_row["total_pages"] or 0)
            covered = int(doc_row["covered_to"] or 0)
            docs.append({
                "doc_id": doc_row["doc_id"],
                "file_name": doc_row["file_name"],
                "total_pages": total,
                "indexed_pages": int(doc_row["indexed_pages"] or 0),
                "covered_to": covered,
                "truncated": bool(doc_row["truncated"]),
                "uncovered_from": (covered + 1) if total > covered else None,
                "uncovered_pages": max(0, total - covered),
                "updated_at": doc_row["updated_at"],
            })
    finally:
        conn.close()
    return {"subject_id": subject_id, "pages": row["pages"] or 0,
            "documents": row["documents"] or 0, "updated_at": row["updated_at"],
            "document_details": docs,
            "active": get_active_progress(subject_id)}


def get_document_status(subject_id: str, doc_id: str) -> Optional[Dict[str, Any]]:
    """单份资料的建页状态（前端"继续处理未覆盖页"要用）。"""
    for doc in (get_page_index_status(subject_id).get("document_details") or []):
        if doc["doc_id"] == doc_id:
            return doc
    return None


def delete_subject_pages(subject_id: str) -> int:
    """删除某学科的页面索引、别名与页面图（学科删除时调用）。"""
    cancel_document_tasks(subject_id)
    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM pages WHERE subject_id=?", (subject_id,))
        conn.execute("DELETE FROM doc_alias WHERE subject_id=?", (subject_id,))
        conn.execute("DELETE FROM doc_state WHERE subject_id=?", (subject_id,))
        conn.commit()
        removed = cur.rowcount
    finally:
        conn.close()
    _remove_images(_images_root() / _subject_key(subject_id))
    return removed


def delete_document_pages(subject_id: str, doc_id: Optional[str] = None,
                          file_name: Optional[str] = None) -> Dict[str, Any]:
    """删除**单份资料**的页面索引与页面图，并让搜索结果立即失效。

    删除/替换资料时由 knowledge_service 调用。会同时取消该文档正在跑的
    建页任务，避免"删完又被旧任务写回来"。
    """
    target = str(doc_id or "").strip() or (
        _doc_id_for_file(file_name) if file_name else "")
    if not target:
        return {"ok": False, "deleted": 0}
    cancel_document_tasks(subject_id, target)
    target = resolve_doc_id(subject_id, target)
    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM pages WHERE subject_id=? AND doc_id=?",
                           (subject_id, target))
        conn.execute("DELETE FROM doc_alias WHERE subject_id=? AND doc_id=?",
                     (subject_id, target))
        conn.execute("DELETE FROM doc_state WHERE subject_id=? AND doc_id=?",
                     (subject_id, target))
        conn.commit()
        removed = cur.rowcount
    finally:
        conn.close()
    _remove_images(_images_root() / _subject_key(subject_id) / _doc_key(target))
    return {"ok": True, "doc_id": target, "deleted": removed}


def _remove_images(directory: Path) -> None:
    """删页面图目录；失败只记日志，不影响索引已经失效这件事。"""
    try:
        directory = Path(directory)
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
    except Exception as e:
        logger.debug(f"清理页面图目录失败 {directory}: {e}")


# ---- 建页任务的取消登记（防止删除后旧任务把内容写回来） ----
# _CANCEL_REQUESTS 的存在意义：**删除资料可能早于建页任务注册**。
# 只给"正在跑"的任务发取消信号会漏掉"即将启动"的，所以取消请求先落账，
# 任务注册时发现账上有自己 → 直接置中止位，一页都不写。
_ACTIVE_LOCK = threading.Lock()
_ACTIVE_TASKS: Dict[str, threading.Event] = {}
_CANCEL_REQUESTS: Dict[str, str] = {}


def _task_key(subject_id: str, doc_id: str) -> str:
    return f"{subject_id}::{doc_id}"


def cancel_document_tasks(subject_id: str, doc_id: Optional[str] = None) -> int:
    """取消该学科（或指定文档）正在运行/即将运行的建页任务。"""
    prefix = f"{subject_id}::"
    with _ACTIVE_LOCK:
        keys = [k for k in _ACTIVE_TASKS
                if k.startswith(prefix) and (not doc_id or k.endswith(str(doc_id)))]
        for key in keys:
            _ACTIVE_TASKS[key].set()
        # 对"还没注册"的目标记录取消请求；带 doc_id 的精确记录，
        # 学科级取消则覆盖该学科所有尚未注册的 key（记录学科前缀）
        if doc_id:
            _CANCEL_REQUESTS[_task_key(subject_id, str(doc_id))] = _now_str()
        else:
            for key in list(_CANCEL_REQUESTS):
                if key.startswith(prefix):
                    _CANCEL_REQUESTS[key] = _now_str()
            _CANCEL_REQUESTS[prefix.rstrip(":") + "::*"] = _now_str()
        return len(keys)


def _now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _register_task(subject_id: str, doc_id: str) -> threading.Event:
    flag = threading.Event()
    key = _task_key(subject_id, doc_id)
    prefix = f"{subject_id}::"
    with _ACTIVE_LOCK:
        requested = (key in _CANCEL_REQUESTS
                      or any(k == prefix.rstrip(":") + "::*"
                             for k in _CANCEL_REQUESTS))
        if requested:
            flag.set()
        _ACTIVE_TASKS[key] = flag
    return flag


def _unregister_task(subject_id: str, doc_id: str) -> None:
    key = _task_key(subject_id, doc_id)
    with _ACTIVE_LOCK:
        _ACTIVE_TASKS.pop(key, None)
        # 本次任务结束，清掉它自己的历史取消请求；
        # 学科级通配请求只有在没有活跃任务时才清（否则会放行并行任务）
        _CANCEL_REQUESTS.pop(key, None)
        if not any(k.startswith(f"{subject_id}::") for k in _ACTIVE_TASKS):
            for k in list(_CANCEL_REQUESTS):
                if k.startswith(f"{subject_id}"):
                    _CANCEL_REQUESTS.pop(k, None)


def active_page_index_tasks(subject_id: Optional[str] = None) -> int:
    with _ACTIVE_LOCK:
        if not subject_id:
            return len(_ACTIVE_TASKS)
        return sum(1 for k in _ACTIVE_TASKS if k.startswith(f"{subject_id}::"))


# 进行中任务的页级进度（内存态，进程内有效；已完成部分永远以 pages 表为准）
_ACTIVE_PROGRESS: Dict[str, Dict[str, Any]] = {}


def _report_progress(subject_id: str, doc_id: str, file_name: str,
                     done: int, total: int, page_no: int) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PROGRESS[_task_key(subject_id, doc_id)] = {
            "file_name": file_name, "doc_id": doc_id,
            "done": int(done), "total": int(total), "page": int(page_no),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }


def get_active_progress(subject_id: str) -> List[Dict[str, Any]]:
    with _ACTIVE_LOCK:
        prefix = f"{subject_id}::"
        return [dict(v) for k, v in _ACTIVE_PROGRESS.items() if k.startswith(prefix)]


def _clear_progress(subject_id: str, doc_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PROGRESS.pop(_task_key(subject_id, doc_id), None)


__all__ = [
    "build_page_index", "build_document_page_index", "search_pages", "get_page_image",
    "get_page_index_status", "list_document_pages", "list_page_texts",
    "delete_subject_pages", "delete_document_pages", "cancel_document_tasks",
    "clear_cancel_request", "active_page_index_tasks", "page_image_url", "images_root",
    "set_doc_alias", "resolve_doc_id",
]
