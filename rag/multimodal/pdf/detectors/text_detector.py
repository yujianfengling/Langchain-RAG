"""
文本检测器

检测 PDF 中的文本元素。
"""

import logging
from typing import List

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class TextDetector:
    """
    文本检测器
    
    检测 PDF 中的文本元素，返回文本内容和位置。
    """
    
    def detect(self, page, page_num: int) -> List[PDFElement]:
        """
        检测页面中的文本
        
        Args:
            page: PyMuPDF 页面对象
            page_num: 页码
            
        Returns:
            文本元素列表
        """
        elements = []
        
        try:
            # 获取文本块
            blocks = page.get_text("dict")["blocks"]
            
            for block in blocks:
                if block["type"] == 0:  # 文本块
                    # 合并文本块中的所有文本
                    text_content = ""
                    for line in block["lines"]:
                        for span in line["spans"]:
                            text_content += span["text"]
                    
                    # 如果文本不为空，创建元素
                    if text_content.strip():
                        # 获取边界框
                        bbox = block["bbox"]
                        
                        element = PDFElement(
                            type=ElementType.TEXT,
                            content=text_content.strip(),
                            page=page_num,
                            bbox=bbox,
                            confidence=1.0,
                            metadata={
                                'font_size': self._get_average_font_size(block),
                                'is_bold': self._is_bold(block),
                                'block_type': 'text'
                            }
                        )
                        elements.append(element)
            
            logger.debug(f"第 {page_num} 页检测到 {len(elements)} 个文本元素")
            return elements
            
        except Exception as e:
            logger.error(f"文本检测失败: 第 {page_num} 页, 错误: {str(e)}")
            return []
    
    def _get_average_font_size(self, block: dict) -> float:
        """获取文本块的平均字体大小"""
        font_sizes = []
        for line in block["lines"]:
            for span in line["spans"]:
                font_sizes.append(span["size"])
        return sum(font_sizes) / len(font_sizes) if font_sizes else 0.0
    
    def _is_bold(self, block: dict) -> bool:
        """判断文本块是否为粗体"""
        for line in block["lines"]:
            for span in line["spans"]:
                if "bold" in span["font"].lower():
                    return True
        return False
