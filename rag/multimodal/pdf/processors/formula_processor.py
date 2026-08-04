"""
公式处理器

处理和优化公式内容。
"""

import logging
import re
from typing import Dict, Any, List, Optional

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class FormulaProcessor:
    """
    公式处理器
    
    处理和优化公式内容，包括 LaTeX 转换、符号标准化等。
    """
    
    # 数学符号映射
    SYMBOL_MAP = {
        '×': '\\times',
        '÷': '\\div',
        '±': '\\pm',
        '∓': '\\mp',
        '≤': '\\leq',
        '≥': '\\geq',
        '≠': '\\neq',
        '≈': '\\approx',
        '≡': '\\equiv',
        '∞': '\\infty',
        'π': '\\pi',
        'α': '\\alpha',
        'β': '\\beta',
        'γ': '\\gamma',
        'δ': '\\delta',
        'ε': '\\epsilon',
        'θ': '\\theta',
        'λ': '\\lambda',
        'μ': '\\mu',
        'σ': '\\sigma',
        'φ': '\\phi',
        'ω': '\\omega',
        '∑': '\\sum',
        '∏': '\\prod',
        '∫': '\\int',
        '∂': '\\partial',
        '√': '\\sqrt',
        '∇': '\\nabla',
    }
    
    # 函数名称映射
    FUNCTION_MAP = {
        'sin': '\\sin',
        'cos': '\\cos',
        'tan': '\\tan',
        'cot': '\\cot',
        'sec': '\\sec',
        'csc': '\\csc',
        'arcsin': '\\arcsin',
        'arccos': '\\arccos',
        'arctan': '\\arctan',
        'log': '\\log',
        'ln': '\\ln',
        'exp': '\\exp',
        'lim': '\\lim',
        'max': '\\max',
        'min': '\\min',
        'sup': '\\sup',
        'inf': '\\inf',
    }
    
    def __init__(self):
        """初始化公式处理器"""
        logger.info("FormulaProcessor 初始化完成")
    
    def process(
        self,
        formula_element: PDFElement
    ) -> Dict[str, Any]:
        """
        处理公式元素
        
        Args:
            formula_element: 公式元素
            
        Returns:
            处理结果
        """
        if formula_element.type != ElementType.FORMULA:
            return {'error': '不是公式元素'}
        
        original_text = formula_element.content
        
        try:
            # 转换为 LaTeX
            latex = self._convert_to_latex(original_text)
            
            # 验证 LaTeX
            is_valid = self._validate_latex(latex)
            
            # 计算复杂度
            complexity = self._calculate_complexity(latex)
            
            result = {
                'original': original_text,
                'latex': latex,
                'is_valid': is_valid,
                'complexity': complexity,
                'page': formula_element.page,
                'bbox': formula_element.bbox,
                'confidence': formula_element.confidence,
                'metadata': formula_element.metadata,
            }
            
            logger.info(f"公式处理完成: {original_text[:50]}...")
            return result
            
        except Exception as e:
            logger.error(f"公式处理失败: {str(e)}")
            return {
                'original': original_text,
                'error': str(e),
            }
    
    def process_batch(
        self,
        formula_elements: List[PDFElement]
    ) -> List[Dict[str, Any]]:
        """
        批量处理公式
        
        Args:
            formula_elements: 公式元素列表
            
        Returns:
            处理结果列表
        """
        results = []
        
        for element in formula_elements:
            result = self.process(element)
            results.append(result)
        
        logger.info(f"批量处理完成: {len(results)} 个公式")
        return results
    
    def _convert_to_latex(self, text: str) -> str:
        """
        将文本转换为 LaTeX
        
        Args:
            text: 原始文本
            
        Returns:
            LaTeX 格式
        """
        latex = text
        
        # 1. 替换数学符号
        for symbol, latex_cmd in self.SYMBOL_MAP.items():
            latex = latex.replace(symbol, latex_cmd)
        
        # 2. 替换函数名
        for func, latex_func in self.FUNCTION_MAP.items():
            # 只替换独立的函数名
            pattern = r'\b' + func + r'\b'
            # 使用 lambda 避免反斜杠转义问题
            latex = re.sub(pattern, lambda m: latex_func, latex)
        
        # 3. 处理上下标
        latex = self._process_superscripts(latex)
        latex = self._process_subscripts(latex)
        
        # 4. 处理分数
        latex = self._process_fractions(latex)
        
        # 5. 处理括号
        latex = self._process_parentheses(latex)
        
        # 6. 处理特殊结构
        latex = self._process_special_structures(latex)
        
        return latex
    
    def _process_superscripts(self, text: str) -> str:
        """处理上标"""
        # 匹配 ^ 后面的数字或字母
        pattern = r'\^([0-9a-zA-Z]+)'
        replacement = r'^{\1}'
        return re.sub(pattern, replacement, text)
    
    def _process_subscripts(self, text: str) -> str:
        """处理下标"""
        pattern = r'_([0-9a-zA-Z]+)'
        replacement = r'_{\1}'
        return re.sub(pattern, replacement, text)
    
    def _process_fractions(self, text: str) -> str:
        """处理分数"""
        # 匹配 a/b 格式的分数
        pattern = r'([0-9a-zA-Z]+)/([0-9a-zA-Z]+)'
        replacement = r'\\frac{\1}{\2}'
        return re.sub(pattern, replacement, text)
    
    def _process_parentheses(self, text: str) -> str:
        """处理括号"""
        text = text.replace('(', '\\left(')
        text = text.replace(')', '\\right)')
        text = text.replace('[', '\\left[')
        text = text.replace(']', '\\right]')
        return text
    
    def _process_special_structures(self, text: str) -> str:
        """处理特殊结构"""
        # 处理矩阵
        if '...' in text and '|' in text:
            text = self._process_matrix(text)
        
        return text
    
    def _process_matrix(self, text: str) -> str:
        """处理矩阵"""
        # 简单的矩阵处理
        # 这里可以根据需要扩展
        return text
    
    def _validate_latex(self, latex: str) -> bool:
        """
        验证 LaTeX 是否有效
        
        Args:
            latex: LaTeX 字符串
            
        Returns:
            是否有效
        """
        # 检查括号是否匹配
        open_parens = latex.count('(') + latex.count('[') + latex.count('{')
        close_parens = latex.count(')') + latex.count(']') + latex.count('}')
        
        if open_parens != close_parens:
            return False
        
        # 检查是否有未闭合的命令
        if latex.count('\\left') != latex.count('\\right'):
            return False
        
        return True
    
    def _calculate_complexity(self, latex: str) -> str:
        """
        计算公式复杂度
        
        Args:
            latex: LaTeX 字符串
            
        Returns:
            复杂度等级 (low/medium/high)
        """
        # 计算各种元素的数量
        operators = len(re.findall(r'[+\-*/=]', latex))
        functions = len(re.findall(r'\\[a-zA-Z]+', latex))
        subscripts = latex.count('_{')
        superscripts = latex.count('^{')
        fractions = latex.count('\\frac')
        
        # 计算总复杂度分数
        score = operators + functions * 2 + subscripts * 3 + superscripts * 3 + fractions * 5
        
        if score < 5:
            return 'low'
        elif score < 15:
            return 'medium'
        else:
            return 'high'
