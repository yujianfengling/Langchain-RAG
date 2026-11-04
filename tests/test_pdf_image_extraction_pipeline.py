"""
PDF图片提取管道测试
测试PdfLoader集成ImageExtractor的功能
"""

import unittest
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path
import tempfile
import shutil

# 添加项目根目录到路径
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.loaders.pdf_loader import PdfLoader
from rag.multimodal.pdf.models import PDFElement, ElementType


class TestPdfImageExtractionPipeline(unittest.TestCase):
    """测试PDF图片提取管道"""
    
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
            content="这是一段文本",
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
                'bpc': 8
            },
            page=1,
            bbox=(0, 0, 100, 100),
            confidence=1.0
        )
        
        self.table_element = PDFElement(
            type=ElementType.TABLE,
            content="表格内容",
            page=1,
            bbox=(0, 0, 100, 100),
            confidence=1.0
        )
    
    def tearDown(self):
        """清理测试环境"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_extract_images_from_elements_with_images(self):
        """测试从元素列表中提取图片"""
        # 准备元素列表
        elements = [self.text_element, self.image_element, self.table_element]
        
        # 模拟ImageExtractor
        mock_extractor = Mock()
        expected_path = Path('/path/to/image.png')
        mock_extractor.extract_all.return_value = [expected_path]
        
        # 设置loader的image_extractor
        self.loader._image_extractor = mock_extractor
        
        # 执行提取
        result = self.loader._extract_images_from_elements(elements, self.pdf_file)
        
        # 验证结果
        self.assertEqual(len(result), 3)
        
        # 验证图片元素被更新
        image_result = [e for e in result if e.type == ElementType.IMAGE][0]
        self.assertIn('image_path', image_result.content)
        self.assertEqual(image_result.content['image_path'], str(expected_path))
        
        # 验证其他元素未被修改
        text_result = [e for e in result if e.type == ElementType.TEXT][0]
        self.assertEqual(text_result.content, "这是一段文本")
        
        table_result = [e for e in result if e.type == ElementType.TABLE][0]
        self.assertEqual(table_result.content, "表格内容")
        
        # 验证ImageExtractor被调用
        mock_extractor.extract_all.assert_called_once()
    
    def test_extract_images_filters_small_and_limits_count(self):
        """正文型 PDF：小图不落盘，且最多保留指定数量的大图。"""
        large = PDFElement(
            type=ElementType.IMAGE,
            content={'xref': 2, 'width': 300, 'height': 300},
            page=2, bbox=(0, 0, 300, 300), confidence=1.0)
        small = PDFElement(
            type=ElementType.IMAGE,
            content={'xref': 3, 'width': 8, 'height': 8},
            page=2, bbox=(0, 0, 8, 8), confidence=1.0)
        elements = [self.text_element, self.image_element, small, large]
        mock_extractor = Mock()
        mock_extractor.extract_all.return_value = [Path('/path/to/large.png')]
        self.loader._image_extractor = mock_extractor

        result = self.loader._extract_images_from_elements(
            elements, self.pdf_file, max_images=1, min_pixels=10000)

        assert len(result) == 2  # text + 最大的一张图
        images = [e for e in result if e.type == ElementType.IMAGE]
        assert len(images) == 1
        assert images[0].content['xref'] == 2

    def test_extract_images_from_elements_without_images(self):
        """测试没有图片元素时的处理"""
        # 准备元素列表（没有图片）
        elements = [self.text_element, self.table_element]
        
        # 模拟ImageExtractor
        mock_extractor = Mock()
        
        # 设置loader的image_extractor
        self.loader._image_extractor = mock_extractor
        
        # 执行提取
        result = self.loader._extract_images_from_elements(elements, self.pdf_file)
        
        # 验证结果
        self.assertEqual(len(result), 2)
        
        # 验证ImageExtractor未被调用
        mock_extractor.extract_all.assert_not_called()
    
    def test_extract_images_from_elements_extractor_failure(self):
        """测试ImageExtractor失败时的处理"""
        # 准备元素列表
        elements = [self.text_element, self.image_element]
        
        # 模拟ImageExtractor抛出异常
        mock_extractor = Mock()
        mock_extractor.extract_all.side_effect = Exception("提取失败")
        
        # 设置loader的image_extractor
        self.loader._image_extractor = mock_extractor
        
        # 执行提取
        result = self.loader._extract_images_from_elements(elements, self.pdf_file)
        
        # 验证结果（应该返回原始元素）
        self.assertEqual(len(result), 2)
        
        # 验证图片元素未被修改
        image_result = [e for e in result if e.type == ElementType.IMAGE][0]
        self.assertNotIn('image_path', image_result.content)
    
    @patch('rag.loaders.pdf_loader.PdfLoader.analyzer')
    @patch('rag.loaders.pdf_loader.PdfLoader.adapter')
    @patch('rag.loaders.pdf_loader.PdfLoader.image_extractor')
    def test_load_with_new_architecture_with_images(self, mock_extractor, mock_adapter, mock_analyzer):
        """测试新架构加载包含图片的PDF"""
        # 准备测试数据
        elements = [self.text_element, self.image_element]
        mock_analyzer.analyze.return_value = elements
        
        enhanced_image_element = PDFElement(
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
        mock_extractor.extract_all.return_value = [Path('/path/to/image.png')]
        
        documents = [Mock()]
        mock_adapter.adapt.return_value = documents
        
        # 执行加载
        result = self.loader._load_with_new_architecture(self.pdf_file)
        
        # 验证结果
        self.assertTrue(result.success)
        self.assertEqual(len(result.documents), 1)
        
        # 验证调用顺序
        mock_analyzer.analyze.assert_called_once_with(self.pdf_file)
        mock_extractor.extract_all.assert_called_once()
        mock_adapter.adapt.assert_called_once()
    
    @patch('rag.loaders.pdf_loader.PdfLoader.analyzer')
    @patch('rag.loaders.pdf_loader.PdfLoader.adapter')
    def test_load_with_new_architecture_without_images(self, mock_adapter, mock_analyzer):
        """测试新架构加载不包含图片的PDF"""
        # 准备测试数据（没有图片）
        elements = [self.text_element, self.table_element]
        mock_analyzer.analyze.return_value = elements
        
        documents = [Mock()]
        mock_adapter.adapt.return_value = documents
        
        # 执行加载
        result = self.loader._load_with_new_architecture(self.pdf_file)
        
        # 验证结果
        self.assertTrue(result.success)
        self.assertEqual(len(result.documents), 1)
        
        # 验证调用
        mock_analyzer.analyze.assert_called_once_with(self.pdf_file)
        mock_adapter.adapt.assert_called_once()
    
    @patch('rag.loaders.pdf_loader.PdfLoader.analyzer')
    def test_load_with_new_architecture_no_elements(self, mock_analyzer):
        """测试新架构加载没有元素的PDF"""
        # 准备测试数据（没有元素）
        mock_analyzer.analyze.return_value = []
        
        # 执行加载（应该降级到旧架构）
        with patch.object(self.loader, '_load_with_legacy_architecture') as mock_legacy:
            mock_legacy.return_value = Mock(success=True)
            result = self.loader._load_with_new_architecture(self.pdf_file)
            
            # 验证降级到旧架构
            mock_legacy.assert_called_once_with(self.pdf_file)
    
    @patch('rag.loaders.pdf_loader.PdfLoader.analyzer')
    @patch('rag.loaders.pdf_loader.PdfLoader.adapter')
    @patch('rag.loaders.pdf_loader.PdfLoader.image_extractor')
    def test_load_with_new_architecture_extractor_failure(self, mock_extractor, mock_adapter, mock_analyzer):
        """测试ImageExtractor失败时的处理"""
        # 准备测试数据
        elements = [self.text_element, self.image_element]
        mock_analyzer.analyze.return_value = elements
        
        # 模拟ImageExtractor抛出异常
        mock_extractor.extract_all.side_effect = Exception("提取失败")
        
        documents = [Mock()]
        mock_adapter.adapt.return_value = documents
        
        # 执行加载（应该继续处理）
        result = self.loader._load_with_new_architecture(self.pdf_file)
        
        # 验证结果
        self.assertTrue(result.success)
        self.assertEqual(len(result.documents), 1)
        
        # 验证adapter被调用（使用原始元素）
        mock_adapter.adapt.assert_called_once()
    
    def test_image_element_content_structure(self):
        """测试图片元素内容结构"""
        # 创建带有image_path的图片元素
        element = PDFElement(
            type=ElementType.IMAGE,
            content={
                'xref': 1,
                'width': 100,
                'height': 100,
                'image_path': '/path/to/image.png'
            },
            page=1,
            bbox=(0, 0, 100, 100),
            confidence=1.0
        )
        
        # 验证内容结构
        self.assertIn('xref', element.content)
        self.assertIn('width', element.content)
        self.assertIn('height', element.content)
        self.assertIn('image_path', element.content)
        self.assertEqual(element.content['image_path'], '/path/to/image.png')
    
    def test_multiple_image_elements(self):
        """测试多个图片元素的处理"""
        # 创建多个图片元素
        image_element1 = PDFElement(
            type=ElementType.IMAGE,
            content={'xref': 1, 'width': 100, 'height': 100},
            page=1,
            bbox=(0, 0, 100, 100),
            confidence=1.0
        )
        
        image_element2 = PDFElement(
            type=ElementType.IMAGE,
            content={'xref': 2, 'width': 200, 'height': 200},
            page=2,
            bbox=(0, 0, 200, 200),
            confidence=1.0
        )
        
        elements = [self.text_element, image_element1, image_element2]
        
        # 模拟ImageExtractor
        mock_extractor = Mock()
        expected_path1 = Path('/path/to/image1.png')
        expected_path2 = Path('/path/to/image2.png')
        mock_extractor.extract_all.return_value = [expected_path1, expected_path2]
        
        # 设置loader的image_extractor
        self.loader._image_extractor = mock_extractor
        
        # 执行提取
        result = self.loader._extract_images_from_elements(elements, self.pdf_file)
        
        # 验证结果
        self.assertEqual(len(result), 3)
        
        # 验证两个图片元素都被更新
        image_results = [e for e in result if e.type == ElementType.IMAGE]
        self.assertEqual(len(image_results), 2)
        
        for img in image_results:
            self.assertIn('image_path', img.content)
            self.assertTrue(img.content['image_path'].endswith('.png'))
        
        # 验证ImageExtractor被调用
        mock_extractor.extract_all.assert_called_once()


if __name__ == '__main__':
    unittest.main()