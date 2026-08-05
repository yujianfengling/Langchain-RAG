"""
DocumentAdapter metadata 测试

验证所有 Adapter 输出的 LangChain Document 必须包含：
- type 字段
- modality 字段
- 对于 image 类型，必须包含 image_path 字段
"""

import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock

# 添加项目根目录到路径
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.multimodal.pdf.adapters.text_adapter import TextAdapter
from rag.multimodal.pdf.adapters.image_adapter import ImageAdapter
from rag.multimodal.pdf.adapters.formula_adapter import FormulaAdapter
from rag.multimodal.pdf.adapters.table_adapter import TableAdapter
from rag.multimodal.pdf.adapters.document_adapter import DocumentAdapter
from rag.multimodal.pdf.models import PDFElement, ElementType


class TestTextAdapterMetadata(unittest.TestCase):
    """测试 TextAdapter 的 metadata 输出"""
    
    def setUp(self):
        """设置测试环境"""
        self.adapter = TextAdapter()
        self.pdf_path = Path("/test/sample.pdf")
    
    def test_text_metadata_contains_type_and_modality(self):
        """测试文本 metadata 包含 type 和 modality 字段"""
        # 创建文本元素
        element = PDFElement(
            type=ElementType.TEXT,
            content="这是一段测试文本",
            page=0,
            bbox=(0, 0, 100, 20)
        )
        
        # 转换
        doc = self.adapter.adapt(element, self.pdf_path)
        
        # 验证
        self.assertIsNotNone(doc)
        self.assertIn('type', doc.metadata)
        self.assertIn('modality', doc.metadata)
        self.assertEqual(doc.metadata['type'], 'text')
        self.assertEqual(doc.metadata['modality'], 'text')
    
    def test_text_metadata_preserves_existing_fields(self):
        """测试文本 metadata 保留现有字段"""
        element = PDFElement(
            type=ElementType.TEXT,
            content="测试文本",
            page=1,
            bbox=(10, 20, 30, 40),
            confidence=0.95,
            metadata={'font_size': 12, 'is_bold': True}
        )
        
        doc = self.adapter.adapt(element, self.pdf_path)
        
        # 验证现有字段
        self.assertEqual(doc.metadata['page'], 1)
        self.assertEqual(doc.metadata['bbox'], [10, 20, 30, 40])
        self.assertEqual(doc.metadata['confidence'], 0.95)
        self.assertEqual(doc.metadata['font_size'], 12)
        self.assertTrue(doc.metadata['is_bold'])
        
        # 验证新增字段
        self.assertEqual(doc.metadata['type'], 'text')
        self.assertEqual(doc.metadata['modality'], 'text')
    
    def test_text_metadata_without_pdf_path(self):
        """测试没有 pdf_path 时的 metadata"""
        element = PDFElement(
            type=ElementType.TEXT,
            content="测试文本",
            page=0,
            bbox=(0, 0, 100, 20)
        )
        
        doc = self.adapter.adapt(element, None)
        
        # 验证 type 和 modality 存在
        self.assertEqual(doc.metadata['type'], 'text')
        self.assertEqual(doc.metadata['modality'], 'text')
        
        # 验证没有文件信息
        self.assertNotIn('source', doc.metadata)
        self.assertNotIn('file_name', doc.metadata)
        self.assertNotIn('file_type', doc.metadata)


class TestImageAdapterMetadata(unittest.TestCase):
    """测试 ImageAdapter 的 metadata 输出"""
    
    def setUp(self):
        """设置测试环境"""
        self.adapter = ImageAdapter()
        self.pdf_path = Path("/test/sample.pdf")
    
    def test_image_metadata_contains_type_and_modality(self):
        """测试图片 metadata 包含 type 和 modality 字段"""
        element = PDFElement(
            type=ElementType.IMAGE,
            content={'width': 100, 'height': 80, 'xref': 123},
            page=0,
            bbox=(10, 10, 110, 90)
        )
        
        doc = self.adapter.adapt(element, self.pdf_path)
        
        # 验证
        self.assertIsNotNone(doc)
        self.assertIn('type', doc.metadata)
        self.assertIn('modality', doc.metadata)
        self.assertEqual(doc.metadata['type'], 'image')
        self.assertEqual(doc.metadata['modality'], 'image')
    
    def test_image_metadata_contains_image_path(self):
        """测试图片 metadata 包含 image_path 字段"""
        element = PDFElement(
            type=ElementType.IMAGE,
            content={
                'width': 100,
                'height': 80,
                'xref': 123,
                'image_path': '/path/to/image.png'
            },
            page=0,
            bbox=(10, 10, 110, 90)
        )
        
        doc = self.adapter.adapt(element, self.pdf_path)
        
        # 验证 image_path 存在
        self.assertIn('image_path', doc.metadata)
        self.assertEqual(doc.metadata['image_path'], '/path/to/image.png')
    
    def test_image_metadata_without_image_path(self):
        """测试没有 image_path 时的 metadata"""
        element = PDFElement(
            type=ElementType.IMAGE,
            content={'width': 100, 'height': 80, 'xref': 123},
            page=0,
            bbox=(10, 10, 110, 90)
        )
        
        doc = self.adapter.adapt(element, self.pdf_path)
        
        # 验证 type 和 modality 存在
        self.assertEqual(doc.metadata['type'], 'image')
        self.assertEqual(doc.metadata['modality'], 'image')
        
        # 验证 image_path 不存在
        self.assertNotIn('image_path', doc.metadata)
    
    def test_image_metadata_preserves_existing_fields(self):
        """测试图片 metadata 保留现有字段"""
        element = PDFElement(
            type=ElementType.IMAGE,
            content={
                'width': 200,
                'height': 150,
                'xref': 456,
                'image_path': '/test/image.jpg'
            },
            page=2,
            bbox=(20, 30, 220, 180),
            confidence=0.85,
            metadata={'image_type': 'jpeg', 'size': 1024}
        )
        
        doc = self.adapter.adapt(element, self.pdf_path)
        
        # 验证现有字段
        self.assertEqual(doc.metadata['page'], 2)
        self.assertEqual(doc.metadata['bbox'], [20, 30, 220, 180])
        self.assertEqual(doc.metadata['confidence'], 0.85)
        self.assertEqual(doc.metadata['image_width'], 200)
        self.assertEqual(doc.metadata['image_height'], 150)
        self.assertEqual(doc.metadata['image_xref'], 456)
        self.assertEqual(doc.metadata['image_type'], 'jpeg')
        self.assertEqual(doc.metadata['image_size'], 1024)
        
        # 验证新增字段
        self.assertEqual(doc.metadata['type'], 'image')
        self.assertEqual(doc.metadata['modality'], 'image')
        self.assertEqual(doc.metadata['image_path'], '/test/image.jpg')


class TestFormulaAdapterMetadata(unittest.TestCase):
    """测试 FormulaAdapter 的 metadata 输出"""
    
    def setUp(self):
        """设置测试环境"""
        self.adapter = FormulaAdapter()
        self.pdf_path = Path("/test/sample.pdf")
    
    def test_formula_metadata_contains_type_and_modality(self):
        """测试公式 metadata 包含 type 和 modality 字段"""
        element = PDFElement(
            type=ElementType.FORMULA,
            content="E=mc^2",
            page=0,
            bbox=(10, 10, 100, 30)
        )
        
        doc = self.adapter.adapt(element, self.pdf_path)
        
        # 验证
        self.assertIsNotNone(doc)
        self.assertIn('type', doc.metadata)
        self.assertIn('modality', doc.metadata)
        self.assertEqual(doc.metadata['type'], 'formula')
        self.assertEqual(doc.metadata['modality'], 'formula')
    
    def test_formula_metadata_preserves_existing_fields(self):
        """测试公式 metadata 保留现有字段"""
        element = PDFElement(
            type=ElementType.FORMULA,
            content="F=ma",
            page=1,
            bbox=(15, 25, 115, 45),
            confidence=0.9,
            metadata={
                'formula_type': 'physics',
                'is_inline': False,
                'latex': 'F=ma'
            }
        )
        
        doc = self.adapter.adapt(element, self.pdf_path)
        
        # 验证现有字段
        self.assertEqual(doc.metadata['page'], 1)
        self.assertEqual(doc.metadata['bbox'], [15, 25, 115, 45])
        self.assertEqual(doc.metadata['confidence'], 0.9)
        self.assertEqual(doc.metadata['formula_type'], 'physics')
        self.assertFalse(doc.metadata['is_inline'])
        self.assertEqual(doc.metadata['latex'], 'F=ma')
        
        # 验证新增字段
        self.assertEqual(doc.metadata['type'], 'formula')
        self.assertEqual(doc.metadata['modality'], 'formula')


class TestTableAdapterMetadata(unittest.TestCase):
    """测试 TableAdapter 的 metadata 输出"""
    
    def setUp(self):
        """设置测试环境"""
        self.adapter = TableAdapter()
        self.pdf_path = Path("/test/sample.pdf")
    
    def test_table_metadata_contains_type_and_modality(self):
        """测试表格 metadata 包含 type 和 modality 字段"""
        element = PDFElement(
            type=ElementType.TABLE,
            content={
                'rows': 2,
                'cols': 3,
                'data': [['A', 'B', 'C'], ['1', '2', '3']]
            },
            page=0,
            bbox=(10, 10, 200, 100)
        )
        
        doc = self.adapter.adapt(element, self.pdf_path)
        
        # 验证
        self.assertIsNotNone(doc)
        self.assertIn('type', doc.metadata)
        self.assertIn('modality', doc.metadata)
        self.assertEqual(doc.metadata['type'], 'table')
        self.assertEqual(doc.metadata['modality'], 'table')
    
    def test_table_metadata_preserves_existing_fields(self):
        """测试表格 metadata 保留现有字段"""
        element = PDFElement(
            type=ElementType.TABLE,
            content={
                'rows': 3,
                'cols': 4,
                'data': [['Header1', 'Header2', 'Header3', 'Header4']]
            },
            page=2,
            bbox=(20, 30, 220, 130),
            confidence=0.88,
            metadata={'has_header': True, 'is_empty': False}
        )
        
        doc = self.adapter.adapt(element, self.pdf_path)
        
        # 验证现有字段
        self.assertEqual(doc.metadata['page'], 2)
        self.assertEqual(doc.metadata['bbox'], [20, 30, 220, 130])
        self.assertEqual(doc.metadata['confidence'], 0.88)
        self.assertEqual(doc.metadata['table_rows'], 3)
        self.assertEqual(doc.metadata['table_cols'], 4)
        self.assertTrue(doc.metadata['has_header'])
        self.assertFalse(doc.metadata['is_empty'])
        
        # 验证新增字段
        self.assertEqual(doc.metadata['type'], 'table')
        self.assertEqual(doc.metadata['modality'], 'table')


class TestDocumentAdapterMetadata(unittest.TestCase):
    """测试 DocumentAdapter 的 metadata 输出"""
    
    def setUp(self):
        """设置测试环境"""
        self.adapter = DocumentAdapter()
        self.pdf_path = Path("/test/sample.pdf")
    
    def test_document_adapter_produces_correct_metadata(self):
        """测试 DocumentAdapter 产生正确的 metadata"""
        # 创建各种类型的元素
        elements = [
            PDFElement(
                type=ElementType.TEXT,
                content="文本内容",
                page=0,
                bbox=(0, 0, 100, 20)
            ),
            PDFElement(
                type=ElementType.IMAGE,
                content={
                    'width': 100,
                    'height': 80,
                    'xref': 123,
                    'image_path': '/test/image.png'
                },
                page=0,
                bbox=(10, 30, 110, 110)
            ),
            PDFElement(
                type=ElementType.FORMULA,
                content="E=mc^2",
                page=1,
                bbox=(10, 10, 100, 30)
            ),
            PDFElement(
                type=ElementType.TABLE,
                content={
                    'rows': 2,
                    'cols': 2,
                    'data': [['A', 'B'], ['1', '2']]
                },
                page=1,
                bbox=(10, 40, 200, 100)
            )
        ]
        
        # 转换
        docs = self.adapter.adapt(elements, self.pdf_path)
        
        # 验证每个文档的 metadata
        self.assertEqual(len(docs), 4)
        
        # 文本文档
        text_doc = docs[0]
        self.assertEqual(text_doc.metadata['type'], 'text')
        self.assertEqual(text_doc.metadata['modality'], 'text')
        
        # 图片文档
        image_doc = docs[1]
        self.assertEqual(image_doc.metadata['type'], 'image')
        self.assertEqual(image_doc.metadata['modality'], 'image')
        self.assertIn('image_path', image_doc.metadata)
        self.assertEqual(image_doc.metadata['image_path'], '/test/image.png')
        
        # 公式文档
        formula_doc = docs[2]
        self.assertEqual(formula_doc.metadata['type'], 'formula')
        self.assertEqual(formula_doc.metadata['modality'], 'formula')
        
        # 表格文档
        table_doc = docs[3]
        self.assertEqual(table_doc.metadata['type'], 'table')
        self.assertEqual(table_doc.metadata['modality'], 'table')
    
    def test_document_adapter_batch_produces_correct_metadata(self):
        """测试 DocumentAdapter 批量转换产生正确的 metadata"""
        # 创建两组元素
        element_groups = [
            [
                PDFElement(
                    type=ElementType.TEXT,
                    content="第一组文本",
                    page=0,
                    bbox=(0, 0, 100, 20)
                )
            ],
            [
                PDFElement(
                    type=ElementType.IMAGE,
                    content={
                        'width': 50,
                        'height': 40,
                        'xref': 789,
                        'image_path': '/test/batch_image.jpg'
                    },
                    page=1,
                    bbox=(10, 10, 60, 50)
                )
            ]
        ]
        
        pdf_paths = [
            Path("/test/doc1.pdf"),
            Path("/test/doc2.pdf")
        ]
        
        # 批量转换
        docs = self.adapter.adapt_batch(element_groups, pdf_paths)
        
        # 验证
        self.assertEqual(len(docs), 2)
        
        # 第一个文档
        self.assertEqual(docs[0].metadata['type'], 'text')
        self.assertEqual(docs[0].metadata['modality'], 'text')
        # 注意：Windows 路径会被转换，使用 Path 对象的 as_posix() 方法比较
        expected_source1 = Path("/test/doc1.pdf").as_posix()
        actual_source1 = Path(docs[0].metadata['source']).as_posix()
        self.assertEqual(actual_source1, expected_source1)
        
        # 第二个文档
        self.assertEqual(docs[1].metadata['type'], 'image')
        self.assertEqual(docs[1].metadata['modality'], 'image')
        self.assertIn('image_path', docs[1].metadata)
        self.assertEqual(docs[1].metadata['image_path'], '/test/batch_image.jpg')
        expected_source2 = Path("/test/doc2.pdf").as_posix()
        actual_source2 = Path(docs[1].metadata['source']).as_posix()
        self.assertEqual(actual_source2, expected_source2)


class TestMetadataConsistency(unittest.TestCase):
    """测试 metadata 一致性"""
    
    def test_all_adapters_produce_consistent_metadata_structure(self):
        """测试所有 adapter 产生一致的 metadata 结构"""
        pdf_path = Path("/test/sample.pdf")
        
        # 创建各种类型的元素
        elements = [
            PDFElement(
                type=ElementType.TEXT,
                content="文本",
                page=0,
                bbox=(0, 0, 100, 20)
            ),
            PDFElement(
                type=ElementType.IMAGE,
                content={'width': 100, 'height': 80, 'xref': 123},
                page=0,
                bbox=(10, 30, 110, 110)
            ),
            PDFElement(
                type=ElementType.FORMULA,
                content="公式",
                page=1,
                bbox=(10, 10, 100, 30)
            ),
            PDFElement(
                type=ElementType.TABLE,
                content={'rows': 2, 'cols': 2, 'data': []},
                page=1,
                bbox=(10, 40, 200, 100)
            )
        ]
        
        # 使用各个 adapter 转换
        adapters = [
            TextAdapter(),
            ImageAdapter(),
            FormulaAdapter(),
            TableAdapter()
        ]
        
        for i, adapter in enumerate(adapters):
            doc = adapter.adapt(elements[i], pdf_path)
            
            # 验证所有 adapter 都有 type 和 modality
            self.assertIn('type', doc.metadata)
            self.assertIn('modality', doc.metadata)
            
            # 验证 type 和 modality 一致
            self.assertEqual(doc.metadata['type'], doc.metadata['modality'])
            
            # 验证有 page 和 bbox
            self.assertIn('page', doc.metadata)
            self.assertIn('bbox', doc.metadata)
            
            # 验证有文件信息（因为提供了 pdf_path）
            self.assertIn('source', doc.metadata)
            self.assertIn('file_name', doc.metadata)
            self.assertIn('file_type', doc.metadata)


if __name__ == '__main__':
    unittest.main()