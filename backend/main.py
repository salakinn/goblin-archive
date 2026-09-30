from __future__ import annotations

import json
import logging
import os
import asyncio
import hashlib
import hmac
import secrets
import time
import math
import re
import uuid
import sqlite3
import zipfile
from importlib.metadata import PackageNotFoundError, version as package_version
from datetime import datetime, timezone
from threading import Lock
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
import httpx

from backend.config import get_settings
from backend.backup import archive_lock, BackupError, recover_interrupted_restore
from backend.backup_jobs import BackupJobs
from backend.author_normalization import corrected_author_names
from backend.ai_config import AIConfigUpdate, public_config, save_ai_config
from backend.ai_audit import audit_error, audit_hooks
from backend.ai import (AIError, TAGGING_VERSION, TITLE_NORMALIZATION_VERSION, TaggingOutput,
                        generate_tags, make_ai_provider, normalize_book_title, tagging_context,
                        tagging_fingerprint)
from backend.metadata import normalize_language, normalize_tag_name, sanitize_component
from backend.metadata_store import MetadataConflict, persist_metadata, recover_metadata
from backend.models import MetadataSourceValue, Tag, TranslationGlossary
from backend.usage import records as ai_usage_records, summary as ai_usage_summary
from backend.usage import AILimitError, expire_reservations, fail as fail_ai_usage
from backend.usage import finish as finish_ai_usage, reserve as reserve_ai_usage
from backend.language import (VERSION as LANGUAGE_VERSION, TextExtractionError,
                              agreed_language, detect_language, extract_language_samples,
                              language_fingerprint, local_assessment, local_fingerprint,
                              LOCAL_VERSION)
from backend.covers import make_cover_service
from backend.database import SessionLocal, get_db, init_db
from backend.imports import ArchiveBusyError, ImportManager
from backend.previews import PreviewConflict, PreviewManager
from backend.duplicate_scanner import DuplicateScanner
from backend.isbn import canonical_isbn13, make_isbn_resolver
from backend.providers import make_provider_chain
from backend.repository import (
    add_book_tag,
    book_to_dict,
    get_book,
    list_authors,
    list_books,
    remove_book_tag,
    search_books,
    count_books,
    cached_filter_options,
    invalidate_filter_cache,
    replace_book_fts,
    get_or_create_author,
)
from backend.translation import TranslationManager, TranslationError
from backend.updater import UpdateError, install_update, update_status
from backend import auth
from openai import APIConnectionError, APIStatusError, APITimeoutError, DefaultHttpxClient, OpenAI

settings = get_settings()
ai_tagging_lock = Lock()
_preview_title_secret = secrets.token_bytes(32)


def _write_upload_chunk(path: Path, chunk: bytes, first: bool) -> None:
    mode = "xb" if first else "ab"
    with path.open(mode) as target:
        target.write(chunk)


def configure_logging() -> None:
    settings.ensure_directories()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(settings.logs_dir / "goblin.log", encoding="utf-8"),
        ],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    with archive_lock(settings.data_dir):
        recover_interrupted_restore(settings.data_dir.resolve())
        configure_logging()
        app.state.staging_lock = asyncio.Lock()
        app.state.backup_jobs = BackupJobs(settings.data_dir)
        auth.ensure_setup_code(settings.data_dir.resolve())
        init_db()
        recover_metadata(settings, SessionLocal)
        expire_reservations(SessionLocal)
        chain = make_provider_chain(settings.providers, settings.provider_timeout)
        cover_service = make_cover_service(settings.provider_timeout)
        isbn_resolver = make_isbn_resolver(settings, SessionLocal, settings.provider_timeout)
        app.state.import_manager = ImportManager(
            settings, SessionLocal, chain, cover_service, isbn_resolver,
        )
        async def postprocess_step(book_id: str, step: str) -> str:
            manager: ImportManager = app.state.import_manager
            def run_service(service):
                with SessionLocal() as service_db:
                    return service(book_id, service_db)
            with SessionLocal() as db:
                book = get_book(db, book_id)
                if book is None:
                    return "Buch wurde entfernt"
                if step == "isbn":
                    if book.isbn:
                        return "ISBN vorhanden"
                elif step == "language" and book.language:
                    return "Sprache vorhanden"
                elif step == "tags" and not (settings.openai_api_key.get_secret_value() or
                                              settings.ai_custom_api_key.get_secret_value()):
                    return "Tagging übersprungen: KI nicht eingerichtet"
            if step == "isbn":
                result = await manager.search_isbn(book_id)
                return ("ISBN erkannt" if result.get("auto_applied") else
                        "Werkreferenz erkannt" if result.get("reference_applied") else
                        "Keine eindeutige ISBN")
            if step == "authors":
                result = await asyncio.to_thread(run_service, _normalize_book_authors)
                return "Autoren abgeglichen" if result.get("changed") else "Autoren unverändert"
            if step == "language":
                result = await asyncio.to_thread(run_service, _detect_book_language)
                return "Sprache erkannt" if result.get("applied") else "Sprache nicht eindeutig"
            if step == "tags":
                result = await asyncio.to_thread(run_service, _generate_book_tags)
                return f"{result.get('added', 0)} Tags ergänzt"
            raise ValueError(f"Unbekannter Nachbearbeitungsschritt: {step}")
        app.state.import_manager.postprocess_step = postprocess_step
        app.state.import_manager.ai_busy = ai_tagging_lock.locked
        app.state.preview_manager = PreviewManager(app.state.import_manager)
        app.state.import_manager.preview_manager = app.state.preview_manager
        app.state.preview_manager.resume()
        app.state.duplicate_scanner = DuplicateScanner(settings, SessionLocal)
        async def expire_previews():
            while True:
                await asyncio.sleep(300)
                if not app.state.backup_jobs.maintenance:
                    await asyncio.to_thread(app.state.preview_manager.cleanup)
        cleanup_task = asyncio.create_task(expire_previews())
        app.state.translation_manager = TranslationManager(settings, SessionLocal)
        try:
            yield
        finally:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            await app.state.duplicate_scanner.stop()
            await app.state.import_manager.stop()
            await chain.close()
            await cover_service.close()
            await isbn_resolver.close()


def current_version() -> str:
    override = os.getenv("GOBLIN_APP_VERSION")
    if override:
        return override
    try:
        return package_version("goblin-archivar")
    except PackageNotFoundError:
        return "0.1.0"


app = FastAPI(
    title="Goblin Archivar API",
    version=current_version(),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def authenticate(request: Request, call_next):
    path = request.url.path
    jobs = getattr(request.app.state, "backup_jobs", None)
    if jobs and jobs.switching and path.startswith("/api/"):
        return JSONResponse(status_code=503, content={"detail": {"message": "Archiv wird wiederhergestellt"}})
    public = {"/api/health", "/api/auth/status", "/api/auth/setup", "/api/auth/login"}
    try:
        if path.startswith("/api/") and path not in public:
            auth.require_request_auth(request, settings.data_dir.resolve())
            auth.check_csrf(request)
        elif path in {"/api/auth/setup", "/api/auth/login"}:
            auth.check_origin(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    if path == "/api/backups/restore/upload":
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > settings.max_backup_upload_bytes + 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": {"message": "Backup-Datei überschreitet das konfigurierte Größenlimit"}})
    counted = bool(jobs and path.startswith("/api/") and
                   request.method not in {"GET", "HEAD", "OPTIONS"} and
                   not path.startswith("/api/backups"))
    if counted and jobs.maintenance:
        return JSONResponse(status_code=409, content={"detail": {"message": "Archiv wird gesichert oder wiederhergestellt. Bitte später erneut versuchen."}})
    if counted:
        jobs.active_writes += 1
    try:
        response = await call_next(request)
    finally:
        if counted:
            jobs.active_writes -= 1
    if path.startswith("/api/auth/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/backups")
def backup_list(request: Request):
    return {"items": request.app.state.backup_jobs.list()}


@app.post("/api/backups", status_code=202)
async def backup_create(request: Request):
    try:
        return request.app.state.backup_jobs.create(request.app)
    except BackupError as exc:
        raise HTTPException(409, detail={"message": str(exc)}) from exc


@app.get("/api/backups/{job_id}")
def backup_detail(job_id: str, request: Request):
    try:
        return request.app.state.backup_jobs.get(job_id)
    except BackupError as exc:
        raise HTTPException(404, detail={"message": str(exc)}) from exc


@app.get("/api/backups/{job_id}/download")
def backup_download(job_id: str, request: Request):
    try:
        job = request.app.state.backup_jobs.get(job_id)
    except BackupError as exc:
        raise HTTPException(404, detail={"message": str(exc)}) from exc
    path = request.app.state.backup_jobs.root / f"{job_id}.zip"
    if job["kind"] != "backup" or job["status"] != "ready" or not path.is_file():
        raise HTTPException(404, detail={"message": "Backup-Datei nicht verfügbar"})
    return FileResponse(path, filename=f"goblin-backup-{job['created_at'][:10]}.zip",
                        media_type="application/zip", headers={"Cache-Control": "no-store"})


@app.delete("/api/backups/{job_id}")
def backup_delete(job_id: str, request: Request):
    try:
        request.app.state.backup_jobs.delete(job_id)
    except BackupError as exc:
        raise HTTPException(409, detail={"message": str(exc)}) from exc
    return {"deleted": True}


@app.delete("/api/backups/{job_id}/previous")
def backup_delete_previous(job_id: str, request: Request):
    try:
        request.app.state.backup_jobs.delete_previous(job_id)
    except (BackupError, OSError) as exc:
        raise HTTPException(409, detail={"message": str(exc)}) from exc
    return {"deleted": True}


@app.post("/api/backups/restore/upload", status_code=201)
async def backup_upload(request: Request, file: UploadFile = File(...)):
    jobs: BackupJobs = request.app.state.backup_jobs
    if jobs.busy:
        raise HTTPException(409, detail={"message": "Ein Sicherungsauftrag läuft bereits"})
    limit = settings.max_backup_upload_bytes
    path = jobs.root / f"upload-{uuid.uuid4().hex}.zip"
    size = 0
    try:
        with path.open("xb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise BackupError("Backup-Datei überschreitet das konfigurierte Größenlimit")
                target.write(chunk)
        return await asyncio.to_thread(jobs.prepared_restore, path, settings.max_backup_unpacked_bytes)
    except (BackupError, OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, detail={"message": str(exc)}) from exc


@app.post("/api/backups/{job_id}/restore", status_code=202)
async def backup_restore(job_id: str, request: Request):
    try:
        return request.app.state.backup_jobs.restore(request.app, job_id)
    except BackupError as exc:
        raise HTTPException(409, detail={"message": str(exc)}) from exc


class PasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class SetupRequest(PasswordRequest):
    code: str = Field(min_length=1, max_length=256)


def _set_session(response: Response, request: Request, token: str) -> dict:
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_SECONDS,
                        httponly=True, samesite="strict", path="/",
                        secure=settings.auth_secure_cookies or request.url.scheme == "https")
    return {"authenticated": True, "configured": True, "csrf_token": auth.csrf_token(token)}


@app.get("/api/auth/status")
def auth_status(request: Request):
    token = request.cookies.get(auth.COOKIE)
    configured = auth.initialized(settings.data_dir.resolve())
    valid = configured and auth.valid_session(settings.data_dir.resolve(), token)
    return {"configured": configured, "authenticated": valid,
            "csrf_token": auth.csrf_token(token) if valid and token else None}


@app.post("/api/auth/setup")
def auth_setup(body: SetupRequest, request: Request, response: Response):
    if auth.initialized(settings.data_dir.resolve()):
        raise HTTPException(409, detail={"message": "Anmeldung ist bereits eingerichtet."})
    token = auth.setup(settings.data_dir.resolve(), body.code, body.password,
                       request.client.host if request.client else "unknown")
    return _set_session(response, request, token)


@app.post("/api/auth/login")
def auth_login(body: PasswordRequest, request: Request, response: Response):
    token = auth.login(settings.data_dir.resolve(), body.password,
                       request.client.host if request.client else "unknown")
    return _set_session(response, request, token)


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    auth.logout(settings.data_dir.resolve(), request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"authenticated": False}


@app.exception_handler(SQLAlchemyError)
async def database_error(_request: Request, exc: SQLAlchemyError):
    logging.getLogger(__name__).exception("Database error", exc_info=exc)
    return JSONResponse(status_code=500, content={"error": {"code": "database_error", "message": "Datenbankfehler"}})


@app.exception_handler(MetadataConflict)
async def metadata_conflict(_request: Request, exc: MetadataConflict):
    return JSONResponse(status_code=409, content={"detail": {"code": "metadata_conflict", "message": str(exc)}})


@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version}


@app.get("/api/update")
def check_update():
    try:
        return update_status(settings, app.version)
    except UpdateError as exc:
        raise HTTPException(503, detail={"code": "update_check_failed", "message": str(exc)}) from exc


class InstallUpdateRequest(BaseModel):
    password: str = Field(min_length=1, max_length=500)


@app.post("/api/update/install", status_code=202)
def install_available_update(body: InstallUpdateRequest, request: Request):
    if (request.app.state.translation_manager.running or
            request.app.state.import_manager.has_active_imports() or
            request.app.state.preview_manager.busy() or
            request.app.state.import_manager.maintenance_active):
        raise HTTPException(409, detail={"code": "archive_busy", "message": "Bitte laufende Importe und Übersetzungen zuerst beenden."})
    try:
        return install_update(settings, app.version, body.password)
    except UpdateError as exc:
        raise HTTPException(400, detail={"code": "update_failed", "message": str(exc)}) from exc


@app.get("/api/settings/ai")
def ai_settings():
    return public_config(settings)


@app.put("/api/settings/ai")
def update_ai_settings(update: AIConfigUpdate):
    requested = update.model_dump(exclude_unset=True)
    switching = (("provider" in requested and requested["provider"] != settings.ai_provider) or
                 ("base_url" in requested and requested["base_url"] is not None and
                  requested["base_url"].rstrip("/") != settings.ai_base_url))
    manager = getattr(app.state, "translation_manager", None)
    if switching and manager is not None and manager.running:
        raise HTTPException(409, detail={"code": "translation_active",
                                         "message": "KI-Anbieter kann während einer laufenden Übersetzung nicht gewechselt werden."})
    try:
        return save_ai_config(settings, update)
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_ai_settings", "message": str(exc)}) from exc


def _ai_connection_settings(update: AIConfigUpdate | None):
    update = update or AIConfigUpdate()
    provider = update.provider or settings.ai_provider
    base_url = update.base_url if update.base_url is not None else settings.ai_base_url
    candidate = settings.model_copy(deep=True)
    candidate.ai_provider = provider
    candidate.ai_base_url = base_url
    if update.api_key is not None:
        if provider == "openai":
            candidate.openai_api_key = SecretStr(update.api_key)
        else:
            candidate.ai_custom_api_key = SecretStr(update.api_key)
    elif provider != settings.ai_provider or (provider == "custom" and base_url != settings.ai_base_url):
        if provider == "openai":
            candidate.openai_api_key = SecretStr("")
        else:
            candidate.ai_custom_api_key = SecretStr("")
    return candidate


def _provider_model_prices(base_url: str, key: str) -> dict[str, dict[str, float]]:
    """Read optional LiteLLM model metadata; the standard models API has no prices."""
    try:
        with httpx.Client(timeout=10, follow_redirects=False,
                          event_hooks=audit_hooks(settings)) as client:
            response = client.get(f"{base_url.rstrip('/')}/model/info",
                                  headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        if response.status_code != 200 or len(response.content) > 2_000_000:
            return {}
        data = response.json().get("data")
        if not isinstance(data, list):
            return {}
    except httpx.HTTPError as exc:
        audit_error(settings, exc)
        return {}
    except (ValueError, AttributeError):
        return {}

    def per_million(value):
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not 0 <= value <= 1 or not math.isfinite(value)):
            return None
        return round(value * 1_000_000, 6)

    prices = {}
    for item in data[:1000]:
        if not isinstance(item, dict) or not isinstance(item.get("model_name"), str):
            continue
        info = item.get("model_info")
        if not isinstance(info, dict):
            continue
        currency = info.get("currency", item.get("currency", "USD"))
        if currency != "USD":
            continue
        input_price = per_million(info.get("input_cost_per_token"))
        output_price = per_million(info.get("output_cost_per_token"))
        if input_price is not None and output_price is not None:
            prices[item["model_name"]] = {"input_usd_per_million": input_price,
                                          "output_usd_per_million": output_price}
    return prices


@app.post("/api/settings/ai/models")
def list_ai_models(update: AIConfigUpdate | None = None):
    candidate = _ai_connection_settings(update)
    key = (candidate.openai_api_key if candidate.ai_provider == "openai"
           else candidate.ai_custom_api_key).get_secret_value()
    if not key:
        raise HTTPException(400, detail={"code": "missing_api_key", "message": "Bitte einen API-Key eingeben."})
    if candidate.ai_provider == "custom" and not candidate.ai_base_url:
        raise HTTPException(400, detail={"code": "missing_base_url", "message": "Bitte eine Base URL eingeben."})
    try:
        kwargs = {"api_key": key, "timeout": 15, "max_retries": 0,
                  "http_client": DefaultHttpxClient(event_hooks=audit_hooks(candidate))}
        if candidate.ai_provider == "custom":
            kwargs["base_url"] = candidate.ai_base_url
        with OpenAI(**kwargs) as client:
            models = client.models.list()
        names = sorted({model.id for model in models.data if model.id})[:200]
        prices = (_provider_model_prices(candidate.ai_base_url, key)
                  if candidate.ai_provider == "custom" else {})
        return {"models": names, "prices": {name: prices[name] for name in names if name in prices}}
    except (APITimeoutError, APIConnectionError) as exc:
        audit_error(candidate, exc)
        raise HTTPException(503, detail={"code": "ai_unavailable", "message": "Der KI-Anbieter ist nicht erreichbar."}) from exc
    except APIStatusError as exc:
        message = {401: "Der API-Key ist ungültig.", 403: "Kein Zugriff auf die Modellliste.",
                   404: "Dieser Dienst bietet keine Modellliste an. Bitte den Modellnamen manuell eingeben."}.get(
                       exc.status_code, "Modellliste konnte nicht geladen werden.")
        raise HTTPException(503, detail={"code": "ai_models_failed", "message": message}) from exc


@app.post("/api/settings/ai/test")
def test_ai_settings(update: AIConfigUpdate | None = None):
    candidate = _ai_connection_settings(update)
    key = (candidate.openai_api_key if candidate.ai_provider == "openai"
           else candidate.ai_custom_api_key).get_secret_value()
    if not key:
        raise HTTPException(400, detail={"code": "missing_api_key", "message": "Bitte einen API-Key eingeben."})
    model_name = update.ai_tagging_model if update and update.ai_tagging_model else candidate.ai_tagging_model
    try:
        make_ai_provider(candidate).generate(
            model=model_name, instructions="Liefere eine leere Tagliste.",
            context={"test": True}, schema=TaggingOutput, max_output_tokens=100, timeout=15)
        return {"ok": True, "model": model_name}
    except AIError as exc:
        raise HTTPException(503, detail={"code": "ai_test_failed", "message": str(exc)}) from exc


@app.get("/api/books")
def books(
    author: str | None = None,
    tag: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    language: str | None = None,
    publisher: str | None = None,
    format: str | None = None,
    series: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    filters = dict(author=author, tag=tag, year_from=year_from, year_to=year_to,
                   language=language, publisher=publisher, format=format, series=series)
    result = list_books(
        db, author=author, tag=tag, year_from=year_from, year_to=year_to,
        language=language, publisher=publisher, format=format, series=series,
        limit=limit, offset=offset,
    )
    return {"items": [book_to_dict(book) for book in result], "total": count_books(db, **filters),
            "limit": limit, "offset": offset}


@app.get("/api/authors")
def authors(db: Session = Depends(get_db)):
    items = list_authors(db)
    return {"items": items, "total": len(items)}


@app.get("/api/filter-options")
def filter_options(db: Session = Depends(get_db)):
    return cached_filter_options(db)


@app.get("/api/search")
def search(q: str = Query(min_length=1, max_length=300), db: Session = Depends(get_db)):
    result = search_books(db, q)
    return {"items": [book_to_dict(book) for book in result], "total": len(result)}


@app.get("/api/books/{book_id}")
def book_detail(book_id: str, db: Session = Depends(get_db)):
    book = get_book(db, book_id)
    if not book:
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"})
    return book_to_dict(book, detail=True)


class TranslationRequest(BaseModel):
    target_language: str
    profile: str = "buch"
    budget_usd: float | None = None
    glossary_id: int | None = None


class GlossaryUpdate(BaseModel):
    glossary: list[dict[str, str]]
    style: str = ""


class BudgetUpdate(BaseModel):
    budget_usd: float | None = None


class GlossaryResourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_language: str = Field(min_length=2, max_length=30)
    target_language: str = Field(min_length=2, max_length=30)
    glossary: list[dict[str, str]] = Field(default_factory=list, max_length=200)
    style: str = Field(default="", max_length=2000)


def _translation_error(exc):
    if isinstance(exc, KeyError):
        raise HTTPException(404, detail={"code": "not_found", "message": "Übersetzungsauftrag oder Buch nicht gefunden"}) from exc
    raise HTTPException(400, detail={"code": "translation_error", "message": str(exc)}) from exc


@app.get("/api/books/{book_id}/translations")
def list_translations(book_id: str, request: Request):
    manager = request.app.state.translation_manager
    return {"items": [manager._public(job) for job in manager.list(book_id)]}


@app.post("/api/books/{book_id}/translations")
def create_translation(book_id: str, body: TranslationRequest, request: Request):
    try:
        return request.app.state.translation_manager.create(book_id, body.target_language, body.profile, body.budget_usd, body.glossary_id)
    except (KeyError, TranslationError) as exc:
        _translation_error(exc)


@app.get("/api/translations/{job_id}")
def translation_status(job_id: str, request: Request):
    try:
        return request.app.state.translation_manager.public(job_id)
    except KeyError as exc:
        _translation_error(exc)


@app.get("/api/translations/{job_id}/segments")
def translation_segments(job_id: str, request: Request,
                         offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    try:
        return request.app.state.translation_manager.segments(job_id, offset, limit)
    except KeyError as exc:
        _translation_error(exc)


@app.put("/api/translations/{job_id}/glossary")
def update_translation_glossary(job_id: str, body: GlossaryUpdate, request: Request):
    try:
        return request.app.state.translation_manager.update_glossary(job_id, body.glossary, body.style)
    except (KeyError, TranslationError) as exc:
        _translation_error(exc)


@app.put("/api/translations/{job_id}/budget")
def update_translation_budget(job_id: str, body: BudgetUpdate, request: Request):
    try:
        return request.app.state.translation_manager.update_budget(job_id, body.budget_usd)
    except (KeyError, TranslationError) as exc:
        _translation_error(exc)


@app.post("/api/translations/{job_id}/segments/{segment_id}/repair")
def repair_translation_segment(job_id: str, segment_id: str, request: Request):
    try:
        return request.app.state.translation_manager.repair_segment(job_id, segment_id)
    except (KeyError, TranslationError) as exc:
        _translation_error(exc)
    except AILimitError as exc:
        raise HTTPException(409, detail={"code": "ai_limit", "message": str(exc)}) from exc
    except AIError as exc:
        raise HTTPException(503, detail={"code": "ai_error", "message": str(exc)}) from exc


def _usage_factory(db: Session):
    return sessionmaker(bind=db.get_bind(), expire_on_commit=False)


@app.get("/api/ai/usage")
def ai_usage(feature: str | None = None, book_id: str | None = None, db: Session = Depends(get_db)):
    return ai_usage_summary(_usage_factory(db), settings, feature=feature, book_id=book_id)


@app.get("/api/ai/usage/records")
def ai_usage_detail(limit: int = Query(100, ge=1, le=500), feature: str | None = None,
                    book_id: str | None = None, db: Session = Depends(get_db)):
    return {"items": ai_usage_records(_usage_factory(db), limit=limit, feature=feature, book_id=book_id)}


@app.get("/api/glossaries")
def list_glossaries(db: Session = Depends(get_db)):
    rows = db.scalars(select(TranslationGlossary).order_by(TranslationGlossary.name)).all()
    return {"items": [{"id": row.id, "name": row.name, "source_language": row.source_language,
                       "target_language": row.target_language, "version": row.version,
                       "glossary": json.loads(row.entries_json), "style": row.style} for row in rows]}


@app.post("/api/glossaries")
def create_glossary(body: GlossaryResourceRequest, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    row = TranslationGlossary(name=body.name, source_language=body.source_language,
                              target_language=body.target_language, version=1,
                              entries_json=json.dumps(body.glossary, ensure_ascii=False), style=body.style,
                              created_at=now, updated_at=now)
    db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "name": row.name, "source_language": row.source_language,
            "target_language": row.target_language, "version": row.version,
            "glossary": body.glossary, "style": body.style}


@app.put("/api/glossaries/{glossary_id}")
def update_glossary_resource(glossary_id: int, body: GlossaryResourceRequest, db: Session = Depends(get_db)):
    row = db.get(TranslationGlossary, glossary_id)
    if row is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "Glossar nicht gefunden"})
    row.name, row.source_language, row.target_language = body.name, body.source_language, body.target_language
    row.entries_json, row.style = json.dumps(body.glossary, ensure_ascii=False), body.style
    row.version += 1; row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": row.id, "name": row.name, "source_language": row.source_language,
            "target_language": row.target_language, "version": row.version,
            "glossary": body.glossary, "style": body.style}


@app.post("/api/translations/{job_id}/start")
def start_translation(job_id: str, request: Request):
    try:
        return request.app.state.translation_manager.start(job_id)
    except (KeyError, TranslationError) as exc:
        _translation_error(exc)


@app.post("/api/translations/{job_id}/pause")
def pause_translation(job_id: str, request: Request):
    try:
        return request.app.state.translation_manager.stop(job_id)
    except (KeyError, TranslationError) as exc:
        _translation_error(exc)


@app.post("/api/translations/{job_id}/cancel")
def cancel_translation(job_id: str, request: Request):
    try:
        return request.app.state.translation_manager.stop(job_id, cancel=True)
    except (KeyError, TranslationError) as exc:
        _translation_error(exc)


class TagSelection(BaseModel):
    name: str


class BookMetadataUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    authors: list[str] = Field(default_factory=list, max_length=30)
    publication_year: int | None = Field(default=None, ge=0, le=9999)
    language: str | None = Field(default=None, max_length=30)
    publisher: str | None = Field(default=None, max_length=300)
    isbn: str | None = Field(default=None, max_length=20)
    reference_isbn: str | None = Field(default=None, max_length=20)
    series: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=100000)


@app.get("/api/books/{book_id}/metadata-sources")
def book_metadata_sources(book_id: str, db: Session = Depends(get_db)):
    if not get_book(db, book_id):
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"})
    rows = db.scalars(select(MetadataSourceValue).where(
        MetadataSourceValue.book_id == book_id).order_by(MetadataSourceValue.id)).all()
    return {"items": [{"field": row.field, "value": json.loads(row.value_json),
                       "source": row.source, "recorded_at": row.recorded_at.isoformat()}
                      for row in rows]}


@app.put("/api/books/{book_id}/metadata")
def update_book_metadata(book_id: str, body: BookMetadataUpdate, db: Session = Depends(get_db)):
    book = get_book(db, book_id)
    if not book:
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"})
    language = normalize_language(body.language)
    if body.language and (not language or not re.fullmatch(r"[a-z]{2,3}", language)):
        raise HTTPException(400, detail={"code": "invalid_metadata", "message": "Ungültige Sprache"})
    title = body.title.strip()
    if not title:
        raise HTTPException(400, detail={"code": "invalid_metadata", "message": "Titel darf nicht leer sein"})
    if any(not name.strip() or len(name.strip()) > 300 for name in body.authors):
        raise HTTPException(400, detail={"code": "invalid_metadata", "message": "Ungültiger Autorenname"})
    authors = []
    seen = set()
    for raw in body.authors:
        name = " ".join(raw.split()).strip()
        if name and name.casefold() not in seen:
            authors.append(get_or_create_author(db, name))
            seen.add(name.casefold())
    document = json.loads(book.metadata_json)
    metadata = document.setdefault("metadata", {})
    isbn = body.isbn.strip() if body.isbn else None
    reference_isbn = body.reference_isbn.strip() if body.reference_isbn else None
    if isbn != book.isbn:
        isbn = canonical_isbn13(isbn) if isbn else None
        if body.isbn and not isbn:
            raise HTTPException(400, detail={"code": "invalid_metadata", "message": "Ungültige Ausgaben-ISBN"})
    if reference_isbn != book.reference_isbn:
        reference_isbn = canonical_isbn13(reference_isbn) if reference_isbn else None
        if body.reference_isbn and not reference_isbn:
            raise HTTPException(400, detail={"code": "invalid_metadata", "message": "Ungültige Referenz-ISBN"})
    values = {"title": title, "authors": [author.name for author in authors],
              "publication_year": body.publication_year, "language": language,
              "publisher": body.publisher.strip() if body.publisher else None,
              "isbn": isbn, "reference_isbn": reference_isbn,
              "series": body.series.strip() if body.series else None,
              "description": body.description.strip() if body.description else None}
    previous = {"title": book.title, "authors": [author.name for author in book.authors],
                "publication_year": book.publication_year, "language": normalize_language(book.language),
                "publisher": book.publisher, "isbn": book.isbn,
                "reference_isbn": book.reference_isbn, "series": book.series,
                "description": book.description}
    changed = {field for field, value in values.items() if value != previous[field]}
    if not changed:
        return book_to_dict(book, detail=True)
    for field in changed:
        metadata[field] = {"value": values[field], "source": "manual"}
    book.title = values["title"]
    book.publication_year = values["publication_year"]
    book.language = values["language"]
    book.publisher = values["publisher"]
    book.isbn = values["isbn"]
    book.reference_isbn = values["reference_isbn"]
    book.series = values["series"]
    book.description = values["description"]
    book.authors = authors
    try:
        persist_metadata(db, book, document, settings, before_commit=lambda: replace_book_fts(db, book))
    except MetadataConflict:
        db.rollback()
        raise
    invalidate_filter_cache()
    return book_to_dict(book, detail=True)


def _persist_book_tags(db: Session, book, document: dict | None = None) -> None:
    if document is None:
        document = json.loads(book.metadata_json)
    document["tags"] = [tag.name for tag in book.tags]
    current_names = {tag.normalized_name for tag in book.tags}
    if "tag_sources" in document:
        document["tag_sources"] = {
            name: source for name, source in document["tag_sources"].items()
            if name in current_names
        }
    persist_metadata(db, book, document, settings)
    invalidate_filter_cache()


@app.post("/api/books/{book_id}/tags")
def create_book_tag(book_id: str, selection: TagSelection, db: Session = Depends(get_db)):
    book = get_book(db, book_id)
    if not book:
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"})
    try:
        add_book_tag(db, book, selection.name)
        _persist_book_tags(db, book)
    except MetadataConflict:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, detail={"code": "invalid_tag", "message": str(exc)}) from exc
    invalidate_filter_cache()
    return book_to_dict(get_book(db, book_id), detail=True)


def _generate_book_tags(book_id: str, db: Session):
    if not ai_tagging_lock.acquire(blocking=False):
        raise HTTPException(409, detail={"message": "Eine KI-Anfrage läuft bereits. Bitte kurz warten."})
    try:
        book = get_book(db, book_id)
        if not book:
            raise HTTPException(404, detail={"message": "Buch nicht gefunden"})
        context = tagging_context(book)
        fingerprint = tagging_fingerprint(context, settings)
        document = json.loads(book.metadata_json)
        if document.get("ai_tagging", {}).get("fingerprint") == fingerprint:
            return {"book": book_to_dict(book, detail=True), "added": 0, "cached": True}
        evidence_fields = ("title", "filename_hint", "description", "series", "isbn", "genres", "work_hints")
        placeholders = {"unbekannter titel", "unbekannt", "unknown title", "unknown"}
        has_book_identity = any(
            value and (not isinstance(value, str) or value.strip().casefold() not in placeholders)
            for field in evidence_fields
            for value in (context.get(field) if isinstance(context.get(field), list)
                          else [context.get(field)])
        )
        if not has_book_identity and not any(context["authors"]):
            return {"book": book_to_dict(book, detail=True), "added": 0, "cached": False,
                    "message": "Zu wenig lesbare Buchdaten für passende KI-Tags."}
        sources = document.get("tag_sources", {})
        stale_ai_names = {
            name for name, source in sources.items()
            if isinstance(source, dict) and source.get("source") == "ai"
            and source.get("prompt_version") != TAGGING_VERSION
        }
        context["existing_tags"] = [tag.name for tag in book.tags[:100]
                                    if tag.normalized_name not in stale_ai_names]
        # Do not hold a database transaction across the network request.
        db.rollback()
        usage_factory = _usage_factory(db)
        try:
            usage_id = reserve_ai_usage(
                usage_factory, settings, feature="tagging", model=settings.ai_tagging_model,
                estimated_input_tokens=len(json.dumps(context, ensure_ascii=False).encode("utf-8")) + 1500,
                max_output_tokens=1200,
                rate=(settings.ai_tagging_input_usd_per_million, settings.ai_tagging_output_usd_per_million),
                book_id=book_id)
        except AILimitError as exc:
            raise HTTPException(409, detail={"code": "ai_limit", "message": str(exc)}) from exc
        try:
            result = generate_tags(make_ai_provider(settings), settings, context)
        except AIError as exc:
            fail_ai_usage(usage_factory, usage_id)
            raise HTTPException(503, detail={"code": "ai_error", "message": str(exc)}) from exc
        except Exception:
            fail_ai_usage(usage_factory, usage_id)
            raise
        finish_ai_usage(usage_factory, usage_id, input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens, usage_known=result.usage_known)
        db.expire_all()
        book = get_book(db, book_id)
        if not book:
            raise HTTPException(404, detail={"message": "Buch wurde inzwischen entfernt"})
        if tagging_fingerprint(tagging_context(book), settings) != fingerprint:
            raise HTTPException(409, detail={"message": "Buchdaten wurden inzwischen geändert. Bitte erneut versuchen."})
        document = json.loads(book.metadata_json)
        sources = document.setdefault("tag_sources", {})
        removed = 0
        for tag in list(book.tags):
            provenance = sources.get(tag.normalized_name, {})
            if not isinstance(provenance, dict):
                provenance = {}
            if provenance.get("source") == "ai" and provenance.get("prompt_version") != TAGGING_VERSION:
                remove_book_tag(db, book, tag.id)
                sources.pop(tag.normalized_name, None)
                removed += 1
        existing = {tag.normalized_name for tag in book.tags}
        timestamp = datetime.now(timezone.utc).isoformat()
        added = 0
        for tag in result.value.tags:
            normalized = normalize_tag_name(tag.name)
            if normalized in existing:
                continue
            add_book_tag(db, book, tag.name)
            existing.add(normalized)
            sources[normalized] = {
                "source": "ai", "provider": settings.ai_provider,
                "model": settings.ai_tagging_model, "created_at": timestamp,
                "reason": tag.reason, "prompt_version": TAGGING_VERSION,
            }
            added += 1
        document["ai_tagging"] = {
            "fingerprint": fingerprint, "provider": settings.ai_provider,
            "model": settings.ai_tagging_model, "created_at": timestamp,
            "prompt_version": TAGGING_VERSION,
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        }
        _persist_book_tags(db, book, document)
        return {"book": book_to_dict(book, detail=True), "added": added, "removed": removed, "cached": False}
    finally:
        ai_tagging_lock.release()


@app.post("/api/books/{book_id}/ai/tags")
def generate_book_tags(book_id: str, db: Session = Depends(get_db)):
    return _generate_book_tags(book_id, db)


@app.post("/api/books/{book_id}/ai/title")
def normalize_book_title_route(book_id: str, db: Session = Depends(get_db)):
    if not ai_tagging_lock.acquire(blocking=False):
        raise HTTPException(409, detail={"message": "Eine KI-Anfrage läuft bereits. Bitte kurz warten."})
    try:
        book = get_book(db, book_id)
        if not book:
            raise HTTPException(404, detail={"message": "Buch nicht gefunden"})
        document = json.loads(book.metadata_json)
        title_field = document.get("metadata", {}).get("title", {})
        if title_field.get("source") in {"manual", "user"} or title_field.get("confirmed"):
            return {"book": book_to_dict(book, detail=True), "changed": False, "cached": False,
                    "message": "Der manuell gepflegte Titel bleibt unverändert."}
        original_title = book.title
        context = {
            "title": original_title,
            "authors": [author.name for author in book.authors[:20]],
            "publication_year": book.publication_year,
            "language": book.language,
            "publisher": book.publisher,
            "isbn": book.isbn,
            "reference_isbn": book.reference_isbn,
            "series": book.series,
            "work_match": book.work_match_json,
            "filename_hint": book.original_filename,
        }
        def fingerprint_for(value):
            payload = [value, settings.ai_provider, settings.ai_base_url, settings.ai_tagging_model,
                       TITLE_NORMALIZATION_VERSION]
            return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

        fingerprint = fingerprint_for(context)
        previous = document.get("ai_title_normalization", {})
        if fingerprint in {previous.get("fingerprint"), previous.get("normalized_fingerprint")}:
            return {"book": book_to_dict(book, detail=True), "changed": False, "cached": True,
                    "message": "Dieser Titel wurde bereits bereinigt."}

        db.rollback()
        usage_factory = _usage_factory(db)
        try:
            usage_id = reserve_ai_usage(
                usage_factory, settings, feature="title-normalization", model=settings.ai_tagging_model,
                estimated_input_tokens=len(json.dumps(context, ensure_ascii=False).encode("utf-8")) + 800,
                max_output_tokens=220,
                rate=(settings.ai_tagging_input_usd_per_million, settings.ai_tagging_output_usd_per_million),
                book_id=book_id)
        except AILimitError as exc:
            raise HTTPException(409, detail={"code": "ai_limit", "message": str(exc)}) from exc
        try:
            result = normalize_book_title(make_ai_provider(settings), settings, context)
        except AIError as exc:
            fail_ai_usage(usage_factory, usage_id)
            raise HTTPException(503, detail={"code": "ai_error", "message": str(exc)}) from exc
        except Exception:
            fail_ai_usage(usage_factory, usage_id)
            raise
        finish_ai_usage(usage_factory, usage_id, input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens, usage_known=result.usage_known)
        db.expire_all()
        book = get_book(db, book_id)
        if not book:
            raise HTTPException(404, detail={"message": "Buch wurde inzwischen entfernt"})
        document = json.loads(book.metadata_json)
        current_title_field = document.get("metadata", {}).get("title", {})
        if book.title != original_title or current_title_field != title_field:
            raise HTTPException(409, detail={"message": "Buchtitel wurde inzwischen geändert. Bitte erneut versuchen."})
        normalized_title = result.value.title
        changed = normalized_title != original_title
        timestamp = datetime.now(timezone.utc).isoformat()
        normalized_context = {**context, "title": normalized_title}
        document["ai_title_normalization"] = {
            "fingerprint": fingerprint, "provider": settings.ai_provider,
            "normalized_fingerprint": fingerprint_for(normalized_context),
            "model": settings.ai_tagging_model, "created_at": timestamp,
            "prompt_version": TITLE_NORMALIZATION_VERSION,
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "original_title": original_title, "normalized_title": normalized_title,
        }
        if changed:
            book.title = normalized_title
            document.setdefault("metadata", {})["title"] = {
                "value": normalized_title, "source": "ai", "provider": settings.ai_provider,
                "model": settings.ai_tagging_model, "created_at": timestamp,
            }
        try:
            persist_metadata(db, book, document, settings,
                             before_commit=(lambda: replace_book_fts(db, book)) if changed else None)
        except MetadataConflict as exc:
            db.rollback()
            raise HTTPException(409, detail={"message": str(exc)}) from exc
        invalidate_filter_cache()
        updated = get_book(db, book_id)
        return {"book": book_to_dict(updated, detail=True), "changed": changed, "cached": False,
                "original_title": original_title, "normalized_title": normalized_title}
    finally:
        ai_tagging_lock.release()


def _normalize_book_authors(book_id: str, db: Session):
    book = get_book(db, book_id)
    if not book:
        raise HTTPException(404, detail={"message": "Buch nicht gefunden"})
    document = json.loads(book.metadata_json)
    original = [author.name for author in book.authors]
    normalized = corrected_author_names(db, book, document)
    if not normalized:
        return {"book": book_to_dict(book, detail=True), "changed": False,
                "message": "Keine sichere Namenskorrektur aus einem passenden Katalogtreffer möglich."}
    match = json.loads(book.work_match_json)
    source = "+".join(match.get("sources", [])) or "catalogue"
    book.authors = [get_or_create_author(db, name) for name in normalized]
    timestamp = datetime.now(timezone.utc).isoformat()
    document.setdefault("metadata", {})["authors"] = {
        "value": normalized, "source": f"work_match:{source}",
        "original_value": original, "confidence": match.get("confidence"),
    }
    document["author_normalization"] = {
        "original_names": original, "normalized_names": normalized,
        "source": source, "confidence": match.get("confidence"),
        "created_at": timestamp,
    }
    try:
        persist_metadata(db, book, document, settings,
                         before_commit=lambda: replace_book_fts(db, book))
    except MetadataConflict as exc:
        db.rollback()
        raise HTTPException(409, detail={"message": str(exc)}) from exc
    invalidate_filter_cache()
    return {"book": book_to_dict(get_book(db, book_id), detail=True), "changed": True,
            "original_authors": original, "normalized_authors": normalized}


@app.post("/api/books/{book_id}/authors/normalize")
def normalize_book_authors_route(book_id: str, db: Session = Depends(get_db)):
    return _normalize_book_authors(book_id, db)


@app.delete("/api/books/{book_id}/tags/{tag_id}")
def delete_book_tag(book_id: str, tag_id: int, db: Session = Depends(get_db)):
    book = get_book(db, book_id)
    if not book:
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"})
    try:
        remove_book_tag(db, book, tag_id)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "Tag nicht gefunden"}) from exc
    _persist_book_tags(db, book)
    invalidate_filter_cache()
    return book_to_dict(get_book(db, book_id), detail=True)


def _safe_library_file(relative_path: str) -> Path:
    candidate = (settings.library_dir / relative_path).resolve()
    try:
        candidate.relative_to(settings.library_dir.resolve())
    except ValueError as exc:
        raise HTTPException(404, detail="Datei nicht gefunden") from exc
    if not candidate.is_file():
        raise HTTPException(404, detail="Datei nicht gefunden")
    return candidate


def _detect_book_language(book_id: str, db: Session):
    if not ai_tagging_lock.acquire(blocking=False):
        raise HTTPException(409, detail={"message": "Eine KI-Anfrage läuft bereits. Bitte kurz warten."})
    try:
        book = get_book(db, book_id)
        if not book:
            raise HTTPException(404, detail={"message": "Buch nicht gefunden"})
        document = json.loads(book.metadata_json)
        previous_field = document.get("metadata", {}).get("language", {})
        if previous_field.get("source") in {"manual", "user"} or previous_field.get("confirmed"):
            return {"book": book_to_dict(book, detail=True), "status": "protected",
                    "applied": False, "cached": False}
        previous_language = book.language
        path = _safe_library_file(book.library_path)
        file_format, file_hash = book.format, book.sha256
        before = path.stat()
        file_signature = (before.st_size, before.st_mtime_ns, before.st_ino)
        db.rollback()
        try:
            samples = extract_language_samples(path, file_format)
        except TextExtractionError:
            samples = []
        if len(samples) < 3:
            book = get_book(db, book_id)
            if not book:
                raise HTTPException(404, detail={"message": "Buch wurde inzwischen entfernt"})
            return {"book": book_to_dict(book, detail=True),
                    "status": "insufficient_text", "applied": False, "cached": False}
        local_key = local_fingerprint(samples)
        fingerprint = language_fingerprint(samples, settings)
        last = document.get("language_detection", {})
        if (last.get("local_fingerprint") == local_key and last.get("cacheable")
                and (last.get("source") == "local" or last.get("fingerprint") == fingerprint)):
            book = get_book(db, book_id)
            if not book:
                raise HTTPException(404, detail={"message": "Buch wurde inzwischen entfernt"})
            return {"book": book_to_dict(book, detail=True),
                    "status": last["status"], "applied": False, "cached": True}
        local_language, local_scores, fallback_reason = local_assessment(samples)
        language = local_language
        source = "local"
        assessments = local_scores
        result = None
        fallback_error = None
        configured = (bool(settings.openai_api_key.get_secret_value()) if settings.ai_provider == "openai"
                      else bool(settings.ai_custom_api_key.get_secret_value() and settings.ai_base_url))
        if not local_language and settings.ai_language_fallback_enabled and configured:
            source = "ai"
            db.rollback()
            usage_factory = _usage_factory(db)
            try:
                usage_id = reserve_ai_usage(
                    usage_factory, settings, feature="language-detection", model=settings.ai_language_model,
                    estimated_input_tokens=sum(len(sample.text.encode("utf-8")) for sample in samples) + 1500,
                    max_output_tokens=1200,
                    rate=(settings.ai_language_input_usd_per_million, settings.ai_language_output_usd_per_million),
                    book_id=book_id)
                try:
                    result = detect_language(make_ai_provider(settings), settings, samples)
                except Exception:
                    fail_ai_usage(usage_factory, usage_id)
                    raise
                finish_ai_usage(usage_factory, usage_id, input_tokens=result.input_tokens,
                                output_tokens=result.output_tokens, usage_known=result.usage_known)
                language = agreed_language(result.value)
                assessments = result.value.model_dump()["samples"]
            except (AIError, AILimitError) as exc:
                fallback_error = str(exc)
        elif not local_language:
            fallback_error = ("KI-Fallback deaktiviert" if not settings.ai_language_fallback_enabled
                              else "Keine KI-Anbindung eingerichtet")
        db.expire_all()
        book = get_book(db, book_id)
        if not book:
            raise HTTPException(404, detail={"message": "Buch wurde inzwischen entfernt"})
        document = json.loads(book.metadata_json)
        current_field = document.get("metadata", {}).get("language", {})
        try:
            after = path.stat()
        except FileNotFoundError as exc:
            raise HTTPException(409, detail={"message": "Die Buchdatei wurde inzwischen entfernt."}) from exc
        if (book.language != previous_language or current_field != previous_field
                or book.sha256 != file_hash or book.format != file_format
                or (settings.library_dir / book.library_path).resolve() != path
                or file_signature != (after.st_size, after.st_mtime_ns, after.st_ino)):
            raise HTTPException(409, detail={"message": "Buchdaten wurden inzwischen geändert. Bitte erneut versuchen."})
        status = "detected" if language else "unclear"
        timestamp = datetime.now(timezone.utc).isoformat()
        record = {
            "fingerprint": fingerprint if source == "ai" else local_key,
            "local_fingerprint": local_key, "cacheable": bool(language or result),
            "status": status, "language": language, "source": source,
            "detector_version": LOCAL_VERSION,
            "provider": settings.ai_provider if result else None,
            "model": settings.ai_language_model if result else None,
            "created_at": timestamp, "prompt_version": LANGUAGE_VERSION,
            "fallback_reason": fallback_reason, "fallback_error": fallback_error,
            "local_assessments": local_scores,
            "previous_language": previous_language, "previous_field": previous_field,
            "samples": [sample.evidence() for sample in samples],
            "assessments": assessments,
            "input_tokens": result.input_tokens if result else 0,
            "output_tokens": result.output_tokens if result else 0,
        }
        document["language_detection"] = record
        document.setdefault("language_detection_history", []).append(record)
        if language:
            book.language = language
            document.setdefault("metadata", {})["language"] = {
                "value": language, "source": source,
                "provider": settings.ai_provider if result else None,
                "model": settings.ai_language_model if result else LOCAL_VERSION,
                "created_at": timestamp,
            }
        _persist_book_tags(db, book, document)
        return {"book": book_to_dict(book, detail=True), "status": status,
                "applied": bool(language), "cached": False}
    finally:
        ai_tagging_lock.release()


@app.post("/api/books/{book_id}/ai/language")
def detect_book_language(book_id: str, db: Session = Depends(get_db)):
    return _detect_book_language(book_id, db)


@app.get("/api/books/{book_id}/download")
def download_book(book_id: str, db: Session = Depends(get_db)):
    book = get_book(db, book_id)
    if not book:
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"})
    path = _safe_library_file(book.library_path)
    media_types = {
        ".epub": "application/epub+zip",
        ".pdf": "application/pdf",
        ".mobi": "application/x-mobipocket-ebook",
        ".azw3": "application/vnd.amazon.ebook",
        ".fb2": "application/x-fictionbook+xml",
    }
    return FileResponse(
        path,
        filename=f"{sanitize_component(book.title, 'Buch', 150)}{path.suffix.lower()}",
        media_type=media_types.get(path.suffix.lower(), "application/octet-stream"),
    )


@app.get("/api/books/{book_id}/cover")
def book_cover(book_id: str, db: Session = Depends(get_db)):
    book = get_book(db, book_id)
    if not book or not book.cover_path:
        raise HTTPException(404, detail={"code": "no_cover", "message": "Kein Cover vorhanden"})
    path = _safe_library_file(book.cover_path)
    media_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    return FileResponse(path, media_type=media_types.get(path.suffix.lower(), "application/octet-stream"))


@app.post("/api/books/{book_id}/cover/refresh")
async def refresh_book_cover(book_id: str, request: Request, db: Session = Depends(get_db)):
    if not get_book(db, book_id):
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"})
    manager: ImportManager = request.app.state.import_manager
    try:
        return await manager.refresh_cover(book_id)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"}) from exc


class IsbnSelection(BaseModel):
    isbn: str


@app.get("/api/books/{book_id}/isbn/candidates")
def isbn_candidates(book_id: str, request: Request):
    manager: ImportManager = request.app.state.import_manager
    if not manager.isbn_resolver:
        return {"book_id": book_id, "candidates": []}
    try:
        return {
            "book_id": book_id,
            "candidates": manager.isbn_resolver.list_candidates(book_id),
        }
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"}) from exc


@app.post("/api/books/{book_id}/isbn/search")
async def search_book_isbn(book_id: str, request: Request, refresh: bool = False):
    manager: ImportManager = request.app.state.import_manager
    try:
        return await manager.search_isbn(book_id, force=refresh)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"}) from exc


@app.post("/api/books/{book_id}/isbn/apply")
async def apply_book_isbn(book_id: str, selection: IsbnSelection, request: Request):
    manager: ImportManager = request.app.state.import_manager
    try:
        return await manager.apply_isbn(book_id, selection.isbn)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"}) from exc
    except MetadataConflict:
        raise
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_isbn", "message": str(exc)}) from exc


@app.post("/api/books/{book_id}/isbn/reference")
async def apply_book_isbn_reference(book_id: str, selection: IsbnSelection, request: Request):
    manager: ImportManager = request.app.state.import_manager
    try:
        return await manager.apply_isbn_reference(book_id, selection.isbn)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"}) from exc
    except MetadataConflict:
        raise
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_isbn", "message": str(exc)}) from exc


async def _store_uploads(request: Request, files: list[UploadFile]) -> list[tuple[str, Path]]:
    if not files:
        raise HTTPException(400, detail={"code": "empty_upload", "message": "Keine Dateien übergeben"})
    if len(files) > settings.max_upload_files:
        raise HTTPException(413, detail={"code": "too_many_files", "message": f"Maximal {settings.max_upload_files} Dateien pro Upload erlaubt"})
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > settings.max_upload_bytes + 1024 * 1024:
        raise HTTPException(413, detail={"code": "upload_too_large", "message": "Upload überschreitet das Größenlimit"})
    settings.ensure_directories()
    stored: list[tuple[str, Path]] = []
    total_written = 0
    current_path: Path | None = None
    try:
        for upload in files:
            original_name = Path(upload.filename or "unbenannt").name
            suffix = Path(original_name).suffix.lower()[:10]
            staging_path = settings.staging_dir / f"upload-{uuid.uuid4().hex}{suffix}"
            current_path = staging_path
            written = 0
            first = True
            while chunk := await upload.read(1024 * 1024):
                written += len(chunk)
                total_written += len(chunk)
                if written > settings.max_upload_bytes or total_written > settings.max_upload_bytes:
                    raise HTTPException(413, detail={"code": "upload_too_large", "message": f"Datei überschreitet {settings.max_upload_bytes // 1024 // 1024} MiB"})
                lock = getattr(request.app.state, "staging_lock", None)
                if lock is None:
                    lock = request.app.state.staging_lock = asyncio.Lock()
                async with lock:
                    staging_size = sum(path.stat().st_size for path in settings.staging_dir.iterdir()
                                       if path.is_file())
                    if staging_size + len(chunk) > settings.max_staging_bytes:
                        raise HTTPException(507, detail={"code": "staging_full",
                                                         "message": "Der Staging-Speicher ist voll"})
                    await asyncio.to_thread(_write_upload_chunk, staging_path, chunk, first)
                first = False
            stored.append((original_name, staging_path))
            current_path = None
            await upload.close()
    except HTTPException:
        for _name, path in stored:
            path.unlink(missing_ok=True)
        if current_path:
            current_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        for _name, path in stored:
            path.unlink(missing_ok=True)
        if current_path:
            current_path.unlink(missing_ok=True)
        raise HTTPException(507, detail={"code": "staging_failed", "message": f"Upload konnte nicht gespeichert werden: {exc}"}) from exc
    return stored


@app.get("/api/import/limits")
def import_limits():
    return {"max_files": settings.max_upload_files, "max_bytes": settings.max_upload_bytes}


@app.post("/api/import", status_code=202)
async def import_files(request: Request, files: list[UploadFile] = File(...)):
    stored = await _store_uploads(request, files)
    manager: ImportManager = request.app.state.import_manager
    try:
        job = manager.create_job(stored)
    except ArchiveBusyError as exc:
        for _name, path in stored:
            path.unlink(missing_ok=True)
        raise HTTPException(409, detail={"code": "archive_busy", "message": str(exc)}) from exc
    return job.as_dict()


class PreviewEdit(BaseModel):
    revision: int
    changes: dict[str, object] = Field(default_factory=dict)
    cover_choice: str | None = None


class PreviewTitleSuggestion(BaseModel):
    revision: int
    original_title: str
    suggested_title: str
    token: str | None = None


def _preview_title_token(preview_id: str, body: PreviewTitleSuggestion, expires: int) -> str:
    payload = json.dumps([preview_id, body.revision, body.original_title,
                          body.suggested_title, expires], ensure_ascii=False, separators=(",", ":"))
    signature = hmac.new(_preview_title_secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


@app.post("/api/import/previews/{preview_id}/ai/title/suggest")
def suggest_preview_title(preview_id: str, body: PreviewTitleSuggestion, request: Request,
                          db: Session = Depends(get_db)):
    from backend.title_cleanup import clean_title
    manager: PreviewManager = request.app.state.preview_manager
    try:
        preview = manager.get(preview_id)
    except KeyError as exc:
        raise HTTPException(404, detail={"message": "Vorschau nicht gefunden"}) from exc
    title = preview["metadata"]["title"]["value"] if preview["metadata"] else None
    if preview["status"] != "ready" or preview["revision"] != body.revision or title != body.original_title:
        raise HTTPException(409, detail={"message": "Vorschau wurde inzwischen geändert"})
    if not ai_tagging_lock.acquire(blocking=False):
        raise HTTPException(409, detail={"message": "Eine KI-Anfrage läuft bereits. Bitte kurz warten."})
    try:
        context = {"title": title, "filename_hint": preview["filename"],
                   "authors": preview["metadata"].get("authors", {}).get("value") or [],
                   "isbn": preview["metadata"].get("isbn", {}).get("value"),
                   "language": preview["metadata"].get("language", {}).get("value"),
                   "series": preview["metadata"].get("series", {}).get("value")}
        db.rollback()
        usage_factory = _usage_factory(db)
        try:
            usage_id = reserve_ai_usage(
                usage_factory, settings, feature="title-normalization", model=settings.ai_tagging_model,
                estimated_input_tokens=len(json.dumps(context, ensure_ascii=False).encode("utf-8")) + 800,
                max_output_tokens=220,
                rate=(settings.ai_tagging_input_usd_per_million, settings.ai_tagging_output_usd_per_million))
        except AILimitError as exc:
            raise HTTPException(409, detail={"code": "ai_limit", "message": str(exc)}) from exc
        try:
            result = normalize_book_title(make_ai_provider(settings), settings, context)
            proposed, _ = clean_title(result.value.title)
        except (AIError, ValueError) as exc:
            fail_ai_usage(usage_factory, usage_id)
            raise HTTPException(503, detail={"code": "ai_error", "message": str(exc)}) from exc
        except Exception:
            fail_ai_usage(usage_factory, usage_id)
            raise
        finish_ai_usage(usage_factory, usage_id, input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens, usage_known=result.usage_known)
        current = manager.get(preview_id)
        if current["revision"] != body.revision or current["metadata"]["title"]["value"] != title:
            raise HTTPException(409, detail={"message": "Titel wurde während der KI-Prüfung geändert"})
        suggested = PreviewTitleSuggestion(revision=body.revision, original_title=title, suggested_title=proposed)
        return {"revision": body.revision, "original_title": title, "suggested_title": proposed,
                "token": _preview_title_token(preview_id, suggested, int(time.time()) + 900),
                "provider": settings.ai_provider, "model": settings.ai_tagging_model}
    finally:
        ai_tagging_lock.release()


@app.post("/api/import/previews/{preview_id}/ai/title/accept")
def accept_preview_title(preview_id: str, body: PreviewTitleSuggestion, request: Request):
    try:
        expires = int((body.token or "").split(".", 1)[0])
    except ValueError:
        expires = 0
    if expires < time.time() or not hmac.compare_digest(
            body.token or "", _preview_title_token(preview_id, body, expires)):
        raise HTTPException(400, detail={"message": "KI-Vorschlag ist ungültig oder abgelaufen"})
    try:
        return request.app.state.preview_manager.accept_title(
            preview_id, body.revision, body.original_title, body.suggested_title,
            settings.ai_provider, settings.ai_tagging_model)
    except PreviewConflict as exc:
        raise HTTPException(409, detail={"code": "preview_conflict", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_preview", "message": str(exc)}) from exc


@app.post("/api/import/previews", status_code=202)
async def create_previews(request: Request, files: list[UploadFile] = File(...)):
    stored = await _store_uploads(request, files)
    try:
        return {"items": request.app.state.preview_manager.create(stored)}
    except PreviewConflict as exc:
        for _, path in stored:
            path.unlink(missing_ok=True)
        raise HTTPException(409, detail={"code": "archive_busy", "message": str(exc)}) from exc
    except Exception:
        for _, path in stored:
            path.unlink(missing_ok=True)
        raise


@app.get("/api/import/previews")
def list_previews(request: Request):
    return {"items": request.app.state.preview_manager.list()}


@app.get("/api/import/previews/{preview_id}")
def get_preview(preview_id: str, request: Request):
    try:
        return request.app.state.preview_manager.get(preview_id)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "Vorschau nicht gefunden"}) from exc


@app.put("/api/import/previews/{preview_id}")
def edit_preview(preview_id: str, body: PreviewEdit, request: Request):
    try:
        return request.app.state.preview_manager.edit(preview_id, body.changes, body.revision, body.cover_choice)
    except PreviewConflict as exc:
        raise HTTPException(409, detail={"code": "preview_conflict", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_preview", "message": str(exc)}) from exc


@app.post("/api/import/previews/{preview_id}/enrich", status_code=202)
async def enrich_preview(preview_id: str, request: Request):
    manager: PreviewManager = request.app.state.preview_manager
    try:
        await manager.analyze(preview_id, enrich=True)
        return manager.get(preview_id)
    except PreviewConflict as exc:
        raise HTTPException(409, detail={"code": "preview_conflict", "message": str(exc)}) from exc


@app.post("/api/import/previews/{preview_id}/reanalyze", status_code=202)
async def reanalyze_preview(preview_id: str, request: Request):
    manager: PreviewManager = request.app.state.preview_manager
    try:
        await manager.analyze(preview_id)
        return manager.get(preview_id)
    except PreviewConflict as exc:
        raise HTTPException(409, detail={"code": "preview_conflict", "message": str(exc)}) from exc


@app.post("/api/import/previews/{preview_id}/cover/search")
async def search_preview_cover(preview_id: str, request: Request):
    try:
        return await request.app.state.preview_manager.search_external(preview_id)
    except PreviewConflict as exc:
        raise HTTPException(409, detail={"code": "preview_conflict", "message": str(exc)}) from exc


@app.get("/api/import/previews/{preview_id}/cover")
def preview_cover(preview_id: str, request: Request, choice: str = Query("selected", pattern="^(selected|embedded|external)$")):
    manager: PreviewManager = request.app.state.preview_manager
    with manager.session_factory() as db:
        from backend.models import ImportPreview
        row = db.get(ImportPreview, preview_id)
        if not row or row.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
            raise HTTPException(404, detail={"code": "no_cover", "message": "Kein Cover vorhanden"})
        raw_path = row.cover_path if choice == "selected" else getattr(row, f"{choice}_cover_path")
        raw_info = row.cover_json if choice == "selected" else getattr(row, f"{choice}_cover_json")
        if not raw_path or not raw_info:
            raise HTTPException(404, detail={"code": "no_cover", "message": "Kein Cover vorhanden"})
        path = Path(raw_path)
        if not path.is_relative_to(settings.staging_dir.resolve()) or not path.is_file():
            raise HTTPException(404, detail={"code": "no_cover", "message": "Kein Cover vorhanden"})
        return FileResponse(path, media_type=json.loads(raw_info)["mime_type"])


@app.post("/api/import/previews/{preview_id}/confirm")
async def confirm_preview(preview_id: str, request: Request):
    try:
        return await request.app.state.preview_manager.confirm(preview_id)
    except PreviewConflict as exc:
        raise HTTPException(409, detail={"code": "preview_conflict", "message": str(exc)}) from exc


class DuplicateDecisionBody(BaseModel):
    revision: int
    action: str
    book_id: str | None = None


@app.put("/api/import/previews/{preview_id}/duplicate-decision")
def decide_preview_duplicate(preview_id: str, body: DuplicateDecisionBody, request: Request):
    try:
        return request.app.state.preview_manager.decide(preview_id, body.revision,
                                                        body.action, body.book_id)
    except PreviewConflict as exc:
        raise HTTPException(409, detail={"code": "preview_conflict", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_decision", "message": str(exc)}) from exc


@app.post("/api/duplicates/scans", status_code=202)
async def start_duplicate_scan(request: Request):
    return request.app.state.duplicate_scanner.start()


@app.get("/api/duplicates/scans")
def latest_duplicate_scan(request: Request):
    return {"scan": request.app.state.duplicate_scanner.latest()}


@app.get("/api/duplicates/scans/{scan_id}")
def get_duplicate_scan(scan_id: str, request: Request):
    try:
        return request.app.state.duplicate_scanner.get(scan_id)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "scan_not_found"}) from exc


@app.get("/api/duplicates")
def list_duplicate_matches(request: Request, offset: int = 0, limit: int = 50,
                           decision: str | None = None):
    return request.app.state.duplicate_scanner.list_matches(offset=offset, limit=limit,
                                                             decision=decision)


@app.get("/api/books/{book_id}/duplicates")
def book_duplicate_matches(book_id: str, request: Request):
    try:
        return request.app.state.duplicate_scanner.matches_for_book(book_id)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found"}) from exc


@app.get("/api/duplicates/aliases")
def list_duplicate_aliases(request: Request):
    return {"items": request.app.state.duplicate_scanner.list_aliases()}


class MetadataAliasBody(BaseModel):
    field: str
    variant: str
    canonical: str


@app.put("/api/duplicates/aliases")
async def save_duplicate_alias(body: MetadataAliasBody, request: Request):
    scanner = request.app.state.duplicate_scanner
    if scanner.task and not scanner.task.done():
        raise HTTPException(409, detail={"code": "scan_running", "message": "Prüfung läuft"})
    try:
        result = scanner.save_alias(body.field, body.variant, body.canonical)
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_alias", "message": str(exc)}) from exc
    scanner.start()
    return result


@app.delete("/api/duplicates/aliases/{alias_id}")
async def delete_duplicate_alias(alias_id: int, request: Request):
    scanner = request.app.state.duplicate_scanner
    if scanner.task and not scanner.task.done():
        raise HTTPException(409, detail={"code": "scan_running", "message": "Prüfung läuft"})
    try:
        scanner.delete_alias(alias_id)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "alias_not_found"}) from exc
    scanner.start()
    return {"deleted": True}


class ArchiveDuplicateDecisionBody(BaseModel):
    left_book_id: str
    right_book_id: str
    decision: str | None


@app.put("/api/duplicates/decision")
def decide_archive_duplicate(body: ArchiveDuplicateDecisionBody, request: Request):
    try:
        return request.app.state.duplicate_scanner.decide(
            body.left_book_id, body.right_book_id, body.decision)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "match_not_found"}) from exc
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_decision", "message": str(exc)}) from exc


@app.post("/api/import/previews/{preview_id}/discard")
def discard_preview(preview_id: str, request: Request):
    try:
        return request.app.state.preview_manager.discard(preview_id)
    except PreviewConflict as exc:
        raise HTTPException(409, detail={"code": "preview_conflict", "message": str(exc)}) from exc


@app.post("/api/import/previews/{preview_id}/skip")
def skip_preview(preview_id: str, request: Request):
    try:
        return request.app.state.preview_manager.skip(preview_id)
    except PreviewConflict as exc:
        raise HTTPException(409, detail={"code": "preview_conflict", "message": str(exc)}) from exc


@app.post("/api/import/previews/{preview_id}/resume")
def resume_preview(preview_id: str, request: Request):
    try:
        return request.app.state.preview_manager.resume_one(preview_id)
    except PreviewConflict as exc:
        raise HTTPException(409, detail={"code": "preview_conflict", "message": str(exc)}) from exc


@app.delete("/api/archive")
async def delete_archive(request: Request, confirmation: str = Query(...)):
    if confirmation != "LÖSCHEN":
        raise HTTPException(
            400,
            detail={"code": "confirmation_required", "message": "Bestätigung 'LÖSCHEN' fehlt"},
        )
    manager: ImportManager = request.app.state.import_manager
    if request.app.state.translation_manager.running:
        raise HTTPException(409, detail={"code": "archive_busy", "message": "Übersetzung läuft"})
    if request.app.state.preview_manager.busy():
        raise HTTPException(409, detail={"code": "archive_busy", "message": "Vorschau wird gerade bearbeitet"})
    scanner_task = request.app.state.duplicate_scanner.task
    if scanner_task and not scanner_task.done():
        raise HTTPException(409, detail={"code": "archive_busy", "message": "Duplikatprüfung läuft"})
    try:
        return await manager.clear()
    except ArchiveBusyError as exc:
        raise HTTPException(409, detail={"code": "archive_busy", "message": str(exc)}) from exc


@app.get("/api/imports/{import_id}")
def import_status(import_id: str, request: Request):
    manager: ImportManager = request.app.state.import_manager
    job = manager.jobs.get(import_id)
    if not job:
        raise HTTPException(404, detail={"code": "not_found", "message": "Import nicht gefunden"})
    return job.as_dict()


@app.post("/api/imports/{import_id}/items/{item_id}/retry", status_code=202)
def retry_import_step(import_id: str, item_id: str, request: Request):
    manager: ImportManager = request.app.state.import_manager
    try:
        return manager.retry_postprocessing(import_id, item_id).as_dict()
    except ArchiveBusyError as exc:
        raise HTTPException(409, detail={"code": "archive_busy", "message": str(exc)}) from exc
    except KeyError as exc:
        raise HTTPException(409, detail={"code": "retry_unavailable",
                                         "message": "Für diese Datei gibt es keinen fehlgeschlagenen Nachbearbeitungsschritt"}) from exc


@app.get("/api/imports")
def list_imports(request: Request):
    manager: ImportManager = request.app.state.import_manager
    return {"items": [job.as_dict() for job in manager.jobs.values()]}


@app.get("/api/events")
def events(request: Request):
    manager: ImportManager = request.app.state.import_manager
    return StreamingResponse(
        manager.events.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/providers")
def providers():
    return {"providers": [{"name": name, "position": index + 1, "active": True} for index, name in enumerate(settings.providers)]}


static_dir = Path(__file__).parent / "static"
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
