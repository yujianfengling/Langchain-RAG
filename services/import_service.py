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

_DB_LOCK = threading.RLock()

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


def _item_max_seconds(path: Optional[Path] = None) -> float:
    """单个文件解析的墙钟上限。

    - 默认 180s（env IMPORT_ITEM_MAX_SECONDS）。
    - 大 PDF 单独放宽（env IMPORT_PDF_ITEM_MAX_SECONDS，默认 600s）：
      实测一本 958 页教材按 200 页分段解析，冷启约 88s，但后端同时有其它负载时会超过 180s，
      于是每次都在 180s 被杀、整本重来。按测量只对这类文件放宽，其余仍是 180s。
      放宽是安全的：解析已按页分段并逐段落盘（PdfLoader._load_pdf_in_ranges），
      超时/中断只丢当前段，重试从缺失段继续。
    """
    try:
        value = float(os.getenv("IMPORT_ITEM_MAX_SECONDS", "180"))
    except (TypeError, ValueError):
        value = 180.0
    base = value if value > 0 else 180.0
    if path is not None and str(getattr(path, "suffix", "")).lower() == ".pdf":
        try:
            import fitz
            doc = fitz.open(str(path))
            pages = doc.page_count
            doc.close()
            # 扫描件按页 OCR 通常远慢于文本抽取：不受 300 页阈值限制，
            # 直接给 IMPORT_OCR_ITEM_MAX_SECONDS（默认 1800s）。页级缓存支持断点续跑。
            ocr_on = (os.getenv("IMPORT_OCR_ENGINE", "auto") or "").strip().lower() not in (
                "off", "none", "0", "false")
            if ocr_on and pages > 0:
                try:
                    from rag.loaders import pdf_ocr
                    if pdf_ocr.detect_scanned(path):
                        try:
                            ocr_max = float(os.getenv("IMPORT_OCR_ITEM_MAX_SECONDS", "1800"))
                        except (TypeError, ValueError):
                            ocr_max = 1800.0
                        return max(ocr_max, base)
                except Exception as e:
                    logger.debug(f"扫描件判断失败，按普通 PDF 计时: {e}")
            try:
                threshold = int(os.getenv("IMPORT_PDF_RANGE_THRESHOLD", "300"))
            except (TypeError, ValueError):
                threshold = 300
            if pages > threshold:
                try:
                    big = float(os.getenv("IMPORT_PDF_ITEM_MAX_SECONDS", "600"))
                except (TypeError, ValueError):
                    big = 600.0
                return big if big > 0 else 600.0
        except Exception as e:      # 读页数失败就用默认上限，不影响其它逻辑
            logger.debug(f"读取 PDF 页数失败，使用默认单文件上限: {e}")
    return base


# ==================== 自动分批 / 重试 / 执行者 / 资源 配置 ====================
#
# 2026-10-08：把“人工分批”改成“后台自动分批”。
#   - IMPORT_BATCH_SECONDS（默认 240s）是**单批**的墙钟边界：到边界就保存进度、
#     让出资源、自动进入下一批，直到没有剩余成员；不再要求用户点“继续”。
#   - IMPORT_MAX_SECONDS（formats.limits()，默认 7200s）是**整个任务**的总上限，
#     仅作防失控兜底，不再当作每批边界。
#   - IMPORT_BATCH_MAX（默认 200）是最大批次数；再加上“无进展检测”与“不可恢复错误
#     立即停止”，保证绝不无限循环。


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _batch_seconds() -> float:
    """单批解析的墙钟边界（秒）。默认 240，env IMPORT_BATCH_SECONDS，合法区间 30~3600。"""
    value = _env_float("IMPORT_BATCH_SECONDS", 240.0)
    if value <= 0:
        return 240.0
    return min(max(value, 30.0), 3600.0)


def _batch_max_count() -> int:
    """单任务最大批次数（防失控）。默认 200，env IMPORT_BATCH_MAX。"""
    value = _env_int("IMPORT_BATCH_MAX", 200)
    return value if value > 0 else 200


def _batch_sleep_seconds() -> float:
    """批间让出 CPU 的时间（秒）。默认 0.5，env IMPORT_BATCH_SLEEP_SECONDS。"""
    value = _env_float("IMPORT_BATCH_SLEEP_SECONDS", 0.5)
    return max(0.0, min(value, 30.0))


def _item_max_retries() -> int:
    """网络类瞬时错误的最大重试次数（默认 2，env IMPORT_ITEM_MAX_RETRIES）。"""
    value = _env_int("IMPORT_ITEM_MAX_RETRIES", 2)
    return max(0, min(value, 5))


def _retry_backoff_seconds(attempt: int) -> float:
    """第 attempt（从 1 开始）次重试前的退避秒数：1s/2s/4s，上限 4s。"""
    base = _env_float("IMPORT_RETRY_BACKOFF_SECONDS", 1.0)
    if base <= 0:
        base = 1.0
    return min(base * (2 ** max(0, attempt - 1)), 4.0)


def _executor_timeout_seconds() -> float:
    """执行者心跳超时（秒）。超时视为过期，可被接管。默认 300，env IMPORT_EXECUTOR_TIMEOUT_SECONDS。"""
    value = _env_float("IMPORT_EXECUTOR_TIMEOUT_SECONDS", 300.0)
    return value if value > 0 else 300.0


def _executor_heartbeat_seconds() -> float:
    """心跳刷新间隔（秒）：默认 30，env IMPORT_EXECUTOR_HEARTBEAT_SECONDS。

    上限取超时阈值的 1/5：即使一次长操作或调度抖动拖了几拍，声明也不会过期。
    与最长单次操作的关系见 _HeartbeatWatchdog 的说明。
    """
    timeout = _executor_timeout_seconds()
    value = _env_float("IMPORT_EXECUTOR_HEARTBEAT_SECONDS", 30.0)
    if value <= 0:
        value = 30.0
    cap = max(0.05, timeout / 5.0)
    return max(0.05, min(value, cap))


def _memory_tracking_enabled() -> bool:
    raw = (os.getenv("IMPORT_MEMORY_TRACK", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


# 网络类瞬时错误：有限次退避重试。凭据失效/不支持/加密/路径穿越一律不在其中。
# 注意：item_timeout/vision_timeout **不**自动重试 —— 它们已经耗尽了该文件/该图的墙钟
# 预算，立即重试只会重复等待；单图超时由视觉层自己按张处理。用户仍可主动 retry。
_TRANSIENT_ERROR_TYPES = frozenset({
    "timeout", "connection_error", "network", "network_error",
    "rate_limited", "server_error", "io_error",
})
# 明确不重试（重试不会改变结果，只会浪费资源）：
#   凭据失效(401/403)、明确不支持、加密包、路径穿越、文件过大、缺组件、墙钟超时。
_NON_RETRYABLE_ERROR_TYPES = frozenset({
    "invalid_api_key", "auth_invalid", "unauthorized", "forbidden",
    "unsupported_format", "archive_encrypted", "archive_unsafe_path",
    "archive_unsupported", "archive_corrupt", "archive_limit_exceeded",
    "file_too_large", "pil_unavailable", "converter_unavailable",
    "item_timeout", "vision_timeout",
})


def _is_transient_error(error_type: Optional[str], message: str = "") -> bool:
    """是否为“值得自动重试”的网络类瞬时错误（有限次、带退避）。"""
    et = (error_type or "").strip().lower()
    if et in _TRANSIENT_ERROR_TYPES:
        return True
    if et in _NON_RETRYABLE_ERROR_TYPES:
        return False
    text = (message or "").lower()
    markers = ("timeout", "timed out", "超时", "connection", "连接",
               "reset by peer", "temporarily unavailable", "429", "500",
               "502", "503", "504")
    return any(m in text for m in markers)


# ---- 内存峰值（任务 6：要测量，不要只写注释）----
#
# tracemalloc 记录 Python 分配峰值；RSS 记录进程真实驻留。两者都落库：
#   item.timings.memory_peak_mb / rss_mb
#   job.stage_timings.parse_memory_peak_mb / build_memory_peak_mb / rss_peak_mb


def _mem_start() -> bool:
    """启动 tracemalloc 并清零峰值；已启动则不重复。失败返回 False。"""
    if not _memory_tracking_enabled():
        return False
    try:
        import tracemalloc
        if not tracemalloc.is_tracing():
            tracemalloc.start()
        tracemalloc.reset_peak()
        return True
    except Exception:
        return False


def _mem_current_bytes() -> int:
    try:
        import tracemalloc
        if not tracemalloc.is_tracing():
            return 0
        return int(tracemalloc.get_traced_memory()[0])
    except Exception:
        return 0


def _mem_peak_mb(baseline_current: int = 0) -> float:
    """自上次 reset_peak 以来的额外分配峰值（MB），减去 baseline。"""
    try:
        import tracemalloc
        if not tracemalloc.is_tracing():
            return 0.0
        _cur, peak = tracemalloc.get_traced_memory()
        delta = int(peak) - int(baseline_current or 0)
        return round(max(0, delta) / (1024 * 1024), 3)
    except Exception:
        return 0.0


def _mem_reset_peak() -> None:
    try:
        import tracemalloc
        if tracemalloc.is_tracing():
            tracemalloc.reset_peak()
    except Exception:
        pass


def _rss_mb() -> float:
    """进程 RSS（MB）。psutil 优先；否则 Windows ctypes；再否则 0（不假报）。"""
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 2)
    except Exception:
        pass
    try:
        import resource  # 类 Unix
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux: KB；macOS: bytes
        return round(usage / 1024, 2) if usage > 0 else 0.0
    except Exception:
        pass
    if os.name != "nt":
        return 0.0
    try:
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _PMC()
        counters.cb = ctypes.sizeof(_PMC)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb)
        if ok:
            return round(counters.WorkingSetSize / (1024 * 1024), 2)
    except Exception:
        pass
    return 0.0


# ---- 单文件/单图超时后的协作式中止（任务 6）----
#
# Python 无法强杀线程：被放弃的解析线程仍会跑完。为了做到“超时后不再派发新工作”，
# 这里给每个解析线程挂一个 abort Event；解析线程在**每次派发新的视觉调用前**检查它。
# 父线程在单文件墙钟超时后置位该 Event，被放弃的线程下一次循环即停止派发。
_ACTIVE_PARSE_ABORTS: Dict[int, "threading.Event"] = {}
_ACTIVE_PARSE_ABORTS_LOCK = threading.Lock()


def _vision_abort_requested() -> bool:
    """当前线程所属的解析是否已被放弃（供增强器在派发新视觉工作前检查）。"""
    with _ACTIVE_PARSE_ABORTS_LOCK:
        event = _ACTIVE_PARSE_ABORTS.get(threading.get_ident())
    return bool(event is not None and event.is_set())


# ---- 页级进度（2026-10-28）----
#
# 不改 `_parse_file/_parse_with_timeout/_parse_file_guarded` 的签名（多处测试直接
# monkeypatch 成 4 元组）。用线程局部表把“当前解析哪个 item”传给解析线程，
# 由 pdf_loader 每完成一页调用 `_report_parse_progress`；写库节流，失败静默。
_PENDING_ITEMS_BY_PATH: Dict[str, str] = {}
_ACTIVE_PARSE_PROGRESS: Dict[int, str] = {}
_PROGRESS_LOCK = threading.Lock()
_PROGRESS_LAST: Dict[str, float] = {}


def _report_parse_progress(done: int, total: int) -> None:
    """页级进度上报：≥2s 或每 2% 才写一次库；任何异常都不能影响解析。"""
    if not total or int(total) <= 0:
        return
    done = max(0, min(int(done), int(total)))
    tid = threading.get_ident()
    with _PROGRESS_LOCK:
        item_id = _ACTIVE_PARSE_PROGRESS.get(tid)
    if not item_id:
        return
    now = time.monotonic()
    last = _PROGRESS_LAST.get(item_id, 0.0)
    step = max(1, int(total) // 50)
    if done < total and (now - last) < 2.0 and (done % step) != 0:
        return
    _PROGRESS_LAST[item_id] = now
    try:
        _update_item(item_id, progress=min(0.99, done / float(total)),
                     message=f"解析中 {done}/{total} 页")
    except Exception:
        pass


def _ocr_user_note(display_name: str, stats: Dict[str, Any]) -> str:
    """把扫描件的可理解边界写成一句用户可读的话（进 job.warnings，前端可见）。"""
    if not stats:
        return ""
    pages = int(stats.get("pages") or 0)
    with_text = int(stats.get("pages_with_text") or 0)
    blank = int(stats.get("pages_blank") or 0)
    failed = int(stats.get("pages_failed") or 0)
    parts = [f"{display_name}：扫描件走本地 OCR（{stats.get('engine') or '未知引擎'}）",
             f"{with_text}/{pages} 页识别出文字"]
    if blank:
        preview = "、".join(str(p) for p in (stats.get("pages_blank_list") or [])[:10])
        parts.append(f"{blank} 页未识别到文字（第 {preview} 页）")
    if failed:
        preview = "、".join(str(p) for p in (stats.get("pages_failed_list") or [])[:10])
        parts.append(f"{failed} 页识别失败需人工确认（第 {preview} 页）")
    parts.append("插图/图表未作理解，仅取到其中印刷文字")
    return "；".join(parts)


def _parse_config_signature() -> str:
    """解析配置指纹：用于"内容没变就复用"的幂等判定（视觉提示词版本/模式等）"""
    from rag.enrichment.multimodal_enricher import VISION_PROMPT_VERSION
    return "|".join([
        f"vision_prompt={VISION_PROMPT_VERSION}",
        f"vision_mode={os.getenv('VISION_MODE', 'vision')}",
        f"pdf_map={os.getenv('PDF_HEADING_DETECT', '1')}",
        f"ocr={os.getenv('IMPORT_OCR_ENGINE', 'auto')}",
        "ctx=2",
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
        # 执行者声明（任务 2）：同一任务只有一个执行者。
        # executor_id 非空且心跳未超时 → 拒绝重复执行；心跳超时可接管。
        "executor_id": "TEXT",
        "executor_heartbeat": "REAL",
        # 执行者声明代数（围栏/fencing）：每次成功声明 +1。旧执行者的 epoch 必然落后，
        # 于是"我是否仍是当前执行者"可由 (executor_id, executor_epoch) 精确判断。
        "executor_epoch": "INTEGER DEFAULT 0",
        # 用户意图（任务 3）：暂停/取消后**绝不自动重启**；resume 是唯一清除入口。
        "pause_requested": "INTEGER DEFAULT 0",
        "paused": "INTEGER DEFAULT 0",
        # 自动分批进度（任务 1）：已完成的批次数
        "batch_index": "INTEGER DEFAULT 0",
        # 分类计数（任务 4）：members_discovered / containers / directories / materials
        "discovery_json": "TEXT",
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
        # 2026-10-28：大文件走“已落盘的临时文件”，避免整份读进内存；
        # bytes 入参保持原行为（测试与旧调用不变）。
        is_path = isinstance(content, (str, Path))
        if is_path:
            try:
                size = Path(content).stat().st_size
            except OSError:
                rejected.append({"filename": filename,
                                 "reason": "临时文件读取失败（可能已被清理）",
                                 "error_type": "io_error"})
                continue
        else:
            size = len(content or b"")
        if size == 0:
            rejected.append({"filename": filename, "reason": "文件内容为空",
                             "error_type": "empty_content"})
            continue
        is_archive = formats.is_archive(ext) if ext else False
        limit_bytes = archive_bytes if is_archive else doc_bytes
        if size > limit_bytes:
            actual_mb = round(size / (1024 * 1024), 2)
            limit_mb = limit_bytes // (1024 * 1024)
            kind = "压缩包" if is_archive else "普通文档"
            rejected.append({
                "filename": filename,
                "reason": f"文件实际 {actual_mb}MB，超过{kind}单文件上限 {limit_mb}MB",
                "error_type": "size_exceeded"})
            continue
        if total + size > max_total_bytes:
            actual_mb = round((total + size) / (1024 * 1024), 2)
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
        total += size
        accepted.append((filename, content))

    job_id = _new_id("imp")
    raw_dir = _import_tmp_root() / job_id / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    raw_rows: List[Tuple[int, str, str, int]] = []
    for seq, (filename, content) in enumerate(accepted):
        base = Path(filename.replace("\\", "/")).name
        safe = _safe_stored_name(base)
        dest = raw_dir / f"{seq:03d}_{safe}"
        if isinstance(content, (str, Path)):
            src = Path(content)
            try:
                os.replace(src, dest)          # 同一数据盘：原子重命名，不复制
            except OSError:
                shutil.move(str(src), str(dest))
            size = dest.stat().st_size
        else:
            dest.write_bytes(content)
            size = len(content)
        raw_rows.append((seq, base, str(dest), size))

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

    # 2026-10-08：同名不同内容按"**修改**"处理 —— 替换旧版本，而不是再存一份副本。
    # 语义变化的原因：把修改当作"新增一份副本"会让同一份资料在库里越积越多，
    # 旧分块不会被撤除，检索可能同时召回新旧两个版本。
    # 旧版本字节移入 <学科>/_superseded/ 留档（建库只扫学科根目录下的文件，
    # 子目录不会被索引），因此"替换"既不丢数据、也不会让旧版本继续被召回。
    return {"name": desired, "skip": False,
            "reason": "同名不同内容，已替换旧版本（旧版本留档于 _superseded/）",
            "renamed": True, "existed": True, "replace": True,
            "old_sha": existing_hash}


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
    raw_meta = dict(getattr(result, "metadata", None) or {})
    nested = raw_meta.get("vision_stats")
    vision_stats = nested if isinstance(nested, dict) else raw_meta
    meta: Dict[str, Any] = {
        "vision_stats": vision_stats or {},
        "ocr_stats": raw_meta.get("ocr_stats") or {},
        "pages_total": raw_meta.get("pages_total") or 0,
        "pages_parsed": raw_meta.get("pages_parsed") or 0,
        "parse_engine": raw_meta.get("parse_engine") or "",
    }
    meta["parse_ms"] = int((time.monotonic() - start) * 1000)
    if result.success:
        return result.documents, None, None, meta
    return [], result.error_type or "parse_error", result.error_message or "解析失败", meta


def _parse_file_guarded(path: Path, abort: "threading.Event"
                        ) -> Tuple[List[Any], Optional[str], Optional[str], Dict[str, Any]]:
    """在解析线程里登记 abort Event 与页级进度槽（2026-10-28）。"""
    tid = threading.get_ident()
    with _ACTIVE_PARSE_ABORTS_LOCK:
        _ACTIVE_PARSE_ABORTS[tid] = abort
    item_id = None
    try:
        with _PROGRESS_LOCK:
            item_id = _PENDING_ITEMS_BY_PATH.pop(str(Path(path).resolve()), None)
            if item_id:
                _ACTIVE_PARSE_PROGRESS[tid] = item_id
        return _parse_file(path)
    finally:
        with _ACTIVE_PARSE_ABORTS_LOCK:
            _ACTIVE_PARSE_ABORTS.pop(tid, None)
        with _PROGRESS_LOCK:
            _ACTIVE_PARSE_PROGRESS.pop(tid, None)


def _parse_with_timeout(path: Path, timeout: float
                        ) -> Tuple[List[Any], Optional[str], Optional[str], Dict[str, Any]]:
    """
    在单文件墙钟上限内解析；超时记为 item_timeout 并**继续处理后面的文件**。

    Python 无法强杀线程：被放弃的解析线程仍会跑完。这里在超时后置位 abort Event，
    被放弃线程在**每次派发新的视觉调用前**检查它，从而做到“超时后不再派发新工作”。
    """
    start = time.monotonic()
    _mem_reset_peak()
    mem_base = _mem_current_bytes()
    abort = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="import-parse")
    future = executor.submit(_parse_file_guarded, path, abort)
    try:
        docs, error_type, message, meta = future.result(timeout=timeout)
        meta.setdefault("parse_ms", int((time.monotonic() - start) * 1000))
        meta["item_timed_out"] = False
        meta["memory_peak_mb"] = _mem_peak_mb(mem_base)
        meta["rss_mb"] = _rss_mb()
        meta.setdefault("retries", 0)
        return docs, error_type, message, meta
    except FuturesTimeout:
        # 协作式中止：被放弃的线程下一次派发视觉前会看到该 Event 并停止。
        abort.set()
        logger.warning(f"单文件解析超时（>{timeout:g}s），跳过并继续: {path.name}")
        return [], "item_timeout", (
            f"单文件解析超时（>{timeout:g}s），已跳过该文件并继续处理其余文件"), {
                "parse_ms": int((time.monotonic() - start) * 1000),
                "vision_stats": {}, "item_timed_out": True,
                "memory_peak_mb": _mem_peak_mb(mem_base), "rss_mb": _rss_mb(),
                "retries": 0}
    except Exception as e:  # 解析线程自身异常也要给出准确原因
        logger.warning(f"单文件解析异常: {path.name}: {e}")
        return [], "parse_error", f"解析异常: {e}", {
            "parse_ms": int((time.monotonic() - start) * 1000),
            "vision_stats": {}, "item_timed_out": False,
            "memory_peak_mb": _mem_peak_mb(mem_base), "rss_mb": _rss_mb(),
            "retries": 0}
    finally:
        executor.shutdown(wait=False)


def _parse_item_with_retries(path: Path
                             ) -> Tuple[List[Any], Optional[str], Optional[str], Dict[str, Any]]:
    """
    单文件解析 + 网络类瞬时错误的有界退避重试（任务 5）。

    - 只在 _is_transient_error() 为真时重试，最多 IMPORT_ITEM_MAX_RETRIES 次；
    - 退避 1s/2s/4s（IMPORT_RETRY_BACKOFF_SECONDS，上限 4s）；
    - 凭据失效(401/403)、明确不支持、加密包、路径穿越一律不重试，立即明确失败。
    """
    max_retries = _item_max_retries()
    attempt = 0
    while True:
        docs, error_type, message, meta = _parse_with_timeout(
            path, _item_max_seconds(path))
        if (docs or attempt >= max_retries
                or not _is_transient_error(error_type, message or "")):
            meta["retries"] = attempt
            if attempt:
                meta["retry_error_type"] = error_type
            return docs, error_type, message, meta
        attempt += 1
        backoff = _retry_backoff_seconds(attempt)
        logger.warning(
            f"单文件瞬时错误，第 {attempt}/{max_retries} 次重试（退避 {backoff:g}s）:"
            f" {path.name}: {error_type}")
        time.sleep(backoff)


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
    # 2026-10-31 正文先行：含 deferred 图片的文档集不进解析缓存——
    # 图片内容由后台增强后增量补入；缓存了会导致重复导入复用"缺图片"的版本。
    if any((getattr(d, "metadata", {}) or {}).get("vision_status") == "deferred"
           for d in docs):
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
    _fence_guard()   # 失去执行权：一律不写
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
    except _ExecutorLostError as e:
        # 被接管：新执行者负责收尾。旧执行者绝不写终态、绝不建库。
        logger.warning(f"[import][fencing] 任务 {job_id} 原执行者已停止并退出：{e}")
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
    _fence_guard()   # 失去执行权：一律不写
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
    _fence_guard()   # 失去执行权：一律不写
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
    _fence_guard()   # 失去执行权：一律不写
    item_id = _new_id("itm")
    cols = ["item_id", "job_id", "seq"] + list(fields)
    values = [item_id, job_id, seq] + list(fields.values())
    placeholders = ",".join("?" for _ in cols)
    with _DB_LOCK, _connect() as conn:
        conn.execute(f"INSERT INTO import_items ({','.join(cols)}) VALUES ({placeholders})",
                     values)
        conn.commit()
    return item_id


def _new_executor_id() -> str:
    """本执行者标识：主机:进程:线程:随机后缀（用于执行者声明与排查）"""
    import socket
    host = (socket.gethostname() or "host")[:24]
    return f"{host}:{os.getpid()}:{threading.get_ident()}:{uuid.uuid4().hex[:6]}"


def _claim_executor_ex(job_id: str, executor_id: str) -> Optional[int]:
    """
    尝试声明执行者并返回本次声明的单调递增代数（epoch）；被拒绝返回 None。

    True/False 语义见 _claim_executor。epoch 是围栏（fencing）的核心：
    同一任务每被成功声明一次就 +1，旧执行者手里的 epoch 必然落后，
    因此即使旧线程仍在运行，也能在**每次写入/建库前**识别"我已不是当前执行者"。

    判定用**一条条件 UPDATE**（SQL 层原子），而不是"先读后写"：
    跨进程并发触发（双击/重试/resume/auto_resume）也不会两个都成功。
    """
    now = time.time()
    cutoff = now - _executor_timeout_seconds()
    with _DB_LOCK, _connect() as conn:
        if _job_row(conn, job_id) is None:
            return None
        cur = conn.execute(
            "UPDATE import_jobs SET executor_id=?, executor_heartbeat=?,"
            " executor_epoch=COALESCE(executor_epoch, 0) + 1, updated_at=?"
            " WHERE job_id=? AND (executor_id IS NULL OR executor_id=?"
            " OR executor_heartbeat IS NULL OR executor_heartbeat < ?)",
            (executor_id, now, _now(), job_id, executor_id, cutoff))
        if cur.rowcount != 1:
            conn.commit()
            return None
        row = conn.execute(
            "SELECT executor_epoch FROM import_jobs WHERE job_id=?", (job_id,)).fetchone()
        conn.commit()
    try:
        return int(row["executor_epoch"])
    except (TypeError, ValueError, KeyError, IndexError):
        return 1


def _claim_executor(job_id: str, executor_id: str) -> bool:
    """
    尝试声明执行者（任务 2：同一任务只有一个执行者）。

    True = 获得执行权：任务当前无执行者 / 执行者就是自己（续期）/ 原心跳已超时（可接管）。
    False = 已有**活跃**执行者；调用方必须拒绝执行，绝不静默重复跑。
    """
    return _claim_executor_ex(job_id, executor_id) is not None


def _refresh_executor(job_id: str, executor_id: str,
                      epoch: Optional[int] = None) -> bool:
    """续期心跳（看护线程 / 批边界 / 建库前调用）。

    epoch 非空时同时校验代数（围栏）：True = 仍持有声明；False = 已被接管。
    数据库异常按"暂不确定"处理（返回 True），避免把瞬时错误误判成失活、误杀正常任务。
    """
    try:
        with _DB_LOCK, _connect() as conn:
            if epoch is None:
                cur = conn.execute(
                    "UPDATE import_jobs SET executor_heartbeat=?"
                    " WHERE job_id=? AND executor_id=?",
                    (time.time(), job_id, executor_id))
            else:
                cur = conn.execute(
                    "UPDATE import_jobs SET executor_heartbeat=?"
                    " WHERE job_id=? AND executor_id=? AND executor_epoch=?",
                    (time.time(), job_id, executor_id, int(epoch)))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"执行者心跳续期失败（按仍持有处理）: {job_id}: {e}")
        return True


# ---- 写入围栏（fencing）：失去执行权的旧线程绝不再写 ----
#
# Python 无法强杀线程：心跳过期被接管（或 resume/auto_resume 重新声明）后，
# 旧执行者线程可能仍在跑。仅靠批边界检查不够——它可能正处在一次长建库/落盘中。
# 因此每次写入/建库前都核对 (executor_id, executor_epoch) 是否仍属于自己；
# 一旦失去声明：打明确日志、停止写入并退出，绝不静默继续。


class _ExecutorLostError(RuntimeError):
    """本执行者已失去该任务的执行权（被接管）。用于中止后续所有写入。"""


_FENCE_LOCAL = threading.local()


def _set_executor_fence(job_id: str, executor_id: str, epoch: int) -> None:
    _FENCE_LOCAL.fence = (job_id, executor_id, int(epoch))


def _clear_executor_fence() -> None:
    if hasattr(_FENCE_LOCAL, "fence"):
        del _FENCE_LOCAL.fence


def _executor_fence() -> Optional[Tuple[str, str, int]]:
    return getattr(_FENCE_LOCAL, "fence", None)


def _is_executor_current(job_id: str, executor_id: str, epoch: int) -> bool:
    """我是否仍是该任务的当前执行者（id 与 epoch 都一致）。

    读失败按"已失活"处理（安全优先）：宁可不写，也绝不出现两个执行者同时提交。
    """
    try:
        with _DB_LOCK, _connect() as conn:
            row = conn.execute(
                "SELECT executor_id, executor_epoch FROM import_jobs WHERE job_id=?",
                (job_id,)).fetchone()
        if row is None:
            return False
        return (row["executor_id"] == executor_id
                and int(row["executor_epoch"] or 0) == int(epoch))
    except Exception as e:
        logger.warning(f"[import][fencing] 执行权核对失败，按已失活停止写入: {job_id}: {e}")
        return False


def _fence_lost() -> bool:
    """非抛出版本的围栏检查（主循环据此优雅退出）。未登记围栏时恒为 False。"""
    fence = _executor_fence()
    if fence is None:
        return False
    return not _is_executor_current(*fence)


def _fence_guard() -> None:
    """写入/建库前调用：失去执行权立即抛出，阻断任何落盘/建库/条目写入。"""
    fence = _executor_fence()
    if fence is None:
        return
    job_id, executor_id, epoch = fence
    if not _is_executor_current(job_id, executor_id, epoch):
        raise _ExecutorLostError(
            f"[import][fencing] 任务 {job_id} 的执行权已被接管"
            f"（本执行者 {executor_id} epoch={epoch}）；停止写入并退出")


class _HeartbeatWatchdog:
    """独立看护线程：与主工作线程解耦，长解析/长建库期间照常续期心跳。

    为什么必须独立：实测该学科一次建库 build_ms≈424s，远大于默认超时 300s；
    若只在批边界刷新，长建库期间心跳必过期 → 被误判失活 → 被接管 → 两个执行者。
    接管判定只基于"心跳是否过期"，与单次操作耗时无关；这里用心跳间隔
    （默认 30s，且不超过超时阈值的 1/5）保证任何单次长操作期间都有多次续期。
    """

    def __init__(self, job_id: str, executor_id: str, epoch: int) -> None:
        self.job_id = job_id
        self.executor_id = executor_id
        self.epoch = int(epoch)
        self.lost = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"import-hb-{job_id}", daemon=True)

    def _run(self) -> None:
        interval = _executor_heartbeat_seconds()
        while not self._stop.wait(interval):
            if not _refresh_executor(self.job_id, self.executor_id, self.epoch):
                self.lost.set()
                logger.warning(
                    f"[import][fencing] 任务 {self.job_id} 心跳续期被拒绝：执行权已被接管"
                    f"（executor_id={self.executor_id} epoch={self.epoch}）")
                return

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def _release_executor(job_id: str, executor_id: str,
                      epoch: Optional[int] = None) -> None:
    """释放自己的执行者声明（只清自己的，避免误清接管者）。

    epoch 非空时一并核对代数：只有"仍是我这一代"才清，防止旧执行者退出时
    误清掉接管者的声明。
    """
    try:
        with _DB_LOCK, _connect() as conn:
            if epoch is None:
                conn.execute(
                    "UPDATE import_jobs SET executor_id=NULL, executor_heartbeat=NULL"
                    " WHERE job_id=? AND executor_id=?",
                    (job_id, executor_id))
            else:
                conn.execute(
                    "UPDATE import_jobs SET executor_id=NULL, executor_heartbeat=NULL"
                    " WHERE job_id=? AND executor_id=? AND executor_epoch=?",
                    (job_id, executor_id, int(epoch)))
            conn.commit()
    except Exception as e:
        logger.warning(f"执行者声明释放失败: {job_id}: {e}")


def _is_paused(job_id: str) -> bool:
    with _DB_LOCK, _connect() as conn:
        row = conn.execute("SELECT paused FROM import_jobs WHERE job_id=?",
                           (job_id,)).fetchone()
    return bool(row and row["paused"])


def _is_pause_requested(job_id: str) -> bool:
    """用户是否要求暂停（pause_requested 或已 paused 且未取消）。"""
    with _DB_LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT pause_requested, paused, status FROM import_jobs WHERE job_id=?",
            (job_id,)).fetchone()
        if row is None:
            return True
        if row["status"] == "cancelled":
            return False
        return bool(row["pause_requested"]) or bool(row["paused"])


def _save_discovery(job_id: str, discovery: Dict[str, Any]) -> None:
    _set_job_status_values(
        job_id, discovery_json=json.dumps(discovery, ensure_ascii=False))


def _save_batch_index(job_id: str, batch_no: int) -> None:
    _set_job_status_values(job_id, batch_index=int(batch_no))


def _remaining_candidates(job_id: str,
                          candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """计划里尚未建立 item 的候选。已建 item 的成员（含 failed）不再自动重跑，
    保证自动分批有界；失败项走用户主动 retry/continue。"""
    with _DB_LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT origin_archive, source_path FROM import_items WHERE job_id=?",
            (job_id,)).fetchall()
    keys = {_member_key(r["origin_archive"], r["source_path"]) for r in rows}
    return [c for c in candidates
            if _member_key(c.get("origin_archive"),
                           c.get("source_path")) not in keys]


def _terminal_item_count(job_id: str) -> int:
    with _DB_LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM import_items WHERE job_id=? AND status IN"
            " ('ready','failed','skipped')", (job_id,)).fetchone()
    return int(row[0] if row else 0)


def _pause_finish(job_id: str, warnings: List[str],
                  archive_summaries: List[Dict[str, Any]],
                  stage_timings: Dict[str, Any]) -> None:
    """在批边界暂停：置 paused=1、status=partial，**不自动重启**。"""
    counts = _item_counts(job_id)
    with _DB_LOCK, _connect() as conn:
        unprocessed = _unprocessed_count(conn, job_id)
    summary = (f"已暂停：{counts['ready']} 份可学习，{unprocessed} 份未处理"
               f"（可继续）")
    _set_job_status_values(job_id, pause_requested=0, paused=1)
    _finish_job(job_id, "partial", summary, warnings, archive_summaries, stage_timings)


def _clear_interrupted_items(job_id: str) -> None:
    """删除“服务重启导致中断”的条目，使其重新成为未处理成员而被自动分批重跑。

    reconcile 会把 in-flight 条目记为 failed/error_type=interrupted；若不清掉，
    _remaining_candidates 会认为它们已处理，自动接续/恢复就永远无法真正继续。
    只删 error_type='interrupted' 的条目，真实失败（build_failed 等）不受影响。"""
    try:
        with _DB_LOCK, _connect() as conn:
            conn.execute(
                "DELETE FROM import_items WHERE job_id=? AND error_type='interrupted'",
                (job_id,))
            conn.commit()
    except Exception as e:
        logger.warning(f"清理被中断条目失败（不影响主流程）: {job_id}: {e}")


def _run_job(job_id: str, only_keys: Optional[set] = None,
             reuse_job_id: Optional[str] = None) -> None:
    """
    执行导入任务（任务 1：后台自动分批；任务 2：单一执行者）。

    - 启动前先 claim 执行者；已被活跃执行者持有则拒绝执行并记日志。
    - 单批到 IMPORT_BATCH_SECONDS 边界就保存进度、让出资源、自动进入下一批，
      直到没有剩余成员；整个过程在同一 job 内完成，不要求用户点“继续”。
    """
    executor_id = _new_executor_id()
    epoch = _claim_executor_ex(job_id, executor_id)
    if epoch is None:
        logger.warning(
            f"[import] 任务 {job_id} 已被活跃执行者持有，拒绝重复执行"
            "（不会产生重复资料/重复索引）")
        return
    # 围栏：登记"本线程 = 本任务的当前执行者(id+epoch)"，此后每次写入/建库前核对。
    _set_executor_fence(job_id, executor_id, epoch)
    # 独立看护线程：长解析/长建库期间持续续期心跳，避免被误判失活而遭接管。
    watchdog = _HeartbeatWatchdog(job_id, executor_id, epoch)
    watchdog.start()
    try:
        _run_job_locked(job_id, executor_id, only_keys=only_keys,
                        reuse_job_id=reuse_job_id, epoch=epoch)
    finally:
        watchdog.stop()
        _clear_executor_fence()
        _release_executor(job_id, executor_id, epoch)


def _run_job_locked(job_id: str, executor_id: str,
                    only_keys: Optional[set] = None,
                    reuse_job_id: Optional[str] = None,
                    epoch: Optional[int] = None) -> None:
    # 本函数跑在后台线程里，而知识库构建会在**这里**第一次导入 knowledge_service →
    # "from rag.rag_service import ..."。若此时另一个线程（如启动预热）也在导入 rag，
    # Python 3.14 会抛 deadlock detected by _ModuleLock('rag.rag_service')，
    # 任务直接以“任务执行异常”失败且 0 条目（2026-10-07 实测 imp_9938303fadb9）。
    # 统一走 rag_loader 的进程锁，把“第一次导入”串行化。
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
    # 用户主动暂停/取消的任务**绝不自动重启**（任务 3）；resume 是唯一清除入口。
    if _is_paused(job_id) or _is_cancelled(job_id):
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
    # 2026-10-31 正文先行：deferred 图片文档（建库发布成功后统一增强补入）
    deferred_vision_docs: List[Any] = []
    deferred_vision_dirs: set = set()
    stage_timings: Dict[str, Any] = {"extract_ms": 0, "parse_ms": 0,
                                     "build_ms": 0, "vision_ms": 0, "total_ms": 0}
    _mem_start()
    stage_timings["rss_start_mb"] = _rss_mb()

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
        # ---------- 1) 展开压缩包 / 生成候选资料与计划 / 分类计数 ----------
        t_extract = time.monotonic()
        candidates: List[Dict[str, Any]] = []
        job_seq = 0
        discovery: Dict[str, int] = {"members_discovered": 0, "containers": 0,
                                     "directories": 0, "materials": 0}
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
                    discovery["containers"] += 1
                    discovery["members_discovered"] += 1
                    # 被接受的每个文件都必须在 items 里有终态记录：
                    # 压缩包自身损坏/加密时也如实失败（带 error_type 与可读原因）。
                    job_seq += 1
                    _insert_item(
                        job_id, job_seq, display_name=filename, source_path=filename,
                        origin_archive=filename, size=row["size"],
                        status="failed", stage="parsing", progress=None,
                        message=f"压缩包处理失败：{reason}",
                        error_type=error_type, retryable=0)
                    continue
                entries = result["entries"]
                nested = result.get("nested") or []
                discovery["containers"] += 1 + len(nested)
                discovery["directories"] += int(result.get("directories") or 0)
                # 发现条目 = 解出的文件 + 被跳过的条目 + 嵌套压缩包本身（容器单列）
                discovery["members_discovered"] += (
                    len(entries) + len(result["skipped"]) + len(nested))
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
                discovery["members_discovered"] += 1
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

        discovery["materials"] = len(candidates)
        _save_discovery(job_id, discovery)
        plan = [{
            "display_name": c["display_name"],
            "source_path": c["source_path"],
            "origin_archive": c["origin_archive"],
            "size": c["size"],
        } for c in candidates]
        _save_plan(job_id, plan)
        stage_timings["extract_ms"] = int((time.monotonic() - t_extract) * 1000)
        stage_timings["members_discovered"] = discovery["members_discovered"]
        stage_timings["containers"] = discovery["containers"]
        stage_timings["directories"] = discovery["directories"]
        stage_timings["materials"] = discovery["materials"]

        # ---------- 2) 自动分批：解析 + 复制 + 建库 ----------
        subject_dir = _subject_dir(subject_id)
        subject_dir.mkdir(parents=True, exist_ok=True)
        copied_all: List[str] = []
        # 被"修改"替换掉的旧版本留档：(superseded_path, original_path)，
        # 仅在建库成功前用于回滚；建库成功后清空（留档保留）。
        replaced_all: List[tuple] = []
        max_batches = _batch_max_count()
        total_limit = float(limits.get("max_seconds") or 0)
        batch_seconds = _batch_seconds()
        batch_no = 0
        t_parse = time.monotonic()

        while True:
            if _fence_lost():
                logger.warning(
                    f"[import][fencing] 任务 {job_id} 执行权已被接管，"
                    "原执行者停止写入并退出（不再产生任何资料/索引）")
                return
            if _is_cancelled(job_id):
                _remove_copied(subject_dir, copied_all)
                _restore_replaced(replaced_all)
                _cancel_finish(job_id, "任务已取消（已导入的资料不会删除）")
                return
            if _is_pause_requested(job_id):
                stage_timings["parse_ms"] = int((time.monotonic() - t_parse) * 1000)
                stage_timings["total_ms"] = int((time.monotonic() - t_wall) * 1000)
                _pause_finish(job_id, warnings, archive_summaries, stage_timings)
                return
            if total_limit > 0 and _monotonic() - started > total_limit:
                warnings.append(
                    f"整个任务超过总上限 {int(total_limit)}s，剩余成员未处理（可继续）")
                break
            if batch_no >= max_batches:
                warnings.append(
                    f"已达最大批次数 {max_batches}，停止自动分批（剩余成员可继续处理）")
                break
            remaining = _remaining_candidates(job_id, candidates)
            if not remaining:
                break
            batch_no += 1
            batch_started = _monotonic()
            term_before = _terminal_item_count(job_id)
            parsed_docs: List[Any] = []
            ready_names: set = set()
            _mem_reset_peak()
            batch_mem_base = _mem_current_bytes()

            for cand in remaining:
                if _fence_lost():
                    logger.warning(
                        f"[import][fencing] 任务 {job_id} 执行权已被接管，"
                        "原执行者停止写入并退出（不再产生任何资料/索引）")
                    return
                if _is_cancelled(job_id):
                    _remove_copied(subject_dir, copied_all)
                    _restore_replaced(replaced_all)
                    _cancel_finish(job_id, "任务已取消（已导入的资料不会删除）")
                    return
                if _is_pause_requested(job_id):
                    break      # 到下一个批边界暂停：本批已解析内容仍会建库落库
                if _monotonic() - batch_started > batch_seconds:
                    break      # 批边界：保存进度、让出资源、自动进入下一批
                if total_limit > 0 and _monotonic() - started > total_limit:
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
                            _fence_guard()
                            _register_material(subject_id, final_name,
                                               stored_ready.stat().st_size, user_id)
                            _update_item(
                                item_id, status="ready", stage="indexing", progress=1.0,
                                message="复用已就绪内容（内容哈希一致，未重复解析）",
                                doc_id=reuse.get("doc_id"), stored_path=str(stored_ready),
                                content_sha=src_sha, reuse_key=reuse_key,
                                parse_config=parse_cfg, timings_json=json.dumps(
                                    {"parse_ms": 0, "reused": True},
                                    ensure_ascii=False))
                            continue

                try:
                    with _PROGRESS_LOCK:
                        _PENDING_ITEMS_BY_PATH[str(src_path.resolve())] = item_id
                    docs, error_type, message, meta = _parse_item_with_retries(src_path)
                finally:
                    with _PROGRESS_LOCK:
                        _PENDING_ITEMS_BY_PATH.pop(str(src_path.resolve()), None)
                vision_stats = meta.get("vision_stats") or {}
                ocr_stats = meta.get("ocr_stats") or {}
                stage_timings["vision_ms"] += int(vision_stats.get("vision_ms") or 0)
                stage_timings["ocr_ms"] = int(stage_timings.get("ocr_ms") or 0) + int(
                    ocr_stats.get("elapsed_ms") or 0)
                # 2026-10-31 正文先行：deferred 图片文档移出正文流（不进切分/建库），
                # 收集（含 image_path）待建库发布成功后统一增强并增量补入索引。
                _still_deferred = [
                    _d for _d in (docs or [])
                    if (getattr(_d, "metadata", {}) or {}).get("vision_status") == "deferred"]
                had_deferred_this_item = bool(_still_deferred)
                if _still_deferred:
                    deferred_vision_dirs.update(
                        str(Path((_d.metadata or {}).get("image_path") or "").parent)
                        for _d in _still_deferred
                        if (_d.metadata or {}).get("image_path"))
                    # 图片文件在 import_tmp 会被任务收尾清理——deferred 前先
                    # 持久化到 get_data_dir()/deferred_vision/<job_id>/，并更新
                    # image_path，否则后台增强时文件已丢失（2026-10-31 实测）。
                    persist_dir = Path(get_data_dir()) / "deferred_vision" / job_id
                    persist_dir.mkdir(parents=True, exist_ok=True)
                    for _d in _still_deferred:
                        _d.metadata["source_content_sha"] = src_sha

                        deferred_vision_docs.append(_d)
                        src_img = (_d.metadata or {}).get("image_path")
                        if src_img and Path(src_img).exists():
                            dst_img = persist_dir / (
                                f"{len(deferred_vision_docs):04d}_"
                                + Path(src_img).name)
                            try:
                                shutil.copy2(src_img, dst_img)
                                _d.metadata["image_path"] = str(dst_img)
                            except OSError as e:
                                logger.warning(
                                    f"deferred 图片持久化失败，该图将跳过增强: {e}")
                                _d.metadata["image_path"] = ""
                    docs = [_d for _d in docs
                            if (getattr(_d, "metadata", {}) or {}).get("vision_status") != "deferred"]
                timings = {
                    "parse_ms": int(meta.get("parse_ms") or 0),
                    "vision_ms": int(vision_stats.get("vision_ms") or 0),
                    "vision_calls": int(vision_stats.get("vision_calls") or 0),
                    "vision_cached": int(vision_stats.get("vision_cached") or 0),
                    "vision_failed": int(vision_stats.get("vision_failed") or 0),
                    "vision_timeout": int(vision_stats.get("vision_timeout") or 0),
                    "vision_skipped": int(vision_stats.get("vision_skipped") or 0),
                    "images_total": int(vision_stats.get("images_total") or 0),
                    "images_deduped": int(vision_stats.get("images_deduped") or 0),
                    # 本地 OCR（2026-10-28）：扫描件页数与引擎，便于解释耗时
                    "ocr_ms": int(ocr_stats.get("elapsed_ms") or 0),
                    "ocr_pages": int(ocr_stats.get("pages") or 0),
                    "ocr_pages_blank": int(ocr_stats.get("pages_blank") or 0),
                    "ocr_pages_failed": int(ocr_stats.get("pages_failed") or 0),
                    "ocr_pages_figure": int(ocr_stats.get("pages_figure_only") or 0),
                    "ocr_engine": str(ocr_stats.get("engine") or ""),
                    "pages_total": int(meta.get("pages_total") or 0),
                    "pages_parsed": int(meta.get("pages_parsed") or 0),
                    "parse_engine": str(meta.get("parse_engine") or ""),
                    "item_timed_out": bool(meta.get("item_timed_out")),
                    "retries": int(meta.get("retries") or 0),
                    # 内存峰值（任务 6）：解析阶段 Python 分配峰值 + 进程 RSS
                    "memory_peak_mb": float(meta.get("memory_peak_mb") or 0.0),
                    "rss_mb": float(meta.get("rss_mb") or 0.0),
                }
                timings_json = json.dumps(timings, ensure_ascii=False)

                if not docs:
                    retryable = 1 if error_type in (
                        "converter_unavailable", "io_error", "item_timeout",
                        "parse_error") else 0
                    _update_item(item_id, status="failed", stage="parsing", progress=None,
                                 message=message or "解析失败",
                                 error_type=error_type or "parse_error",
                                 retryable=retryable, timings_json=timings_json)
                    if error_type == "item_timeout":
                        warnings.append(f"{cand['display_name']}：{message}")
                    continue

                # 扫描件（本地 OCR）：把"图表未理解 / 空白页 / 识别失败"写进**用户可见**结果，
                # 不能只留在日志里（2026-10-28）。
                if str(meta.get("parse_engine") or "").startswith("local_ocr"):
                    note = _ocr_user_note(cand["display_name"],
                                          meta.get("ocr_stats") or {})
                    if note:
                        warnings.append(note)

                content = src_path.read_bytes()
                decision = _decide_target(subject_dir, cand["display_name"], content)
                final_path = subject_dir / decision["name"]

                if decision["skip"]:
                    # 同名同内容：不重复落盘、不重复索引，但内容确实可学习 → ready
                    _rewrite_metadata(docs, decision["name"], final_path)
                    _rewrite_metadata(_still_deferred, decision["name"], final_path)
                    parsed_docs.extend(docs)
                    ready_names.add(decision["name"])
                    if src_sha and not had_deferred_this_item:
                        # 正文先行：deferred 批不进解析缓存（图片内容由后台补齐后
                        # 会写入视觉缓存；下次导入图片经缓存快扫可完整入库）
                        _save_docs_cache(src_sha, parse_cfg, docs)
                    _fence_guard()
                    doc_id, _mid = _register_material(subject_id, decision["name"],
                                                      len(content), user_id)
                    _update_item(item_id, status="ready", stage="indexing", progress=1.0,
                                 message=decision["reason"], doc_id=doc_id,
                                 stored_path=str(final_path), content_sha=src_sha,
                                 reuse_key=_reuse_key(src_sha, parse_cfg, embedding_sig),
                                 parse_config=parse_cfg, timings_json=timings_json)
                    continue

                _fence_guard()
                from services.subject_mutation import subject_mutation
                with subject_mutation(subject_id):
                    existed_before = final_path.exists()
                    # 修改（同名不同内容）：把旧版本移入 _superseded/ 留档，再写入新版本。
                    # 这样旧分块随之从下一次建库中消失（文件已不在根目录），新分块生效。
                    if existed_before and decision.get("replace"):
                        try:
                            sup_dir = subject_dir / "_superseded"
                            sup_dir.mkdir(parents=True, exist_ok=True)
                            old_sha12 = str(decision.get("old_sha") or "old")[:12]
                            sup_path = sup_dir / f"{final_path.name}.{old_sha12}"
                            if sup_path.exists():
                                final_path.unlink()
                            else:
                                shutil.move(str(final_path), str(sup_path))
                            replaced_all.append((str(sup_path), str(final_path)))
                            # 内容被同名替换：旧页面图与页面文本必须立刻失效，否则
                            # "看原页"和页面检索还会指向上一版内容（新页由导入后的建页任务重建）。
                            _invalidate_replaced_pages(subject_id, decision["name"])
                        except Exception as e:
                            logger.warning(f"旧版本留档失败（继续替换，不影响新版本生效）: {e}")
                    final_path.write_bytes(content)
                if not existed_before:
                    copied_new_name = decision["name"]
                    copied_all.append(copied_new_name)
                # 显式释放本项大对象，避免循环内累积（任务 6）
                del content

                _rewrite_metadata(docs, decision["name"], final_path)
                _rewrite_metadata(_still_deferred, decision["name"], final_path)
                parsed_docs.extend(docs)
                ready_names.add(decision["name"])
                if src_sha:
                    _save_docs_cache(src_sha, parse_cfg, docs)

                _fence_guard()
                doc_id, material_id = _register_material(
                    subject_id, decision["name"], final_path.stat().st_size, user_id)
                msg = "可学习" if not decision["renamed"] else decision["reason"]
                _update_item(item_id, status="indexing", stage="indexing", progress=None,
                             message=msg, doc_id=doc_id, stored_path=str(final_path),
                             content_sha=src_sha,
                             reuse_key=_reuse_key(src_sha, parse_cfg, embedding_sig),
                             parse_config=parse_cfg, timings_json=timings_json)

            if not _refresh_executor(job_id, executor_id, epoch):
                logger.warning(
                    f"[import][fencing] 任务 {job_id} 执行权已被接管，"
                    "原执行者停止写入并退出（不再产生任何资料/索引）")
                return

            # 本批统一建库（本批 + 既有资料，只建一次）
            new_indexing = _items_with_status(job_id, "indexing")
            if parsed_docs or new_indexing:
                if reuse_job_id:
                    _include_reused_ready_docs(reuse_job_id, parse_cfg,
                                               ready_names, parsed_docs)
                t_build = time.monotonic()
                _mem_reset_peak()
                build_base = _mem_current_bytes()
                existing_docs = _collect_existing_documents(subject_id, ready_names)
                all_docs = parsed_docs + existing_docs
                del existing_docs
                if all_docs:
                    try:
                        from services import knowledge_service
                        # 同一学科串行建库：避免并发“备份→构建→回滚”互相覆盖索引目录
                        with _subject_build_lock(subject_id):
                            # 拿到学科锁后再核对一次：绝不能出现两个执行者都在建库
                            _fence_guard()
                            build_result = knowledge_service.rebuild_subject_knowledge_from_documents(
                                subject_id, all_docs)
                    except Exception as e:
                        logger.error(f"导入建库失败: {job_id}: {e}", exc_info=True)
                        for iid in new_indexing:
                            _update_item(iid, status="failed", stage="indexing",
                                         progress=None, message=f"建库失败：{e}",
                                         error_type="build_failed", retryable=1)
                        stage_timings["build_ms"] += int((time.monotonic() - t_build) * 1000)
                        stage_timings["build_memory_peak_mb"] = max(
                            float(stage_timings.get("build_memory_peak_mb") or 0.0),
                            _mem_peak_mb(build_base))
                        # 不可恢复错误：立即停止自动分批（不无限重试建库）
                        warnings.append("建库失败（不可恢复错误），已停止自动分批")
                        break
                    stage_timings["build_ms"] += int((time.monotonic() - t_build) * 1000)
                    stage_timings["build_memory_peak_mb"] = max(
                        float(stage_timings.get("build_memory_peak_mb") or 0.0),
                        _mem_peak_mb(build_base))
                    chunk_count = None
                    try:
                        chunk_count = (build_result or {}).get("chunk_count")
                    except Exception:
                        chunk_count = None
                    if isinstance(chunk_count, int):
                        stage_timings["build_chunks"] = chunk_count
                    # 2026-10-08：把"向量化增量"的实测计数写进任务，便于验收与排障
                    try:
                        _emb = (build_result or {}).get("embedding_stats") or {}
                        if _emb:
                            stage_timings["embed_total"] = int(_emb.get("total") or 0)
                            stage_timings["embed_cache_hits"] = int(_emb.get("hits") or 0)
                            stage_timings["embed_misses"] = int(_emb.get("misses") or 0)
                            stage_timings["embed_calls"] = int(_emb.get("embed_calls") or 0)
                            stage_timings["embed_embedded_texts"] = int(
                                _emb.get("embedded_texts") or 0)
                            stage_timings["embed_ms"] = int(_emb.get("embed_ms") or 0)
                            stage_timings["embed_lookup_ms"] = int(_emb.get("lookup_ms") or 0)
                    except Exception:
                        pass
                    # 修改过的资料：旧版本已移入 _superseded/，替换属于成功结果，
                    # 不参与"取消回滚"（回滚只针对本批新增但尚未建库的文件）。
                    replaced_all.clear()
                    for iid in new_indexing:
                        _update_item(iid, status="ready", stage="indexing", progress=1.0,
                                     message="可学习", error_type=None, retryable=0)
                    # 只有本批恰好一份资料时，才能把总 chunk 数如实归到该条目
                    if len(new_indexing) == 1 and isinstance(chunk_count, int):
                        _merge_item_timings(new_indexing[0], {"chunks": chunk_count})
                    # 2026-10-31 正文先行：本批含 deferred 图片时，建库完成后
                    # 立即调度后台增强与增量补入（每批一次，不重复调度）。
                    if deferred_vision_docs:
                        _schedule_deferred_vision(
                            job_id, subject_id, list(deferred_vision_docs),
                            warnings, dirs=list(deferred_vision_dirs))
                        deferred_vision_docs.clear()
                        deferred_vision_dirs.clear()
                else:
                    stage_timings["build_ms"] += int((time.monotonic() - t_build) * 1000)
                del all_docs, parsed_docs
            stage_timings["parse_memory_peak_mb"] = max(
                float(stage_timings.get("parse_memory_peak_mb") or 0.0),
                _mem_peak_mb(batch_mem_base))
            _save_batch_index(job_id, batch_no)

            if _is_cancelled(job_id):
                _remove_copied(subject_dir, copied_all)
                _restore_replaced(replaced_all)
                _cancel_finish(job_id, "任务已取消（已导入的资料不会删除）")
                return
            if _is_pause_requested(job_id):
                stage_timings["parse_ms"] = int((time.monotonic() - t_parse) * 1000)
                stage_timings["total_ms"] = int((time.monotonic() - t_wall) * 1000)
                _pause_finish(job_id, warnings, archive_summaries, stage_timings)
                return

            # 无进展检测（防失控）：本批没有产生任何新 ready/failed/skipped 就停止
            if _terminal_item_count(job_id) <= term_before:
                warnings.append(
                    "本批没有产生任何新 ready/failed/skipped（无进展），已停止自动分批")
                break
            if not _remaining_candidates(job_id, candidates):
                break
            sleep_s = _batch_sleep_seconds()
            if sleep_s > 0:
                time.sleep(sleep_s)

        stage_timings["parse_ms"] = int((time.monotonic() - t_parse) * 1000)
        stage_timings["rss_peak_mb"] = _rss_mb()
        stage_timings["total_ms"] = int((time.monotonic() - t_wall) * 1000)
        _finish_job(job_id, _derive_status(job_id), _summary(job_id), warnings,
                    archive_summaries, stage_timings)

    finally:
        # 清理本次解包/临时内容（重试/继续所需的上传原文另外保留）
        shutil.rmtree(tmp_job_dir / "extracted", ignore_errors=True)

# ==================== 导入后续接页面索引（2026-10-30） ====================
_PAGE_INDEX_SUFFIXES = (".pdf", ".pptx", ".ppt")
_PAGE_INDEX_SCHEDULED: set = set()
_PAGE_INDEX_SCHEDULED_LOCK = threading.Lock()


def _invalidate_replaced_pages(subject_id: str, file_name: str) -> None:
    """资料被同名替换后，让它的页面索引与页面图立即失效（失败只记日志，不影响替换）。

    同时会取消该文档正在跑的建页任务，避免旧任务把上一版的页面又写回来。
    """
    try:
        from services import page_index
        page_index.delete_document_pages(subject_id, file_name=file_name)
    except Exception as e:
        logger.debug(f"替换资料时清理页面索引失败（不影响替换）: {e}")


def _schedule_deferred_vision(job_id: str, subject_id: str,
                              docs: List[Any], warnings: List[str],
                              dirs: Optional[set] = None) -> None:
    from services import import_vision
    warnings.append(f"{len(docs)} 张图片待增强；正文已可学习，增强状态单独记录")
    import_vision.enqueue(job_id, subject_id, docs)


def _schedule_page_index_after_import(job_id: str, subject_id: str) -> None:
    """建库成功后接续建页面索引，不再让用户另外找"建立页面索引"。

    设计取舍：
    - 资料**此时已经可以检索学习**，页面图只是"看原页/定位高亮"那一层，
      所以异步、逐份串行执行，失败只记日志，绝不影响导入结果；
    - 复用导入期的页级 OCR 缓存（`PageTextCache`），扫描件不会跑第二遍 OCR；
    - 串行而非并发：导入刚结束，机器可能还在跑别的任务，建页不该抢 CPU。
    页级断点在 page_index 内部保证，中断后重跑只补缺失页。
    """
    if os.getenv("IMPORT_PAGE_INDEX", "1").strip().lower() in ("0", "false", "off"):
        return
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT doc_id, stored_path FROM import_items "
                "WHERE job_id=? AND status='ready'", (job_id,)).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"读取导入条目失败（跳过页面索引）: {e}")
        return

    entries = []
    for row in rows:
        raw_path = str(row["stored_path"] or "")
        if not raw_path:
            continue
        path = Path(raw_path)
        if path.suffix.lower() not in _PAGE_INDEX_SUFFIXES or not path.is_file():
            continue
        doc_id = str(row["doc_id"] or "")
        material_id = doc_id[2:] if doc_id.startswith("m:") else None
        entries.append((path, material_id))
    if not entries:
        return

    key = f"{job_id}:{subject_id}"
    with _PAGE_INDEX_SCHEDULED_LOCK:
        if key in _PAGE_INDEX_SCHEDULED:
            return
        _PAGE_INDEX_SCHEDULED.add(key)

    def _worker() -> None:
        from services import page_index
        for path, material_id in entries:
            try:
                # 替换失效时会记该文档的取消账（防旧任务把已删内容写回）；
                # 本次是**新版**调度，必须先清账，否则注册时被旧账误杀——
                # 实测同名替换后 pages=0 而 doc_state 仍显示已覆盖（2026-10-31）。
                page_index.clear_cancel_request(
                    subject_id, page_index._doc_id_for_file(path.name))
                result = page_index.build_document_page_index(
                    subject_id, path, material_id=material_id)
                logger.info(
                    f"[page-index] {path.name}: 新建 {result.get('indexed')} 页，"
                    f"复用OCR缓存 {result.get('reused_cache')} 页，"
                    f"实际OCR {result.get('ocr_pages')} 页，"
                    f"共 {result.get('total_pages')} 页"
                    + (f"，未覆盖自第 {result.get('uncovered_from')} 页起"
                       if result.get("truncated") else ""))
            except Exception as e:
                logger.warning(f"[page-index] {path.name} 建页失败（不影响学习）: {e}")

    threading.Thread(target=_worker, name=f"page-index-{subject_id}",
                     daemon=True).start()
    logger.info(
        f"[page-index] 导入后接续建页：{len(entries)} 份资料（后台执行，不阻塞学习）")


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


def _restore_replaced(replaced: List[tuple]) -> None:
    """取消/失败回滚：把"修改"替换掉的旧版本放回原位，避免用户丢失原文件。"""
    for sup_path, original in list(replaced or []):
        try:
            sup = Path(sup_path)
            orig = Path(original)
            if sup.exists():
                if orig.exists():
                    orig.unlink()
                shutil.move(str(sup), str(orig))
        except Exception as e:
            logger.warning(f"回滚被替换的旧版本失败: {sup_path}: {e}")


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
    _fence_guard()   # 失去执行权：一律不写
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
    _fence_guard()   # 失去执行权：绝不写终态，交给接管者收尾
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
    # 2026-10-30：资料已可学习的收尾（含 partial——部分成功也要给已建好的部分建页）。
    # 失败任务不建页；建页本身异步、失败不影响导入结果。
    if status in ("succeeded", "partial") and job and counts["ready"] > 0:
        try:
            _schedule_page_index_after_import(job_id, str(job["subject_id"]))
        except Exception as e:
            logger.debug(f"接续页面索引失败（不影响导入结果）: {e}")


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
    public = _job_to_public(dict(job), [dict(i) for i in items])
    from services import import_vision
    public["image_enhancement"] = import_vision.status(job_id)
    public["text_status"] = public.get("status")
    return public


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
        # 分类计数（任务 4）：把“发现的条目”与“有效资料”分开，
        # 绝不把发现条目数直接当成可学习资料数。字段只增不改。
        discovery = _load(job.get("discovery_json"), {}) or {}
        if isinstance(discovery, dict):
            for key in ("members_discovered", "containers", "directories", "materials"):
                if key in discovery:
                    try:
                        totals[key] = int(discovery.get(key) or 0)
                    except (TypeError, ValueError):
                        totals[key] = 0

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
    # 用户意图（任务 3）：paused=1 表示用户主动暂停；pause_requested=1 表示“到批边界停”。
    # 二者都不会自动重启；只有 resume 才清除，且 resume 后 status 回到 running。
    paused = bool(job.get("paused"))
    pause_requested = bool(job.get("pause_requested"))
    can_pause = public_status in ("queued", "running") and not paused
    can_resume = (paused and public_status != "cancelled"
                  and bool(job.get("raw_dir")))
    can_cancel = public_status != "cancelled"
    try:
        batch_index = int(job.get("batch_index") or 0)
    except (TypeError, ValueError):
        batch_index = 0
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
        # 阶段计时：extract_ms / parse_ms / build_ms / vision_ms / total_ms（+ 分类计数与内存峰值）
        "stage_timings": _load(job.get("stage_timings_json"), {}) or {},
        "can_retry": can_retry,
        "can_continue": can_continue,
        # 任务 3：暂停/继续/取消入口的状态（只增不改）
        "paused": paused,
        "pause_requested": pause_requested,
        "can_pause": can_pause,
        "can_resume": can_resume,
        "can_cancel": can_cancel,
        "batch_index": batch_index,
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
    """
    取消任务（任务 3）：设置 cancel_requested；已暂停/已终态的任务直接置 cancelled。

    - 运行中的任务在**下一个检查点/批边界**停止，且**绝不自动重启**；
    - 已导入的资料不删除（调用方决定是否清理临时目录）。
    """
    _init_db()
    with _DB_LOCK, _connect() as conn:
        job = _job_row(conn, job_id)
        if job is None:
            return False
        conn.execute(
            "UPDATE import_jobs SET cancel_requested=1, pause_requested=0, paused=0"
            " WHERE job_id=?", (job_id,))
        if job["status"] in ("queued", "partial", "failed", "succeeded"):
            conn.execute(
                "UPDATE import_jobs SET status='cancelled', finished_at=?, updated_at=?,"
                " summary_text=? WHERE job_id=?",
                (_now(), _now(), "任务已取消（已导入的资料不会删除）", job_id))
        conn.commit()
    return True


def pause_job(job_id: str) -> bool:
    """
    请求暂停（任务 3）：在**下一个批边界**停止，置 partial + paused=1。

    - 幂等；已取消的任务不能暂停；succeeded 的任务无需暂停；
    - 暂停后**绝不自动重启**，且新来的批次不会自动恢复它；
    - resume 是唯一恢复入口。
    """
    _init_db()
    with _DB_LOCK, _connect() as conn:
        job = _job_row(conn, job_id)
        if job is None:
            return False
        if job["status"] == "cancelled":
            raise ValueError("任务已取消，不能暂停")
        if job["status"] == "succeeded":
            from services.import_vision import status as vision_status
            v = vision_status(job_id)
            if not any(v['counts'].get(k, 0) for k in ('pending', 'running', 'retry', 'paused')):
                raise ValueError("任务已完成，无需暂停")
        if bool(job["paused"]):
            return True
        conn.execute(
            "UPDATE import_jobs SET pause_requested=1, cancel_requested=0, updated_at=?"
            " WHERE job_id=?", (_now(), job_id))
        if job["status"] != "running":
            # 没有工作线程在跑（queued/partial/failed）：立即置 paused，避免被自动接续；
            # 运行中的任务由工作线程在批边界调用 _pause_finish 落 paused。
            conn.execute(
                "UPDATE import_jobs SET status='partial', paused=1, pause_requested=0,"
                " finished_at=?, updated_at=?, summary_text=? WHERE job_id=?",
                (_now(), _now(), "已暂停（可继续）", job_id))
        conn.commit()
    return True


def resume_job(job_id: str) -> Optional[str]:
    """
    从暂停处继续自动分批（任务 3）。清除用户暂停意图，重新 claim 并执行**同一任务**。

    - 只有 paused=1 的任务可 resume；不要求用户重传；
    - 已 ready 的成员不重复解析（计划/条目已在库），只处理剩余成员。
    """
    _init_db()
    with _DB_LOCK, _connect() as conn:
        job = _job_row(conn, job_id)
        if job is None:
            return None
        if job["status"] == "cancelled":
            raise ValueError("任务已取消，不能继续")
        if not bool(job["paused"]):
            raise ValueError("任务未处于暂停状态，无需继续")
        from services.import_vision import status as vision_status
        image_only = any(vision_status(job_id)['counts'].get(k, 0) for k in ('paused','pending','running'))
        if not job["raw_dir"] and not image_only:
            raise ValueError("原包已清理，无法继续")
        conn.execute(
            "UPDATE import_jobs SET paused=0, pause_requested=0, cancel_requested=0,"
            " status='queued', finished_at=NULL, executor_id=NULL,"
            " executor_heartbeat=NULL, updated_at=? WHERE job_id=?", (_now(), job_id))
        conn.commit()
    if image_only:
        from services.import_vision import recover
        recover()
        return job_id
    # 恢复时把“重启中断”的条目清掉，让它们重新被自动分批处理
    _clear_interrupted_items(job_id)
    threading.Thread(target=_run_job_thread, args=(job_id,),
                     name=f"import-{job_id}", daemon=True).start()
    return job_id


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
    "auto_resume_interrupted_jobs",
    "cancel_job",
    "cleanup_job_files",
    "continue_job",
    "create_import_job",
    "get_job",
    "list_jobs",
    "pause_job",
    "reconcile_interrupted_jobs",
    "resume_job",
    "retry_job",
]


# ==================== 服务重启后的中断恢复 ====================


_LAST_INTERRUPTED_JOB_IDS: List[str] = []


def reconcile_interrupted_jobs() -> int:
    """启动时把**被中断**的导入任务收敛为 partial（可继续），返回收敛数量。

    为什么必须做（2026-10-07 实测）：整机内存压力下后端进程被杀，当时正在运行的
    导入任务行永远停在 status='running'。用户刷新页面只看到"处理中"，
    既没有进度也没有「继续处理」入口，而且这条任务会挡住后续 continue 链。
    任务靠**线程**执行，进程一重启就不可能还有"仍在运行"的任务，
    因此启动时把所有 queued/running 一律收敛为 partial + 可继续。

    2026-10-08：同时清空 executor_id/executor_heartbeat（旧进程的执行者声明已失效，
    必须能被本进程接管）；并记录本次收敛的任务 id，供 auto_resume_interrupted_jobs
    在非用户暂停/取消时自动继续自动分批。
    """
    _init_db()
    fixed = 0
    converged: List[str] = []
    with _DB_LOCK, _connect() as conn:
        # 1) 先收敛"进行中"的条目。任务靠线程执行，进程刚启动时不可能还有线程在跑，
        #    因此任何 pending/parsing/indexing 的条目都是上次进程留下的残骸。
        #    不收敛的话页面会永远显示"处理中 N"，与"任务并未在跑"自相矛盾。
        stale = conn.execute(
            "UPDATE import_items SET status='failed', stage='parsing', progress=NULL,"
            " message=?, error_type='interrupted', retryable=1"
            " WHERE status IN ('pending','parsing','indexing')",
            ("服务重启导致任务中断，可继续处理",)).rowcount
        # 2) 再把停在 queued/running 的任务收敛为 partial（可继续），并释放旧执行者声明
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
                " summary_text=?, warnings_json=?, executor_id=NULL,"
                " executor_heartbeat=NULL WHERE job_id=?",
                (_now(), _now(), msg,
                 json.dumps([f"服务重启导致任务中断，剩余 {unprocessed} 份未处理（可继续处理）"],
                            ensure_ascii=False),
                 job_id))
            converged.append(job_id)
            fixed += 1
        conn.commit()
    _LAST_INTERRUPTED_JOB_IDS.clear()
    _LAST_INTERRUPTED_JOB_IDS.extend(converged)
    if fixed or stale:
        logger.warning(
            f"[import] 启动恢复：收敛 {fixed} 个被中断任务为 partial（可继续），"
            f"清理 {stale} 个残留的进行中条目")
    return fixed


def auto_resume_interrupted_jobs() -> int:
    """
    服务重启后**自动继续自动分批**（任务 1/2/3）：只恢复本次启动收敛的任务，
    且跳过用户主动暂停/取消的（paused=1 / cancel_requested=1 / status=cancelled）。
    用户暂停/取消的任务绝不在这里自动重启。返回自动接续的任务数。
    """
    flag = (os.getenv("IMPORT_AUTO_RESUME", "1") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return 0
    started = 0
    for job_id in list(_LAST_INTERRUPTED_JOB_IDS):
        job = get_job(job_id)
        if not job:
            continue
        if job.get("paused") or job.get("status") == "cancelled":
            continue
        if not (job.get("can_continue") or job.get("can_resume")):
            continue
        # 让被中断的成员重新成为未处理，从而真正继续自动分批
        _clear_interrupted_items(job_id)
        # 同步置为 queued，避免调用方在“线程尚未接管”的窗口里把 partial 当成最终态
        with _DB_LOCK, _connect() as conn:
            conn.execute(
                "UPDATE import_jobs SET status='queued', finished_at=NULL, updated_at=?"
                " WHERE job_id=?", (_now(), job_id))
            conn.commit()
        threading.Thread(target=_run_job_thread, args=(job_id,),
                         name=f"import-{job_id}", daemon=True).start()
        started += 1
    if started:
        logger.warning(
            f"[import] 启动恢复：自动继续 {started} 个被中断任务（无需人工点击）")
    return started


from services.import_vision import coordinated_control as _vision_control
cancel_job = _vision_control(cancel_job)
pause_job = _vision_control(pause_job)
resume_job = _vision_control(resume_job)

