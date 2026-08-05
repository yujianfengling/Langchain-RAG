"""
MultimodalEnricher单元测试
测试VisionProvider抽象接口和MultimodalEnricher的基本功能
"""

import unittest
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path
import tempfile
import shutil

# 添加项目根目录到路径
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.enrichment.multimodal_enricher import (
    VisionProvider,
    QwenVisionProvider,
    MultimodalEnricher
)


class TestVisionProvider(unittest.TestCase):
    """测试VisionProvider抽象接口"""
    
    def test_vision_provider_is_abstract(self):
        """测试VisionProvider是抽象类"""
        # 尝试实例化抽象类应该失败
        with self.assertRaises(TypeError):
            VisionProvider()
    
    def test_vision_provider_requires_analyze_image(self):
        """测试VisionProvider必须实现analyze_image方法"""
        # 创建一个没有实现analyze_image的子类
        class IncompleteProvider(VisionProvider):
            pass
        
        # 尝试实例化应该失败
        with self.assertRaises(TypeError):
            IncompleteProvider()


class TestQwenVisionProvider(unittest.TestCase):
    """测试QwenVisionProvider适配器"""
    
    def setUp(self):
        """设置测试环境"""
        # 创建临时目录和测试文件
        self.test_dir = tempfile.mkdtemp()
        self.test_image = Path(self.test_dir) / "test_image.jpg"
        self.test_image.touch()
        
        # 创建mock的MultimodalService
        self.mock_service = Mock()
        self.mock_service.process_image_to_document.return_value = {
            "page_content": "图片描述：测试图片\nOCR文字：测试文字",
            "metadata": {
                "caption": "测试图片",
                "ocr_result": "测试文字",
                "model_used": "qwen-vision",
                "modality": "image"
            }
        }
    
    def tearDown(self):
        """清理测试环境"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_qwen_vision_provider_init_with_service(self):
        """测试带服务初始化的QwenVisionProvider"""
        provider = QwenVisionProvider(self.mock_service)
        self.assertEqual(provider._multimodal_service, self.mock_service)
    
    def test_qwen_vision_provider_analyze_image_success(self):
        """测试成功分析图像"""
        provider = QwenVisionProvider(self.mock_service)
        result = provider.analyze_image(str(self.test_image))
        
        # 验证结果
        self.assertEqual(result["description"], "测试图片")
        self.assertEqual(result["ocr_text"], "测试文字")
        self.assertEqual(result["model_used"], "qwen-vision")
        self.assertIsNone(result["error"])
        
        # 验证服务调用
        self.mock_service.process_image_to_document.assert_called_once_with(str(self.test_image))
    
    def test_qwen_vision_provider_analyze_image_file_not_found(self):
        """测试分析不存在的图像文件"""
        provider = QwenVisionProvider(self.mock_service)
        result = provider.analyze_image("/nonexistent/image.jpg")
        
        # 验证错误信息
        self.assertIn("不存在", result["error"])
        self.assertEqual(result["description"], "")
    
    def test_qwen_vision_provider_analyze_image_service_error(self):
        """测试服务调用失败的情况"""
        # 设置服务抛出异常
        self.mock_service.process_image_to_document.side_effect = Exception("服务错误")
        
        provider = QwenVisionProvider(self.mock_service)
        result = provider.analyze_image(str(self.test_image))
        
        # 验证错误信息
        self.assertIn("服务错误", result["error"])
        self.assertEqual(result["description"], "")
    
    @patch('rag.enrichment.multimodal_enricher.QwenVisionProvider._ensure_service')
    def test_qwen_vision_provider_auto_init(self, mock_ensure):
        """测试自动初始化MultimodalService"""
        provider = QwenVisionProvider()
        mock_ensure.assert_called_once()


class TestMultimodalEnricher(unittest.TestCase):
    """测试MultimodalEnricher"""
    
    def setUp(self):
        """设置测试环境"""
        # 创建mock的VisionProvider
        self.mock_provider = Mock(spec=VisionProvider)
        self.mock_provider.analyze_image.return_value = {
            "description": "测试描述",
            "ocr_text": "测试OCR",
            "model_used": "test-model",
            "error": None
        }
        
        # 创建MultimodalEnricher实例
        self.enricher = MultimodalEnricher(self.mock_provider)
        
        # 创建临时目录
        self.test_dir = tempfile.mkdtemp()
        self.test_image = Path(self.test_dir) / "test.jpg"
        self.test_image.touch()
    
    def tearDown(self):
        """清理测试环境"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def _create_mock_document(self, content: str, metadata: dict = None):
        """创建mock文档"""
        doc = Mock()
        doc.page_content = content
        doc.metadata = metadata or {}
        return doc
    
    def test_enrich_text_document(self):
        """测试增强文本文档"""
        # 创建文本文档
        doc = self._create_mock_document("这是一段文本内容")
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "text")
        self.assertEqual(result[0].page_content, "这是一段文本内容")
    
    def test_enrich_image_document_with_metadata(self):
        """测试增强已有元数据的图像文档"""
        # 创建图像文档（已有元数据）
        doc = self._create_mock_document(
            "图片描述：已有描述",
            metadata={
                "modality": "image",
                "image_path": str(self.test_image),
                "caption": "已有描述",
                "ocr_result": "已有OCR"
            }
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "image")
        # 应该调用VisionProvider并更新metadata
        self.mock_provider.analyze_image.assert_called_once_with(str(self.test_image))
        self.assertEqual(result[0].metadata["description"], "测试描述")
        self.assertEqual(result[0].metadata["ocr_text"], "测试OCR")
        self.assertEqual(result[0].metadata["model_used"], "test-model")
    
    def test_enrich_image_document_without_metadata(self):
        """测试增强没有元数据的图像文档"""
        # 创建图像文档（没有元数据）
        doc = self._create_mock_document(
            "图片描述：无",
            metadata={
                "modality": "image",
                "image_path": str(self.test_image)
            }
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "image")
        # 应该调用VisionProvider
        self.mock_provider.analyze_image.assert_called_once_with(str(self.test_image))
        # 应该更新元数据
        self.assertEqual(result[0].metadata["description"], "测试描述")
        self.assertEqual(result[0].metadata["ocr_text"], "测试OCR")
        self.assertEqual(result[0].metadata["model_used"], "test-model")
    
    def test_enrich_table_document(self):
        """测试增强表格文档"""
        # 创建表格文档
        doc = self._create_mock_document(
            "表格内容：| 列1 | 列2 |\n| --- | --- |\n| 值1 | 值2 |",
            metadata={"source": "table"}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "table")
        # 验证写入metadata
        self.assertEqual(result[0].metadata["description"], "表格内容")
        self.assertIn("ocr_text", result[0].metadata)
        # 验证page_content格式
        self.assertTrue(result[0].page_content.startswith("表格内容:\n"))
    
    def test_enrich_table_document_with_dict_content(self):
        """测试增强字典格式的表格文档（新格式：headers/rows）"""
        # 创建字典格式的表格文档（新格式）
        doc = self._create_mock_document(
            {"headers": ["列1", "列2"], "rows": [["值1", "值2"]]},
            metadata={"source": "table"}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "table")
        # 验证内容被转换为Markdown表格
        self.assertIn("| 列1 | 列2 |", result[0].page_content)
        self.assertIn("| 值1 | 值2 |", result[0].page_content)
        # 验证写入metadata
        self.assertEqual(result[0].metadata["description"], "表格内容")
        self.assertIn("ocr_text", result[0].metadata)
        # 验证page_content格式
        self.assertTrue(result[0].page_content.startswith("表格内容:\n"))
    
    def test_enrich_empty_table_document(self):
        """测试增强空表格文档"""
        # 创建空表格文档
        doc = self._create_mock_document(
            "",
            metadata={"source": "table"}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "table")
        # 验证使用fallback
        self.assertIn("| (空表格) |", result[0].page_content)
        # 验证写入metadata
        self.assertEqual(result[0].metadata["description"], "表格内容")
        self.assertIn("ocr_text", result[0].metadata)
        # 验证page_content格式
        self.assertTrue(result[0].page_content.startswith("表格内容:\n"))
    
    def test_enrich_empty_documents(self):
        """测试增强空文档列表"""
        result = self.enricher.enrich([])
        self.assertEqual(result, [])
    
    def test_enrich_document_without_metadata(self):
        """测试增强没有metadata属性的文档"""
        # 创建没有metadata的文档
        doc = Mock()
        doc.page_content = "文本内容"
        delattr(doc, 'metadata')
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "text")
    
    def test_enrich_document_with_none_metadata(self):
        """测试增强metadata为None的文档"""
        # 创建metadata为None的文档
        doc = self._create_mock_document("文本内容", metadata=None)
        doc.metadata = None
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "text")
    
    def test_enrich_with_vision_provider_error(self):
        """测试VisionProvider返回错误的情况"""
        # 设置VisionProvider返回错误
        self.mock_provider.analyze_image.return_value = {
            "description": "",
            "ocr_text": "",
            "model_used": "",
            "error": "VisionProvider错误"
        }
        
        # 创建图像文档
        doc = self._create_mock_document(
            "图片描述：无",
            metadata={"modality": "image", "image_path": str(self.test_image)}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "image")
        # 应该没有更新描述
        self.assertNotIn("description", result[0].metadata)
    
    def test_enrich_with_vision_provider_exception(self):
        """测试VisionProvider抛出异常的情况"""
        # 设置VisionProvider抛出异常
        self.mock_provider.analyze_image.side_effect = Exception("Provider异常")
        
        # 创建图像文档
        doc = self._create_mock_document(
            "图片描述：无",
            metadata={"modality": "image", "image_path": str(self.test_image)}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "image")
        # 应该没有更新描述
        self.assertNotIn("description", result[0].metadata)
    
    def test_detect_document_type(self):
        """测试文档类型检测"""
        # 测试文本文档
        doc = self._create_mock_document("普通文本")
        self.assertEqual(self.enricher._detect_document_type(doc), "text")
        
        # 测试图像文档（通过元数据）
        doc = self._create_mock_document("内容", metadata={"file_type": "jpg"})
        self.assertEqual(self.enricher._detect_document_type(doc), "image")
        
        # 测试图像文档（通过内容）
        doc = self._create_mock_document("图片描述：测试图片")
        self.assertEqual(self.enricher._detect_document_type(doc), "image")
        
        # 测试表格文档
        doc = self._create_mock_document("表格内容：| 列1 | 列2 |")
        self.assertEqual(self.enricher._detect_document_type(doc), "table")
    
    def test_extract_image_path(self):
        """测试图像路径提取"""
        # 测试从image_path提取
        doc = self._create_mock_document("内容", metadata={"image_path": str(self.test_image)})
        self.assertEqual(self.enricher._extract_image_path(doc), str(self.test_image))
        
        # 测试从original_path提取
        doc = self._create_mock_document("内容", metadata={"original_path": str(self.test_image)})
        self.assertEqual(self.enricher._extract_image_path(doc), str(self.test_image))
        
        # 测试不存在的路径
        doc = self._create_mock_document("内容", metadata={"image_path": "/nonexistent/image.jpg"})
        self.assertIsNone(self.enricher._extract_image_path(doc))
        
        # 测试没有路径字段
        doc = self._create_mock_document("内容")
        self.assertIsNone(self.enricher._extract_image_path(doc))
    
    def test_update_page_content_with_vision(self):
        """测试使用视觉结果更新page_content"""
        doc = self._create_mock_document("原始内容")
        vision_result = {
            "description": "新描述",
            "ocr_text": "新OCR文字"
        }
        
        self.enricher._update_page_content_with_vision(doc, vision_result)
        
        self.assertIn("图片描述:", doc.page_content)
        self.assertIn("新描述", doc.page_content)
        self.assertIn("OCR:", doc.page_content)
        self.assertIn("新OCR文字", doc.page_content)
    
    def test_normalize_table_content(self):
        """测试表格内容规范化"""
        # 测试新字典格式（headers/rows）
        doc = self._create_mock_document({"headers": ["A", "B"], "rows": [["1", "2"]]})
        self.enricher._normalize_table_content(doc)
        self.assertIn("| A | B |", doc.page_content)
        self.assertIn("| 1 | 2 |", doc.page_content)
        
        # 测试旧字典格式（简单键值对）
        doc = self._create_mock_document({"列1": "值1", "列2": "值2"})
        self.enricher._normalize_table_content(doc)
        self.assertIn("| 列1 | 列2 |", doc.page_content)
        
        # 测试字符串格式
        doc = self._create_mock_document("表格内容")
        self.enricher._normalize_table_content(doc)
        self.assertEqual(doc.page_content, "表格内容")
        
        # 测试空内容
        doc = self._create_mock_document("")
        self.enricher._normalize_table_content(doc)
        self.assertEqual(doc.page_content, "| (空表格) |")
    
    def test_dict_to_markdown_table(self):
        """测试字典转Markdown表格"""
        # 测试新字典格式（headers/rows）
        data = {"headers": ["A", "B"], "rows": [["1", "2"], ["3", "4"]]}
        result = self.enricher._dict_to_markdown_table(data)
        self.assertIn("| A | B |", result)
        self.assertIn("| 1 | 2 |", result)
        self.assertIn("| 3 | 4 |", result)
        
        # 测试旧字典格式（简单键值对）
        data = {"列1": "值1", "列2": "值2"}
        result = self.enricher._dict_to_markdown_table(data)
        self.assertIn("| 列1 | 列2 |", result)
        self.assertIn("| 值1 | 值2 |", result)
        
        # 测试空字典
        data = {}
        result = self.enricher._dict_to_markdown_table(data)
        self.assertEqual(result, "| (空表格) |")
        
        # 测试只有headers没有rows
        data = {"headers": ["A", "B"], "rows": []}
        result = self.enricher._dict_to_markdown_table(data)
        self.assertIn("| A | B |", result)
        self.assertNotIn("| 1 | 2 |", result)  # 没有数据行


if __name__ == '__main__':
    unittest.main()