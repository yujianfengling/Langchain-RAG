"""
批量导入任务服务（2026-10-07）

职责：
- 后台线程执行导入（不阻塞请求）；任务与条目落 SQLite（data/import_jobs.db）
- 状态机：job=queued|running|succeeded|partial|failed|cancelled
          item=pending|parsing|indexing|ready|failed|skipped
- 一批资料**统一提交一次建库**（复用 knowledge_service 的"备份→构建→失败回滚"）
- 失败不让已有知识库变空；部分成功区分"本批结果"与"知识库可用状态"
- 重试幂等：同名同内容跳过，不产生重复资料/索引；同名不同内容不静默覆盖
- 压缩包先展开（archive_service），Office 容器（docx/pptx/xlsx）绝不走压缩包展开

安全：只写受控临时目录（data/import_tmp/<job_id>）与当前学科资料目录；
绝不执行包内程序；失败/取消清理临时内容，不误删已有资料。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config.path_config import get_data_dir

logger = logging.getLogger(__name__)

_DB_LOCK = threading.Lock()

JOB_STATUSES = ("queued", "running", "succeeded", "partial", "failed", "cancelled")
ITEM_STATUSES = ("pending", "parsing", "indexing", "ready", "failed", "skipped")
TERMINAL_JOB_STATUSES = ("succeeded", "partial", "failed", "cancelled")


# ==================== 路径与数据库 ====================


def _db_path() -> Path:
    return get_data_dir() / "import_jobs.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _import_tmp_root() -> Path:
    root = get_data_dir() / "import_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _subject_dir(subject_id: str) -> Path:
    from services.knowledge_service import _resolve_path
    return _resolve_path(f"data/subjects/{subject_id}")


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _init_db() -> None:
    with _DB_LOCK, _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS import_jobs (
            job_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            user_id TEXT,
            status TEXT NOT NULL,
            created_at TEXT, updated_at TEXT, finished_at TEXT,
            totals_json TEXT, warnings_json TEXT, archive_summaries_json TEXT,
            kb_json TEXT, summary_text TEXT,
            retry_of TEXT, cancel_requested INTEGER DEFAULT 0, raw_dir TEXT
        );
        CREATE TABLE IF NOT EXISTS import_items (
            item_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            seq INTEGER,
            display_name TEXT, source_path TEXT, origin_archive TEXT,
            size INTEGER, status TEXT, stage TEXT, progress REAL,
            message TEXT, doc_id TEXT, error_type TEXT, retryable INTEGER DEFAULT 0,
            stored_path TEXT, content_sha TEXT
        );
        CREATE TABLE IF NOT EXISTS import_raw (
            job_id TEXT, seq INTEGER, filename TEXT, stored_path TEXT, size INTEGER,
            PRIMARY KEY (job_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_import_items_job ON import_items(job_id, seq);
        CREATE INDEX IF NOT EXISTS idx_import_jobs_subject ON import_jobs(subject_id, created_at);
        """)


# ==================== 上传校验 ====================


def _limits() -> Dict[str, Any]:
    from rag import formats
    return formats.limits()


def _classify_upload(filename: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    返回 (ext, reason, error_type)；reason 非空表示拒绝。
    """
    from rag import formats

    raw = (filename or "").replace("\\", "/")
    base = Path(raw).name
    if not base or base in (".", ".."):
        return None, "文件名不合法", "unsupported_format"
    if ".." in base:
        return None, "文件名包含路径穿越", "archive_unsafe_path"
    ext = formats.normalize_ext(Path(base).suffix)
    if not ext:
        return None, "文件必须包含扩展名", "unsupported_format"

    spec = formats.get_format(ext)
    if spec is None:
        return ext, f"不支持的文件类型: {ext}", "unsupported_format"
    if not spec.available:
        reason = formats.unavailable_reason(ext)
        return ext, reason, "converter_unavailable" if spec.requires == "libreoffice" else "unsupported_format"
    return ext, None, None


def create_import_job(subject_id: str, files: Sequence[Tuple[str, bytes]],
                      user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    创建导入任务并立刻返回（解析/建库在后台线程）。

    Args:
        subject_id: 学科 ID
        files: [(filename, content_bytes), ...]
        user_id: 发起人（用于课程资料登记）

    Returns:
        {"job_id", "accepted", "rejected": [{"filename","reason","error_type"}]}
    """
    _init_db()
    limits = _limits()
    max_file_bytes = limits["max_file_mb"] * 1024 * 1024
    max_total_bytes = limits["max_total_mb"] * 1024 * 1024

    rejected: List[Dict[str, Any]] = []
    accepted: List[Tuple[str, bytes]] = []
    total = 0

    for filename, content in files:
        ext, reason, etype = _classify_upload(filename)
        if reason:
            rejected.append({"filename": filename, "reason": reason,
                             "error_type": etype})
            continue
        if len(content) == 0:
            rejected.append({"filename": filename, "reason": "文件内容为空",
                             "error_type": "empty_content"})
            continue
        if len(content) > max_file_bytes:
            rejected.append({
                "filename": filename,
                "reason": f"文件超过单文件上限 {limits['max_file_mb']}MB",
                "error_type": "size_exceeded"})
            continue
        if total + len(content) > max_total_bytes:
            rejected.append({
                "filename": filename,
                "reason": f"本批总量超过上限 {limits['max_total_mb']}MB",
                "error_type": "size_exceeded"})
            continue
        if len(accepted) >= limits["max_files"]:
            rejected.append({"filename": filename,
                             "reason": f"本批文件数超过上限 {limits['max_files']}",
                             "error_type": "count_exceeded"})
            continue
        total += len(content)
        accepted.append((filename, content))

    job_id = _new_id("imp")
    raw_dir = _import_tmp_root() / job_id / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    raw_rows: List[Tuple[int, str, str, int]] = []
    for seq, (filename, content) in enumerate(accepted):
        base = Path(filename.replace("\\", "/")).name
        safe = _safe_stored_name(base)
        dest = raw_dir / f"{seq:03d}_{safe}"
        dest.write_bytes(content)
        raw_rows.append((seq, base, str(dest), len(content)))

    status = "queued" if accepted else "failed"
    summary_text = ("任务已排队" if accepted
                    else "没有可导入的文件（全部被拒绝）")
    with _DB_LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO import_jobs (job_id, subject_id, user_id, status, created_at,"
            " updated_at, totals_json, warnings_json, archive_summaries_json, kb_json,"
            " summary_text, raw_dir) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, subject_id, user_id, status, _now(), _now(),
             json.dumps({"files": 0, "ready": 0, "failed": 0, "skipped": 0, "bytes": total},
                        ensure_ascii=False),
             json.dumps([], ensure_ascii=False), json.dumps([], ensure_ascii=False),
             "{}", summary_text, str(raw_dir)))
        conn.executemany(
            "INSERT INTO import_raw (job_id, seq, filename, stored_path, size)"
            " VALUES (?,?,?,?,?)",
            [(job_id, seq, fn, sp, sz) for seq, fn, sp, sz in raw_rows])
        conn.commit()

    if accepted:
        t = threading.Thread(target=_run_job_thread, args=(job_id,),
                             name=f"import-{job_id}", daemon=True)
        t.start()

    return {"job_id": job_id, "accepted": len(accepted), "rejected": rejected}


# ==================== 名称安全 / 去重 ====================


_BAD_NAME_CHARS = '\\/:*?"<>|'


def _safe_stored_name(name: str) -> str:
    base = Path((name or "").replace("\\", "/")).name
    cleaned = "".join("_" if ch in _BAD_NAME_CHARS else ch for ch in base).strip(" .")
    if not cleaned:
        cleaned = "file"
    if len(cleaned) > 200:
        stem = Path(cleaned).stem[:180]
        cleaned = stem + Path(cleaned).suffix
    return cleaned


def _sha256(path: Path) -> str:
    from services.archive_service import sha256_file
    try:
        return sha256_file(str(path))
    except Exception:
        return ""


def _decide_target(subject_dir: Path, desired_name: str,
                   content: bytes) -> Dict[str, Any]:
    """
    决定落盘文件名，绝不静默覆盖同名不同内容的已有资料。

    Returns:
        {"name", "skip", "reason", "renamed", "existed"}
    """
    import hashlib
    desired = _safe_stored_name(desired_name)
    target = subject_dir / desired
    if not target.exists():
        return {"name": desired, "skip": False, "reason": None,
                "renamed": False, "existed": False}

    existing_hash = _sha256(target)
    new_hash = hashlib.sha256(content).hexdigest()
    if existing_hash and existing_hash == new_hash:
        return {"name": desired, "skip": True, "reason": "同名同内容，已跳过（不重复导入）",
                "renamed": False, "existed": True}

    stem = Path(desired).stem
    suffix = Path(desired).suffix
    for i in range(2, 200):
        candidate = f"{stem} ({i}){suffix}"
        cand_path = subject_dir / candidate
        if not cand_path.exists():
            return {"name": candidate, "skip": False,
                    "reason": "同名不同内容，已另存为",
                    "renamed": True, "existed": True}
        if _sha256(cand_path) == new_hash:
            return {"name": candidate, "skip": True,
                    "reason": "同名同内容，已跳过（不重复导入）",
                    "renamed": False, "existed": True}
    return {"name": f"{stem}_{uuid.uuid4().hex[:6]}{suffix}", "skip": False,
            "reason": "同名不同内容，已另存为", "renamed": True, "existed": True}


# ==================== 登记 / 解析 ====================


def _register_material(subject_id: str, final_name: str, size: int,
                       user_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    课程学科：登记/更新资料记录，返回 (doc_id, material_id)。
    个人/历史学科：返回 (f:<name>, None)。
    """
    from services import material_scope
    doc_id = material_scope.DOC_PREFIX_FILE + final_name
    try:
        from services import teaching_data as td
        owner = td.subject_owner(subject_id)
        if not owner:
            return doc_id, None
        material = td.upsert_material(
            owner["course_id"], subject_id, final_name,
            size_bytes=size, status="ready", error_message=None,
            visibility="students", uploaded_by=user_id)
        mid = material.get("id") if isinstance(material, dict) else None
        if mid:
            return material_scope.DOC_PREFIX_MATERIAL + mid, mid
    except Exception as e:
        logger.warning(f"课程资料登记失败（不影响本次导入，检索期按文件名回退）: {e}")
    return doc_id, None


def _rewrite_metadata(docs: List[Any], final_name: str, final_path: Path) -> None:
    for doc in docs:
        meta = getattr(doc, "metadata", None)
        if not isinstance(meta, dict):
            continue
        meta["source"] = final_name
        meta["file_name"] = final_name
        meta["source_path"] = str(final_path)


def _parse_file(path: Path) -> Tuple[List[Any], Optional[str], Optional[str]]:
    """返回 (documents, error_type, message)"""
    from rag.document import load_document_result
    result = load_document_result(path)
    if result.success:
        return result.documents, None, None
    return [], result.error_type or "parse_error", result.error_message or "解析失败"


def _collect_existing_documents(subject_id: str, exclude: set) -> List[Any]:
    """解析学科目录下既有文件（排除本批已解析的文件名），避免重复解析本批内容"""
    from services.knowledge_service import supported_document_extensions
    subject_dir = _subject_dir(subject_id)
    if not subject_dir.exists():
        return []
    docs: List[Any] = []
    allowed = supported_document_extensions()
    for f in sorted(subject_dir.iterdir()):
        if not f.is_file() or f.suffix.lower() not in allowed:
            continue
        if f.name in exclude:
            continue
        got, _et, _msg = _parse_file(f)
        docs.extend(got)
    return docs


# ==================== 任务执行 ====================


def _run_job_thread(job_id: str) -> None:
    try:
        _run_job(job_id)
    except Exception as e:
        logger.error(f"导入任务异常: {job_id}: {e}", exc_info=True)
        try:
            _finish_job(job_id, "failed", f"任务执行异常: {e}")
        except Exception:
            logger.error(f"标记任务失败也失败: {job_id}", exc_info=True)


def _job_row(conn: sqlite3.Connection, job_id: str) -> Optional[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM import_jobs WHERE job_id=?", (job_id,))
    return cur.fetchone()


def _is_cancelled(job_id: str) -> bool:
    with _DB_LOCK, _connect() as conn:
        row = conn.execute("SELECT cancel_requested, status FROM import_jobs WHERE job_id=?",
                           (job_id,)).fetchone()
        if row is None:
            return True
        return bool(row["cancel_requested"]) or row["status"] == "cancelled"


def _set_job_status(job_id: str, status: str, **fields: Any) -> None:
    sets = ["status=?", "updated_at=?"]
    values: List[Any] = [status, _now()]
    for key, value in fields.items():
        sets.append(f"{key}=?")
        values.append(value)
    values.append(job_id)
    with _DB_LOCK, _connect() as conn:
        conn.execute(f"UPDATE import_jobs SET {', '.join(sets)} WHERE job_id=?", values)
        conn.commit()


def _update_item(item_id: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [item_id]
    with _DB_LOCK, _connect() as conn:
        conn.execute(f"UPDATE import_items SET {sets} WHERE item_id=?", values)
        conn.commit()


def _insert_item(job_id: str, seq: int, **fields: Any) -> str:
    item_id = _new_id("itm")
    cols = ["item_id", "job_id", "seq"] + list(fields)
    values = [item_id, job_id, seq] + list(fields.values())
    placeholders = ",".join("?" for _ in cols)
    with _DB_LOCK, _connect() as conn:
        conn.execute(f"INSERT INTO import_items ({','.join(cols)}) VALUES ({placeholders})",
                     values)
        conn.commit()
    return item_id


def _run_job(job_id: str) -> None:
    _init_db()
    with _DB_LOCK, _connect() as conn:
        job = _job_row(conn, job_id)
        raw_rows = conn.execute(
            "SELECT * FROM import_raw WHERE job_id=? ORDER BY seq", (job_id,)).fetchall()
    if job is None:
        return
    subject_id = job["subject_id"]
    user_id = job["user_id"]

    _set_job_status(job_id, "running")
    limits = _limits()
    started = time.monotonic()
    warnings: List[str] = []
    archive_summaries: List[Dict[str, Any]] = []
    tmp_job_dir = _import_tmp_root() / job_id
    tmp_job_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ---------- 1) 展开压缩包 / 生成候选资料 ----------
        candidates: List[Dict[str, Any]] = []
        job_seq = 0
        for row in raw_rows:
            if _is_cancelled(job_id):
                _cancel_finish(job_id, "任务在展开阶段被取消")
                return
            filename = row["filename"]
            stored = Path(row["stored_path"])
            if not stored.exists():
                warnings.append(f"原始上传文件缺失: {filename}")
                continue
            from rag import formats
            ext = formats.normalize_ext(stored.suffix)
            if formats.is_archive(ext):
                try:
                    dest = tmp_job_dir / "extracted" / f"{row['seq']:03d}"
                    result = _extract(stored, dest, limits, warnings)
                except Exception as e:
                    error_type = getattr(e, "error_type", "archive_corrupt")
                    reason = str(e)
                    warnings.append(f"压缩包 {filename} 处理失败（{error_type}）：{reason}")
                    archive_summaries.append({
                        "archive": filename, "extracted": 0, "skipped": 0,
                        "note": f"失败：{reason}"})
                    # 被接受的每个文件都必须在 items 里有终态记录：
                    # 压缩包自身损坏/加密时也如实失败（带 error_type 与可读原因），
                    # 绝不能只在 warnings 里留一句话、items 却是空的。
                    job_seq += 1
                    _insert_item(
                        job_id, job_seq, display_name=filename, source_path=filename,
                        origin_archive=filename, size=row["size"],
                        status="failed", stage="parsing", progress=None,
                        message=f"压缩包处理失败：{reason}",
                        error_type=error_type, retryable=0)
                    continue
                entries = result["entries"]
                archive_summaries.append({"archive": filename,
                                          "extracted": len(entries),
                                          "skipped": len(result["skipped"]),
                                          "note": result["summary"]["note"]})
                # 被忽略/不安全的条目如实登记为 skipped，说明跳过了什么
                for skip in result["skipped"]:
                    job_seq += 1
                    _insert_item(
                        job_id, job_seq,
                        display_name=skip.get("source_path"),
                        source_path=skip.get("source_path"),
                        origin_archive=filename, size=0,
                        status="skipped", stage="parsing", progress=1.0,
                        message=skip.get("reason"), error_type=skip.get("error_type"),
                        retryable=0)
                for entry in entries:
                    candidates.append({
                        "display_name": entry["display_name"],
                        "source_path": entry["source_path"],
                        "origin_archive": filename,
                        "abs_path": Path(entry["abs_path"]),
                        "size": entry["size"],
                    })
            else:
                candidates.append({
                    "display_name": filename,
                    "source_path": filename,
                    "origin_archive": None,
                    "abs_path": stored,
                    "size": row["size"],
                })

        # 数量上限（展开后）
        if len(candidates) > limits["max_files"]:
            warnings.append(
                f"解包后资料数 {len(candidates)} 超过上限 {limits['max_files']}，超出部分已忽略")
            candidates = candidates[:limits["max_files"]]

        # ---------- 2) 解析 + 复制到学科目录 ----------
        subject_dir = _subject_dir(subject_id)
        subject_dir.mkdir(parents=True, exist_ok=True)
        parsed_docs: List[Any] = []
        ready_names: set = set()
        copied_new: List[str] = []

        for cand in candidates:
            if _is_cancelled(job_id):
                _remove_copied(subject_dir, copied_new)
                _cancel_finish(job_id, "任务在解析阶段被取消")
                return
            if time.monotonic() - started > limits["max_seconds"]:
                warnings.append(
                    f"解析耗时超过上限 {limits['max_seconds']}s，剩余资料未处理")
                break
            job_seq += 1
            item_id = _insert_item(
                job_id, job_seq, display_name=cand["display_name"],
                source_path=cand["source_path"], origin_archive=cand["origin_archive"],
                size=cand["size"], status="parsing", stage="parsing",
                progress=None, retryable=0)

            src_path = cand["abs_path"]
            from rag import formats
            ext = formats.normalize_ext(src_path.suffix)
            spec = formats.get_format(ext)
            if spec is None or not spec.available or formats.is_archive(ext):
                reason = (formats.unavailable_reason(ext) if spec is not None
                          else f"不支持的文件类型: {ext}")
                etype = ("converter_unavailable"
                         if spec is not None and spec.requires == "libreoffice"
                         else "unsupported_format")
                _update_item(item_id, status="skipped", stage="parsing", progress=1.0,
                             message=reason, error_type=etype,
                             retryable=1 if etype == "converter_unavailable" else 0)
                if etype == "converter_unavailable":
                    warnings.append(f"{cand['display_name']}：{reason}")
                continue

            docs, error_type, message = _parse_file(src_path)
            if not docs:
                retryable = 1 if error_type in ("converter_unavailable", "io_error") else 0
                _update_item(item_id, status="failed", stage="parsing", progress=None,
                             message=message or "解析失败", error_type=error_type or "parse_error",
                             retryable=retryable)
                continue

            content = src_path.read_bytes()
            decision = _decide_target(subject_dir, cand["display_name"], content)
            final_path = subject_dir / decision["name"]

            if decision["skip"]:
                # 同名同内容：不重复落盘、不重复索引，但内容确实可学习 → 记为 ready
                _rewrite_metadata(docs, decision["name"], final_path)
                parsed_docs.extend(docs)
                ready_names.add(decision["name"])
                doc_id, _mid = _register_material(subject_id, decision["name"],
                                                  len(content), user_id)
                _update_item(item_id, status="ready", stage="indexing", progress=1.0,
                             message=decision["reason"], doc_id=doc_id,
                             stored_path=str(final_path))
                continue

            existed_before = final_path.exists()
            final_path.write_bytes(content)
            if not existed_before:
                copied_new.append(decision["name"])

            _rewrite_metadata(docs, decision["name"], final_path)
            parsed_docs.extend(docs)
            ready_names.add(decision["name"])

            doc_id, material_id = _register_material(subject_id, decision["name"],
                                                     len(content), user_id)
            msg = "可学习" if not decision["renamed"] else decision["reason"]
            _update_item(item_id, status="indexing", stage="indexing", progress=None,
                         message=msg, doc_id=doc_id, stored_path=str(final_path),
                         content_sha=_sha256(final_path))

        # ---------- 3) 统一建库（本批 + 既有资料，只建一次） ----------
        if _is_cancelled(job_id):
            _remove_copied(subject_dir, copied_new)
            _cancel_finish(job_id, "任务在建库前被取消")
            return

        indexing_ready = _items_with_status(job_id, "indexing")
        existing_docs = _collect_existing_documents(subject_id, ready_names)
        all_docs = parsed_docs + existing_docs
        if not all_docs:
            _finalize_without_build(job_id, subject_id, warnings, archive_summaries)
            return

        try:
            from services import knowledge_service
            knowledge_service.rebuild_subject_knowledge_from_documents(subject_id, all_docs)
        except Exception as e:
            logger.error(f"导入建库失败: {job_id}: {e}", exc_info=True)
            for item_id in indexing_ready:
                _update_item(item_id, status="failed", stage="indexing", progress=None,
                             message=f"建库失败：{e}", error_type="build_failed", retryable=1)
            _finish_job(job_id, _derive_status(job_id), _summary(job_id), warnings,
                        archive_summaries)
            return

        for item_id in indexing_ready:
            _update_item(item_id, status="ready", stage="indexing", progress=1.0,
                         message="可学习", error_type=None, retryable=0)

        _finish_job(job_id, _derive_status(job_id), _summary(job_id), warnings,
                    archive_summaries)

    finally:
        # 清理本次解包/临时内容（重试所需的上传原文另外保留，见 retry_job）
        shutil.rmtree(tmp_job_dir / "extracted", ignore_errors=True)


def _extract(stored: Path, dest: Path, limits: Dict[str, Any],
             warnings: List[str]) -> Dict[str, Any]:
    from services import archive_service
    archive_limits = {
        "max_total_bytes": limits["max_total_mb"] * 1024 * 1024,
        "max_files": limits["max_files"],
        "max_depth": limits["max_depth"],
        "max_seconds": limits["max_seconds"],
    }
    return archive_service.extract_archive(str(stored), str(dest), limits=archive_limits)


def _remove_copied(subject_dir: Path, names: List[str]) -> None:
    for name in names:
        try:
            p = subject_dir / name
            if p.exists() and p.is_file():
                p.unlink()
        except Exception as e:
            logger.warning(f"取消清理已复制文件失败: {name}: {e}")


def _items_with_status(job_id: str, status: str) -> List[str]:
    with _DB_LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT item_id FROM import_items WHERE job_id=? AND status=?",
            (job_id, status)).fetchall()
    return [r["item_id"] for r in rows]


def _cancel_finish(job_id: str, message: str) -> None:
    _set_job_status(job_id, "cancelled", finished_at=_now(), summary_text=message)
    _set_cancel_flag(job_id, 1)


def _set_cancel_flag(job_id: str, value: int) -> None:
    with _DB_LOCK, _connect() as conn:
        conn.execute("UPDATE import_jobs SET cancel_requested=? WHERE job_id=?",
                     (value, job_id))
        conn.commit()


def _derive_status(job_id: str) -> str:
    summary = _item_counts(job_id)
    if summary["ready"] == 0:
        return "failed"
    if summary["failed"] > 0:
        return "partial"
    return "succeeded"


def _item_counts(job_id: str) -> Dict[str, int]:
    with _DB_LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM import_items WHERE job_id=? GROUP BY status",
            (job_id,)).fetchall()
    counts = {s: 0 for s in ITEM_STATUSES}
    for r in rows:
        counts[r["status"]] = r["n"]
    return counts


def _finalize_without_build(job_id: str, subject_id: str, warnings: List[str],
                            archive_summaries: List[Dict[str, Any]]) -> None:
    _finish_job(job_id, _derive_status(job_id), _summary(job_id), warnings,
                archive_summaries)


def _summary(job_id: str) -> str:
    c = _item_counts(job_id)
    parts = []
    if c["ready"]:
        parts.append(f"{c['ready']} 份资料已可学习")
    if c["failed"]:
        parts.append(f"{c['failed']} 份未能导入")
    if c["skipped"]:
        parts.append(f"{c['skipped']} 份已跳过")
    if not parts:
        parts.append("没有可导入的资料")
    return "，".join(parts)


def _finish_job(job_id: str, status: str, summary_text: str,
                warnings: Optional[List[str]] = None,
                archive_summaries: Optional[List[Dict[str, Any]]] = None) -> None:
    counts = _item_counts(job_id)
    with _DB_LOCK, _connect() as conn:
        job = _job_row(conn, job_id)
    total_bytes = 0
    with _DB_LOCK, _connect() as conn:
        size_row = conn.execute(
            "SELECT COALESCE(SUM(size), 0) AS total FROM import_items WHERE job_id=?",
            (job_id,)).fetchone()
    if size_row is not None:
        total_bytes = int(size_row["total"] or 0)
    totals = {"files": counts["ready"] + counts["failed"] + counts["skipped"],
              "ready": counts["ready"], "failed": counts["failed"],
              "skipped": counts["skipped"], "bytes": total_bytes}
    _set_job_status(
        job_id, status,
        finished_at=_now(),
        summary_text=summary_text,
        totals_json=json.dumps(totals, ensure_ascii=False),
        warnings_json=json.dumps(warnings or [], ensure_ascii=False),
        archive_summaries_json=json.dumps(archive_summaries or [], ensure_ascii=False),
        kb_json=json.dumps(_kb_public(job["subject_id"] if job else ""), ensure_ascii=False),
    )


def _kb_public(subject_id: str) -> Dict[str, Any]:
    try:
        from services import knowledge_service
        stats = knowledge_service.get_knowledge_base_stats(subject_id)
        return {
            "status": stats.get("status", "empty"),
            "ready": bool(stats.get("has_knowledge_base")),
            "document_count": stats.get("document_count", 0),
            "chunk_count": stats.get("chunk_count", 0),
        }
    except Exception:
        return {"status": "empty", "ready": False, "document_count": 0, "chunk_count": 0}


# ==================== 查询 / 重试 / 取消 ====================


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    _init_db()
    with _DB_LOCK, _connect() as conn:
        job = _job_row(conn, job_id)
        if job is None:
            return None
        items = conn.execute(
            "SELECT * FROM import_items WHERE job_id=? ORDER BY seq", (job_id,)).fetchall()
    return _job_to_public(dict(job), [dict(i) for i in items])


def list_jobs(subject_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    _init_db()
    limit = max(1, min(int(limit or 5), 50))
    with _DB_LOCK, _connect() as conn:
        jobs = conn.execute(
            "SELECT * FROM import_jobs WHERE subject_id=? ORDER BY created_at DESC LIMIT ?",
            (subject_id, limit)).fetchall()
        out = []
        for job in jobs:
            items = conn.execute(
                "SELECT * FROM import_items WHERE job_id=? ORDER BY seq",
                (job["job_id"],)).fetchall()
            out.append(_job_to_public(dict(job), [dict(i) for i in items]))
    return out


def _job_to_public(job: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
    def _load(raw: Any, default: Any) -> Any:
        try:
            return json.loads(raw) if raw else default
        except Exception:
            return default

    totals = _load(job.get("totals_json"), {})
    warnings = _load(job.get("warnings_json"), [])
    archive_summaries = _load(job.get("archive_summaries_json"), [])
    kb = _load(job.get("kb_json"), {})
    if not kb:
        kb = _kb_public(job.get("subject_id", ""))

    public_items = [{
        "item_id": it.get("item_id"),
        "display_name": it.get("display_name"),
        "source_path": it.get("source_path"),
        "origin_archive": it.get("origin_archive"),
        "size": it.get("size"),
        "status": it.get("status"),
        "stage": it.get("stage"),
        "progress": it.get("progress"),
        "message": it.get("message"),
        "doc_id": it.get("doc_id"),
        "error_type": it.get("error_type"),
        "retryable": bool(it.get("retryable")),
    } for it in items]

    can_retry = any(it["retryable"] and it["status"] == "failed" for it in public_items)
    return {
        "job_id": job.get("job_id"),
        "subject_id": job.get("subject_id"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "finished_at": job.get("finished_at"),
        "totals": totals,
        "items": public_items,
        "archive_summaries": archive_summaries,
        "kb": kb,
        "summary_text": job.get("summary_text") or "",
        "can_retry": can_retry,
        "warnings": warnings,
    }


def retry_job(job_id: str, user_id: Optional[str] = None) -> Optional[str]:
    """
    重试失败且 retryable 的条目；返回新 job_id。

    幂等：重新处理原始上传内容，已成功落盘的"同名同内容"会被判为跳过，
    因此不会产生重复资料或重复索引（见 _decide_target）。
    """
    _init_db()
    with _DB_LOCK, _connect() as conn:
        job = _job_row(conn, job_id)
        raw_rows = conn.execute(
            "SELECT * FROM import_raw WHERE job_id=? ORDER BY seq", (job_id,)).fetchall()
    if job is None:
        return None
    if job["status"] not in TERMINAL_JOB_STATUSES:
        raise ValueError("任务尚未结束，不能重试")
    if not any(it["retryable"] and it["status"] == "failed"
               for it in (get_job(job_id) or {}).get("items", [])):
        raise ValueError("没有可重试的失败条目")

    new_job_id = _new_id("imp")
    new_raw = _import_tmp_root() / new_job_id / "raw"
    new_raw.mkdir(parents=True, exist_ok=True)
    for row in raw_rows:
        src = Path(row["stored_path"])
        if src.exists():
            shutil.copy2(src, new_raw / src.name)

    with _DB_LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO import_jobs (job_id, subject_id, user_id, status, created_at,"
            " updated_at, totals_json, warnings_json, archive_summaries_json, kb_json,"
            " summary_text, retry_of, raw_dir) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_job_id, job["subject_id"], user_id or job["user_id"], "queued",
             _now(), _now(), "{}", "[]", "[]", "{}", "重试任务已排队", job_id,
             str(new_raw)))
        conn.executemany(
            "INSERT INTO import_raw (job_id, seq, filename, stored_path, size)"
            " VALUES (?,?,?,?,?)",
            [(new_job_id, row["seq"], row["filename"],
              str(new_raw / Path(row["stored_path"]).name), row["size"])
             for row in raw_rows])
        conn.commit()

    threading.Thread(target=_run_job_thread, args=(new_job_id,),
                     name=f"import-{new_job_id}", daemon=True).start()
    return new_job_id


def cancel_job(job_id: str) -> bool:
    """请求取消；queued 任务直接置为 cancelled。返回是否找到任务。"""
    _init_db()
    with _DB_LOCK, _connect() as conn:
        job = _job_row(conn, job_id)
        if job is None:
            return False
        conn.execute("UPDATE import_jobs SET cancel_requested=1 WHERE job_id=?", (job_id,))
        if job["status"] in ("queued",):
            conn.execute(
                "UPDATE import_jobs SET status='cancelled', finished_at=?, updated_at=?,"
                " summary_text=? WHERE job_id=?",
                (_now(), _now(), "任务已取消", job_id))
        conn.commit()
    return True


def cleanup_job_files(job_id: str) -> None:
    """删除该任务的临时目录（只在受控 import_tmp 根内）。"""
    try:
        from services.archive_service import cleanup as _cleanup
        with _DB_LOCK, _connect() as conn:
            job = _job_row(conn, job_id)
        if job is None:
            return
        root = str(_import_tmp_root() / job_id)
        _cleanup(root, root=str(_import_tmp_root()))
    except Exception as e:
        logger.warning(f"清理导入临时目录失败: {job_id}: {e}")


__all__ = [
    "ITEM_STATUSES",
    "JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "cancel_job",
    "cleanup_job_files",
    "create_import_job",
    "get_job",
    "list_jobs",
    "retry_job",
]
