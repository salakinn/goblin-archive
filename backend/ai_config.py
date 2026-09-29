"""Private, runtime-editable AI configuration for the local installation."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator

from backend.config import Settings

AI_FIELDS = (
    "ai_tagging_model", "ai_language_model", "ai_translation_model",
    "ai_translation_qa_model", "ai_translation_editor_model",
    "ai_translation_input_usd_per_million", "ai_translation_output_usd_per_million",
    "ai_translation_qa_input_usd_per_million", "ai_translation_qa_output_usd_per_million",
    "ai_translation_editor_input_usd_per_million", "ai_translation_editor_output_usd_per_million",
    "ai_tagging_input_usd_per_million", "ai_tagging_output_usd_per_million",
    "ai_language_input_usd_per_million", "ai_language_output_usd_per_million",
    "ai_daily_limit_usd", "ai_monthly_limit_usd", "ai_warning_percent",
)
MODEL_FIELDS = AI_FIELDS[:5]
PRICE_FIELDS = tuple(field for field in AI_FIELDS if "_usd_per_million" in field)
CONFIG_LOCK = Lock()


class AIConfigUpdate(BaseModel):
    provider: str | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=500)
    ai_tagging_model: str | None = None
    ai_language_model: str | None = None
    ai_translation_model: str | None = None
    ai_translation_qa_model: str | None = None
    ai_translation_editor_model: str | None = None
    ai_translation_input_usd_per_million: float | None = Field(default=None, ge=0)
    ai_translation_output_usd_per_million: float | None = Field(default=None, ge=0)
    ai_translation_qa_input_usd_per_million: float | None = Field(default=None, ge=0)
    ai_translation_qa_output_usd_per_million: float | None = Field(default=None, ge=0)
    ai_translation_editor_input_usd_per_million: float | None = Field(default=None, ge=0)
    ai_translation_editor_output_usd_per_million: float | None = Field(default=None, ge=0)
    ai_tagging_input_usd_per_million: float | None = Field(default=None, ge=0)
    ai_tagging_output_usd_per_million: float | None = Field(default=None, ge=0)
    ai_language_input_usd_per_million: float | None = Field(default=None, ge=0)
    ai_language_output_usd_per_million: float | None = Field(default=None, ge=0)
    ai_daily_limit_usd: float | None = Field(default=None, ge=0)
    ai_monthly_limit_usd: float | None = Field(default=None, ge=0)
    ai_warning_percent: int | None = Field(default=None, ge=1, le=100)

    @field_validator(*MODEL_FIELDS)
    @classmethod
    def valid_model(cls, value):
        if value is not None and (len(value) > 120 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value)):
            raise ValueError("Ungültiger Modellname")
        return value

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        if value is not None and (not value.strip() or any(ord(char) < 33 for char in value)):
            raise ValueError("API-Key darf nicht leer sein oder Leerzeichen enthalten")
        return value

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, value):
        if value is not None and value not in {"openai", "custom"}:
            raise ValueError("Unbekannter KI-Anbieter")
        return value

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value):
        if value is None or value == "":
            return value
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                any(ord(char) < 33 for char in value)):
            raise ValueError("Ungültige Base URL")
        return value.rstrip("/")


def config_path(settings: Settings) -> Path:
    return settings.data_dir.resolve() / "ai-settings.json"


def public_config(settings: Settings) -> dict:
    key = settings.openai_api_key if settings.ai_provider == "openai" else settings.ai_custom_api_key
    return {"provider": settings.ai_provider, "base_url": settings.ai_base_url,
            "key_configured": bool(key.get_secret_value()),
            **{field: getattr(settings, field) for field in AI_FIELDS}}


def load_ai_config(settings: Settings) -> Settings:
    path = config_path(settings)
    if not path.exists():
        return settings
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Ungültige KI-Konfigurationsdatei")
    values = {field: data[field] for field in AI_FIELDS if field in data}
    values["ai_provider"] = AIConfigUpdate.valid_provider(data.get("provider", "openai"))
    values["ai_base_url"] = AIConfigUpdate.valid_base_url(data.get("base_url", ""))
    if "api_key" in data:
        values["openai_api_key"] = data["api_key"]
    if "custom_api_key" in data:
        values["ai_custom_api_key"] = data["custom_api_key"]
    if values["ai_provider"] == "custom" and not values["ai_base_url"]:
        raise ValueError("Für einen eigenen KI-Anbieter ist eine Base URL erforderlich")
    validated = Settings(**{**settings.model_dump(), **values})
    for field in AI_FIELDS:
        setattr(settings, field, getattr(validated, field))
    settings.openai_api_key = validated.openai_api_key
    settings.ai_provider = validated.ai_provider
    settings.ai_base_url = validated.ai_base_url
    settings.ai_custom_api_key = validated.ai_custom_api_key
    return settings


def save_ai_config(settings: Settings, update: AIConfigUpdate) -> dict:
    with CONFIG_LOCK:
        current = {field: getattr(settings, field) for field in AI_FIELDS}
        changes = update.model_dump(exclude_unset=True)
        provider = changes.pop("provider", None) or settings.ai_provider
        base_url = changes.pop("base_url", None)
        if base_url is None:
            base_url = settings.ai_base_url
        if provider == "custom" and not base_url:
            raise ValueError("Für einen eigenen KI-Anbieter ist eine Base URL erforderlich")
        openai_key = settings.openai_api_key.get_secret_value()
        custom_key = settings.ai_custom_api_key.get_secret_value()
        if "api_key" in changes:
            if provider == "openai":
                openai_key = changes.pop("api_key") or ""
            else:
                custom_key = changes.pop("api_key") or ""
        if provider != settings.ai_provider or base_url != settings.ai_base_url:
            for field in PRICE_FIELDS:
                current[field] = None if field in {
                    "ai_translation_qa_input_usd_per_million",
                    "ai_translation_qa_output_usd_per_million",
                    "ai_translation_editor_input_usd_per_million",
                    "ai_translation_editor_output_usd_per_million",
                } else 0
        if any(changes.get(field) is None for field in (*MODEL_FIELDS,
                "ai_translation_input_usd_per_million", "ai_translation_output_usd_per_million",
                "ai_tagging_input_usd_per_million", "ai_tagging_output_usd_per_million",
                "ai_language_input_usd_per_million", "ai_language_output_usd_per_million")
               if field in changes):
            raise ValueError("Modellname und Grundpreise dürfen nicht leer sein")
        current.update(changes)
        validated = Settings(**{**settings.model_dump(), **current,
                                "ai_provider": provider, "ai_base_url": base_url,
                                "openai_api_key": SecretStr(openai_key),
                                "ai_custom_api_key": SecretStr(custom_key)})
        payload = {field: getattr(validated, field) for field in AI_FIELDS}
        payload["api_key"] = validated.openai_api_key.get_secret_value()
        payload["custom_api_key"] = validated.ai_custom_api_key.get_secret_value()
        payload["provider"] = validated.ai_provider
        payload["base_url"] = validated.ai_base_url
        path = config_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".ai-settings-{uuid.uuid4().hex}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)
        for field in AI_FIELDS:
            setattr(settings, field, getattr(validated, field))
        settings.openai_api_key = validated.openai_api_key
        settings.ai_custom_api_key = validated.ai_custom_api_key
        settings.ai_provider = validated.ai_provider
        settings.ai_base_url = validated.ai_base_url
        return public_config(settings)
