"""Stable storage locations for newly archived books."""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


BOOK_ID = re.compile(r"bk_[0-9a-f]{32}\Z")
FORMAT = re.compile(r"[a-z0-9]{1,10}\Z")


@dataclass(frozen=True)
class BookLocation:
    book_id: str
    directory: Path
    relative_file: str


def relative_book_file(book_id: str, file_format: str) -> str:
    if not BOOK_ID.fullmatch(book_id) or not FORMAT.fullmatch(file_format):
        raise ValueError("Ungültige Buch-ID oder ungültiges Dateiformat")
    return f"{book_id[3:7]}/{book_id}/book.{file_format}"


def reserve_book_location(library_dir: Path, file_format: str,
                          id_exists: Callable[[str], bool]) -> BookLocation:
    """Reserve a unique directory without touching an existing book."""
    for _ in range(20):
        book_id = f"bk_{uuid.uuid4().hex}"
        relative = relative_book_file(book_id, file_format)
        if id_exists(book_id):
            continue
        directory = library_dir / book_id[3:7] / book_id
        directory.parent.mkdir(parents=True, exist_ok=True)
        try:
            directory.mkdir()
        except FileExistsError:
            continue
        return BookLocation(book_id, directory, relative)
    raise FileExistsError("Kein konfliktfreier Archivpfad konnte erzeugt werden")


def publish_book_directory(staged: Path, location: BookLocation) -> None:
    """Replace only our empty reservation with a complete staged book."""
    if any(location.directory.iterdir()):
        raise FileExistsError(location.directory)
    os.rename(staged, location.directory)
