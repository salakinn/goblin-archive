"""Conservatively replace script-mismatched author names from an exact work match."""
from __future__ import annotations

import json
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.metadata import normalize_text
from backend.models import Author, Book


def _is_latin(value: str) -> bool:
    letters = [char for char in value if char.isalpha()]
    if not letters:
        return False
    latin = sum("LATIN" in unicodedata.name(char, "") for char in letters)
    return latin / len(letters) >= 0.8


def _person_key(value: str) -> tuple[str, str] | None:
    if "," in value:
        family, given = value.split(",", 1)
    else:
        parts = value.split()
        if len(parts) < 2:
            return None
        family, given = parts[-1], " ".join(parts[:-1])
    family_tokens = normalize_text(family).split()
    given_tokens = normalize_text(given).split()
    if not family_tokens or not given_tokens:
        return None
    return family_tokens[-1], "".join(token[0] for token in given_tokens)


def _same_person_key(candidate: tuple[str, str] | None, existing: tuple[str, str] | None) -> bool:
    if not candidate or not existing:
        return False
    family, initials = candidate
    other_family, other_initials = existing
    return family == other_family and initials == other_initials


def corrected_author_names(db: Session, book: Book, document: dict) -> list[str] | None:
    """Return canonical names only for exact-title matches with non-Latin embedded names."""
    author_field = document.get("metadata", {}).get("authors", {})
    if author_field.get("source") in {"manual", "user"} or author_field.get("confirmed"):
        return None
    current = [author.name for author in book.authors]
    if not current or all(_is_latin(name) for name in current):
        return None
    try:
        match = book.work_match_json and json.loads(book.work_match_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(match, dict) or not isinstance(match.get("authors"), list):
        return None
    if normalize_text(match.get("title")) != normalize_text(book.title):
        return None
    try:
        if float(match.get("confidence", 0)) < 60:
            return None
    except (TypeError, ValueError):
        return None
    candidates = [name.strip() for name in match["authors"]
                  if isinstance(name, str) and name.strip() and _is_latin(name)]
    if len(candidates) != len(current):
        return None

    library_names = [author.name for author in db.scalars(select(Author)).all()]
    normalized: list[str] = []
    for old, candidate in zip(current, candidates):
        candidate_key = _person_key(candidate)
        matches = [name for name in library_names
                   if _same_person_key(candidate_key, _person_key(name))]
        # Prefer the full form already used elsewhere in the archive over a catalogue
        # abbreviation such as "Rowling, J.K.".
        replacement = min(matches, key=lambda name: (name.count("."), -len(name))) if matches else candidate
        if not _same_person_key(candidate_key, _person_key(replacement)):
            return None
        normalized.append(replacement)
    return normalized if normalized != current else None
