"""章节映射与页码展示（2026-10-28）。

借鉴 DeepTutor 的 heading 栈思路：让每个 chunk 带上「它在全书中的位置」，
而不是只有孤立的页码。三个来源按可信度合并：
1. PDF 书签（TOC，fitz.get_toc）—— 最可信；
2. 字号启发式（正文 PDF 的大号/加粗短行）；
3. OCR 文本正则（扫描件的「第X章 / 1.2 / Chapter N」）。

`display_page()` 是**唯一**的 0 基→1 基转换点；索引里的 `page` 仍保持 0 基，
避免改动既有 metadata 契约与旧索引。
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from utils.logger import logger

_HEADING_RE = re.compile(
    # 中文“第X章/节”后面常常没有空格（“第一章数据库系统概述”），不能用 \b；
    # 用“后面不是数字/点”的前瞻，既放过中文，又避免把 “2026 年” 当标题。
    r"^\s*(第[一二三四五六七八九十百千零〇两\d]+[章节篇部]|chapter\s+\d+|\d{1,2}(?:\.\d{1,2}){0,3})(?=\s|$|[^\d.])",
    re.IGNORECASE,
)
_PAGE_RANGE_RE = re.compile(r"^\s*(\d+)\s*(?:[-–~]\s*(\d+))?\s*$")
# 编号类标题必须“编号 + 空格 + 正文”，例如 “3.1 关系模型”。
# 2026-10-28：表格里的 “95”、“2021001 张三” 这类纯数字行必须排除，
# 否则会被当成标题并一路污染后续页（实测 OCR 成绩表把 95 当成了章节名）。
_NUM_HEADING_RE = re.compile(
    r"^(\d{1,2}(?:\.\d{1,2}){0,3})(?=[\s　]+[一-鿿A-Za-z])"
)


@dataclass(frozen=True)
class Heading:
    page: int          # 0 基
    level: int         # 1 起
    title: str
    source: str = "toc"


def toc_headings(pdf_path: Path) -> List[Heading]:
    """读取 PDF 书签；没有书签或读取失败返回空列表。"""
    try:
        import fitz

        doc = fitz.open(str(pdf_path))
        try:
            toc = doc.get_toc(simple=True) or []
        finally:
            doc.close()
    except Exception as e:
        logger.debug(f"读取 PDF 书签失败（忽略）: {e}")
        return []

    out: List[Heading] = []
    for item in toc:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        try:
            level = max(1, int(item[0]))
            title = str(item[1] or "").strip()
            page1 = int(item[2])
        except (TypeError, ValueError):
            continue
        if not title:
            continue
        out.append(Heading(page=max(0, page1 - 1), level=level, title=title, source="toc"))
    return out


def detect_text_heading(text: str) -> Optional[Tuple[int, str]]:
    """从一行文本判断是否为章节标题，返回 (level, title)。"""
    line = (text or "").strip()
    if not line or len(line) > 48:
        return None
    match = _HEADING_RE.match(line)
    if not match:
        return None
    token = match.group(1)
    lowered = token.lower()
    if token.startswith("第") or lowered.startswith("chapter"):
        level = 1
    else:
        # 编号类必须后接正文才认是标题
        if not _NUM_HEADING_RE.match(line):
            return None
        level = token.count(".") + 1
    return max(1, min(4, level)), line


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return float(ordered[mid - 1] + ordered[mid]) / 2.0


def collect_from_elements(elements: Sequence[Any]) -> List[Heading]:
    """从 PDFElement 列表提取标题候选（字号 + 正则）。"""
    per_page_fonts: Dict[int, List[float]] = {}
    for element in elements:
        meta = getattr(element, "metadata", None) or {}
        try:
            font = float(meta.get("font_size") or 0)
        except (TypeError, ValueError):
            font = 0.0
        if font <= 0:
            continue
        try:
            page = int(getattr(element, "page", 0) or 0)
        except (TypeError, ValueError):
            page = 0
        per_page_fonts.setdefault(page, []).append(font)

    medians = {page: _median(fonts) for page, fonts in per_page_fonts.items()}
    out: List[Heading] = []
    seen = set()
    for element in elements:
        content = getattr(element, "content", None)
        text = str(content or "").strip()
        if not text:
            continue
        try:
            page = int(getattr(element, "page", 0) or 0)
        except (TypeError, ValueError):
            page = 0
        meta = getattr(element, "metadata", None) or {}
        try:
            font = float(meta.get("font_size") or 0)
        except (TypeError, ValueError):
            font = 0.0
        bold = bool(meta.get("is_bold"))

        regex_hit = detect_text_heading(text)
        if regex_hit:
            level, title = regex_hit
            key = (page, title)
            if key not in seen:
                seen.add(key)
                out.append(Heading(page=page, level=level, title=title, source="text"))
            continue

        median = medians.get(page, 0.0)
        if font <= 0 or median <= 0:
            continue
        if len(text) > 40 or text.endswith(("。", "；", "，", ".", ";", ",")):
            continue
        if font >= median * 1.35:
            level = 1
        elif font >= median * 1.15 and (bold or len(text) <= 24):
            level = 2
        else:
            continue
        key = (page, text)
        if key not in seen:
            seen.add(key)
            out.append(Heading(page=page, level=level, title=text, source="text"))
    return out


def headings_from_pages(pages: Dict[int, str]) -> List[Heading]:
    """扫描件：从 OCR 页文本中提取标题（每页最多一个）。"""
    out: List[Heading] = []
    for page_no, text in (pages or {}).items():
        for line in str(text or "").splitlines():
            hit = detect_text_heading(line)
            if hit:
                level, title = hit
                out.append(Heading(page=max(0, int(page_no) - 1), level=level,
                                   title=title, source="ocr"))
                break
    return out


def merge_headings(*groups: Iterable[Heading]) -> List[Heading]:
    """合并多来源标题：TOC 优先；没有 TOC 时用字号/OCR 候选。"""
    toc: List[Heading] = []
    others: List[Heading] = []
    for group in groups:
        for heading in group or []:
            if heading.source == "toc":
                toc.append(heading)
            else:
                others.append(heading)
    if toc:
        return sorted(toc, key=lambda h: (h.page, h.level))
    # 去重（同页同标题只留一个），按页码排序
    seen = set()
    unique: List[Heading] = []
    for heading in sorted(others, key=lambda h: (h.page, h.level)):
        key = (heading.page, heading.title)
        if key in seen:
            continue
        seen.add(key)
        unique.append(heading)
    return unique


def annotate(docs: Sequence[Any], headings: Sequence[Heading]) -> None:
    """给文档补 `section / heading / heading_level / parent_headings`。

    - 已有 `section`（来自 Markdown 标题）不覆盖，只补层级路径；
    - `page` 保持 0 基；
    - 文档顺序不被改变。
    """
    if not docs or not headings:
        return
    entries: List[Tuple[Heading, List[str]]] = []
    stack: List[str] = []
    for heading in sorted(headings, key=lambda h: (h.page, h.level)):
        stack = stack[: max(0, heading.level - 1)]
        stack.append(heading.title)
        entries.append((heading, list(stack)))
    if not entries:
        return
    pages = [h.page for h, _ in entries]

    for doc in docs:
        meta = getattr(doc, "metadata", None)
        if meta is None:
            meta = {}
            try:
                doc.metadata = meta
            except Exception:
                continue
        try:
            page = int(meta.get("page") or 0)
        except (TypeError, ValueError):
            page = 0
        index = bisect_right(pages, page) - 1
        if index < 0:
            continue
        heading, path = entries[index]
        meta.setdefault("heading", heading.title)
        meta.setdefault("heading_level", heading.level)
        if len(path) > 1:
            meta.setdefault("parent_headings", " > ".join(path[:-1]))
        if not meta.get("section"):
            meta["section"] = heading.title


def display_page(meta: Optional[Dict[str, Any]]) -> Any:
    """把 0 基页码转成 1 基展示值；范围字符串逐段 +1；非法值原样返回。

    只有显式标记了 `page_base=0` 的新 PDF 文档才 +1：
    - 旧索引/普通文本没有该标记，页码可能已经是 1 基，不能盲加；
    - `page_base=1` 原样返回。
    """
    if not isinstance(meta, dict):
        return None
    page = meta.get("page")
    base = meta.get("page_base")
    if base is None or int(base) != 0:
        return page
    if isinstance(page, bool):
        return page
    if isinstance(page, int):
        return page + 1
    if isinstance(page, float):
        return int(page) + 1
    if isinstance(page, str):
        match = _PAGE_RANGE_RE.match(page)
        if not match:
            return page
        start = int(match.group(1)) + 1
        end = match.group(2)
        return f"{start}-{int(end) + 1}" if end else start
    return page


__all__ = [
    "Heading",
    "toc_headings",
    "detect_text_heading",
    "collect_from_elements",
    "headings_from_pages",
    "merge_headings",
    "annotate",
    "display_page",
]
