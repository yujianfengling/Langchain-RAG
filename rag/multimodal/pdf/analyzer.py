"""
PDF 元素分析器

负责分析 PDF 文件，检测和提取各种元素。
"""

import logging
from pathlib import Path
from typing import List, Union, Optional

from .models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class PDFAnalyzer:
    """
    PDF 元素分析器
    
    分析 PDF 文件，检测文本、图片、表格、公式等元素。
    
    Attributes:
        detect_text: 是否检测文本
        detect_images: 是否检测图片
        detect_tables: 是否检测表格
        detect_formulas: 是否检测公式
    """
    
    def __init__(
        self,
        detect_text: bool = True,
        detect_images: bool = True,
        detect_tables: bool = True,
        detect_formulas: bool = True
    ):
        """
        初始化 PDF 分析器
        
        Args:
            detect_text: 是否检测文本
            detect_images: 是否检测图片
            detect_tables: 是否检测表格
            detect_formulas: 是否检测公式
        """
        self.detect_text = detect_text
        self.detect_images = detect_images
        self.detect_tables = detect_tables
        self.detect_formulas = detect_formulas
        
        # 延迟加载检测器
        self._text_detector = None
        self._image_detector = None
        self._table_detector = None
        self._formula_detector = None
        
        # 检查 PyMuPDF 是否可用
        self._pymupdf_available = self._check_pymupdf()
        
        logger.info(f"PDFAnalyzer 初始化完成: text={detect_text}, images={detect_images}, tables={detect_tables}, formulas={detect_formulas}")
    
    def _check_pymupdf(self) -> bool:
        """检查 PyMuPDF 是否可用"""
        try:
            import fitz
            logger.info(f"PyMuPDF 可用，版本: {fitz.version}")
            return True
        except ImportError:
            logger.warning("PyMuPDF 不可用，某些功能将受限")
            return False
    
    @property
    def text_detector(self):
        """获取文本检测器（延迟加载）"""
        if self._text_detector is None and self.detect_text:
            from .detectors.text_detector import TextDetector
            self._text_detector = TextDetector()
        return self._text_detector
    
    @property
    def image_detector(self):
        """获取图片检测器（延迟加载）"""
        if self._image_detector is None and self.detect_images:
            from .detectors.image_detector import ImageDetector
            self._image_detector = ImageDetector()
        return self._image_detector
    
    @property
    def table_detector(self):
        """获取表格检测器（延迟加载）"""
        if self._table_detector is None and self.detect_tables:
            from .detectors.table_detector import TableDetector
            self._table_detector = TableDetector()
        return self._table_detector
    
    @property
    def formula_detector(self):
        """获取公式检测器（延迟加载）"""
        if self._formula_detector is None and self.detect_formulas:
            from .detectors.formula_detector import FormulaDetector
            self._formula_detector = FormulaDetector()
        return self._formula_detector
    
    def analyze(self, pdf_path: Union[str, Path], start_page: int = 0,
                end_page: Optional[int] = None, page_offset: int = 0) -> List[PDFElement]:
        """
        分析 PDF 文件（可按页范围分段）。

        Args:
            pdf_path: PDF 文件路径
            start_page: 起始页（0 基，**含**），默认 0
            end_page: 结束页（0 基，**不含**），None=到末页
            page_offset: 传给检测器的页码偏移。分段解析时用它把"分段内页码"
                         还原成**全书真实页码**（例如第 2 段从 200 开始解析时 offset=200），
                         保证引用里的页码是真实页码而不是段内页码。

        Returns:
            检测到的元素列表

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 不支持的文件格式

        2026-10-07：新增分段参数（默认值保持原行为）。
        背景：实测一本 958 页教材一次性解析需 ~84s 且一次产出 7.4 万个元素；
        按 200 页分段每段仅 ~17s，任一段失败/超时只丢该段，其余段可继续。
        """
        pdf_path = Path(pdf_path)
        
        # 验证文件
        if not pdf_path.exists():
            raise FileNotFoundError(f"文件不存在: {pdf_path}")
        
        if pdf_path.suffix.lower() != '.pdf':
            raise ValueError(f"不是 PDF 文件: {pdf_path}")
        
        if not self._pymupdf_available:
            logger.error("PyMuPDF 不可用，无法分析 PDF")
            return []
        
        try:
            import fitz
            
            # 打开 PDF 文件
            doc = fitz.open(str(pdf_path))
            
            all_elements = []
            
            # 遍历指定页范围（默认全书；分段解析时只处理本段）
            total_pages = len(doc)
            first = max(0, int(start_page or 0))
            last = total_pages if end_page is None else min(int(end_page), total_pages)
            for page_num in range(first, last):
                page = doc[page_num]
                # 传给检测器的页码 = 全书真实页码（0 基），保证元数据 page 正确
                absolute = page_num + int(page_offset or 0)

                # 检测各种元素
                if self.detect_text and self.text_detector:
                    text_elements = self.text_detector.detect(page, absolute)
                    all_elements.extend(text_elements)

                if self.detect_images and self.image_detector:
                    image_elements = self.image_detector.detect(page, absolute)
                    all_elements.extend(image_elements)

                if self.detect_tables and self.table_detector:
                    table_elements = self.table_detector.detect(page, absolute)
                    all_elements.extend(table_elements)

                if self.detect_formulas and self.formula_detector:
                    formula_elements = self.formula_detector.detect(page, absolute)
                    all_elements.extend(formula_elements)
            
            doc.close()
            
            logger.info(f"PDF 分析完成: {pdf_path}, 检测到 {len(all_elements)} 个元素")
            return all_elements
            
        except Exception as e:
            logger.error(f"PDF 分析失败: {pdf_path}, 错误: {str(e)}")
            raise
    
    def analyze_page(self, pdf_path: Union[str, Path], page_num: int) -> List[PDFElement]:
        """
        分析 PDF 的单页
        
        Args:
            pdf_path: PDF 文件路径
            page_num: 页码 (从 0 开始)
            
        Returns:
            检测到的元素列表
        """
        pdf_path = Path(pdf_path)
        
        if not pdf_path.exists():
            raise FileNotFoundError(f"文件不存在: {pdf_path}")
        
        if not self._pymupdf_available:
            logger.error("PyMuPDF 不可用，无法分析 PDF")
            return []
        
        try:
            import fitz
            
            doc = fitz.open(str(pdf_path))
            
            if page_num < 0 or page_num >= len(doc):
                raise ValueError(f"页码超出范围: {page_num} (总共 {len(doc)} 页)")
            
            page = doc[page_num]
            elements = []
            
            # 检测各种元素
            if self.detect_text and self.text_detector:
                text_elements = self.text_detector.detect(page, page_num)
                elements.extend(text_elements)
            
            if self.detect_images and self.image_detector:
                image_elements = self.image_detector.detect(page, page_num)
                elements.extend(image_elements)
            
            if self.detect_tables and self.table_detector:
                table_elements = self.table_detector.detect(page, page_num)
                elements.extend(table_elements)
            
            if self.detect_formulas and self.formula_detector:
                formula_elements = self.formula_detector.detect(page, page_num)
                elements.extend(formula_elements)
            
            doc.close()
            
            logger.info(f"PDF 页面分析完成: {pdf_path}, 第 {page_num} 页, 检测到 {len(elements)} 个元素")
            return elements
            
        except Exception as e:
            logger.error(f"PDF 页面分析失败: {pdf_path}, 第 {page_num} 页, 错误: {str(e)}")
            raise
