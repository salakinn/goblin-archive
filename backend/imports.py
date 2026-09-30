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
from typing import Any, Awaitable, Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.archive_paths import publish_book_directory, reserve_book_location
from backend.covers import CoverAsset, CoverService
from backend.extractors import InvalidBookError, detect_and_extract
from backend.isbn import IsbnResolver
from backend.metadata import BookMetadata, fallback_title, normalize_language
from backend.title_cleanup import clean_field, clean_title
from backend.metadata_store import persist_metadata
from backend.metadata_sources import record_sources
from backend.maintenance import clear_archive
from backend.models import Book, BookFingerprint, BookSimilarityBucket
from backend.duplicates import (Fingerprint, TEXT_VERSION, buckets, comparison_for_metadata,
                                find_matches, fingerprint)
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
    id: str = field(default_factory=lambda: f"item_{uuid.uuid4().hex[:16]}")
    status: str = "queued"
    event: str = "queued"
    message: str | None = None
    book_id: str | None = None
    preview_id: str | None = None
    sha256: str | None = None
    warnings: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)


@dataclass
class ImportJob:
    id: str
    created_at: str
    items: list[ImportItem] = field(default_factory=list)
    status: str = "queued"
    revision: int = 0
    postprocess_tasks: list[asyncio.Task] = field(default_factory=list, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "status": self.status,
            "revision": self.revision,
            "total": len(self.items),
            "completed": sum(item.status in {"finished", "duplicate", "failed", "discarded"} for item in self.items),
            "queued": sum(item.status == "queued" for item in self.items),
            "active": sum(item.status in {"running", "postprocessing"} for item in self.items),
            "needs_review": sum(item.status == "needs_review" for item in self.items),
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
                    event = data["event"]
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
        self.queue: asyncio.Queue[tuple[Callable[[], Awaitable[Any]], asyncio.Future]] = asyncio.Queue(
            maxsize=settings.max_queued_import_files)
        self.workers: list[asyncio.Task] = []
        self.tasks: set[asyncio.Task] = set()
        self.stopping = False
        self.pending = 0
        self.reserved = 0
        self.postprocess_step: Callable[[str, str], Awaitable[str | None]] | None = None
        self.ai_busy: Callable[[], bool] | None = None
        self.provider_slots = asyncio.Semaphore(settings.provider_concurrency)

    async def enrich_metadata(self, metadata: BookMetadata):
        async with self.provider_slots:
            return await self.provider_chain.enrich(metadata)

    async def resolve_cover(self, embedded, isbn):
        async with self.provider_slots:
            return await self.cover_service.resolve(embedded, isbn)

    def _ensure_workers(self) -> None:
        if not self.workers:
            self.workers = [asyncio.create_task(self._worker())
                            for _ in range(self.settings.import_concurrency)]

    def _track(self, task: asyncio.Task) -> None:
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _worker(self) -> None:
        while True:
            operation, future = await self.queue.get()
            try:
                if not future.cancelled():
                    future.set_result(await operation())
            except BaseException as exc:
                if not future.cancelled():
                    future.set_exception(exc)
            finally:
                self.pending -= 1
                self.queue.task_done()

    async def submit(self, operation: Callable[[], Awaitable[Any]], *, internal: bool = False) -> Any:
        self._ensure_workers()
        while internal and self.pending >= self.settings.max_queued_import_files:
            await asyncio.sleep(0.02)
        if self.pending >= self.settings.max_queued_import_files:
            raise ArchiveBusyError("Die Importwarteschlange ist voll")
        future = asyncio.get_running_loop().create_future()
        self.pending += 1
        try:
            self.queue.put_nowait((operation, future))
        except asyncio.QueueFull:
            self.pending -= 1
            raise ArchiveBusyError("Die Importwarteschlange ist voll") from None
        return await future

    async def stop(self) -> None:
        self.stopping = True
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
        preview_manager = getattr(self, "preview_manager", None)
        if preview_manager is not None:
            await preview_manager.stop()
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
        if self.workers:
            await self.queue.join()
            for worker in self.workers:
                worker.cancel()
            await asyncio.gather(*self.workers, return_exceptions=True)
            self.workers.clear()

    def has_active_imports(self) -> bool:
        return (self.pending > 0 or self.reserved > 0 or
                any(job.status in {"queued", "running"} for job in self.jobs.values()))

    def backlog(self) -> int:
        waiting_postprocess = sum(
            item.status == "queued" and item.book_id is not None
            for job in self.jobs.values() for item in job.items)
        return self.pending + self.reserved + waiting_postprocess

    def _find_import_matches(self, metadata: BookMetadata, analyzed: Fingerprint,
                             staging_path: Path) -> list[dict]:
        with self.session_factory() as session:
            return find_matches(session, comparison_for_metadata(metadata.as_dict()),
                                analyzed.text_hash, signature=analyzed.signature,
                                source_path=staging_path, library_dir=self.settings.library_dir,
                                word_count=analyzed.word_count)

    def create_job(self, uploads: list[tuple[str, Path]]) -> ImportJob:
        if self.maintenance_active or self.stopping:
            raise ArchiveBusyError("Das Archiv wird gerade geleert")
        if self.backlog() + len(uploads) > self.settings.max_queued_import_files:
            raise ArchiveBusyError("Die Importwarteschlange ist voll")
        job = ImportJob(
            id=f"imp_{uuid.uuid4().hex[:10]}",
            created_at=datetime.now(timezone.utc).isoformat(),
            items=[ImportItem(filename=name) for name, _ in uploads],
        )
        self.jobs[job.id] = job
        self.reserved += len(uploads)
        self._track(asyncio.create_task(self._run(job, uploads)))
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
        job.revision += 1
        await self.events.publish(event, {"import_id": job.id, "item_id": item.id,
                                          "revision": job.revision,
                                          "filename": item.filename, **extra})

    async def _run(self, job: ImportJob, uploads: list[tuple[str, Path]]) -> None:
        job.status = "running"
        await self.events.publish("import.started", {"import_id": job.id, "total": len(job.items)})
        async def run_item(item: ImportItem, path: Path) -> None:
            self.reserved -= 1
            try:
                await self.submit(lambda: self._process_one(job, item, path), internal=True)
            except BaseException:
                if item.status == "queued":
                    path.unlink(missing_ok=True)
                raise
        remaining = iter((item, path) for item, (_name, path)
                         in zip(job.items, uploads, strict=True))
        active: dict[asyncio.Task, ImportItem] = {}
        def launch_one() -> None:
            pair = next(remaining, None)
            if pair is not None:
                item, path = pair
                active[asyncio.create_task(run_item(item, path))] = item
        for _ in range(min(self.settings.import_concurrency, len(uploads))):
            launch_one()
        while active:
            done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                item = active.pop(task)
                result = task.exception()
                if result is not None and item.status == "queued":
                    item.status = "failed"
                    item.message = str(result)
                    await self._emit(job, item, "import.failed", error=item.message)
                launch_one()
        if job.postprocess_tasks:
            await asyncio.gather(*job.postprocess_tasks, return_exceptions=True)
        self._refresh_job_status(job)

    @staticmethod
    def _refresh_job_status(job: ImportJob) -> None:
        job.status = ("running" if any(item.status in {"queued", "running", "postprocessing"}
                                       for item in job.items)
                      else "needs_review" if any(item.status == "needs_review" for item in job.items)
                      else "failed" if all(item.status == "failed" for item in job.items)
                      else "finished")
        job.revision += 1

    def _preview_owner(self, preview_id: str) -> tuple[ImportJob, ImportItem] | None:
        for job in self.jobs.values():
            for item in job.items:
                if item.preview_id == preview_id:
                    return job, item
        return None

    def resolve_preview(self, preview_id: str, book_id: str | None,
                        *, discarded: bool = False) -> None:
        owner = self._preview_owner(preview_id)
        if owner is None:
            return
        job, item = owner
        item.book_id = book_id
        item.status = "discarded" if discarded else "duplicate"
        item.message = "Vorschau verworfen" if discarded else "Vorhandenes Buch verwendet"
        item.event = "import.discarded" if discarded else "import.duplicate"
        self._refresh_job_status(job)

    def start_postprocessing(self, book_id: str, filename: str,
                             *, preview_id: str | None = None) -> None:
        if self.postprocess_step is None:
            return
        owner = self._preview_owner(preview_id) if preview_id else None
        if owner:
            job, item = owner
            item.status = "queued"
            item.book_id = book_id
            item.message = "Archiviert · Nachbearbeitung wartet"
            job.status = "running"
            job.revision += 1
        else:
            item = ImportItem(filename=filename, status="queued", book_id=book_id,
                              event="import.postprocess.queued", message="Nachbearbeitung wartet")
            job = ImportJob(id=f"imp_{uuid.uuid4().hex[:10]}",
                            created_at=datetime.now(timezone.utc).isoformat(),
                            items=[item], status="running")
            self.jobs[job.id] = job
        task = asyncio.create_task(self._postprocess(job, item))
        self._track(task)
        job.postprocess_tasks.append(task)
        task.add_done_callback(lambda _: self._refresh_job_status(job))

    def retry_postprocessing(self, job_id: str, item_id: str) -> ImportJob:
        if self.stopping or self.maintenance_active:
            raise ArchiveBusyError("Das Archiv ist gerade nicht für neue Arbeit verfügbar")
        job = self.jobs.get(job_id)
        item = next((row for row in job.items if row.id == item_id), None) if job else None
        if not job or not item or item.status != "finished" or not item.failed_steps:
            raise KeyError(item_id)
        steps = tuple(item.failed_steps)
        item.failed_steps = []
        item.warnings = []
        item.status = "queued"
        item.message = "Nachbearbeitung wartet auf Wiederholung"
        job.status = "running"
        job.revision += 1
        task = asyncio.create_task(self._postprocess(job, item, steps=steps))
        self._track(task)
        job.postprocess_tasks.append(task)
        task.add_done_callback(lambda _: self._refresh_job_status(job))
        return job

    async def _postprocess(self, job: ImportJob, item: ImportItem,
                           *, steps: tuple[str, ...] = ("isbn", "authors", "language", "tags")) -> None:
        if self.postprocess_step is None or item.book_id is None:
            return
        results = list(item.results)
        for step, label in (("isbn", "ISBN"), ("authors", "Autoren"),
                            ("language", "Sprache"), ("tags", "Tags")):
            if step not in steps:
                continue
            item.status = "queued"
            item.message = f"Archiviert · {label} wartet…"
            await self._emit(job, item, "import.postprocess.queued", book_id=item.book_id)
            try:
                for attempt in range(3):
                    if step in {"language", "tags"} and self.ai_busy:
                        while self.ai_busy():
                            await asyncio.sleep(0.1)
                    try:
                        async def run_step(step=step):
                            item.status = "postprocessing"
                            item.message = f"Archiviert · {label} wird geprüft…"
                            await self._emit(job, item, f"import.postprocess.{step}",
                                             book_id=item.book_id)
                            return await self.postprocess_step(item.book_id, step)
                        result = await self.submit(
                            run_step, internal=True)
                        break
                    except Exception as exc:
                        if (getattr(exc, "status_code", None) != 409 or
                                step not in {"language", "tags"} or attempt == 2):
                            raise
                        await asyncio.sleep(0.2)
                if result:
                    results.append(result)
            except Exception as exc:
                logger.warning("Postprocessing %s failed for %s: %s", step, item.book_id, exc)
                item.warnings.append(f"{label}: {exc}")
                item.failed_steps.append(step)
        item.status = "finished"
        item.results = results
        item.message = ("Archiviert · Nachbearbeitung mit Hinweisen: " + "; ".join(item.warnings)
                        if item.warnings else "Archiviert · " + "; ".join(results))
        await self._emit(job, item, "import.finished", book_id=item.book_id,
                         warnings=item.warnings, results=results)

    async def _process_one(self, job: ImportJob, item: ImportItem, staging_path: Path) -> None:
        try:
            item.status = "running"
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
            raw_sources = json.loads(json.dumps(extracted.metadata.as_dict(), ensure_ascii=False))
            await self._emit(job, item, "import.metadata.embedded", title=extracted.metadata.title.value)
            if not extracted.metadata.title.value:
                extracted.metadata.title.value = fallback_title(item.filename)
                extracted.metadata.title.source = "filename"
            title_history: list[dict] = []
            clean_field(extracted.metadata.title, title_history)
            # Automatic external cover lookup is deliberately limited to an ISBN that
            # came from the imported file. An ISBN inferred by fuzzy title/author
            # metadata enrichment may describe a different edition.
            embedded_isbn = extracted.metadata.isbn.value
            metadata, providers = await self.enrich_metadata(extracted.metadata)
            clean_field(metadata.title, title_history)
            if providers:
                await self._emit(job, item, "import.metadata.provider", providers=providers)
            metadata.language.value = normalize_language(metadata.language.value)
            preview_manager = getattr(self, "preview_manager", None)
            analyzed = None
            if preview_manager is not None:
                analyzed = await asyncio.to_thread(fingerprint, staging_path, file_format)
                matches = await asyncio.to_thread(self._find_import_matches, metadata,
                                                  analyzed, staging_path)
                if matches:
                    previews = preview_manager.create([(item.filename, staging_path)],
                                                      already_accepted=True)
                    item.status = "needs_review"
                    item.preview_id = previews[0]["id"]
                    item.message = "Mögliche Duplikate prüfen"
                    await self._emit(job, item, "import.needs_review", preview_id=previews[0]["id"])
                    return
            cover = None
            try:
                cover = await self.resolve_cover(extracted.cover, embedded_isbn)
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
                cover, providers, analyzed, raw_sources, title_history,
            )
            item.book_id = book_id
            invalidate_filter_cache()
            await self._emit(job, item, "import.archived", book_id=book_id, path=str(target))
            if self.postprocess_step is not None:
                item.status = "queued"
                item.message = "Archiviert · Nachbearbeitung wartet"
                job.postprocess_tasks.append(asyncio.create_task(self._postprocess(job, item)))
            else:
                item.status = "finished"
                item.message = "Archiviert"
                await self._emit(job, item, "import.finished", book_id=book_id)
        except SQLAlchemyError as exc:
            with self.session_factory() as session:
                duplicate_id = find_book_id_by_hash(session, item.sha256) if item.sha256 else None
            if duplicate_id:
                item.status = "duplicate"
                item.book_id = duplicate_id
                item.message = "Identische Datei ist bereits archiviert"
                await self._emit(job, item, "import.duplicate", book_id=duplicate_id,
                                 sha256=item.sha256)
            else:
                item.status = "failed"
                item.message = str(exc)
                logger.exception("Import failed filename=%r", item.filename)
                await self._emit(job, item, "import.failed", error=str(exc))
        except (InvalidBookError, OSError, ValueError) as exc:
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
            if item.status != "needs_review":
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
        analyzed: Fingerprint | None = None,
        source_values: dict | None = None,
        title_history: list[dict] | None = None,
    ) -> tuple[str, Path]:
        imported_at = datetime.now(timezone.utc)
        author_names = list(dict.fromkeys(str(value).strip() for value in (metadata.authors.value or []) if str(value).strip()))
        genre_names = list(dict.fromkeys(str(value).strip() for value in (metadata.genres.value or []) if str(value).strip()))
        title = str(metadata.title.value)
        if metadata.title.source not in {"manual", "user"}:
            title, rules = clean_title(title)
            if title != metadata.title.value:
                title_history = [*(title_history or []), {"old_title": metadata.title.value,
                    "new_title": title, "source": metadata.title.source, "version": "1",
                    "rules": rules, "created_at": imported_at.isoformat()}]
                metadata.title.value = title
        else:
            clean_title(title)
        filename = f"book.{file_format}"
        with self.session_factory() as session:
            location = reserve_book_location(self.settings.library_dir, file_format,
                                             lambda value: session.get(Book, value) is not None)
        book_id, target_dir, relative_file = location.book_id, location.directory, location.relative_file
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
            "title_cleanup_history": title_history or [],
            "cover": cover.metadata() if cover else None,
            "providers_consulted_successfully": providers,
        }

        tmp_dir = self.settings.library_dir / f".incoming-{uuid.uuid4().hex}"
        try:
            tmp_dir.mkdir(parents=False)
            shutil.copy2(staging_path, tmp_dir / filename)
            if cover and cover_name:
                (tmp_dir / cover_name).write_bytes(cover.content)
            metadata_text = json.dumps(document, ensure_ascii=False, indent=2)
            (tmp_dir / "metadata.json").write_text(metadata_text + "\n", encoding="utf-8")
            publish_book_directory(tmp_dir, location)

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
                    analyzed = analyzed or fingerprint(staging_path, file_format)
                    session.add(BookFingerprint(book_id=book_id, version=TEXT_VERSION,
                                                text_hash=analyzed.text_hash,
                                                word_count=analyzed.word_count,
                                                status=analyzed.status, reason=analyzed.reason,
                                                signature_json=json.dumps(analyzed.signature)))
                    session.add_all(BookSimilarityBucket(book_id=book_id, band=band, bucket=value)
                                    for band, value in enumerate(buckets(analyzed.signature)))
                    record_sources(session, book_id, [source_values or {}, metadata.as_dict()])
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
            if target_dir.exists():
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
            replacement = await self.resolve_cover(extracted.cover, isbn)
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
        async with self.provider_slots:
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
