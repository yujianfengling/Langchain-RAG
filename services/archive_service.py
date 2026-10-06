"""
压缩包导入服务（2026-10-07）

支持 zip / 7z / rar，负责：
- 中文文件名（zip 的 cp437/GBK 解码）、空格、大小写扩展名
- 忽略 __MACOSX、点开头、Thumbs.db、desktop.ini、~$* 等无关内容，并说明跳过了什么
- 子目录与同名文件（保留相对路径，绝不互相覆盖）
- 嵌套压缩包有限层级
- 路径穿越 / 绝对路径 / 符号链接拒绝
- 上传体积、展开总量、文件数量、深度、超时限制
- 加密或损坏压缩包明确失败（archive_encrypted / archive_corrupt），不显示成功

安全：只写入调用方给定的受控临时目录；不执行包内程序/脚本/宏。
Office 文件本身就是 ZIP 容器：本模块不负责判定，调用方必须先按扩展名分派，
.docx/.pptx/.xlsx 绝不走压缩包展开。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

ARCHIVE_EXTS = (".zip", ".7z", ".rar")

# 无关内容判定（按路径分量 / 文件名）
_IGNORED_DIRS = {"__macosx", "$recycle.bin", ".git", ".svn"}
_IGNORED_FILES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"}
_BLOCKED_SUFFIXES = (".exe", ".dll", ".bat", ".cmd", ".ps1", ".sh", ".vbs", ".js",
                     ".msi", ".scr", ".com", ".jar")


class ArchiveError(Exception):
    """压缩包处理失败（带 error_type 与可读原因）"""

    def __init__(self, message: str, error_type: str = "archive_corrupt"):
        super().__init__(message)
        self.error_type = error_type


# ==================== 外部工具探测 ====================


def rar_tool() -> Optional[str]:
    """外部 rar 解压工具（unrar/bsdtar/7z）；找不到返回 None"""
    env_tool = (os.getenv("UNRAR_TOOL") or "").strip()
    if env_tool:
        found = shutil.which(env_tool)
        if not found and Path(env_tool).is_file():
            found = env_tool
        if found:
            return found
    for name in ("unrar", "bsdtar", "7z", "7zz", "unar"):
        found = shutil.which(name)
        if found:
            return found
    return None


def archive_kind(ext: str) -> Optional[str]:
    ext = (ext or "").lower()
    if not ext.startswith("."):
        ext = "." + ext
    return {".zip": "zip", ".7z": "7z", ".rar": "rar"}.get(ext)


def default_limits() -> Dict[str, Any]:
    """从 formats.limits() 取默认限制（惰性导入，避免循环依赖）"""
    try:
        from rag import formats
        caps = formats.limits()
    except Exception:
        caps = {"max_total_mb": 1024, "max_files": 100, "max_depth": 2, "max_seconds": 300}
    return {
        "max_total_bytes": int(caps.get("max_total_mb", 1024)) * 1024 * 1024,
        "max_files": int(caps.get("max_files", 100)),
        "max_depth": int(caps.get("max_depth", 2)),
        "max_seconds": int(caps.get("max_seconds", 300)),
    }


# ==================== 名称处理 ====================


def decode_zip_name(info: zipfile.ZipInfo) -> str:
    """
    还原 zip 中文文件名：按 cp437 取回原始字节，再尝试 UTF-8 / GBK 解码。

    为什么**不**只看 UTF-8 标志位：部分归档器把 GBK 字节当成 UTF-8 写入并置了
    0x800 标志，此时 zipfile 解出的是一串 cp437 乱码；统一走"cp437 取原始字节
    再解码"能同时覆盖"未置标志的 GBK"和"被误置标志的 GBK"。真正的 UTF-8 中文名
    无法用 cp437 编码，会原样返回。
    """
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def normalize_relpath(name: str) -> str:
    """把压缩包内路径规范成 posix 相对路径；非法（绝对/穿越/NUL）返回空串"""
    if not name or "\0" in name:
        return ""
    text = name.replace("\\", "/").strip()
    if not text:
        return ""
    if text.startswith("/"):
        return ""
    if len(text) >= 2 and text[1] == ":":
        return ""
    parts: List[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return ""
        parts.append(part)
    return "/".join(parts)


def is_ignored(rel_path: str) -> Optional[str]:
    """是否应忽略；返回原因或 None"""
    parts = rel_path.split("/")
    lower_parts = [p.lower() for p in parts]
    if any(p in _IGNORED_DIRS for p in lower_parts[:-1]):
        return "系统元数据目录（__MACOSX 等）"
    name = lower_parts[-1] if lower_parts else ""
    if name in _IGNORED_DIRS:
        return "系统元数据目录（__MACOSX 等）"
    if name in _IGNORED_FILES:
        return "系统/临时文件（Thumbs.db / desktop.ini 等）"
    if name.startswith("."):
        return "隐藏文件（以 . 开头）"
    if name.startswith("~$"):
        return "Office 临时文件（~$ 开头）"
    if name.endswith(".tmp"):
        return "临时文件（.tmp）"
    if name.endswith(_BLOCKED_SUFFIXES):
        return "可执行/脚本文件（安全限制，不导入）"
    return None


def _display_name(rel_path: str) -> str:
    """把相对路径扁平化成不冲突的展示名：第1章/课件.pptx -> 第1章__课件.pptx"""
    return rel_path.replace("/", "__")


# 条目：(相对路径, 读取函数, 声明大小, 跳过原因)
Member = Tuple[str, Callable[[], bytes], int, Optional[str]]


# ==================== 主流程 ====================


def extract_archive(archive_path: str, dest_dir: str,
                    limits: Optional[Dict[str, Any]] = None,
                    password: Optional[str] = None) -> Dict[str, Any]:
    """
    解压一个压缩包到受控目录（含有限层级的嵌套压缩包）。

    Returns:
        {
          "entries": [ {source_path, abs_path, size, origin_archive, depth, display_name} ],
          "skipped": [ {source_path, reason, error_type} ],
          "nested":  [ {"archive": rel, "depth": n, "extracted": m, "skipped": k} ],
          "summary": {archive, kind, extracted, skipped, bytes, note},
        }

    Raises:
        ArchiveError: 不支持 / 加密 / 损坏 / 越限 / 路径不安全
    """
    src = Path(archive_path)
    if not src.exists() or not src.is_file():
        raise ArchiveError(f"压缩包不存在: {archive_path}", error_type="io_error")

    kind = archive_kind(src.suffix)
    if kind is None:
        raise ArchiveError(f"不支持的压缩包类型: {src.suffix}", error_type="archive_unsupported")

    if password:
        # 支持密码时绝不记录密码；仅在内存中传递
        logger.info("压缩包使用密码解压（密码不落盘、不记日志）: %s", src.name)

    limits = {**default_limits(), **(limits or {})}
    dest_root = Path(dest_dir)
    dest_root.mkdir(parents=True, exist_ok=True)

    state: Dict[str, Any] = {
        "bytes": 0,
        "files": 0,
        "start": time.monotonic(),
        "entries": [],
        "skipped": [],
        "nested": [],
    }

    _extract_one(src, dest_root, origin=src.name, depth=0, limits=limits,
                 state=state, password=password, rel_prefix="")

    extracted = len(state["entries"])
    skipped = len(state["skipped"])
    total_nested = sum(n["extracted"] for n in state["nested"])
    note_parts = []
    if skipped:
        note_parts.append(f"已忽略 {skipped} 个无关/不安全条目")
    if state["nested"]:
        note_parts.append(f"展开 {len(state['nested'])} 个嵌套压缩包（{total_nested} 个文件）")
    if not note_parts:
        note_parts.append("无跳过内容")

    return {
        "entries": state["entries"],
        "skipped": state["skipped"],
        "nested": state["nested"],
        "summary": {
            "archive": src.name,
            "kind": kind,
            "extracted": extracted,
            "skipped": skipped,
            "bytes": state["bytes"],
            "note": "；".join(note_parts),
        },
    }


def _check_limits(state: Dict[str, Any], limits: Dict[str, Any],
                  extra_bytes: int = 0, extra_files: int = 0) -> None:
    if time.monotonic() - state["start"] > limits["max_seconds"]:
        raise ArchiveError(
            f"解压超时（>{limits['max_seconds']}s），已停止",
            error_type="archive_limit_exceeded")
    if state["files"] + extra_files > limits["max_files"]:
        raise ArchiveError(
            f"压缩包内文件数超过限制（>{limits['max_files']}），已停止",
            error_type="archive_limit_exceeded")
    if state["bytes"] + extra_bytes > limits["max_total_bytes"]:
        raise ArchiveError(
            f"解压总量超过限制（>{limits['max_total_bytes'] // (1024 * 1024)}MB），已停止",
            error_type="archive_limit_exceeded")


def _extract_one(src: Path, dest_root: Path, origin: str, depth: int,
                 limits: Dict[str, Any], state: Dict[str, Any],
                 password: Optional[str], rel_prefix: str) -> None:
    kind = archive_kind(src.suffix)
    if kind == "zip":
        members = _iter_zip(src, password)
    elif kind == "7z":
        members = _iter_7z(src, password)
    elif kind == "rar":
        members = _iter_rar(src, password)
    else:
        raise ArchiveError(f"不支持的压缩包类型: {src.suffix}", error_type="archive_unsupported")

    target_root = dest_root / f"{src.stem}_{uuid.uuid4().hex[:8]}"
    target_root.mkdir(parents=True, exist_ok=True)

    for raw_rel, reader, _declared, member_skip in members:
        if raw_rel.endswith("/"):
            continue
        safe_rel = normalize_relpath(raw_rel)
        if not safe_rel:
            state["skipped"].append({
                "source_path": raw_rel,
                "reason": "绝对路径或路径穿越（安全拒绝）",
                "error_type": "archive_unsafe_path",
            })
            continue

        rel_prefix_full = f"{rel_prefix}{safe_rel}" if rel_prefix else safe_rel

        if member_skip:
            state["skipped"].append({"source_path": rel_prefix_full, "reason": member_skip,
                                     "error_type": "archive_unsafe_path"})
            continue

        reason = is_ignored(safe_rel)
        if reason:
            state["skipped"].append({"source_path": rel_prefix_full, "reason": reason,
                                     "error_type": None})
            continue

        ext = Path(safe_rel).suffix.lower()

        # 嵌套压缩包（有限层级）
        if ext in ARCHIVE_EXTS:
            if depth + 1 > limits["max_depth"]:
                state["skipped"].append({
                    "source_path": rel_prefix_full,
                    "reason": f"嵌套压缩包超过层级限制（max_depth={limits['max_depth']}）",
                    "error_type": "archive_nested_limit",
                })
                continue
            nested_path = target_root / (uuid.uuid4().hex[:8] + ext)
            try:
                data = reader()
            except ArchiveError:
                raise
            except Exception as e:
                raise ArchiveError(f"读取嵌套压缩包失败: {safe_rel}: {e}",
                                   error_type="archive_corrupt")
            _check_limits(state, limits, extra_bytes=len(data), extra_files=1)
            nested_path.write_bytes(data)
            before_e = len(state["entries"])
            before_s = len(state["skipped"])
            _extract_one(nested_path, target_root, origin=origin, depth=depth + 1,
                         limits=limits, state=state, password=password,
                         rel_prefix=rel_prefix_full + "::")
            state["nested"].append({
                "archive": rel_prefix_full,
                "depth": depth + 1,
                "extracted": len(state["entries"]) - before_e,
                "skipped": len(state["skipped"]) - before_s,
            })
            continue

        try:
            data = reader()
        except ArchiveError:
            raise
        except Exception as e:
            raise ArchiveError(f"读取压缩包条目失败: {safe_rel}: {e}", error_type="archive_corrupt")

        _check_limits(state, limits, extra_bytes=len(data), extra_files=1)

        out_dir = target_root / uuid.uuid4().hex[:8]
        out_dir.mkdir(parents=True, exist_ok=True)
        leaf = Path(safe_rel).name or "file"
        written = out_dir / leaf
        written.write_bytes(data)

        state["bytes"] += len(data)
        state["files"] += 1
        state["entries"].append({
            "source_path": rel_prefix_full,
            "abs_path": str(written),
            "size": len(data),
            "origin_archive": origin,
            "depth": depth,
            "display_name": _display_name(safe_rel) if depth == 0 else _display_name(rel_prefix_full),
        })


# ==================== 各格式条目迭代 ====================


def _iter_zip(src: Path, password: Optional[str]) -> Iterator[Member]:
    try:
        zf = zipfile.ZipFile(src)
    except zipfile.BadZipFile as e:
        raise ArchiveError(f"损坏的 ZIP 压缩包: {e}", error_type="archive_corrupt")
    except Exception as e:
        raise ArchiveError(f"无法打开 ZIP 压缩包: {e}", error_type="archive_corrupt")

    with zf:
        infos = zf.infolist()
        if len(infos) > 20000:
            raise ArchiveError(f"压缩包内条目过多（{len(infos)}），已拒绝",
                               error_type="archive_limit_exceeded")
        for info in infos:
            rel = decode_zip_name(info)
            if info.is_dir():
                yield rel.rstrip("/") + "/", _empty_reader, 0, None
                continue
            if info.flag_bits & 0x1:
                raise ArchiveError("压缩包已加密，且未提供密码（或压缩包不支持该密码）",
                                   error_type="archive_encrypted")
            if _is_zip_symlink(info):
                yield rel, _empty_reader, info.file_size, "符号链接（安全限制，不导入）"
                continue

            def _read(_info=info, _zf=zf, _pwd=password) -> bytes:
                with _zf.open(_info, pwd=_pwd.encode() if _pwd else None) as fh:
                    return fh.read()

            yield rel, _read, info.file_size, None


def _empty_reader() -> bytes:
    return b""


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _iter_7z(src: Path, password: Optional[str]) -> Iterator[Member]:
    try:
        import py7zr
    except ImportError:
        raise ArchiveError("缺少 py7zr，无法解压 .7z", error_type="archive_unsupported")

    try:
        archive = py7zr.SevenZipFile(str(src), mode="r", password=password)
    except Exception as e:
        msg = str(e).lower()
        if "password" in msg or "encrypted" in msg:
            raise ArchiveError("7z 压缩包已加密，需要密码", error_type="archive_encrypted")
        raise ArchiveError(f"7z 压缩包损坏或无法打开: {e}", error_type="archive_corrupt")

    # py7zr 1.x 移除了 read()，只能先把**安全**成员解压到临时目录再读取。
    # 不安全/被忽略的成员不进入 extract targets，避免任何落盘。
    staging = tempfile.mkdtemp(prefix="rag_7z_")
    try:
        with archive:
            try:
                names = archive.getnames()
            except Exception as e:
                msg = str(e).lower()
                if "password" in msg:
                    raise ArchiveError("7z 压缩包已加密，需要密码",
                                       error_type="archive_encrypted")
                raise ArchiveError(f"读取 7z 目录失败: {e}", error_type="archive_corrupt")

            safe_targets = []
            for name in names:
                if name.endswith("/"):
                    continue
                safe_rel = normalize_relpath(name)
                if safe_rel and not is_ignored(safe_rel):
                    safe_targets.append(name)
            if safe_targets:
                try:
                    archive.extract(path=staging, targets=safe_targets)
                except Exception as e:
                    msg = str(e).lower()
                    if "password" in msg:
                        raise ArchiveError("7z 压缩包已加密，需要密码",
                                           error_type="archive_encrypted")
                    raise ArchiveError(f"解压 7z 失败: {e}", error_type="archive_corrupt")

            for name in names:
                if name.endswith("/"):
                    yield name, _empty_reader, 0, None
                    continue
                safe_rel = normalize_relpath(name)
                if not safe_rel:
                    # 由 _extract_one 统一记为路径穿越
                    yield name, _empty_reader, 0, None
                    continue
                path = Path(staging) / safe_rel
                if not path.exists():
                    yield name, _empty_reader, 0, None
                    continue
                size = path.stat().st_size

                def _read(_p=path) -> bytes:
                    return _p.read_bytes()

                yield name, _read, size, None
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _iter_rar(src: Path, password: Optional[str]) -> Iterator[Member]:
    try:
        import rarfile
    except ImportError:
        raise ArchiveError("缺少 rarfile，无法解压 .rar", error_type="archive_unsupported")

    tool = rar_tool()
    if not tool:
        raise ArchiveError(
            "缺少外部 unrar/bsdtar/7z 工具，无法解压 .rar；"
            "请安装 WinRAR/unrar 并加入 PATH",
            error_type="archive_unsupported")
    _configure_rarfile(rarfile, tool)

    try:
        rf = rarfile.RarFile(str(src))
    except rarfile.BadRarFile as e:
        raise ArchiveError(f"损坏的 RAR 压缩包: {e}", error_type="archive_corrupt")
    except rarfile.NeedFirstVolume as e:
        raise ArchiveError(f"RAR 分卷不完整: {e}", error_type="archive_corrupt")
    except Exception as e:
        raise ArchiveError(f"无法打开 RAR 压缩包: {e}", error_type="archive_corrupt")

    with rf:
        for info in rf.infolist():
            if info.isdir():
                yield info.filename.rstrip("/") + "/", _empty_reader, 0, None
                continue
            if info.needs_password():
                raise ArchiveError("RAR 压缩包已加密，需要密码",
                                   error_type="archive_encrypted")
            if info.is_symlink():
                yield info.filename, _empty_reader, info.file_size, "符号链接（安全限制，不导入）"
                continue

            def _read(_info=info, _rf=rf, _pwd=password) -> bytes:
                return _rf.read(_info, pwd=_pwd)

            yield info.filename, _read, info.file_size, None


def _configure_rarfile(rarfile: Any, tool: str) -> None:
    name = Path(tool).name.lower()
    if "bsdtar" in name:
        rarfile.BSDTAR_TOOL = tool
    elif "7z" in name:
        rarfile.SEVENZIP_TOOL = tool
    elif "unar" in name:
        rarfile.UNAR_TOOL = tool
    else:
        rarfile.UNRAR_TOOL = tool


def cleanup(dest_dir: str, root: Optional[str] = None) -> None:
    """清理解压临时目录（限定在给定 root 内，避免误删已有资料）"""
    if not dest_dir:
        return
    try:
        target = Path(dest_dir).resolve()
        if root:
            root_path = Path(root).resolve()
            if target == root_path or root_path not in target.parents:
                logger.warning("拒绝清理受控根之外的目录: %s", target)
                return
        shutil.rmtree(target, ignore_errors=True)
    except Exception as e:
        logger.warning("清理解压临时目录失败（不阻断主流程）: %s", e)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


__all__ = [
    "ARCHIVE_EXTS",
    "ArchiveError",
    "archive_kind",
    "cleanup",
    "decode_zip_name",
    "default_limits",
    "extract_archive",
    "is_ignored",
    "normalize_relpath",
    "rar_tool",
    "sha256_file",
]
