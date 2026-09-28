from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.models import Author, Book, Genre, ImportPreview, IsbnCandidateRecord, IsbnLookupCache, Tag, TranslationJob


def _move_aside(directory: Path, data_dir: Path) -> Path:
    backup = data_dir / f".clearing-{directory.name}-{uuid.uuid4().hex}"
    directory.rename(backup)
    directory.mkdir(parents=True)
    return backup


def clear_archive(settings: Settings, session_factory: Callable[[], Session]) -> dict[str, int]:
    """Remove all imported books while keeping the database schema and logs intact."""
    settings.ensure_directories()
    backups: list[tuple[Path, Path]] = []

    try:
        for directory in (settings.library_dir, settings.staging_dir):
            backups.append((directory, _move_aside(directory, settings.data_dir.resolve())))

        with session_factory() as session:
            book_count = session.scalar(select(func.count()).select_from(Book)) or 0
            session.execute(text("DELETE FROM books_fts"))
            session.execute(delete(IsbnCandidateRecord))
            session.execute(delete(TranslationJob))
            session.execute(delete(ImportPreview))
            session.execute(delete(Book))
            session.execute(delete(Author))
            session.execute(delete(Genre))
            session.execute(delete(Tag))
            session.execute(delete(IsbnLookupCache))
            session.commit()
    except Exception:
        for directory, backup in reversed(backups):
            if directory.exists():
                shutil.rmtree(directory, ignore_errors=True)
            if backup.exists():
                backup.rename(directory)
        raise

    for _directory, backup in backups:
        shutil.rmtree(backup, ignore_errors=True)

    return {"deleted_books": int(book_count)}
