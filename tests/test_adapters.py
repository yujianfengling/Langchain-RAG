"""
适配器测试
"""

import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_core.documents import Document
from rag.multimodal.pdf.models import PDFElement, ElementType
from rag.multimodal.pdf.adapters import DocumentAdapter, TextAdapter, ImageAdapter, FormulaAdapter, TableAdapter


class TestTextAdapter(unittest.TestCase):
    """文本适配器测试"""
    
    def test_init(self):
        """测试初始化"""
        adapter = TextAdapter()
        self.assertIsNotNone(adapter)
    
    def test_adapt_text(self):
        """测试转换文本元素"""
        adapter = TextAdapter()
        
        element = PDFElement(
            type=ElementType.TEXT,
            content="Hello World",
            page=0,
            bbox=(10.0, 20.0, 100.0, 30.0)
        )
        
        doc = adapter.adapt(element)
        
        self.assertIsNotNone(doc)
        self.assertEqual(doc.page_content, "Hello World")
        self.assertEqual(doc.metadata['type'], 'text')
        self.assertEqual(doc.metadata['page'], 0)
    
    def test_adapt_text_with_pdf_path(self):
        """测试带PDF路径的文本转换"""
        adapter = TextAdapter()
        
        element = PDFElement(
            type=ElementType.TEXT,
            content="Test content",
            page=1,
            bbox=(0, 0, 100, 20)
        )
        
        pdf_path = Path("test.pdf")
        doc = adapter.adapt(element, pdf_path)
        
        self.assertIsNotNone(doc)
        self.assertEqual(doc.metadata['source'], 'test.pdf')
        self.assertEqual(doc.metadata['file_name'], 'test.pdf')
        self.assertEqual(doc.metadata['file_type'], 'pdf')
    
    def test_adapt_empty_text(self):
        """测试转换空文本"""
        adapter = TextAdapter()
        
        element = PDFElement(
            type=ElementType.TEXT,
            content="",
            page=0,
            bbox=(0, 0, 100, 20)
        )
        
        doc = adapter.adapt(element)
        
        self.assertIsNone(doc)
    
    def test_adapt_wrong_type(self):
        """测试错误类型"""
        adapter = TextAdapter()
        
        element = PDFElement(
            type=ElementType.IMAGE,
            content={'xref': 1},
            page=0,
            bbox=(0, 0, 100, 100)
        )
        
        doc = adapter.adapt(element)
        
        self.assertIsNone(doc)


class TestImageAdapter(unittest.TestCase):
    """图片适配器测试"""
    
    def test_init(self):
        """测试初始化"""
        adapter = ImageAdapter()
        self.assertIsNotNone(adapter)
    
    def test_adapt_image(self):
        """测试转换图片元素"""
        adapter = ImageAdapter()
        
        element = PDFElement(
            type=ElementType.IMAGE,
            content={'xref': 1, 'width': 100, 'height': 100},
            page=0,
            bbox=(10, 10, 110, 110)
        )
        
        doc = adapter.adapt(element)
        
        self.assertIsNotNone(doc)
        self.assertIn('[图片]', doc.page_content)
        self.assertEqual(doc.metadata['type'], 'image')
        self.assertEqual(doc.metadata['image_width'], 100)
        self.assertEqual(doc.metadata['image_height'], 100)
    
    def test_adapt_image_with_description(self):
        """测试带描述的图片转换"""
        adapter = ImageAdapter()
        
        element = PDFElement(
            type=ElementType.IMAGE,
            content={'xref': 1, 'width': 200, 'height': 150},
            page=1,
            bbox=(0, 0, 200, 150),
            metadata={'description': 'A beautiful landscape'}
        )
        
        doc = adapter.adapt(element)
        
        self.assertIsNotNone(doc)
        self.assertIn('A beautiful landscape', doc.page_content)


class TestFormulaAdapter(unittest.TestCase):
    """公式适配器测试"""
    
    def test_init(self):
        """测试初始化"""
        adapter = FormulaAdapter()
        self.assertIsNotNone(adapter)
    
    def test_adapt_formula(self):
        """测试转换公式元素"""
        adapter = FormulaAdapter()
        
        element = PDFElement(
            type=ElementType.FORMULA,
            content="E = mc^2",
            page=0,
            bbox=(10, 10, 100, 30),
            metadata={'formula_type': 'equation'}
        )
        
        doc = adapter.adapt(element)
        
        self.assertIsNotNone(doc)
        self.assertIn('[公式]', doc.page_content)
        self.assertIn('E = mc^2', doc.page_content)
        self.assertEqual(doc.metadata['type'], 'formula')
        self.assertEqual(doc.metadata['formula_type'], 'equation')
    
    def test_adapt_formula_with_latex(self):
        """测试带LaTeX的公式转换"""
        adapter = FormulaAdapter()
        
        element = PDFElement(
            type=ElementType.FORMULA,
            content="E = mc^2",
            page=0,
            bbox=(10, 10, 100, 30),
            metadata={'latex': 'E = mc^{2}'}
        )
        
        doc = adapter.adapt(element)
        
        self.assertIsNotNone(doc)
        self.assertIn('LaTeX: E = mc^{2}', doc.page_content)


class TestTableAdapter(unittest.TestCase):
    """表格适配器测试"""
    
    def test_init(self):
        """测试初始化"""
        adapter = TableAdapter()
        self.assertIsNotNone(adapter)
    
    def test_adapt_table(self):
        """测试转换表格元素"""
        adapter = TableAdapter()
        
        element = PDFElement(
            type=ElementType.TABLE,
            content={
                'data': [['Name', 'Age'], ['Alice', '30'], ['Bob', '25']],
                'rows': 3,
                'cols': 2,
            },
            page=0,
            bbox=(10, 10, 300, 200),
            metadata={'has_header': True}
        )
        
        doc = adapter.adapt(element)
        
        self.assertIsNotNone(doc)
        self.assertIn('[表格]', doc.page_content)
        self.assertIn('3行x2列', doc.page_content)
        self.assertEqual(doc.metadata['type'], 'table')
        self.assertEqual(doc.metadata['table_rows'], 3)
        self.assertEqual(doc.metadata['table_cols'], 2)
        self.assertTrue(doc.metadata['has_header'])


class TestDocumentAdapter(unittest.TestCase):
    """文档适配器测试"""
    
    def test_init(self):
        """测试初始化"""
        adapter = DocumentAdapter()
        self.assertIsNotNone(adapter)
        self.assertIsNotNone(adapter.text_adapter)
        self.assertIsNotNone(adapter.image_adapter)
        self.assertIsNotNone(adapter.formula_adapter)
        self.assertIsNotNone(adapter.table_adapter)
    
    def test_adapt_mixed_elements(self):
        """测试转换混合元素"""
        adapter = DocumentAdapter()
        
        elements = [
            PDFElement(
                type=ElementType.TEXT,
                content="Title",
                page=0,
                bbox=(10, 10, 100, 30)
            ),
            PDFElement(
                type=ElementType.IMAGE,
                content={'xref': 1, 'width': 100, 'height': 100},
                page=0,
                bbox=(10, 40, 110, 140)
            ),
            PDFElement(
                type=ElementType.FORMULA,
                content="E = mc^2",
                page=0,
                bbox=(10, 150, 100, 170)
            ),
            PDFElement(
                type=ElementType.TABLE,
                content={'data': [['A', 'B'], ['1', '2']], 'rows': 2, 'cols': 2},
                page=0,
                bbox=(10, 180, 300, 250)
            ),
        ]
        
        docs = adapter.adapt(elements)
        
        self.assertEqual(len(docs), 4)
        
        # 检查类型
        types = [doc.metadata['type'] for doc in docs]
        self.assertIn('text', types)
        self.assertIn('image', types)
        self.assertIn('formula', types)
        self.assertIn('table', types)
    
    def test_adapt_empty_list(self):
        """测试转换空列表"""
        adapter = DocumentAdapter()
        
        docs = adapter.adapt([])
        
        self.assertEqual(len(docs), 0)
    
    def test_merge_documents_concatenate(self):
        """测试合并文档（连接）"""
        adapter = DocumentAdapter()
        
        docs = [
            Document(page_content="Doc 1", metadata={'type': 'text', 'page': 0}),
            Document(page_content="Doc 2", metadata={'type': 'text', 'page': 1}),
        ]
        
        merged = adapter.merge_documents(docs, strategy='concatenate')
        
        self.assertEqual(len(merged), 1)
        self.assertIn('Doc 1', merged[0].page_content)
        self.assertIn('Doc 2', merged[0].page_content)
    
    def test_merge_documents_group_by_page(self):
        """测试合并文档（按页分组）"""
        adapter = DocumentAdapter()
        
        docs = [
            Document(page_content="Page 0 - Doc 1", metadata={'type': 'text', 'page': 0, 'source': 'test.pdf'}),
            Document(page_content="Page 0 - Doc 2", metadata={'type': 'text', 'page': 0, 'source': 'test.pdf'}),
            Document(page_content="Page 1 - Doc 1", metadata={'type': 'text', 'page': 1, 'source': 'test.pdf'}),
        ]
        
        merged = adapter.merge_documents(docs, strategy='group_by_page')
        
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].metadata['page'], 0)
        self.assertEqual(merged[1].metadata['page'], 1)
    
    def test_merge_documents_group_by_type(self):
        """测试合并文档（按类型分组）"""
        adapter = DocumentAdapter()
        
        docs = [
            Document(page_content="Text 1", metadata={'type': 'text', 'source': 'test.pdf'}),
            Document(page_content="Text 2", metadata={'type': 'text', 'source': 'test.pdf'}),
            Document(page_content="Image 1", metadata={'type': 'image', 'source': 'test.pdf'}),
        ]
        
        merged = adapter.merge_documents(docs, strategy='group_by_type')
        
        self.assertEqual(len(merged), 2)
        
        types = [doc.metadata['type'] for doc in merged]
        self.assertIn('merged_text', types)
        self.assertIn('merged_image', types)


if __name__ == '__main__':
    unittest.main()
