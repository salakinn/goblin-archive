from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.covers import CoverAsset, CoverService
from backend.extractors import InvalidBookError, detect_and_extract
from backend.isbn import IsbnResolver
from backend.metadata import BookMetadata, fallback_title, normalize_language, sanitize_component
from backend.metadata_store import persist_metadata
from backend.maintenance import clear_archive
from backend.models import Book
from backend.providers import ProviderChain
from backend.repository import (find_book_id_by_hash, get_book, get_or_create_author, get_or_create_tag,
                                insert_book, invalidate_filter_cache)

logger = logging.getLogger(__name__)


class ArchiveBusyError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ImportItem:
    filename: str
    status: str = "queued"
    event: str = "queued"
    message: str | None = None
    book_id: str | None = None
    sha256: str | None = None


@dataclass
class ImportJob:
    id: str
    created_at: str
    items: list[ImportItem] = field(default_factory=list)
    status: str = "queued"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "status": self.status,
            "total": len(self.items),
            "completed": sum(item.status in {"finished", "duplicate", "failed"} for item in self.items),
            "items": [item.__dict__ for item in self.items],
        }


class EventBroker:
    def __init__(self):
        self.subscribers: set[asyncio.Queue] = set()

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        payload = {"event": event, **data}
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def stream(self):
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.subscribers.add(queue)
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15)
                    event = data.pop("event")
                    yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self.subscribers.discard(queue)


class ImportManager:
    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[], Session],
        provider_chain: ProviderChain,
        cover_service: CoverService | None = None,
        isbn_resolver: IsbnResolver | None = None,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.provider_chain = provider_chain
        self.cover_service = cover_service or CoverService([])
        self.isbn_resolver = isbn_resolver
        self.jobs: dict[str, ImportJob] = {}
        self.events = EventBroker()
        self.maintenance_active = False

    def has_active_imports(self) -> bool:
        return any(job.status in {"queued", "running"} for job in self.jobs.values())

    def create_job(self, uploads: list[tuple[str, Path]]) -> ImportJob:
        if self.maintenance_active:
            raise ArchiveBusyError("Das Archiv wird gerade geleert")
        job = ImportJob(
            id=f"imp_{uuid.uuid4().hex[:10]}",
            created_at=datetime.now(timezone.utc).isoformat(),
            items=[ImportItem(filename=name) for name, _ in uploads],
        )
        self.jobs[job.id] = job
        asyncio.create_task(self._run(job, uploads))
        return job

    async def clear(self) -> dict[str, int]:
        if self.maintenance_active or self.has_active_imports():
            raise ArchiveBusyError("Das Archiv kann während eines Imports nicht geleert werden")
        self.maintenance_active = True
        try:
            result = await asyncio.to_thread(clear_archive, self.settings, self.session_factory)
            self.jobs.clear()
            invalidate_filter_cache()
            await self.events.publish("archive.cleared", result)
            return result
        finally:
            self.maintenance_active = False

    async def _emit(self, job: ImportJob, item: ImportItem, event: str, **extra: Any) -> None:
        item.event = event
        await self.events.publish(event, {"import_id": job.id, "filename": item.filename, **extra})

    async def _run(self, job: ImportJob, uploads: list[tuple[str, Path]]) -> None:
        job.status = "running"
        await self.events.publish("import.started", {"import_id": job.id, "total": len(job.items)})
        for item, (_name, path) in zip(job.items, uploads, strict=True):
            await self._process_one(job, item, path)
        job.status = "failed" if all(item.status == "failed" for item in job.items) else "finished"

    async def _process_one(self, job: ImportJob, item: ImportItem, staging_path: Path) -> None:
        try:
            await self._emit(job, item, "import.hashing")
            digest = await asyncio.to_thread(sha256_file, staging_path)
            item.sha256 = digest
            with self.session_factory() as session:
                duplicate_id = find_book_id_by_hash(session, digest)
                if duplicate_id:
                    item.status = "duplicate"
                    item.book_id = duplicate_id
                    item.message = "Identische Datei ist bereits archiviert"
                    await self._emit(job, item, "import.duplicate", book_id=duplicate_id, sha256=digest)
                    logger.info("Duplicate import filename=%r sha256=%s book_id=%s", item.filename, digest, duplicate_id)
                    return

            file_format, extracted = await asyncio.to_thread(detect_and_extract, staging_path, item.filename)
            await self._emit(job, item, "import.metadata.embedded", title=extracted.metadata.title.value)
            # Automatic external cover lookup is deliberately limited to an ISBN that
            # came from the imported file. An ISBN inferred by fuzzy title/author
            # metadata enrichment may describe a different edition.
            embedded_isbn = extracted.metadata.isbn.value
            metadata, providers = await self.provider_chain.enrich(extracted.metadata)
            if providers:
                await self._emit(job, item, "import.metadata.provider", providers=providers)
            if not metadata.title.value:
                metadata.title.value = fallback_title(item.filename)
                metadata.title.source = "filename"
            metadata.language.value = normalize_language(metadata.language.value)
            cover = None
            try:
                cover = await self.cover_service.resolve(extracted.cover, embedded_isbn)
                if cover:
                    if cover.source == "embedded":
                        await self._emit(job, item, "import.cover.embedded")
                    else:
                        await self._emit(job, item, "import.cover.provider", provider=cover.provider)
                    await self._emit(
                        job, item, "import.cover.found", source=cover.source, provider=cover.provider,
                    )
                else:
                    await self._emit(job, item, "import.cover.missing")
            except Exception as exc:
                # Covers are optional. Even malformed embedded data or an unexpected provider
                # response must never turn an otherwise valid book into a failed import.
                logger.warning("Cover resolution failed for %r: %s", item.filename, exc)
                await self._emit(job, item, "import.cover.missing")
            await self._emit(job, item, "import.archiving", title=metadata.title.value)
            book_id, target = await asyncio.to_thread(
                self._archive, staging_path, item.filename, digest, file_format, metadata,
                cover, providers,
            )
            item.status = "finished"
            item.book_id = book_id
            item.message = "Archiviert"
            invalidate_filter_cache()
            await self._emit(job, item, "import.finished", book_id=book_id, path=str(target))
        except (InvalidBookError, OSError, SQLAlchemyError, ValueError) as exc:
            item.status = "failed"
            item.message = str(exc)
            logger.exception("Import failed filename=%r", item.filename)
            await self._emit(job, item, "import.failed", error=str(exc))
        except Exception as exc:  # a failed import must not kill the whole background task
            item.status = "failed"
            item.message = f"Unerwarteter Importfehler: {exc}"
            logger.exception("Unexpected import failure filename=%r", item.filename)
            await self._emit(job, item, "import.failed", error=item.message)
        finally:
            staging_path.unlink(missing_ok=True)

    def _archive(
        self,
        staging_path: Path,
        original_filename: str,
        digest: str,
        file_format: str,
        metadata: BookMetadata,
        cover: CoverAsset | None,
        providers: list[str],
    ) -> tuple[str, Path]:
        imported_at = datetime.now(timezone.utc)
        author_names = list(dict.fromkeys(str(value).strip() for value in (metadata.authors.value or []) if str(value).strip()))
        genre_names = list(dict.fromkeys(str(value).strip() for value in (metadata.genres.value or []) if str(value).strip()))
        author_dir = sanitize_component(author_names[0] if author_names else "Unbekannter Autor")
        title = str(metadata.title.value)
        title_dir = sanitize_component(title, "Unbekannter Titel")
        filename = f"{sanitize_component(title, 'Unbekannter Titel', 150)}.{file_format}"

        for _ in range(20):
            book_id = f"bk_{uuid.uuid4().hex[:8]}"
            target_dir = self.settings.library_dir / author_dir / title_dir / book_id
            if not target_dir.exists():
                break
        else:
            raise FileExistsError("Kein konfliktfreier Archivpfad konnte erzeugt werden")

        relative_file = (target_dir / filename).relative_to(self.settings.library_dir).as_posix()
        cover_name = cover.filename if cover else None
        document = {
            "schema_version": 1,
            "id": book_id,
            "import": {"original_filename": original_filename, "imported_at": imported_at.isoformat()},
            "file": {
                "filename": filename,
                "format": file_format,
                "size": staging_path.stat().st_size,
                "sha256": digest,
                "library_path": relative_file,
                "cover_filename": cover_name,
            },
            "metadata": metadata.as_dict(),
            "cover": cover.metadata() if cover else None,
            "providers_consulted_successfully": providers,
        }

        tmp_dir = self.settings.library_dir / f".incoming-{uuid.uuid4().hex}"
        final_created = False
        try:
            tmp_dir.mkdir(parents=False)
            shutil.copy2(staging_path, tmp_dir / filename)
            if cover and cover_name:
                (tmp_dir / cover_name).write_bytes(cover.content)
            metadata_text = json.dumps(document, ensure_ascii=False, indent=2)
            (tmp_dir / "metadata.json").write_text(metadata_text + "\n", encoding="utf-8")
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            os.rename(tmp_dir, target_dir)
            final_created = True

            with self.session_factory() as session:
                try:
                    book = Book(
                        id=book_id, title=title, publication_year=metadata.publication_year.value,
                        language=metadata.language.value, publisher=metadata.publisher.value,
                        isbn=metadata.isbn.value, series=metadata.series.value,
                        description=metadata.description.value, format=file_format,
                        file_size=staging_path.stat().st_size, sha256=digest,
                        library_path=relative_file,
                        has_cover=bool(cover),
                        cover_path=(target_dir / cover_name).relative_to(self.settings.library_dir).as_posix() if cover_name else None,
                        cover_source=cover.source if cover else None,
                        cover_provider=cover.provider if cover else None,
                        original_filename=original_filename, imported_at=imported_at,
                        metadata_json=metadata_text,
                    )
                    book.authors = [get_or_create_author(session, value) for value in author_names]
                    book.tags = [get_or_create_tag(session, value) for value in genre_names]
                    insert_book(session, book)
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
            logger.info(
                "Imported filename=%r sha256=%s title=%r authors=%r isbn=%r providers=%r target=%s result=success",
                original_filename, digest, title, author_names, metadata.isbn.value, providers, target_dir,
            )
            return book_id, target_dir
        except Exception:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            if final_created and target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            raise

    async def refresh_cover(self, book_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            book = session.get(Book, book_id)
            if not book:
                raise KeyError(book_id)
            source_file = self.settings.library_dir / book.library_path
            original_filename = book.original_filename
            isbn = book.isbn
            previous = json.loads(book.metadata_json).get("cover")

        try:
            _file_format, extracted = await asyncio.to_thread(
                detect_and_extract, source_file, original_filename,
            )
            replacement = await self.cover_service.resolve(extracted.cover, isbn)
        except Exception as exc:
            logger.warning("Cover refresh failed for book %s: %s", book_id, exc)
            replacement = None
        if not replacement:
            return {"found": False, "replaced": False, "cover": previous}

        await asyncio.to_thread(self._store_refreshed_cover, book_id, replacement)
        return {"found": True, "replaced": True, "cover": replacement.metadata()}

    def _store_refreshed_cover(self, book_id: str, cover: CoverAsset) -> None:
        with self.session_factory() as session:
            book = session.get(Book, book_id)
            if not book:
                raise KeyError(book_id)
            book_dir = (self.settings.library_dir / book.library_path).parent
            final_path = book_dir / cover.filename
            temporary = book_dir / f".cover-{uuid.uuid4().hex}.tmp"
            old_path = self.settings.library_dir / book.cover_path if book.cover_path else None
            old_cover = old_path.read_bytes() if old_path and old_path.is_file() else None
            temporary.write_bytes(cover.content)
            try:
                os.replace(temporary, final_path)
                document = json.loads(book.metadata_json)
                document.setdefault("file", {})["cover_filename"] = cover.filename
                document["cover"] = cover.metadata()
                book.cover_path = final_path.relative_to(self.settings.library_dir).as_posix()
                book.has_cover = True
                book.cover_source = cover.source
                book.cover_provider = cover.provider
                persist_metadata(session, book, document, self.settings)
            except Exception:
                session.rollback()
                if old_path and old_cover is not None:
                    old_path.write_bytes(old_cover)
                if final_path != old_path:
                    final_path.unlink(missing_ok=True)
                raise
            finally:
                temporary.unlink(missing_ok=True)
            if old_path and old_path != final_path:
                old_path.unlink(missing_ok=True)

    async def search_isbn(self, book_id: str, force: bool = False) -> dict[str, Any]:
        if not self.isbn_resolver:
            return {"book_id": book_id, "auto_applied": False, "candidates": []}
        result = await self.isbn_resolver.search(book_id, force=force)
        result["auto_applied"] = False
        result["reference_applied"] = False
        auto = result.get("auto_candidate")
        if auto:
            await asyncio.to_thread(self.isbn_resolver.apply, book_id, auto["isbn13"])
            invalidate_filter_cache()
            result["auto_applied"] = True
            with self.session_factory() as session:
                book = get_book(session, book_id)
                needs_cover = bool(book and not book.has_cover)
            if needs_cover:
                result["cover"] = await self.refresh_cover(book_id)
            await self.events.publish("book.isbn.resolved", {
                "book_id": book_id, "isbn": auto["isbn13"], "automatic": True,
            })
        elif result.get("auto_reference"):
            reference = result["auto_reference"]
            await asyncio.to_thread(
                self.isbn_resolver.apply_reference, book_id, reference["isbn13"],
            )
            invalidate_filter_cache()
            result["reference_applied"] = True
            await self.events.publish("book.work.resolved", {
                "book_id": book_id,
                "reference_isbn": reference["isbn13"],
                "confidence": reference["work_score"],
                "automatic": True,
            })
        return result

    async def apply_isbn(self, book_id: str, isbn: str) -> dict[str, Any]:
        if not self.isbn_resolver:
            raise RuntimeError("ISBN-Auflösung ist nicht verfügbar")
        candidate = await asyncio.to_thread(self.isbn_resolver.apply, book_id, isbn)
        invalidate_filter_cache()
        with self.session_factory() as session:
            book = get_book(session, book_id)
            needs_cover = bool(book and not book.has_cover)
        cover = await self.refresh_cover(book_id) if needs_cover else None
        await self.events.publish("book.isbn.resolved", {
            "book_id": book_id, "isbn": candidate["isbn13"], "automatic": False,
        })
        return {"applied": True, "candidate": candidate, "cover": cover}

    async def apply_isbn_reference(self, book_id: str, isbn: str) -> dict[str, Any]:
        if not self.isbn_resolver:
            raise RuntimeError("ISBN-Auflösung ist nicht verfügbar")
        result = await asyncio.to_thread(self.isbn_resolver.apply_reference, book_id, isbn)
        invalidate_filter_cache()
        await self.events.publish("book.work.resolved", {
            "book_id": book_id,
            "reference_isbn": result["candidate"]["isbn13"],
            "confidence": result["candidate"]["work_score"],
            "automatic": False,
        })
        return {"applied": True, **result}
