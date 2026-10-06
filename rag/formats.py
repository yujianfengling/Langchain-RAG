"""
统一格式能力注册表（**单一事实来源**，2026-10-07）

前后端共用同一份事实：
- 前端 <input accept> / 拖拽校验
- 后端上传校验与解析器分派
- 帮助说明与 GET /api/knowledge/capabilities

每项字段严格遵循 docs/IMPORT_API_CONTRACT.md：
    ext / kind / label / parser / requires / available / note

设计原则（铁律）：
- "支持某格式"必须意味着内容能被**真实解析**并进入检索；后缀能选、文件能存不算。
- available=false 的格式仍出现在清单里，但必须能给出明确原因，绝不假装成功。
- 外部能力（LibreOffice / unrar）在进程内按需探测，探测失败不影响主流程。
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# ==================== 错误类型（失败必须带 error_type，便于前端区分） ====================

ERROR_UNSUPPORTED_FORMAT = "unsupported_format"
ERROR_PARSE = "parse_error"
ERROR_EMPTY = "empty_content"
ERROR_CONVERTER_UNAVAILABLE = "converter_unavailable"
ERROR_CONVERTER_FAILED = "converter_failed"
ERROR_CONVERTER_TIMEOUT = "converter_timeout"
ERROR_CONVERTER_ERROR = "converter_error"
ERROR_ARCHIVE_ENCRYPTED = "archive_encrypted"
ERROR_ARCHIVE_CORRUPT = "archive_corrupt"
ERROR_ARCHIVE_LIMIT = "archive_limit_exceeded"
ERROR_ARCHIVE_UNSAFE_PATH = "archive_unsafe_path"
ERROR_ARCHIVE_NESTED_LIMIT = "archive_nested_limit"
ERROR_ARCHIVE_UNSUPPORTED = "archive_unsupported"
ERROR_BUILD_FAILED = "build_failed"
ERROR_IO = "io_error"

# ==================== 资料大类 ====================

KIND_PRESENTATION = "presentation"
KIND_DOCUMENT = "document"
KIND_SPREADSHEET = "spreadsheet"
KIND_IMAGE = "image"
KIND_ARCHIVE = "archive"

# ==================== 数据结构 ====================


@dataclass(frozen=True)
class FormatSpec:
    """单个格式的能力描述（对外序列化时去掉内部字段）"""

    ext: str                       # 带点的规范化扩展名，如 ".pptx"
    kind: str
    label: str
    parser: str
    requires: Optional[str]
    available: bool
    note: Optional[str] = None
    direct: bool = True            # True=可直接解析；False=需外部转换组件
    archive: bool = False

    def to_public(self) -> Dict[str, Any]:
        """按契约序列化：ext 不带点，字段固定 7 个"""
        return {
            "ext": self.ext.lstrip("."),
            "kind": self.kind,
            "label": self.label,
            "parser": self.parser,
            "requires": self.requires,
            "available": self.available,
            "note": self.note,
        }


# ==================== 可用性探测 ====================


def module_available(name: str) -> bool:
    """模块是否可导入（不真正导入，避免拖慢启动）"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def converter_capabilities() -> Dict[str, Any]:
    """LibreOffice 转换组件能力（惰性导入，避免循环依赖）"""
    try:
        from services import office_converter
        return office_converter.capabilities()
    except Exception as e:  # 转换组件本身坏了也不能拖垮能力清单
        return {
            "available": False,
            "path": None,
            "kind": None,
            "note": f"转换组件探测失败：{e}",
        }


def _library_note(module: str) -> str:
    return f"需要安装 {module}（{module} 未安装）"


# ==================== 注册表 ====================


def _build_specs() -> List[FormatSpec]:
    """每次调用都重新计算（外部能力可能在运行期变化）"""
    conv = converter_capabilities()
    conv_ok = bool(conv.get("available"))
    conv_note = conv.get("note")
    # 转换组件如实对外："libreoffice" / "msoffice"；不可用时 requires 保留历史标识
    # "libreoffice"（前端据此把失败归类为 converter_unavailable，字段语义稳定）。
    conv_kind = conv.get("kind")
    conv_requires = conv_kind if conv_kind else "libreoffice"
    conv_label = f"{conv_kind or 'libreoffice'} → "

    specs: List[FormatSpec] = []

    # ---------- 课件 ----------
    pptx_ok = module_available("pptx")
    specs.append(FormatSpec(".pptx", KIND_PRESENTATION, "PowerPoint 演示文稿",
                            "python-pptx", None, pptx_ok,
                            None if pptx_ok else _library_note("python-pptx")))
    specs.append(FormatSpec(".ppt", KIND_PRESENTATION, "PowerPoint 97-2003 演示文稿",
                            conv_label + "pptx", conv_requires, conv_ok, conv_note,
                            direct=False))

    # ---------- 文档 ----------
    pdf_ok = module_available("pdfplumber")
    specs.append(FormatSpec(".pdf", KIND_DOCUMENT, "PDF 文档",
                            "pdfplumber（可选视觉增强）", None, pdf_ok,
                            None if pdf_ok else _library_note("pdfplumber")))
    docx_ok = module_available("docx2txt") or module_available("docx")
    specs.append(FormatSpec(".docx", KIND_DOCUMENT, "Word 文档",
                            "docx2txt", None, docx_ok,
                            None if docx_ok else _library_note("docx2txt")))
    specs.append(FormatSpec(".doc", KIND_DOCUMENT, "Word 97-2003 文档",
                            conv_label + "docx", conv_requires, conv_ok, conv_note,
                            direct=False))
    specs.append(FormatSpec(".txt", KIND_DOCUMENT, "纯文本", "stdlib", None, True))
    specs.append(FormatSpec(".md", KIND_DOCUMENT, "Markdown", "stdlib", None, True))

    # ---------- 表格 ----------
    xlsx_ok = module_available("openpyxl")
    specs.append(FormatSpec(".xlsx", KIND_SPREADSHEET, "Excel 工作簿",
                            "openpyxl", None, xlsx_ok,
                            None if xlsx_ok else _library_note("openpyxl")))
    xls_ok = module_available("xlrd")
    specs.append(FormatSpec(".xls", KIND_SPREADSHEET, "Excel 97-2003 工作簿",
                            "xlrd", None, xls_ok,
                            None if xls_ok else _library_note("xlrd")))
    specs.append(FormatSpec(".csv", KIND_SPREADSHEET, "CSV 表格", "stdlib", None, True))

    # ---------- 图片 ----------
    image_ok = module_available("PIL")
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        specs.append(FormatSpec(ext, KIND_IMAGE, f"图片 {ext.lstrip('.').upper()}",
                                "Pillow + 视觉模型", "vision", image_ok,
                                None if image_ok else _library_note("Pillow")))

    # ---------- 可选扩展（有可靠解析器才注册） ----------
    if module_available("bs4"):
        specs.append(FormatSpec(".html", KIND_DOCUMENT, "HTML 网页", "beautifulsoup4", None, True))
        specs.append(FormatSpec(".htm", KIND_DOCUMENT, "HTML 网页", "beautifulsoup4", None, True))
    if module_available("striprtf"):
        specs.append(FormatSpec(".rtf", KIND_DOCUMENT, "RTF 富文本", "striprtf", None, True))
    if module_available("odf"):
        specs.append(FormatSpec(".odt", KIND_DOCUMENT, "OpenDocument 文本", "odfpy", None, True))
        specs.append(FormatSpec(".odp", KIND_PRESENTATION, "OpenDocument 演示文稿", "odfpy", None, True))
    if module_available("ebooklib"):
        specs.append(FormatSpec(".epub", KIND_DOCUMENT, "EPUB 电子书", "ebooklib", None, True))

    # ---------- 压缩包 ----------
    specs.append(FormatSpec(".zip", KIND_ARCHIVE, "ZIP 压缩包", "zipfile", None, True,
                            archive=True, direct=False))
    sz_ok = module_available("py7zr")
    specs.append(FormatSpec(".7z", KIND_ARCHIVE, "7-Zip 压缩包", "py7zr", None, sz_ok,
                            None if sz_ok else _library_note("py7zr"),
                            archive=True, direct=False))
    rar_mod = module_available("rarfile")
    rar_backend = _rar_backend()
    rar_tool = rar_backend.get("tool")
    rar_kind = rar_backend.get("kind")
    rar_ok = rar_mod and bool(rar_tool)
    if not rar_mod:
        rar_note = _library_note("rarfile")
    elif not rar_tool:
        rar_note = ("已安装 rarfile，但缺少外部解压后端（unrar/unar/7z/7zz 或系统 "
                    "bsdtar）；无法解压 .rar。Windows 10+ 自带 %SystemRoot%\\System32\\tar.exe"
                    "（bsdtar），若被删除请安装 WinRAR/unrar 或 7-Zip 并加入 PATH")
    else:
        rar_note = f"解压后端：{rar_kind or 'external'}（{rar_tool}）"
    specs.append(FormatSpec(".rar", KIND_ARCHIVE, "RAR 压缩包",
                            f"rarfile + {rar_kind or 'unrar'}", "unrar",
                            rar_ok, rar_note, archive=True, direct=False))

    return specs


def _rar_tool() -> Optional[str]:
    """外部 rar 解压工具路径（惰性导入 archive_service，避免循环依赖）"""
    backend = _rar_backend()
    return backend.get("tool")


def _rar_backend() -> Dict[str, Any]:
    """外部 rar 解压后端 {tool, kind}；探测失败返回空后端（惰性导入）"""
    try:
        from services import archive_service
        return archive_service.rar_backend()
    except Exception:
        return {"tool": None, "kind": None}


# ==================== 对外查询 ====================


def list_formats(include_unavailable: bool = True) -> List[FormatSpec]:
    """全部格式能力（默认含不可用项，供能力清单展示）"""
    specs = _build_specs()
    if include_unavailable:
        return specs
    return [s for s in specs if s.available]


def list_archives(include_unavailable: bool = True) -> List[FormatSpec]:
    specs = [s for s in _build_specs() if s.archive]
    if include_unavailable:
        return specs
    return [s for s in specs if s.available]


def normalize_ext(value: str) -> str:
    """把任意写法（pptx / .PPTX / a.PPTX）规范成带点小写扩展名"""
    if not value:
        return ""
    text = str(value).strip().lower()
    if not text.startswith("."):
        text = "." + text
    return text


def get_format(ext: str) -> Optional[FormatSpec]:
    """按扩展名取格式能力（规范化为小写）"""
    target = normalize_ext(ext)
    for spec in _build_specs():
        if spec.ext == target:
            return spec
    return None


def is_available(ext: str) -> bool:
    spec = get_format(ext)
    return bool(spec and spec.available)


def is_archive(ext: str) -> bool:
    spec = get_format(ext)
    return bool(spec and spec.archive)


def available_extensions() -> List[str]:
    """available=true 的全部扩展名（含压缩包），带点小写，已排序"""
    return sorted({s.ext for s in _build_specs() if s.available})


def parseable_extensions() -> List[str]:
    """可被解析进入检索的扩展名（排除压缩包；含可用的转换格式）"""
    return sorted({s.ext for s in _build_specs()
                   if s.available and not s.archive})


def archive_extensions() -> List[str]:
    return sorted({s.ext for s in _build_specs() if s.archive})


def accept_string() -> str:
    """<input accept> 用的逗号分隔列表（只含 available=true）"""
    return ",".join(available_extensions())


def _env_int(name: str, default: int) -> int:
    """读取整数型 env；空值/非法值一律回退默认（不因配置写错而放宽限制）"""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def limits() -> Dict[str, Any]:
    """
    导入限制的**唯一来源**（默认值可被 env IMPORT_MAX_* 覆盖）。

    字段与语义：
    - max_file_mb          普通文档单文件上限（旧字段，语义保持 = 非压缩包）
                           默认 200，env IMPORT_MAX_DOCUMENT_MB
                           （兼容旧 env IMPORT_MAX_FILE_MB 作为回退）
    - max_archive_mb       压缩包单文件上限，默认 500，env IMPORT_MAX_ARCHIVE_MB
    - max_extract_file_mb  压缩包展开后**单个成员**上限，默认 200，
                           env IMPORT_MAX_EXTRACT_FILE_MB
    - max_extract_total_mb 单批**展开总量**上限，默认 1024，
                           env IMPORT_MAX_EXTRACT_TOTAL_MB
    - max_total_mb         旧字段，语义 = 单批展开总量（= max_extract_total_mb），
                           保留以免前端契约破裂
    - max_files            文件数量（含展开成员），默认 100，env IMPORT_MAX_FILES
    - max_depth            递归深度，默认 2，env IMPORT_MAX_DEPTH
    - max_seconds          单任务超时秒数，默认 300，env IMPORT_MAX_SECONDS
    """
    # 兼容旧 env：优先新名，未设置时回退旧名 IMPORT_MAX_FILE_MB
    doc_mb = _env_int("IMPORT_MAX_DOCUMENT_MB", _env_int("IMPORT_MAX_FILE_MB", 200))
    extract_total_mb = _env_int("IMPORT_MAX_EXTRACT_TOTAL_MB", 1024)
    return {
        "max_file_mb": doc_mb,
        "max_archive_mb": _env_int("IMPORT_MAX_ARCHIVE_MB", 500),
        "max_extract_file_mb": _env_int("IMPORT_MAX_EXTRACT_FILE_MB", 200),
        "max_extract_total_mb": extract_total_mb,
        "max_total_mb": extract_total_mb,
        "max_files": _env_int("IMPORT_MAX_FILES", 100),
        "max_depth": _env_int("IMPORT_MAX_DEPTH", 2),
        "max_seconds": _env_int("IMPORT_MAX_SECONDS", 300),
    }


def upload_limit_mb(ext: str) -> int:
    """按扩展名取"单文件上传上限"（MB）：压缩包用 max_archive_mb，其余用 max_file_mb"""
    spec = get_format(ext)
    lim = limits()
    if spec is not None and spec.archive:
        return int(lim["max_archive_mb"])
    return int(lim["max_file_mb"])


def upload_limit_bytes(ext: str) -> int:
    """按扩展名取单文件上传上限（字节）"""
    return upload_limit_mb(ext) * 1024 * 1024


def extract_limits_bytes() -> Dict[str, int]:
    """压缩包展开限制（字节），供 archive_service / import_service 共用"""
    lim = limits()
    return {
        "max_total_bytes": int(lim["max_extract_total_mb"]) * 1024 * 1024,
        "max_extract_file_bytes": int(lim["max_extract_file_mb"]) * 1024 * 1024,
        "max_files": int(lim["max_files"]),
        "max_depth": int(lim["max_depth"]),
        "max_seconds": int(lim["max_seconds"]),
    }


def requires_office_converter(ext: str) -> bool:
    """该扩展名是否需要外部 Office 转换组件（.ppt/.doc）"""
    spec = get_format(ext)
    return bool(spec is not None and not spec.direct and not spec.archive)


def capabilities() -> Dict[str, Any]:
    """GET /api/knowledge/capabilities 的 data 结构"""
    specs = _build_specs()
    formats = [s.to_public() for s in specs if not s.archive]
    archives = [s.to_public() for s in specs if s.archive]
    return {
        "formats": formats,
        "archives": archives,
        "converter": converter_capabilities(),
        "limits": limits(),
        "accept": accept_string(),
    }


def unavailable_reason(ext: str) -> str:
    """给用户看的"为什么不可用"（不可用时必须明确）"""
    spec = get_format(ext)
    if spec is None:
        return f"不支持的文件类型: {normalize_ext(ext) or ext}"
    if spec.available:
        return ""
    return spec.note or f"{spec.ext} 当前不可用（缺少解析组件）"


__all__ = [
    "FormatSpec",
    "KIND_PRESENTATION",
    "KIND_DOCUMENT",
    "KIND_SPREADSHEET",
    "KIND_IMAGE",
    "KIND_ARCHIVE",
    "ERROR_UNSUPPORTED_FORMAT",
    "ERROR_PARSE",
    "ERROR_EMPTY",
    "ERROR_CONVERTER_UNAVAILABLE",
    "ERROR_CONVERTER_FAILED",
    "ERROR_CONVERTER_TIMEOUT",
    "ERROR_CONVERTER_ERROR",
    "ERROR_ARCHIVE_ENCRYPTED",
    "ERROR_ARCHIVE_CORRUPT",
    "ERROR_ARCHIVE_LIMIT",
    "ERROR_ARCHIVE_UNSAFE_PATH",
    "ERROR_ARCHIVE_NESTED_LIMIT",
    "ERROR_ARCHIVE_UNSUPPORTED",
    "ERROR_BUILD_FAILED",
    "ERROR_IO",
    "accept_string",
    "archive_extensions",
    "available_extensions",
    "capabilities",
    "converter_capabilities",
    "extract_limits_bytes",
    "get_format",
    "is_archive",
    "is_available",
    "limits",
    "requires_office_converter",
    "upload_limit_bytes",
    "upload_limit_mb",
    "list_archives",
    "list_formats",
    "module_available",
    "normalize_ext",
    "parseable_extensions",
    "unavailable_reason",
]
