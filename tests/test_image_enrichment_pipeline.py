"""
Image Enrichment Pipeline 测试

验证 Phase 2.2-B 的 image 处理流程：
1. image Document 判断：metadata["modality"] == "image"
2. 获取图片路径：metadata["image_path"]
3. 调用：VisionProvider.analyze_image(image_path)
4. 将结果写回 metadata：description, ocr_text, model_used
5. 更新 page_content 格式
6. 错误处理：Vision失败时保留原Document
"""

import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, MagicMock

# 添加项目根目录到路径
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.enrichment.multimodal_enricher import MultimodalEnricher, VisionProvider


class TestImageEnrichmentPipeline(unittest.TestCase):
    """测试 Image Enrichment Pipeline"""
    
    def setUp(self):
        """设置测试环境"""
        # 创建临时目录和测试图片
        self.test_dir = tempfile.mkdtemp()
        self.test_image = Path(self.test_dir) / "test_image.png"
        self.test_image.touch()
        
        # 创建 mock VisionProvider
        self.mock_provider = Mock(spec=VisionProvider)
        self.mock_provider.analyze_image.return_value = {
            "description": "这是一张测试图片",
            "ocr_text": "OCR识别文字",
            "model_used": "qwen-vision",
            "error": None
        }
        
        # 创建 MultimodalEnricher 实例
        self.enricher = MultimodalEnricher(self.mock_provider)
    
    def tearDown(self):
        """清理测试环境"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_image_normal_enrichment(self):
        """测试 image 正常增强流程"""
        # 创建 image 文档
        doc = Mock()
        doc.page_content = "[图片] 尺寸: 100x80"
        doc.metadata = {
            "type": "image",
            "modality": "image",
            "image_path": str(self.test_image),
            "page": 0,
            "bbox": [10, 10, 110, 90]
        }
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        enriched_doc = result[0]
        
        # 验证 metadata
        self.assertEqual(enriched_doc.metadata["modality"], "image")
        self.assertEqual(enriched_doc.metadata["description"], "这是一张测试图片")
        self.assertEqual(enriched_doc.metadata["ocr_text"], "OCR识别文字")
        self.assertEqual(enriched_doc.metadata["model_used"], "qwen-vision")
        
        # 验证 page_content 更新
        self.assertIn("图片描述:", enriched_doc.page_content)
        self.assertIn("这是一张测试图片", enriched_doc.page_content)
        self.assertIn("OCR:", enriched_doc.page_content)
        self.assertIn("OCR识别文字", enriched_doc.page_content)
        
        # 验证 VisionProvider 被调用
        self.mock_provider.analyze_image.assert_called_once_with(str(self.test_image))
    
    def test_image_path_missing_skip(self):
        """测试 image_path 不存在时跳过增强"""
        # 创建 image 文档（没有 image_path）
        doc = Mock()
        doc.page_content = "[图片] 尺寸: 100x80"
        doc.metadata = {
            "type": "image",
            "modality": "image",
            "page": 0,
            "bbox": [10, 10, 110, 90]
        }
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        enriched_doc = result[0]
        
        # 验证 metadata 没有更新
        self.assertNotIn("description", enriched_doc.metadata)
        self.assertNotIn("ocr_text", enriched_doc.metadata)
        self.assertNotIn("model_used", enriched_doc.metadata)
        
        # 验证 page_content 没有更新
        self.assertEqual(enriched_doc.page_content, "[图片] 尺寸: 100x80")
        
        # 验证 VisionProvider 没有被调用
        self.mock_provider.analyze_image.assert_not_called()
    
    def test_image_path_not_exist_skip(self):
        """测试 image_path 文件不存在时跳过增强"""
        # 创建 image 文档（image_path 指向不存在的文件）
        doc = Mock()
        doc.page_content = "[图片] 尺寸: 100x80"
        doc.metadata = {
            "type": "image",
            "modality": "image",
            "image_path": "/nonexistent/path/image.png",
            "page": 0,
            "bbox": [10, 10, 110, 90]
        }
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        enriched_doc = result[0]
        
        # 验证 metadata 没有更新
        self.assertNotIn("description", enriched_doc.metadata)
        self.assertNotIn("ocr_text", enriched_doc.metadata)
        self.assertNotIn("model_used", enriched_doc.metadata)
        
        # 验证 page_content 没有更新
        self.assertEqual(enriched_doc.page_content, "[图片] 尺寸: 100x80")
        
        # 验证 VisionProvider 没有被调用
        self.mock_provider.analyze_image.assert_not_called()
    
    def test_vision_failure_graceful_degradation(self):
        """测试 Vision 失败时的优雅降级"""
        # 设置 VisionProvider 返回错误
        self.mock_provider.analyze_image.return_value = {
            "description": "",
            "ocr_text": "",
            "model_used": "",
            "error": "VisionProvider错误"
        }
        
        # 创建 image 文档
        doc = Mock()
        doc.page_content = "[图片] 尺寸: 100x80"
        doc.metadata = {
            "type": "image",
            "modality": "image",
            "image_path": str(self.test_image),
            "page": 0,
            "bbox": [10, 10, 110, 90]
        }
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        enriched_doc = result[0]
        
        # 验证 metadata 没有更新（错误时保留原Document）
        self.assertNotIn("description", enriched_doc.metadata)
        self.assertNotIn("ocr_text", enriched_doc.metadata)
        # 失败降级路径会记录实际尝试的 provider 与模型（真实模型名）
        self.assertEqual(enriched_doc.metadata.get("provider_used"), "qwen-vision")
        self.assertEqual(enriched_doc.metadata.get("model_used"), "qwen-vl-plus")

        # 验证 page_content 没有更新
        self.assertEqual(enriched_doc.page_content, "[图片] 尺寸: 100x80")

        # 验证 VisionProvider 被调用
        self.mock_provider.analyze_image.assert_called_once_with(str(self.test_image))
    
    def test_vision_exception_graceful_degradation(self):
        """测试 Vision 异常时的优雅降级"""
        # 设置 VisionProvider 抛出异常
        self.mock_provider.analyze_image.side_effect = Exception("Provider异常")
        
        # 创建 image 文档
        doc = Mock()
        doc.page_content = "[图片] 尺寸: 100x80"
        doc.metadata = {
            "type": "image",
            "modality": "image",
            "image_path": str(self.test_image),
            "page": 0,
            "bbox": [10, 10, 110, 90]
        }
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        enriched_doc = result[0]
        
        # 验证 metadata 没有更新（异常时保留原Document）
        self.assertNotIn("description", enriched_doc.metadata)
        self.assertNotIn("ocr_text", enriched_doc.metadata)
        # 异常路径同样记录 provider_used / model_used（不缺失）
        self.assertEqual(enriched_doc.metadata.get("provider_used"), "qwen-vision")
        self.assertEqual(enriched_doc.metadata.get("model_used"), "qwen-vl-plus")

        # 验证 page_content 没有更新
        self.assertEqual(enriched_doc.page_content, "[图片] 尺寸: 100x80")

        # 验证 VisionProvider 被调用
        self.mock_provider.analyze_image.assert_called_once_with(str(self.test_image))
    
    def test_text_not_affected(self):
        """测试 text 文档不受影响"""
        # 创建 text 文档
        doc = Mock()
        doc.page_content = "这是一段文本内容"
        doc.metadata = {
            "type": "text",
            "page": 0,
            "bbox": [10, 10, 200, 30]
        }
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        enriched_doc = result[0]
        
        # 验证 metadata 只添加了 modality
        self.assertEqual(enriched_doc.metadata["modality"], "text")
        self.assertNotIn("description", enriched_doc.metadata)
        self.assertNotIn("ocr_text", enriched_doc.metadata)
        self.assertNotIn("model_used", enriched_doc.metadata)
        
        # 验证 page_content 没有更新
        self.assertEqual(enriched_doc.page_content, "这是一段文本内容")
        
        # 验证 VisionProvider 没有被调用
        self.mock_provider.analyze_image.assert_not_called()
    
    def test_image_with_existing_content(self):
        """测试 image 文档已有内容时的增强"""
        # 创建 image 文档（已有内容）
        doc = Mock()
        doc.page_content = "图片内容：测试图片"
        doc.metadata = {
            "type": "image",
            "modality": "image",
            "image_path": str(self.test_image),
            "page": 0,
            "bbox": [10, 10, 110, 90]
        }
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        enriched_doc = result[0]
        
        # 验证 metadata 更新
        self.assertEqual(enriched_doc.metadata["description"], "这是一张测试图片")
        self.assertEqual(enriched_doc.metadata["ocr_text"], "OCR识别文字")
        self.assertEqual(enriched_doc.metadata["model_used"], "qwen-vision")
        
        # 验证 page_content 包含原有内容和新内容
        self.assertIn("图片内容：测试图片", enriched_doc.page_content)
        self.assertIn("图片描述:", enriched_doc.page_content)
        self.assertIn("这是一张测试图片", enriched_doc.page_content)
        self.assertIn("OCR:", enriched_doc.page_content)
        self.assertIn("OCR识别文字", enriched_doc.page_content)
    
    def test_image_with_empty_vision_result(self):
        """测试 Vision 返回空结果时的处理"""
        # 设置 VisionProvider 返回空结果
        self.mock_provider.analyze_image.return_value = {
            "description": "",
            "ocr_text": "",
            "model_used": "qwen-vision",
            "error": None
        }
        
        # 创建 image 文档
        doc = Mock()
        doc.page_content = "[图片] 尺寸: 100x80"
        doc.metadata = {
            "type": "image",
            "modality": "image",
            "image_path": str(self.test_image),
            "page": 0,
            "bbox": [10, 10, 110, 90]
        }
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        enriched_doc = result[0]
        
        # 验证 metadata 更新
        self.assertEqual(enriched_doc.metadata["description"], "")
        self.assertEqual(enriched_doc.metadata["ocr_text"], "")
        self.assertEqual(enriched_doc.metadata["model_used"], "qwen-vision")
        
        # 验证 page_content 没有更新（空描述和空OCR不更新page_content）
        self.assertEqual(enriched_doc.page_content, "[图片] 尺寸: 100x80")
    
    def test_image_modality_mismatch_skip(self):
        """测试 modality 不匹配时跳过增强"""
        # 创建 image 文档（modality 不是 image）
        doc = Mock()
        doc.page_content = "[图片] 尺寸: 100x80"
        doc.metadata = {
            "type": "image",
            "modality": "text",  # 故意设置错误
            "image_path": str(self.test_image),
            "page": 0,
            "bbox": [10, 10, 110, 90]
        }
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        enriched_doc = result[0]
        
        # 验证 metadata 没有更新
        self.assertNotIn("description", enriched_doc.metadata)
        self.assertNotIn("ocr_text", enriched_doc.metadata)
        self.assertNotIn("model_used", enriched_doc.metadata)
        
        # 验证 page_content 没有更新
        self.assertEqual(enriched_doc.page_content, "[图片] 尺寸: 100x80")
        
        # 验证 VisionProvider 没有被调用
        self.mock_provider.analyze_image.assert_not_called()


class TestImageEnrichmentIntegration(unittest.TestCase):
    """测试 Image Enrichment 集成"""
    
    def setUp(self):
        """设置测试环境"""
        # 创建临时目录和测试图片
        self.test_dir = tempfile.mkdtemp()
        self.test_image = Path(self.test_dir) / "test_image.png"
        self.test_image.touch()
        
        # 创建真实的 MultimodalEnricher（带 mock VisionProvider）
        self.mock_provider = Mock(spec=VisionProvider)
        self.mock_provider.analyze_image.return_value = {
            "description": "集成测试图片描述",
            "ocr_text": "集成测试OCR文字",
            "model_used": "qwen-vision",
            "error": None
        }
        
        self.enricher = MultimodalEnricher(self.mock_provider)
    
    def tearDown(self):
        """清理测试环境"""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_mixed_document_types(self):
        """测试混合文档类型的增强"""
        # 创建混合文档列表
        docs = [
            # text 文档
            Mock(
                page_content="文本内容",
                metadata={"type": "text", "page": 0, "bbox": [10, 10, 200, 30]}
            ),
            # image 文档
            Mock(
                page_content="[图片] 尺寸: 100x80",
                metadata={
                    "type": "image",
                    "modality": "image",
                    "image_path": str(self.test_image),
                    "page": 0,
                    "bbox": [10, 40, 110, 120]
                }
            ),
            # 另一个 text 文档
            Mock(
                page_content="更多文本内容",
                metadata={"type": "text", "page": 1, "bbox": [10, 10, 200, 30]}
            )
        ]
        
        # 执行增强
        result = self.enricher.enrich(docs)
        
        # 验证结果
        self.assertEqual(len(result), 3)
        
        # 验证 text 文档
        self.assertEqual(result[0].metadata["modality"], "text")
        self.assertEqual(result[0].page_content, "文本内容")
        
        # 验证 image 文档
        self.assertEqual(result[1].metadata["modality"], "image")
        self.assertEqual(result[1].metadata["description"], "集成测试图片描述")
        self.assertEqual(result[1].metadata["ocr_text"], "集成测试OCR文字")
        self.assertEqual(result[1].metadata["model_used"], "qwen-vision")
        self.assertIn("图片描述:", result[1].page_content)
        self.assertIn("OCR:", result[1].page_content)
        
        # 验证第二个 text 文档
        self.assertEqual(result[2].metadata["modality"], "text")
        self.assertEqual(result[2].page_content, "更多文本内容")
        
        # 验证 VisionProvider 只被调用了一次（只对 image 文档）
        self.mock_provider.analyze_image.assert_called_once_with(str(self.test_image))
    
    def test_multiple_image_documents(self):
        """测试多个 image 文档的增强"""
        # 创建第二个测试图片
        test_image2 = Path(self.test_dir) / "test_image2.png"
        test_image2.touch()
        
        docs = [
            # 第一个 image 文档
            Mock(
                page_content="[图片] 尺寸: 100x80",
                metadata={
                    "type": "image",
                    "modality": "image",
                    "image_path": str(self.test_image),
                    "page": 0
                }
            ),
            # 第二个 image 文档
            Mock(
                page_content="[图片] 尺寸: 200x150",
                metadata={
                    "type": "image",
                    "modality": "image",
                    "image_path": str(test_image2),
                    "page": 1
                }
            )
        ]
        
        # 执行增强
        result = self.enricher.enrich(docs)
        
        # 验证结果
        self.assertEqual(len(result), 2)
        
        # 验证两个 image 文档都被增强
        for doc in result:
            self.assertEqual(doc.metadata["modality"], "image")
            self.assertEqual(doc.metadata["description"], "集成测试图片描述")
            self.assertEqual(doc.metadata["ocr_text"], "集成测试OCR文字")
            self.assertEqual(doc.metadata["model_used"], "qwen-vision")
        
        # 验证 VisionProvider 被调用了两次
        self.assertEqual(self.mock_provider.analyze_image.call_count, 2)


if __name__ == '__main__':
    unittest.main()
