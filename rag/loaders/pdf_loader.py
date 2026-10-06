"""
PdfLoader - PDF 文件加载器

负责加载 .pdf 文件。
V2.3.0: 集成新的 PDF 分析器架构，支持元素检测和提取。
"""

from pathlib import Path
from typing import Any, Dict, List, Union, Optional
import hashlib
import json
import logging
import os

from .base import BaseLoader, LoadResult

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
            def _enrich_one(doc):
                try:
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
            
            # 提取图片文件（如果有）
            elements = self._extract_images_from_elements(elements, file_path)
            
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
            
            if not documents:
                logger.warning(f"文档转换失败: {file_path}")
                # 降级到旧架构
                return self._load_with_legacy_architecture(file_path)
            
            logger.info(f"PDF 加载成功（新架构）: {file_path}, {len(documents)} 个文档")
            return self._create_success_result(documents)
            
        except Exception as e:
            logger.warning(f"新架构加载失败，降级到旧架构: {str(e)}")
            return self._load_with_legacy_architecture(file_path)
    
    # ==================== 大 PDF 分段解析（2026-10-07） ====================
    # 实测：958 页教材一次性解析 ~84s、7.4 万个元素；按 200 页分段每段 ~17s。
    # 分段后每段独立完成并落盘缓存：超时/中断只丢当前段，重试从缺失段继续，
    # 不会"整本教材一次超时后全部重来"。页码按 page_offset 还原为**全书真实页码**。

    _RANGE_CACHE_SCHEMA = "pdfrange-v1"

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
                docs.extend(cached)
                continue
            parsed += 1
            # 注意：analyze 按**全文档绝对页号**迭代（start_page/end_page 是绝对下标），
            # 检测器收到的已经是真实页码，这里不能再传 page_offset，否则页码会被加倍
            # （实测把 958 页的书算到 1757 页）。
            elements = self.analyzer.analyze(file_path, start_page=start, end_page=end)
            elements = self._extract_images_from_elements(elements, file_path)
            part_docs = self.adapter.adapt(elements, file_path, merge_small=True,
                                           pre_merge_hook=hook)
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

    def _extract_images_from_elements(self, elements: list, file_path: Path) -> list:
        """
        从元素列表中提取图片文件
        
        Args:
            elements: PDFElement 列表
            file_path: PDF 文件路径
            
        Returns:
            更新后的元素列表
        """
        from ..multimodal.pdf.models import ElementType
        
        # 筛选出图片元素
        image_elements = [e for e in elements if e.type == ElementType.IMAGE]
        
        if not image_elements:
            return elements
        
        try:
            # 使用 ImageExtractor 提取图片
            # 调用 extract_all 方法，参数顺序为 (pdf_path, image_elements)
            image_paths = self.image_extractor.extract_all(file_path, image_elements)
            
            # 按页面分组图片元素
            page_groups = {}
            for element in image_elements:
                page_num = element.page
                if page_num not in page_groups:
                    page_groups[page_num] = []
                page_groups[page_num].append(element)
            
            # 按页面顺序映射路径
            path_index = 0
            enhanced_dict = {}
            for page_num in sorted(page_groups.keys()):
                page_elements = page_groups[page_num]
                for element in page_elements:
                    if path_index < len(image_paths):
                        # 为元素添加 image_path
                        element.content['image_path'] = str(image_paths[path_index])
                        element.content['extraction_method'] = 'image_extractor'
                        enhanced_dict[element.content.get('xref')] = element
                        path_index += 1
            
            # 更新原始元素列表
            result_elements = []
            for element in elements:
                if element.type == ElementType.IMAGE:
                    # 获取xref
                    xref = None
                    if hasattr(element, 'content') and isinstance(element.content, dict):
                        xref = element.content.get('xref')
                    
                    # 如果找到增强元素，则替换
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
            return elements
    
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
