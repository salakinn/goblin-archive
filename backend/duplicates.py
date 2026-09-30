"""Local, explainable duplicate evidence for books and import previews."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from ebooklib import ITEM_DOCUMENT, epub
from pypdf import PdfReader
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.fb2 import children, parse_fb2
from backend.metadata_normalization import (Comparison, VERSION as NORMALIZATION_VERSION,
                                            apply_aliases, normalize)
from backend.models import Book, BookComparison, BookFingerprint, BookSimilarityBucket, MetadataAlias

TEXT_VERSION = 2
MAX_FILE_BYTES = 100_000_000
MAX_TEXT_CHARS = 4_000_000


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignore = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignore += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignore = max(0, self.ignore - 1)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_data(self, data):
        if not self.ignore:
            self.parts.append(data)


@dataclass(frozen=True)
class Fingerprint:
    text_hash: str | None
    word_count: int
    status: str
    reason: str | None = None
    signature: tuple[int, ...] = ()


def normalized_text(path: Path, file_format: str) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Datei überschreitet das Analyselimit")
    if file_format == "epub":
        book = epub.read_epub(str(path), options={"ignore_ncx": True})
        parts = []
        items = [book.get_item_with_id(item_id) for item_id, _ in book.spine if item_id != "nav"]
        for item in items:
            if item is None or item.get_type() != ITEM_DOCUMENT:
                continue
            parser = _PlainText()
            parser.feed(item.get_content().decode("utf-8", "replace"))
            parts.append(" ".join(parser.parts))
        raw = " ".join(parts)
    elif file_format == "pdf":
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise ValueError("PDF ist verschlüsselt")
        parts = []
        size = 0
        for page in reader.pages:
            part = page.extract_text() or ""
            size += len(part)
            if size > MAX_TEXT_CHARS:
                raise ValueError("Text überschreitet das Analyselimit")
            parts.append(part)
        raw = " ".join(parts)
    elif file_format == "fb2":
        parts = []
        size = 0
        for body in children(parse_fb2(path), "body"):
            if body.get("name", "").lower() in {"notes", "footnotes", "comments"}:
                continue
            for part in body.itertext():
                size += len(part)
                if size > MAX_TEXT_CHARS:
                    raise ValueError("Text überschreitet das Analyselimit")
                parts.append(part)
        raw = " ".join(parts)
    else:
        raise ValueError("Textextraktion für dieses Format fehlt")
    if len(raw) > MAX_TEXT_CHARS:
        raise ValueError("Text überschreitet das Analyselimit")
    normalized = unicodedata.normalize("NFKC", raw).replace("\u00ad", "")
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def shingles(text: str) -> set[int]:
    words = re.findall(r"\w+", text)
    if len(words) > 150_000:
        return set()
    return {int.from_bytes(hashlib.blake2b(" ".join(words[i:i+5]).encode(), digest_size=8).digest(), "big")
            for i in range(max(0, len(words) - 4))}


def signature_for(shingle_values: set[int]) -> tuple[int, ...]:
    mask = (1 << 64) - 1
    return tuple(min(((value * (2 * seed + 1) + seed * 0x9e3779b97f4a7c15) & mask)
                     for value in shingle_values) for seed in range(32)) if shingle_values else ()


def buckets(signature: tuple[int, ...]) -> list[str]:
    return [hashlib.sha256(json.dumps(signature[band * 4:band * 4 + 4]).encode()).hexdigest()
            for band in range(8)] if len(signature) == 32 else []


def text_relation_paths(left: Path, right: Path, left_format: str, right_format: str,
                        left_words: int, right_words: int) -> str | None:
    smaller, larger = min(left_words, right_words), max(left_words, right_words)
    if smaller < 100 or not larger:
        return None
    try:
        left_shingles = shingles(normalized_text(left, left_format))
        right_shingles = shingles(normalized_text(right, right_format))
        if not left_shingles or not right_shingles:
            return None
        overlap = len(left_shingles & right_shingles)
        union = len(left_shingles | right_shingles)
        if smaller >= 1000 and smaller / larger >= 0.9 and union and overlap / union >= 0.95:
            return "near"
        if smaller / larger < 0.9 and overlap / min(len(left_shingles), len(right_shingles)) >= 0.95:
            return "partial"
    except Exception:
        pass
    return None


def fingerprint(path: Path, file_format: str) -> Fingerprint:
    """Only complete, bounded extraction creates a comparable text hash."""
    try:
        normalized = normalized_text(path, file_format)
        if not normalized:
            return Fingerprint(None, 0, "unavailable", "Kein maschinenlesbarer Text")
        words = len(normalized.split())
        if words > 150_000:
            return Fingerprint(None, words, "partial", "Text überschreitet das Ähnlichkeitslimit")
        signature = signature_for(shingles(normalized)) if words >= 1000 else ()
        return Fingerprint(hashlib.sha256(normalized.encode()).hexdigest(), words,
                           "complete", signature=signature)
    except ValueError as exc:
        return Fingerprint(None, 0, "unavailable", str(exc))
    except Exception as exc:
        return Fingerprint(None, 0, "failed", type(exc).__name__)


def comparison_for_book(book: Book) -> Comparison:
    return normalize(title=book.title, authors=[author.name for author in book.authors],
                     isbn=book.isbn, reference_isbn=book.reference_isbn,
                     language=book.language, publisher=book.publisher,
                     series=book.series, year=book.publication_year)


def comparison_for_metadata(data: dict) -> Comparison:
    def value(name):
        return data.get(name, {}).get("value")
    return normalize(title=value("title"), authors=value("authors") or [],
                     isbn=value("isbn"), language=value("language"),
                     publisher=value("publisher"), series=value("series"),
                     year=value("publication_year"))


def alias_map(session: Session) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for item in session.scalars(select(MetadataAlias)):
        result.setdefault(item.field, {})[item.variant_key] = item.canonical_key
    return result


def evidence(left: Comparison, right: Comparison, left_hash: str | None,
             right_hash: str | None, *, near_text: bool = False,
             partial_text: bool = False,
             left_words: int = 0, right_words: int = 0) -> dict | None:
    reasons: list[str] = []
    conflicts: list[str] = []
    same_text = bool(left_hash and left_hash == right_hash)
    same_isbn = bool(left.isbn and left.isbn == right.isbn)
    same_title = bool(left.title_search and left.title_search == right.title_search)
    same_author = bool(set(left.authors) & set(right.authors))
    left_tokens, right_tokens = set(left.title_search.split()), set(right.title_search.split())
    title_similarity = (len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
                        if left_tokens and right_tokens else 0)
    short_text = same_text and min(left_words, right_words) < 1000
    if same_text:
        reasons.append("Gleicher kurzer Text" if short_text else "Gleicher extrahierter Text")
    elif near_text:
        reasons.append("Nahezu gleicher vollständiger Text")
    elif partial_text:
        reasons.append("Teilinhalt stimmt überein; mögliche Leseprobe oder Sammelband")
    if same_isbn:
        reasons.append("Gleiche Ausgaben-ISBN")
    if same_title and same_author:
        reasons.append("Titel und Autor stimmen überein")
    elif len(left_tokens) >= 3 and title_similarity >= 0.8 and same_author:
        reasons.append("Ähnlicher Titel und gleicher Autor")
    if left.language and right.language and left.language != right.language:
        conflicts.append("Unterschiedliche Sprache")
    if left.authors and right.authors and not same_author:
        conflicts.append("Unterschiedliche Autoren")
    if left.title and right.title and not same_title:
        conflicts.append("Unterschiedlicher Titel")
    if left.series and right.series and left.series != right.series:
        conflicts.append("Unterschiedliche Reihe")
    if (not same_text and not near_text and not partial_text and not same_isbn and
        ((left.language and right.language and left.language != right.language) or
         (left.year and right.year and abs(left.year - right.year) > 1 and
          left.publisher and right.publisher and left.publisher != right.publisher))):
        return None
    if not reasons:
        return None
    return {"kind": "short_content" if short_text else "content" if same_text or near_text else
            "edition" if same_isbn else "partial_content" if partial_text else "metadata",
            "reasons": reasons, "conflicts": conflicts, "requires_review": True}


def find_matches(session: Session, comparison: Comparison, text_hash: str | None,
                 *, signature: tuple[int, ...] = (), source_path: Path | None = None,
                 library_dir: Path | None = None, exclude_book_id: str | None = None,
                 word_count: int = 0, limit: int = 50) -> list[dict]:
    """Indexed ISBN/text candidates plus bounded title candidates."""
    aliases = alias_map(session)
    comparison = apply_aliases(comparison, aliases)
    candidates: dict[str, Book] = {}
    if comparison.isbn:
        for book in session.scalars(select(Book).join(BookComparison, BookComparison.book_id == Book.id).where(
                BookComparison.isbn == comparison.isbn,
                BookComparison.version == NORMALIZATION_VERSION).limit(limit)):
            candidates[book.id] = book
    if text_hash:
        ids = session.scalars(select(BookFingerprint.book_id).where(
            BookFingerprint.text_hash == text_hash).limit(limit)).all()
        for book in session.scalars(select(Book).where(Book.id.in_(ids))):
            candidates[book.id] = book
    for bucket in buckets(signature):
        ids = session.scalars(select(BookSimilarityBucket.book_id).where(
            BookSimilarityBucket.bucket == bucket).limit(limit)).all()
        for book in session.scalars(select(Book).where(Book.id.in_(ids))):
            candidates[book.id] = book
    if comparison.title_search:
        for book in session.scalars(select(Book).join(BookComparison, BookComparison.book_id == Book.id).where(
                BookComparison.title_search == comparison.title_search,
                BookComparison.version == NORMALIZATION_VERSION).limit(limit)):
            candidates[book.id] = book
        tokens = [token for token in comparison.title_search.split() if len(token) >= 4]
        if len(tokens) >= 3:
            ids = set()
            for token in sorted(set(tokens), key=len, reverse=True)[:3]:
                query = f'title:"{token}"'
                ids.update(row[0] for row in session.execute(text(
                    "SELECT book_id FROM books_fts WHERE books_fts MATCH :query LIMIT :limit"),
                    {"query": query, "limit": limit}))
            for book in session.scalars(select(Book).where(Book.id.in_(ids))):
                candidates[book.id] = book
    matches = []
    for book in candidates.values():
        if book.id == exclude_book_id:
            continue
        record = session.get(BookFingerprint, book.id)
        near_text = False
        partial_text = False
        if (source_path and library_dir and record and record.version == TEXT_VERSION
                and text_hash != record.text_hash
                and word_count >= 100 and record.word_count >= 100):
            old_signature = tuple(json.loads(record.signature_json or "[]"))
            if (set(buckets(signature)) & set(buckets(old_signature)) or
                    (comparison.isbn and comparison.isbn == book.isbn) or
                    (comparison.title_search and
                     comparison.title_search == comparison_for_book(book).title_search)):
                relation = text_relation_paths(source_path, library_dir / book.library_path,
                                               source_path.suffix.lower().lstrip("."), book.format,
                                               word_count, record.word_count)
                near_text = relation == "near"
                partial_text = relation == "partial"
        result = evidence(comparison, apply_aliases(comparison_for_book(book), aliases), text_hash,
                          record.text_hash if record and record.version == TEXT_VERSION else None,
                          near_text=near_text, partial_text=partial_text, left_words=word_count,
                          right_words=record.word_count if record else 0)
        if result:
            matches.append({"book_id": book.id, "title": book.title,
                            "authors": [author.name for author in book.authors],
                            "format": book.format, "isbn": book.isbn, **result})
    return sorted(matches, key=lambda item: ("content", "edition", "short_content", "partial_content", "metadata").index(item["kind"]))
