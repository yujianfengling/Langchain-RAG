"""
公式检测器

检测 PDF 中的公式元素。
"""

import logging
from typing import List

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class FormulaDetector:
    """
    公式检测器
    
    检测 PDF 中的公式元素，返回公式位置和内容。
    
    注意：公式检测的准确率可能较低，需要进一步优化。
    """
    
    # 公式特征关键词
    FORMULA_KEYWORDS = [
        '=', '+', '-', '*', '/', '^', '√', '∑', '∫', '∂',
        'sin', 'cos', 'tan', 'log', 'ln', 'exp',
        'lim', 'inf', 'sup', 'max', 'min',
        'α', 'β', 'γ', 'δ', 'ε', 'θ', 'λ', 'μ', 'π', 'σ', 'φ', 'ω',
        '∞', '∈', '∉', '⊂', '⊃', '∪', '∩', '∀', '∃',
        '≤', '≥', '≠', '≈', '≡',
    ]
    
    def detect(self, page, page_num: int) -> List[PDFElement]:
        """
        检测页面中的公式
        
        Args:
            page: PyMuPDF 页面对象
            page_num: 页码
            
        Returns:
            公式元素列表
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
                    
                    # 判断是否为公式
                    if self._is_formula(text_content):
                        bbox = block["bbox"]
                        
                        element = PDFElement(
                            type=ElementType.FORMULA,
                            content=text_content.strip(),
                            page=page_num,
                            bbox=bbox,
                            confidence=self._calculate_confidence(text_content),
                            metadata={
                                'formula_type': self._get_formula_type(text_content),
                                'is_inline': self._is_inline_formula(text_content),
                            }
                        )
                        elements.append(element)
            
            logger.debug(f"第 {page_num} 页检测到 {len(elements)} 个公式元素")
            return elements
            
        except Exception as e:
            logger.error(f"公式检测失败: 第 {page_num} 页, 错误: {str(e)}")
            return []
    
    def _is_formula(self, text: str) -> bool:
        """
        判断文本是否为公式
        
        Args:
            text: 文本内容
            
        Returns:
            是否为公式
        """
        # 检查是否包含公式特征
        for keyword in self.FORMULA_KEYWORDS:
            if keyword in text:
                return True
        
        # 检查是否包含数学符号
        if any(c in text for c in '0123456789'):
            # 检查是否包含运算符
            if any(op in text for op in ['=', '+', '-', '*', '/', '^']):
                return True
        
        return False
    
    def _calculate_confidence(self, text: str) -> float:
        """
        计算公式检测的置信度
        
        Args:
            text: 公式文本
            
        Returns:
            置信度 (0.0-1.0)
        """
        confidence = 0.5  # 基础置信度
        
        # 检查公式特征数量
        feature_count = sum(1 for keyword in self.FORMULA_KEYWORDS if keyword in text)
        confidence += feature_count * 0.1
        
        # 检查是否有数字
        if any(c.isdigit() for c in text):
            confidence += 0.1
        
        # 检查是否有运算符
        if any(op in text for op in ['=', '+', '-', '*', '/', '^']):
            confidence += 0.2
        
        return min(confidence, 1.0)
    
    def _get_formula_type(self, text: str) -> str:
        """
        获取公式类型
        
        Args:
            text: 公式文本
            
        Returns:
            公式类型
        """
        # 简单分类
        if any(keyword in text for keyword in ['∫', '∑', '∂']):
            return 'calculus'
        elif any(keyword in text for keyword in ['sin', 'cos', 'tan']):
            return 'trigonometric'
        elif any(keyword in text for keyword in ['log', 'ln', 'exp']):
            return 'exponential'
        elif any(keyword in text for keyword in ['lim', 'inf', 'sup']):
            return 'limit'
        elif '=' in text:
            return 'equation'
        else:
            return 'expression'
    
    def _is_inline_formula(self, text: str) -> bool:
        """
        判断是否为行内公式
        
        Args:
            text: 公式文本
            
        Returns:
            是否为行内公式
        """
        # 简单判断：如果文本较短，可能是行内公式
        return len(text) < 100
