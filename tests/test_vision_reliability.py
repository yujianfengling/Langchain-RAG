"""
V2.3.1 Phase 2.4.4: Vision Reliability Hardening 测试
覆盖重试机制、超时配置、缓存增强、优雅降级等场景
"""
import pytest
import time
import json
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime, timedelta

# 导入被测模块
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.model_service import (
    _is_retryable_error, _get_error_type,
    VISION_MAX_RETRIES, VISION_DEFAULT_TIMEOUT,
    VISION_RETRY_BACKOFF_BASE, VISION_RETRY_BACKOFF_MAX
)
from services.multimodal_service import MultimodalService
from rag.multimodal.document_converter import (
    MultimodalDocumentConverter, VISION_PROMPT_VERSION,
    IMAGE_DESCRIPTION_CACHE_DIR
)
from rag.enrichment.multimodal_enricher import (
    MultimodalEnricher, QwenVisionProvider
)


class TestRetryableErrorClassification:
    """测试可重试错误分类"""
    
    def test_timeout_is_retryable(self):
        """超时错误应该可重试"""
        import requests
        exc = requests.exceptions.Timeout("Connection timed out")
        assert _is_retryable_error(exc) is True
    
    def test_connection_error_is_retryable(self):
        """网络连接错误应该可重试"""
        import requests
        exc = requests.exceptions.ConnectionError("Connection refused")
        assert _is_retryable_error(exc) is True
    
    def test_429_is_retryable(self):
        """HTTP 429 (Rate Limit) 应该可重试"""
        import requests
        response = MagicMock()
        response.status_code = 429
        exc = requests.exceptions.HTTPError(response=response)
        assert _is_retryable_error(exc) is True
    
    def test_500_is_retryable(self):
        """HTTP 500 (Internal Server Error) 应该可重试"""
        import requests
        response = MagicMock()
        response.status_code = 500
        exc = requests.exceptions.HTTPError(response=response)
        assert _is_retryable_error(exc) is True
    
    def test_502_is_retryable(self):
        """HTTP 502 (Bad Gateway) 应该可重试"""
        import requests
        response = MagicMock()
        response.status_code = 502
        exc = requests.exceptions.HTTPError(response=response)
        assert _is_retryable_error(exc) is True
    
    def test_400_is_not_retryable(self):
        """HTTP 400 (Bad Request) 不应重试"""
        import requests
        response = MagicMock()
        response.status_code = 400
        exc = requests.exceptions.HTTPError(response=response)
        assert _is_retryable_error(exc) is False
    
    def test_401_is_not_retryable(self):
        """HTTP 401 (Unauthorized) 不应重试"""
        import requests
        response = MagicMock()
        response.status_code = 401
        exc = requests.exceptions.HTTPError(response=response)
        assert _is_retryable_error(exc) is False
    
    def test_403_is_not_retryable(self):
        """HTTP 403 (Forbidden) 不应重试"""
        import requests
        response = MagicMock()
        response.status_code = 403
        exc = requests.exceptions.HTTPError(response=response)
        assert _is_retryable_error(exc) is False
    
    def test_value_error_is_not_retryable(self):
        """ValueError 不应重试"""
        exc = ValueError("Invalid parameter")
        assert _is_retryable_error(exc) is False


class TestErrorTypeClassification:
    """测试错误类型分类"""
    
    def test_timeout_error_type(self):
        """超时错误类型"""
        import requests
        exc = requests.exceptions.Timeout("Connection timed out")
        assert _get_error_type(exc) == "timeout"
    
    def test_connection_error_type(self):
        """网络连接错误类型"""
        import requests
        exc = requests.exceptions.ConnectionError("Connection refused")
        assert _get_error_type(exc) == "connection_error"

    def test_429_error_type(self):
        """HTTP 429 错误类型"""
        import requests
        response = MagicMock()
        response.status_code = 429
        exc = requests.exceptions.HTTPError(response=response)
        assert _get_error_type(exc) == "rate_limited"

    def test_500_error_type(self):
        """HTTP 500 错误类型"""
        import requests
        response = MagicMock()
        response.status_code = 500
        exc = requests.exceptions.HTTPError(response=response)
        assert _get_error_type(exc) == "server_error"

    def test_400_error_type(self):
        """HTTP 400 错误类型"""
        import requests
        response = MagicMock()
        response.status_code = 400
        exc = requests.exceptions.HTTPError(response=response)
        assert _get_error_type(exc) == "invalid_request"

    def test_401_error_type(self):
        """HTTP 401 错误类型"""
        import requests
        response = MagicMock()
        response.status_code = 401
        exc = requests.exceptions.HTTPError(response=response)
        assert _get_error_type(exc) == "invalid_api_key"


class TestVisionRetryMechanism:
    """测试Vision API重试机制"""
    
    @patch('services.model_service.requests.post')
    def test_first_call_success(self, mock_post):
        """第一次调用成功"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "图片描述"}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response
        
        from services.model_service import ModelProvider
        provider = ModelProvider.__new__(ModelProvider)
        provider.api_key = "test_key"
        provider.model_name = "test_model"
        provider.config = {
            "model_name": "test-model",
            "base_url": "https://api.test.com/v1",
            "name": "Test Model"
        }
        
        messages = [{"role": "user", "content": "test"}]
        result = provider._call_openai_compatible_api(messages)
        
        assert result["answer"] == "图片描述"
        assert result["retries"] == 0
        assert mock_post.call_count == 1
    
    @patch('services.model_service.requests.post')
    @patch('services.model_service.time.sleep')
    def test_first_timeout_second_success(self, mock_sleep, mock_post):
        """第一次超时，第二次成功"""
        import requests
        
        # 第一次超时
        timeout_response = requests.exceptions.Timeout("Timeout")
        # 第二次成功
        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "choices": [{"message": {"content": "图片描述"}}]
        }
        success_response.raise_for_status = MagicMock()
        
        mock_post.side_effect = [timeout_response, success_response]
        
        from services.model_service import ModelProvider
        provider = ModelProvider.__new__(ModelProvider)
        provider.api_key = "test_key"
        provider.model_name = "test_model"
        provider.config = {
            "model_name": "test-model",
            "base_url": "https://api.test.com/v1",
            "name": "Test Model"
        }
        
        messages = [{"role": "user", "content": "test"}]
        result = provider._call_openai_compatible_api(messages)
        
        assert result["answer"] == "图片描述"
        assert result["retries"] == 1
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once()
    
    @patch('services.model_service.requests.post')
    @patch('services.model_service.time.sleep')
    def test_429_then_retry_success(self, mock_sleep, mock_post):
        """HTTP 429 后重试成功"""
        import requests
        
        # 第一次 429
        response_429 = MagicMock()
        response_429.status_code = 429
        error_429 = requests.exceptions.HTTPError(response=response_429)
        
        # 第二次成功
        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "choices": [{"message": {"content": "图片描述"}}]
        }
        success_response.raise_for_status = MagicMock()
        
        mock_post.side_effect = [error_429, success_response]
        
        from services.model_service import ModelProvider
        provider = ModelProvider.__new__(ModelProvider)
        provider.api_key = "test_key"
        provider.model_name = "test_model"
        provider.config = {
            "model_name": "test-model",
            "base_url": "https://api.test.com/v1",
            "name": "Test Model"
        }
        
        messages = [{"role": "user", "content": "test"}]
        result = provider._call_openai_compatible_api(messages)
        
        assert result["answer"] == "图片描述"
        assert result["retries"] == 1
        assert mock_post.call_count == 2
    
    @patch('services.model_service.requests.post')
    @patch('services.model_service.time.sleep')
    def test_500_then_retry_success(self, mock_sleep, mock_post):
        """HTTP 500 后重试成功"""
        import requests
        
        # 第一次 500
        response_500 = MagicMock()
        response_500.status_code = 500
        error_500 = requests.exceptions.HTTPError(response=response_500)
        
        # 第二次成功
        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "choices": [{"message": {"content": "图片描述"}}]
        }
        success_response.raise_for_status = MagicMock()
        
        mock_post.side_effect = [error_500, success_response]
        
        from services.model_service import ModelProvider
        provider = ModelProvider.__new__(ModelProvider)
        provider.api_key = "test_key"
        provider.model_name = "test_model"
        provider.config = {
            "model_name": "test-model",
            "base_url": "https://api.test.com/v1",
            "name": "Test Model"
        }
        
        messages = [{"role": "user", "content": "test"}]
        result = provider._call_openai_compatible_api(messages)
        
        assert result["answer"] == "图片描述"
        assert result["retries"] == 1
        assert mock_post.call_count == 2
    
    @patch('services.model_service.requests.post')
    def test_400_no_retry(self, mock_post):
        """HTTP 400 不应重试"""
        import requests
        
        response_400 = MagicMock()
        response_400.status_code = 400
        error_400 = requests.exceptions.HTTPError(response=response_400)
        mock_post.side_effect = error_400
        
        from services.model_service import ModelProvider
        provider = ModelProvider.__new__(ModelProvider)
        provider.api_key = "test_key"
        provider.model_name = "test_model"
        provider.config = {
            "model_name": "test-model",
            "base_url": "https://api.test.com/v1",
            "name": "Test Model"
        }
        
        messages = [{"role": "user", "content": "test"}]
        
        with pytest.raises(Exception):
            provider._call_openai_compatible_api(messages)
        
        assert mock_post.call_count == 1  # 只调用一次，不重试
    
    @patch('services.model_service.requests.post')
    def test_401_no_retry(self, mock_post):
        """HTTP 401 不应重试"""
        import requests
        
        response_401 = MagicMock()
        response_401.status_code = 401
        error_401 = requests.exceptions.HTTPError(response=response_401)
        mock_post.side_effect = error_401
        
        from services.model_service import ModelProvider
        provider = ModelProvider.__new__(ModelProvider)
        provider.api_key = "test_key"
        provider.model_name = "test_model"
        provider.config = {
            "model_name": "test-model",
            "base_url": "https://api.test.com/v1",
            "name": "Test Model"
        }
        
        messages = [{"role": "user", "content": "test"}]
        
        with pytest.raises(Exception):
            provider._call_openai_compatible_api(messages)
        
        assert mock_post.call_count == 1  # 只调用一次，不重试
    
    @patch('services.model_service.requests.post')
    @patch('services.model_service.time.sleep')
    def test_three_failures_then_degradation(self, mock_sleep, mock_post):
        """连续3次失败后降级"""
        import requests
        
        # 3次超时
        timeout_exc = requests.exceptions.Timeout("Timeout")
        mock_post.side_effect = [timeout_exc, timeout_exc, timeout_exc]
        
        from services.model_service import ModelProvider
        provider = ModelProvider.__new__(ModelProvider)
        provider.api_key = "test_key"
        provider.model_name = "test_model"
        provider.config = {
            "model_name": "test-model",
            "base_url": "https://api.test.com/v1",
            "name": "Test Model"
        }
        
        messages = [{"role": "user", "content": "test"}]

        from services.model_service import ModelAPIError
        with pytest.raises(ModelAPIError, match="响应超时"):
            provider._call_openai_compatible_api(messages)

        assert mock_post.call_count == 3
        assert mock_sleep.call_count == 2  # 第3次失败后不再sleep


class TestVisionStatusMetadata:
    """测试vision_status元数据"""
    
    def test_vision_status_success(self):
        """成功时vision_status为success"""
        service = MultimodalService.__new__(MultimodalService)
        service.vision_provider = MagicMock()
        service.vision_provider.model_name = "test-model"
        
        # Mock describe_image返回成功结果
        with patch.object(service, 'describe_image', return_value={
            "answer": '{"is_content":true,"description":"图片描述","ocr_text":""}',
            "model": "test-model",
            "latency": 1.0,
            "retries": 0
        }):
            with patch('os.path.exists', return_value=True):
                with patch('os.path.getsize', return_value=1024):
                    with patch('PIL.Image.open') as mock_image:
                        mock_image.return_value.__enter__ = MagicMock(return_value=MagicMock(size=(100, 100)))
                        mock_image.return_value.__exit__ = MagicMock()
                        
                        result = service.extract_text_from_image("/test/image.jpg")
        
        assert result["vision_status"] == "success"
        assert result["retries"] == 0
    
    def test_vision_status_failed_on_timeout(self):
        """超时时vision_status为failed"""
        service = MultimodalService.__new__(MultimodalService)
        service.vision_provider = MagicMock()
        service.vision_provider.model_name = "test-model"
        
        # Mock describe_image抛出超时错误
        with patch.object(service, 'describe_image', side_effect=ValueError("响应超时")):
            with patch('os.path.exists', return_value=True):
                with patch('os.path.getsize', return_value=1024):
                    with patch('PIL.Image.open') as mock_image:
                        mock_image.return_value.__enter__ = MagicMock(return_value=MagicMock(size=(100, 100)))
                        mock_image.return_value.__exit__ = MagicMock()
                        
                        result = service.extract_text_from_image("/test/image.jpg")
        
        assert result["vision_status"] == "failed"
        assert result["error_type"] == "timeout"
    
    def test_vision_status_skipped_on_file_not_found(self):
        """文件不存在时vision_status为skipped"""
        service = MultimodalService.__new__(MultimodalService)
        service.vision_provider = MagicMock()
        
        with patch('os.path.exists', return_value=False):
            result = service.extract_text_from_image("/test/nonexistent.jpg")
        
        assert result["vision_status"] == "skipped"
        assert result["error_type"] == "file_not_found"


class TestCacheKeyEnhancement:
    """测试缓存键增强"""
    
    def test_cache_key_includes_provider_and_model(self):
        """缓存键应包含Provider和模型信息"""
        converter = MultimodalDocumentConverter.__new__(MultimodalDocumentConverter)
        converter.multimodal_service = MagicMock()
        converter.multimodal_service.vision_provider = MagicMock()
        converter.multimodal_service.vision_provider.__class__.__name__ = "QwenVisionProvider"
        converter.multimodal_service.vision_provider.model_name = "qwen-vl-max"
        
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            f.write(b"test image content")
            temp_path = Path(f.name)
        
        try:
            cache_key = converter._compute_cache_key(temp_path)
            
            # 验证缓存键是有效的MD5
            assert len(cache_key) == 32
            assert all(c in '0123456789abcdef' for c in cache_key)
        finally:
            temp_path.unlink()
    
    def test_different_models_different_cache_keys(self):
        """不同模型应生成不同的缓存键"""
        converter = MultimodalDocumentConverter.__new__(MultimodalDocumentConverter)
        
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            f.write(b"test image content")
            temp_path = Path(f.name)
        
        try:
            # 使用模型1
            converter.multimodal_service = MagicMock()
            converter.multimodal_service.vision_provider = MagicMock()
            converter.multimodal_service.vision_provider.__class__.__name__ = "QwenVisionProvider"
            converter.multimodal_service.vision_provider.model_name = "qwen-vl-max"
            key1 = converter._compute_cache_key(temp_path)
            
            # 使用模型2
            converter.multimodal_service.vision_provider.model_name = "qwen-vl-plus"
            key2 = converter._compute_cache_key(temp_path)
            
            assert key1 != key2
        finally:
            temp_path.unlink()
    
    def test_cache_hit_marks_vision_status_as_cached(self):
        """缓存命中应标记vision_status为cached"""
        converter = MultimodalDocumentConverter.__new__(MultimodalDocumentConverter)
        
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            f.write(b"test image content")
            temp_path = Path(f.name)
        
        try:
            cache_key = "test_cache_key"
            cache_data = {
                "page_content": "图片描述",
                "metadata": {
                    "model_used": "qwen-vl-max",
                    "cache_prompt_version": VISION_PROMPT_VERSION,
                    "vision_status": "success"
                },
                "created_at": datetime.now().isoformat()
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(cache_data))):
                with patch.object(Path, 'exists', return_value=True):
                    doc = converter._load_from_cache(cache_key, temp_path)
            
            assert doc is not None
            assert doc.metadata["vision_status"] == "cached"
        finally:
            temp_path.unlink()
    
    def test_cache_miss_on_version_mismatch(self):
        """版本不匹配时缓存未命中"""
        converter = MultimodalDocumentConverter.__new__(MultimodalDocumentConverter)
        
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            f.write(b"test image content")
            temp_path = Path(f.name)
        
        try:
            cache_key = "test_cache_key"
            cache_data = {
                "page_content": "图片描述",
                "metadata": {
                    "model_used": "qwen-vl-max",
                    "cache_prompt_version": "v0.9",  # 旧版本
                    "vision_status": "success"
                },
                "created_at": datetime.now().isoformat()
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(cache_data))):
                with patch.object(Path, 'exists', return_value=True):
                    doc = converter._load_from_cache(cache_key, temp_path)
            
            assert doc is None  # 版本不匹配，缓存未命中
        finally:
            temp_path.unlink()
    
    def test_failed_result_not_cached(self):
        """失败结果不应被缓存"""
        converter = MultimodalDocumentConverter.__new__(MultimodalDocumentConverter)
        
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            f.write(b"test image content")
            temp_path = Path(f.name)
        
        try:
            from langchain_core.documents import Document
            
            # 创建一个失败的Document
            failed_doc = Document(
                page_content="图片描述生成失败",
                metadata={
                    "vision_status": "failed",
                    "model_used": "qwen-vl-max"
                }
            )
            
            cache_key = "test_cache_key"
            
            # 尝试保存失败结果
            with patch('builtins.open', mock_open()) as mock_file:
                converter._save_to_cache(cache_key, temp_path, failed_doc)
            
            # 验证没有写入文件
            mock_file.assert_not_called()
        finally:
            temp_path.unlink()


class TestNormalDocumentNotAffected:
    """测试普通文档不受影响"""
    
    def test_text_document_not_affected(self):
        """普通text文档不应受影响"""
        enricher = MultimodalEnricher.__new__(MultimodalEnricher)
        enricher.vision_provider = MagicMock()
        
        from langchain_core.documents import Document
        
        text_doc = Document(
            page_content="这是一段普通文本",
            metadata={"type": "text", "source": "test.pdf"}
        )
        
        result = enricher._enrich_text_document(text_doc)
        
        assert result.metadata["modality"] == "text"
        assert "vision_status" not in result.metadata
    
    def test_table_document_not_affected(self):
        """普通table文档不应受影响"""
        enricher = MultimodalEnricher.__new__(MultimodalEnricher)
        enricher.vision_provider = MagicMock()
        
        from langchain_core.documents import Document
        
        table_doc = Document(
            page_content="| 列1 | 列2 |\n|---|---|\n| 数据1 | 数据2 |",
            metadata={"type": "table", "source": "test.pdf"}
        )
        
        result = enricher._enrich_table_document(table_doc)
        
        assert result.metadata["modality"] == "table"
        assert "vision_status" not in result.metadata


class TestRetryConfiguration:
    """测试重试配置"""
    
    def test_max_retries_is_3(self):
        """最大重试次数应为3"""
        assert VISION_MAX_RETRIES == 3
    
    def test_default_timeout_is_60(self):
        """默认超时应为60秒"""
        assert VISION_DEFAULT_TIMEOUT == 60
    
    def test_backoff_base_is_1(self):
        """退避基数应为1秒"""
        assert VISION_RETRY_BACKOFF_BASE == 1.0
    
    def test_backoff_max_is_10(self):
        """最大退避时间应为10秒"""
        assert VISION_RETRY_BACKOFF_MAX == 10.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
