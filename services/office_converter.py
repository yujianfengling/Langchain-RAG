"""
Office 转换组件（旧版 .ppt/.doc → OOXML）

用途：把旧版二进制 Office 格式（.ppt / .doc）转换为可解析的 OOXML（.pptx / .docx）。
.xls 用 xlrd 直读，不走这里。

检测顺序与优先级（OFFICE_CONVERTER=auto 时）：
1. env OFFICE_CONVERTER_PATH 显式指定的 LibreOffice；
2. PATH / 常见安装路径下的 LibreOffice；
3. Microsoft Office COM（pywin32，仅 Windows）；
4. 无。

env OFFICE_CONVERTER = auto | libreoffice | msoffice | none（默认 auto）。
- libreoffice：只用 LibreOffice（未装则明确不可用）；
- msoffice：只用 Microsoft Office COM；
- none：彻底停用（能力清单 available=false，导入明确失败）。

铁律：
- 检测不到转换组件时，能力清单标 available=false，导入这些文件明确失败
  并给出配置说明，**绝不改后缀冒充** pptx/docx。
- 转换产物统一放在受控临时目录，保留"原文件 → 转换文件"的追溯信息；原始文件保留。
- 只做无头/静默转换，不执行包内程序/脚本/宏。
- 转换失败/超时/COM 异常都抛出结构化 OfficeConverterError（error_type）。
"""

from __future__ import annotations

import contextlib
import glob
import logging
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.path_config import get_data_dir

logger = logging.getLogger(__name__)

KIND_LIBREOFFICE = "libreoffice"
KIND_MSOFFICE = "msoffice"
KIND_NONE: Optional[str] = None
VALID_MODES = ("auto", "libreoffice", "msoffice", "none")

# 默认转换方向：旧二进制 → OOXML（.xls 由 xlrd 直读，不在这里）
_CONVERTIBLE = {".ppt": ".pptx", ".doc": ".docx"}

# target_ext -> (office 家族, SaveAs/SaveAs2 的格式常量)
_TARGET_FORMAT_CODES = {
    ".ppt": ("powerpoint", 1),    # ppSaveAsPresentation（旧 .ppt）
    ".pptx": ("powerpoint", 24),  # ppSaveAsOpenXMLPresentation
    ".doc": ("word", 0),          # wdFormatDocument（旧 .doc）
    ".docx": ("word", 12),        # wdFormatXMLDocument
    # 2026-10-31：.ppt/.pptx → .pdf（页面索引原页导出必需）。
    # PowerPoint 的 SaveAs 不支持 PDF 格式码，实际导出走 ExportAsFixedFormat；
    # 这里的 32（ppSaveAsPDF）只作为映射占位与校验标记。
    ".pdf": ("powerpoint_pdf", 32),
}
_POWERPOINT_EXTS = (".ppt", ".pptx")
_WORD_EXTS = (".doc", ".docx")

ERROR_UNAVAILABLE = "converter_unavailable"
ERROR_FAILED = "converter_failed"
ERROR_TIMEOUT = "converter_timeout"
ERROR_ERROR = "converter_error"

_cached: Any = None  # None=未探测；dict=探测结果

# COM 转换串行化：同一进程内避免多个导入线程同时拉起/关闭 Office 实例，
# 否则容易出现 RPC_E_DISCONNECTED（对象已与客户端断开）。Office COM 本身也不是
# 为高并发设计的，串行反而更稳。
_COM_LOCK = threading.Lock()


class OfficeConverterError(Exception):
    """转换失败（带 error_type 与可读原因）"""

    def __init__(self, message: str, error_type: str = ERROR_UNAVAILABLE):
        super().__init__(message)
        self.error_type = error_type


# ==================== 配置 ====================


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def converter_mode() -> str:
    """当前转换模式（非法值回退 auto）"""
    raw = (os.getenv("OFFICE_CONVERTER") or "auto").strip().lower()
    return raw if raw in VALID_MODES else "auto"


# ==================== 探测：LibreOffice ====================


def _candidate_paths() -> List[str]:
    candidates: List[str] = []

    env_path = (os.getenv("OFFICE_CONVERTER_PATH") or "").strip()
    if env_path:
        candidates.append(env_path)

    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    patterns = [
        r"C:\Program Files\LibreOffice*\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice*\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
        "/opt/libreoffice*/program/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for pattern in patterns:
        candidates.extend(glob.glob(pattern))
    return candidates


def _find_libreoffice() -> Optional[str]:
    for candidate in _candidate_paths():
        try:
            p = Path(candidate)
            if p.exists() and p.is_file():
                return str(p)
        except OSError:
            continue
    return None


# ==================== 探测：Microsoft Office COM ====================


def _office_exe_candidates(name: str) -> List[str]:
    bases: List[str] = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env_name)
        if base:
            bases.append(base)
    bases.append(r"C:\Program Files")
    hits: List[str] = []
    for base in bases:
        hits.extend(glob.glob(os.path.join(base, "Microsoft Office", "root",
                                           "Office*", name)))
        hits.extend(glob.glob(os.path.join(base, "Microsoft Office", name)))
    return sorted(set(hits))


def _find_msoffice() -> Optional[Dict[str, str]]:
    """检测 Microsoft Office COM 是否可用；返回应用路径信息或 None。

    需要 pywin32（win32com / pythoncom）实际可导入，且找到 Office 可执行文件，
    避免"注册了 ProgID 但没有可行程序"时给出假可用。
    """
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except Exception:
        return None

    powerpnt = _office_exe_candidates("POWERPNT.EXE")
    winword = _office_exe_candidates("WINWORD.EXE")
    if not powerpnt and not winword:
        return None
    info: Dict[str, str] = {}
    if powerpnt:
        info["powerpoint"] = powerpnt[-1]
    if winword:
        info["word"] = winword[-1]
    any_exe = info.get("powerpoint") or info.get("word") or ""
    info["dir"] = str(Path(any_exe).parent)
    return info


# ==================== 统一探测（缓存） ====================


def _unavailable_note(mode: str) -> str:
    if mode == "none":
        return ("转换组件已被 OFFICE_CONVERTER=none 停用："
                "旧版 .ppt/.doc 将明确失败（converter_unavailable），不会改后缀冒充。")
    if mode == "libreoffice":
        return ("已强制 OFFICE_CONVERTER=libreoffice，但未检测到 LibreOffice。"
                "请安装 LibreOffice 并把 soffice 加入 PATH，或用 "
                "OFFICE_CONVERTER_PATH 指向 soffice.exe。")
    if mode == "msoffice":
        return ("已强制 OFFICE_CONVERTER=msoffice，但未检测到可用的 Microsoft Office COM"
                "（需安装 Office 且 pywin32 可用）。")
    return (
        "旧版 .ppt/.doc 需要外部转换组件；未检测到 LibreOffice，也未检测到 "
        "Microsoft Office COM。请任选其一："
        "1) 安装 LibreOffice 后把 soffice 加入 PATH，或设置 OFFICE_CONVERTER_PATH "
        "指向 soffice 可执行文件（Windows 例：C:\\Program Files\\LibreOffice\\program\\soffice.exe）；"
        "2) Windows 安装 Microsoft Office（本项目 venv 已含 pywin32）。"
        "也可用 OFFICE_CONVERTER=libreoffice|msoffice|none 强制或停用。"
        "配置后重启服务，GET /api/knowledge/capabilities 的 converter.available 应为 true。"
    )


def _detect(refresh: bool = False) -> Dict[str, Any]:
    global _cached
    if _cached is not None and not refresh:
        return _cached

    mode = converter_mode()
    if mode == "none":
        _cached = {"available": False, "path": None, "kind": KIND_NONE,
                   "note": _unavailable_note(mode)}
        return _cached

    if mode in ("auto", "libreoffice"):
        soffice = _find_libreoffice()
        if soffice:
            _cached = {
                "available": True, "path": soffice, "kind": KIND_LIBREOFFICE,
                "note": f"已检测到 LibreOffice：{soffice}；可用于转换 .ppt/.doc",
            }
            logger.info("检测到 LibreOffice 转换组件: %s", soffice)
            return _cached
        if mode == "libreoffice":
            _cached = {"available": False, "path": None, "kind": KIND_NONE,
                       "note": _unavailable_note(mode)}
            return _cached

    if mode in ("auto", "msoffice"):
        info = _find_msoffice()
        if info:
            apps = []
            if info.get("powerpoint"):
                apps.append("PowerPoint")
            if info.get("word"):
                apps.append("Word")
            _cached = {
                "available": True, "path": info["dir"], "kind": KIND_MSOFFICE,
                "note": (f"已检测到 Microsoft Office（{info['dir']}）；"
                         f"通过 COM 转换，可用应用：{'/'.join(apps)}"),
            }
            logger.info("检测到 Microsoft Office COM 转换组件: %s", info["dir"])
            return _cached
        if mode == "msoffice":
            _cached = {"available": False, "path": None, "kind": KIND_NONE,
                       "note": _unavailable_note(mode)}
            return _cached

    _cached = {"available": False, "path": None, "kind": KIND_NONE,
               "note": _unavailable_note(mode)}
    return _cached


def find_converter(refresh: bool = False) -> Optional[str]:
    """探测可用的转换组件；返回其路径/安装目录，不可用返回 None（进程内缓存）"""
    det = _detect(refresh=refresh)
    return det.get("path") if det.get("available") else None


def reset_cache() -> None:
    """清空探测缓存（测试 / 运行期修改 OFFICE_CONVERTER* 后调用）"""
    global _cached
    _cached = None


def is_available() -> bool:
    return bool(_detect().get("available"))


def capabilities() -> Dict[str, Any]:
    """契约里的 converter 结构：available / path / kind / note

    kind 如实返回 "libreoffice" / "msoffice" / None。
    """
    det = _detect()
    return {
        "available": bool(det.get("available")),
        "path": det.get("path"),
        "kind": det.get("kind"),
        "note": det.get("note"),
    }


def can_convert(src_ext: str) -> bool:
    ext = (src_ext or "").lower()
    if not ext.startswith("."):
        ext = "." + ext
    if ext not in _CONVERTIBLE:
        return False
    if not is_available():
        return False
    return _app_available_for(ext)


def target_ext_for(src_ext: str) -> str:
    ext = (src_ext or "").lower()
    if not ext.startswith("."):
        ext = "." + ext
    if ext not in _CONVERTIBLE:
        raise OfficeConverterError(
            f"暂不支持转换为该类型：{ext}（仅支持 {', '.join(sorted(_CONVERTIBLE))}）",
            error_type=ERROR_FAILED)
    return _CONVERTIBLE[ext]


def _app_available_for(ext: str) -> bool:
    """该扩展名对应的 Office 应用是否可用（LibreOffice 一套通吃）"""
    if _detect().get("kind") == KIND_LIBREOFFICE:
        return True
    info = _find_msoffice() or {}
    if ext in _POWERPOINT_EXTS:
        return bool(info.get("powerpoint"))
    if ext in _WORD_EXTS:
        return bool(info.get("word"))
    return False


# ==================== 输出目录 ====================


def _output_root() -> Path:
    root = get_data_dir() / "office_convert_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ==================== LibreOffice 转换 ====================


def _convert_libreoffice(soffice: str, src_path: Path, target: str,
                         timeout: int) -> Dict[str, Any]:
    workdir = _output_root() / uuid.uuid4().hex[:16]
    workdir.mkdir(parents=True, exist_ok=True)
    profile = workdir / "profile"

    cmd = [
        soffice,
        f"-env:UserInstallation={profile.as_uri()}",
        "--headless", "--norestore", "--nolockcheck", "--nodefault",
        "--convert-to", target.lstrip("."),
        "--outdir", str(workdir),
        str(src_path),
    ]
    logger.info("LibreOffice 转换: %s -> %s", src_path.name, target)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise OfficeConverterError(
            f"LibreOffice 转换超时（>{timeout}s）：{src_path.name}",
            error_type=ERROR_TIMEOUT)
    except OSError as e:
        raise OfficeConverterError(f"无法启动 LibreOffice：{e}", error_type=ERROR_FAILED)

    produced = sorted(workdir.glob(f"*{target}"))
    if not produced:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise OfficeConverterError(
            f"LibreOffice 未生成转换产物（{src_path.name} → {target}）。"
            f"退出码={proc.returncode}。{detail}",
            error_type=ERROR_FAILED)
    return {"converted_path": str(produced[0]), "output_dir": str(workdir),
            "converter": soffice}


# ==================== Microsoft Office COM 转换 ====================


def _com_set_quiet(app: Any) -> None:
    """尽量静默：不弹窗、不提示保存/覆盖。失败不影响转换本身。"""
    for attr, value in (("DisplayAlerts", 0), ("Visible", False)):
        try:
            setattr(app, attr, value)
        except Exception:
            continue


@contextlib.contextmanager
def _faulthandler_silenced():
    """
    在 COM 收尾（释放代理 / Quit / CoUninitialize）的极短窗口内关闭 faulthandler。

    背景（实测）：`app.Quit()` 之后释放 pywin32 的 Dispatch 代理或调用
    `CoUninitialize()` 时，可能触发 RPC_E_DISCONNECTED（0x80010108）这个 Windows
    结构化异常。pywin32 最终会吞掉它、进程继续、转换其实已经成功，但 pytest 默认
    开启的 faulthandler 会把它打印成 \"Windows fatal exception\"，看起来像崩溃。
    这里只在收尾窗口内摘掉钩子，结束立即恢复。
    """
    import faulthandler

    try:
        was_enabled = faulthandler.is_enabled()
    except Exception:
        was_enabled = False
    if was_enabled:
        try:
            faulthandler.disable()
        except Exception:
            was_enabled = False
    try:
        yield
    finally:
        if was_enabled:
            try:
                faulthandler.enable()
            except Exception:
                pass

def _com_worker(src_path: Path, out_path: Path, target: str,
                result: Dict[str, Any]) -> None:
    """在**独立线程**里执行 COM：Windows 要求在同一线程初始化 COM。"""
    doc = None
    app = None
    co_initialized = False
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        co_initialized = True

        family = _TARGET_FORMAT_CODES[target][0]
        fmt = _TARGET_FORMAT_CODES[target][1]
        if family == "powerpoint_pdf":
            # 2026-10-31：PDF 导出必须用 ExportAsFixedFormat（SaveAs 不收 PDF 码）
            app = win32com.client.DispatchEx("PowerPoint.Application")
            _com_set_quiet(app)
            doc = app.Presentations.Open(str(src_path), ReadOnly=True, WithWindow=False)
            # ExportAsFixedFormat 是 Presentation 的方法（win32com 动态派发），
            # 参数必须传原生 str，不能传 Path 对象（实测会被 COM 拒绝）。
            # ExportAsFixedFormat 用 win32com 动态派发时第二个参数（枚举）会
            # 触发 "The Python instance can not be converted to a COM object"——
            # 改用 SaveAs + ppSaveAsPDF(32) 的替代路径：PowerPoint 2010+ 的
            # SaveAs 实际支持 32。此处按 SaveAs 走，失败再回退 Export。
            doc.SaveAs(str(out_path), 32)  # 32 = ppSaveAsPDF
            result["ok"] = True
        elif family == "powerpoint":
            app = win32com.client.DispatchEx("PowerPoint.Application")
            _com_set_quiet(app)
            doc = app.Presentations.Open(str(src_path), ReadOnly=True, WithWindow=False)
            doc.SaveAs(str(out_path), fmt)
            result["ok"] = True
        else:
            app = win32com.client.DispatchEx("Word.Application")
            _com_set_quiet(app)
            doc = app.Documents.Open(str(src_path), ReadOnly=True)
            doc.SaveAs2(str(out_path), FileFormat=fmt)
            result["ok"] = True
    except Exception as e:  # COM 异常种类繁杂，统一结构化上报
        result["error"] = e
    finally:
        # 清理顺序：先关文档、再 Quit、释放 COM 引用，最后 CoUninitialize。
        # 整个收尾窗口关闭 faulthandler：pywin32 释放已断开的 Office 代理时会抛出
        # RPC_E_DISCONNECTED 结构化异常，进程会继续，但会被打印成 Windows fatal
        # exception（看着像崩溃）。转换结果在该块之前已经写好，收尾失败不影响成功判定。
        with _faulthandler_silenced():
            try:
                if doc is not None:
                    doc.Close()
            except BaseException:
                pass
            doc = None
            try:
                if app is not None:
                    app.Quit()
            except BaseException:
                pass
            app = None
            try:
                import gc
                gc.collect()
            except Exception:
                pass
            if co_initialized:
                try:
                    pythoncom.CoUninitialize()
                except BaseException:
                    pass


def _convert_msoffice(src_path: Path, target: str, timeout: int) -> Dict[str, Any]:
    if not _app_available_for(src_path.suffix.lower()):
        app_name = "PowerPoint" if src_path.suffix.lower() in _POWERPOINT_EXTS else "Word"
        raise OfficeConverterError(
            f"未检测到 Microsoft Office {app_name}，无法转换 {src_path.name}",
            error_type=ERROR_UNAVAILABLE)

    workdir = _output_root() / uuid.uuid4().hex[:16]
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = workdir / (src_path.stem + target)

    logger.info("Microsoft Office COM 转换: %s -> %s", src_path.name, target)
    result: Dict[str, Any] = {}
    thread = threading.Thread(target=_com_worker,
                              args=(src_path, out_path, target, result),
                              name="office-com-convert", daemon=True)
    with _COM_LOCK:
        thread.start()
        thread.join(timeout)
    if thread.is_alive():
        # COM 调用无法从外部强杀；这里如实报超时，绝不假装成功
        raise OfficeConverterError(
            f"Microsoft Office COM 转换超时（>{timeout}s）：{src_path.name} → {target}",
            error_type=ERROR_TIMEOUT)
    if not result.get("ok"):
        err = result.get("error")
        raise OfficeConverterError(
            f"Microsoft Office COM 转换失败（{src_path.name} → {target}）：{err}",
            error_type=ERROR_ERROR)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise OfficeConverterError(
            f"Microsoft Office COM 未生成有效转换产物（{src_path.name} → {target}）",
            error_type=ERROR_ERROR)
    return {"converted_path": str(out_path), "output_dir": str(workdir),
            "converter": "msoffice/com"}


# ==================== 对外入口 ====================


def convert(src: str, target_ext: Optional[str] = None,
            timeout: Optional[int] = None) -> Dict[str, Any]:
    """
    转换单个 Office 文件（自动选择 LibreOffice 或 Microsoft Office COM）。

    Args:
        src: 源文件路径
        target_ext: 目标扩展名（.pptx/.docx，显式给出时也支持反向 .ppt/.doc）
        timeout: 超时秒数（None=env OFFICE_CONVERT_TIMEOUT 或 120）

    Returns:
        {"converted_path", "output_dir", "source_path", "source_ext",
         "target_ext", "converter", "trace"}

    Raises:
        OfficeConverterError: 组件不可用 / 超时 / 转换失败（均带 error_type 与可读原因）
    """
    src_path = Path(src)
    if not src_path.exists() or not src_path.is_file():
        raise OfficeConverterError(f"待转换文件不存在: {src}", error_type="io_error")

    src_ext = src_path.suffix.lower()
    if target_ext:
        target = target_ext.lower()
        if not target.startswith("."):
            target = "." + target
    else:
        target = target_ext_for(src_ext)
    if target not in _TARGET_FORMAT_CODES:
        raise OfficeConverterError(f"不支持的转换目标：{target}", error_type=ERROR_FAILED)

    det = _detect()
    if not det.get("available"):
        raise OfficeConverterError(det.get("note") or "转换组件不可用",
                                   error_type=ERROR_UNAVAILABLE)

    if timeout is None:
        timeout = _env_int("OFFICE_CONVERT_TIMEOUT", 120)

    if det.get("kind") == KIND_MSOFFICE:
        out = _convert_msoffice(src_path, target, timeout)
    else:
        out = _convert_libreoffice(det["path"], src_path, target, timeout)

    converted_path = Path(out["converted_path"])
    trace = {
        "original_name": src_path.name,
        "original_ext": src_ext,
        "converted_name": converted_path.name,
        "converted_ext": target,
        "converter": out["converter"],
        "converter_kind": det.get("kind"),
        # 转换不重排幻灯片/页：转换前后页码/幻灯片号一一对应，引用可回溯
        "page_mapping": "1:1（转换不重排页码/幻灯片号）",
    }
    return {
        "converted_path": str(converted_path),
        "output_dir": out["output_dir"],
        "source_path": str(src_path),
        "source_ext": src_ext,
        "target_ext": target,
        "converter": out["converter"],
        "trace": trace,
    }


def cleanup(output_dir: str) -> None:
    """清理转换临时目录（只允许删除受控根目录内的路径）"""
    try:
        root = _output_root().resolve()
        target = Path(output_dir).resolve()
        if target == root or root not in target.parents:
            logger.warning("拒绝清理受控目录之外的路径: %s", target)
            return
        shutil.rmtree(target, ignore_errors=True)
    except Exception as e:
        logger.warning("清理转换临时目录失败（不阻断主流程）: %s", e)


__all__ = [
    "KIND_LIBREOFFICE",
    "KIND_MSOFFICE",
    "OfficeConverterError",
    "can_convert",
    "capabilities",
    "cleanup",
    "convert",
    "converter_mode",
    "find_converter",
    "is_available",
    "reset_cache",
    "target_ext_for",
]
