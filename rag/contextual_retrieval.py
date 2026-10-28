"""Contextual Retrieval（2026-10-24）

在入库前给每个 chunk 加上「它在全文中的位置/主题」上下文，再送 embedding 与 BM25。
参考 Anthropic Contextual Retrieval 的思路，按本项目体量做了两点裁剪：

1. 默认用**确定性前缀**（资料名/章节/页码），零成本、可重复；
2. 设置 CONTEXTUAL_LLM=1 时，按 (资料, 章节) 分组用模型生成一句上下文，
   并对结果做磁盘缓存；同一章节只调用一次，不按 chunk 逐条烧钱。

注意：chunk 的 page_content 会带前缀，但 metadata 保留 original_content；
引用展示仍取 original_content（由上层按需处理）。
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.path_config import get_data_dir
from utils.logger import logger

CONTEXTUAL_VERSION = 1
_CACHE_DIR = lambda: Path(get_data_dir()) / "cache" / "contextual"


def _env_on(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on", "auto")


def llm_enabled() -> bool:
    return _env_on("CONTEXTUAL_LLM", "0")


def _doc_meta(doc: Any) -> Dict[str, Any]:
    return dict(getattr(doc, "metadata", None) or {})


def _content(doc: Any) -> str:
    return str(getattr(doc, "page_content", "") or "")


def _source_name(meta: Dict[str, Any]) -> str:
    return str(meta.get("file_name") or meta.get("source") or "").replace("\\", "/").split("/")[-1]


def deterministic_prefix(meta: Dict[str, Any]) -> str:
    parts: List[str] = []
    source = _source_name(meta)
    if source:
        parts.append(f"资料《{source}》")
    section = str(meta.get("section") or "").strip()
    if section:
        parts.append(f"章节：{section}")
    page = meta.get("page")
    if page not in (None, ""):
        parts.append(f"第 {page} 页")
    if not parts:
        return ""
    return "【上下文】" + "；".join(parts) + "。\n"


def _cache_path(key: str) -> Path:
    return _CACHE_DIR() / (key + ".txt")


def _cached_context(key: str) -> Optional[str]:
    path = _cache_path(key)
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return None


def _write_cache(key: str, text: str) -> None:
    try:
        path = _cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        logger.debug(f"上下文缓存写入失败: {e}")


def _llm_context(group_key: str, sample: str) -> str:
    key = hashlib.sha1(f"v{CONTEXTUAL_VERSION}|{group_key}".encode("utf-8")).hexdigest()[:24]
    cached = _cached_context(key)
    if cached is not None:
        return cached
    try:
        from services.ai_service import call_llm
        prompt = (
            "你在为课程资料做检索增强。请用一句中文说明下面这段内容在全文中的位置与主题，"
            "不超过 50 字，不要复述原文，不要编造。\n\n"
            + sample[:1200]
        )
        text = call_llm(prompt, system_message="你只输出一句上下文说明。").strip()
        text = re.sub(r"\s+", " ", text)[:80]
        if text:
            _write_cache(key, text)
            return text
    except Exception as e:
        logger.info(f"Contextual LLM 生成失败，回退确定性前缀: {type(e).__name__}: {e}")
    return ""


def _make_doc(content: str, metadata: Dict[str, Any]) -> Any:
    try:
        from langchain_core.documents import Document
        return Document(page_content=content, metadata=metadata)
    except ImportError:
        from rag.text_splitter import SimpleDocument
        return SimpleDocument(content, metadata)


def enrich_documents(documents: List[Any], subject_id: str = "") -> Tuple[List[Any], Dict[str, Any]]:
    """给每个 chunk 加上下文前缀；返回 (新文档, 统计)。"""
    if not documents:
        return [], {"enabled": False, "prefix_count": 0, "llm_groups": 0}
    meta_by_group: Dict[str, str] = {}
    if llm_enabled():
        for doc in documents:
            meta = _doc_meta(doc)
            source = _source_name(meta)
            section = str(meta.get("section") or "").strip()
            if not source and not section:
                continue
            key = source + "|" + section
            if key not in meta_by_group:
                meta_by_group[key] = _content(doc)[:1200]
        for key, sample in list(meta_by_group.items())[:50]:
            text = _llm_context(f"{subject_id}|{key}", sample)
            if text:
                meta_by_group[key] = text
    enriched: List[Any] = []
    prefix_count = 0
    for doc in documents:
        meta = _doc_meta(doc)
        content = _content(doc)
        if content.startswith("【上下文】"):
            enriched.append(doc)
            continue
        source = _source_name(meta)
        section = str(meta.get("section") or "").strip()
        group_key = source + "|" + section
        llm_text = ""
        if llm_enabled() and group_key in meta_by_group:
            candidate = meta_by_group[group_key]
            # 只有真正由模型生成/命中的短文本才当上下文；原始 sample 不等于上下文
            if candidate != _content(doc)[:1200] and len(candidate) <= 100:
                llm_text = candidate
        prefix = deterministic_prefix(meta)
        if llm_text:
            prefix = f"【上下文】{llm_text}。\n"
        if not prefix:
            enriched.append(doc)
            continue
        new_meta = dict(meta)
        new_meta["context_prefix"] = prefix
        new_meta["original_content"] = content
        new_meta["contextual_version"] = CONTEXTUAL_VERSION
        enriched.append(_make_doc(prefix + content, new_meta))
        prefix_count += 1
    return enriched, {"enabled": True, "prefix_count": prefix_count,
                      "llm_groups": len(meta_by_group) if llm_enabled() else 0}


__all__ = ["CONTEXTUAL_VERSION", "enrich_documents", "deterministic_prefix", "llm_enabled"]
