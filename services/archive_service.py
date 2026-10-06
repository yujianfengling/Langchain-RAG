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
import subprocess
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


def _kind_for_tool(path: str) -> str:
    """按可执行文件路径/名称判定后端类型"""
    name = Path(path).name.lower()
    if "7zz" in name:
        return "7zz"
    if "7z" in name:
        return "7z"
    if "bsdtar" in name:
        return "bsdtar"
    if name in ("tar", "tar.exe"):
        # Windows 自带的 C:\Windows\System32\tar.exe 就是 bsdtar
        return "bsdtar"
    if "unar" in name:
        return "unar"
    return "unrar"


def _system_bsdtar() -> Optional[str]:
    """系统自带 bsdtar（Windows 10+ 的 %SystemRoot%\\System32\\tar.exe）"""
    root = (os.getenv("SystemRoot") or os.getenv("windir") or r"C:\Windows").strip()
    candidate = Path(root) / "System32" / "tar.exe"
    try:
        if candidate.is_file():
            return str(candidate)
    except OSError:
        return None
    return None


def rar_backend() -> Dict[str, Optional[str]]:
    """
    探测可用的 rar 解压后端，返回 {"tool": 路径或 None, "kind": 类型或 None}。

    优先级：env UNRAR_TOOL → PATH 上的 unrar / unar / 7z / 7zz →
    系统自带 bsdtar（%SystemRoot%\\System32\\tar.exe）。
    """
    env_tool = (os.getenv("UNRAR_TOOL") or "").strip()
    if env_tool:
        found = shutil.which(env_tool)
        if not found:
            try:
                if Path(env_tool).is_file():
                    found = env_tool
            except OSError:
                found = None
        if found:
            return {"tool": found, "kind": _kind_for_tool(found)}
    for name in ("unrar", "unar", "7z", "7zz"):
        found = shutil.which(name)
        if found:
            return {"tool": found, "kind": _kind_for_tool(found)}
    tar = shutil.which("bsdtar") or _system_bsdtar()
    if tar:
        return {"tool": tar, "kind": "bsdtar"}
    return {"tool": None, "kind": None}


def rar_tool() -> Optional[str]:
    """外部 rar 解压工具路径（unrar/unar/7z/bsdtar）；找不到返回 None"""
    return rar_backend().get("tool")


def rar_tool_kind() -> Optional[str]:
    """当前 rar 后端类型（unrar / unar / 7z / 7zz / bsdtar）；无后端返回 None"""
    return rar_backend().get("kind")


def archive_kind(ext: str) -> Optional[str]:
    ext = (ext or "").lower()
    if not ext.startswith("."):
        ext = "." + ext
    return {".zip": "zip", ".7z": "7z", ".rar": "rar"}.get(ext)


def default_limits() -> Dict[str, Any]:
    """从 formats 的单一来源取默认限制（惰性导入，避免循环依赖）"""
    try:
        from rag import formats
        return formats.extract_limits_bytes()
    except Exception:
        mb = 1024 * 1024
        return {
            "max_total_bytes": 1024 * mb,
            "max_extract_file_bytes": 200 * mb,
            "max_files": 100,
            "max_depth": 2,
            "max_seconds": 300,
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


class _NameInfo:
    """py7zr 老版本没有 list() 时的最小成员信息（无法判定目录，交给解压后兜底）"""

    def __init__(self, filename: str):
        self.filename = filename
        self.is_directory = False


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
        # 目录成员数（任务 4：分类计数；目录不是资料，也不读取、不报错）
        "directories": 0,
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
        "directories": state["directories"],
        "containers": 1 + len(state["nested"]),
        "summary": {
            "archive": src.name,
            "kind": kind,
            "extracted": extracted,
            "skipped": skipped,
            "bytes": state["bytes"],
            "note": "；".join(note_parts),
        },
    }


def _check_member_size(size: int, member: str, limits: Dict[str, Any]) -> None:
    """单个成员大小限制（展开后单文件上限，默认 200MB）"""
    limit = int(limits.get("max_extract_file_bytes") or 0)
    if limit and size > limit:
        actual_mb = round(size / (1024 * 1024), 2)
        limit_mb = limit // (1024 * 1024)
        raise ArchiveError(
            f"压缩包内单个文件超过限制：{member} 实际 {actual_mb}MB，上限 {limit_mb}MB",
            error_type="archive_limit_exceeded")


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
        actual_mb = round((state["bytes"] + extra_bytes) / (1024 * 1024), 2)
        limit_mb = limits["max_total_bytes"] // (1024 * 1024)
        raise ArchiveError(
            f"解压总量超过限制（实际 {actual_mb}MB，上限 {limit_mb}MB），已停止",
            error_type="archive_limit_exceeded")


def _make_staging(staging_root: Optional[Path], prefix: str) -> Path:
    """
    在**受控目录**下创建解包暂存目录，绝不再用系统 TEMP。

    2026-10-07 实测事故：287MB 的 .7z 因系统 TEMP
    （C 盘用户目录下的 Temp\rag_7z_xxx）创建中文目录 "新建文件夹" 时报
    [Errno 13] Permission denied，被误判成 archive_corrupt。
    系统 TEMP 在多用户/受限环境下 ACL 不可控；本项目的安全约定也只允许写入
    受控临时目录与学科目录。这里按优先级尝试：
      1) 调用方给定的受控目录（任务自己的 import_tmp/<job>/extracted）；
      2) <data_dir>/import_tmp/archive_tmp/；
      3) 最后才退回系统 TEMP（仅当上面都不可写时，保证功能不因环境差异全坏）。
    """
    attempts: List[Path] = []
    if staging_root is not None:
        attempts.append(Path(staging_root) / f".{prefix}{uuid.uuid4().hex[:10]}")
    try:
        from config.path_config import get_data_dir
        attempts.append(Path(get_data_dir()) / "import_tmp" / "archive_tmp"
                        / f".{prefix}{uuid.uuid4().hex[:10]}")
    except Exception:
        pass

    for candidate in attempts:
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
            except Exception as e:  # noqa: PERF203 - 逐个候选尝试
                logger.warning("受控暂存目录不可用 %s: %s", candidate, e)
        except Exception as e:
            logger.warning("受控暂存目录不可用 %s: %s", candidate, e)

    # 兜底：仍使用系统 TEMP，但把失败原因写清楚
    try:
        return Path(tempfile.mkdtemp(prefix=prefix))
    except Exception as e:
        raise ArchiveError(f"无法创建解包暂存目录: {e}", error_type="archive_corrupt")


def _extract_one(src: Path, dest_root: Path, origin: str, depth: int,
                 limits: Dict[str, Any], state: Dict[str, Any],
                 password: Optional[str], rel_prefix: str) -> None:
    kind = archive_kind(src.suffix)
    if kind == "zip":
        members = _iter_zip(src, password)
    elif kind == "7z":
        members = _iter_7z(src, password, dest_root)
    elif kind == "rar":
        members = _iter_rar(src, password, dest_root)
    else:
        raise ArchiveError(f"不支持的压缩包类型: {src.suffix}", error_type="archive_unsupported")

    target_root = dest_root / f"{src.stem}_{uuid.uuid4().hex[:8]}"
    target_root.mkdir(parents=True, exist_ok=True)

    for raw_rel, reader, _declared, member_skip in members:
        if raw_rel.endswith("/"):
            # 目录成员：只计数，绝不当文件读取（真实事故：目录被 read_bytes 抛
            # Permission denied 并被误判成 archive_corrupt）。
            state["directories"] += 1
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
            except ArchiveError as e:
                # 单个嵌套包读不出来只跳过它，其余成员继续（不拖垮整包）
                state["skipped"].append({
                    "source_path": rel_prefix_full,
                    "reason": f"嵌套压缩包读取失败（已跳过，其余继续）：{e}",
                    "error_type": getattr(e, "error_type", "archive_member_unreadable")})
                continue
            except Exception as e:
                state["skipped"].append({
                    "source_path": rel_prefix_full,
                    "reason": f"嵌套压缩包读取失败（已跳过，其余继续）：{e}",
                    "error_type": "archive_member_unreadable"})
                continue
            _check_member_size(len(data), rel_prefix_full, limits)
            _check_limits(state, limits, extra_bytes=len(data), extra_files=1)
            try:
                nested_path.write_bytes(data)
            except OSError as e:
                state["skipped"].append({
                    "source_path": rel_prefix_full,
                    "reason": f"嵌套压缩包写入失败（已跳过，其余继续）：{e}",
                    "error_type": ("archive_disk_full"
                                   if getattr(e, "errno", None) == 28
                                   else "archive_member_unreadable")})
                continue
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
        except ArchiveError as e:
            # 单成员读取失败：只让该成员 skipped/failed，其余继续，不误报 archive_corrupt
            state["skipped"].append({
                "source_path": rel_prefix_full,
                "reason": f"成员读取失败（已跳过，其余继续）：{e}",
                "error_type": getattr(e, "error_type", "archive_member_unreadable")})
            continue
        except Exception as e:
            state["skipped"].append({
                "source_path": rel_prefix_full,
                "reason": f"成员读取失败（已跳过，其余继续）：{e}",
                "error_type": "archive_member_unreadable"})
            continue

        _check_member_size(len(data), rel_prefix_full, limits)
        _check_limits(state, limits, extra_bytes=len(data), extra_files=1)

        out_dir = target_root / uuid.uuid4().hex[:8]
        out_dir.mkdir(parents=True, exist_ok=True)
        leaf = Path(safe_rel).name or "file"
        written = out_dir / leaf
        try:
            written.write_bytes(data)
        except OSError as e:
            state["skipped"].append({
                "source_path": rel_prefix_full,
                "reason": f"成员写入失败（已跳过，其余继续）：{e}",
                "error_type": ("archive_disk_full"
                               if getattr(e, "errno", None) == 28
                               else "archive_member_unreadable")})
            continue

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


def _iter_7z(src: Path, password: Optional[str],
             staging_root: Optional[Path] = None) -> Iterator[Member]:
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
    #
    # 2026-10-07：暂存目录不再放系统 TEMP（实测 287MB .7z 在系统 TEMP 下创建中文目录
    # 报 Permission denied，被误判成 archive_corrupt）。优先用调用方给的受控目录。
    staging = _make_staging(staging_root, "rag_7z_")
    try:
        with archive:
            try:
                infos = list(archive.list())
            except Exception as e:
                msg = str(e).lower()
                if "password" in msg or "encrypted" in msg:
                    raise ArchiveError("7z 压缩包已加密，需要密码",
                                       error_type="archive_encrypted")
                # 老版本 py7zr 没有 list()：退回 getnames；目录判定由解压后的
                # path.is_dir() 兜底，避免旧环境重新踩"目录被当文件读"的坑。
                logger.warning("7z list() 不可用，退回 getnames: %s", e)
                try:
                    infos = [_NameInfo(n) for n in archive.getnames()]
                except Exception as e2:
                    raise ArchiveError(f"读取 7z 目录失败: {e2}",
                                       error_type="archive_corrupt")

            # 目录判定必须用**成员元数据**（FileInfo.is_directory），绝不能用
            # "结尾是否 /"：py7zr 的 getnames() 对目录不带结尾斜杠，真实事故里
            # 12 个目录被当成文件加进 extract targets 并 read_bytes()，
            # 在 Windows 抛 Permission denied，最后被包装成 archive_corrupt。
            file_infos: List[Any] = []
            dir_names: List[str] = []
            for info in infos:
                name = getattr(info, "filename", "") or ""
                if not name:
                    continue
                if getattr(info, "is_directory", None) or name.endswith("/"):
                    dir_names.append(name)
                    continue
                file_infos.append(info)

            safe_targets: List[str] = []
            for info in file_infos:
                name = info.filename
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
                    if isinstance(e, PermissionError) or "permission denied" in msg:
                        # 解包目标不可写 ≠ 压缩包损坏：给出准确原因与暂存目录
                        raise ArchiveError(
                            f"解包暂存目录不可写（{staging}）：{e}",
                            error_type="archive_staging_unwritable")
                    if "no space" in msg or "disk full" in msg:
                        raise ArchiveError(f"磁盘空间不足: {e}",
                                           error_type="archive_disk_full")
                    raise ArchiveError(f"解压 7z 失败: {e}", error_type="archive_corrupt")
            if dir_names:
                logger.info("7z 目录成员只计数不读取（不计入失败资料）: dirs=%d files=%d",
                            len(dir_names), len(file_infos))

            for info in file_infos:
                name = info.filename
                safe_rel = normalize_relpath(name)
                if not safe_rel:
                    # 由 _extract_one 统一记为路径穿越
                    yield name, _empty_reader, 0, None
                    continue
                path = Path(staging) / safe_rel
                try:
                    # 解压后再挡一层：目录绝不读取
                    if not path.exists() or path.is_dir():
                        logger.warning("7z 成员解压后不存在或是目录，跳过: %s", name)
                        continue
                    size = path.stat().st_size
                except OSError as e:
                    logger.warning("7z 成员状态读取失败，跳过: %s: %s", name, e)
                    continue

                def _read(_p=path, _name=name) -> bytes:
                    try:
                        if _p.is_dir():
                            raise ArchiveError(
                                f"压缩包成员是目录，不应读取: {_name}",
                                error_type="archive_member_unreadable")
                        return _p.read_bytes()
                    except ArchiveError:
                        raise
                    except PermissionError as e:
                        raise ArchiveError(
                            f"压缩包成员不可读（权限）: {_name}: {e}",
                            error_type="archive_member_unreadable")
                    except OSError as e:
                        raise ArchiveError(
                            f"读取压缩包成员失败: {_name}: {e}",
                            error_type="archive_member_unreadable")

                yield name, _read, size, None

            # 目录成员只如实计数（以 / 结尾 yielded），由 _extract_one 计数后跳过，
            # 绝不作为文件读取。
            for dname in dir_names:
                yield dname.rstrip("/") + "/", _empty_reader, 0, None
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _rar_tool_error(stderr: str) -> ArchiveError:
    """把 bsdtar 的失败输出翻译成结构化原因（不假成功）"""
    text = (stderr or "").lower()
    if "encryption is not supported" in text or "password" in text or "encrypted" in text:
        return ArchiveError(
            "RAR 压缩包已加密，当前后端（bsdtar）不支持解密；"
            "请安装 WinRAR/unrar 或 7-Zip 后重试",
            error_type="archive_encrypted")
    if "solid archive" in text:
        return ArchiveError(
            "RAR 为 solid（固实）压缩包，当前 bsdtar 后端不支持；"
            "请安装 unrar/7-Zip 后重试",
            error_type="archive_unsupported")
    detail = (stderr or "").strip()[-400:]
    return ArchiveError(f"bsdtar 解压 RAR 失败：{detail or '未知错误'}",
                        error_type="archive_corrupt")


def _iter_rar_via_bsdtar(src: Path, tool: str, timeout: int,
                         staging_root: Optional[Path] = None) -> Iterator[Member]:
    """直接用 bsdtar 解压到受控暂存目录再读取（rarfile 的 bsdtar 读取路径实测不可靠）"""
    staging = _make_staging(staging_root, "rag_rar_")
    try:
        try:
            proc = subprocess.run([tool, "-xf", str(src), "-C", staging],
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ArchiveError(f"bsdtar 解压 RAR 超时（>{timeout}s）",
                               error_type="archive_limit_exceeded")
        except OSError as e:
            raise ArchiveError(f"无法启动 bsdtar（{tool}）：{e}",
                               error_type="archive_unsupported")
        if proc.returncode != 0:
            raise _rar_tool_error(proc.stderr or proc.stdout or "")
        for root, _dirs, files in os.walk(staging):
            for fname in sorted(files):
                fpath = Path(root) / fname
                rel = fpath.relative_to(staging).as_posix()
                size = fpath.stat().st_size

                def _read(_p=fpath) -> bytes:
                    return _p.read_bytes()

                yield rel, _read, size, None
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _rar_read_error(exc: Exception, name: str) -> ArchiveError:
    msg = str(exc).lower()
    if "password" in msg or "encrypt" in msg:
        return ArchiveError(f"RAR 条目已加密，无法读取：{name}",
                            error_type="archive_encrypted")
    if "solid" in msg:
        return ArchiveError(f"RAR 为 solid 压缩包，当前后端不支持：{name}",
                            error_type="archive_unsupported")
    return ArchiveError(f"读取 RAR 条目失败: {name}: {exc}", error_type="archive_corrupt")


def _iter_rar(src: Path, password: Optional[str],
             staging_root: Optional[Path] = None) -> Iterator[Member]:
    try:
        import rarfile
    except ImportError:
        raise ArchiveError("缺少 rarfile，无法解压 .rar", error_type="archive_unsupported")

    backend = rar_backend()
    tool = backend.get("tool")
    kind = backend.get("kind")
    if not tool:
        raise ArchiveError(
            "缺少外部解压后端（unrar/unar/7z/7zz 或系统 bsdtar），无法解压 .rar；"
            "Windows 10+ 自带 System32 下的 tar.exe（bsdtar），"
            "若缺失请安装 WinRAR/unrar 并加入 PATH",
            error_type="archive_unsupported")
    _configure_rarfile(rarfile, tool, kind)

    try:
        rf = rarfile.RarFile(str(src))
    except rarfile.BadRarFile as e:
        msg = str(e).lower()
        if "encrypt" in msg or "password" in msg:
            raise ArchiveError("RAR 压缩包已加密，需要密码",
                               error_type="archive_encrypted")
        raise ArchiveError(f"损坏的 RAR 压缩包: {e}", error_type="archive_corrupt")
    except rarfile.NeedFirstVolume as e:
        raise ArchiveError(f"RAR 分卷不完整: {e}", error_type="archive_corrupt")
    except Exception as e:
        msg = str(e).lower()
        if "encrypt" in msg or "password" in msg:
            raise ArchiveError("RAR 压缩包已加密，需要密码",
                               error_type="archive_encrypted")
        if "solid" in msg:
            raise ArchiveError(f"RAR 为 solid 压缩包，当前后端不支持：{e}",
                               error_type="archive_unsupported")
        raise ArchiveError(f"无法打开 RAR 压缩包: {e}", error_type="archive_corrupt")

    with rf:
        try:
            needs_password = bool(rf.needs_password())
        except Exception:
            needs_password = False
        if needs_password and not password:
            raise ArchiveError("RAR 压缩包已加密，需要密码（当前未提供密码）",
                               error_type="archive_encrypted")

        if kind == "bsdtar":
            timeout = int(default_limits()["max_seconds"])
            yield from _iter_rar_via_bsdtar(src, tool, timeout, staging_root)
            return

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
                try:
                    return _rf.read(_info, pwd=_pwd)
                except Exception as e:  # 读失败要给出准确原因，不能当成成功
                    raise _rar_read_error(e, _info.filename)

            yield info.filename, _read, info.file_size, None


def _configure_rarfile(rarfile: Any, tool: str,
                       kind: Optional[str] = None) -> None:
    """把探测到的后端路径写入 rarfile 的对应外部工具常量"""
    kind = kind or _kind_for_tool(tool)
    if kind == "bsdtar":
        rarfile.BSDTAR_TOOL = tool
    elif kind == "7zz":
        rarfile.SEVENZIP2_TOOL = tool
    elif kind == "7z":
        rarfile.SEVENZIP_TOOL = tool
    elif kind == "unar":
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
    "rar_backend",
    "rar_tool",
    "rar_tool_kind",
    "sha256_file",
]
