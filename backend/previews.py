"""Durable, editable import previews backed by staging files."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, update

from backend.covers import CoverAsset, embedded_cover
from backend.extractors import detect_and_extract
from backend.imports import ImportManager, sha256_file
from backend.isbn import canonical_isbn13
from backend.metadata import BookMetadata, FieldValue, fallback_title, normalize_language
from backend.models import ImportPreview
from backend.repository import find_book_id_by_hash, invalidate_filter_cache

logger = logging.getLogger(__name__)
FIELDS = set(BookMetadata.__dataclass_fields__)


class PreviewConflict(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _expired(value: datetime) -> bool:
    return value.replace(tzinfo=timezone.utc) <= _now()


def _metadata(data: dict) -> BookMetadata:
    return BookMetadata(**{name: FieldValue(**data.get(name, {})) for name in FIELDS})


def _public(row: ImportPreview) -> dict:
    return {"id": row.id, "group_id": row.group_id, "filename": row.filename,
            "created_at": row.created_at.isoformat(), "expires_at": row.expires_at.isoformat(),
            "status": row.status, "metadata": json.loads(row.metadata_json) if row.metadata_json else None,
            "cover": json.loads(row.cover_json) if row.cover_json else None,
            "embedded_cover": json.loads(row.embedded_cover_json) if row.embedded_cover_json else None,
            "external_cover": json.loads(row.external_cover_json) if row.external_cover_json else None,
            "cover_selected": bool(row.cover_path), "providers": json.loads(row.providers_json),
            "format": row.file_format, "sha256": row.sha256,
            "duplicate_book_id": row.duplicate_book_id, "duplicate_preview_id": None,
            "book_id": row.book_id,
            "error": row.error, "revision": row.revision, "edited": row.edited}


class PreviewManager:
    def __init__(self, imports: ImportManager):
        self.imports = imports
        self.settings = imports.settings
        self.session_factory = imports.session_factory
        self.tasks: set[asyncio.Task] = set()

    def _task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def create(self, uploads: list[tuple[str, Path]]) -> list[dict]:
        if self.imports.maintenance_active:
            raise PreviewConflict("Das Archiv wird gerade geleert")
        group = f"prg_{uuid.uuid4().hex[:12]}"
        now = _now()
        with self.session_factory() as db:
            rows = [ImportPreview(id=f"pr_{uuid.uuid4().hex[:16]}", group_id=group,
                                  filename=name, staging_path=str(path), created_at=now,
                                  expires_at=now + timedelta(hours=24), status="queued")
                    for name, path in uploads]
            db.add_all(rows)
            db.commit()
            result = [_public(row) for row in rows]
        for row in rows:
            self._task(self.analyze(row.id))
        return result

    def list(self) -> list[dict]:
        with self.session_factory() as db:
            rows = db.scalars(select(ImportPreview).where(ImportPreview.expires_at > _now())
                              .order_by(ImportPreview.created_at.desc(), ImportPreview.id.desc())).all()
            result = [_public(row) for row in rows]
            for item in result:
                if item["sha256"] and item["status"] not in {"archived", "discarded"}:
                    item["duplicate_book_id"] = find_book_id_by_hash(db, item["sha256"])
        seen: dict[str, str] = {}
        for item in reversed(result):
            if item["status"] in {"archived", "discarded"} or not item["sha256"]:
                continue
            item["duplicate_preview_id"] = seen.get(item["sha256"])
            seen.setdefault(item["sha256"], item["id"])
        return result

    def busy(self) -> bool:
        with self.session_factory() as db:
            return db.scalar(select(ImportPreview.id).where(
                ImportPreview.status.in_(["analyzing", "cover_search", "archiving"])).limit(1)) is not None

    def get(self, preview_id: str) -> dict:
        with self.session_factory() as db:
            row = db.get(ImportPreview, preview_id)
            if not row or _expired(row.expires_at):
                raise KeyError(preview_id)
            result = _public(row)
            if row.sha256 and row.status not in {"archived", "discarded"}:
                result["duplicate_book_id"] = find_book_id_by_hash(db, row.sha256)
                earlier = db.scalar(select(ImportPreview.id).where(
                    ImportPreview.sha256 == row.sha256, ImportPreview.id != row.id,
                    ((ImportPreview.created_at < row.created_at) |
                     ((ImportPreview.created_at == row.created_at) & (ImportPreview.id < row.id))),
                    ImportPreview.status.not_in(["archived", "discarded"]),
                    ImportPreview.expires_at > _now()).order_by(ImportPreview.created_at, ImportPreview.id))
                result["duplicate_preview_id"] = earlier
            return result

    def _claim(self, preview_id: str, allowed: tuple[str, ...], status: str) -> ImportPreview:
        with self.session_factory() as db:
            result = db.execute(update(ImportPreview).where(ImportPreview.id == preview_id,
                          ImportPreview.expires_at > _now(), ImportPreview.status.in_(allowed))
                          .values(status=status, revision=ImportPreview.revision + 1))
            if result.rowcount != 1:
                raise PreviewConflict("Vorschau ist abgelaufen oder wird bereits bearbeitet")
            db.commit()
            return db.get(ImportPreview, preview_id)

    async def analyze(self, preview_id: str, *, enrich: bool = False) -> None:
        try:
            row = self._claim(preview_id, ("queued", "ready", "failed"), "analyzing")
            path = Path(row.staging_path)
            digest = await asyncio.to_thread(sha256_file, path)
            with self.session_factory() as db:
                duplicate = find_book_id_by_hash(db, digest)
            file_format, extracted = await asyncio.to_thread(detect_and_extract, path, row.filename)
            metadata = _metadata(json.loads(row.metadata_json)) if enrich and row.metadata_json else extracted.metadata
            providers: list[str] = []
            if enrich:
                metadata, providers = await self.imports.provider_chain.enrich(metadata)
            if not metadata.title.value:
                metadata.title = FieldValue(fallback_title(row.filename), "filename")
            metadata.language.value = normalize_language(metadata.language.value)
            cover = embedded_cover(extracted.cover) if not enrich else None
            cover_path = self.settings.staging_dir / f"{preview_id}-embedded{cover.extension}" if cover else None
            if cover and cover_path:
                await asyncio.to_thread(cover_path.write_bytes, cover.content)
            with self.session_factory() as db:
                current = db.get(ImportPreview, preview_id)
                if current.status != "analyzing":
                    raise PreviewConflict("Vorschau wurde während der Analyse geändert")
                old_cover = current.cover_path
                old_embedded = current.embedded_cover_path
                current.metadata_json = json.dumps(metadata.as_dict(), ensure_ascii=False)
                if not enrich:
                    current.embedded_cover_json = json.dumps(cover.metadata(), ensure_ascii=False) if cover else None
                    current.embedded_cover_path = str(cover_path) if cover_path else None
                    current.cover_json = json.dumps(cover.metadata(), ensure_ascii=False) if cover else None
                    current.cover_path = str(cover_path) if cover_path else None
                    current.edited = False
                current.providers_json = json.dumps(providers)
                current.file_format = file_format
                current.sha256 = digest
                current.duplicate_book_id = duplicate
                current.error = None
                current.status = "ready"
                current.revision += 1
                db.commit()
            if not enrich:
                for previous in {old_cover, old_embedded}:
                    if previous and previous != str(cover_path) and previous != current.external_cover_path:
                        Path(previous).unlink(missing_ok=True)
        except Exception as exc:
            logger.exception("Preview analysis failed: %s", preview_id)
            with self.session_factory() as db:
                current = db.get(ImportPreview, preview_id)
                if current and current.status == "analyzing":
                    current.status = "failed"
                    current.error = str(exc)[:500]
                    current.revision += 1
                    db.commit()

    def edit(self, preview_id: str, changes: dict, revision: int, cover_choice: str | None) -> dict:
        if set(changes) - FIELDS:
            raise ValueError("Unbekanntes Metadatenfeld")
        with self.session_factory() as db:
            row = db.get(ImportPreview, preview_id)
            if not row or row.status != "ready" or _expired(row.expires_at) or row.revision != revision:
                raise PreviewConflict("Vorschau wurde inzwischen geändert oder ist abgelaufen")
            metadata = json.loads(row.metadata_json or "{}")
            for name, value in changes.items():
                if name in {"authors", "genres"}:
                    if not isinstance(value, list) or any(not isinstance(v, str) or len(v) > 300 for v in value):
                        raise ValueError("Autoren und Tags müssen Textlisten sein")
                    value = list(dict.fromkeys(v.strip() for v in value if v.strip()))
                elif name == "publication_year":
                    if value not in (None, ""):
                        value = int(value)
                        if not 1000 <= value <= _now().year + 2:
                            raise ValueError("Ungültiges Erscheinungsjahr")
                    else:
                        value = None
                elif value is not None:
                    if not isinstance(value, str) or len(value) > (10000 if name == "description" else 500):
                        raise ValueError("Ungültiger Metadatenwert")
                    value = value.strip() or None
                if name == "language":
                    value = normalize_language(value)
                    if value and (len(value) > 3 or not value.isalpha()):
                        raise ValueError("Ungültige Sprache")
                if name == "isbn" and value:
                    value = canonical_isbn13(value)
                    if not value:
                        raise ValueError("Ungültige ISBN")
                if name == "title" and not value:
                    raise ValueError("Titel darf nicht leer sein")
                if metadata.get(name, {}).get("value") != value:
                    metadata[name] = {"value": value, "source": "manual"}
                    row.edited = True
            if cover_choice is not None:
                if cover_choice == "none":
                    row.cover_path = None
                    row.cover_json = None
                    row.edited = True
                elif cover_choice in {"embedded", "external"}:
                    option_path = getattr(row, f"{cover_choice}_cover_path")
                    option_json = getattr(row, f"{cover_choice}_cover_json")
                    if not option_path or not option_json:
                        raise ValueError("Dieses Cover ist nicht verfügbar")
                    row.cover_path = option_path
                    row.cover_json = option_json
                    row.edited = True
                else:
                    raise ValueError("Dieses Cover ist nicht verfügbar")
            row.metadata_json = json.dumps(metadata, ensure_ascii=False)
            row.revision += 1
            db.commit()
            return _public(row)

    async def search_external(self, preview_id: str) -> dict:
        row = self._claim(preview_id, ("ready",), "cover_search")
        try:
            metadata = json.loads(row.metadata_json or "{}")
            isbn = metadata.get("isbn", {}).get("value")
            cover = await self.imports.cover_service.resolve(None, isbn)
            with self.session_factory() as db:
                current = db.get(ImportPreview, preview_id)
                if cover:
                    path = self.settings.staging_dir / f"{preview_id}-external-{uuid.uuid4().hex[:8]}{cover.extension}"
                    await asyncio.to_thread(path.write_bytes, cover.content)
                    old_cover = current.external_cover_path
                    current.external_cover_path = str(path)
                    current.external_cover_json = json.dumps(cover.metadata(), ensure_ascii=False)
                current.status = "ready"
                current.revision += 1
                db.commit()
                result = _public(current)
            if cover and old_cover and old_cover != str(path) and old_cover != current.cover_path:
                Path(old_cover).unlink(missing_ok=True)
            return result
        except Exception:
            with self.session_factory() as db:
                current = db.get(ImportPreview, preview_id)
                if current and current.status == "cover_search":
                    current.status = "ready"
                    db.commit()
            raise

    async def confirm(self, preview_id: str) -> dict:
        row = self._claim(preview_id, ("ready",), "archiving")
        attempted_archive = False
        try:
            with self.session_factory() as db:
                duplicate = find_book_id_by_hash(db, row.sha256)
            if duplicate:
                raise PreviewConflict(f"Datei ist bereits archiviert ({duplicate})")
            cover = None
            if row.cover_path and row.cover_json:
                info = json.loads(row.cover_json)
                cover = CoverAsset(Path(row.cover_path).read_bytes(), Path(row.cover_path).suffix,
                                   info["mime_type"], info["width"], info["height"],
                                   info["source"], info.get("provider"), info.get("source_id"),
                                   info.get("source_url"), info.get("fetched_at"))
            attempted_archive = True
            book_id, _ = await asyncio.to_thread(self.imports._archive, Path(row.staging_path),
                           row.filename, row.sha256, row.file_format,
                           _metadata(json.loads(row.metadata_json)), cover,
                           json.loads(row.providers_json))
            with self.session_factory() as db:
                current = db.get(ImportPreview, preview_id)
                current.status = "archived"
                current.book_id = book_id
                current.revision += 1
                db.commit()
                result = _public(current)
            self._remove_files(row)
            invalidate_filter_cache()
            await self.imports.events.publish("import.finished", {"book_id": book_id})
            return result
        except Exception:
            with self.session_factory() as db:
                current = db.get(ImportPreview, preview_id)
                if current and current.status == "archiving":
                    imported = find_book_id_by_hash(db, row.sha256) if attempted_archive else None
                    current.status = "archived" if imported else "ready"
                    current.book_id = imported
                    db.commit()
                    if imported:
                        self._remove_files(current)
            raise

    def discard(self, preview_id: str) -> dict:
        row = self._claim(preview_id, ("ready", "failed", "queued", "skipped"), "discarded")
        self._remove_files(row)
        return self.get(preview_id)

    def skip(self, preview_id: str) -> dict:
        self._claim(preview_id, ("ready", "failed"), "skipped")
        return self.get(preview_id)

    def resume_one(self, preview_id: str) -> dict:
        self._claim(preview_id, ("skipped",), "ready")
        return self.get(preview_id)

    def _remove_files(self, row: ImportPreview) -> None:
        Path(row.staging_path).unlink(missing_ok=True)
        for path in {row.cover_path, row.embedded_cover_path, row.external_cover_path}:
            if path:
                Path(path).unlink(missing_ok=True)

    def cleanup(self) -> None:
        with self.session_factory() as db:
            rows = db.scalars(select(ImportPreview)).all()
            for row in rows:
                if _expired(row.expires_at) and row.status not in {"analyzing", "cover_search", "archiving"}:
                    self._remove_files(row)
                    db.delete(row)
                elif row.status == "archiving":
                    duplicate = find_book_id_by_hash(db, row.sha256) if row.sha256 else None
                    row.status = "archived" if duplicate else "ready"
                    row.book_id = duplicate
                    if duplicate:
                        self._remove_files(row)
                elif row.status in {"analyzing", "cover_search"}:
                    row.status = "queued" if row.status == "analyzing" else "ready"
            db.commit()

    def resume(self) -> None:
        self.cleanup()
        with self.session_factory() as db:
            ids = db.scalars(select(ImportPreview.id).where(ImportPreview.status == "queued")).all()
        for preview_id in ids:
            self._task(self.analyze(preview_id))
