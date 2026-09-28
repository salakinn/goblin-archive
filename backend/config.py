from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GOBLIN_", env_file=".env", extra="ignore")

    data_dir: Path = Path("goblin-data")
    provider_order: str = "lobid,openlibrary,googlebooks"
    provider_timeout: float = 5.0
    ai_provider: str = "openai"
    openai_api_key: SecretStr = SecretStr("")
    ai_tagging_model: str = "gpt-5.4-nano"
    ai_language_model: str = "gpt-5.4-nano"
    ai_translation_model: str = "gpt-5.4-mini"
    ai_translation_qa_model: str = "gpt-5.4-mini"
    ai_translation_editor_model: str = "gpt-5.4"
    ai_translation_input_usd_per_million: float = Field(default=0, ge=0)
    ai_translation_output_usd_per_million: float = Field(default=0, ge=0)
    ai_translation_qa_input_usd_per_million: float | None = Field(default=None, ge=0)
    ai_translation_qa_output_usd_per_million: float | None = Field(default=None, ge=0)
    ai_translation_editor_input_usd_per_million: float | None = Field(default=None, ge=0)
    ai_translation_editor_output_usd_per_million: float | None = Field(default=None, ge=0)
    ai_tagging_input_usd_per_million: float = Field(default=0, ge=0)
    ai_tagging_output_usd_per_million: float = Field(default=0, ge=0)
    ai_language_input_usd_per_million: float = Field(default=0, ge=0)
    ai_language_output_usd_per_million: float = Field(default=0, ge=0)
    ai_translation_timeout: float = Field(default=180.0, gt=0, le=600)
    ai_daily_limit_usd: float = Field(default=0, ge=0)
    ai_monthly_limit_usd: float = Field(default=0, ge=0)
    ai_warning_percent: int = Field(default=80, ge=1, le=100)
    epubcheck_command: str = ""
    ai_timeout: float = Field(default=30.0, gt=0, le=120)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    truenas_ws_url: str = ""
    truenas_username: str = ""
    truenas_api_key_file: Path | None = None
    truenas_ca_file: Path | None = None
    truenas_app_name: str = ""
    update_password_file: Path | None = None
    update_mode: str = ""
    update_root: Path | None = None
    update_service: str = "goblin-archive.service"
    auth_secure_cookies: bool = False
    max_upload_bytes: int = Field(default=200 * 1024 * 1024, ge=1, le=2 * 1024 * 1024 * 1024)
    max_upload_files: int = Field(default=20, ge=1, le=100)

    @property
    def library_dir(self) -> Path:
        return self.data_dir.resolve() / "library"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir.resolve() / "staging"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir.resolve() / "logs"

    @property
    def database_path(self) -> Path:
        return self.data_dir.resolve() / "goblin.db"

    @property
    def providers(self) -> list[str]:
        return [value.strip() for value in self.provider_order.split(",") if value.strip()]

    def ensure_directories(self) -> None:
        for directory in (self.data_dir.resolve(), self.library_dir, self.staging_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    from backend.ai_config import load_ai_config
    return load_ai_config(Settings())
