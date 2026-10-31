"""
PptxLoader - .pptx 课件加载器（2026-10-07）

真实解析（python-pptx），不是"能选中后缀"：
- 按幻灯片顺序输出，每页一个 Document
- metadata 至少含 page(1 起) / slide_title / modality="slide" / file_name
- 抽取：幻灯片标题、占位符与文本框正文、表格（Markdown）、有意义的备注、图片
- **组合形状（group shapes）递归**，不只读最外层
- 图片走现有视觉链路（MultimodalEnricher），识别正文进入同一份资料
- 空白/无文本页如实标记 slide_text_empty，不当成解析成功
- 引用能显示原课件名与真实幻灯片编号（metadata.page 被 extract_sources 使用）
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

from .base import BaseLoader, LoadResult

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff"}


def _table_to_markdown(table: Any) -> str:
    """把 python-pptx 表格转成 Markdown（单元格内的 | 转义，空表返回空串）"""
    try:
        rows = list(table.rows)
    except Exception:
        return ""
    if not rows:
        return ""
    lines: List[str] = []
    for r_idx, row in enumerate(rows):
        cells = []
        for cell in row.cells:
            text = (cell.text or "").replace("\n", " ").replace("|", "\\|").strip()
            cells.append(text)
        if any(cells):
            lines.append("| " + " | ".join(cells) + " |")
            if r_idx == 0:
                lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
    return "\n".join(lines)


def _iter_shapes(shapes: Any):
    """递归遍历形状，展开组合形状（group shapes）"""
    for shape in shapes:
        if _is_group(shape):
            inner = getattr(shape, "shapes", None)
            if inner is not None:
                yield from _iter_shapes(inner)
                continue
        yield shape


def _is_group(shape: Any) -> bool:
    try:
        from pptx.enum.shapes import MSO_SHAPE_TYPE
        return shape.shape_type == MSO_SHAPE_TYPE.GROUP
    except Exception:
        return hasattr(shape, "shapes")


def _iter_pictures(shapes: Any):
    """递归遍历形状，产出所有图片形状"""
    for shape in _iter_shapes(shapes):
        try:
            from pptx.enum.shapes import MSO_SHAPE_TYPE
            is_pic = shape.shape_type == MSO_SHAPE_TYPE.PICTURE
        except Exception:
            is_pic = hasattr(shape, "image")
        if is_pic:
            yield shape


class PptxLoader(BaseLoader):
    """PowerPoint (.pptx) 加载器"""

    def __init__(self, enricher: Any = None, image_output_dir: Optional[str] = None,
                 enrich_images: bool = True, defer_vision: bool = False):
        self._enricher = enricher
        self._image_output_dir = image_output_dir
        self._enrich_images = enrich_images
        # 2026-10-31 导入提速：defer_vision=正文先行——文本页立即返回，
        # 图片只做"缓存快扫"（命中的带内容入库），未命中的标 deferred
        # 交给导入服务在建库后统一增强并增量补入。不开启时维持旧行为。
        # 默认由 IMPORT_VISION_DEFER 控制（默认开启；设 0/false 关闭回旧行为）。
        if not defer_vision:
            import os as _os
            defer_vision = _os.getenv("IMPORT_VISION_DEFER", "1").strip().lower() not in (
                "0", "false", "off")
        self._defer_vision = defer_vision

    def supports(self, file_path: Union[str, Path]) -> bool:
        return Path(file_path).suffix.lower() == ".pptx"

    def get_supported_extensions(self) -> List[str]:
        return [".pptx"]

    # ---------- 内部工具 ----------

    def _get_enricher(self) -> Optional[Any]:
        if self._enricher is not None:
            return self._enricher
        try:
            from ..enrichment import MultimodalEnricher
            self._enricher = MultimodalEnricher()
        except Exception as e:
            logger.warning(f"MultimodalEnricher 不可用，图片将不做视觉增强: {e}")
            self._enricher = None
        return self._enricher

    def _new_image_dir(self) -> Tuple[Path, bool]:
        """返回（图片目录, 是否由本 loader 临时创建）"""
        if self._image_output_dir:
            p = Path(self._image_output_dir)
            p.mkdir(parents=True, exist_ok=True)
            return p, False
        from config.path_config import get_data_dir
        base = Path(get_data_dir()) / "import_tmp" / "pptx_images"
        p = base / uuid.uuid4().hex[:12]
        p.mkdir(parents=True, exist_ok=True)
        return p, True

    def _save_picture(self, shape: Any, image_dir: Path, base_name: str,
                      seq: int) -> Optional[Path]:
        try:
            image = shape.image
            blob = image.blob
            ext = (image.ext or "png").lower()
            if not ext.startswith("."):
                ext = "." + ext
            if ext not in _IMAGE_EXTS:
                ext = ".png"
            out = image_dir / f"{base_name}_img{seq}{ext}"
            out.write_bytes(blob)
            return out
        except Exception as e:
            logger.warning(f"提取 PPTX 图片失败（跳过该图）: {e}")
            return None

    # ---------- 主流程 ----------

    def load(self, file_path: Union[str, Path]) -> LoadResult:
        try:
            file_path = self._validate_file(file_path)
        except Exception as e:
            return self._create_error_result(str(e), error_type="io_error")

        if file_path.suffix.lower() != ".pptx":
            return self._create_error_result(
                f"PptxLoader 不支持 {file_path.suffix.lower()}（仅支持 .pptx）",
                error_type="unsupported_format")

        try:
            from pptx import Presentation
        except ImportError:
            return self._create_error_result(
                "解析 .pptx 需要 python-pptx，请先安装：pip install python-pptx",
                error_type="unsupported_format")

        try:
            presentation = Presentation(str(file_path))
        except Exception as e:
            return self._create_error_result(
                f"无法打开 PPTX 文件（可能损坏或不是真正的 pptx）: {e}",
                error_type="parse_error")

        total_slides = len(presentation.slides)
        image_dir, temp_created = self._new_image_dir()
        image_docs: List[Any] = []
        slide_docs: List[Any] = []
        empty_slides: List[int] = []
        image_total = 0
        has_any_content = False

        try:
            from langchain_core.documents import Document
        except ImportError:  # pragma: no cover - 运行环境始终有 langchain
            from rag.text_splitter import SimpleDocument as Document  # type: ignore

        try:
            for slide_index, slide in enumerate(presentation.slides, start=1):
                title = ""
                try:
                    if slide.shapes.title is not None:
                        title = (slide.shapes.title.text or "").strip()
                except Exception:
                    title = ""

                text_parts: List[str] = []
                table_count = 0
                title_el = None
                try:
                    title_shape = slide.shapes.title
                    title_el = getattr(title_shape, "_element", None)
                except Exception:
                    title_el = None
                for shape in _iter_shapes(slide.shapes):
                    # 标题已单独抽取，避免正文重复一遍（按 XML 元素判等，
                    # python-pptx 每次访问 .title 可能返回不同代理对象）
                    if title_el is not None and getattr(shape, "_element", None) is title_el:
                        continue
                    # 表格
                    try:
                        if getattr(shape, "has_table", False) and shape.has_table:
                            md = _table_to_markdown(shape.table)
                            if md:
                                text_parts.append(md)
                                table_count += 1
                            continue
                    except Exception:
                        pass
                    # 文本框 / 占位符正文
                    try:
                        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                            for para in shape.text_frame.paragraphs:
                                text = "".join(
                                    run.text or "" for run in para.runs).strip()
                                if text:
                                    text_parts.append(text)
                    except Exception:
                        pass

                # 备注（只取有意义的）
                notes = ""
                try:
                    if slide.has_notes_slide:
                        notes = (slide.notes_slide.notes_text_frame.text or "").strip()
                except Exception:
                    notes = ""
                if notes and len(notes) >= 2 and notes not in text_parts:
                    text_parts.append(f"[备注] {notes}")

                # 图片
                pic_seq = 0
                for pic in _iter_pictures(slide.shapes):
                    pic_seq += 1
                    img_path = self._save_picture(pic, image_dir,
                                                  f"slide{slide_index:03d}", pic_seq)
                    if img_path is None:
                        continue
                    image_total += 1
                    image_docs.append(Document(
                        page_content="",
                        metadata={
                            "source": file_path.name,
                            "source_path": str(file_path),
                            "file_name": file_path.name,
                            "file_type": img_path.suffix.lstrip("."),
                            "modality": "image",
                            "image_path": str(img_path),
                            "page": slide_index,
                            "slide_title": title,
                            "slide_image_index": pic_seq,
                            "origin_file_type": "pptx",
                        },
                    ))

                body = "\n".join(p for p in text_parts if p).strip()
                if body or title:
                    has_any_content = True
                else:
                    empty_slides.append(slide_index)

                content_parts = []
                if title:
                    content_parts.append(title)
                if body:
                    content_parts.append(body)
                slide_content = "\n".join(content_parts).strip()

                slide_docs.append(Document(
                    page_content=slide_content,
                    metadata={
                        "source": file_path.name,
                        "source_path": str(file_path),
                        "file_name": file_path.name,
                        "file_type": "pptx",
                        "modality": "slide",
                        "page": slide_index,
                        "slide_title": title,
                        "slide_text_empty": not bool(body),
                        "slide_table_count": table_count,
                        "slide_image_count": pic_seq,
                        "total_slides": total_slides,
                    },
                ))

            # 视觉增强（只对图片文档；文本/幻灯片文档保留既有 modality）
            #
            # 2026-10-07：改走批量入口 enrich_images —— 同一批内按内容哈希去重、
            # 受控并发（VISION_MAX_CONCURRENCY，默认 3）、单张超时（
            # VISION_IMAGE_TIMEOUT_SECONDS，默认 30s）。此前逐张串行，
            # 单张 40s+ 的调用会把整份课件拖到分钟级。
            vision_stats: dict = {}
            if self._defer_vision and image_docs:
                # 正文先行：缓存快扫（0 API），未命中的标 deferred 留给后台增强
                enricher = self._get_enricher()
                if enricher is not None:
                    try:
                        image_docs = enricher.fill_images_from_cache(image_docs)
                        deferred = sum(1 for d in image_docs
                                       if (d.metadata or {}).get("vision_status") == "deferred")
                        vision_stats = {
                            "images_total": len(image_docs),
                            "vision_deferred": deferred,
                            "vision_cached": len(image_docs) - deferred,
                        }
                        logger.info(
                            f"[PptxLoader] 正文先行: {len(image_docs)} 张图片，"
                            f"缓存命中 {len(image_docs) - deferred}，待后台增强 {deferred}")
                    except Exception as e:
                        logger.warning(f"PPTX 缓存快扫失败，图片全部延后: {e}")
                        for d in image_docs:
                            d.metadata["vision_status"] = "deferred"
                        vision_stats = {"images_total": len(image_docs),
                                        "vision_deferred": len(image_docs)}
            elif self._enrich_images and image_docs:
                enricher = self._get_enricher()
                if enricher is not None:
                    try:
                        image_docs = enricher.enrich_images(image_docs)
                    except Exception as e:
                        logger.warning(f"PPTX 图片批量增强失败，保留原文档: {e}")
                    image_docs = [d for d in image_docs
                                  if d is not None and (d.page_content or "").strip()]
                    vision_stats = dict(getattr(enricher, "last_stats", {}) or {})
                    if image_docs:
                        has_any_content = True

            documents = slide_docs + image_docs

            if not has_any_content:
                return self._create_error_result(
                    f"PPTX 没有可解析内容（{len(slide_docs)} 页全部为空白/无文本）: {file_path.name}",
                    metadata={"slides": len(slide_docs), "empty_slides": empty_slides,
                              "images": image_total},
                    error_type="empty_content")

            return self._create_success_result(
                documents,
                metadata={
                    "slides": len(slide_docs),
                    "empty_slides": empty_slides,
                    "images": image_total,
                    "images_indexed": len(image_docs),
                    "vision_stats": vision_stats,
                },
            )
        finally:
            # 2026-10-31 正文先行：defer 模式必须保留图片目录——deferred 图的
            # image_path 指向这里，任务收尾删除会导致后台增强"文件丢失"全跳过
            # （实测 persisted=0）。目录由导入服务在后台增强完成后清理。
            if temp_created and not self._defer_vision:
                shutil.rmtree(image_dir, ignore_errors=True)


__all__ = ["PptxLoader"]
