from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from threading import Lock
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.ai import (AIError, TAGGING_VERSION, generate_tags, make_ai_provider,
                        tagging_context, tagging_fingerprint)
from backend.metadata import normalize_tag_name
from backend.models import Tag
from backend.language import (VERSION as LANGUAGE_VERSION, TextExtractionError,
                              agreed_language, detect_language, extract_language_samples,
                              language_fingerprint)
from backend.covers import make_cover_service
from backend.database import SessionLocal, get_db, init_db
from backend.imports import ArchiveBusyError, ImportManager
from backend.isbn import make_isbn_resolver
from backend.providers import make_provider_chain
from backend.repository import (
    add_book_tag,
    book_to_dict,
    get_book,
    list_authors,
    list_books,
    list_filter_options,
    remove_book_tag,
    search_books,
)

settings = get_settings()
ai_tagging_lock = Lock()


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
    init_db()
    chain = make_provider_chain(settings.providers, settings.provider_timeout)
    cover_service = make_cover_service(settings.provider_timeout)
    isbn_resolver = make_isbn_resolver(settings, SessionLocal, settings.provider_timeout)
    app.state.import_manager = ImportManager(
        settings, SessionLocal, chain, cover_service, isbn_resolver,
    )
    yield
    await chain.close()
    await cover_service.close()
    await isbn_resolver.close()


app = FastAPI(
    title="Goblin Archivar API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SQLAlchemyError)
async def database_error(_request: Request, exc: SQLAlchemyError):
    logging.getLogger(__name__).exception("Database error", exc_info=exc)
    return JSONResponse(status_code=500, content={"error": {"code": "database_error", "message": "Datenbankfehler"}})


@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version}


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
    db: Session = Depends(get_db),
):
    result = list_books(
        db, author=author, tag=tag, year_from=year_from, year_to=year_to,
        language=language, publisher=publisher, format=format, series=series,
    )
    return {"items": [book_to_dict(book) for book in result], "total": len(result)}


@app.get("/api/authors")
def authors(db: Session = Depends(get_db)):
    items = list_authors(db)
    return {"items": items, "total": len(items)}


@app.get("/api/filter-options")
def filter_options(db: Session = Depends(get_db)):
    return list_filter_options(db)


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


class TagSelection(BaseModel):
    name: str


def _persist_book_tags(db: Session, book, document: dict | None = None) -> None:
    metadata_path = (settings.library_dir / book.library_path).parent / "metadata.json"
    old_metadata = metadata_path.read_bytes()
    if document is None:
        document = json.loads(book.metadata_json)
    document["tags"] = [tag.name for tag in book.tags]
    current_names = {tag.normalized_name for tag in book.tags}
    if "tag_sources" in document:
        document["tag_sources"] = {
            name: source for name, source in document["tag_sources"].items()
            if name in current_names
        }
    metadata_text = json.dumps(document, ensure_ascii=False, indent=2)
    temporary = metadata_path.with_name(f".metadata-tags-{uuid.uuid4().hex}.tmp")
    temporary.write_text(metadata_text + "\n", encoding="utf-8")
    try:
        os.replace(temporary, metadata_path)
        book.metadata_json = metadata_text
        db.commit()
    except Exception:
        db.rollback()
        metadata_path.write_bytes(old_metadata)
        raise
    finally:
        temporary.unlink(missing_ok=True)


@app.post("/api/books/{book_id}/tags")
def create_book_tag(book_id: str, selection: TagSelection, db: Session = Depends(get_db)):
    book = get_book(db, book_id)
    if not book:
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"})
    try:
        add_book_tag(db, book, selection.name)
        _persist_book_tags(db, book)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, detail={"code": "invalid_tag", "message": str(exc)}) from exc
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
        try:
            result = generate_tags(make_ai_provider(settings), settings, context)
        except AIError as exc:
            raise HTTPException(503, detail={"code": "ai_error", "message": str(exc)}) from exc
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
        try:
            result = detect_language(make_ai_provider(settings), settings, samples)
        except AIError as exc:
            raise HTTPException(503, detail={"code": "ai_error", "message": str(exc)}) from exc
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
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_isbn", "message": str(exc)}) from exc


@app.post("/api/books/{book_id}/isbn/reference")
async def apply_book_isbn_reference(book_id: str, selection: IsbnSelection, request: Request):
    manager: ImportManager = request.app.state.import_manager
    try:
        return await manager.apply_isbn_reference(book_id, selection.isbn)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "Buch nicht gefunden"}) from exc
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid_isbn", "message": str(exc)}) from exc


@app.post("/api/import", status_code=202)
async def import_files(request: Request, files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, detail={"code": "empty_upload", "message": "Keine Dateien übergeben"})
    settings.ensure_directories()
    stored: list[tuple[str, Path]] = []
    try:
        for upload in files:
            original_name = Path(upload.filename or "unbenannt").name
            suffix = Path(original_name).suffix.lower()[:10]
            staging_path = settings.staging_dir / f"upload-{uuid.uuid4().hex}{suffix}"
            with staging_path.open("xb") as target:
                while chunk := await upload.read(1024 * 1024):
                    target.write(chunk)
            stored.append((original_name, staging_path))
            await upload.close()
    except OSError as exc:
        for _name, path in stored:
            path.unlink(missing_ok=True)
        raise HTTPException(507, detail={"code": "staging_failed", "message": f"Upload konnte nicht gespeichert werden: {exc}"}) from exc
    manager: ImportManager = request.app.state.import_manager
    try:
        job = manager.create_job(stored)
    except ArchiveBusyError as exc:
        for _name, path in stored:
            path.unlink(missing_ok=True)
        raise HTTPException(409, detail={"code": "archive_busy", "message": str(exc)}) from exc
    return job.as_dict()


@app.delete("/api/archive")
async def delete_archive(request: Request, confirmation: str = Query(...)):
    if confirmation != "LÖSCHEN":
        raise HTTPException(
            400,
            detail={"code": "confirmation_required", "message": "Bestätigung 'LÖSCHEN' fehlt"},
        )
    manager: ImportManager = request.app.state.import_manager
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
