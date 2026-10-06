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

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config.path_config import get_data_dir

logger = logging.getLogger(__name__)

_DB_LOCK = threading.Lock()

# 同一学科同时只允许一个任务在建库，避免两个任务并发"备份→构建→回滚"
# 互相覆盖对方的索引目录（可能出现"回滚把新索引删掉"的窗口）。
_BUILD_LOCKS: Dict[str, threading.Lock] = {}
_BUILD_LOCKS_GUARD = threading.Lock()

JOB_STATUSES = ("queued", "running", "succeeded", "partial", "failed", "cancelled")
ITEM_STATUSES = ("pending", "parsing", "indexing", "ready", "failed", "skipped")
TERMINAL_JOB_STATUSES = ("succeeded", "partial", "failed", "cancelled")

# 未处理成员在 items 里没有行，靠计划表（import_jobs.plan_json）计数。
# item 的"进行中"状态（非终态）。
_ACTIVE_ITEM_STATUSES = ("pending", "parsing", "indexing")


def _subject_build_lock(subject_id: str) -> threading.Lock:
    with _BUILD_LOCKS_GUARD:
        lock = _BUILD_LOCKS.get(subject_id)
        if lock is None:
            lock = threading.Lock()
            _BUILD_LOCKS[subject_id] = lock
        return lock


def _item_max_seconds() -> float:
    """单个文件解析的墙钟上限（默认 180s，env IMPORT_ITEM_MAX_SECONDS）"""
    try:
        value = float(os.getenv("IMPORT_ITEM_MAX_SECONDS", "180"))
    except (TypeError, ValueError):
        return 180.0
    return value if value > 0 else 180.0


def _parse_config_signature() -> str:
    """解析配置指纹：用于"内容没变就复用"的幂等判定（视觉提示词版本/模式等）"""
    from rag.enrichment.multimodal_enricher import VISION_PROMPT_VERSION
    return "|".join([
        f"vision_prompt={VISION_PROMPT_VERSION}",
        f"vision_mode={os.getenv('VISION_MODE', 'vision')}",
    ])


def _embedding_signature() -> str:
    """
    embedding 版本指纹。优先取向量库 meta，其次取环境变量；取不到用 default。
    只有"内容 + 解析配置 + embedding 版本"三者一致，才判定为可复用。
    """
    parts: List[str] = []
    try:
        from rag.vector_store import read_index_meta
        from services.knowledge_service import get_vector_store_dir
        meta = read_index_meta(get_vector_store_dir("__reuse_probe__")) or {}
        if meta.get("model_name") or meta.get("embedding_model"):
            parts.append(f"meta={meta.get('model_name') or meta.get('embedding_model')}")
    except Exception:
        pass
    for name in ("EMBEDDING_MODEL_NAME", "EMBEDDING_MODEL"):
        value = os.getenv(name)
        if value:
            parts.append(f"{name}={value}")
    return "|".join(parts) or "default"


def _reuse_key(content_sha: str, parse_config: str, embedding_sig: str) -> str:
    raw = f"{content_sha}|{parse_config}|{embedding_sig}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _member_key(origin_archive: Optional[str], source_path: Optional[str]) -> str:
    """成员唯一键：用 (origin_archive, source_path) 对齐重解包前后的同一成员"""
    return f"{origin_archive or ''}\x00{source_path or ''}"


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


def _monotonic() -> float:
    """单调时钟包装（测试可替换，用于确定性验证整批预算行为）"""
    return time.monotonic()


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
        _migrate_db(conn)


# 只增不改：新列用 ALTER TABLE 补（老库自动升级，不丢已有任务记录）
_MIGRATIONS = {
    "import_jobs": {
        # 成员计划：发现但尚未建 item 的成员靠它计数与"继续处理"
        "plan_json": "TEXT",
        # 阶段计时：extract_ms / parse_ms / build_ms / total_ms
        "stage_timings_json": "TEXT",
        # 计划成员总数（有效学习资料数）
        "total_members": "INTEGER",
        # 继续处理来源任务
        "continue_of": "TEXT",
    },
    "import_items": {
        # 单条计时：parse_ms / vision_ms / vision_calls / chunks ...
        "timings_json": "TEXT",
        # 复用键：content_sha + 解析配置 + embedding 版本
        "reuse_key": "TEXT",
        # 解析配置指纹（便于排查"为什么这次没复用"）
        "parse_config": "TEXT",
    },
}


def _migrate_db(conn: sqlite3.Connection) -> None:
    for table, columns in _MIGRATIONS.items():
        try:
            existing = {row["name"] for row in
                        conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            continue
        for column, coltype in columns.items():
            if column in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            except Exception as e:
                logger.warning(f"导入表迁移失败 {table}.{column}: {e}")


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
        etype = ("converter_unavailable"
                 if formats.requires_office_converter(ext) else "unsupported_format")
        return ext, reason, etype
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
    from rag import formats
    # 单文件上限按"是否压缩包"分流：压缩包 max_archive_mb，普通文档 max_file_mb
    archive_bytes = int(limits["max_archive_mb"]) * 1024 * 1024
    doc_bytes = int(limits["max_file_mb"]) * 1024 * 1024
    max_total_bytes = int(limits.get("max_total_mb",
                                     limits.get("max_extract_total_mb", 1024))) * 1024 * 1024

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
        is_archive = formats.is_archive(ext) if ext else False
        limit_bytes = archive_bytes if is_archive else doc_bytes
        if len(content) > limit_bytes:
            actual_mb = round(len(content) / (1024 * 1024), 2)
            limit_mb = limit_bytes // (1024 * 1024)
            kind = "压缩包" if is_archive else "普通文档"
            rejected.append({
                "filename": filename,
                "reason": f"文件实际 {actual_mb}MB，超过{kind}单文件上限 {limit_mb}MB",
                "error_type": "size_exceeded"})
            continue
        if total + len(content) > max_total_bytes:
            actual_mb = round((total + len(content)) / (1024 * 1024), 2)
            limit_mb = max_total_bytes // (1024 * 1024)
            rejected.append({
                "filename": filename,
                "reason": f"本批总量实际 {actual_mb}MB，超过上限 {limit_mb}MB",
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
             json.dumps({"files": 0, "ready": 0, "processing": 0, "failed": 0,
                         "unprocessed": 0, "skipped": 0, "total": 0, "bytes": total},
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


def _parse_file(path: Path) -> Tuple[List[Any], Optional[str], Optional[str], Dict[str, Any]]:
    """返回 (documents, error_type, message, meta)。meta 含 vision_stats。"""
    from rag.document import load_document_result
    start = time.monotonic()
    result = load_document_result(path)
    meta: Dict[str, Any] = {
        "vision_stats": dict(getattr(result, "metadata", None) or {}) or {},
    }
    meta["vision_stats"] = meta["vision_stats"].get("vision_stats", {}) if isinstance(
        meta["vision_stats"], dict) else {}
    meta["parse_ms"] = int((time.monotonic() - start) * 1000)
    if result.success:
        return result.documents, None, None, meta
    return [], result.error_type or "parse_error", result.error_message or "解析失败", meta


def _parse_with_timeout(path: Path, timeout: float
                        ) -> Tuple[List[Any], Optional[str], Optional[str], Dict[str, Any]]:
    """
    在单文件墙钟上限内解析；超时记为 item_timeout 并**继续处理后面的文件**。

    被放弃的解析线程无法强杀，但它受视觉单张超时与 requests 超时约束，
    不会再无限消耗；这里只保证整批不被它拖死。
    """
    start = time.monotonic()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="import-parse")
    future = executor.submit(_parse_file, path)
    try:
        docs, error_type, message, meta = future.result(timeout=timeout)
        meta.setdefault("parse_ms", int((time.monotonic() - start) * 1000))
        meta["item_timed_out"] = False
        return docs, error_type, message, meta
    except FuturesTimeout:
        logger.warning(f"单文件解析超时（>{timeout:g}s），跳过并继续: {path.name}")
        return [], "item_timeout", (
            f"单文件解析超时（>{timeout:g}s），已跳过该文件并继续处理其余文件"), {
                "parse_ms": int((time.monotonic() - start) * 1000),
                "vision_stats": {}, "item_timed_out": True}
    except Exception as e:  # 解析线程自身异常也要给出准确原因
        logger.warning(f"单文件解析异常: {path.name}: {e}")
        return [], "parse_error", f"解析异常: {e}", {
            "parse_ms": int((time.monotonic() - start) * 1000),
            "vision_stats": {}, "item_timed_out": False}
    finally:
        executor.shutdown(wait=False)


# ==================== 解析结果缓存（复用 chunks，不重复解析） ====================


def _docs_cache_path(content_sha: str, parse_config: str) -> Path:
    cfg = hashlib.md5(parse_config.encode("utf-8")).hexdigest()[:8]
    return get_data_dir() / "cache" / "parsed_docs" / f"{content_sha[:20]}_{cfg}.json"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def _save_docs_cache(content_sha: str, parse_config: str, docs: List[Any]) -> None:
    if not content_sha or not docs:
        return
    try:
        payload = [{"page_content": getattr(d, "page_content", "") or "",
                    "metadata": _jsonable(getattr(d, "metadata", {}) or {})}
                   for d in docs]
        path = _docs_cache_path(content_sha, parse_config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"解析结果缓存写入失败（不影响导入）: {e}")


def _make_document(page_content: str, metadata: Dict[str, Any]) -> Any:
    try:
        from langchain_core.documents import Document
        return Document(page_content=page_content, metadata=metadata)
    except Exception:
        from rag.text_splitter import SimpleDocument
        return SimpleDocument(page_content=page_content, metadata=metadata)


def _load_docs_cache(content_sha: str, parse_config: str) -> Optional[List[Any]]:
    if not content_sha:
        return None
    try:
        path = _docs_cache_path(content_sha, parse_config)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not payload:
            return None
        return [_make_document(item.get("page_content", ""), dict(item.get("metadata") or {}))
                for item in payload if isinstance(item, dict)]
    except Exception as e:
        logger.warning(f"解析结果缓存读取失败（改为重新解析）: {e}")
        return None


def _collect_existing_documents(subject_id: str, exclude: set) -> List[Any]:
    """
    解析学科目录下既有文件（排除本批已解析的文件名），避免重复解析本批内容。

    2026-10-07：命中"内容哈希 + 解析配置"缓存时直接复用已解析文档，
    不再重复解析、重复调用视觉（图片描述另有其缓存，这里是文档级缓存）。
    """
    from services.knowledge_service import supported_document_extensions
    subject_dir = _subject_dir(subject_id)
    if not subject_dir.exists():
        return []
    docs: List[Any] = []
    allowed = supported_document_extensions()
    parse_cfg = _parse_config_signature()
    for f in sorted(subject_dir.iterdir()):
        if not f.is_file() or f.suffix.lower() not in allowed:
            continue
        if f.name in exclude:
            continue
        content_sha = _sha256(f)
        cached = _load_docs_cache(content_sha, parse_cfg)
        if cached is not None:
            docs.extend(cached)
            continue
        got, _et, _msg, _meta = _parse_file(f)
        if got:
            _save_docs_cache(content_sha, parse_cfg, got)
        docs.extend(got)
    return docs


# ==================== 计划 / 未处理计数 ====================


def _save_plan(job_id: str, plan: List[Dict[str, Any]]) -> None:
    payload = [{
        "display_name": p.get("display_name"),
        "source_path": p.get("source_path"),
        "origin_archive": p.get("origin_archive"),
        "size": int(p.get("size") or 0),
        "key": _member_key(p.get("origin_archive"), p.get("source_path")),
    } for p in plan]
    _set_job_status_values(
        job_id, plan_json=json.dumps(payload, ensure_ascii=False),
        total_members=len(payload))


def _set_job_status_values(job_id: str, **fields: Any) -> None:
    """只更新字段，不改 status（内部用）"""
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [job_id]
    with _DB_LOCK, _connect() as conn:
        conn.execute(f"UPDATE import_jobs SET {sets} WHERE job_id=?", values)
        conn.commit()


def _load_plan(job: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not job:
        return []
    raw = job.get("plan_json")
    try:
        payload = json.loads(raw) if raw else []
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def _unprocessed_count(conn: sqlite3.Connection, job_id: str) -> int:
    """
    发现但没有建 item 的成员数（计划里有、items 里没有）。
    这是"状态与计数必须正确"的核心：此前这些成员完全不可见。
    """
    job = _job_row(conn, job_id)
    plan = _load_plan(dict(job) if job else None)
    if not plan:
        return 0
    rows = conn.execute(
        "SELECT origin_archive, source_path FROM import_items WHERE job_id=?",
        (job_id,)).fetchall()
    item_keys = {_member_key(r["origin_archive"], r["source_path"]) for r in rows}
    return sum(1 for p in plan
               if p.get("key") not in item_keys
               and _member_key(p.get("origin_archive"), p.get("source_path")) not in item_keys)


# ==================== 任务执行 ====================


def _run_job_thread(job_id: str, only_keys: Optional[set] = None,
                    reuse_job_id: Optional[str] = None) -> None:
    try:
        _run_job(job_id, only_keys=only_keys, reuse_job_id=reuse_job_id)
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


def _merge_item_timings(item_id: str, extra: Dict[str, Any]) -> None:
    """把额外计时合并进已有 timings_json（不覆盖 parse/vision 计时）"""
    with _DB_LOCK, _connect() as conn:
        row = conn.execute("SELECT timings_json FROM import_items WHERE item_id=?",
                           (item_id,)).fetchone()
    current: Dict[str, Any] = {}
    try:
        current = json.loads(row["timings_json"]) if row and row["timings_json"] else {}
    except Exception:
        current = {}
    if not isinstance(current, dict):
        current = {}
    current.update(extra)
    _update_item(item_id, timings_json=json.dumps(current, ensure_ascii=False))


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


def _run_job(job_id: str, only_keys: Optional[set] = None,
             reuse_job_id: Optional[str] = None) -> None:
    """
    执行导入任务。

    Args:
        only_keys: 仅处理这些成员键（继续处理用）；None=处理全部（已 ready 的按内容哈希复用）
        reuse_job_id: 复用来源任务的已就绪条目（不重复解析/不重复向量化）
    """
    # 本函数跑在后台线程里，而知识库构建会在**这里**第一次导入 knowledge_service →
    # "from rag.rag_service import ..."。若此时另一个线程（如启动预热）也在导入 rag，
    # Python 3.14 会抛 deadlock detected by _ModuleLock('rag.rag_service')，
    # 任务直接以"任务执行异常"失败且 0 条目（2026-10-07 实测 imp_9938303fadb9）。
    # 统一走 rag_loader 的进程锁，把"第一次导入"串行化。
    try:
        from services.rag_loader import load_rag_stack
        load_rag_stack()
    except Exception as e:      # 导入失败不提前终止：让后续步骤给出真实错误
        logger.warning(f"导入任务预载 rag 栈失败（继续执行）: {e}")
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
    started = _monotonic()
    t_wall = time.monotonic()
    warnings: List[str] = []
    archive_summaries: List[Dict[str, Any]] = []
    tmp_job_dir = _import_tmp_root() / job_id
    tmp_job_dir.mkdir(parents=True, exist_ok=True)
    stage_timings: Dict[str, Any] = {"extract_ms": 0, "parse_ms": 0,
                                     "build_ms": 0, "vision_ms": 0, "total_ms": 0}

    parse_cfg = _parse_config_signature()
    embedding_sig = _embedding_signature()
    reuse_map: Dict[str, Dict[str, Any]] = {}
    if reuse_job_id:
        with _DB_LOCK, _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM import_items WHERE job_id=? AND status='ready'",
                (reuse_job_id,)).fetchall()
        for row in rows:
            reuse_map[_member_key(row["origin_archive"], row["source_path"])] = dict(row)

    try:
        # ---------- 1) 展开压缩包 / 生成候选资料与计划 ----------
        t_extract = time.monotonic()
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

        # 继续处理：只保留未处理/可重试的成员
        if only_keys is not None:
            candidates = [c for c in candidates
                          if _member_key(c.get("origin_archive"),
                                         c.get("source_path")) in only_keys]

        plan = [{
            "display_name": c["display_name"],
            "source_path": c["source_path"],
            "origin_archive": c["origin_archive"],
            "size": c["size"],
        } for c in candidates]
        _save_plan(job_id, plan)
        stage_timings["extract_ms"] = int((time.monotonic() - t_extract) * 1000)

        # ---------- 2) 解析 + 复制到学科目录 ----------
        subject_dir = _subject_dir(subject_id)
        subject_dir.mkdir(parents=True, exist_ok=True)
        parsed_docs: List[Any] = []
        ready_names: set = set()
        copied_new: List[str] = []
        t_parse = time.monotonic()

        for cand in candidates:
            if _is_cancelled(job_id):
                _remove_copied(subject_dir, copied_new)
                _cancel_finish(job_id, "任务在解析阶段被取消")
                return
            if _monotonic() - started > limits["max_seconds"]:
                # 整批预算到：剩余成员不建 item（计划里如实体现为未处理），
                # job 记为 partial 且可 continue；绝不假装 succeeded。
                warnings.append(
                    f"整批预算 {limits['max_seconds']}s 已到，剩余成员未处理（可继续处理）")
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
                         if formats.requires_office_converter(ext)
                         else "unsupported_format")
                _update_item(item_id, status="skipped", stage="parsing", progress=1.0,
                             message=reason, error_type=etype, timings_json="{}",
                             retryable=1 if etype == "converter_unavailable" else 0)
                if etype == "converter_unavailable":
                    warnings.append(f"{cand['display_name']}：{reason}")
                continue

            src_sha = _sha256(src_path)

            # 复用已就绪条目（内容哈希 + 解析配置 + embedding 版本一致）
            reuse = reuse_map.get(_member_key(cand.get("origin_archive"),
                                              cand.get("source_path")))
            if reuse and reuse.get("status") == "ready" and src_sha:
                reuse_key = _reuse_key(src_sha, parse_cfg, embedding_sig)
                stored_ready = Path(reuse.get("stored_path") or "")
                if (reuse.get("content_sha") == src_sha
                        and stored_ready.exists()
                        and (not reuse.get("reuse_key")
                             or reuse.get("reuse_key") == reuse_key)):
                    docs = _load_docs_cache(src_sha, parse_cfg)
                    if docs is None:
                        docs, _et, _msg, _meta = _parse_file(src_path)
                        if docs:
                            _save_docs_cache(src_sha, parse_cfg, docs)
                    if docs:
                        final_name = stored_ready.name
                        _rewrite_metadata(docs, final_name, stored_ready)
                        parsed_docs.extend(docs)
                        ready_names.add(final_name)
                        _register_material(subject_id, final_name,
                                           stored_ready.stat().st_size, user_id)
                        _update_item(
                            item_id, status="ready", stage="indexing", progress=1.0,
                            message="复用已就绪内容（内容哈希一致，未重复解析）",
                            doc_id=reuse.get("doc_id"), stored_path=str(stored_ready),
                            content_sha=src_sha, reuse_key=reuse_key,
                            parse_config=parse_cfg, timings_json=json.dumps(
                                {"parse_ms": 0, "reused": True}, ensure_ascii=False))
                        continue

            docs, error_type, message, meta = _parse_with_timeout(
                src_path, _item_max_seconds())
            vision_stats = meta.get("vision_stats") or {}
            stage_timings["vision_ms"] += int(vision_stats.get("vision_ms") or 0)
            timings = {
                "parse_ms": int(meta.get("parse_ms") or 0),
                "vision_ms": int(vision_stats.get("vision_ms") or 0),
                "vision_calls": int(vision_stats.get("vision_calls") or 0),
                "vision_cached": int(vision_stats.get("vision_cached") or 0),
                "vision_failed": int(vision_stats.get("vision_failed") or 0),
                "vision_timeout": int(vision_stats.get("vision_timeout") or 0),
                "images_total": int(vision_stats.get("images_total") or 0),
                "images_deduped": int(vision_stats.get("images_deduped") or 0),
                "item_timed_out": bool(meta.get("item_timed_out")),
            }
            timings_json = json.dumps(timings, ensure_ascii=False)

            if not docs:
                retryable = 1 if error_type in (
                    "converter_unavailable", "io_error", "item_timeout", "parse_error") else 0
                _update_item(item_id, status="failed", stage="parsing", progress=None,
                             message=message or "解析失败",
                             error_type=error_type or "parse_error",
                             retryable=retryable, timings_json=timings_json)
                if error_type == "item_timeout":
                    warnings.append(f"{cand['display_name']}：{message}")
                continue

            content = src_path.read_bytes()
            decision = _decide_target(subject_dir, cand["display_name"], content)
            final_path = subject_dir / decision["name"]

            if decision["skip"]:
                # 同名同内容：不重复落盘、不重复索引，但内容确实可学习 → 记为 ready
                _rewrite_metadata(docs, decision["name"], final_path)
                parsed_docs.extend(docs)
                ready_names.add(decision["name"])
                if src_sha:
                    _save_docs_cache(src_sha, parse_cfg, docs)
                doc_id, _mid = _register_material(subject_id, decision["name"],
                                                  len(content), user_id)
                _update_item(item_id, status="ready", stage="indexing", progress=1.0,
                             message=decision["reason"], doc_id=doc_id,
                             stored_path=str(final_path), content_sha=src_sha,
                             reuse_key=_reuse_key(src_sha, parse_cfg, embedding_sig),
                             parse_config=parse_cfg, timings_json=timings_json)
                continue

            existed_before = final_path.exists()
            final_path.write_bytes(content)
            if not existed_before:
                copied_new.append(decision["name"])

            _rewrite_metadata(docs, decision["name"], final_path)
            parsed_docs.extend(docs)
            ready_names.add(decision["name"])
            if src_sha:
                _save_docs_cache(src_sha, parse_cfg, docs)

            doc_id, material_id = _register_material(subject_id, decision["name"],
                                                     len(content), user_id)
            msg = "可学习" if not decision["renamed"] else decision["reason"]
            _update_item(item_id, status="indexing", stage="indexing", progress=None,
                         message=msg, doc_id=doc_id, stored_path=str(final_path),
                         content_sha=src_sha,
                         reuse_key=_reuse_key(src_sha, parse_cfg, embedding_sig),
                         parse_config=parse_cfg, timings_json=timings_json)

        stage_timings["parse_ms"] = int((time.monotonic() - t_parse) * 1000)

        # 继续处理时：已 ready 的资料直接复用缓存文档，不重复解析
        if reuse_job_id:
            _include_reused_ready_docs(reuse_job_id, parse_cfg, ready_names, parsed_docs)

        # ---------- 3) 统一建库（本批 + 既有资料，只建一次） ----------
        if _is_cancelled(job_id):
            _remove_copied(subject_dir, copied_new)
            _cancel_finish(job_id, "任务在建库前被取消")
            return

        indexing_ready = _items_with_status(job_id, "indexing")
        existing_docs = _collect_existing_documents(subject_id, ready_names)
        all_docs = parsed_docs + existing_docs
        if not all_docs:
            stage_timings["total_ms"] = int((time.monotonic() - t_wall) * 1000)
            _finalize_without_build(job_id, subject_id, warnings, archive_summaries,
                                    stage_timings)
            return

        t_build = time.monotonic()
        try:
            from services import knowledge_service
            # 同一学科串行建库：避免两个任务并发"备份→构建→回滚"互相覆盖索引目录
            with _subject_build_lock(subject_id):
                build_result = knowledge_service.rebuild_subject_knowledge_from_documents(
                    subject_id, all_docs)
        except Exception as e:
            logger.error(f"导入建库失败: {job_id}: {e}", exc_info=True)
            for item_id in indexing_ready:
                _update_item(item_id, status="failed", stage="indexing", progress=None,
                             message=f"建库失败：{e}", error_type="build_failed", retryable=1)
            stage_timings["build_ms"] = int((time.monotonic() - t_build) * 1000)
            stage_timings["total_ms"] = int((time.monotonic() - t_wall) * 1000)
            _finish_job(job_id, _derive_status(job_id), _summary(job_id), warnings,
                        archive_summaries, stage_timings)
            return
        stage_timings["build_ms"] = int((time.monotonic() - t_build) * 1000)

        chunk_count = None
        try:
            chunk_count = (build_result or {}).get("chunk_count")
        except Exception:
            chunk_count = None
        if isinstance(chunk_count, int):
            stage_timings["build_chunks"] = chunk_count
        for item_id in indexing_ready:
            _update_item(item_id, status="ready", stage="indexing", progress=1.0,
                         message="可学习", error_type=None, retryable=0)
        # 只有本批恰好一份资料时，才能把总 chunk 数如实归到该条目
        if len(indexing_ready) == 1 and isinstance(chunk_count, int):
            _merge_item_timings(indexing_ready[0], {"chunks": chunk_count})

        stage_timings["total_ms"] = int((time.monotonic() - t_wall) * 1000)
        _finish_job(job_id, _derive_status(job_id), _summary(job_id), warnings,
                    archive_summaries, stage_timings)

    finally:
        # 清理本次解包/临时内容（重试/继续所需的上传原文另外保留，见 retry_job/continue_job）
        shutil.rmtree(tmp_job_dir / "extracted", ignore_errors=True)


def _include_reused_ready_docs(reuse_job_id: str, parse_cfg: str,
                               ready_names: set, parsed_docs: List[Any]) -> None:
    """把来源任务里已 ready 的资料从缓存文档直接纳入本次建库（不重复解析）"""
    with _DB_LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM import_items WHERE job_id=? AND status='ready'",
            (reuse_job_id,)).fetchall()
    for row in rows:
        stored = Path(row["stored_path"] or "")
        if not stored.exists() or stored.name in ready_names:
            continue
        content_sha = row["content_sha"] or _sha256(stored)
        docs = _load_docs_cache(content_sha, parse_cfg)
        if docs is None:
            docs, _et, _msg, _meta = _parse_file(stored)
            if docs:
                _save_docs_cache(content_sha, parse_cfg, docs)
        if docs:
            _rewrite_metadata(docs, stored.name, stored)
            parsed_docs.extend(docs)
            ready_names.add(stored.name)


def _extract(stored: Path, dest: Path, limits: Dict[str, Any],
             warnings: List[str]) -> Dict[str, Any]:
    from services import archive_service
    mb = 1024 * 1024
    archive_limits = {
        "max_total_bytes": int(limits.get("max_extract_total_mb",
                                          limits.get("max_total_mb", 1024))) * mb,
        "max_extract_file_bytes": int(limits.get("max_extract_file_mb", 200)) * mb,
        "max_files": int(limits.get("max_files", 100)),
        "max_depth": int(limits.get("max_depth", 2)),
        "max_seconds": int(limits.get("max_seconds", 300)),
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
    """
    只有"全部成员都有了终态（ready/failed/skipped）且无失败"才算 succeeded。

    只要还有未处理成员（发现但没建 item）或失败条目，必须是 partial，
    绝不 succeeded —— 2026-10-07 事故（16 个成员只处理 8 个却报 succeeded）的直接修复。
    """
    counts = _item_counts(job_id)
    with _DB_LOCK, _connect() as conn:
        unprocessed = _unprocessed_count(conn, job_id)
    if counts["ready"] == 0:
        return "failed"
    if unprocessed > 0 or counts["failed"] > 0:
        return "partial"
    if any(counts[s] > 0 for s in _ACTIVE_ITEM_STATUSES):
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
                            archive_summaries: List[Dict[str, Any]],
                            stage_timings: Optional[Dict[str, Any]] = None) -> None:
    _finish_job(job_id, _derive_status(job_id), _summary(job_id), warnings,
                archive_summaries, stage_timings)


def _summary(job_id: str) -> str:
    """
    用户可见摘要：必须同时说出"可学习"和"未处理"。

    事故里只写"8 份资料已可学习"，用户不知道还有 8 份没进来。
    现在写成"N 份可学习，M 份未处理（可继续）"。
    """
    c = _item_counts(job_id)
    with _DB_LOCK, _connect() as conn:
        unprocessed = _unprocessed_count(conn, job_id)
    parts = []
    if c["ready"]:
        parts.append(f"{c['ready']} 份可学习")
    if unprocessed:
        parts.append(f"{unprocessed} 份未处理（可继续）")
    if c["failed"]:
        parts.append(f"{c['failed']} 份未能导入")
    if c["skipped"]:
        parts.append(f"{c['skipped']} 份已跳过")
    if not parts:
        parts.append("没有可导入的资料")
    return "，".join(parts)


def _finish_job(job_id: str, status: str, summary_text: str,
                warnings: Optional[List[str]] = None,
                archive_summaries: Optional[List[Dict[str, Any]]] = None,
                stage_timings: Optional[Dict[str, Any]] = None) -> None:
    counts = _item_counts(job_id)
    with _DB_LOCK, _connect() as conn:
        job = _job_row(conn, job_id)
        unprocessed = _unprocessed_count(conn, job_id)
    total_bytes = 0
    with _DB_LOCK, _connect() as conn:
        size_row = conn.execute(
            "SELECT COALESCE(SUM(size), 0) AS total FROM import_items WHERE job_id=?",
            (job_id,)).fetchone()
    if size_row is not None:
        total_bytes = int(size_row["total"] or 0)
    processing = sum(counts[s] for s in _ACTIVE_ITEM_STATUSES)
    totals = {
        # 旧字段保持既有语义（已出终态的条目数），只增不改
        "files": counts["ready"] + counts["failed"] + counts["skipped"],
        "ready": counts["ready"],
        "processing": processing,
        "failed": counts["failed"],
        "unprocessed": unprocessed,
        "skipped": counts["skipped"],
        # total == ready + processing + failed + unprocessed + skipped
        "total": (counts["ready"] + processing + counts["failed"]
                  + unprocessed + counts["skipped"]),
        "bytes": total_bytes,
    }
    _set_job_status(
        job_id, status,
        finished_at=_now(),
        summary_text=summary_text,
        totals_json=json.dumps(totals, ensure_ascii=False),
        warnings_json=json.dumps(warnings or [], ensure_ascii=False),
        archive_summaries_json=json.dumps(archive_summaries or [], ensure_ascii=False),
        kb_json=json.dumps(_kb_public(job["subject_id"] if job else ""), ensure_ascii=False),
        stage_timings_json=json.dumps(stage_timings or {}, ensure_ascii=False),
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
        # 计时可解释（任务 5）：解析/视觉/阶段耗时，含复用与超时标记
        "timings": _load(it.get("timings_json"), {}) or {},
    } for it in items]

    # 未处理成员数（计划里有、items 里没有）——状态与计数一致性的关键
    plan = _load(job.get("plan_json"), []) or []
    item_keys = {_member_key(i.get("origin_archive"), i.get("source_path"))
                 for i in items}
    if plan:
        unprocessed = sum(
            1 for p in plan
            if isinstance(p, dict)
            and p.get("key") not in item_keys
            and _member_key(p.get("origin_archive"), p.get("source_path")) not in item_keys)
    else:
        # 旧任务（本改动之前创建）没有 plan_json：用 archive_summaries 的 extracted 回填
        # "发现但未处理"的成员数——否则会出现"未处理 0，却提示仍有成员未处理"的自相矛盾。
        # 真实事故 imp_d3557678d5b5：extracted=16 而只有 8 条 item。
        # 只影响返回视图，绝不改写数据库里的历史记录。
        summaries = _load(job.get("archive_summaries_json"), []) or []
        discovered = 0
        for s in summaries:
            if isinstance(s, dict):
                try:
                    discovered += max(0, int(s.get("extracted") or 0))
                except (TypeError, ValueError):
                    continue
        unprocessed = max(0, discovered - len(items))

    processing = sum(1 for it in public_items
                     if it["status"] in _ACTIVE_ITEM_STATUSES)
    # 分类计数一律**由条目现算**，再据此求和得到 total：
    # 被中断的任务（totals_json 还是 {}）此前只回填了 processing/unprocessed/total，
    # 于是出现 total=76、各项之和=51 的破例（2026-10-07 实测 imp_44ea33bdf563：
    # 25 个条目在进程被杀后收敛为 failed，却没有 failed 计数）。
    # 这样 total == ready+processing+failed+unprocessed+skipped 恒成立。
    ready_count = sum(1 for it in public_items if it["status"] == "ready")
    failed_count = sum(1 for it in public_items if it["status"] == "failed")
    skipped_count = sum(1 for it in public_items if it["status"] == "skipped")
    total_members = (ready_count + processing + failed_count
                     + unprocessed + skipped_count)
    # 回填/覆盖旧字段：调用方应优先使用 totals 里的新字段
    if isinstance(totals, dict):
        totals["ready"] = ready_count
        totals["failed"] = failed_count
        totals["skipped"] = skipped_count
        totals["processing"] = processing
        totals["unprocessed"] = unprocessed
        totals["total"] = total_members
        totals.setdefault("files", len(items))
        totals.setdefault("bytes", 0)

    can_retry = any(it["retryable"] and it["status"] == "failed" for it in public_items)
    # 旧任务（本改动之前创建）没有 plan_json，但 partial + 原包仍在时同样应可继续；
    # 继续时会重跑全部成员，已 ready 的按内容哈希复用，不会重复导入。
    #
    # 事故任务（imp_d3557678d5b5）被旧代码错记为 succeeded，但 warnings 明确写了
    # "剩余资料未处理"。没有计划时以此判定"实际未完成"，避免真实受害任务无法继续。
    legacy_partial = (not plan) and (
        unprocessed > 0
        or job.get("status") == "partial"
        or any("未处理" in str(w) for w in warnings))
    # 旧任务的失败项（例如被旧代码误判为 archive_corrupt 的 7z）也允许"继续"：
    # 继续会用修好的解包器重跑，不要求用户重传。
    legacy_has_failed = (not plan) and any(
        it["status"] == "failed" for it in public_items)
    can_continue = (
        job.get("status") in TERMINAL_JOB_STATUSES
        and bool(job.get("raw_dir"))
        and (unprocessed > 0 or can_retry or legacy_partial or legacy_has_failed)
    )
    # 事故任务被旧代码错记为 succeeded；这里基于其自身 warnings 呈现为 partial，
    # 且不落库改写历史（只影响返回给前端的视图），用户因此能看到"可继续"。
    public_status = job.get("status")
    summary_text = job.get("summary_text") or ""
    if legacy_partial and public_status == "succeeded":
        public_status = "partial"
        if "未处理" not in summary_text:
            summary_text = (summary_text + "；仍有成员未处理（可继续）").lstrip("；")
    return {
        "job_id": job.get("job_id"),
        "subject_id": job.get("subject_id"),
        "status": public_status,
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "finished_at": job.get("finished_at"),
        "totals": totals,
        "items": public_items,
        "archive_summaries": archive_summaries,
        "kb": kb,
        "summary_text": summary_text,
        # 阶段计时：extract_ms / parse_ms / build_ms / vision_ms / total_ms
        "stage_timings": _load(job.get("stage_timings_json"), {}) or {},
        "can_retry": can_retry,
        "can_continue": can_continue,
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
    new_raw = _copy_raw(raw_rows, new_job_id)

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

    # 复用来源任务里已 ready 的资料，避免重试时重复解析/重复视觉调用
    threading.Thread(target=_run_job_thread, args=(new_job_id,),
                     kwargs={"reuse_job_id": job_id},
                     name=f"import-{new_job_id}", daemon=True).start()
    return new_job_id


def _copy_raw(raw_rows: List[sqlite3.Row], new_job_id: str) -> Path:
    """把原包/原始上传复制到新任务的 raw 目录（保留原包，可跨重启恢复）。"""
    new_raw = _import_tmp_root() / new_job_id / "raw"
    new_raw.mkdir(parents=True, exist_ok=True)
    for row in raw_rows:
        src = Path(row["stored_path"])
        if src.exists():
            shutil.copy2(src, new_raw / src.name)
    return new_raw


def continue_job(job_id: str, user_id: Optional[str] = None) -> Optional[str]:
    """
    从**同一个已保留的原包/原始文件**继续处理未完成成员，不要求用户重传。

    - 只处理"未处理"或"失败且可重试"的成员，已 ready 的不再处理；
    - 跨服务重启：计划与原包都落库/保留，重新解包后按
      (origin_archive, source_path) 与既有条目对齐，不产生重复资料/分块/节点；
    - 幂等复用按 content_sha + 解析配置 + embedding 版本判定，内容变了就重新解析。
    """
    _init_db()
    job = get_job(job_id)
    if job is None:
        return None
    if job.get("status") not in TERMINAL_JOB_STATUSES:
        raise ValueError("任务尚未结束，不能继续")
    if not job.get("can_continue"):
        raise ValueError("没有可继续处理的条目")

    with _DB_LOCK, _connect() as conn:
        row = _job_row(conn, job_id)
        raw_rows = conn.execute(
            "SELECT * FROM import_raw WHERE job_id=? ORDER BY seq", (job_id,)).fetchall()
    if row is None:
        return None

    plan = _load_plan(dict(row))
    item_by_key: Dict[str, Dict[str, Any]] = {}
    for it in job.get("items", []):
        item_by_key[_member_key(it.get("origin_archive"), it.get("source_path"))] = it

    remaining: List[Dict[str, Any]] = []
    seen: set = set()
    for p in plan:
        key = p.get("key") or _member_key(p.get("origin_archive"), p.get("source_path"))
        if key not in item_by_key and key not in seen:
            remaining.append({**p, "key": key})
            seen.add(key)
    for it in job.get("items", []):
        if it.get("status") == "failed" and it.get("retryable"):
            key = _member_key(it.get("origin_archive"), it.get("source_path"))
            if key not in seen:
                remaining.append({"display_name": it.get("display_name"),
                                  "source_path": it.get("source_path"),
                                  "origin_archive": it.get("origin_archive"),
                                  "size": it.get("size"), "key": key})
                seen.add(key)

    # 有计划：只处理剩余成员；无计划（旧任务）：重跑全部，已 ready 的按哈希复用
    only_keys = {r["key"] for r in remaining} if plan else None
    if plan and not only_keys:
        raise ValueError("没有可继续处理的条目")

    new_job_id = _new_id("imp")
    new_raw = _copy_raw(raw_rows, new_job_id)
    with _DB_LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO import_jobs (job_id, subject_id, user_id, status, created_at,"
            " updated_at, totals_json, warnings_json, archive_summaries_json, kb_json,"
            " summary_text, retry_of, continue_of, raw_dir)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_job_id, row["subject_id"], user_id or row["user_id"], "queued",
             _now(), _now(), "{}", "[]", "[]", "{}",
             "继续处理任务已排队", job_id, job_id, str(new_raw)))
        conn.executemany(
            "INSERT INTO import_raw (job_id, seq, filename, stored_path, size)"
            " VALUES (?,?,?,?,?)",
            [(new_job_id, r["seq"], r["filename"],
              str(new_raw / Path(r["stored_path"]).name), r["size"]) for r in raw_rows])
        conn.commit()

    threading.Thread(target=_run_job_thread, args=(new_job_id,),
                     kwargs={"only_keys": only_keys, "reuse_job_id": job_id},
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
    "continue_job",
    "create_import_job",
    "get_job",
    "list_jobs",
    "reconcile_interrupted_jobs",
    "retry_job",
]


# ==================== 服务重启后的中断恢复 ====================


def reconcile_interrupted_jobs() -> int:
    """启动时把**被中断**的导入任务收敛为 partial（可继续），返回收敛数量。

    为什么必须做（2026-10-07 实测）：整机内存压力下后端进程被杀，当时正在运行的
    导入任务行永远停在 status='running'。用户刷新页面只看到"处理中"，
    既没有进度也没有「继续处理」入口，而且这条任务会挡住后续 continue 链。
    任务靠**线程**执行，进程一重启就不可能还有"仍在运行"的任务，
    因此启动时把所有 queued/running 一律收敛为 partial + 可继续。
    """
    _init_db()
    fixed = 0
    with _DB_LOCK, _connect() as conn:
        # 1) 先收敛"进行中"的条目。任务靠线程执行，进程刚启动时不可能还有线程在跑，
        #    因此任何 pending/parsing/indexing 的条目都是上次进程留下的残骸。
        #    不收敛的话页面会永远显示"处理中 N"，与"任务并未在跑"自相矛盾
        #    （2026-10-07 实测：被 reconcile 过一次的 partial 任务仍挂着 25 个 indexing）。
        stale = conn.execute(
            "UPDATE import_items SET status='failed', stage='parsing', progress=NULL,"
            " message=?, error_type='interrupted', retryable=1"
            " WHERE status IN ('pending','parsing','indexing')",
            ("服务重启导致任务中断，可继续处理",)).rowcount
        # 2) 再把停在 queued/running 的任务收敛为 partial（可继续）
        rows = conn.execute(
            "SELECT job_id FROM import_jobs WHERE status IN ('queued','running')"
        ).fetchall()
        for row in rows:
            job_id = row["job_id"]
            unprocessed = _unprocessed_count(conn, job_id)
            ready = conn.execute(
                "SELECT COUNT(*) FROM import_items WHERE job_id=? AND status='ready'",
                (job_id,)).fetchone()[0]
            msg = (f"服务重启导致任务中断：已完成 {ready} 份，"
                   f"{unprocessed} 份未处理（可继续处理）")
            conn.execute(
                "UPDATE import_jobs SET status='partial', updated_at=?, finished_at=?,"
                " summary_text=?, warnings_json=? WHERE job_id=?",
                (_now(), _now(), msg,
                 json.dumps([f"服务重启导致任务中断，剩余 {unprocessed} 份未处理（可继续处理）"],
                            ensure_ascii=False),
                 job_id))
            fixed += 1
        conn.commit()
    if fixed or stale:
        logger.warning(
            f"[import] 启动恢复：收敛 {fixed} 个被中断任务为 partial（可继续），"
            f"清理 {stale} 个残留的进行中条目")
    return fixed
