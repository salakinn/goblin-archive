"""Stable comparison keys; never modifies the displayed book metadata."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from backend.isbn import canonical_isbn13
from backend.metadata import normalize_language

VERSION = 1


def key(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value)).casefold()).strip()


def search_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", key(value))).strip()


def author_key(value: str) -> str:
    value = key(value)
    if value.count(",") == 1:
        surname, given = (part.strip() for part in value.split(","))
        if surname and given and " " not in surname:
            value = f"{given} {surname}"
    return search_key(value)


def alias_key(field: str, value: str) -> str:
    return author_key(value) if field == "author" else key(value)


@dataclass(frozen=True)
class Comparison:
    title: str
    title_search: str
    authors: tuple[str, ...]
    isbn: str | None
    reference_isbn: str | None
    language: str | None
    publisher: str
    series: str
    year: int | None


def apply_aliases(value: Comparison, aliases: dict[str, dict[str, str]]) -> Comparison:
    from dataclasses import replace

    return replace(value,
                   authors=tuple(sorted({aliases.get("author", {}).get(name, name)
                                         for name in value.authors})),
                   publisher=aliases.get("publisher", {}).get(value.publisher, value.publisher),
                   series=aliases.get("series", {}).get(value.series, value.series))


def normalize(*, title: str | None, authors: list[str] | None = None,
              isbn: str | None = None, reference_isbn: str | None = None,
              language: str | None = None, publisher: str | None = None,
              series: str | None = None, year: int | None = None) -> Comparison:
    return Comparison(
        title=key(title), title_search=search_key(title),
        authors=tuple(sorted({author_key(name) for name in (authors or []) if author_key(name)})),
        isbn=canonical_isbn13(isbn) if isbn else None,
        reference_isbn=canonical_isbn13(reference_isbn) if reference_isbn else None,
        language=normalize_language(language), publisher=key(publisher),
        series=key(series), year=year,
    )
