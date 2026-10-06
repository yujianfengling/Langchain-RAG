"""
LegacyOfficeLoader - 旧版 Office（.ppt / .doc）加载器（2026-10-07）

旧版二进制格式**不能**直接解析，也不能改后缀冒充 pptx/docx：
必须先经 LibreOffice 转成 OOXML，再交给 PptxLoader / DocxLoader 真实解析。

- 未检测到 LibreOffice：明确失败（converter_unavailable）并给出配置说明。
- 转换产物放受控临时目录，解析后清理；原始文件保留。
- 追溯信息：转换前后的文件名/格式写入 LoadResult.metadata.conversion 与每个 Document
  的 metadata（converted_from），引用仍显示原始课件名与真实幻灯片/页码。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional, Union

from .base import BaseLoader, LoadResult

logger = logging.getLogger(__name__)

_SUPPORTED = {".ppt": ".pptx", ".doc": ".docx"}


class LegacyOfficeLoader(BaseLoader):
    """旧版 Office 加载器（依赖 LibreOffice 转换）"""

    def __init__(self, enricher: Any = None, enrich_images: bool = True):
        self._enricher = enricher
        self._enrich_images = enrich_images

    def supports(self, file_path: Union[str, Path]) -> bool:
        return Path(file_path).suffix.lower() in _SUPPORTED

    def get_supported_extensions(self) -> List[str]:
        return sorted(_SUPPORTED)

    def load(self, file_path: Union[str, Path]) -> LoadResult:
        try:
            file_path = self._validate_file(file_path)
        except Exception as e:
            return self._create_error_result(str(e), error_type="io_error")

        ext = file_path.suffix.lower()
        if ext not in _SUPPORTED:
            return self._create_error_result(
                f"LegacyOfficeLoader 不支持 {ext}", error_type="unsupported_format")

        try:
            from services import office_converter
        except Exception as e:
            return self._create_error_result(
                f"转换组件不可用: {e}", error_type="converter_unavailable")

        if not office_converter.find_converter():
            return self._create_error_result(
                office_converter.capabilities()["note"],
                error_type="converter_unavailable")

        try:
            converted = office_converter.convert(str(file_path))
        except office_converter.OfficeConverterError as e:
            return self._create_error_result(str(e), error_type=e.error_type)
        except Exception as e:
            return self._create_error_result(
                f"转换为 {_SUPPORTED[ext]} 失败: {e}", error_type="converter_failed")

        try:
            converted_path = Path(converted["converted_path"])
            if converted["target_ext"] == ".pptx":
                from .pptx_loader import PptxLoader
                delegate = PptxLoader(enricher=self._enricher,
                                      enrich_images=self._enrich_images)
            else:
                from .docx_loader import DocxLoader
                delegate = DocxLoader()

            result = delegate.load(converted_path)
            if result.success:
                for doc in result.documents:
                    meta = getattr(doc, "metadata", None)
                    if not isinstance(meta, dict):
                        continue
                    meta["file_name"] = file_path.name
                    meta["source"] = file_path.name
                    meta["source_path"] = str(file_path)
                    meta["file_type"] = ext.lstrip(".")
                    meta["origin_file_type"] = ext.lstrip(".")
                    meta["converted_from"] = converted["trace"]["converted_name"]

            return LoadResult(
                documents=result.documents if result.success else [],
                success=result.success,
                error_message=result.error_message,
                error_type=result.error_type,
                metadata={**(result.metadata or {}), "conversion": converted["trace"]},
            )
        finally:
            office_converter.cleanup(converted["output_dir"])


__all__ = ["LegacyOfficeLoader"]
