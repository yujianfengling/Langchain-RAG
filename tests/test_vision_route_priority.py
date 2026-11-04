from types import SimpleNamespace
from unittest.mock import patch
from services.multimodal_service import MultimodalService


def test_deepseek_description_disables_thinking_in_final_body(tmp_path, monkeypatch):
    import json
    from services.model_service import OpenAICompatibleProvider

    monkeypatch.setenv("VISION_MODE", "vision")
    image = tmp_path / "image.png"
    image.write_bytes(b"mock-image")
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.model_name = "deepseek"
    provider.api_key = "test-only"
    provider.config = {"model_name": "deepseek-flash", "base_url": "https://api.deepseek.com", "name": "test"}
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"choices": [{"message": {"content": '{"is_content":true,"description":"diagram","ocr_text":"code"}'}, "finish_reason": "stop"}]},
    )
    with patch("services.model_service.requests.post", return_value=response) as http:
        result = MultimodalService(provider).describe_image(str(image))
    body = json.loads(http.call_args.kwargs["data"])
    assert body["thinking"] == {"type": "disabled"}
    assert body["model"] == "deepseek-flash"
    assert result["answer"]
    assert http.call_count == 1


def test_other_vision_provider_receives_no_deepseek_thinking_option(tmp_path, monkeypatch):
    from unittest.mock import Mock

    monkeypatch.setenv("VISION_MODE", "vision")
    image = tmp_path / "image.png"
    image.write_bytes(b"mock-image")
    provider = SimpleNamespace(model_name="qwen-vision", vision=Mock(return_value={
        "answer": '{"is_content":true,"description":"ok","ocr_text":""}'}))
    MultimodalService(provider).describe_image(str(image))
    assert "extra_payload" not in provider.vision.call_args.kwargs


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
