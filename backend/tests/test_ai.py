import json
import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APITimeoutError, RateLimitError

from backend import ai, main
from backend.ai import AIError, AIResult, CompatibleProvider, GeneratedTag, OpenAIProvider, TaggingOutput, make_ai_provider
from backend.config import Settings
from backend.database import get_db
from backend.repository import get_book, search_books
from backend.usage import records
from backend.tests.conftest import add_book


@pytest.fixture
def tagging_api(db_context, monkeypatch):
    settings, factory = db_context
    monkeypatch.setattr(main, "settings", settings)
    with factory() as session:
        book = add_book(session)
        path = (settings.library_dir / book.library_path).parent / "metadata.json"
        path.parent.mkdir(parents=True)
        path.write_text(book.metadata_json)
    provider = MagicMock()
    provider.generate.return_value = AIResult(TaggingOutput(tags=[
        GeneratedTag(name=" fantasy ", reason="Vorhandenes Genre"),
        GeneratedTag(name="Abenteuer", reason="Beschreibung nennt ein Abenteuer"),
        GeneratedTag(name="abenteuer", reason="Doppelter Begriff"),
    ]), 100, 30)
    monkeypatch.setattr(main, "make_ai_provider", lambda _: provider)

    def override_db():
        with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = override_db
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        yield client, provider, path, factory
    finally:
        client.close()
        main.app.dependency_overrides.pop(get_db, None)


URL = "/api/books/bk_test0001/ai/tags"


def test_direct_tagging_persists_and_caches(tagging_api):
    client, provider, path, factory = tagging_api
    response = client.post(URL)
    assert response.status_code == 200, response.text
    assert response.json()["added"] == 1
    assert {tag["name"] for tag in response.json()["book"]["tags"]} == {"Fantasy", "Abenteuer"}
    document = json.loads(path.read_text())
    assert set(document["tag_sources"]) == {"abenteuer"}
    assert document["tag_sources"]["abenteuer"]["source"] == "ai"
    assert document["ai_tagging"]["input_tokens"] == 100
    with factory() as session:
        assert json.loads(get_book(session, "bk_test0001").metadata_json) == document
        assert len(search_books(session, "Abenteuer")) == 1
    assert client.post(URL).json()["cached"] is True
    assert provider.generate.call_count == 1
    tag_id = next(t["id"] for t in response.json()["book"]["tags"] if t["name"] == "Abenteuer")
    assert client.delete(f"/api/books/bk_test0001/tags/{tag_id}").status_code == 200
    assert json.loads(path.read_text())["tag_sources"] == {}
    assert client.post(URL).json()["added"] == 0
    assert provider.generate.call_count == 1


def test_tagging_uses_same_database_and_global_cost_limit(tagging_api):
    client, provider, _path, factory = tagging_api
    main.settings.ai_tagging_input_usd_per_million = 100
    main.settings.ai_tagging_output_usd_per_million = 100
    main.settings.ai_daily_limit_usd = 0.001
    assert client.post(URL).status_code == 409
    provider.generate.assert_not_called()
    main.settings.ai_daily_limit_usd = 1
    assert client.post(URL).status_code == 200
    usage = records(factory)
    assert len(usage) == 1
    assert usage[0]['cost_usd'] == 0.013
    assert usage[0]['book_id'] == 'bk_test0001'


def test_failure_leaves_metadata_unchanged_and_can_retry(tagging_api):
    client, provider, path, _ = tagging_api
    before = path.read_bytes()
    success = provider.generate.return_value
    provider.generate.side_effect = AIError("Dienst nicht erreichbar")
    response = client.post(URL)
    assert response.status_code == 503
    assert path.read_bytes() == before
    provider.generate.side_effect = None
    provider.generate.return_value = success
    assert client.post(URL).json()["added"] == 1


def test_empty_result_and_changed_context(tagging_api):
    client, provider, _, factory = tagging_api
    provider.generate.return_value = AIResult(TaggingOutput(tags=[]), 10, 5)
    assert client.post(URL).json()["added"] == 0
    assert client.post(URL).json()["cached"] is True
    with factory() as session:
        get_book(session, "bk_test0001").description = "Neue Beschreibung"
        session.commit()
    assert client.post(URL).json()["cached"] is False
    assert provider.generate.call_count == 2


def test_tagging_retries_old_empty_result_with_inference_prompt(tagging_api):
    client, provider, path, factory = tagging_api
    with factory() as session:
        book = get_book(session, "bk_test0001")
        old_context = ai.tagging_context(book)
        for field in ("publication_year", "publisher", "isbn", "genres"):
            old_context.pop(field)
        old_payload = [old_context, main.settings.ai_provider, main.settings.ai_base_url,
                       main.settings.ai_tagging_model, "1"]
        old_fingerprint = hashlib.sha256(json.dumps(old_payload, sort_keys=True).encode()).hexdigest()
        document = json.loads(book.metadata_json)
        document["ai_tagging"] = {"fingerprint": old_fingerprint}
        book.metadata_json = json.dumps(document)
        session.commit()
        path.write_text(book.metadata_json)

    response = client.post(URL)
    assert response.status_code == 200, response.text
    assert response.json()["cached"] is False
    kwargs = provider.generate.call_args.kwargs
    assert "Selbst ohne Beschreibung" in kwargs["instructions"]
    assert kwargs["context"]["title"] == "Der Hobbit"
    assert kwargs["context"]["publication_year"] == 1937


def test_missing_book_and_concurrent_request(tagging_api):
    client, provider, _, _ = tagging_api
    assert client.post("/api/books/missing/ai/tags").status_code == 404
    with main.ai_tagging_lock:
        assert client.post(URL).status_code == 409
    provider.generate.assert_not_called()


def test_changed_during_request_does_not_apply(tagging_api):
    client, provider, path, factory = tagging_api
    before = path.read_bytes()

    def change_book(**kwargs):
        with factory() as session:
            get_book(session, "bk_test0001").description = "Changed concurrently"
            session.commit()
        return AIResult(TaggingOutput(tags=[]), 0, 0)

    provider.generate.side_effect = change_book
    assert client.post(URL).status_code == 409
    assert path.read_bytes() == before


def test_failed_commit_restores_file_and_tags(tagging_api, monkeypatch):
    client, _, path, factory = tagging_api
    before = path.read_bytes()

    def fail_commit(self):
        raise RuntimeError("disk full")

    monkeypatch.setattr(factory.class_, "commit", fail_commit)
    assert client.post(URL).status_code == 500
    assert path.read_bytes() == before
    with factory() as session:
        assert [t.name for t in get_book(session, "bk_test0001").tags] == ["Fantasy"]
        assert not search_books(session, "Abenteuer")


def test_openai_request_and_refusal(monkeypatch):
    sdk = MagicMock()
    sdk.__enter__.return_value = sdk
    constructor = MagicMock(return_value=sdk)
    monkeypatch.setattr(ai, "OpenAI", constructor)
    sdk.responses.parse.return_value = SimpleNamespace(
        status="completed", output_parsed=TaggingOutput(tags=[]),
        usage=SimpleNamespace(input_tokens=9, output_tokens=3),
    )
    provider = OpenAIProvider(Settings(openai_api_key="test-key"))
    result = provider.generate(model="test-model", instructions="Rules", context={"title": "Test"}, schema=TaggingOutput)
    assert result.input_tokens == 9
    kwargs = sdk.responses.parse.call_args.kwargs
    assert kwargs["store"] is False
    assert kwargs["text_format"] is TaggingOutput
    assert kwargs["model"] == "test-model"
    assert json.loads(kwargs["input"]) == {"title": "Test"}
    sdk.responses.parse.return_value.output_parsed = None
    with pytest.raises(AIError, match="vollständiges Ergebnis"):
        provider.generate(model="test-model", instructions="", context={}, schema=TaggingOutput)


@pytest.mark.parametrize("error, message", [
    (APITimeoutError(request=httpx.Request("POST", "https://api.openai.com")), "zu lange"),
    (RateLimitError("secret provider payload", response=httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com")), body=None), "Limit"),
])
def test_provider_errors_are_safe(monkeypatch, error, message):
    sdk = MagicMock()
    sdk.__enter__.return_value = sdk
    sdk.responses.parse.side_effect = error
    monkeypatch.setattr(ai, "OpenAI", MagicMock(return_value=sdk))
    with pytest.raises(AIError, match=message):
        OpenAIProvider(Settings(openai_api_key="test-key")).generate(
            model="test", instructions="", context={}, schema=TaggingOutput)


def test_missing_api_key():
    with pytest.raises(AIError, match="Einstellungen"):
        OpenAIProvider(Settings(openai_api_key="")).generate(
            model="test", instructions="", context={}, schema=TaggingOutput)


def test_compatible_chat_completion_validates_json_and_reports_usage(monkeypatch):
    sdk = MagicMock()
    sdk.__enter__.return_value = sdk
    constructor = MagicMock(return_value=sdk)
    monkeypatch.setattr(ai, "OpenAI", constructor)
    sdk.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"tags":[]}'))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=4),
    )
    settings = Settings(ai_provider='custom', ai_base_url='https://example.test/v1',
                        ai_custom_api_key='custom-key')
    result = make_ai_provider(settings).generate(
        model='other-model', instructions='Rules', context={'title': 'Test'}, schema=TaggingOutput)
    assert isinstance(make_ai_provider(settings), CompatibleProvider)
    assert result.value.tags == [] and (result.input_tokens, result.output_tokens) == (11, 4)
    assert constructor.call_args.kwargs['base_url'] == 'https://example.test/v1'
    assert constructor.call_args.kwargs['api_key'] == 'custom-key'
    kwargs = sdk.chat.completions.create.call_args.kwargs
    assert kwargs['model'] == 'other-model'
    assert json.loads(kwargs['messages'][1]['content']) == {'title': 'Test'}
    sdk.chat.completions.create.return_value.choices[0].message.content = '{"tags":"wrong"}'
    with pytest.raises(AIError, match='ungültiges Ergebnis'):
        make_ai_provider(settings).generate(model='other-model', instructions='', context={}, schema=TaggingOutput)


def test_custom_connection_check_uses_configured_model(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, ai_provider='custom', ai_base_url='https://example.test/v1',
                        ai_custom_api_key='custom-key', ai_tagging_model='other-model')
    monkeypatch.setattr(main, 'settings', settings)
    provider = MagicMock()
    monkeypatch.setattr(main, 'make_ai_provider', lambda _: provider)
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        response = client.post('/api/settings/ai/test')
        assert response.status_code == 200
        assert response.json() == {'ok': True, 'model': 'other-model'}
        assert provider.generate.call_args.kwargs['model'] == 'other-model'
    finally:
        client.close()
