from types import SimpleNamespace
from unittest.mock import patch
from services.multimodal_service import MultimodalService


def test_configured_deepseek_is_not_shadowed_by_dashscope(monkeypatch):
    monkeypatch.setenv('VISION_MODE','vision')
    configs={'deepseek':{'model_name':'deepseek-chat','vision_model_name':'deepseek-flash','base_url':'https://api.deepseek.com'}}
    with patch('config.models.get_model_api_key',return_value='test-only'),patch('config.models.get_model_config',side_effect=lambda n:configs.get(n,{})),patch('services.multimodal_service.get_model_provider') as qwen,patch('requests.post') as http:
        service=MultimodalService()
        assert service.vision_provider.model_name=='deepseek'
        assert service.vision_provider.config['model_name']=='deepseek-flash'
        qwen.assert_not_called();http.assert_not_called()


def test_qwen_only_configuration_keeps_existing_route(monkeypatch):
    monkeypatch.setenv('VISION_MODE','vision')
    provider=SimpleNamespace(model_name='qwen-vision')
    with patch('config.models.get_model_api_key',side_effect=lambda n:'test-only' if n=='qwen-vision' else ''),patch('config.models.get_model_config',return_value={}),patch('services.multimodal_service.get_model_provider',return_value=provider),patch('requests.post') as http:
        assert MultimodalService().vision_provider is provider
        http.assert_not_called()


def test_explicit_provider_is_not_reselected(monkeypatch):
    monkeypatch.setenv('VISION_MODE','vision')
    provider=SimpleNamespace(model_name='injected-test')
    with patch('requests.post') as http:
        assert MultimodalService(provider).vision_provider is provider
        http.assert_not_called()
