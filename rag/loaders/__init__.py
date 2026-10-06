"""
rag.loaders - 文档加载器模块

提供统一的文档加载接口，支持多种文件格式。
分派表以 rag.formats（单一事实来源）为准。
"""

from pathlib import Path

from .base import BaseLoader, LoadResult
from .text_loader import TextLoader
from .markdown_loader import MarkdownLoader
from .pdf_loader import PdfLoader
from .docx_loader import DocxLoader
from .image_loader import ImageLoader
from .pptx_loader import PptxLoader
from .spreadsheet_loader import SpreadsheetLoader
from .legacy_office_loader import LegacyOfficeLoader
from .extended_loader import ExtendedLoader

__all__ = [
    'BaseLoader',
    'LoadResult',
    'TextLoader',
    'MarkdownLoader',
    'PdfLoader',
    'DocxLoader',
    'ImageLoader',
    'PptxLoader',
    'SpreadsheetLoader',
    'LegacyOfficeLoader',
    'ExtendedLoader',
    'get_loader_for_file',
    'get_supported_extensions',
]

# 扩展名 -> Loader 类。集合与 rag.formats 一致（新增格式时两处都要动，
# 测试 tests/test_formats_import.py 会断言一致性）。
_LOADER_MAP = {
    '.txt': TextLoader,
    '.md': MarkdownLoader,
    '.markdown': MarkdownLoader,
    '.pdf': PdfLoader,
    '.docx': DocxLoader,
    '.ppt': LegacyOfficeLoader,
    '.doc': LegacyOfficeLoader,
    '.pptx': PptxLoader,
    '.xlsx': SpreadsheetLoader,
    '.xls': SpreadsheetLoader,
    '.csv': SpreadsheetLoader,
    '.html': ExtendedLoader,
    '.htm': ExtendedLoader,
    '.rtf': ExtendedLoader,
    '.odt': ExtendedLoader,
    '.odp': ExtendedLoader,
    '.epub': ExtendedLoader,
    '.jpg': ImageLoader,
    '.jpeg': ImageLoader,
    '.png': ImageLoader,
    '.webp': ImageLoader,
}


def get_loader_for_file(file_path):
    """
    根据文件路径获取对应的 Loader 实例

    Args:
        file_path: 文件路径

    Returns:
        对应的 Loader 实例；不支持时返回 None
    """
    suffix = Path(file_path).suffix.lower()
    loader_class = _LOADER_MAP.get(suffix)
    if loader_class is None:
        return None
    return loader_class()


def get_supported_extensions():
    """当前**可真实解析**的扩展名（含可用的转换格式；不含压缩包）"""
    try:
        from rag import formats
        return formats.parseable_extensions()
    except Exception:
        return sorted(_LOADER_MAP)
