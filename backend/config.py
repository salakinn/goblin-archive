from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GOBLIN_", env_file=".env", extra="ignore")

    data_dir: Path = Path("goblin-data")
    provider_order: str = "lobid,openlibrary,googlebooks"
    provider_timeout: float = 5.0
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

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
    return Settings()

