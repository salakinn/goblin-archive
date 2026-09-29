import json
import stat
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend import main
from backend.ai_config import load_ai_config
from backend.config import Settings


def test_ai_settings_save_reload_and_hide_key(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / 'data')
    monkeypatch.setattr(main, 'settings', settings)
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        initial = client.get('/api/settings/ai').json()
        assert initial['key_configured'] is False
        assert 'api_key' not in initial
        saved = client.put('/api/settings/ai', json={
            'api_key': 'sk-test-secret',
            'ai_tagging_model': 'gpt-test',
            'ai_translation_model': 'gpt-translation',
            'ai_translation_input_usd_per_million': 0.5,
            'ai_translation_output_usd_per_million': 1.5,
        })
        assert saved.status_code == 200, saved.text
        assert saved.json()['key_configured'] is True
        assert saved.json()['ai_tagging_model'] == 'gpt-test'
        assert 'sk-test-secret' not in saved.text
        path = settings.data_dir / 'ai-settings.json'
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert json.loads(path.read_text())['api_key'] == 'sk-test-secret'
        assert settings.openai_api_key.get_secret_value() == 'sk-test-secret'
        restarted = load_ai_config(Settings(data_dir=settings.data_dir))
        assert restarted.openai_api_key.get_secret_value() == 'sk-test-secret'
        assert restarted.ai_translation_model == 'gpt-translation'
        invalid = client.put('/api/settings/ai', json={'ai_tagging_model': 'bad model\n'})
        assert invalid.status_code == 422
        assert settings.ai_tagging_model == 'gpt-test'
        assert client.put('/api/settings/ai', json={'api_key': None}).json()['key_configured'] is False
        assert json.loads(path.read_text())['api_key'] == ''
    finally:
        client.close()


def test_ai_connection_check_uses_saved_key_without_exposing_it(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / 'data', openai_api_key='sk-test-secret', ai_tagging_model='gpt-test')
    monkeypatch.setattr(main, 'settings', settings)
    provider = MagicMock()
    factory = MagicMock(return_value=provider)
    monkeypatch.setattr(main, 'make_ai_provider', factory)
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        result = client.post('/api/settings/ai/test')
        assert result.status_code == 200
        assert result.json() == {'ok': True, 'model': 'gpt-test'}
        assert 'sk-test-secret' not in result.text
        factory.assert_called_once()
        assert factory.call_args.args[0].openai_api_key.get_secret_value() == 'sk-test-secret'
        assert provider.generate.call_args.kwargs['model'] == 'gpt-test'
    finally:
        client.close()


def test_custom_settings_keep_keys_separate_and_survive_restart(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / 'data', openai_api_key='openai-secret')
    monkeypatch.setattr(main, 'settings', settings)
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        saved = client.put('/api/settings/ai', json={
            'provider': 'custom', 'base_url': 'https://example.test/v1/',
            'api_key': 'custom-secret', 'ai_tagging_model': 'other-model',
        })
        assert saved.status_code == 200, saved.text
        assert saved.json()['provider'] == 'custom'
        assert saved.json()['base_url'] == 'https://example.test/v1'
        assert saved.json()['key_configured'] is True
        assert 'custom-secret' not in saved.text
        reloaded = load_ai_config(Settings(data_dir=settings.data_dir))
        assert reloaded.ai_provider == 'custom'
        assert reloaded.ai_custom_api_key.get_secret_value() == 'custom-secret'
        assert reloaded.openai_api_key.get_secret_value() == 'openai-secret'
        assert client.put('/api/settings/ai', json={'provider': 'openai'}).json()['key_configured'] is True
        assert client.put('/api/settings/ai', json={'provider': 'custom', 'api_key': None}).json()['key_configured'] is False
        assert settings.openai_api_key.get_secret_value() == 'openai-secret'
    finally:
        client.close()


def test_custom_settings_reject_invalid_url_without_changing_active_provider(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / 'data')
    monkeypatch.setattr(main, 'settings', settings)
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        assert client.put('/api/settings/ai', json={'provider': 'custom'}).status_code == 400
        assert client.put('/api/settings/ai', json={
            'provider': 'custom', 'base_url': 'https://user:password@example.test/v1'
        }).status_code == 422
        assert settings.ai_provider == 'openai'
    finally:
        client.close()


def test_switching_provider_clears_previous_token_prices(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / 'data',
                        ai_tagging_input_usd_per_million=2,
                        ai_tagging_output_usd_per_million=8)
    monkeypatch.setattr(main, 'settings', settings)
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        response = client.put('/api/settings/ai', json={
            'provider': 'custom', 'base_url': 'http://localhost:1234/v1',
        })
        assert response.status_code == 200
        assert response.json()['ai_tagging_input_usd_per_million'] == 0
        assert response.json()['ai_tagging_output_usd_per_million'] == 0
    finally:
        client.close()


def test_unsaved_custom_connection_and_model_discovery(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / 'data')
    monkeypatch.setattr(main, 'settings', settings)
    sdk = MagicMock()
    sdk.__enter__.return_value = sdk
    sdk.models.list.return_value = SimpleNamespace(data=[SimpleNamespace(id='model-b'),
                                                    SimpleNamespace(id='model-a')])
    constructor = MagicMock(return_value=sdk)
    monkeypatch.setattr(main, 'OpenAI', constructor)
    monkeypatch.setattr(main, '_provider_model_prices', lambda *_: {
        'model-a': {'input_usd_per_million': 0.4, 'output_usd_per_million': 1.6},
        'hidden-model': {'input_usd_per_million': 1, 'output_usd_per_million': 2},
    })
    provider = MagicMock()
    monkeypatch.setattr(main, 'make_ai_provider', lambda _: provider)
    candidate = {'provider': 'custom', 'base_url': 'https://example.test/v1',
                 'api_key': 'unsaved-secret', 'ai_tagging_model': 'model-a'}
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        response = client.post('/api/settings/ai/models', json=candidate)
        assert response.status_code == 200
        assert response.json() == {'models': ['model-a', 'model-b'], 'prices': {
            'model-a': {'input_usd_per_million': 0.4, 'output_usd_per_million': 1.6},
        }}
        assert constructor.call_args.kwargs['base_url'] == 'https://example.test/v1'
        result = client.post('/api/settings/ai/test', json=candidate)
        assert result.status_code == 200
        assert result.json()['model'] == 'model-a'
        assert provider.generate.call_args.kwargs['model'] == 'model-a'
        assert settings.ai_provider == 'openai'
        assert not (settings.data_dir / 'ai-settings.json').exists()
        assert 'unsaved-secret' not in response.text + result.text
    finally:
        client.close()


def test_saved_key_not_sent_to_unsaved_custom_url(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / 'data', ai_provider='custom',
                        ai_base_url='https://original.test/v1', ai_custom_api_key='saved-secret')
    monkeypatch.setattr(main, 'settings', settings)
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        response = client.post('/api/settings/ai/models', json={
            'provider': 'custom', 'base_url': 'https://different.test/v1',
        })
        assert response.status_code == 400
        assert response.json()['detail']['code'] == 'missing_api_key'
    finally:
        client.close()


def test_provider_model_prices_are_scaled_and_incomplete_rates_ignored(monkeypatch):
    import httpx

    response = httpx.Response(200, json={'data': [
        {'model_name': 'priced', 'model_info': {
            'input_cost_per_token': 0.0000004, 'output_cost_per_token': 0.0000016}},
        {'model_name': 'unknown', 'model_info': {'input_cost_per_token': 0.0000004}},
        {'model_name': 'invalid', 'model_info': {
            'input_cost_per_token': -1, 'output_cost_per_token': 0.000001}},
    ]})
    sdk = MagicMock()
    sdk.__enter__.return_value = sdk
    sdk.get.return_value = response
    monkeypatch.setattr(main.httpx, 'Client', MagicMock(return_value=sdk))
    prices = main._provider_model_prices('https://example.test/v1', 'test-secret')
    assert prices == {'priced': {'input_usd_per_million': 0.4,
                                 'output_usd_per_million': 1.6}}
    assert sdk.get.call_args.args[0] == 'https://example.test/v1/model/info'
    assert sdk.get.call_args.kwargs['headers']['Authorization'] == 'Bearer test-secret'


def test_missing_model_info_endpoint_keeps_manual_pricing_available(monkeypatch):
    import httpx

    sdk = MagicMock()
    sdk.__enter__.return_value = sdk
    sdk.get.return_value = httpx.Response(404)
    monkeypatch.setattr(main.httpx, 'Client', MagicMock(return_value=sdk))
    assert main._provider_model_prices('https://example.test/v1', 'test-secret') == {}
