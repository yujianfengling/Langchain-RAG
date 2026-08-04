"""
提取器测试
"""

import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.multimodal.pdf.models import PDFElement, ElementType
from rag.multimodal.pdf.extractors import ImageExtractor, TableExtractor, FormulaExtractor


class TestImageExtractor(unittest.TestCase):
    """图片提取器测试"""
    
    def test_init(self):
        """测试初始化"""
        extractor = ImageExtractor()
        self.assertIsNone(extractor.output_dir)
    
    def test_extract_empty_list(self):
        """测试提取空列表"""
        extractor = ImageExtractor()
        result = extractor.extract(Path("test.pdf"), 0, [])
        self.assertEqual(result, [])
    
    def test_extract_all_empty(self):
        """测试提取所有空列表"""
        extractor = ImageExtractor()
        result = extractor.extract_all(Path("test.pdf"), [])
        self.assertEqual(result, [])


class TestTableExtractor(unittest.TestCase):
    """表格提取器测试"""
    
    def test_init(self):
        """测试初始化"""
        extractor = TableExtractor()
        self.assertIsNotNone(extractor)
    
    def test_extract_empty_list(self):
        """测试提取空列表"""
        extractor = TableExtractor()
        result = extractor.extract([])
        self.assertEqual(result, [])
    
    def test_extract_with_data(self):
        """测试提取有数据的表格"""
        extractor = TableExtractor()
        
        # 创建模拟的表格元素
        table_element = PDFElement(
            type=ElementType.TABLE,
            content={
                'data': [['Name', 'Age', 'City'], ['Alice', '30', 'New York'], ['Bob', '25', 'London']],
                'rows': 3,
                'cols': 3,
            },
            page=0,
            bbox=(10.0, 10.0, 300.0, 200.0),
            confidence=0.9,
            metadata={'has_header': True}
        )
        
        result = extractor.extract([table_element])
        
        self.assertEqual(len(result), 1)
        table = result[0]
        self.assertEqual(table['row_count'], 2)
        self.assertEqual(table['col_count'], 3)
        self.assertTrue(table['has_header'])
    
    def test_to_csv(self):
        """测试导出 CSV"""
        extractor = TableExtractor()
        
        table = {
            'headers': ['Name', 'Age'],
            'rows': [{'Name': 'Alice', 'Age': 30}, {'Name': 'Bob', 'Age': 25}],
            'row_count': 2,
            'col_count': 2,
            'has_header': True,
            'page': 0,
            'bbox': (0, 0, 100, 100),
            'confidence': 1.0,
            'metadata': {}
        }
        
        csv_content = extractor.to_csv(table)
        
        self.assertIn('Name,Age', csv_content)
        self.assertIn('Alice,30', csv_content)
        self.assertIn('Bob,25', csv_content)
    
    def test_to_markdown(self):
        """测试导出 Markdown"""
        extractor = TableExtractor()
        
        table = {
            'headers': ['Name', 'Age'],
            'rows': [{'Name': 'Alice', 'Age': 30}, {'Name': 'Bob', 'Age': 25}],
            'row_count': 2,
            'col_count': 2,
            'has_header': True,
            'page': 0,
            'bbox': (0, 0, 100, 100),
            'confidence': 1.0,
            'metadata': {}
        }
        
        md_content = extractor.to_markdown(table)
        
        self.assertIn('| Name | Age |', md_content)
        self.assertIn('| --- | --- |', md_content)
        self.assertIn('| Alice | 30 |', md_content)
        self.assertIn('| Bob | 25 |', md_content)


class TestFormulaExtractor(unittest.TestCase):
    """公式提取器测试"""
    
    def test_init(self):
        """测试初始化"""
        extractor = FormulaExtractor()
        self.assertIsNotNone(extractor)
    
    def test_extract_empty_list(self):
        """测试提取空列表"""
        extractor = FormulaExtractor()
        result = extractor.extract([])
        self.assertEqual(result, [])
    
    def test_extract_with_data(self):
        """测试提取有数据的公式"""
        extractor = FormulaExtractor()
        
        # 创建模拟的公式元素
        formula_element = PDFElement(
            type=ElementType.FORMULA,
            content="E = mc^2",
            page=0,
            bbox=(10.0, 10.0, 100.0, 30.0),
            confidence=0.8,
            metadata={'formula_type': 'equation'}
        )
        
        result = extractor.extract([formula_element])
        
        self.assertEqual(len(result), 1)
        formula = result[0]
        self.assertEqual(formula['original'], "E = mc^2")
        self.assertIn('latex', formula)
    
    def test_convert_to_latex(self):
        """测试转换为 LaTeX"""
        extractor = FormulaExtractor()
        
        # 测试基本转换
        latex = extractor._convert_to_latex("E = mc^2")
        self.assertIn('E = mc^{2}', latex)
        
        # 测试符号转换
        latex = extractor._convert_to_latex("√2 + ∞")
        self.assertIn('\\sqrt', latex)
        self.assertIn('\\infty', latex)
    
    def test_to_latex_inline(self):
        """测试导出 LaTeX 行内公式"""
        extractor = FormulaExtractor()
        
        formula = {
            'original': 'E = mc^2',
            'latex': 'E = mc^{2}',
            'page': 0,
            'bbox': (0, 0, 100, 30),
            'confidence': 0.8,
            'metadata': {}
        }
        
        latex = extractor.to_latex(formula, inline=True)
        self.assertEqual(latex, '$E = mc^{2}$')
    
    def test_to_latex_display(self):
        """测试导出 LaTeX 块级公式"""
        extractor = FormulaExtractor()
        
        formula = {
            'original': 'E = mc^2',
            'latex': 'E = mc^{2}',
            'page': 0,
            'bbox': (0, 0, 100, 30),
            'confidence': 0.8,
            'metadata': {}
        }
        
        latex = extractor.to_latex(formula, inline=False)
        self.assertEqual(latex, '\\[E = mc^{2}\\]')


class TestPDFElement(unittest.TestCase):
    """PDFElement 测试"""
    
    def test_creation(self):
        """测试创建"""
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
    
    def test_to_dict(self):
        """测试转换为字典"""
        element = PDFElement(
            type=ElementType.IMAGE,
            content={'xref': 1},
            page=1,
            bbox=(0.0, 0.0, 200.0, 200.0),
            confidence=0.9,
            metadata={'image_type': 'png'}
        )
        
        d = element.to_dict()
        
        self.assertEqual(d['type'], 'image')
        self.assertEqual(d['page'], 1)
        self.assertEqual(d['confidence'], 0.9)
    
    def test_from_dict(self):
        """测试从字典创建"""
        d = {
            'type': 'table',
            'content': {'data': []},
            'page': 2,
            'bbox': [10.0, 10.0, 300.0, 200.0],
            'confidence': 0.8,
            'metadata': {'has_header': True}
        }
        
        element = PDFElement.from_dict(d)
        
        self.assertEqual(element.type, ElementType.TABLE)
        self.assertEqual(element.page, 2)
        self.assertEqual(element.confidence, 0.8)


if __name__ == '__main__':
    unittest.main()
