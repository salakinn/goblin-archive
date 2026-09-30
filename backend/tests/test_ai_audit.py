from __future__ import annotations

import json
import stat

import httpx

from backend.ai_audit import audit_hooks
from backend import ai
from backend.ai import TaggingOutput
from backend.config import Settings


def test_ai_audit_records_request_response_and_redacts_credentials(tmp_path):
    settings = Settings(data_dir=tmp_path)

    def respond(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"tags":[]}'}}],
                                         "api_key": "response-secret"})

    with httpx.Client(transport=httpx.MockTransport(respond),
                      event_hooks=audit_hooks(settings)) as client:
        client.post("https://example.test/v1/chat/completions?api_key=query-secret",
                    headers={"Authorization": "Bearer header-secret"},
                    json={"model": "test", "messages": [{"content": "Gnadenlos"}],
                          "api_key": "body-secret"})

    path = settings.logs_dir / "ai-requests.jsonl"
    request, response = [json.loads(line) for line in path.read_text().splitlines()]
    assert request["event"] == "request" and response["event"] == "response"
    assert request["id"] == response["id"]
    assert request["method"] == "POST"
    assert request["body"]["messages"] == [{"content": "Gnadenlos"}]
    assert request["body"]["api_key"] == "[REDACTED]"
    assert response["status"] == 200
    assert response["body"]["choices"][0]["message"]["content"] == '{"tags":[]}'
    assert "secret" not in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_ai_audit_preserves_token_counts(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"usage": {"prompt_tokens": 20, "completion_tokens": 4}})),
            event_hooks=audit_hooks(settings)) as client:
        client.post("https://example.test/v1/chat/completions", json={"max_tokens": 1200})
    request, response = [json.loads(line) for line in (
        settings.logs_dir / "ai-requests.jsonl").read_text().splitlines()]
    assert request["body"]["max_tokens"] == 1200
    assert response["body"]["usage"] == {"prompt_tokens": 20, "completion_tokens": 4}


def test_ai_audit_keeps_failed_http_response(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            429, json={"error": {"message": "Rate limit"}})),
            event_hooks=audit_hooks(settings)) as client:
        response = client.get("https://example.test/v1/models")
    assert response.status_code == 429
    entries = [json.loads(line) for line in (settings.logs_dir / "ai-requests.jsonl").read_text().splitlines()]
    assert entries[1]["status"] == 429
    assert entries[1]["body"] == {"error": {"message": "Rate limit"}}


def test_compatible_provider_writes_actual_sdk_exchange(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, ai_provider="custom",
                        ai_base_url="https://example.test/v1", ai_custom_api_key="private-key")

    def client_factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"id": "completion-1", "object": "chat.completion", "model": "test",
                       "choices": [{"index": 0, "finish_reason": "stop", "message": {
                           "role": "assistant", "content": '{"tags":[]}'}}],
                       "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24}})),
            event_hooks=kwargs["event_hooks"])

    monkeypatch.setattr(ai, "DefaultHttpxClient", client_factory)
    result = ai.CompatibleProvider(settings).generate(
        model="test", instructions="Tag the book", context={"title": "Gnadenlos"},
        schema=TaggingOutput)
    assert result.value.tags == []
    entries = [json.loads(line) for line in (settings.logs_dir / "ai-requests.jsonl").read_text().splitlines()]
    assert entries[0]["url"] == "https://example.test/v1/chat/completions"
    assert entries[0]["body"]["messages"][1]["content"] == '{"title": "Gnadenlos"}'
    assert entries[1]["body"]["choices"][0]["message"]["content"] == '{"tags":[]}'
    assert "private-key" not in (settings.logs_dir / "ai-requests.jsonl").read_text()
