from __future__ import annotations

import json
import logging
import os
import asyncio
import re
import uuid
from importlib.metadata import PackageNotFoundError, version as package_version
from datetime import datetime, timezone
from threading import Lock
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings
from backend.ai_config import AIConfigUpdate, public_config, save_ai_config
from backend.ai import (AIError, TAGGING_VERSION, generate_tags, make_ai_provider,
                        tagging_context, tagging_fingerprint)
from backend.metadata import normalize_language, normalize_tag_name
from backend.metadata_store import MetadataConflict, persist_metadata, recover_metadata
from backend.models import Tag, TranslationGlossary
from backend.usage import records as ai_usage_records, summary as ai_usage_summary
from backend.usage import AILimitError, expire_reservations, fail as fail_ai_usage
from backend.usage import finish as finish_ai_usage, reserve as reserve_ai_usage
from backend.language import (VERSION as LANGUAGE_VERSION, TextExtractionError,
                              agreed_language, detect_language, extract_language_samples,
                              language_fingerprint)
from backend.covers import make_cover_service
from backend.database import SessionLocal, get_db, init_db
from backend.imports import ArchiveBusyError, ImportManager
from backend.previews import PreviewConflict, PreviewManager
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
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

settings = get_settings()
ai_tagging_lock = Lock()


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
    configure_logging()
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
    app.state.preview_manager = PreviewManager(app.state.import_manager)
    app.state.preview_manager.resume()
    async def expire_previews():
        while True:
            await asyncio.sleep(300)
            await asyncio.to_thread(app.state.preview_manager.cleanup)
    cleanup_task = asyncio.create_task(expire_previews())
    app.state.translation_manager = TranslationManager(settings, SessionLocal)
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
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
    public = {"/api/health", "/api/auth/status", "/api/auth/setup", "/api/auth/login"}
    try:
        if path.startswith("/api/") and path not in public:
            auth.require_request_auth(request, settings.data_dir.resolve())
            auth.check_csrf(request)
        elif path in {"/api/auth/setup", "/api/auth/login"}:
            auth.check_origin(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    response = await call_next(request)
    if path.startswith("/api/auth/"):
        response.headers["Cache-Control"] = "no-store"
    return response


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
    try:
        return save_ai_config(settings, update)
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_ai_settings", "message": str(exc)}) from exc


@app.post("/api/settings/ai/test")
def test_ai_settings():
    key = settings.openai_api_key.get_secret_value()
    if not key:
        raise HTTPException(400, detail={"code": "missing_api_key", "message": "Bitte zuerst einen OpenAI-API-Key speichern."})
    try:
        with OpenAI(api_key=key, timeout=15, max_retries=0) as client:
            model = client.models.retrieve(settings.ai_tagging_model)
        return {"ok": True, "model": model.id}
    except APITimeoutError as exc:
        raise HTTPException(503, detail={"code": "ai_timeout", "message": "OpenAI antwortet derzeit nicht."}) from exc
    except APIConnectionError as exc:
        raise HTTPException(503, detail={"code": "ai_unavailable", "message": "OpenAI ist nicht erreichbar."}) from exc
    except APIStatusError as exc:
        message = {401: "Der API-Key ist ungültig.", 403: "Kein Zugriff auf das gewählte Modell.",
                   404: "Das gewählte Modell wurde nicht gefunden.",
                   429: "OpenAI-Limit erreicht."}.get(exc.status_code, "OpenAI konnte die Verbindung nicht bestätigen.")
        raise HTTPException(503, detail={"code": "ai_test_failed", "message": message}) from exc


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


@app.post("/api/books/{book_id}/ai/tags")
def generate_book_tags(book_id: str, db: Session = Depends(get_db)):
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
        context["existing_tags"] = [tag.name for tag in book.tags[:100]]
        context["library_tags"] = list(db.scalars(select(Tag.name).order_by(Tag.normalized_name).limit(200)))
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
        return {"book": book_to_dict(book, detail=True), "added": added, "cached": False}
    finally:
        ai_tagging_lock.release()


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


@app.post("/api/books/{book_id}/ai/language")
def detect_book_language(book_id: str, db: Session = Depends(get_db)):
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
        except TextExtractionError as exc:
            raise HTTPException(422, detail={"message": str(exc)}) from exc
        if len(samples) < 3:
            book = get_book(db, book_id)
            if not book:
                raise HTTPException(404, detail={"message": "Buch wurde inzwischen entfernt"})
            return {"book": book_to_dict(book, detail=True),
                    "status": "insufficient_text", "applied": False, "cached": False}
        fingerprint = language_fingerprint(samples, settings)
        last = document.get("language_detection", {})
        if last.get("fingerprint") == fingerprint:
            book = get_book(db, book_id)
            if not book:
                raise HTTPException(404, detail={"message": "Buch wurde inzwischen entfernt"})
            return {"book": book_to_dict(book, detail=True),
                    "status": last["status"], "applied": False, "cached": True}
        db.rollback()
        usage_factory = _usage_factory(db)
        try:
            usage_id = reserve_ai_usage(
                usage_factory, settings, feature="language-detection", model=settings.ai_language_model,
                estimated_input_tokens=sum(len(sample.text.encode("utf-8")) for sample in samples) + 1500,
                max_output_tokens=1200,
                rate=(settings.ai_language_input_usd_per_million, settings.ai_language_output_usd_per_million),
                book_id=book_id)
        except AILimitError as exc:
            raise HTTPException(409, detail={"code": "ai_limit", "message": str(exc)}) from exc
        try:
            result = detect_language(make_ai_provider(settings), settings, samples)
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
        language = agreed_language(result.value)
        status = "detected" if language else "unclear"
        timestamp = datetime.now(timezone.utc).isoformat()
        record = {
            "fingerprint": fingerprint, "status": status, "language": language,
            "provider": settings.ai_provider, "model": settings.ai_language_model,
            "created_at": timestamp, "prompt_version": LANGUAGE_VERSION,
            "previous_language": previous_language, "previous_field": previous_field,
            "samples": [sample.evidence() for sample in samples],
            "assessments": result.value.model_dump()["samples"],
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        }
        document["language_detection"] = record
        document.setdefault("language_detection_history", []).append(record)
        if language:
            book.language = language
            document.setdefault("metadata", {})["language"] = {
                "value": language, "source": "ai", "provider": settings.ai_provider,
                "model": settings.ai_language_model, "created_at": timestamp,
            }
        _persist_book_tags(db, book, document)
        return {"book": book_to_dict(book, detail=True), "status": status,
                "applied": bool(language), "cached": False}
    finally:
        ai_tagging_lock.release()


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
    }
    return FileResponse(
        path,
        filename=book.original_filename,
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
