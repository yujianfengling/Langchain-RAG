"""
LibreOffice 转换组件（2026-10-07）

用途：把旧版二进制 Office 格式（.ppt / .doc）转换为可解析的 OOXML（.pptx / .docx）。
.xls 用 xlrd 直读，不走这里。

铁律：
- 检测不到 LibreOffice 时，能力清单标 available=false，导入这些文件明确失败
  并给出配置说明（装什么、怎么配 OFFICE_CONVERTER_PATH、怎么验证），
  绝不改后缀冒充 pptx/docx。
- 转换产物统一放在受控临时目录，保留"原文件 → 转换文件"的追溯信息；原始文件保留。
- 只做无头转换，不执行包内程序/脚本/宏。
"""

from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.path_config import get_data_dir

logger = logging.getLogger(__name__)

KIND = "libreoffice"
_CONVERTIBLE = {".ppt": ".pptx", ".doc": ".docx"}

_cached_path: Any = None  # None=未探测；False=确认不可用；str=可用路径


class OfficeConverterError(Exception):
    """转换失败（带 error_type 与可读原因）"""

    def __init__(self, message: str, error_type: str = "converter_unavailable"):
        super().__init__(message)
        self.error_type = error_type


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


def find_converter(refresh: bool = False) -> Optional[str]:
    """探测 LibreOffice 可执行文件；返回路径或 None（结果在进程内缓存）"""
    global _cached_path
    if _cached_path is not None and not refresh:
        return _cached_path or None

    for candidate in _candidate_paths():
        try:
            p = Path(candidate)
            if p.exists() and p.is_file():
                _cached_path = str(p)
                logger.info("检测到 LibreOffice 转换组件: %s", p)
                return _cached_path
        except OSError:
            continue
    _cached_path = False
    return None


def reset_cache() -> None:
    """清空探测缓存（测试 / 运行期修改 OFFICE_CONVERTER_PATH 后调用）"""
    global _cached_path
    _cached_path = None


def capabilities() -> Dict[str, Any]:
    """契约里的 converter 结构：available / path / kind / note"""
    path = find_converter()
    if path:
        note = f"已检测到 LibreOffice：{path}；可用于转换 .ppt/.doc"
        return {"available": True, "path": path, "kind": KIND, "note": note}

    note = (
        "旧版 .ppt/.doc 需要 LibreOffice 转换；未检测到。请安装 LibreOffice 后，"
        "把 soffice 加入 PATH，或设置环境变量 OFFICE_CONVERTER_PATH 指向 soffice 可执行文件"
        "（Windows 例：C:\\Program Files\\LibreOffice\\program\\soffice.exe），"
        "重启服务后访问 GET /api/knowledge/capabilities 应显示 available=true。"
        "说明：安装 LibreOffice 不会触发本服务下载大型模型。"
    )
    return {"available": False, "path": None, "kind": KIND, "note": note}


def can_convert(src_ext: str) -> bool:
    return src_ext.lower() in _CONVERTIBLE and bool(find_converter())


def target_ext_for(src_ext: str) -> str:
    ext = (src_ext or "").lower()
    if not ext.startswith("."):
        ext = "." + ext
    if ext not in _CONVERTIBLE:
        raise OfficeConverterError(
            f"暂不支持转换为该类型：{ext}（仅支持 {', '.join(sorted(_CONVERTIBLE))}）",
            error_type="converter_failed")
    return _CONVERTIBLE[ext]


def _output_root() -> Path:
    root = get_data_dir() / "office_convert_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def convert(src: str, target_ext: Optional[str] = None,
            timeout: Optional[int] = None) -> Dict[str, Any]:
    """
    用 LibreOffice 无头模式转换单个文件。

    Args:
        src: 源文件路径
        target_ext: 目标扩展名（.pptx/.docx）；None 时按源扩展名推导
        timeout: 超时秒数（None=env OFFICE_CONVERT_TIMEOUT 或 120）

    Returns:
        {"converted_path", "output_dir", "source_path", "source_ext",
         "target_ext", "converter", "trace"}

    Raises:
        OfficeConverterError: 组件不可用 / 转换失败（均带 error_type 与可读原因）
    """
    src_path = Path(src)
    if not src_path.exists() or not src_path.is_file():
        raise OfficeConverterError(f"待转换文件不存在: {src}", error_type="io_error")

    src_ext = src_path.suffix.lower()
    target = target_ext.lower() if target_ext else target_ext_for(src_ext)
    if not target.startswith("."):
        target = "." + target
    if target not in (".pptx", ".docx", ".xlsx"):
        raise OfficeConverterError(f"不支持的转换目标：{target}", error_type="converter_failed")

    soffice = find_converter()
    if not soffice:
        raise OfficeConverterError(capabilities()["note"],
                                   error_type="converter_unavailable")

    if timeout is None:
        try:
            timeout = int(os.getenv("OFFICE_CONVERT_TIMEOUT", "120"))
        except (TypeError, ValueError):
            timeout = 120

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
            f"LibreOffice 转换超时（>{timeout}s）：{src_path.name}", error_type="converter_failed")
    except OSError as e:
        raise OfficeConverterError(
            f"无法启动 LibreOffice：{e}", error_type="converter_failed")

    produced = sorted(workdir.glob(f"*{target}"))
    if not produced:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise OfficeConverterError(
            f"LibreOffice 未生成转换产物（{src_path.name} → {target}）。"
            f"退出码={proc.returncode}。{detail}",
            error_type="converter_failed")

    return {
        "converted_path": str(produced[0]),
        "output_dir": str(workdir),
        "source_path": str(src_path),
        "source_ext": src_ext,
        "target_ext": target,
        "converter": soffice,
        "trace": {
            "original_name": src_path.name,
            "original_ext": src_ext,
            "converted_name": produced[0].name,
            "converted_ext": target,
            "converter": soffice,
        },
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
    "OfficeConverterError",
    "can_convert",
    "capabilities",
    "cleanup",
    "convert",
    "find_converter",
    "reset_cache",
    "target_ext_for",
]
