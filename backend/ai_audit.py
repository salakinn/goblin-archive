"""Private JSONL record of outbound AI HTTP requests and inbound responses."""
from __future__ import annotations

import fcntl
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from backend.config import Settings


SENSITIVE = ("authorization", "cookie", "secret", "password", "api_key",
             "apikey", "credential")


def _sensitive(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    return (normalized == "token" or normalized.endswith("_token") or
            normalized in {"auth", "x_auth"} or any(part in normalized for part in SENSITIVE))


def _redact(value):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _sensitive(key) else _redact(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _body(content: bytes):
    try:
        return _redact(json.loads(content))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return content.decode("utf-8", errors="replace")


def _url(value: str) -> str:
    parsed = urlsplit(value)
    query = urlencode([(key, "[REDACTED]" if _sensitive(key) else val)
                       for key, val in parse_qsl(parsed.query, keep_blank_values=True)])
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def _headers(headers: httpx.Headers) -> dict[str, str]:
    return {key: "[REDACTED]" if _sensitive(key) else value
            for key, value in headers.items()}


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = (json.dumps({"at": datetime.now(timezone.utc).isoformat(), **record},
                       ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "ab", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def audit_hooks(settings: Settings) -> dict[str, list]:
    """httpx hooks shared by SDK calls and direct provider requests."""
    path = settings.logs_dir / "ai-requests.jsonl"

    def request_hook(request: httpx.Request) -> None:
        request_id = uuid.uuid4().hex
        request.extensions["goblin_ai_request_id"] = request_id
        _append(path, {"id": request_id, "event": "request", "method": request.method,
                       "url": _url(str(request.url)), "headers": _headers(request.headers),
                       "body": _body(request.read())})

    def response_hook(response: httpx.Response) -> None:
        _append(path, {"id": response.request.extensions.get("goblin_ai_request_id"),
                       "event": "response", "status": response.status_code,
                       "headers": _headers(response.headers), "body": _body(response.read())})

    return {"request": [request_hook], "response": [response_hook]}


def audit_error(settings: Settings, exc: Exception) -> None:
    request = getattr(exc, "request", None)
    if request is None:
        return
    request_id = request.extensions.get("goblin_ai_request_id")
    if request_id:
        _append(settings.logs_dir / "ai-requests.jsonl",
                {"id": request_id, "event": "error", "type": type(exc).__name__})
