"""扫描件本地 OCR（2026-10-28）。

设计目标（借鉴 DeepTutor 的“可插拔解析 + 扫描件本地 OCR 兜底”）：
- 按页渲染、按页返回，天然保留**真实页码**；
- 页级缓存（文件 SHA + 页码 + 引擎 + DPI），中断后重跑只补缺失页；
- 引擎按需降级：rapidocr -> liteparse -> pytesseract(需系统二进制) -> 明确失败；
- 绝不在这里调用付费视觉模型。

本模块不依赖项目内的 import_service，避免循环导入；进度由调用方通过回调上报。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from config.path_config import get_data_dir
from utils.logger import logger

OCR_CACHE_VERSION = "v1"
# 200 DPI 渲染后统一缩到最长边 1600：实测教材单页 OCR 8.4s -> 5.0s，
# 识别字数几乎不变（1508 -> 1507）；用 env 可继续调。
_MAX_RENDER_SIDE = 1600
_DEFAULT_DPI = 200
_RAPID_LOCAL = threading.local()
_STATS_LOCK = threading.Lock()


def _max_render_side() -> int:
    return max(600, _env_int("IMPORT_OCR_MAX_SIDE", _MAX_RENDER_SIDE))


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _preferred_engine() -> str:
    return (os.getenv("IMPORT_OCR_ENGINE", "auto") or "auto").strip().lower()


def _rapidocr_available() -> bool:
    try:
        import rapidocr  # noqa: F401
        return True
    except Exception:
        return False


def _liteparse_available() -> bool:
    try:
        import liteparse  # noqa: F401
        return True
    except Exception:
        return False


def _pytesseract_available() -> bool:
    try:
        import pytesseract
        if shutil.which("tesseract"):
            return True
        # Windows 上 tesseract 常不在 PATH，交给项目既有的 _configure_tesseract
        from rag.multimodal.image.extractor import _configure_tesseract
        _configure_tesseract()
        return bool(getattr(pytesseract.pytesseract, "tesseract_cmd", "")) and Path(
            pytesseract.pytesseract.tesseract_cmd).exists()
    except Exception:
        return False


def select_engine(prefer: Optional[str] = None) -> Tuple[Optional[str], str]:
    """返回 (引擎名 | None, 原因)。None 表示本机没有可用的本地 OCR。"""
    prefer = (prefer or _preferred_engine()).strip().lower()
    if prefer in ("off", "none", "0", "false"):
        return None, "IMPORT_OCR_ENGINE=off"
    if prefer and prefer != "auto":
        candidates = [prefer]
    else:
        candidates = ["rapidocr", "liteparse", "pytesseract"]
    checks = {
        "rapidocr": _rapidocr_available,
        "liteparse": _liteparse_available,
        "pytesseract": _pytesseract_available,
    }
    for name in candidates:
        check = checks.get(name)
        if check and check():
            return name, ""
    return None, "未检测到本地 OCR 引擎（rapidocr/liteparse/tesseract 均不可用）"


def engine_status() -> Dict[str, Any]:
    name, reason = select_engine()
    return {"name": name, "available": bool(name), "note": reason,
            "dpi": _env_int("IMPORT_OCR_DPI", _DEFAULT_DPI)}


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:24]


def _cache_dir(pdf_path: Path) -> Path:
    d = Path(get_data_dir()) / "cache" / "ocr_pages" / _file_sha(pdf_path)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(pdf_path: Path, page_no: int, engine: str, dpi: int) -> Path:
    return _cache_dir(pdf_path) / f"p{page_no}_{engine}_{dpi}_{OCR_CACHE_VERSION}.txt"


def _read_cache(path: Path) -> Optional[str]:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        pass
    return None


def _write_cache(path: Path, text: str) -> None:
    try:
        path.write_text(text or "", encoding="utf-8")
    except OSError as e:
        logger.debug(f"OCR 页缓存写入失败（不影响本次）: {e}")


def page_count(pdf_path: Path) -> int:
    try:
        import fitz

        doc = fitz.open(str(pdf_path))
        try:
            return int(doc.page_count)
        finally:
            doc.close()
    except Exception as e:
        logger.warning(f"读取 PDF 页数失败: {e}")
        return 0


def detect_scanned(pdf_path: Path, sample: int = 5) -> bool:
    """抽样判断是否扫描件：抽样页文本层几乎为空。

    只取首/中/尾与少量前页，避免整本遍历；打不开的文件按“非扫描件”处理，
    让原有链路去处理（失败原因会更具体）。
    """
    try:
        import fitz

        doc = fitz.open(str(pdf_path))
        try:
            total = int(doc.page_count)
            if total <= 0:
                return False
            idxs = {0, total // 2, total - 1}
            for i in range(min(max(1, sample), total)):
                idxs.add(i)
            lengths = []
            for i in sorted(i for i in idxs if 0 <= i < total):
                text = (doc.load_page(i).get_text("text") or "").strip()
                lengths.append(len(text))
            return bool(lengths) and all(n < 20 for n in lengths)
        finally:
            doc.close()
    except Exception as e:
        logger.warning(f"扫描件检测失败（按非扫描件处理）: {e}")
        return False


def _render_page(doc: Any, page_index: int, dpi: int) -> Any:
    import fitz
    from PIL import Image

    page = doc.load_page(page_index)
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    max_side = _max_render_side()
    if max(image.size) > max_side:
        ratio = max_side / max(image.size)
        image = image.resize((max(1, int(image.width * ratio)),
                              max(1, int(image.height * ratio))), Image.LANCZOS)
    return image


def _rapidocr_engine() -> Any:
    """每线程一个 RapidOCR 实例：onnxruntime 会话可并行，但实例不共享。"""
    engine = getattr(_RAPID_LOCAL, "engine", None)
    if engine is None:
        from rapidocr import RapidOCR
        engine = RapidOCR()
        _RAPID_LOCAL.engine = engine
    return engine


def _ocr_workers() -> int:
    """OCR 并发：默认 2 路，上限 3 路；1 = 串行。"""
    return max(1, min(_env_int("IMPORT_OCR_WORKERS", 2), 3))


def _ocr_page(pdf_path: Path, page_no: int, engine: str, dpi: int,
              should_abort: Optional[Callable[[], bool]] = None,
              stats: Optional[Dict[str, Any]] = None) -> str:
    """单页 OCR（线程池任务）：任务内独立打开 PDF，避免 fitz 多线程不安全。"""
    cache = _cache_path(pdf_path, page_no, engine, dpi)
    cached = _read_cache(cache)
    if cached is not None:
        _note(stats, "cached", page_no)
        return cached
    if should_abort and should_abort():
        return ""
    import fitz

    doc = fitz.open(str(pdf_path))
    try:
        image = _render_page(doc, page_no - 1, dpi)
    finally:
        doc.close()
    text = _ocr_image(image, engine)
    _write_cache(cache, text)
    _note(stats, "text" if str(text or "").strip() else "blank", page_no)
    return text


def _note(stats: Optional[Dict[str, Any]], key: str, page_no: int) -> None:
    """记录页级结果分类：cached / text / blank / failed，用于区分“真空白”与“识别失败”。"""
    if not isinstance(stats, dict):
        return
    try:
        with _STATS_LOCK:
            bucket = stats.setdefault(key, [])
            if isinstance(bucket, list) and page_no not in bucket:
                bucket.append(int(page_no))
    except Exception:
        pass


def _reorder_reading_order(items: List[Tuple[str, Any]], width: int) -> List[str]:
    """把 OCR 片段整理成阅读顺序（2026-10-28）。

    RapidOCR 按行识别，双栏排版会输出“左1 → 右1 → 左2 → 右2 …”，
    直接拼接会破坏语义连贯（实测双栏页正是这种交错）。
    做法：用 x 方向占用直方图找**竖向空白栏间距**，据此把页面分成多栏，
    栏内按 y 排序、栏间从左到右。判断不出多栏时**原样返回**，绝不乱排。
    """
    rows: List[Tuple[float, float, float, float, str]] = []
    for text, box in items:
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
        except Exception:
            rows.append((0.0, 0.0, 0.0, 0.0, str(text)))
            continue
        rows.append((min(xs), max(xs), min(ys), max(ys), str(text)))

    if len(rows) < 4 or width <= 0:
        return [item[4] for item in rows]

    # 通栏标题/页眉会横跨栏间距，先把它们排除再做占用统计
    page_span = max(r[1] for r in rows) - min(r[0] for r in rows)
    if page_span <= 0:
        return [item[4] for item in rows]
    column_items = [r for r in rows if (r[1] - r[0]) <= page_span * 0.6]
    if len(column_items) < 4:
        return [item[4] for item in rows]

    bins = 200
    occupancy = [0] * bins
    for x0, x1, _, _, _ in column_items:
        start = max(0, int(x0 / width * bins))
        end = min(bins - 1, int(x1 / width * bins))
        for idx in range(start, end + 1):
            occupancy[idx] += 1

    covered = [idx for idx, hits in enumerate(occupancy) if hits]
    if not covered:
        return [item[4] for item in rows]

    # 找内部空白段（栏间距）：要求宽度 >= 页面宽度的 2%
    gaps: List[Tuple[int, int]] = []
    idx = covered[0]
    while idx <= covered[-1]:
        if occupancy[idx] == 0:
            start_idx = idx
            while idx <= covered[-1] and occupancy[idx] == 0:
                idx += 1
            if (idx - start_idx) >= max(2, int(bins * 0.02)):
                gaps.append((start_idx, idx - 1))
            continue
        idx += 1

    if not gaps:
        return [item[4] for item in rows]

    # 用每一段空白的中线作为分栏边界
    boundaries = [(width * (a + b) / 2.0 / bins) for a, b in gaps]
    clusters: List[List[Tuple[float, float, float, float, str]]] = [
        [] for _ in range(len(boundaries) + 1)]
    for row in column_items:
        center_x = (row[0] + row[1]) / 2.0
        slot = 0
        while slot < len(boundaries) and center_x > boundaries[slot]:
            slot += 1
        clusters[slot].append(row)

    wide = [r for r in rows if (r[1] - r[0]) > page_span * 0.6]
    solid = [c for c in clusters if len(c) >= 2]
    if len(solid) < 2:
        return [item[4] for item in rows]

    ordered: List[Tuple[float, float, float, float, str]] = []
    wide.sort(key=lambda r: (r[2], r[0]))
    pending_wide = list(wide)
    for cluster in clusters:
        if not cluster:
            continue
        cluster.sort(key=lambda r: (r[2], r[0]))
        cluster_top = min(r[2] for r in cluster)
        while pending_wide and min(r[2] for r in pending_wide) < cluster_top:
            ordered.append(pending_wide.pop(0))
        ordered.extend(cluster)
    ordered.extend(pending_wide)
    return [item[4] for item in ordered]


def _ocr_image(image: Any, engine: str) -> str:
    if engine == "rapidocr":
        try:
            import numpy as np

            out = _rapidocr_engine()(np.array(image))
        except Exception:
            out = _rapidocr_engine()(image)
        texts = getattr(out, "txts", None)
        if texts is None:
            # 兼容旧版返回值 (result, elapse)
            if isinstance(out, tuple) and out and isinstance(out[0], list):
                return "\n".join(str(item[1]) for item in out[0] if len(item) > 1)
            return ""
        raw_boxes = getattr(out, "boxes", None)
        boxes = list(raw_boxes) if raw_boxes is not None else []
        clean = [(str(t), b) for t, b in zip(texts, boxes) if str(t).strip()]
        if not clean:
            return ""
        try:
            width = int(image.width)
        except Exception:
            width = 0
        ordered = _reorder_reading_order(clean, width)
        return "\n".join(ordered) if ordered else "\n".join(
            str(t) for t in texts if str(t).strip())
    if engine == "pytesseract":
        try:
            import pytesseract
            from rag.multimodal.image.extractor import _configure_tesseract

            _configure_tesseract()
            try:
                return pytesseract.image_to_string(image, lang="chi_sim+eng").strip()
            except Exception:
                return pytesseract.image_to_string(image, lang="eng").strip()
        except Exception as e:
            logger.warning(f"pytesseract OCR 失败: {e}")
            return ""
    if engine == "liteparse":
        # LiteParse 面向文件而非内存图；落临时文件再解析，作为最后兜底。
        import tempfile

        try:
            from liteparse import LiteParse

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
                tmp = Path(fh.name)
            image.save(str(tmp), "PNG")
            try:
                out = LiteParse().parse(str(tmp), output_format="text")
                if isinstance(out, str):
                    return out.strip()
                return str(getattr(out, "text", "") or "").strip()
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"LiteParse OCR 失败: {e}")
            return ""
    return ""


def ocr_pdf_pages(
    pdf_path: Path,
    pages: Optional[Iterable[int]] = None,
    on_page: Optional[Callable[[int, str, int], None]] = None,
    should_abort: Optional[Callable[[], bool]] = None,
    engine: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> Dict[int, str]:
    """逐页 OCR，返回 {1 基页码: 文本}。已缓存的页直接复用。

    `on_page(done_count, text, total)` 每完成一页回调（用于进度）；
    `should_abort()` 返回 True 时停止（已缓存页保留）。
    `IMPORT_OCR_WORKERS` 控制并发（默认 2，上限 3；1 = 串行）。
    `stats` 会被写入 cached / text / blank / failed 四类页码，
    用于区分“真正空白页”与“识别失败”。
    """
    pdf_path = Path(pdf_path)
    name, reason = select_engine(engine)
    if not name:
        logger.warning(f"本地 OCR 不可用，跳过 OCR: {reason}")
        return {}
    try:
        import fitz

        doc = fitz.open(str(pdf_path))
    except Exception as e:
        logger.warning(f"OCR 打开 PDF 失败: {e}")
        return {}

    dpi = _env_int("IMPORT_OCR_DPI", _DEFAULT_DPI)
    total = int(doc.page_count)
    target = sorted({int(p) for p in (pages or range(1, total + 1)) if 1 <= int(p) <= total})
    workers = _ocr_workers()
    out: Dict[int, str] = {}

    # 启动前已中止：不要提交任何页（否则会产出一堆空文本）
    if should_abort and should_abort():
        logger.info("[ocr] 启动前已收到中止信号，跳过 OCR")
        try:
            doc.close()
        except Exception:
            pass
        return {}

    # 串行：共享一个 doc，省掉逐页打开的开销
    if workers <= 1:
        done = 0
        try:
            for page_no in target:
                if should_abort and should_abort():
                    logger.info(f"[ocr] 收到中止信号，已处理 {done}/{len(target)} 页")
                    break
                cache = _cache_path(pdf_path, page_no, name, dpi)
                cached = _read_cache(cache)
                if cached is not None:
                    text = cached
                    _note(stats, "cached", page_no)
                else:
                    try:
                        image = _render_page(doc, page_no - 1, dpi)
                        text = _ocr_image(image, name)
                    except Exception as e:
                        logger.warning(f"[ocr] 第 {page_no} 页失败（记为识别失败）: {e}")
                        _note(stats, "failed", page_no)
                        text = ""
                    else:
                        _note(stats, "text" if str(text or "").strip() else "blank",
                              page_no)
                    _write_cache(cache, text)
                out[page_no] = text
                done += 1
                if on_page:
                    on_page(done, text, total)
        finally:
            try:
                doc.close()
            except Exception:
                pass
        return dict(sorted(out.items()))

    # 并行：任务内独立打开 PDF；主线程只做缓存/进度汇总
    try:
        doc.close()
    except Exception:
        pass
    from concurrent.futures import ThreadPoolExecutor, as_completed

    done = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ocr") as pool:
        futures = {pool.submit(_ocr_page, pdf_path, p, name, dpi, should_abort,
                               stats): p
                   for p in target}
        for fut in as_completed(futures):
            page_no = futures[fut]
            try:
                text = fut.result()
            except Exception as e:
                logger.warning(f"[ocr] 第 {page_no} 页失败（记为识别失败）: {e}")
                _note(stats, "failed", page_no)
                text = ""
            out[page_no] = text
            done += 1
            if on_page:
                on_page(done, text, total)
            if should_abort and should_abort():
                logger.info(f"[ocr] 收到中止信号，已处理 {done}/{len(target)} 页")
                for pending in futures:
                    pending.cancel()
                break
    return dict(sorted(out.items()))


def ocr_image(image: Any) -> str:
    """对已渲染的 PIL 图像做 OCR（供 page_index 复用，避免重复渲染）。"""
    name, reason = select_engine()
    if not name:
        logger.debug(f"OCR 不可用，跳过单图: {reason}")
        return ""
    try:
        return (_ocr_image(image, name) or "").strip()
    except Exception as e:
        logger.warning(f"单图 OCR 失败: {e}")
        return ""


def extract_page_text(pdf_path: Path, page_1based: int,
                      ocr_fallback: bool = True) -> Tuple[str, bool]:
    """取单页文本：优先文本层，空则本地 OCR。返回 (text, used_ocr)。"""
    pdf_path = Path(pdf_path)
    try:
        import fitz

        doc = fitz.open(str(pdf_path))
        try:
            total = int(doc.page_count)
            if total <= 0:
                return "", False
            page = max(1, min(int(page_1based or 1), total))
            text = (doc.load_page(page - 1).get_text("text") or "").strip()
        finally:
            doc.close()
    except Exception as e:
        logger.warning(f"读取单页文本失败: {e}")
        return "", False
    if text or not ocr_fallback:
        return text, False
    pages = ocr_pdf_pages(pdf_path, pages=[page_1based])
    return pages.get(int(page_1based), ""), True


__all__ = [
    "OCR_CACHE_VERSION",
    "select_engine",
    "engine_status",
    "page_count",
    "detect_scanned",
    "ocr_pdf_pages",
    "extract_page_text",
    "ocr_image",
]
