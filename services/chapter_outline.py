"""资料的**真实章节大纲**（学习路线与知识图谱共用的"覆盖依据"）。

为什么需要它（2026-10-30 实测根因）：
`knowledge_map.collect_evidence` 对 PDF 只做一次相似度检索，一本 252 页的教材
最终只有 3 段 × 1400 字进模型。模型看不见第 2/3/4/5/7/8/10…18 章，
生成的路线只剩第 1/6/9/13/14 章，而 `uncovered` 还是空的——**13 章被静默丢掉**。
路线要"展示真实覆盖"，就得先从原文拿到权威章节清单，再要求模型逐章交代。

取数顺序（都不额外调用大模型）：
1. 页面索引里已有的逐页文本（扫描件也覆盖，且不重开几百 MB 的 PDF、不再 OCR 一次）；
2. PDF 书签（TOC）；
3. 都没有 → 如实返回 `source="none"`，调用方不得据此声称"覆盖了全部章节"。
"""

from __future__ import annotations

import re
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils.logger import logger

# 严格的"第 N 章 标题"识别。
# 只用 `pdf_map.detect_text_heading` 会把正文里的"第1章中已经介绍过…"也当成标题
# （实测一份教材扫出 120 个假章节），所以这里额外要求整行就是标题、且尾巴干净。
_CHAPTER_RE = re.compile(r"^第\s*(\d{1,2})\s*章(?!\d)\s*(.{0,26})$")
# 标题尾巴里出现这些就不是章节标题，而是句子/页眉碎片
_TITLE_REJECTS = ("。", "，", "、", "；", ":", "：", "中已", "和第", "本章", "本节",
                  "习题", "小结", "…", ".")
# 只看每页开头几行：章节标题（或页眉）一定在页面顶部，正文段落里出现的"第N章"要排除
_SCAN_LINES = 6
_MAX_TITLE_LEN = 26

_CACHE: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()


def _clean_title(text: str) -> str:
    title = re.sub(r"\s+", " ", str(text or "")).strip()
    return title[:_MAX_TITLE_LEN]


def _title_of(lines: List[str], index: int, match: "re.Match[str]") -> str:
    """章节标题可能与"第N章"同行，也可能在下一行。"""
    tail = _clean_title(match.group(2))
    if tail:
        return tail
    nxt = _clean_title(lines[index + 1]) if index + 1 < len(lines) else ""
    if not nxt or any(bad in nxt for bad in _TITLE_REJECTS):
        return ""
    return nxt


def chapter_hits(text: str) -> Iterable[Tuple[int, str]]:
    """从一页文本里找出章节标题，产出 (章号, 标题)。标题拿不到就不产出。"""
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()][:_SCAN_LINES]
    for index, line in enumerate(lines):
        match = _CHAPTER_RE.match(line)
        if not match:
            continue
        title = _title_of(lines, index, match)
        if not title or any(bad in title for bad in _TITLE_REJECTS):
            continue
        try:
            yield int(match.group(1)), title
        except (TypeError, ValueError):
            continue


def outline_from_pages(pages: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """逐页文本 → 章节大纲。

    章标题常常是**每页页眉**（实测 84 页里"第3章 关系数据库标准语言SQL"出现 29 次），
    所以按章号聚合：页码取第一次出现的那页，标题取出现次数最多的写法
    （OCR 少字时会有"第1章 论"/"第1章 绪论"两种变体）。
    """
    grouped: Dict[int, Dict[str, Any]] = {}
    for page in pages or []:
        try:
            page_no = int(page.get("page"))
        except (TypeError, ValueError):
            continue
        for number, title in chapter_hits(page.get("text") or ""):
            entry = grouped.setdefault(
                number, {"number": number, "title": title, "page": page_no, "votes": {}})
            votes = entry["votes"]
            votes[title] = votes.get(title, 0) + 1
            if page_no < entry["page"]:
                entry["page"] = page_no
    chapters: List[Dict[str, Any]] = []
    for number in sorted(grouped):
        entry = grouped[number]
        best = sorted(entry["votes"].items(), key=lambda kv: (-kv[1], -len(kv[0])))[0][0]
        chapters.append({"number": number, "title": best, "page": entry["page"]})
    return chapters


def _toc_chapters(subject_id: str, file_name: str) -> List[Dict[str, Any]]:
    """退路：读 PDF 书签里的一级"第N章"条目（拿不到正文，只有标题和页码）。"""
    try:
        from rag.loaders import pdf_map
        from services.knowledge_service import get_subject_document_path

        path = get_subject_document_path(subject_id, file_name)
        if not path or not str(path).lower().endswith(".pdf"):
            return []
        out: List[Dict[str, Any]] = []
        seen = set()
        for heading in pdf_map.toc_headings(path):
            if heading.level != 1:
                continue
            match = _CHAPTER_RE.match(_clean_title(heading.title))
            if not match:
                continue
            number = int(match.group(1))
            if number in seen:
                continue
            seen.add(number)
            out.append({"number": number, "title": _clean_title(match.group(2)) or heading.title,
                        "page": max(0, int(heading.page))})
        return sorted(out, key=lambda c: c["number"])
    except Exception as e:
        logger.debug(f"读取 PDF 书签失败（章节大纲退路）{file_name}: {e}")
        return []


def document_outline(subject_id: str, file_name: str) -> Dict[str, Any]:
    """一份资料的章节大纲。

    Returns:
        {"file_name", "chapters": [{number,title,page}], "source": page_index|pdf_toc|none,
         "pages": 已解析到的页数, "note": 拿不到大纲时的原因}
    """
    file_name = str(file_name or "")
    if not file_name:
        return {"file_name": "", "chapters": [], "source": "none", "pages": 0,
                "note": "缺少文件名"}
    try:
        from services import page_index
        pages = page_index.list_page_texts(subject_id, file_name)
    except Exception as e:
        logger.debug(f"读取页面文本失败 {file_name}: {e}")
        pages = []

    cache_key = (str(subject_id), file_name, len(pages))
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    chapters = outline_from_pages(pages)
    source = "page_index" if chapters else "none"
    note = ""
    if not chapters:
        chapters = _toc_chapters(subject_id, file_name)
        source = "pdf_toc" if chapters else "none"
    if not chapters:
        note = ("已建页索引但未识别出「第N章」式章节标题" if pages
                else "该资料还没建页索引，也没有 PDF 书签，无法解析章节清单")
    result = {"file_name": file_name, "chapters": chapters, "source": source,
              "pages": len(pages), "note": note}
    if pages:
        # 页数变化说明建页在推进，缓存随之失效；没建页时不缓存（避免把"暂时读不到"钉死）
        with _CACHE_LOCK:
            _CACHE[cache_key] = result
    return result


def subject_outlines(subject_id: str, file_names: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """批量取大纲，返回 {文件名: outline}（只对真有章节的资料保留条目）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for name in file_names or []:
        outline = document_outline(subject_id, str(name))
        if outline.get("chapters"):
            out[str(name)] = outline
    return out


def chapter_ranges(chapters: List[Dict[str, Any]], total_pages: int) -> Dict[int, Tuple[int, int]]:
    """章号 → (起始页, 结束页)，均为 0 基；结束页取下一章起始页（末章到文档末尾）。"""
    ordered = sorted(chapters or [], key=lambda c: int(c.get("page") or 0))
    ranges: Dict[int, Tuple[int, int]] = {}
    for index, chapter in enumerate(ordered):
        start = int(chapter.get("page") or 0)
        end = int(ordered[index + 1].get("page") or 0) - 1 if index + 1 < len(ordered) else max(
            start, int(total_pages or 0) - 1)
        ranges[int(chapter["number"])] = (start, max(start, end))
    return ranges


def chapter_excerpts(subject_id: str, file_name: str, outline: Dict[str, Any],
                     per_chapter_chars: int = 700,
                     max_chapters: int = 40) -> List[Dict[str, Any]]:
    """每章开头一段真实正文，作为路线生成的证据（不额外调模型、不做检索）。

    只在建过页索引时可用；没有页面文本就返回空列表，调用方退回原有检索证据。
    """
    chapters = (outline or {}).get("chapters") or []
    if not chapters:
        return []
    try:
        from services import page_index
        pages = page_index.list_page_texts(subject_id, file_name)
    except Exception as e:
        logger.debug(f"读取页面文本失败（章节证据）{file_name}: {e}")
        return []
    if not pages:
        return []
    by_page = {int(p.get("page")): (p.get("text") or "") for p in pages}
    ranges = chapter_ranges(chapters, len(pages))
    out: List[Dict[str, Any]] = []
    for chapter in sorted(chapters, key=lambda c: c["number"])[:max_chapters]:
        number = int(chapter["number"])
        start, end = ranges.get(number, (int(chapter.get("page") or 0),) * 2)
        body = ""
        for page_no in range(start, min(end, start + 2) + 1):
            body += (by_page.get(page_no) or "") + "\n"
            if len(body) >= per_chapter_chars:
                break
        body = re.sub(r"\s+", " ", body).strip()
        if not body:
            continue
        out.append({"doc_id": "f:" + file_name, "file_name": file_name, "kind": "chapter",
                    "chapter": f"第{number}章 {chapter.get('title') or ''}".strip(),
                    "page": start, "chunk_id": None,
                    "snippet": body[:per_chapter_chars]})
    return out


def merged_chapters(outlines: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把多份资料（同一本书拆成 part1/2/3）的章节合并成一条清单。

    同一章在多份资料里都出现时（实测第4章在 part1、part2 都有），合并 source_docs，
    页码取最早那份资料的页；标题取出现次数最多的写法。
    """
    grouped: Dict[int, Dict[str, Any]] = {}
    for file_name, outline in (outlines or {}).items():
        for chapter in outline.get("chapters") or []:
            number = int(chapter["number"])
            entry = grouped.setdefault(number, {
                "number": number, "title": chapter.get("title") or f"第{number}章",
                "page": chapter.get("page"), "source_docs": [], "titles": {}})
            entry["source_docs"].append(file_name)
            title = chapter.get("title") or ""
            if title:
                entry["titles"][title] = entry["titles"].get(title, 0) + 1
            if entry["page"] is None or (chapter.get("page") or 0) < entry["page"]:
                entry["page"] = chapter.get("page")
    chapters: List[Dict[str, Any]] = []
    for number in sorted(grouped):
        entry = grouped[number]
        if entry["titles"]:
            entry["title"] = sorted(entry["titles"].items(),
                                    key=lambda kv: (-kv[1], -len(kv[0])))[0][0]
        chapters.append({"number": number, "title": entry["title"], "page": entry["page"],
                         "source_docs": sorted(set(entry["source_docs"]))})
    return chapters


def chapter_label(number: Any, title: str = "") -> str:
    """章节节点的标准 label：与原文标题一致，保证 stable_node_id 跨次生成不变。"""
    try:
        num = int(number)
    except (TypeError, ValueError):
        return _clean_title(title)
    title = _clean_title(title)
    return f"第{num}章 {title}".strip() if title else f"第{num}章"


def chapter_number_of(label: str) -> Optional[int]:
    """从一个节点 label 里读出章号（"第3章 关系数据库标准语言SQL" → 3）。"""
    match = re.match(r"^第\s*(\d{1,2})\s*章", str(label or "").strip())
    return int(match.group(1)) if match else None


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


__all__ = [
    "chapter_hits", "outline_from_pages", "document_outline", "subject_outlines",
    "chapter_ranges", "chapter_excerpts", "merged_chapters", "chapter_label",
    "chapter_number_of", "clear_cache",
]
