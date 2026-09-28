"""Private, runtime-editable OpenAI configuration for the local installation."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from threading import Lock

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
CONFIG_LOCK = Lock()


class AIConfigUpdate(BaseModel):
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


def config_path(settings: Settings) -> Path:
    return settings.data_dir.resolve() / "ai-settings.json"


def public_config(settings: Settings) -> dict:
    return {"provider": "openai", "key_configured": bool(settings.openai_api_key.get_secret_value()),
            **{field: getattr(settings, field) for field in AI_FIELDS}}


def load_ai_config(settings: Settings) -> Settings:
    path = config_path(settings)
    if not path.exists():
        return settings
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Ungültige KI-Konfigurationsdatei")
    values = {field: data[field] for field in AI_FIELDS if field in data}
    if "api_key" in data:
        values["openai_api_key"] = data["api_key"]
    validated = Settings(**{**settings.model_dump(), **values})
    for field in AI_FIELDS:
        setattr(settings, field, getattr(validated, field))
    settings.openai_api_key = validated.openai_api_key
    return settings


def save_ai_config(settings: Settings, update: AIConfigUpdate) -> dict:
    with CONFIG_LOCK:
        current = {field: getattr(settings, field) for field in AI_FIELDS}
        current_key = settings.openai_api_key.get_secret_value()
        changes = update.model_dump(exclude_unset=True)
        if "api_key" in changes:
            current_key = changes.pop("api_key") or ""
        if any(changes.get(field) is None for field in (*MODEL_FIELDS,
                "ai_translation_input_usd_per_million", "ai_translation_output_usd_per_million",
                "ai_tagging_input_usd_per_million", "ai_tagging_output_usd_per_million",
                "ai_language_input_usd_per_million", "ai_language_output_usd_per_million")
               if field in changes):
            raise ValueError("Modellname und Grundpreise dürfen nicht leer sein")
        current.update(changes)
        validated = Settings(**{**settings.model_dump(), **current,
                                "openai_api_key": SecretStr(current_key)})
        payload = {field: getattr(validated, field) for field in AI_FIELDS}
        payload["api_key"] = validated.openai_api_key.get_secret_value()
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
        return public_config(settings)
