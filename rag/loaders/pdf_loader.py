"""
PdfLoader - PDF 文件加载器

负责加载 .pdf 文件。
V2.3.0: 集成新的 PDF 分析器架构，支持元素检测和提取。
"""

from pathlib import Path
from typing import Any, Dict, List, Sequence, Union, Optional
import hashlib
import json
import logging
import os
import time

from .base import BaseLoader, LoadResult
from . import pdf_map, pdf_ocr

logger = logging.getLogger(__name__)


class PdfLoader(BaseLoader):
    """
    PDF 文件加载器
    
    支持加载 .pdf 文件，使用新的 PDF 分析器架构。
    
    V2.3.0 更新：
    - 集成 PDFAnalyzer 进行元素检测
    - 使用 DocumentAdapter 转换为 Document
    - 保持向后兼容性
    """
    
    def __init__(self, use_new_architecture: bool = True):
        """
        初始化 PDF 加载器
        
        Args:
            use_new_architecture: 是否使用新架构（默认 True）
        """
        self.use_new_architecture = use_new_architecture
        self._analyzer = None
        self._adapter = None
        self._image_extractor = None
        self._enricher = None
    
    @property
    def analyzer(self):
        """获取 PDF 分析器（延迟加载）"""
        if self._analyzer is None:
            from ..multimodal.pdf import PDFAnalyzer
            self._analyzer = PDFAnalyzer()
        return self._analyzer
    
    @property
    def adapter(self):
        """获取文档适配器（延迟加载）"""
        if self._adapter is None:
            from ..multimodal.pdf.adapters import DocumentAdapter
            self._adapter = DocumentAdapter()
        return self._adapter
    
    @property
    def image_extractor(self):
        """获取图片提取器（延迟加载）"""
        if self._image_extractor is None:
            from ..multimodal.pdf.extractors.image_extractor import ImageExtractor
            self._image_extractor = ImageExtractor()
        return self._image_extractor
    
    @property
    def enricher(self):
        """获取多模态文档增强器（延迟加载）"""
        if self._enricher is None:
            from ..enrichment import MultimodalEnricher
            self._enricher = MultimodalEnricher()
        return self._enricher
    
    def supports(self, file_path: Union[str, Path]) -> bool:
        """判断是否支持该文件"""
        file_path = Path(file_path)
        return file_path.suffix.lower() in ['.pdf']
    
    def load(self, file_path: Union[str, Path]) -> LoadResult:
        """
        加载 PDF 文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            LoadResult 包含加载的文档列表
        """
        try:
            file_path = self._validate_file(file_path)
            
            # 使用新架构
            if self.use_new_architecture:
                return self._load_with_new_architecture(file_path)
            else:
                return self._load_with_legacy_architecture(file_path)
            
        except Exception as e:
            return self._create_error_result(str(e))
    
    def _load_with_new_architecture(self, file_path: Path) -> LoadResult:
        """
        使用新架构加载 PDF
        
        Args:
            file_path: 文件路径
            
        Returns:
            LoadResult
        """
        try:
            # 扫描件分流（2026-10-28）：文本层为空时走本地逐页 OCR，
            # 不再生成几百个 IMAGE 元素、逐图调用付费视觉（旧路径会 600s 超时并丢页）。
            if self._is_scanned_pdf(file_path):
                return self._load_scanned_pdf(file_path)

            # 文本层丰富的 PDF：只挑少量图做视觉，避免 20 张图 × 1 秒把解析拖到 20s+
            vision_limit = self._vision_image_limit(file_path)
            text_chars_per_page = self._text_chars_per_page(file_path)
            vision_state = {
                "images": 0, "started": time.time(), "limit": vision_limit,
                # 只在正文型 PDF 里跳过小图；图片型/扫描件仍保留完整视觉判断
                "skip_small": vision_limit <= 8,
            }

            def _enrich_one(doc):
                try:
                    metadata = getattr(doc, "metadata", None) or {}
                    modality = str(metadata.get("modality") or "")
                    if modality in ("image", "table", "formula"):
                        max_images = int(vision_state.get("limit") or 1)
                        budget_s = float(os.getenv("VISION_DOC_BUDGET_SECONDS", "180") or "180")
                        # 小图（图标/装饰/小公式）直接跳过视觉；页面扫描图不会命中
                        if modality == "image" and vision_state.get("skip_small"):
                            width = int(metadata.get("image_width") or 0)
                            height = int(metadata.get("image_height") or 0)
                            min_pixels = int(os.getenv("VISION_MIN_IMAGE_PIXELS", "40000") or "40000")
                            if width and height and width * height < min_pixels:
                                try:
                                    doc.metadata["vision_skip_reason"] = "too_small"
                                except Exception:
                                    pass
                                return doc
                        if (vision_state["images"] >= max_images
                                or (time.time() - vision_state["started"]) >= budget_s):
                            try:
                                doc.metadata["vision_skip_reason"] = "doc_budget"
                            except Exception:
                                pass
                            return doc
                        vision_state["images"] += 1
                    out = self.enricher._enrich_single_document(doc)
                    # 增强后正文为空的（判定为噪声/装饰）直接丢弃，不进索引
                    return out if (out.page_content or "").strip() else None
                except Exception as e:
                    logger.warning(f"增强单个元素失败，保留原文档: {e}")
                    return doc

            # 大 PDF（默认 >300 页）走**分段解析**：每段独立完成并落盘缓存，
            # 超时/中断只丢当前段，重试从缺失段继续；页码按段偏移还原为全书真实页码。
            # 实测 958 页教材：一次性 ~84s/7.4 万元素；200 页一段仅 ~17s。
            page_count = self._pdf_page_count(file_path)
            range_stats = None
            if page_count and page_count > self._range_threshold():
                documents = self._load_pdf_in_ranges(file_path, _enrich_one)
                range_stats = getattr(self, "_last_range_stats", None)
                if documents:
                    meta = dict(range_stats or {})
                    meta["pages_total"] = page_count
                    meta["pages_parsed"] = page_count
                    logger.info(f"PDF 加载成功（分段）: {file_path}, {len(documents)} 个文档, "
                                f"{meta}")
                    return LoadResult(documents=documents, success=True, metadata=meta)
                logger.warning(f"PDF 分段解析未产出文档，回退单次解析: {file_path}")

            # 使用 PDFAnalyzer 分析 PDF
            elements = self.analyzer.analyze(file_path)
            
            if not elements:
                logger.warning(f"PDF 分析未检测到任何元素: {file_path}")
                # 降级到旧架构
                return self._load_with_legacy_architecture(file_path)
            
            # 正文型 PDF：只保留最大的少量图做视觉，其余装饰图不落盘
            image_max = vision_limit if vision_limit <= 8 else None
            image_min_px = int(os.getenv("VISION_MIN_IMAGE_PIXELS", "40000") or "40000") if image_max else 0
            elements = self._extract_images_from_elements(
                elements, file_path, max_images=image_max, min_pixels=image_min_px)
            
            # 使用 DocumentAdapter 转换为 Document
            # v2.6：生产入库路径启用小元素合并。PDF 抽取是逐元素的，
            # 不合并会产生大量单字符 Document（实测中位长度 5 字符），
            # 下游分块器只能切分过长内容、无法把碎片拼回段落。
            #
            # 2026-10-02：多模态增强改到**合并之前**逐元素执行。
            # 原因：合并只保留块首元素的 metadata，会把 image/table/formula 的
            # modality 覆盖掉，导致下游增强器只认到块首那一个元素
            # （实测 90 张图只识别了 1 张）。放在合并前，每张图都能被识别，
            # 识别出的正文再随合并进入最终 chunk。
            documents = self.adapter.adapt(
                elements, file_path, merge_small=True, pre_merge_hook=_enrich_one)
            self._mark_page_base(documents)

            # 章节上下文（2026-10-28）：TOC 优先，字号/正则补充
            if documents and os.getenv("PDF_HEADING_DETECT", "1") not in ("0", "false", "off"):
                headings = pdf_map.merge_headings(
                    pdf_map.toc_headings(file_path),
                    pdf_map.collect_from_elements(elements))
                pdf_map.annotate(documents, headings)

            if not documents:
                logger.warning(f"文档转换失败: {file_path}")
                # 降级到旧架构
                return self._load_with_legacy_architecture(file_path)
            
            logger.info(f"PDF 加载成功（新架构）: {file_path}, {len(documents)} 个文档")
            meta = {
                "pages_total": page_count or 0,
                "pages_parsed": page_count or 0,
                "parse_engine": "pymupdf",
                "vision_limit": vision_limit,
                "text_chars_per_page": round(text_chars_per_page, 1),
                "vision_stats": getattr(self.enricher, "last_stats", {}) or {},
            }
            return self._create_success_result(documents, metadata=meta)
            
        except Exception as e:
            logger.warning(f"新架构加载失败，降级到旧架构: {str(e)}")
            return self._load_with_legacy_architecture(file_path)
    
    # ==================== 扫描件本地 OCR（2026-10-28） ====================

    @staticmethod
    def _mark_page_base(docs: List[Any]) -> None:
        """标记 PDF 的 page 是 0 基；展示层只对这种文档 +1。"""
        for doc in docs or []:
            meta = getattr(doc, "metadata", None)
            if meta is None:
                meta = {}
                try:
                    doc.metadata = meta
                except Exception:
                    continue
            meta.setdefault("page_base", 0)

    def _is_scanned_pdf(self, file_path: Path) -> bool:
        """是否走本地 OCR：PDF、文本层为空、且本机有可用 OCR 引擎。"""
        try:
            if file_path.suffix.lower() != ".pdf":
                return False
            if (os.getenv("IMPORT_OCR_ENGINE", "auto") or "").strip().lower() in (
                    "off", "none", "0", "false"):
                return False
            if not pdf_ocr.detect_scanned(file_path):
                return False
            name, reason = pdf_ocr.select_engine()
            if not name:
                logger.warning(
                    f"PDF 无文本层，但本地 OCR 不可用（{reason}）。"
                    "请安装 rapidocr/liteparse，或把 IMPORT_OCR_ENGINE 设为 off 以走原链路。")
                return False
            return True
        except Exception as e:
            logger.warning(f"扫描件判断失败（按普通 PDF 处理）: {e}")
            return False

    def _load_scanned_pdf(self, file_path: Path) -> LoadResult:
        """逐页本地 OCR，每页一个 Document（保留真实页码）。"""
        from langchain_core.documents import Document

        engine, reason = pdf_ocr.select_engine()
        if not engine:
            return self._create_error_result(
                f"扫描件需要本地 OCR，但当前不可用：{reason}",
                error_type="ocr_unavailable")
        total = pdf_ocr.page_count(file_path)
        logger.info(f"[pdf-ocr] 扫描件 {file_path.name}：{total} 页，引擎={engine}")
        started = time.time()
        page_stats: Dict[str, Any] = {}
        pages = pdf_ocr.ocr_pdf_pages(
            file_path, on_page=self._report_ocr_page,
            should_abort=self._ocr_should_abort, engine=engine, stats=page_stats)

        documents: List[Any] = []
        for page_no, text in sorted((pages or {}).items()):
            cleaned = (text or "").strip()
            if not cleaned:
                continue
            documents.append(Document(page_content=cleaned, metadata={
                "type": "text",
                "modality": "text",
                "page": max(0, int(page_no) - 1),  # 0 基，展示时统一 +1
                "page_base": 0,
                "source": str(file_path),
                "file_name": file_path.name,
                "file_type": "pdf",
                "ocr": True,
                "ocr_engine": engine,
            }))

        headings = pdf_map.headings_from_pages(pages or {})
        pdf_map.annotate(documents, headings)
        elapsed_ms = int((time.time() - started) * 1000)

        # 空 OCR 页不允许静默跳过：记下页码并判断是否为纯图（大概率是图表/插图），
        # 让任务与日志都能明确提示“这些页没被理解”，而不是只报一个成功总结。
        blank_pages = sorted(int(p) for p in (page_stats.get("blank") or []))
        failed_pages = sorted(int(p) for p in (page_stats.get("failed") or []))
        figure_pages = self._pages_with_images(file_path, blank_pages)
        if failed_pages:
            preview = "、".join(str(p) for p in failed_pages[:20])
            logger.warning(f"[pdf-ocr] {file_path.name} 有 {len(failed_pages)} 页 OCR 失败"
                           f"（不是空白页，需要人工确认）：第 {preview} 页")
        if blank_pages:
            preview = "、".join(str(p) for p in blank_pages[:20])
            more = "…" if len(blank_pages) > 20 else ""
            suffix = (f"；其中第 {'、'.join(str(p) for p in figure_pages[:20])} 页"
                      f"含嵌入式图片，可能主要由图表/插图构成" if figure_pages else "")
            logger.warning(
                f"[pdf-ocr] {file_path.name} 有 {len(blank_pages)}/{total} 页未识别到文本："
                f"第 {preview}{more} 页{suffix}。"
                "扫描件中的插图/图表本身不会被理解，只能取到其中的印刷文字。")

        metadata = {
            "pages_total": total,
            "pages_parsed": len(documents),
            "parse_engine": f"local_ocr:{engine}",
            "ocr_stats": {
                "engine": engine,
                "pages": len(pages or {}),
                "pages_with_text": len(documents),
                "pages_blank": len(blank_pages),
                "pages_blank_list": blank_pages[:100],
                # 与“空白页”分开：这些页 OCR 抛错，需要人工确认而不是当成没内容
                "pages_failed": len(failed_pages),
                "pages_failed_list": failed_pages[:100],
                "pages_cached": len(page_stats.get("cached") or []),
                "pages_figure_only": len(figure_pages),
                "figures_interpreted": False,
                "elapsed_ms": elapsed_ms,
            },
        }
        if not documents:
            return self._create_error_result(
                f"扫描件本地 OCR 未产出文本（引擎={engine}，{total} 页）。"
                "请检查扫描清晰度或换用其他 OCR 引擎。",
                metadata=metadata, error_type="ocr_unavailable")
        logger.info(f"[pdf-ocr] {file_path.name} 完成：{len(documents)}/{total} 页有文本，"
                    f"{elapsed_ms} ms")
        return LoadResult(documents=documents, success=True, metadata=metadata)

    @staticmethod
    def _pages_with_images(file_path: Path, page_numbers: Sequence[int]) -> List[int]:
        """判断哪些页含嵌入式图片（用于区分“空白页”与“纯图/图表页”）。"""
        if not page_numbers:
            return []
        out: List[int] = []
        try:
            import fitz

            doc = fitz.open(str(file_path))
        except Exception:
            return []
        try:
            for page_no in page_numbers:
                try:
                    if doc[page_no - 1].get_images(full=True):
                        out.append(int(page_no))
                except Exception:
                    continue
        finally:
            try:
                doc.close()
            except Exception:
                pass
        return out

    def _report_ocr_page(self, page_no: int, text: str, total: int) -> None:
        """把页级进度上报给导入任务（失败静默，绝不影响解析）。"""
        try:
            from services.import_service import _report_parse_progress
            _report_parse_progress(page_no, total)
        except Exception:
            pass

    def _ocr_should_abort(self) -> bool:
        try:
            from services.import_service import _vision_abort_requested
            return bool(_vision_abort_requested())
        except Exception:
            return False

    # ==================== 大 PDF 分段解析（2026-10-07） ====================
    # 实测：958 页教材一次性解析 ~84s、7.4 万个元素；按 200 页分段每段 ~17s。
    # 分段后每段独立完成并落盘缓存：超时/中断只丢当前段，重试从缺失段继续，
    # 不会"整本教材一次超时后全部重来"。页码按 page_offset 还原为**全书真实页码**。

    _RANGE_CACHE_SCHEMA = "pdfrange-v2"

    def _range_pages(self) -> int:
        try:
            return max(20, int(os.getenv("IMPORT_PDF_RANGE_PAGES", "200")))
        except (TypeError, ValueError):
            return 200

    def _range_threshold(self) -> int:
        try:
            return max(1, int(os.getenv("IMPORT_PDF_RANGE_THRESHOLD", "300")))
        except (TypeError, ValueError):
            return 300

    def _pdf_page_count(self, file_path: Path) -> int:
        try:
            import fitz
            doc = fitz.open(str(file_path))
            n = doc.page_count
            doc.close()
            return int(n)
        except Exception as e:
            logger.warning(f"读取 PDF 页数失败（回退单次解析）: {e}")
            return 0

    def _text_chars_per_page(self, file_path: Path, sample_pages: int = 5) -> float:
        """采样估算文本层密度，用于决定视觉预算；不 OCR、不渲染。"""
        try:
            import fitz
            doc = fitz.open(str(file_path))
            total = doc.page_count
            if total <= 0:
                doc.close()
                return 0.0
            indexes = {0, total - 1, total // 2}
            if total >= 4:
                indexes.add(total // 4)
                indexes.add((total * 3) // 4)
            chars = sum(len(doc[i].get_text("text") or "") for i in sorted(indexes)[:sample_pages])
            doc.close()
            return chars / max(1, min(sample_pages, len(indexes)))
        except Exception as e:
            logger.debug(f"PDF 文本层采样失败（按普通 PDF 处理）: {e}")
            return 0.0

    def _vision_image_limit(self, file_path: Path) -> int:
        """文本层越丰富，视觉预算越小；扫描件/图片型 PDF 保持完整预算。

        分档（可用环境变量覆盖）：
        - >= 100 字符/页：最多 4 张，属于正文型 PDF，图多为公式/示意；
        - >= 50 字符/页：最多 8 张，正文与图混合；
        - 其余（扫描/图片型）：沿用 VISION_MAX_IMAGES_PER_DOC（默认 20）。
        """
        try:
            base = max(1, int(os.getenv("VISION_MAX_IMAGES_PER_DOC", "20") or "20"))
            text_limit = max(1, int(os.getenv("VISION_MAX_IMAGES_PER_TEXT_PDF", "4") or "4"))
            semi_limit = max(1, int(os.getenv("VISION_MAX_IMAGES_PER_SEMI_TEXT_PDF", "8") or "8"))
            threshold = max(1, int(os.getenv("VISION_TEXT_PDF_CHARS_PER_PAGE", "100") or "100"))
            semi_threshold = max(1, int(os.getenv(
                "VISION_TEXT_PDF_MID_CHARS_PER_PAGE", "50") or "50"))
            text_density = self._text_chars_per_page(file_path)
            if text_density >= threshold:
                return min(base, text_limit)
            if text_density >= semi_threshold:
                return min(base, semi_limit)
        except (TypeError, ValueError):
            pass
        return max(1, int(os.getenv("VISION_MAX_IMAGES_PER_DOC", "20") or "20"))

    def _range_cache_dir(self) -> Path:
        from config.path_config import get_data_dir
        d = Path(get_data_dir()) / "cache" / "pdf_ranges"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _file_sha(self, file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:24]

    def _range_cache_path(self, file_path: Path, start: int, end: int, sha: str) -> Path:
        try:
            from ..enrichment.multimodal_enricher import VISION_PROMPT_VERSION
            ver = str(VISION_PROMPT_VERSION)
        except Exception:
            ver = "0"
        key = f"{sha}_{start}_{end}_{ver}_{self._RANGE_CACHE_SCHEMA}"
        return self._range_cache_dir() / f"{key}.json"

    @staticmethod
    def _docs_to_json(docs: List[Any]) -> List[dict]:
        out = []
        for d in docs:
            try:
                md = json.loads(json.dumps(d.metadata or {}, ensure_ascii=False, default=str))
            except Exception:
                md = {}
            out.append({"content": d.page_content or "", "metadata": md})
        return out

    @staticmethod
    def _docs_from_json(payload: List[dict]) -> List[Any]:
        from langchain_core.documents import Document
        return [Document(page_content=it.get("content", ""), metadata=it.get("metadata") or {})
                for it in payload or []]

    def _load_pdf_in_ranges(self, file_path: Path, hook: Any) -> List[Any]:
        """按页范围解析大 PDF，逐段落盘缓存，重试只补齐缺失段。"""
        import fitz
        doc = fitz.open(str(file_path))
        total = doc.page_count
        doc.close()
        span = self._range_pages()
        sha = self._file_sha(file_path)
        ranges = [(s, min(s + span, total)) for s in range(0, total, span)]
        docs: List[Any] = []
        parsed = 0
        headings_toc = (pdf_map.toc_headings(file_path)
                        if os.getenv("PDF_HEADING_DETECT", "1") not in ("0", "false", "off")
                        else [])
        if not ranges:
            return []
        for start, end in ranges:
            cp = self._range_cache_path(file_path, start, end, sha)
            cached = None
            if cp.exists():
                try:
                    cached = self._docs_from_json(json.loads(cp.read_text(encoding="utf-8")))
                except Exception:
                    cached = None
            if cached is not None:
                self._mark_page_base(cached)
                if headings_toc:
                    pdf_map.annotate(cached, headings_toc)
                docs.extend(cached)
                continue
            parsed += 1
            # 注意：analyze 按**全文档绝对页号**迭代（start_page/end_page 是绝对下标），
            # 检测器收到的已经是真实页码，这里不能再传 page_offset，否则页码会被加倍
            # （实测把 958 页的书算到 1757 页）。
            elements = self.analyzer.analyze(file_path, start_page=start, end_page=end)
            vision_limit = self._vision_image_limit(file_path)
            image_max = vision_limit if vision_limit <= 8 else None
            image_min_px = int(os.getenv("VISION_MIN_IMAGE_PIXELS", "40000") or "40000") if image_max else 0
            elements = self._extract_images_from_elements(
                elements, file_path, max_images=image_max, min_pixels=image_min_px)
            part_docs = self.adapter.adapt(elements, file_path, merge_small=True,
                                           pre_merge_hook=hook)
            self._mark_page_base(part_docs)
            # 章节上下文（2026-10-28）：TOC 优先，字号/OCR 补充；写进缓存供重试复用
            range_headings = pdf_map.merge_headings(
                headings_toc, pdf_map.collect_from_elements(elements))
            pdf_map.annotate(part_docs, range_headings)
            for d in part_docs:
                try:
                    d.metadata.setdefault("page_range", f"{start + 1}-{end}")
                except Exception:
                    pass
            try:
                cp.write_text(json.dumps(self._docs_to_json(part_docs), ensure_ascii=False),
                              encoding="utf-8")
            except Exception as e:
                logger.warning(f"PDF 分段缓存写入失败（不影响本次解析）: {e}")
            docs.extend(part_docs)
            logger.info(f"[pdf-range] {file_path.name} 段 {start + 1}-{end} 完成，"
                        f"累计 {len(docs)} 个文档")
        self._last_range_stats = {"pages_total": total, "ranges": len(ranges),
                                  "ranges_parsed": parsed,
                                  "ranges_cached": len(ranges) - parsed}
        return docs

    def _extract_images_from_elements(self, elements: list, file_path: Path,
                                      max_images: Optional[int] = None,
                                      min_pixels: int = 0) -> list:
        """
        从元素列表中提取图片文件。

        max_images/min_pixels 用于正文型 PDF：只保留最大的少量图，
        小图标/装饰图不再落盘。默认 None/0 保持旧行为。
        """
        from ..multimodal.pdf.models import ElementType

        image_elements = [e for e in elements if e.type == ElementType.IMAGE]
        if not image_elements:
            return elements

        def _area(element) -> int:
            content = getattr(element, "content", None)
            if not isinstance(content, dict):
                return 0
            try:
                return int(content.get("width") or 0) * int(content.get("height") or 0)
            except (TypeError, ValueError):
                return 0

        kept_images = image_elements
        if min_pixels > 0:
            kept_images = [e for e in kept_images if _area(e) >= min_pixels]
        if max_images is not None and len(kept_images) > max_images:
            top_ids = {
                id(e) for e in sorted(kept_images, key=_area, reverse=True)[:max_images]
            }
            kept_images = [e for e in kept_images if id(e) in top_ids]
        keep_ids = {id(e) for e in kept_images}
        kept_elements = [
            e for e in elements
            if e.type != ElementType.IMAGE or id(e) in keep_ids
        ]
        dropped = len(image_elements) - len(kept_images)
        if not kept_images:
            logger.info(
                f"图片提取过滤：{file_path.name} 跳过 {dropped} 张"
                f"（max_images={max_images}, min_pixels={min_pixels}）")
            return kept_elements
        if dropped:
            logger.info(
                f"图片提取过滤：{file_path.name} 保留 {len(kept_images)} 张，"
                f"跳过 {dropped} 张（max_images={max_images}, min_pixels={min_pixels}）")

        try:
            image_paths = self.image_extractor.extract_all(file_path, kept_images)

            page_groups = {}
            for element in kept_images:
                page_num = element.page
                page_groups.setdefault(page_num, []).append(element)

            path_index = 0
            enhanced_dict = {}
            for page_num in sorted(page_groups.keys()):
                for element in page_groups[page_num]:
                    if path_index < len(image_paths):
                        element.content['image_path'] = str(image_paths[path_index])
                        element.content['extraction_method'] = 'image_extractor'
                        enhanced_dict[element.content.get('xref')] = element
                        path_index += 1

            result_elements = []
            for element in kept_elements:
                if element.type == ElementType.IMAGE:
                    xref = None
                    if hasattr(element, 'content') and isinstance(element.content, dict):
                        xref = element.content.get('xref')
                    if xref is not None and xref in enhanced_dict:
                        result_elements.append(enhanced_dict[xref])
                    else:
                        result_elements.append(element)
                else:
                    result_elements.append(element)

            logger.info(f"提取了 {len(image_paths)} 个图片文件")
            return result_elements
        except Exception as e:
            logger.warning(f"图片提取失败，继续处理: {str(e)}")
            return kept_elements

    def _load_with_legacy_architecture(self, file_path: Path) -> LoadResult:
        """
        使用旧架构加载 PDF（向后兼容）
        
        Args:
            file_path: 文件路径
            
        Returns:
            LoadResult
        """
        try:
            # 检查 LangChain PyPDFLoader 是否可用
            try:
                from langchain_community.document_loaders import PyPDFLoader
                langchain_available = True
            except ImportError:
                langchain_available = False
            
            # 尝试使用 LangChain PyPDFLoader
            if langchain_available:
                try:
                    loader = PyPDFLoader(str(file_path))
                    documents = loader.load()
                    return self._create_success_result(documents)
                except Exception as e:
                    logger.warning(f"LangChain PyPDFLoader failed: {e}, using fallback")
            
            # 降级方案：使用 pdfplumber
            try:
                import pdfplumber
                
                documents = []
                with pdfplumber.open(file_path) as pdf:
                    for i, page in enumerate(pdf.pages):
                        content = page.extract_text()
                        if content:
                            # 创建 Document 对象
                            from langchain_core.documents import Document
                            metadata = {
                                # v2.6：source 只存文件名（避免完整本地路径进入 LLM 提示词）
                                'source': file_path.name,
                                'source_path': str(file_path),
                                'file_name': file_path.name,
                                'file_type': 'pdf',
                                'page': i + 1,
                                'total_pages': len(pdf.pages)
                            }
                            documents.append(Document(page_content=content, metadata=metadata))
                
                if not documents:
                    return self._create_error_result("PDF文件无法提取文本内容")
                
                return self._create_success_result(documents)
                
            except ImportError:
                return self._create_error_result("PDF加载需要安装langchain-community或pdfplumber包")
            except Exception as e:
                return self._create_error_result(f"读取PDF文件失败: {str(e)}")
            
        except Exception as e:
            return self._create_error_result(str(e))
    
    def get_supported_extensions(self) -> List[str]:
        """获取支持的文件扩展名"""
        return ['.pdf']
    
    def get_elements(self, file_path: Union[str, Path]) -> List:
        """
        获取 PDF 元素（新架构特有）
        
        Args:
            file_path: 文件路径
            
        Returns:
            PDFElement 列表
        """
        file_path = Path(file_path)
        return self.analyzer.analyze(file_path)