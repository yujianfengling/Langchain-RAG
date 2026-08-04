"""
处理器测试
"""

import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.multimodal.pdf.models import PDFElement, ElementType
from rag.multimodal.pdf.processors import VisionProcessor, FormulaProcessor, TableProcessor


class TestVisionProcessor(unittest.TestCase):
    """视觉处理器测试"""
    
    def test_init(self):
        """测试初始化"""
        processor = VisionProcessor()
        self.assertIsNone(processor.vision_provider)
    
    def test_process_element_text(self):
        """测试处理文本元素"""
        processor = VisionProcessor()
        
        element = PDFElement(
            type=ElementType.TEXT,
            content="Hello World",
            page=0,
            bbox=(0, 0, 100, 20)
        )
        
        result = processor.process_element(element, Path("test.pdf"))
        
        self.assertEqual(result['type'], 'text')
        self.assertFalse(result['processed'])
    
    def test_process_element_table(self):
        """测试处理表格元素"""
        processor = VisionProcessor()
        
        element = PDFElement(
            type=ElementType.TABLE,
            content={'data': [['A', 'B'], ['1', '2']], 'rows': 2, 'cols': 2},
            page=0,
            bbox=(0, 0, 200, 100)
        )
        
        result = processor.process_element(element, Path("test.pdf"))
        
        self.assertEqual(result['type'], 'table')
        self.assertTrue(result['processed'])
        self.assertEqual(result['row_count'], 2)


class TestFormulaProcessor(unittest.TestCase):
    """公式处理器测试"""
    
    def test_init(self):
        """测试初始化"""
        processor = FormulaProcessor()
        self.assertIsNotNone(processor)
    
    def test_process_formula(self):
        """测试处理公式"""
        processor = FormulaProcessor()
        
        element = PDFElement(
            type=ElementType.FORMULA,
            content="E = mc^2",
            page=0,
            bbox=(10, 10, 100, 30)
        )
        
        result = processor.process(element)
        
        self.assertIn('original', result)
        self.assertIn('latex', result)
        self.assertEqual(result['original'], 'E = mc^2')
    
    def test_convert_symbols(self):
        """测试符号转换"""
        processor = FormulaProcessor()
        
        latex = processor._convert_to_latex("√2 + ∞")
        self.assertIn('\\sqrt', latex)
        self.assertIn('\\infty', latex)
    
    def test_convert_functions(self):
        """测试函数转换"""
        processor = FormulaProcessor()
        
        latex = processor._convert_to_latex("sin(x) + cos(x)")
        self.assertIn('\\sin', latex)
        self.assertIn('\\cos', latex)
    
    def test_process_batch(self):
        """测试批量处理"""
        processor = FormulaProcessor()
        
        elements = [
            PDFElement(
                type=ElementType.FORMULA,
                content="E = mc^2",
                page=0,
                bbox=(10, 10, 100, 30)
            ),
            PDFElement(
                type=ElementType.FORMULA,
                content="F = ma",
                page=1,
                bbox=(10, 10, 100, 30)
            ),
        ]
        
        results = processor.process_batch(elements)
        
        self.assertEqual(len(results), 2)
        self.assertIn('original', results[0])
        self.assertIn('original', results[1])
    
    def test_validate_latex(self):
        """测试 LaTeX 验证"""
        processor = FormulaProcessor()
        
        # 有效的 LaTeX
        self.assertTrue(processor._validate_latex("E = mc^{2}"))
        self.assertTrue(processor._validate_latex("\\frac{1}{2}"))
        
        # 无效的 LaTeX
        self.assertFalse(processor._validate_latex("\\left( \\right"))
    
    def test_calculate_complexity(self):
        """测试复杂度计算"""
        processor = FormulaProcessor()
        
        # 简单公式
        self.assertEqual(processor._calculate_complexity("E = mc^{2}"), 'low')
        
        # 中等公式
        self.assertEqual(processor._calculate_complexity("\\frac{1}{2} + \\sin(x)"), 'medium')


class TestTableProcessor(unittest.TestCase):
    """表格处理器测试"""
    
    def test_init(self):
        """测试初始化"""
        processor = TableProcessor()
        self.assertIsNotNone(processor)
    
    def test_process_table(self):
        """测试处理表格"""
        processor = TableProcessor()
        
        element = PDFElement(
            type=ElementType.TABLE,
            content={
                'data': [['Name', 'Age'], ['Alice', '30'], ['Bob', '25']],
                'rows': 3,
                'cols': 2,
            },
            page=0,
            bbox=(10, 10, 300, 200)
        )
        
        result = processor.process(element)
        
        self.assertIn('headers', result)
        self.assertIn('rows', result)
        self.assertEqual(result['row_count'], 2)
        self.assertEqual(result['col_count'], 2)
    
    def test_clean_data(self):
        """测试数据清洗"""
        processor = TableProcessor()
        
        data = [
            ['  Name  ', ' Age '],
            ['Alice', '30'],
            ['  Bob  ', '25'],
            ['', ''],  # 空行
        ]
        
        cleaned = processor._clean_data(data)
        
        # 应该删除空行
        self.assertEqual(len(cleaned), 3)
        
        # 应该清理空格
        self.assertEqual(cleaned[0][0], 'Name')
        self.assertEqual(cleaned[2][0], 'Bob')
    
    def test_detect_header(self):
        """测试表头检测"""
        processor = TableProcessor()
        
        # 有表头的情况
        data_with_header = [
            ['Name', 'Age', 'City'],
            ['Alice', '30', 'New York'],
            ['Bob', '25', 'London'],
        ]
        self.assertTrue(processor._detect_header(data_with_header))
        
        # 没有表头的情况
        data_without_header = [
            ['Alice', '30', 'New York'],
            ['Bob', '25', 'London'],
        ]
        # 这个可能检测为无表头
        # 具体取决于算法
    
    def test_convert_cell_type(self):
        """测试单元格类型转换"""
        processor = TableProcessor()
        
        # 数字
        self.assertEqual(processor._convert_cell_type('30'), 30)
        self.assertEqual(processor._convert_cell_type('3.14'), 3.14)
        
        # 布尔值
        self.assertTrue(processor._convert_cell_type('true'))
        self.assertFalse(processor._convert_cell_type('false'))
        
        # 字符串
        self.assertEqual(processor._convert_cell_type('Alice'), 'Alice')
        
        # 空值
        self.assertIsNone(processor._convert_cell_type(''))
        self.assertIsNone(processor._convert_cell_type(None))
    
    def test_to_csv(self):
        """测试导出 CSV"""
        processor = TableProcessor()
        
        table = {
            'headers': ['Name', 'Age'],
            'rows': [{'Name': 'Alice', 'Age': 30}, {'Name': 'Bob', 'Age': 25}],
            'row_count': 2,
            'col_count': 2,
        }
        
        csv_content = processor.to_csv(table)
        
        self.assertIn('Name,Age', csv_content)
        self.assertIn('Alice,30', csv_content)
        self.assertIn('Bob,25', csv_content)
    
    def test_to_markdown(self):
        """测试导出 Markdown"""
        processor = TableProcessor()
        
        table = {
            'headers': ['Name', 'Age'],
            'rows': [{'Name': 'Alice', 'Age': 30}, {'Name': 'Bob', 'Age': 25}],
            'row_count': 2,
            'col_count': 2,
        }
        
        md_content = processor.to_markdown(table)
        
        self.assertIn('| Name | Age |', md_content)
        self.assertIn('| --- | --- |', md_content)
        self.assertIn('| Alice | 30 |', md_content)
    
    def test_process_batch(self):
        """测试批量处理"""
        processor = TableProcessor()
        
        elements = [
            PDFElement(
                type=ElementType.TABLE,
                content={'data': [['A', 'B'], ['1', '2']], 'rows': 2, 'cols': 2},
                page=0,
                bbox=(0, 0, 200, 100)
            ),
            PDFElement(
                type=ElementType.TABLE,
                content={'data': [['X', 'Y'], ['3', '4']], 'rows': 2, 'cols': 2},
                page=1,
                bbox=(0, 0, 200, 100)
            ),
        ]
        
        results = processor.process_batch(elements)
        
        self.assertEqual(len(results), 2)
        self.assertIn('headers', results[0])
        self.assertIn('headers', results[1])


if __name__ == '__main__':
    unittest.main()
