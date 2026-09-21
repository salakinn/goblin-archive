from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FieldValue:
    value: Any = None
    source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "source": self.source}


@dataclass
class BookMetadata:
    title: FieldValue = field(default_factory=FieldValue)
    authors: FieldValue = field(default_factory=lambda: FieldValue([]))
    publication_year: FieldValue = field(default_factory=FieldValue)
    language: FieldValue = field(default_factory=FieldValue)
    publisher: FieldValue = field(default_factory=FieldValue)
    isbn: FieldValue = field(default_factory=FieldValue)
    genres: FieldValue = field(default_factory=lambda: FieldValue([]))
    series: FieldValue = field(default_factory=FieldValue)
    description: FieldValue = field(default_factory=FieldValue)

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {name: getattr(self, name).as_dict() for name in self.__dataclass_fields__}


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^\w\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_tag_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value))).strip()[:200]


def normalize_tag_name(value: str | None) -> str:
    return clean_tag_name(value).casefold()


LANGUAGE_ALIASES = {
    "de": "de", "de-de": "de", "de de": "de", "deu": "de", "ger": "de", "deutsch": "de", "german": "de",
    "en": "en", "eng": "en", "englisch": "en", "english": "en",
    "es": "es", "spa": "es", "spanisch": "es", "spanish": "es", "espanol": "es",
    "fr": "fr", "fra": "fr", "fre": "fr", "franzosisch": "fr", "french": "fr",
    "it": "it", "ita": "it", "italienisch": "it", "italian": "it",
    "pt": "pt", "por": "pt", "portugiesisch": "pt", "portuguese": "pt",
    "nl": "nl", "nld": "nl", "dut": "nl", "niederlandisch": "nl", "dutch": "nl",
    "pl": "pl", "pol": "pl", "polnisch": "pl", "polish": "pl",
    "ru": "ru", "rus": "ru", "russisch": "ru", "russian": "ru",
    "sv": "sv", "swe": "sv", "schwedisch": "sv", "swedish": "sv",
    "da": "da", "dan": "da", "danisch": "da", "danish": "da",
    "no": "no", "nor": "no", "norwegisch": "no", "norwegian": "no",
    "fi": "fi", "fin": "fi", "finnisch": "fi", "finnish": "fi",
    "cs": "cs", "ces": "cs", "cze": "cs", "tschechisch": "cs", "czech": "cs",
    "la": "la", "lat": "la", "latein": "la", "latin": "la",
}

LANGUAGE_LABELS = {
    "de": "Deutsch", "en": "Englisch", "es": "Spanisch", "fr": "Französisch",
    "it": "Italienisch", "pt": "Portugiesisch", "nl": "Niederländisch",
    "pl": "Polnisch", "ru": "Russisch", "sv": "Schwedisch", "da": "Dänisch",
    "no": "Norwegisch", "fi": "Finnisch", "cs": "Tschechisch", "la": "Latein",
}


def normalize_language(value: str | None) -> str | None:
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()
    normalized = unicodedata.normalize("NFKD", raw).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"\s+", " ", normalized).strip().replace("_", "-")
    if normalized in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[normalized]
    base = normalized.split("-", 1)[0]
    if base in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[base]
    return base if len(base) in (2, 3) else normalized


def language_label(value: str | None) -> str | None:
    normalized = normalize_language(value)
    if not normalized:
        return None
    return LANGUAGE_LABELS.get(normalized, normalized.upper() if len(normalized) <= 3 else normalized)


def plausible_match(candidate: BookMetadata, title: str, author: str | None) -> bool:
    wanted_title = normalize_text(title)
    got_title = normalize_text(candidate.title.value)
    if not wanted_title or not got_title:
        return False
    title_ok = wanted_title in got_title or got_title in wanted_title
    if not title_ok:
        wanted_words, got_words = set(wanted_title.split()), set(got_title.split())
        title_ok = bool(wanted_words) and len(wanted_words & got_words) / len(wanted_words) >= 0.7
    if not title_ok:
        return False
    if author and candidate.authors.value:
        wanted_author = normalize_text(author)
        return any(
            wanted_author in normalize_text(item) or normalize_text(item) in wanted_author
            for item in candidate.authors.value
        )
    return True


WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_component(value: str, fallback: str = "Unbekannt", max_length: int = 120) -> str:
    value = unicodedata.normalize("NFC", value or "")
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        value = fallback
    if value.upper() in WINDOWS_RESERVED:
        value = f"_{value}"
    return value[:max_length].rstrip(" .") or fallback


def fallback_title(filename: str) -> str:
    return sanitize_component(Path(filename).stem, "Unbekannter Titel")


def normalize_isbn(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^0-9Xx]", "", value).upper()
    return cleaned if len(cleaned) in (10, 13) else None


def year_from(value: Any) -> int | None:
    match = re.search(r"(?:18|19|20)\d{2}", str(value or ""))
    return int(match.group()) if match else None
