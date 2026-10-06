"""
SpreadsheetLoader - 表格加载器（xlsx / xls / csv，2026-10-07）

- .xlsx：openpyxl 直读
- .xls ：xlrd 直读（无需转换；xlrd>=2 只支持 .xls）
- .csv ：stdlib csv（自动尝试 utf-8 / gbk / latin-1；sniffer 推断分隔符）

每个工作表一个 Document，正文为该表的可检索文本；metadata 含
page（工作表序号，1 起）/ sheet_name / modality="table" / file_name。
超大表按上限截断并如实标记 truncated。
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

from .base import BaseLoader, LoadResult

logger = logging.getLogger(__name__)

MAX_ROWS_PER_SHEET = 5000
MAX_COLS_PER_SHEET = 100


def _cell_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _rows_to_text(rows: List[List[str]]) -> str:
    lines = []
    for row in rows:
        cells = [_cell_to_text(c) for c in row[:MAX_COLS_PER_SHEET]]
        if any(cells):
            lines.append("\t".join(cells))
    return "\n".join(lines)


class SpreadsheetLoader(BaseLoader):
    """表格加载器（.xlsx / .xls / .csv）"""

    def supports(self, file_path: Union[str, Path]) -> bool:
        return Path(file_path).suffix.lower() in (".xlsx", ".xls", ".csv")

    def get_supported_extensions(self) -> List[str]:
        return [".xlsx", ".xls", ".csv"]

    def load(self, file_path: Union[str, Path]) -> LoadResult:
        try:
            file_path = self._validate_file(file_path)
        except Exception as e:
            return self._create_error_result(str(e), error_type="io_error")

        ext = file_path.suffix.lower()
        if ext == ".xlsx":
            return self._load_xlsx(file_path)
        if ext == ".xls":
            return self._load_xls(file_path)
        if ext == ".csv":
            return self._load_csv(file_path)
        return self._create_error_result(f"不支持的表格格式: {ext}",
                                         error_type="unsupported_format")

    # ---------- xlsx ----------

    def _load_xlsx(self, file_path: Path) -> LoadResult:
        try:
            from openpyxl import load_workbook
        except ImportError:
            return self._create_error_result(
                "解析 .xlsx 需要 openpyxl，请先安装：pip install openpyxl",
                error_type="unsupported_format")
        try:
            wb = load_workbook(str(file_path), read_only=True, data_only=True)
        except Exception as e:
            return self._create_error_result(f"无法打开 XLSX 文件: {e}",
                                             error_type="parse_error")

        return self._build_result(
            file_path, "xlsx",
            self._iter_openpyxl_sheets(wb))

    def _iter_openpyxl_sheets(self, wb: Any):
        for idx, name in enumerate(wb.sheetnames, start=1):
            sheet = wb[name]
            rows: List[List[str]] = []
            truncated = False
            for r_idx, row in enumerate(sheet.iter_rows(values_only=True)):
                if r_idx >= MAX_ROWS_PER_SHEET:
                    truncated = True
                    break
                rows.append([_cell_to_text(c) for c in row])
            yield idx, name, rows, truncated

    # ---------- xls ----------

    def _load_xls(self, file_path: Path) -> LoadResult:
        try:
            import xlrd
        except ImportError:
            return self._create_error_result(
                "解析 .xls 需要 xlrd，请先安装：pip install xlrd",
                error_type="unsupported_format")
        try:
            book = xlrd.open_workbook(str(file_path))
        except Exception as e:
            return self._create_error_result(f"无法打开 XLS 文件: {e}",
                                             error_type="parse_error")

        def _iter():
            for idx, sheet in enumerate(book.sheets(), start=1):
                rows: List[List[str]] = []
                truncated = False
                for r in range(sheet.nrows):
                    if r >= MAX_ROWS_PER_SHEET:
                        truncated = True
                        break
                    values = sheet.row_values(r)
                    rows.append([_cell_to_text(v) for v in values[:MAX_COLS_PER_SHEET]])
                yield idx, sheet.name, rows, truncated

        return self._build_result(file_path, "xls", _iter())

    # ---------- csv ----------

    def _read_csv_text(self, file_path: Path) -> Tuple[str, str]:
        raw = file_path.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
            try:
                return raw.decode(enc), enc
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace"), "utf-8"

    def _load_csv(self, file_path: Path) -> LoadResult:
        text, _enc = self._read_csv_text(file_path)
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except Exception:
            dialect = csv.excel

        rows: List[List[str]] = []
        truncated = False
        reader = csv.reader(io.StringIO(text), dialect)
        for idx, row in enumerate(reader):
            if idx >= MAX_ROWS_PER_SHEET:
                truncated = True
                break
            rows.append(row)

        def _iter():
            yield 1, file_path.stem, rows, truncated

        return self._build_result(file_path, "csv", _iter())

    # ---------- 组装 ----------

    def _build_result(self, file_path: Path, file_type: str, sheets: Any) -> LoadResult:
        try:
            from langchain_core.documents import Document
        except ImportError:  # pragma: no cover
            from rag.text_splitter import SimpleDocument as Document  # type: ignore

        documents: List[Any] = []
        empty_sheets: List[str] = []
        for idx, name, rows, truncated in sheets:
            body = _rows_to_text(rows)
            if not body:
                empty_sheets.append(name)
            documents.append(Document(
                page_content=body,
                metadata={
                    "source": file_path.name,
                    "source_path": str(file_path),
                    "file_name": file_path.name,
                    "file_type": file_type,
                    "modality": "table",
                    "page": idx,
                    "sheet_name": name,
                    "row_count": len(rows),
                    "truncated": truncated,
                },
            ))

        if not documents:
            return self._create_error_result(
                f"{file_type} 文件没有工作表: {file_path.name}",
                error_type="empty_content")

        indexed = [d for d in documents if (d.page_content or "").strip()]
        if not indexed:
            return self._create_error_result(
                f"{file_type} 文件没有可解析的表格内容: {file_path.name}",
                metadata={"sheets": len(documents), "empty_sheets": empty_sheets},
                error_type="empty_content")

        return self._create_success_result(
            documents,
            metadata={"sheets": len(documents), "empty_sheets": empty_sheets},
        )


__all__ = ["SpreadsheetLoader"]
