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
from backend.duplicates import (Fingerprint, alias_map, buckets, comparison_for_metadata, evidence, find_matches,
                                fingerprint, text_relation_paths)
from backend.extractors import detect_and_extract
from backend.imports import ImportManager, sha256_file
from backend.isbn import canonical_isbn13
from backend.metadata_normalization import apply_aliases
from backend.metadata import BookMetadata, FieldValue, fallback_title, normalize_language
from backend.title_cleanup import clean_field, clean_title
from backend.models import DuplicateDecisionEvent, DuplicateMatch, ImportPreview
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
            "duplicate_matches": [], "duplicate_decision": json.loads(row.duplicate_decision_json) if row.duplicate_decision_json else None,
            "similar_previews": [],
            "fingerprint": json.loads(row.fingerprint_json) if row.fingerprint_json else None,
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

    async def stop(self) -> None:
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    def _matches(self, db, row: ImportPreview) -> list[dict]:
        if not row.metadata_json or row.status in {"archived", "discarded"}:
            return []
        fingerprint_data = json.loads(row.fingerprint_json) if row.fingerprint_json else {}
        return find_matches(db, comparison_for_metadata(json.loads(row.metadata_json)),
                            fingerprint_data.get("text_hash"),
                            signature=tuple(fingerprint_data.get("signature") or ()),
                            source_path=Path(row.staging_path) if Path(row.staging_path).exists() else None,
                            library_dir=self.settings.library_dir,
                            word_count=fingerprint_data.get("word_count", 0))

    def _with_matches(self, db, row: ImportPreview) -> dict:
        result = _public(row)
        result["duplicate_matches"] = self._matches(db, row)
        result["similar_previews"] = self._similar_previews(db, row)
        return result

    def _current_matches(self, preview_id: str) -> tuple[list[dict], list[dict]]:
        with self.session_factory() as db:
            current = db.get(ImportPreview, preview_id)
            return self._matches(db, current), self._similar_previews(db, current)

    def _similar_previews(self, db, row: ImportPreview) -> list[dict]:
        if not row.metadata_json or row.status in {"archived", "discarded"}:
            return []
        aliases = alias_map(db)
        left = apply_aliases(comparison_for_metadata(json.loads(row.metadata_json)), aliases)
        left_fp = json.loads(row.fingerprint_json) if row.fingerprint_json else {}
        earlier = db.scalars(select(ImportPreview).where(
            ImportPreview.id != row.id, ImportPreview.expires_at > _now(),
            ImportPreview.status.in_(["ready", "skipped", "archiving"]),
            ((ImportPreview.created_at < row.created_at) |
             ((ImportPreview.created_at == row.created_at) & (ImportPreview.id < row.id))),
            ImportPreview.metadata_json.is_not(None)).order_by(
                ImportPreview.created_at.desc()).limit(200)).all()
        found = []
        for other in earlier:
            if row.sha256 and row.sha256 == other.sha256:
                continue
            right_fp = json.loads(other.fingerprint_json) if other.fingerprint_json else {}
            right = apply_aliases(comparison_for_metadata(json.loads(other.metadata_json)), aliases)
            relation = None
            if (left_fp.get("word_count", 0) >= 100 and right_fp.get("word_count", 0) >= 100 and
                    (set(buckets(tuple(left_fp.get("signature") or ()))) &
                     set(buckets(tuple(right_fp.get("signature") or ()))) or
                     left.isbn and left.isbn == right.isbn or
                     left.title_search and left.title_search == right.title_search)):
                relation = text_relation_paths(Path(row.staging_path), Path(other.staging_path),
                                               row.file_format, other.file_format,
                                               left_fp["word_count"], right_fp["word_count"])
            match = evidence(left, right,
                             left_fp.get("text_hash"), right_fp.get("text_hash"),
                             near_text=relation == "near", partial_text=relation == "partial",
                             left_words=left_fp.get("word_count", 0),
                             right_words=right_fp.get("word_count", 0))
            if match:
                found.append({"preview_id": other.id, "filename": other.filename, **match})
        return found

    def create(self, uploads: list[tuple[str, Path]], *, already_accepted: bool = False) -> list[dict]:
        if self.imports.maintenance_active or self.imports.stopping:
            raise PreviewConflict("Das Archiv wird gerade geleert")
        if not already_accepted:
            if (self.imports.backlog() + len(uploads) >
                    self.settings.max_queued_import_files):
                raise PreviewConflict("Die Importwarteschlange ist voll")
            self.imports.reserved += len(uploads)
        group = f"prg_{uuid.uuid4().hex[:12]}"
        now = _now()
        try:
            with self.session_factory() as db:
                rows = [ImportPreview(id=f"pr_{uuid.uuid4().hex[:16]}", group_id=group,
                                      filename=name, staging_path=str(path), created_at=now,
                                      expires_at=now + timedelta(hours=24), status="queued")
                        for name, path in uploads]
                db.add_all(rows)
                db.commit()
                result = [self._with_matches(db, row) for row in rows]
        except BaseException:
            if not already_accepted:
                self.imports.reserved -= len(uploads)
            raise
        for row in rows:
            self._task(self.analyze(row.id, reserved=not already_accepted))
        return result

    def list(self) -> list[dict]:
        with self.session_factory() as db:
            rows = db.scalars(select(ImportPreview).where(ImportPreview.expires_at > _now())
                              .order_by(ImportPreview.created_at.desc(), ImportPreview.id.desc())).all()
            result = [self._with_matches(db, row) for row in rows]
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
            result = self._with_matches(db, row)
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

    async def analyze(self, preview_id: str, *, enrich: bool = False,
                      reserved: bool = False) -> None:
        if reserved:
            self.imports.reserved -= 1
        await self.imports.submit(lambda: self._analyze(preview_id, enrich=enrich), internal=True)

    async def _analyze(self, preview_id: str, *, enrich: bool = False) -> None:
        try:
            row = self._claim(preview_id, ("queued", "ready", "failed"), "analyzing")
            path = Path(row.staging_path)
            digest = await asyncio.to_thread(sha256_file, path)
            with self.session_factory() as db:
                duplicate = find_book_id_by_hash(db, digest)
            file_format, extracted = await asyncio.to_thread(detect_and_extract, path, row.filename)
            analyzed = await asyncio.to_thread(fingerprint, path, file_format)
            previous_data = json.loads(row.metadata_json) if enrich and row.metadata_json else {}
            history = list(previous_data.get("_title_cleanup_history", []))
            metadata = _metadata(previous_data) if enrich and previous_data else extracted.metadata
            providers: list[str] = []
            if not metadata.title.value:
                metadata.title = FieldValue(fallback_title(row.filename), "filename")
            if metadata.title.source not in {"manual", "user"}:
                clean_field(metadata.title, history)
            if enrich:
                metadata, providers = await self.imports.enrich_metadata(metadata)
                clean_field(metadata.title, history)
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
                current.metadata_json = json.dumps({**metadata.as_dict(), "_title_cleanup_history": history}, ensure_ascii=False)
                if not enrich:
                    current.embedded_cover_json = json.dumps(cover.metadata(), ensure_ascii=False) if cover else None
                    current.embedded_cover_path = str(cover_path) if cover_path else None
                    current.cover_json = json.dumps(cover.metadata(), ensure_ascii=False) if cover else None
                    current.cover_path = str(cover_path) if cover_path else None
                    current.edited = False
                current.providers_json = json.dumps(providers)
                current.file_format = file_format
                current.sha256 = digest
                current.fingerprint_json = json.dumps(analyzed.__dict__)
                current.duplicate_decision_json = None
                if not enrich:
                    current.source_values_json = json.dumps(extracted.metadata.as_dict(), ensure_ascii=False)
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
                if name == "title":
                    clean_title(value)
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
            if changes:
                row.duplicate_decision_json = None
            row.revision += 1
            db.commit()
            return self._with_matches(db, row)

    def accept_title(self, preview_id: str, revision: int, original_title: str,
                     suggested_title: str, provider: str, model: str) -> dict:
        clean, _ = clean_title(suggested_title)
        with self.session_factory() as db:
            row = db.get(ImportPreview, preview_id)
            if not row or row.status != "ready" or _expired(row.expires_at) or row.revision != revision:
                raise PreviewConflict("Vorschau wurde inzwischen geändert oder ist abgelaufen")
            metadata = json.loads(row.metadata_json or "{}")
            if metadata.get("title", {}).get("value") != original_title:
                raise PreviewConflict("Titel wurde inzwischen geändert")
            metadata["title"] = {"value": clean, "source": "manual", "confirmed": True}
            if clean != original_title:
                metadata.setdefault("_title_cleanup_history", []).append({
                    "old_title": original_title, "new_title": clean, "source": "ai",
                    "provider": provider, "model": model, "created_at": _now().isoformat()})
            row.metadata_json = json.dumps(metadata, ensure_ascii=False)
            row.edited = True
            row.duplicate_decision_json = None
            row.revision += 1
            db.commit()
            return self._with_matches(db, row)

    def decide(self, preview_id: str, revision: int, action: str, book_id: str | None = None) -> dict:
        if action not in {"keep_both", "use_existing"}:
            raise ValueError("Ungültige Duplikatentscheidung")
        with self.session_factory() as db:
            row = db.get(ImportPreview, preview_id)
            if not row or row.status != "ready" or _expired(row.expires_at) or row.revision != revision:
                raise PreviewConflict("Vorschau wurde inzwischen geändert oder ist abgelaufen")
            matches = self._matches(db, row)
            if action == "use_existing":
                identical = book_id and book_id == find_book_id_by_hash(db, row.sha256)
                if not book_id or not (identical or any(match["book_id"] == book_id for match in matches)):
                    raise PreviewConflict("Das gewählte Buch ist kein aktueller Treffer")
                row.status = "archived"
                row.book_id = book_id
                row.duplicate_decision_json = json.dumps({"action": action, "book_id": book_id})
            else:
                if not matches:
                    raise PreviewConflict("Es gibt keinen aktuellen Duplikatverdacht")
                row.duplicate_decision_json = json.dumps({"action": action,
                    "book_ids": sorted(match["book_id"] for match in matches),
                    "matches": matches,
                    "sha256": row.sha256, "metadata_json": row.metadata_json})
            row.revision += 1
            db.commit()
            result = self._with_matches(db, row)
        if action == "use_existing":
            self._remove_files(row)
            self.imports.resolve_preview(preview_id, book_id)
        return result

    async def search_external(self, preview_id: str) -> dict:
        return await self.imports.submit(lambda: self._search_external(preview_id), internal=True)

    async def _search_external(self, preview_id: str) -> dict:
        row = self._claim(preview_id, ("ready",), "cover_search")
        try:
            metadata = json.loads(row.metadata_json or "{}")
            isbn = metadata.get("isbn", {}).get("value")
            cover = await self.imports.resolve_cover(None, isbn)
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
        return await self.imports.submit(lambda: self._confirm(preview_id), internal=True)

    async def _confirm(self, preview_id: str) -> dict:
        row = self._claim(preview_id, ("ready",), "archiving")
        attempted_archive = False
        try:
            try:
                current_digest = await asyncio.to_thread(sha256_file, Path(row.staging_path))
            except OSError as exc:
                raise PreviewConflict("Importdatei ist nicht mehr verfügbar") from exc
            if current_digest != row.sha256:
                raise PreviewConflict("Datei wurde seit der Analyse verändert")
            with self.session_factory() as db:
                duplicate = find_book_id_by_hash(db, row.sha256)
            if duplicate:
                raise PreviewConflict(f"Datei ist bereits archiviert ({duplicate})")
            matches, similar_previews = await asyncio.to_thread(self._current_matches, preview_id)
            if similar_previews:
                raise PreviewConflict("Ähnliche frühere Vorschau muss zuerst bearbeitet werden")
            if matches:
                decision = json.loads(row.duplicate_decision_json) if row.duplicate_decision_json else {}
                if (decision.get("action") != "keep_both" or
                    decision.get("book_ids") != sorted(match["book_id"] for match in matches) or
                    decision.get("matches") != matches or
                    decision.get("sha256") != row.sha256 or
                    decision.get("metadata_json") != row.metadata_json):
                    raise PreviewConflict("Duplikatverdacht muss vor dem Archivieren geprüft werden")
            cover = None
            if row.cover_path and row.cover_json:
                info = json.loads(row.cover_json)
                cover = CoverAsset(Path(row.cover_path).read_bytes(), Path(row.cover_path).suffix,
                                   info["mime_type"], info["width"], info["height"],
                                   info["source"], info.get("provider"), info.get("source_id"),
                                   info.get("source_url"), info.get("fetched_at"))
            attempted_archive = True
            analyzed = Fingerprint(**json.loads(row.fingerprint_json)) if row.fingerprint_json else None
            book_id, _ = await asyncio.to_thread(self.imports._archive, Path(row.staging_path),
                           row.filename, row.sha256, row.file_format,
                           _metadata(json.loads(row.metadata_json)), cover,
                           json.loads(row.providers_json), analyzed,
                           json.loads(row.source_values_json) if row.source_values_json else None,
                           json.loads(row.metadata_json).get("_title_cleanup_history", []))
            with self.session_factory() as db:
                current = db.get(ImportPreview, preview_id)
                current.status = "archived"
                current.book_id = book_id
                current.revision += 1
                decision = json.loads(row.duplicate_decision_json) if row.duplicate_decision_json else {}
                if decision.get("action") == "keep_both":
                    for match in matches:
                        left, right = sorted((book_id, match["book_id"]))
                        existing = db.get(DuplicateMatch, (left, right))
                        if existing is None:
                            db.add(DuplicateMatch(left_book_id=left, right_book_id=right,
                                                  kind=match["kind"], evidence_json=json.dumps(match),
                                                  decision="keep_both", decided_at=_now()))
                        db.add(DuplicateDecisionEvent(left_book_id=left, right_book_id=right,
                                                      decision="keep_both", created_at=_now()))
                db.commit()
                result = _public(current)
            self._remove_files(row)
            invalidate_filter_cache()
            self.imports.start_postprocessing(book_id, row.filename, preview_id=preview_id)
            await self.imports.events.publish("import.archived", {"book_id": book_id})
            return result
        except Exception as exc:
            imported = None
            with self.session_factory() as db:
                current = db.get(ImportPreview, preview_id)
                if current and current.status == "archiving":
                    imported = find_book_id_by_hash(db, row.sha256) if attempted_archive else None
                    current.status = "archived" if imported else "ready"
                    current.book_id = imported
                    db.commit()
                    if imported:
                        self._remove_files(current)
            if imported:
                raise PreviewConflict(f"Datei ist bereits archiviert ({imported})") from exc
            raise

    def discard(self, preview_id: str) -> dict:
        row = self._claim(preview_id, ("ready", "failed", "queued", "skipped"), "discarded")
        self._remove_files(row)
        self.imports.resolve_preview(preview_id, None, discarded=True)
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
