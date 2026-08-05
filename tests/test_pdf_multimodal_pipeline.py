"""
PDF多模态管道集成测试
测试PdfLoader集成MultimodalEnricher的完整流程
"""

import unittest
from unittest.mock import Mock, MagicMock, patch, PropertyMock
from pathlib import Path
import tempfile
import shutil

# 添加项目根目录到路径
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.loaders.pdf_loader import PdfLoader
from rag.multimodal.pdf.models import PDFElement, ElementType
from langchain_core.documents import Document


class TestPdfMultimodalPipeline(unittest.TestCase):
    """测试PDF多模态管道集成"""
    
    def setUp(self):
        """设置测试环境"""
        # 创建临时目录
        self.test_dir = tempfile.mkdtemp()
        self.pdf_file = Path(self.test_dir) / "test.pdf"
        self.pdf_file.touch()
        
        # 创建PdfLoader实例
        self.loader = PdfLoader(use_new_architecture=True)
        
        # 创建测试元素
        self.text_element = PDFElement(
            type=ElementType.TEXT,
            content="这是一段文本内容",
            page=1,
            bbox=(0, 0, 100, 100),
            confidence=1.0
        )
        
        self.image_element = PDFElement(
            type=ElementType.IMAGE,
            content={
                'xref': 1,
                'width': 100,
                'height': 100,
                'colorspace': 3,
                'bpc': 8,
                'image_path': '/path/to/image.png'
            },
            page=1,
            bbox=(0, 0, 100, 100),
            confidence=1.0
        )
        
        self.table_element = PDFElement(
            type=ElementType.TABLE,
            content={"headers": ["A", "B"], "rows": [["1", "2"]]},
            page=1,
            bbox=(0, 0, 100, 100),
            confidence=1.0
        )
    
    def tearDown(self):
        """清理测试环境"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_full_pipeline_with_multimodal_enricher(self):
        """测试PDF → Document完整链路，包含MultimodalEnricher"""
        # 准备元素列表
        elements = [self.text_element, self.image_element, self.table_element]
        
        # 模拟PDFAnalyzer
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = elements
        self.loader._analyzer = mock_analyzer
        
        # 模拟ImageExtractor
        mock_extractor = Mock()
        mock_extractor.extract_all.return_value = [Path('/path/to/image.png')]
        self.loader._image_extractor = mock_extractor
        
        # 模拟DocumentAdapter
        text_doc = Document(page_content="文本内容", metadata={"source": "text"})
        image_doc = Document(page_content="[图片]", metadata={"image_path": "/path/to/image.png"})
        table_doc = Document(page_content="表格内容", metadata={"source": "table"})
        mock_adapter = Mock()
        mock_adapter.adapt.return_value = [text_doc, image_doc, table_doc]
        self.loader._adapter = mock_adapter
        
        # 模拟MultimodalEnricher
        mock_enricher = Mock()
        enriched_text_doc = Document(page_content="文本内容", metadata={"source": "text", "modality": "text"})
        enriched_image_doc = Document(page_content="图片描述...", metadata={"image_path": "/path/to/image.png", "modality": "image", "description": "描述"})
        enriched_table_doc = Document(page_content="表格内容:\n| A | B |\n| --- | --- |\n| 1 | 2 |", metadata={"source": "table", "modality": "table"})
        mock_enricher.enrich.return_value = [enriched_text_doc, enriched_image_doc, enriched_table_doc]
        self.loader._enricher = mock_enricher
        
        # 执行加载
        result = self.loader.load(self.pdf_file)
        
        # 验证结果
        self.assertTrue(result.success)
        self.assertEqual(len(result.documents), 3)
        
        # 验证MultimodalEnricher被调用
        mock_enricher.enrich.assert_called_once_with([text_doc, image_doc, table_doc])
        
        # 验证文档被增强
        self.assertEqual(result.documents[0].metadata["modality"], "text")
        self.assertEqual(result.documents[1].metadata["modality"], "image")
        self.assertEqual(result.documents[2].metadata["modality"], "table")
    
    def test_image_enrichment_call(self):
        """测试图片文档的enrichment调用"""
        # 准备只有图片的元素
        elements = [self.image_element]
        
        # 模拟PDFAnalyzer
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = elements
        self.loader._analyzer = mock_analyzer
        
        # 模拟ImageExtractor
        mock_extractor = Mock()
        mock_extractor.extract_all.return_value = [Path('/path/to/image.png')]
        self.loader._image_extractor = mock_extractor
        
        # 模拟DocumentAdapter
        image_doc = Document(page_content="[图片]", metadata={"image_path": "/path/to/image.png"})
        mock_adapter = Mock()
        mock_adapter.adapt.return_value = [image_doc]
        self.loader._adapter = mock_adapter
        
        # 模拟MultimodalEnricher
        mock_enricher = Mock()
        enriched_doc = Document(
            page_content="图片描述:\n这是一张图片",
            metadata={
                "image_path": "/path/to/image.png",
                "modality": "image",
                "description": "这是一张图片",
                "ocr_text": "",
                "model_used": "qwen-vision"
            }
        )
        mock_enricher.enrich.return_value = [enriched_doc]
        self.loader._enricher = mock_enricher
        
        # 执行加载
        result = self.loader.load(self.pdf_file)
        
        # 验证结果
        self.assertTrue(result.success)
        self.assertEqual(len(result.documents), 1)
        
        # 验证文档被增强为图片文档
        doc = result.documents[0]
        self.assertEqual(doc.metadata["modality"], "image")
        self.assertIn("description", doc.metadata)
        self.assertIn("model_used", doc.metadata)
    
    def test_table_enrichment_call(self):
        """测试表格文档的enrichment调用"""
        # 准备只有表格的元素
        elements = [self.table_element]
        
        # 模拟PDFAnalyzer
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = elements
        self.loader._analyzer = mock_analyzer
        
        # 模拟ImageExtractor（无图片）
        mock_extractor = Mock()
        mock_extractor.extract_all.return_value = []
        self.loader._image_extractor = mock_extractor
        
        # 模拟DocumentAdapter
        table_doc = Document(page_content="表格内容", metadata={"source": "table"})
        mock_adapter = Mock()
        mock_adapter.adapt.return_value = [table_doc]
        self.loader._adapter = mock_adapter
        
        # 模拟MultimodalEnricher
        mock_enricher = Mock()
        enriched_doc = Document(
            page_content="表格内容:\n| A | B |\n| --- | --- |\n| 1 | 2 |",
            metadata={
                "source": "table",
                "modality": "table",
                "description": "表格内容",
                "ocr_text": "| A | B |\n| --- | --- |\n| 1 | 2 |"
            }
        )
        mock_enricher.enrich.return_value = [enriched_doc]
        self.loader._enricher = mock_enricher
        
        # 执行加载
        result = self.loader.load(self.pdf_file)
        
        # 验证结果
        self.assertTrue(result.success)
        self.assertEqual(len(result.documents), 1)
        
        # 验证文档被增强为表格文档
        doc = result.documents[0]
        self.assertEqual(doc.metadata["modality"], "table")
        self.assertIn("description", doc.metadata)
        self.assertIn("ocr_text", doc.metadata)
        self.assertTrue(doc.page_content.startswith("表格内容:\n"))
    
    def test_vision_failure_fallback(self):
        """测试Vision失败时的降级处理"""
        # 准备图片元素
        elements = [self.image_element]
        
        # 模拟PDFAnalyzer
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = elements
        self.loader._analyzer = mock_analyzer
        
        # 模拟ImageExtractor
        mock_extractor = Mock()
        mock_extractor.extract_all.return_value = [Path('/path/to/image.png')]
        self.loader._image_extractor = mock_extractor
        
        # 模拟DocumentAdapter
        image_doc = Document(page_content="[图片]", metadata={"image_path": "/path/to/image.png"})
        mock_adapter = Mock()
        mock_adapter.adapt.return_value = [image_doc]
        self.loader._adapter = mock_adapter
        
        # 模拟MultimodalEnricher失败
        mock_enricher = Mock()
        mock_enricher.enrich.side_effect = Exception("Vision服务不可用")
        self.loader._enricher = mock_enricher
        
        # 执行加载
        result = self.loader.load(self.pdf_file)
        
        # 验证结果仍然成功（降级处理）
        self.assertTrue(result.success)
        self.assertEqual(len(result.documents), 1)
        
        # 验证返回原始文档（未增强）
        doc = result.documents[0]
        self.assertEqual(doc.page_content, "[图片]")
        self.assertNotIn("modality", doc.metadata)  # 未增强的文档没有modality字段
    
    def test_text_only_pdf_unaffected(self):
        """测试普通文本PDF不受影响"""
        # 准备只有文本的元素
        elements = [self.text_element]
        
        # 模拟PDFAnalyzer
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = elements
        self.loader._analyzer = mock_analyzer
        
        # 模拟ImageExtractor（无图片）
        mock_extractor = Mock()
        mock_extractor.extract_all.return_value = []
        self.loader._image_extractor = mock_extractor
        
        # 模拟DocumentAdapter
        text_doc = Document(page_content="普通文本内容", metadata={"source": "text"})
        mock_adapter = Mock()
        mock_adapter.adapt.return_value = [text_doc]
        self.loader._adapter = mock_adapter
        
        # 模拟MultimodalEnricher（只添加modality字段）
        mock_enricher = Mock()
        enriched_doc = Document(page_content="普通文本内容", metadata={"source": "text", "modality": "text"})
        mock_enricher.enrich.return_value = [enriched_doc]
        self.loader._enricher = mock_enricher
        
        # 执行加载
        result = self.loader.load(self.pdf_file)
        
        # 验证结果
        self.assertTrue(result.success)
        self.assertEqual(len(result.documents), 1)
        
        # 验证文本内容不变
        doc = result.documents[0]
        self.assertEqual(doc.page_content, "普通文本内容")
        self.assertEqual(doc.metadata["modality"], "text")
        
        # 验证VisionProvider未被调用（通过enricher的enrich方法）
        mock_enricher.enrich.assert_called_once()
    
    def test_enricher_integration_with_real_components(self):
        """测试与真实组件的集成（但mock外部依赖）"""
        # 准备元素
        elements = [self.text_element]
        
        # 模拟PDFAnalyzer
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = elements
        self.loader._analyzer = mock_analyzer
        
        # 模拟ImageExtractor（无图片）
        mock_extractor = Mock()
        mock_extractor.extract_all.return_value = []
        self.loader._image_extractor = mock_extractor
        
        # 模拟DocumentAdapter
        text_doc = Document(page_content="测试内容", metadata={"source": "text"})
        mock_adapter = Mock()
        mock_adapter.adapt.return_value = [text_doc]
        self.loader._adapter = mock_adapter
        
        # 使用真实的MultimodalEnricher，但mock VisionProvider
        with patch('rag.enrichment.multimodal_enricher.QwenVisionProvider') as MockVisionProvider:
            mock_vision_provider = Mock()
            MockVisionProvider.return_value = mock_vision_provider
            
            # 创建真实的MultimodalEnricher
            from rag.enrichment import MultimodalEnricher
            self.loader._enricher = MultimodalEnricher(vision_provider=mock_vision_provider)
            
            # 执行加载
            result = self.loader.load(self.pdf_file)
            
            # 验证结果
            self.assertTrue(result.success)
            self.assertEqual(len(result.documents), 1)
            
            # 验证文档被增强
            doc = result.documents[0]
            self.assertEqual(doc.metadata["modality"], "text")
            
            # 验证VisionProvider未被调用（因为是文本）
            mock_vision_provider.analyze_image.assert_not_called()
    
    def test_pipeline_fallback_to_legacy(self):
        """测试当新架构失败时降级到旧架构"""
        # 模拟PDFAnalyzer抛出异常
        mock_analyzer = Mock()
        mock_analyzer.analyze.side_effect = Exception("分析失败")
        self.loader._analyzer = mock_analyzer
        
        # 模拟旧架构成功
        with patch.object(self.loader, '_load_with_legacy_architecture') as mock_legacy:
            mock_legacy.return_value = Mock(success=True, documents=[Document(page_content="旧架构文档")])
            
            # 执行加载
            result = self.loader.load(self.pdf_file)
            
            # 验证降级到旧架构
            mock_legacy.assert_called_once_with(self.pdf_file)
            self.assertTrue(result.success)


if __name__ == '__main__':
    unittest.main()