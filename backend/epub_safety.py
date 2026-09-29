"""Resource limits for untrusted EPUB archives, before any parser expands them."""
from __future__ import annotations

import zipfile
from pathlib import PurePosixPath

MAX_ENTRIES = 3000
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024


class UnsafeEpubError(ValueError):
    pass


def validate_epub_zip(archive: zipfile.ZipFile) -> None:
    entries = archive.infolist()
    if len(entries) > MAX_ENTRIES:
        raise UnsafeEpubError("EPUB enthält zu viele ZIP-Einträge")
    total = 0
    names: set[str] = set()
    for entry in entries:
        name = entry.filename.replace("\\", "/")
        path = PurePosixPath(name)
        if (not name or name.startswith("/") or ".." in path.parts or
                ":" in path.parts[0] or name in names or entry.flag_bits & 1):
            raise UnsafeEpubError("EPUB enthält einen ungültigen ZIP-Eintrag")
        names.add(name)
        if entry.file_size > MAX_MEMBER_BYTES:
            raise UnsafeEpubError("EPUB enthält einen zu großen ZIP-Eintrag")
        total += entry.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise UnsafeEpubError("EPUB ist entpackt zu groß")
