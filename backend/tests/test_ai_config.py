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
    client_mock = MagicMock()
    client_mock.__enter__.return_value = client_mock
    client_mock.models.retrieve.return_value = SimpleNamespace(id='gpt-test')
    constructor = MagicMock(return_value=client_mock)
    monkeypatch.setattr(main, 'OpenAI', constructor)
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        result = client.post('/api/settings/ai/test')
        assert result.status_code == 200
        assert result.json() == {'ok': True, 'model': 'gpt-test'}
        assert 'sk-test-secret' not in result.text
        constructor.assert_called_once_with(api_key='sk-test-secret', timeout=15, max_retries=0)
    finally:
        client.close()
