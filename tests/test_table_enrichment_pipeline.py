"""
Table Enrichment Pipeline测试

测试MultimodalEnricher对表格文档的增强流程
"""

import unittest
from unittest.mock import Mock, MagicMock
from pathlib import Path
import tempfile
import shutil

# 添加项目根目录到路径
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.enrichment.multimodal_enricher import (
    VisionProvider,
    MultimodalEnricher
)


class TestTableEnrichmentPipeline(unittest.TestCase):
    """测试表格增强流水线"""
    
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
    
    def _create_mock_document(self, content, metadata=None):
        """创建mock文档"""
        doc = Mock()
        doc.page_content = content
        doc.metadata = metadata or {}
        return doc
    
    def test_dict_table_conversion(self):
        """测试dict格式表格转换"""
        # 创建字典格式的表格文档（新格式）
        doc = self._create_mock_document(
            {"headers": ["姓名", "年龄", "城市"], "rows": [["张三", "25", "北京"], ["李四", "30", "上海"]]},
            metadata={"modality": "table"}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "table")
        # 验证转换为Markdown表格
        self.assertIn("| 姓名 | 年龄 | 城市 |", result[0].page_content)
        self.assertIn("| 张三 | 25 | 北京 |", result[0].page_content)
        self.assertIn("| 李四 | 30 | 上海 |", result[0].page_content)
        # 验证写入metadata
        self.assertEqual(result[0].metadata["description"], "表格内容")
        self.assertIn("ocr_text", result[0].metadata)
        # 验证page_content格式
        self.assertTrue(result[0].page_content.startswith("表格内容:\n"))
    
    def test_markdown_table_preservation(self):
        """测试Markdown表格保持"""
        # 创建已经是Markdown表格的文档
        markdown_table = "| 列1 | 列2 |\n| --- | --- |\n| 值1 | 值2 |"
        doc = self._create_mock_document(
            markdown_table,
            metadata={"modality": "table"}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "table")
        # 验证内容保持（在page_content中）
        self.assertIn("| 列1 | 列2 |", result[0].page_content)
        self.assertIn("| 值1 | 值2 |", result[0].page_content)
        # 验证写入metadata
        self.assertEqual(result[0].metadata["description"], "表格内容")
        self.assertIn("ocr_text", result[0].metadata)
        # 验证page_content格式
        self.assertTrue(result[0].page_content.startswith("表格内容:\n"))
    
    def test_empty_table_fallback(self):
        """测试空表格fallback"""
        # 创建空内容的表格文档
        doc = self._create_mock_document(
            "",
            metadata={"modality": "table"}
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
    
    def test_text_unaffected(self):
        """测试文本文档不受影响"""
        # 创建文本文档
        doc = self._create_mock_document(
            "这是一段普通文本",
            metadata={"modality": "text"}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "text")
        # 验证内容不变
        self.assertEqual(result[0].page_content, "这是一段普通文本")
        # 验证没有表格相关metadata
        self.assertNotIn("description", result[0].metadata)
        self.assertNotIn("ocr_text", result[0].metadata)
    
    def test_image_unaffected(self):
        """测试图像文档不受影响"""
        # 创建图像文档
        doc = self._create_mock_document(
            "图片描述：测试图片",
            metadata={"modality": "image", "image_path": str(self.test_image)}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "image")
        # 验证调用了VisionProvider
        self.mock_provider.analyze_image.assert_called_once_with(str(self.test_image))
        # 验证更新了图像metadata
        self.assertEqual(result[0].metadata["description"], "测试描述")
        self.assertEqual(result[0].metadata["ocr_text"], "测试OCR")
        self.assertEqual(result[0].metadata["model_used"], "test-model")
    
    def test_mixed_documents(self):
        """测试混合Document"""
        # 创建混合文档列表
        docs = [
            self._create_mock_document("文本内容", metadata={"modality": "text"}),
            self._create_mock_document(
                {"headers": ["A", "B"], "rows": [["1", "2"]]},
                metadata={"modality": "table"}
            ),
            self._create_mock_document(
                "图片描述：测试",
                metadata={"modality": "image", "image_path": str(self.test_image)}
            ),
            self._create_mock_document(
                "| X | Y |\n| --- | --- |\n| 3 | 4 |",
                metadata={"modality": "table"}
            )
        ]
        
        # 执行增强
        result = self.enricher.enrich(docs)
        
        # 验证结果
        self.assertEqual(len(result), 4)
        
        # 验证文本文档
        self.assertEqual(result[0].metadata["modality"], "text")
        self.assertEqual(result[0].page_content, "文本内容")
        
        # 验证第一个表格文档（字典格式）
        self.assertEqual(result[1].metadata["modality"], "table")
        self.assertIn("| A | B |", result[1].page_content)
        self.assertIn("| 1 | 2 |", result[1].page_content)
        self.assertTrue(result[1].page_content.startswith("表格内容:\n"))
        
        # 验证图像文档
        self.assertEqual(result[2].metadata["modality"], "image")
        self.mock_provider.analyze_image.assert_called_once_with(str(self.test_image))
        
        # 验证第二个表格文档（Markdown格式）
        self.assertEqual(result[3].metadata["modality"], "table")
        self.assertIn("| X | Y |", result[3].page_content)
        self.assertIn("| 3 | 4 |", result[3].page_content)
        self.assertTrue(result[3].page_content.startswith("表格内容:\n"))
    
    def test_table_with_old_dict_format(self):
        """测试旧字典格式表格"""
        # 创建旧字典格式的表格文档
        doc = self._create_mock_document(
            {"列1": "值1", "列2": "值2"},
            metadata={"modality": "table"}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "table")
        # 验证转换为Markdown表格
        self.assertIn("| 列1 | 列2 |", result[0].page_content)
        self.assertIn("| 值1 | 值2 |", result[0].page_content)
        # 验证写入metadata
        self.assertEqual(result[0].metadata["description"], "表格内容")
        self.assertIn("ocr_text", result[0].metadata)
        # 验证page_content格式
        self.assertTrue(result[0].page_content.startswith("表格内容:\n"))
    
    def test_table_with_whitespace_content(self):
        """测试空白内容表格"""
        # 创建空白内容的表格文档
        doc = self._create_mock_document(
            "   \n  \t  ",
            metadata={"modality": "table"}
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
    
    def test_table_with_partial_rows(self):
        """测试行数少于表头的表格"""
        # 创建行数少于表头的表格
        doc = self._create_mock_document(
            {"headers": ["A", "B", "C"], "rows": [["1", "2"]]},
            metadata={"modality": "table"}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "table")
        # 验证转换为Markdown表格（应该自动填充空值）
        self.assertIn("| A | B | C |", result[0].page_content)
        self.assertIn("| 1 | 2 |  |", result[0].page_content)
        # 验证写入metadata
        self.assertEqual(result[0].metadata["description"], "表格内容")
        self.assertIn("ocr_text", result[0].metadata)
        # 验证page_content格式
        self.assertTrue(result[0].page_content.startswith("表格内容:\n"))
    
    def test_table_with_extra_rows(self):
        """测试行数多于表头的表格"""
        # 创建行数多于表头的表格
        doc = self._create_mock_document(
            {"headers": ["A", "B"], "rows": [["1", "2", "3"], ["4", "5", "6"]]},
            metadata={"modality": "table"}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "table")
        # 验证转换为Markdown表格（应该截断多余列）
        self.assertIn("| A | B |", result[0].page_content)
        self.assertIn("| 1 | 2 |", result[0].page_content)
        self.assertIn("| 4 | 5 |", result[0].page_content)
        # 验证写入metadata
        self.assertEqual(result[0].metadata["description"], "表格内容")
        self.assertIn("ocr_text", result[0].metadata)
        # 验证page_content格式
        self.assertTrue(result[0].page_content.startswith("表格内容:\n"))
    
    def test_table_without_modality(self):
        """测试没有modality字段的表格文档"""
        # 创建没有modality字段的表格文档
        doc = self._create_mock_document(
            {"headers": ["X", "Y"], "rows": [["10", "20"]]},
            metadata={"source": "table"}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        # 验证自动添加modality字段
        self.assertEqual(result[0].metadata["modality"], "table")
        # 验证转换为Markdown表格
        self.assertIn("| X | Y |", result[0].page_content)
        self.assertIn("| 10 | 20 |", result[0].page_content)
        # 验证写入metadata
        self.assertEqual(result[0].metadata["description"], "表格内容")
        self.assertIn("ocr_text", result[0].metadata)
        # 验证page_content格式
        self.assertTrue(result[0].page_content.startswith("表格内容:\n"))
    
    def test_table_conversion_error_handling(self):
        """测试表格转换错误处理"""
        # 创建一个会导致转换错误的文档（通过mock模拟）
        doc = self._create_mock_document(
            {"headers": ["A"], "rows": [["1"]]},
            metadata={"modality": "table"}
        )
        
        # 模拟_normalize_table_content方法抛出异常
        original_normalize = self.enricher._normalize_table_content
        self.enricher._normalize_table_content = Mock(side_effect=Exception("转换错误"))
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "table")
        # 验证错误处理：保留原始内容
        self.assertIn("表格内容（转换失败）", result[0].metadata["description"])
        self.assertIn("ocr_text", result[0].metadata)
        # 验证page_content格式（错误时保留原始内容，但转换为字符串）
        self.assertIsInstance(result[0].page_content, str)
        # 验证内容不是空的
        self.assertTrue(len(result[0].page_content) > 0)
        
        # 恢复原始方法
        self.enricher._normalize_table_content = original_normalize
    
    def test_table_with_complex_data(self):
        """测试复杂数据表格"""
        # 创建复杂数据表格
        doc = self._create_mock_document(
            {
                "headers": ["产品", "价格", "数量", "总价"],
                "rows": [
                    ["苹果", "5.0", "10", "50.0"],
                    ["香蕉", "3.0", "20", "60.0"],
                    ["橙子", "4.0", "15", "60.0"]
                ]
            },
            metadata={"modality": "table"}
        )
        
        # 执行增强
        result = self.enricher.enrich([doc])
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["modality"], "table")
        # 验证转换为Markdown表格
        self.assertIn("| 产品 | 价格 | 数量 | 总价 |", result[0].page_content)
        self.assertIn("| 苹果 | 5.0 | 10 | 50.0 |", result[0].page_content)
        self.assertIn("| 香蕉 | 3.0 | 20 | 60.0 |", result[0].page_content)
        self.assertIn("| 橙子 | 4.0 | 15 | 60.0 |", result[0].page_content)
        # 验证写入metadata
        self.assertEqual(result[0].metadata["description"], "表格内容")
        self.assertIn("ocr_text", result[0].metadata)
        # 验证page_content格式
        self.assertTrue(result[0].page_content.startswith("表格内容:\n"))


if __name__ == '__main__':
    unittest.main()