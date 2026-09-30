"""Resumable archive scan with bounded database batches."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from sqlalchemy import func, or_, select

from backend.duplicates import TEXT_VERSION, buckets, comparison_for_book, find_matches, fingerprint
from backend.models import (Author, Book, BookComparison, BookFingerprint, BookSimilarityBucket,
                            DuplicateDecisionEvent, DuplicateMatch, DuplicateScanJob,
                            MetadataAlias, MetadataSourceValue)
from backend.repository import update_book_comparison
from backend.metadata_normalization import VERSION as NORMALIZATION_VERSION, alias_key
from backend.metadata_sources import record_sources

logger = logging.getLogger(__name__)


class DuplicateScanner:
    def __init__(self, settings, session_factory):
        self.settings = settings
        self.session_factory = session_factory
        self.task: asyncio.Task | None = None
        self.stop_requested = False

    def start(self) -> dict:
        with self.session_factory() as db:
            running = db.scalar(select(DuplicateScanJob).where(
                DuplicateScanJob.status.in_(["queued", "running"])).order_by(
                DuplicateScanJob.created_at.desc()).limit(1))
            if running:
                job = running
            else:
                job = DuplicateScanJob(id=f"ds_{uuid.uuid4().hex[:16]}", status="queued",
                                       processed=0, total=db.scalar(select(func.count()).select_from(Book)) or 0,
                                       errors=0, created_at=datetime.now(timezone.utc))
                db.add(job)
                db.commit()
            result = self._public(job)
        self._launch(job.id)
        return result

    def resume(self) -> None:
        with self.session_factory() as db:
            job = db.scalar(select(DuplicateScanJob).where(
                DuplicateScanJob.status.in_(["queued", "running"])).order_by(
                DuplicateScanJob.created_at.desc()).limit(1))
            job_id = job.id if job else None
        if job_id:
            self._launch(job_id)

    def _launch(self, job_id: str) -> None:
        if self.task and not self.task.done():
            return
        self.stop_requested = False
        self.task = asyncio.create_task(self._run(job_id))

    async def stop(self) -> None:
        self.stop_requested = True
        if self.task and not self.task.done():
            await self.task

    @staticmethod
    def _public(job: DuplicateScanJob) -> dict:
        return {"id": job.id, "status": job.status, "processed": job.processed,
                "total": job.total, "errors": job.errors, "cursor": job.cursor}

    def get(self, job_id: str) -> dict:
        with self.session_factory() as db:
            job = db.get(DuplicateScanJob, job_id)
            if not job:
                raise KeyError(job_id)
            return self._public(job)

    def latest(self) -> dict | None:
        with self.session_factory() as db:
            job = db.scalar(select(DuplicateScanJob).order_by(
                DuplicateScanJob.created_at.desc()).limit(1))
            return self._public(job) if job else None

    def list_aliases(self) -> list[dict]:
        with self.session_factory() as db:
            rows = db.scalars(select(MetadataAlias).order_by(MetadataAlias.field,
                                                              MetadataAlias.variant)).all()
            return [{"id": row.id, "field": row.field, "variant": row.variant,
                     "canonical": row.canonical} for row in rows]

    def save_alias(self, field: str, variant: str, canonical: str) -> dict:
        if field not in {"author", "publisher", "series"}:
            raise ValueError("Unbekanntes Aliasfeld")
        variant, canonical = variant.strip(), canonical.strip()
        if not variant or not canonical or len(variant) > 300 or len(canonical) > 300:
            raise ValueError("Alias und Ziel müssen Text mit höchstens 300 Zeichen sein")
        variant_key, canonical_key = alias_key(field, variant), alias_key(field, canonical)
        if not variant_key or not canonical_key or variant_key == canonical_key:
            raise ValueError("Alias und Ziel müssen verschieden sein")
        with self.session_factory() as db:
            column = (Author.name if field == "author" else
                      Book.publisher if field == "publisher" else Book.series)
            known_values = db.scalars(select(column).where(column.is_not(None))).all()
            if not any(alias_key(field, value) == canonical_key for value in known_values):
                raise ValueError("Das Ziel muss bereits als Autor, Verlag oder Reihe im Archiv stehen")
            if db.scalar(select(MetadataAlias.id).where(MetadataAlias.field == field,
                                                         MetadataAlias.variant_key == canonical_key).limit(1)):
                raise ValueError("Das Ziel ist selbst eine Schreibvariante")
            row = db.scalar(select(MetadataAlias).where(MetadataAlias.field == field,
                                                        MetadataAlias.variant_key == variant_key))
            if row is None:
                row = MetadataAlias(field=field, variant=variant, canonical=canonical,
                                    variant_key=variant_key, canonical_key=canonical_key,
                                    created_at=datetime.now(timezone.utc))
                db.add(row)
            else:
                row.variant = variant
                row.canonical = canonical
                row.canonical_key = canonical_key
            db.commit()
            return {"id": row.id, "field": row.field, "variant": row.variant,
                    "canonical": row.canonical}

    def delete_alias(self, alias_id: int) -> None:
        with self.session_factory() as db:
            row = db.get(MetadataAlias, alias_id)
            if not row:
                raise KeyError(alias_id)
            db.delete(row)
            db.commit()

    async def _run(self, job_id: str) -> None:
        try:
            while not self.stop_requested and await asyncio.to_thread(self._batch, job_id):
                await asyncio.sleep(0)
        except Exception:
            logger.exception("Duplicate scan %s failed", job_id)
            with self.session_factory() as db:
                job = db.get(DuplicateScanJob, job_id)
                if job:
                    job.status = "failed"
                    db.commit()

    def _batch(self, job_id: str) -> bool:
        with self.session_factory() as db:
            job = db.get(DuplicateScanJob, job_id)
            if not job:
                return False
            job.status = "running"
            books = db.scalars(select(Book).where(Book.id > (job.cursor or ""))
                               .order_by(Book.id).limit(1)).all()
            if not books:
                job.status = "finished"
                db.commit()
                return False
            for book in books:
                if db.scalar(select(MetadataSourceValue.id).where(
                        MetadataSourceValue.book_id == book.id).limit(1)) is None:
                    stored = json.loads(book.metadata_json).get("metadata", {})
                    if not stored:
                        stored = {"title": {"value": book.title, "source": None},
                                  "authors": {"value": [author.name for author in book.authors],
                                              "source": None},
                                  "isbn": {"value": book.isbn, "source": None}}
                    record_sources(db, book.id, [stored])
                update_book_comparison(db, book)
                record = db.get(BookFingerprint, book.id)
                if record is None or record.version != TEXT_VERSION:
                    result = fingerprint(self.settings.library_dir / book.library_path, book.format)
                    if record is None:
                        record = BookFingerprint(book_id=book.id, version=TEXT_VERSION,
                                                 text_hash=result.text_hash, word_count=result.word_count,
                                                 status=result.status, reason=result.reason,
                                                 signature_json=json.dumps(result.signature))
                        db.add(record)
                    else:
                        record.version = TEXT_VERSION
                        record.text_hash = result.text_hash
                        record.word_count = result.word_count
                        record.status = result.status
                        record.reason = result.reason
                        record.signature_json = json.dumps(result.signature)
                    old_buckets = {item.band: item for item in db.scalars(select(BookSimilarityBucket).where(
                        BookSimilarityBucket.book_id == book.id)).all()}
                    new_buckets = buckets(result.signature)
                    for band, old in old_buckets.items():
                        if band >= len(new_buckets):
                            db.delete(old)
                        else:
                            old.bucket = new_buckets[band]
                    for band, value in enumerate(new_buckets):
                        if band not in old_buckets:
                            db.add(BookSimilarityBucket(book_id=book.id, band=band, bucket=value))
                    if result.status == "failed":
                        job.errors += 1
                db.flush()
                matches = find_matches(db, comparison_for_book(book), record.text_hash,
                                       signature=tuple(json.loads(record.signature_json or "[]")),
                                       source_path=self.settings.library_dir / book.library_path,
                                       library_dir=self.settings.library_dir, word_count=record.word_count,
                                       exclude_book_id=book.id)
                current_pairs = {tuple(sorted((book.id, match["book_id"]))) for match in matches}
                previous = db.scalars(select(DuplicateMatch).where(or_(
                    DuplicateMatch.left_book_id == book.id,
                    DuplicateMatch.right_book_id == book.id))).all()
                for old in previous:
                    if (old.left_book_id, old.right_book_id) not in current_pairs:
                        if old.decision:
                            db.add(DuplicateDecisionEvent(left_book_id=old.left_book_id,
                                                          right_book_id=old.right_book_id,
                                                          decision=None, created_at=datetime.now(timezone.utc)))
                        db.delete(old)
                for match in matches:
                    left, right = sorted((book.id, match["book_id"]))
                    existing = db.get(DuplicateMatch, (left, right))
                    if existing is None:
                        db.add(DuplicateMatch(left_book_id=left, right_book_id=right,
                                              kind=match["kind"], evidence_json=json.dumps(match)))
                    else:
                        old = json.loads(existing.evidence_json)
                        if any(old.get(field) != match.get(field) for field in ("kind", "reasons", "conflicts")):
                            if existing.decision:
                                db.add(DuplicateDecisionEvent(left_book_id=left, right_book_id=right,
                                                              decision=None, created_at=datetime.now(timezone.utc)))
                            existing.decision = None
                            existing.decided_at = None
                        existing.kind = match["kind"]
                        existing.evidence_json = json.dumps(match)
                job.cursor = book.id
                job.processed += 1
            db.commit()
            return True

    def list_matches(self, *, offset: int = 0, limit: int = 50,
                     decision: str | None = None) -> dict:
        with self.session_factory() as db:
            query = select(DuplicateMatch)
            if decision == "open":
                query = query.where(DuplicateMatch.decision.is_(None))
            elif decision in {"confirmed", "keep_both", "not_duplicate"}:
                query = query.where(DuplicateMatch.decision == decision)
            rows = db.scalars(query.order_by(DuplicateMatch.left_book_id,
                                             DuplicateMatch.right_book_id)
                              .offset(max(0, offset)).limit(max(1, min(limit, 100)))).all()
            return {"items": [self._serialize_match(db, row) for row in rows]}

    @staticmethod
    def _serialize_match(db, row: DuplicateMatch) -> dict:
        return {"left_book_id": row.left_book_id, "right_book_id": row.right_book_id,
                "left_title": db.get(Book, row.left_book_id).title,
                "right_title": db.get(Book, row.right_book_id).title,
                "kind": row.kind, "evidence": json.loads(row.evidence_json),
                "decision": row.decision}

    def matches_for_book(self, book_id: str) -> dict:
        with self.session_factory() as db:
            if db.get(Book, book_id) is None:
                raise KeyError(book_id)
            rows = db.scalars(select(DuplicateMatch).where(or_(
                DuplicateMatch.left_book_id == book_id,
                DuplicateMatch.right_book_id == book_id)).limit(100)).all()
            return {"items": [self._serialize_match(db, row) for row in rows]}

    def decide(self, left: str, right: str, decision: str | None) -> dict:
        if decision not in {None, "confirmed", "keep_both", "not_duplicate"}:
            raise ValueError("Ungültige Entscheidung")
        left, right = sorted((left, right))
        with self.session_factory() as db:
            row = db.get(DuplicateMatch, (left, right))
            if not row:
                raise KeyError((left, right))
            row.decision = decision
            row.decided_at = datetime.now(timezone.utc) if decision else None
            db.add(DuplicateDecisionEvent(left_book_id=left, right_book_id=right,
                                          decision=decision, created_at=datetime.now(timezone.utc)))
            db.commit()
            return {"left_book_id": left, "right_book_id": right, "decision": decision}
