"""Serialize metadata writes and recover file/SQLite consistency after a crash."""
from __future__ import annotations

import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

from backend.models import Book
from backend.metadata_sources import record_sources


class MetadataConflict(ValueError):
    pass


def _atomic_bytes(path: Path, value: bytes) -> None:
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _book_lock(metadata_path: Path):
    lock_path = metadata_path.with_name(".metadata.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def persist_metadata(db, book: Book, document: dict, settings, *, before_commit=None) -> None:
    metadata_path = (settings.library_dir / book.library_path).parent / "metadata.json"
    journal_dir = settings.data_dir.resolve() / "metadata-recovery"
    journal = journal_dir / f"{book.id}.json"
    metadata_text = json.dumps(document, ensure_ascii=False, indent=2)
    with _book_lock(metadata_path):
        old_bytes = metadata_path.read_bytes()
        old_document = json.loads(book.metadata_json)
        if json.loads(old_bytes) != old_document:
            raise MetadataConflict("Metadaten wurden gleichzeitig geändert; bitte neu laden")
        journal_dir.mkdir(parents=True, exist_ok=True)
        _atomic_bytes(journal, json.dumps({"book_id": book.id, "library_path": book.library_path}).encode())
        finished = False
        try:
            _atomic_bytes(metadata_path, (metadata_text + "\n").encode("utf-8"))
            book.metadata_json = metadata_text
            changed_sources = {field: entry for field, entry in document.get("metadata", {}).items()
                               if isinstance(entry, dict) and
                               ((old_document.get("metadata", {}).get(field) or {}).get("value"),
                                (old_document.get("metadata", {}).get(field) or {}).get("source")) !=
                               (entry.get("value"), entry.get("source"))}
            if changed_sources:
                record_sources(db, book.id, [changed_sources])
            db.flush()
            if before_commit:
                before_commit()
            db.commit()
            finished = True
        except Exception:
            db.rollback()
            _atomic_bytes(metadata_path, old_bytes)
            finished = True
            raise
        finally:
            # If rollback restoration fails, the marker remains for startup recovery.
            if finished:
                journal.unlink(missing_ok=True)


def recover_metadata(settings, session_factory) -> None:
    journal_dir = settings.data_dir.resolve() / "metadata-recovery"
    if not journal_dir.is_dir():
        return
    for journal in journal_dir.glob("*.json"):
        marker = json.loads(journal.read_text(encoding="utf-8"))
        with session_factory() as db:
            book = db.get(Book, marker["book_id"])
            if book is None:
                journal.unlink(missing_ok=True)
                continue
            metadata_path = (settings.library_dir / book.library_path).parent / "metadata.json"
            with _book_lock(metadata_path):
                _atomic_bytes(metadata_path, (book.metadata_json + "\n").encode("utf-8"))
                journal.unlink(missing_ok=True)
