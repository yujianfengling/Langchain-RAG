"""
PDF 分析器测试
"""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.multimodal.pdf import PDFAnalyzer, PDFElement, ElementType


class TestPDFAnalyzer(unittest.TestCase):
    """PDF 分析器测试"""
    
    def test_init_default(self):
        """测试默认初始化"""
        analyzer = PDFAnalyzer()
        
        self.assertTrue(analyzer.detect_text)
        self.assertTrue(analyzer.detect_images)
        self.assertTrue(analyzer.detect_tables)
        self.assertTrue(analyzer.detect_formulas)
    
    def test_init_custom(self):
        """测试自定义初始化"""
        analyzer = PDFAnalyzer(
            detect_text=True,
            detect_images=False,
            detect_tables=True,
            detect_formulas=False
        )
        
        self.assertTrue(analyzer.detect_text)
        self.assertFalse(analyzer.detect_images)
        self.assertTrue(analyzer.detect_tables)
        self.assertFalse(analyzer.detect_formulas)
    
    def test_analyze_nonexistent_file(self):
        """测试分析不存在的文件"""
        analyzer = PDFAnalyzer()
        
        with self.assertRaises(FileNotFoundError):
            analyzer.analyze("nonexistent.pdf")
    
    def test_analyze_non_pdf_file(self):
        """测试分析非 PDF 文件"""
        analyzer = PDFAnalyzer()
        
        # 创建临时文件
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            f.write(b"test content")
            temp_path = Path(f.name)
        
        try:
            with self.assertRaises(ValueError):
                analyzer.analyze(temp_path)
        finally:
            temp_path.unlink()
    
    def test_pdf_element_creation(self):
        """测试 PDFElement 创建"""
        element = PDFElement(
            type=ElementType.TEXT,
            content="Hello World",
            page=0,
            bbox=(10.0, 20.0, 100.0, 30.0)
        )
        
        self.assertEqual(element.type, ElementType.TEXT)
        self.assertEqual(element.content, "Hello World")
        self.assertEqual(element.page, 0)
        self.assertEqual(element.bbox, (10.0, 20.0, 100.0, 30.0))
        self.assertEqual(element.confidence, 1.0)
        self.assertEqual(element.metadata, {})
    
    def test_pdf_element_to_dict(self):
        """测试 PDFElement 转换为字典"""
        element = PDFElement(
            type=ElementType.IMAGE,
            content={'xref': 1, 'width': 100, 'height': 100},
            page=1,
            bbox=(0.0, 0.0, 200.0, 200.0),
            confidence=0.9,
            metadata={'image_type': 'png'}
        )
        
        expected = {
            'type': 'image',
            'content': {'xref': 1, 'width': 100, 'height': 100},
            'page': 1,
            'bbox': [0.0, 0.0, 200.0, 200.0],
            'confidence': 0.9,
            'metadata': {'image_type': 'png'}
        }
        
        self.assertEqual(element.to_dict(), expected)
    
    def test_pdf_element_from_dict(self):
        """测试从字典创建 PDFElement"""
        data = {
            'type': 'table',
            'content': {'data': [['A', 'B'], ['1', '2']], 'rows': 2, 'cols': 2},
            'page': 2,
            'bbox': [10.0, 10.0, 300.0, 200.0],
            'confidence': 0.8,
            'metadata': {'has_header': True}
        }
        
        element = PDFElement.from_dict(data)
        
        self.assertEqual(element.type, ElementType.TABLE)
        self.assertEqual(element.page, 2)
        self.assertEqual(element.confidence, 0.8)
    
    @patch('rag.multimodal.pdf.analyzer.PDFAnalyzer._check_pymupdf')
    def test_pymupdf_not_available(self, mock_check):
        """测试 PyMuPDF 不可用的情况"""
        mock_check.return_value = False
        
        analyzer = PDFAnalyzer()
        
        # 创建临时 PDF 文件
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
            f.write(b"%PDF-1.4 test content")
            temp_path = Path(f.name)
        
        try:
            # 应该返回空列表，而不是抛出异常
            result = analyzer.analyze(temp_path)
            self.assertEqual(result, [])
        finally:
            temp_path.unlink()


class TestElementType(unittest.TestCase):
    """元素类型测试"""
    
    def test_element_types(self):
        """测试元素类型值"""
        self.assertEqual(ElementType.TEXT.value, "text")
        self.assertEqual(ElementType.IMAGE.value, "image")
        self.assertEqual(ElementType.TABLE.value, "table")
        self.assertEqual(ElementType.FORMULA.value, "formula")


if __name__ == '__main__':
    unittest.main()
