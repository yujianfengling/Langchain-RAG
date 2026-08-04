"""
公式提取器

从 PDF 中提取公式并转换为 LaTeX 格式。
"""

import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class FormulaExtractor:
    """
    公式提取器
    
    从 PDF 中提取公式并尝试转换为 LaTeX 格式。
    """
    
    # 常见符号映射
    SYMBOL_MAP = {
        '√': '\\sqrt',
        '∑': '\\sum',
        '∫': '\\int',
        '∂': '\\partial',
        '∞': '\\infty',
        '∈': '\\in',
        '∉': '\\notin',
        '⊂': '\\subset',
        '⊃': '\\supset',
        '∪': '\\cup',
        '∩': '\\cap',
        '∀': '\\forall',
        '∃': '\\exists',
        '≤': '\\leq',
        '≥': '\\geq',
        '≠': '\\neq',
        '≈': '\\approx',
        '≡': '\\equiv',
        'α': '\\alpha',
        'β': '\\beta',
        'γ': '\\gamma',
        'δ': '\\delta',
        'ε': '\\epsilon',
        'θ': '\\theta',
        'λ': '\\lambda',
        'μ': '\\mu',
        'π': '\\pi',
        'σ': '\\sigma',
        'φ': '\\phi',
        'ω': '\\omega',
    }
    
    def __init__(self):
        """初始化公式提取器"""
        logger.info("FormulaExtractor 初始化完成")
    
    def extract(
        self,
        formula_elements: List[PDFElement]
    ) -> List[Dict[str, Any]]:
        """
        提取公式
        
        Args:
            formula_elements: 公式元素列表
            
        Returns:
            提取的公式数据列表
        """
        extracted_formulas = []
        
        for formula_element in formula_elements:
            if formula_element.type != ElementType.FORMULA:
                continue
            
            try:
                # 获取公式文本
                formula_text = formula_element.content
                
                if not formula_text:
                    logger.warning("空公式，跳过")
                    continue
                
                # 转换为 LaTeX
                latex_content = self._convert_to_latex(formula_text)
                
                # 构建结构化数据
                structured_formula = {
                    'original': formula_text,
                    'latex': latex_content,
                    'page': formula_element.page,
                    'bbox': formula_element.bbox,
                    'confidence': formula_element.confidence,
                    'metadata': formula_element.metadata,
                }
                
                extracted_formulas.append(structured_formula)
                
            except Exception as e:
                logger.warning(f"提取公式失败: {str(e)}")
                continue
        
        logger.info(f"提取了 {len(extracted_formulas)} 个公式")
        return extracted_formulas
    
    def _convert_to_latex(self, formula_text: str) -> str:
        """
        将公式文本转换为 LaTeX
        
        Args:
            formula_text: 公式文本
            
        Returns:
            LaTeX 格式的公式
        """
        latex = formula_text
        
        # 替换特殊符号
        for symbol, latex_cmd in self.SYMBOL_MAP.items():
            latex = latex.replace(symbol, latex_cmd)
        
        # 处理上下标
        latex = self._process_superscripts(latex)
        latex = self._process_subscripts(latex)
        
        # 处理分数
        latex = self._process_fractions(latex)
        
        # 处理括号
        latex = self._process_parentheses(latex)
        
        return latex
    
    def _process_superscripts(self, text: str) -> str:
        """
        处理上标
        
        Args:
            text: 文本
            
        Returns:
            处理后的文本
        """
        import re
        
        # 匹配 ^ 后面的数字或字母
        pattern = r'\^([0-9a-zA-Z]+)'
        replacement = r'^{\1}'
        
        return re.sub(pattern, replacement, text)
    
    def _process_subscripts(self, text: str) -> str:
        """
        处理下标
        
        Args:
            text: 文本
            
        Returns:
            处理后的文本
        """
        import re
        
        # 匹配 _ 后面的数字或字母
        pattern = r'_([0-9a-zA-Z]+)'
        replacement = r'_{\1}'
        
        return re.sub(pattern, replacement, text)
    
    def _process_fractions(self, text: str) -> str:
        """
        处理分数
        
        Args:
            text: 文本
            
        Returns:
            处理后的文本
        """
        import re
        
        # 匹配 a/b 格式的分数
        pattern = r'([0-9a-zA-Z]+)/([0-9a-zA-Z]+)'
        replacement = r'\\frac{\1}{\2}'
        
        return re.sub(pattern, replacement, text)
    
    def _process_parentheses(self, text: str) -> str:
        """
        处理括号
        
        Args:
            text: 文本
            
        Returns:
            处理后的文本
        """
        # 将普通括号转换为 LaTeX 括号
        text = text.replace('(', '\\left(')
        text = text.replace(')', '\\right)')
        text = text.replace('[', '\\left[')
        text = text.replace(']', '\\right]')
        
        return text
    
    def to_latex(
        self,
        structured_formula: Dict[str, Any],
        inline: bool = True
    ) -> str:
        """
        将公式转换为 LaTeX 格式
        
        Args:
            structured_formula: 结构化公式数据
            inline: 是否为行内公式
            
        Returns:
            LaTeX 格式的公式
        """
        latex = structured_formula['latex']
        
        if inline:
            return f"${latex}$"
        else:
            return f"\\[{latex}\\]"
    
    def to_mathml(self, structured_formula: Dict[str, Any]) -> Optional[str]:
        """
        将公式转换为 MathML 格式
        
        Args:
            structured_formula: 结构化公式数据
            
        Returns:
            MathML 格式的公式
        """
        try:
            # 这里可以集成 MathML 转换库
            # 目前返回简单的 MathML 结构
            latex = structured_formula['latex']
            
            mathml = f"""<math xmlns="http://www.w3.org/1998/Math/MathML">
  <mrow>
    <mi>{latex}</mi>
  </mrow>
</math>"""
            
            return mathml
            
        except Exception as e:
            logger.error(f"MathML 转换失败: {str(e)}")
            return None
