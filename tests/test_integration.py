"""
集成测试

测试 PDF 分析器、提取器、处理器、适配器的完整流程。
"""

import unittest
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.multimodal.pdf import PDFAnalyzer, PDFElement, ElementType
from rag.multimodal.pdf.adapters import DocumentAdapter


class TestIntegration(unittest.TestCase):
    """集成测试"""
    
    def test_full_pipeline(self):
        """测试完整流程：分析 -> 适配"""
        # 创建模拟的 PDF 元素
        elements = [
            PDFElement(
                type=ElementType.TEXT,
                content="这是标题",
                page=0,
                bbox=(10, 10, 200, 30),
                metadata={'font_size': 16, 'is_bold': True}
            ),
            PDFElement(
                type=ElementType.TEXT,
                content="这是正文内容，包含一些重要的信息。",
                page=0,
                bbox=(10, 40, 400, 100),
                metadata={'font_size': 12, 'is_bold': False}
            ),
            PDFElement(
                type=ElementType.IMAGE,
                content={'xref': 1, 'width': 300, 'height': 200},
                page=0,
                bbox=(10, 110, 310, 310),
                metadata={'image_type': 'png'}
            ),
            PDFElement(
                type=ElementType.FORMULA,
                content="E = mc^2",
                page=0,
                bbox=(10, 320, 100, 340),
                metadata={'formula_type': 'equation', 'latex': 'E = mc^{2}'}
            ),
            PDFElement(
                type=ElementType.TABLE,
                content={
                    'data': [['Name', 'Age'], ['Alice', '30'], ['Bob', '25']],
                    'rows': 3,
                    'cols': 2,
                },
                page=0,
                bbox=(10, 350, 400, 450),
                metadata={'has_header': True}
            ),
        ]
        
        # 使用 DocumentAdapter 转换
        adapter = DocumentAdapter()
        pdf_path = Path("test.pdf")
        
        documents = adapter.adapt(elements, pdf_path)
        
        # 验证结果
        self.assertEqual(len(documents), 5)
        
        # 检查每个文档的类型
        types = [doc.metadata['type'] for doc in documents]
        self.assertIn('text', types)
        self.assertIn('image', types)
        self.assertIn('formula', types)
        self.assertIn('table', types)
        
        # 检查文本内容
        text_docs = [doc for doc in documents if doc.metadata['type'] == 'text']
        self.assertEqual(len(text_docs), 2)
        
        # 检查图片描述
        image_docs = [doc for doc in documents if doc.metadata['type'] == 'image']
        self.assertEqual(len(image_docs), 1)
        self.assertIn('[图片]', image_docs[0].page_content)
        
        # 检查公式描述
        formula_docs = [doc for doc in documents if doc.metadata['type'] == 'formula']
        self.assertEqual(len(formula_docs), 1)
        self.assertIn('[公式]', formula_docs[0].page_content)
        self.assertIn('E = mc^2', formula_docs[0].page_content)
        
        # 检查表格描述
        table_docs = [doc for doc in documents if doc.metadata['type'] == 'table']
        self.assertEqual(len(table_docs), 1)
        self.assertIn('[表格]', table_docs[0].page_content)
        self.assertIn('3行x2列', table_docs[0].page_content)
    
    def test_merge_strategies(self):
        """测试不同的合并策略"""
        elements = [
            PDFElement(
                type=ElementType.TEXT,
                content="Page 0 - Text 1",
                page=0,
                bbox=(0, 0, 100, 20)
            ),
            PDFElement(
                type=ElementType.TEXT,
                content="Page 0 - Text 2",
                page=0,
                bbox=(0, 30, 100, 50)
            ),
            PDFElement(
                type=ElementType.TEXT,
                content="Page 1 - Text 1",
                page=1,
                bbox=(0, 0, 100, 20)
            ),
        ]
        
        adapter = DocumentAdapter()
        documents = adapter.adapt(elements)
        
        # 测试按页合并
        merged_by_page = adapter.merge_documents(documents, strategy='group_by_page')
        self.assertEqual(len(merged_by_page), 2)
        
        # 测试按类型合并
        merged_by_type = adapter.merge_documents(documents, strategy='group_by_type')
        self.assertEqual(len(merged_by_type), 1)  # 所有都是文本类型
        
        # 测试连接合并
        merged_concat = adapter.merge_documents(documents, strategy='concatenate')
        self.assertEqual(len(merged_concat), 1)
        self.assertIn('Page 0 - Text 1', merged_concat[0].page_content)
        self.assertIn('Page 1 - Text 1', merged_concat[0].page_content)
    
    def test_element_metadata_preservation(self):
        """测试元数据保留"""
        element = PDFElement(
            type=ElementType.TEXT,
            content="Test content",
            page=2,
            bbox=(10.5, 20.5, 100.5, 30.5),
            confidence=0.95,
            metadata={
                'font_size': 14,
                'is_bold': True,
                'custom_field': 'custom_value'
            }
        )
        
        adapter = DocumentAdapter()
        pdf_path = Path("document.pdf")
        
        docs = adapter.adapt([element], pdf_path)
        
        self.assertEqual(len(docs), 1)
        doc = docs[0]
        
        # 检查元数据
        self.assertEqual(doc.metadata['page'], 2)
        self.assertEqual(doc.metadata['bbox'], [10.5, 20.5, 100.5, 30.5])
        self.assertEqual(doc.metadata['confidence'], 0.95)
        self.assertEqual(doc.metadata['font_size'], 14)
        self.assertTrue(doc.metadata['is_bold'])
        self.assertEqual(doc.metadata['source'], 'document.pdf')
        self.assertEqual(doc.metadata['file_name'], 'document.pdf')
        self.assertEqual(doc.metadata['file_type'], 'pdf')


class TestPDFLoaderIntegration(unittest.TestCase):
    """PDF Loader 集成测试"""
    
    def test_pdf_loader_new_architecture(self):
        """测试 PDF Loader 新架构"""
        from rag.loaders.pdf_loader import PdfLoader
        
        loader = PdfLoader(use_new_architecture=True)
        
        # 验证支持的文件类型
        self.assertTrue(loader.supports("test.pdf"))
        self.assertTrue(loader.supports("document.PDF"))
        self.assertFalse(loader.supports("test.txt"))
        self.assertFalse(loader.supports("test.png"))
        
        # 验证支持的扩展名
        extensions = loader.get_supported_extensions()
        self.assertIn('.pdf', extensions)
    
    def test_pdf_loader_legacy_architecture(self):
        """测试 PDF Loader 旧架构"""
        from rag.loaders.pdf_loader import PdfLoader
        
        loader = PdfLoader(use_new_architecture=False)
        
        # 验证支持的文件类型
        self.assertTrue(loader.supports("test.pdf"))
        
        # 验证支持的扩展名
        extensions = loader.get_supported_extensions()
        self.assertIn('.pdf', extensions)


class TestDocumentRouterIntegration(unittest.TestCase):
    """文档路由器集成测试"""
    
    def test_document_router(self):
        """测试文档路由器"""
        from rag.document import load_document, get_supported_extensions
        
        # 测试支持的扩展名
        extensions = get_supported_extensions()
        self.assertIn('.pdf', extensions)
        self.assertIn('.txt', extensions)
        self.assertIn('.jpg', extensions)
        self.assertIn('.png', extensions)


if __name__ == '__main__':
    unittest.main()
