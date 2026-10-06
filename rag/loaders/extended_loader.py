"""
ExtendedLoader - 可选扩展格式加载器（html/htm、rtf、odt/odp、epub，2026-10-07）

这些是"有可靠解析器才加"的可选格式；缺少对应库时 registry 不会注册它们，
本加载器也会返回明确的 unsupported_format。

每个文件一个 Document（epub 按章节切分），metadata 含 file_name / file_type /
modality="text"。正文只取可见文本，不把标签或二进制塞进索引。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Union

from .base import BaseLoader, LoadResult

logger = logging.getLogger(__name__)

_EXTS = (".html", ".htm", ".rtf", ".odt", ".odp", ".epub")


def _html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "head"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


class ExtendedLoader(BaseLoader):
    """html/htm/rtf/odt/odp/epub 加载器"""

    def supports(self, file_path: Union[str, Path]) -> bool:
        return Path(file_path).suffix.lower() in _EXTS

    def get_supported_extensions(self) -> List[str]:
        return list(_EXTS)

    def load(self, file_path: Union[str, Path]) -> LoadResult:
        try:
            file_path = self._validate_file(file_path)
        except Exception as e:
            return self._create_error_result(str(e), error_type="io_error")

        ext = file_path.suffix.lower()
        try:
            if ext in (".html", ".htm"):
                return self._text_result(file_path, self._extract_html(file_path), ext)
            if ext == ".rtf":
                return self._text_result(file_path, self._extract_rtf(file_path), ext)
            if ext in (".odt", ".odp"):
                return self._text_result(file_path, self._extract_odf(file_path), ext)
            if ext == ".epub":
                return self._epub_result(file_path)
        except ImportError as e:
            return self._create_error_result(
                f"解析 {ext} 缺少依赖库: {e}", error_type="unsupported_format")
        except Exception as e:
            return self._create_error_result(f"读取 {ext} 文件失败: {e}",
                                             error_type="parse_error")

        return self._create_error_result(f"不支持的格式: {ext}",
                                         error_type="unsupported_format")

    # ---------- 各格式 ----------

    def _extract_html(self, file_path: Path) -> str:
        raw = file_path.read_bytes()
        html = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
            try:
                html = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if html is None:  # pragma: no cover
            html = raw.decode("utf-8", errors="replace")
        return _html_to_text(html)

    def _extract_rtf(self, file_path: Path) -> str:
        from striprtf.striprtf import rtf_to_text
        raw = file_path.read_bytes()
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError:  # pragma: no cover
            text = raw.decode("utf-8", errors="replace")
        return rtf_to_text(text)

    def _extract_odf(self, file_path: Path) -> str:
        from odf import teletype
        from odf.opendocument import load as odf_load
        doc = odf_load(str(file_path))
        parts = []
        for attr in ("text", "presentation"):
            node = getattr(doc, attr, None)
            if node is not None:
                parts.append(teletype.extractText(node))
        return "\n".join(p for p in parts if p and p.strip())

    def _epub_result(self, file_path: Path) -> LoadResult:
        from ebooklib import ITEM_DOCUMENT, epub

        book = epub.read_epub(str(file_path))
        try:
            from langchain_core.documents import Document
        except ImportError:  # pragma: no cover
            from rag.text_splitter import SimpleDocument as Document  # type: ignore

        documents = []
        for idx, item in enumerate(book.get_items_of_type(ITEM_DOCUMENT), start=1):
            try:
                html = item.get_content().decode("utf-8", errors="replace")
            except Exception:
                continue
            body = _html_to_text(html)
            if not body:
                continue
            title = ""
            try:
                title = (item.get_name() or "").split("/")[-1]
            except Exception:
                title = ""
            documents.append(Document(
                page_content=body,
                metadata={
                    "source": file_path.name,
                    "source_path": str(file_path),
                    "file_name": file_path.name,
                    "file_type": "epub",
                    "modality": "text",
                    "page": idx,
                    "chapter": title,
                },
            ))
        if not documents:
            return self._create_error_result("EPUB 没有可解析的正文",
                                             error_type="empty_content")
        return self._create_success_result(documents, metadata={"chapters": len(documents)})

    # ---------- 组装 ----------

    def _text_result(self, file_path: Path, text: str, ext: str) -> LoadResult:
        text = (text or "").strip()
        if not text:
            return self._create_error_result(
                f"{ext} 文件没有可解析的文本: {file_path.name}",
                error_type="empty_content")
        try:
            from langchain_core.documents import Document
        except ImportError:  # pragma: no cover
            from rag.text_splitter import SimpleDocument as Document  # type: ignore
        doc = Document(page_content=text, metadata={
            "source": file_path.name,
            "source_path": str(file_path),
            "file_name": file_path.name,
            "file_type": ext.lstrip("."),
            "modality": "text",
        })
        return self._create_success_result([doc])


__all__ = ["ExtendedLoader"]
