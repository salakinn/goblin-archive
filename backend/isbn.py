from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

import httpx
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.metadata import normalize_language, normalize_text, year_from
from backend.metadata_store import persist_metadata
from backend.models import Book, IsbnCandidateRecord, IsbnLookupCache
from backend.subjects import clean_subjects

logger = logging.getLogger(__name__)

POSITIVE_CACHE_SECONDS = 30 * 24 * 60 * 60
NEGATIVE_CACHE_SECONDS = 7 * 24 * 60 * 60


def _isbn_digits(value: Any) -> str:
    return "".join(char for char in str(value or "").upper() if char.isdigit() or char == "X")


def valid_isbn10(value: str) -> bool:
    digits = _isbn_digits(value)
    if len(digits) != 10 or "X" in digits[:-1]:
        return False
    total = sum((10 - index) * (10 if char == "X" else int(char)) for index, char in enumerate(digits))
    return total % 11 == 0


def valid_isbn13(value: str) -> bool:
    digits = _isbn_digits(value)
    if len(digits) != 13 or not digits.isdigit():
        return False
    total = sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(digits[:12]))
    return (10 - total % 10) % 10 == int(digits[-1])


def isbn10_to_isbn13(value: str) -> str | None:
    digits = _isbn_digits(value)
    if not valid_isbn10(digits):
        return None
    stem = f"978{digits[:9]}"
    total = sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(stem))
    return f"{stem}{(10 - total % 10) % 10}"


def isbn13_to_isbn10(value: str) -> str | None:
    digits = _isbn_digits(value)
    if not valid_isbn13(digits) or not digits.startswith("978"):
        return None
    stem = digits[3:12]
    total = sum((10 - index) * int(char) for index, char in enumerate(stem))
    check = (11 - total % 11) % 11
    return f"{stem}{'X' if check == 10 else check}"


def canonical_isbn13(value: Any) -> str | None:
    digits = _isbn_digits(value)
    if valid_isbn13(digits):
        return digits
    return isbn10_to_isbn13(digits) if valid_isbn10(digits) else None


def _values(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _label(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("label") or value.get("name") or value.get("id")
    text_value = str(value).strip() if value is not None else ""
    return text_value or None


def _first_label(value: Any) -> str | None:
    return next((_label(item) for item in _values(value) if _label(item)), None)


def _labels(value: Any, limit: int = 30) -> list[str]:
    result: list[str] = []
    for item in _values(value):
        label = _label(item)
        if label and label not in result:
            result.append(label)
        if len(result) >= limit:
            break
    return result


def _language(value: Any) -> str | None:
    result = _first_label(value)
    return normalize_language(result)


@dataclass
class RawIsbnCandidate:
    isbn13: str
    isbn10: str | None
    title: str
    authors: list[str]
    publisher: str | None
    publication_year: int | None
    language: str | None
    format: str | None
    source: str
    source_id: str | None
    subjects: list[str] = field(default_factory=list)
    series: list[str] = field(default_factory=list)


class IsbnProvider:
    name: str

    def __init__(self, client: httpx.AsyncClient, concurrency: int = 2):
        self.client = client
        self.semaphore = asyncio.Semaphore(concurrency)

    async def search(self, title: str, author: str | None) -> list[RawIsbnCandidate]:
        raise NotImplementedError


class LobidIsbnProvider(IsbnProvider):
    name = "lobid"

    async def search(self, title: str, author: str | None) -> list[RawIsbnCandidate]:
        escaped_title = title.replace("\\", "\\\\").replace('"', '\\"')
        escaped_author = (author or "").replace("\\", "\\\\").replace('"', '\\"')
        exact_query = f'title:"{escaped_title}"' + (
            f' AND contribution.agent.label:"{escaped_author}"' if author else ""
        )
        async def request(query: str) -> list[dict[str, Any]]:
            async with self.semaphore:
                response = await self.client.get(
                    "https://lobid.org/resources/search",
                    params={"q": query, "format": "json", "size": 10},
                )
            response.raise_for_status()
            return response.json().get("member", [])

        items = await request(exact_query)
        # Catalogues commonly store names as "Surname, Given name". A title-only
        # fallback lets local scoring compare both name orders without making the
        # provider query overly broad on the first attempt.
        if not items and author:
            items = await request(f'title:"{escaped_title}"')
        candidates: list[RawIsbnCandidate] = []
        for item in items:
            authors = [
                label for contribution in _values(item.get("contribution"))
                if isinstance(contribution, dict)
                and any(token in normalize_text(_first_label(contribution.get("role")))
                        for token in ("autor", "verfasser"))
                and (label := _first_label(contribution.get("agent")))
            ]
            publications = _values(item.get("publication"))
            publisher = _first_label(item.get("publishedBy")) or next((
                _first_label(publication.get("publishedBy"))
                for publication in publications if isinstance(publication, dict)
                and _first_label(publication.get("publishedBy"))
            ), None)
            for raw_isbn in _values(item.get("isbn")):
                isbn13 = canonical_isbn13(raw_isbn)
                if not isbn13:
                    continue
                candidates.append(RawIsbnCandidate(
                    isbn13=isbn13,
                    isbn10=isbn13_to_isbn10(isbn13),
                    title=_first_label(item.get("title")) or "",
                    authors=authors,
                    publisher=publisher,
                    publication_year=year_from(_first_label(item.get("dateOfPublication"))),
                    language=_language(item.get("language")),
                    format=_first_label(item.get("type")),
                    source=self.name,
                    source_id=_first_label(item.get("id")),
                    subjects=_labels(item.get("subject")),
                    series=_labels(item.get("series") or item.get("containedIn")),
                ))
        return candidates


class OpenLibraryIsbnProvider(IsbnProvider):
    name = "openlibrary"

    async def search(self, title: str, author: str | None) -> list[RawIsbnCandidate]:
        fields = "key,title,author_name,first_publish_year,publisher,language,isbn,edition_key,subject"
        params = {
            "title": title,
            "limit": 10,
            "fields": fields,
        }
        if author:
            params["author"] = author
        async def request(values: dict[str, Any]) -> list[dict[str, Any]]:
            async with self.semaphore:
                response = await self.client.get("https://openlibrary.org/search.json", params=values)
            response.raise_for_status()
            return response.json().get("docs", [])

        items = await request(params)
        if not items and author:
            items = await request({
                "q": f'"{title}" "{author}"', "limit": 10, "fields": fields,
            })
        candidates: list[RawIsbnCandidate] = []
        for item in items:
            seen: set[str] = set()
            for raw_isbn in _values(item.get("isbn"))[:60]:
                isbn13 = canonical_isbn13(raw_isbn)
                if not isbn13 or isbn13 in seen:
                    continue
                seen.add(isbn13)
                candidates.append(RawIsbnCandidate(
                    isbn13=isbn13,
                    isbn10=isbn13_to_isbn10(isbn13),
                    title=_first_label(item.get("title")) or "",
                    authors=[label for value in _values(item.get("author_name")) if (label := _label(value))],
                    publisher=_first_label(item.get("publisher")),
                    publication_year=year_from(item.get("first_publish_year")),
                    language=_language(item.get("language")),
                    format=None,
                    source=self.name,
                    source_id=_first_label(item.get("key")),
                    subjects=_labels(item.get("subject")),
                    series=[],
                ))
        return candidates


def _similarity(left: str | None, right: str | None) -> float:
    first, second = normalize_text(left), normalize_text(right)
    if not first or not second:
        return 0.0
    if first == second:
        return 1.0
    sequence = SequenceMatcher(None, first, second).ratio()
    first_words, second_words = set(first.split()), set(second.split())
    token = len(first_words & second_words) / max(len(first_words), len(second_words), 1)
    return max(sequence, token)


def _score(query: dict[str, Any], candidate: RawIsbnCandidate) -> float:
    earned = _similarity(query["title"], candidate.title) * 40
    available = 40.0
    author = query.get("author")
    if author:
        earned += max((_similarity(author, value) for value in candidate.authors), default=0) * 35
        available += 35
    if query.get("language") and candidate.language:
        available += 10
        if _language(query["language"]) == _language(candidate.language):
            earned += 10
    if query.get("publication_year") and candidate.publication_year:
        available += 10
        difference = abs(int(query["publication_year"]) - candidate.publication_year)
        earned += 10 if difference == 0 else 6 if difference == 1 else 0
    if query.get("publisher") and candidate.publisher:
        available += 5
        earned += _similarity(query["publisher"], candidate.publisher) * 5
    return round(earned / available * 100, 1)


def _work_score(query: dict[str, Any], candidate: RawIsbnCandidate) -> float:
    """Score work identity without publisher/year edition evidence."""
    earned = _similarity(query["title"], candidate.title) * 50
    available = 50.0
    if query.get("author"):
        earned += max(
            (_similarity(query["author"], value) for value in candidate.authors),
            default=0,
        ) * 40
        available += 40
    if query.get("language") and candidate.language:
        available += 10
        if _language(query["language"]) == _language(candidate.language):
            earned += 10
    return round(earned / available * 100, 1)


def _safe_edition_match(query: dict[str, Any], candidate: RawIsbnCandidate) -> bool:
    """An inferred ISBN is exact only with matching edition-specific evidence."""
    if _score(query, candidate) < 95:
        return False
    if not query.get("publication_year") or not candidate.publication_year:
        return False
    if abs(int(query["publication_year"]) - candidate.publication_year) > 1:
        return False
    if not query.get("publisher") or not candidate.publisher:
        return False
    return _similarity(query["publisher"], candidate.publisher) >= 0.8


class IsbnResolver:
    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[], Session],
        providers: list[IsbnProvider],
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.providers = providers

    async def close(self) -> None:
        clients = {id(provider.client): provider.client for provider in self.providers}
        for client in clients.values():
            await client.aclose()

    def _book_query(self, book_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            book = session.get(Book, book_id)
            if not book:
                raise KeyError(book_id)
            return {
                "title": book.title,
                "author": book.authors[0].name if book.authors else None,
                "language": book.language,
                "publication_year": book.publication_year,
                "publisher": book.publisher,
            }

    @staticmethod
    def _cache_key(query: dict[str, Any]) -> str:
        normalized = {
            "title": normalize_text(query.get("title")),
            "author": normalize_text(query.get("author")),
            "language": _language(query.get("language")),
            "publication_year": query.get("publication_year"),
            "publisher": normalize_text(query.get("publisher")),
        }
        return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()

    def _cached(self, key: str) -> list[RawIsbnCandidate] | None:
        with self.session_factory() as session:
            cached = session.get(IsbnLookupCache, key)
            if not cached or cached.expires_at <= time.time():
                if cached:
                    session.delete(cached)
                    session.commit()
                return None
            return [RawIsbnCandidate(**value) for value in json.loads(cached.payload_json)]

    def _store_cache(self, key: str, candidates: list[RawIsbnCandidate]) -> None:
        ttl = POSITIVE_CACHE_SECONDS if candidates else NEGATIVE_CACHE_SECONDS
        with self.session_factory() as session:
            session.execute(
                delete(IsbnLookupCache).where(IsbnLookupCache.expires_at <= time.time())
            )
            session.merge(IsbnLookupCache(
                key=key,
                payload_json=json.dumps([asdict(value) for value in candidates], ensure_ascii=False),
                expires_at=time.time() + ttl,
            ))
            session.commit()

    async def search(self, book_id: str, force: bool = False) -> dict[str, Any]:
        query = self._book_query(book_id)
        key = self._cache_key(query)
        raw = None if force else self._cached(key)
        cache_hit = raw is not None
        if raw is None:
            results = await asyncio.gather(
                *(provider.search(query["title"], query["author"]) for provider in self.providers),
                return_exceptions=True,
            )
            raw = []
            failed_providers: list[str] = []
            for provider, result in zip(self.providers, results, strict=True):
                if isinstance(result, Exception):
                    logger.warning("ISBN provider %s failed: %s", provider.name, result)
                    failed_providers.append(provider.name)
                else:
                    raw.extend(result)
            # A transient complete/partial outage must not become a long-lived
            # negative result. Positive data remains useful even if one source failed.
            if raw or not failed_providers:
                self._store_cache(key, raw)
        else:
            failed_providers = []

        merged: dict[str, dict[str, Any]] = {}
        for candidate in raw:
            score = _score(query, candidate)
            work_score = _work_score(query, candidate)
            match_type = "edition" if _safe_edition_match(query, candidate) else "reference"
            current = merged.get(candidate.isbn13)
            if current is None:
                merged[candidate.isbn13] = {
                    **asdict(candidate),
                    "score": score,
                    "work_score": work_score,
                    "match_type": match_type,
                    "sources": [candidate.source],
                    "source_ids": [candidate.source_id] if candidate.source_id else [],
                }
            else:
                authors = list(dict.fromkeys(current["authors"] + candidate.authors))
                subjects = list(dict.fromkeys(current["subjects"] + candidate.subjects))
                series = list(dict.fromkeys(current["series"] + candidate.series))
                if score > current["score"]:
                    current.update({
                        **asdict(candidate),
                        "score": score,
                    })
                current["authors"] = authors
                current["subjects"] = subjects
                current["series"] = series
                current["work_score"] = max(current["work_score"], work_score)
                if match_type == "edition":
                    current["match_type"] = "edition"
                if candidate.source not in current["sources"]:
                    current["sources"].append(candidate.source)
                if candidate.source_id and candidate.source_id not in current["source_ids"]:
                    current["source_ids"].append(candidate.source_id)

        # Providers often contribute complementary facts for the same ISBN
        # (for example German title from lobid, canonical author/work ID from
        # Open Library). Re-score the merged identity rather than discarding
        # that cross-provider evidence.
        for value in merged.values():
            combined = RawIsbnCandidate(
                isbn13=value["isbn13"], isbn10=value["isbn10"],
                title=value["title"], authors=value["authors"],
                publisher=value["publisher"], publication_year=value["publication_year"],
                language=value["language"], format=value["format"],
                source=value["source"], source_id=value["source_id"],
                subjects=value["subjects"], series=value["series"],
            )
            value["score"] = _score(query, combined)
            value["work_score"] = _work_score(query, combined)
            value["match_type"] = (
                "edition" if _safe_edition_match(query, combined) else "reference"
            )

        candidates = sorted(merged.values(), key=lambda value: (-value["score"], value["isbn13"]))
        self._persist_candidates(book_id, candidates)
        qualified = [
            value for value in candidates
            if value["match_type"] == "edition" and value["score"] >= 95
        ]
        auto = qualified[0] if len(qualified) == 1 and (
            len(qualified[0]["sources"]) >= 2 or len(candidates) == 1
        ) else None
        references = sorted(
            (value for value in candidates if value["match_type"] == "reference"),
            key=lambda value: (-value["work_score"], -value["score"]),
        )
        auto_reference = None
        if not auto and references and references[0]["work_score"] >= 90:
            top = references[0]
            same_work = [
                value for value in references
                if value["work_score"] == top["work_score"]
                and normalize_text(value["title"]) == normalize_text(top["title"])
                and {
                    normalize_text(author) for author in value["authors"]
                } == {normalize_text(author) for author in top["authors"]}
            ]
            next_different = next((
                value for value in references if value not in same_work
            ), None)
            if next_different is None or top["work_score"] - next_different["work_score"] >= 5:
                auto_reference = top
        return {
            "book_id": book_id,
            "cache_hit": cache_hit,
            "provider_errors": failed_providers,
            "auto_candidate": auto,
            "auto_reference": auto_reference,
            "candidates": candidates,
        }

    def _persist_candidates(self, book_id: str, candidates: list[dict[str, Any]]) -> None:
        with self.session_factory() as session:
            session.execute(delete(IsbnCandidateRecord).where(IsbnCandidateRecord.book_id == book_id))
            for value in candidates:
                session.add(IsbnCandidateRecord(
                    book_id=book_id,
                    isbn13=value["isbn13"],
                    isbn10=value["isbn10"],
                    title=value["title"],
                    authors_json=json.dumps(value["authors"], ensure_ascii=False),
                    publisher=value["publisher"],
                    publication_year=value["publication_year"],
                    language=value["language"],
                    format=value["format"],
                    sources_json=json.dumps(value["sources"], ensure_ascii=False),
                    source_ids_json=json.dumps(value["source_ids"], ensure_ascii=False),
                    score=value["score"],
                    work_score=value["work_score"],
                    match_type=value["match_type"],
                    subjects_json=json.dumps(value["subjects"], ensure_ascii=False),
                    series_json=json.dumps(value["series"], ensure_ascii=False),
                ))
            session.commit()

    def list_candidates(self, book_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            if not session.get(Book, book_id):
                raise KeyError(book_id)
            records = session.scalars(
                select(IsbnCandidateRecord)
                .where(IsbnCandidateRecord.book_id == book_id)
                .order_by(IsbnCandidateRecord.score.desc())
            ).all()
            return [self._record_dict(record) for record in records]

    @staticmethod
    def _record_dict(record: IsbnCandidateRecord) -> dict[str, Any]:
        return {
            "isbn13": record.isbn13,
            "isbn10": record.isbn10,
            "title": record.title,
            "authors": json.loads(record.authors_json),
            "publisher": record.publisher,
            "publication_year": record.publication_year,
            "language": record.language,
            "format": record.format,
            "sources": json.loads(record.sources_json),
            "source_ids": json.loads(record.source_ids_json),
            "score": record.score,
            "work_score": record.work_score,
            "match_type": record.match_type,
            "subjects": json.loads(record.subjects_json),
            "series": json.loads(record.series_json),
        }

    def _selected_candidate(
        self, session: Session, book_id: str, isbn: str,
    ) -> tuple[Book, IsbnCandidateRecord, dict[str, Any]]:
        isbn13 = canonical_isbn13(isbn)
        if not isbn13:
            raise ValueError("Ungültige ISBN")
        book = session.get(Book, book_id)
        if not book:
            raise KeyError(book_id)
        record = session.scalar(select(IsbnCandidateRecord).where(
            IsbnCandidateRecord.book_id == book_id,
            IsbnCandidateRecord.isbn13 == isbn13,
        ))
        if not record:
            raise ValueError("ISBN gehört nicht zu den gefundenen Kandidaten")
        return book, record, self._record_dict(record)

    def _work_match(
        self, session: Session, book_id: str, candidate: dict[str, Any],
    ) -> dict[str, Any]:
        book = session.get(Book, book_id)
        book_authors = [author.name for author in book.authors] if book else []
        matched_authors = [
            author for author in candidate["authors"]
            if any(_similarity(author, expected) >= 0.8 for expected in book_authors)
        ]
        records = session.scalars(select(IsbnCandidateRecord).where(
            IsbnCandidateRecord.book_id == book_id,
        )).all()
        selected_authors = {normalize_text(value) for value in candidate["authors"]}
        related = []
        for record in records:
            value = self._record_dict(record)
            same_identity = (
                normalize_text(value["title"]) == normalize_text(candidate["title"])
                and {normalize_text(author) for author in value["authors"]} == selected_authors
            )
            shared_id = bool(set(value["source_ids"]) & set(candidate["source_ids"]))
            if same_identity or shared_id:
                related.append(value)
        known_names = [*book_authors, *candidate["authors"], candidate["title"], candidate.get("publisher")]
        known_names.extend(value.get("publisher") for value in related)
        subject_hints = clean_subjects(
            candidate["subjects"] + [subject for value in related for subject in value["subjects"]],
            known_names=known_names, limit=30,
        )
        return {
            "title": candidate["title"],
            "authors": matched_authors or candidate["authors"],
            "language": candidate["language"],
            "confidence": candidate["work_score"],
            "sources": candidate["sources"],
            "source_ids": candidate["source_ids"],
            "reference_isbns": list(dict.fromkeys(
                [candidate["isbn13"]] + [value["isbn13"] for value in related]
            )),
            "suggested_genres": subject_hints,
            "work_series": list(dict.fromkeys(
                candidate["series"] + [item for value in related for item in value["series"]]
            ))[:20],
            "matched_at": datetime.now(timezone.utc).isoformat(),
        }

    def _write_identity(
        self,
        session: Session,
        book: Book,
        document: dict[str, Any],
        work_match: dict[str, Any],
    ) -> None:
        document["work_match"] = work_match
        book.work_match_json = json.dumps(work_match, ensure_ascii=False)
        from backend.repository import update_book_comparison
        persist_metadata(session, book, document, self.settings,
                         before_commit=lambda: update_book_comparison(session, book))

    def apply(self, book_id: str, isbn: str) -> dict[str, Any]:
        """Explicitly assign a candidate as the exact edition ISBN."""
        with self.session_factory() as session:
            book, _record, candidate = self._selected_candidate(session, book_id, isbn)
            sources = "+".join(candidate["sources"])
            document = json.loads(book.metadata_json)
            document.setdefault("metadata", {})["isbn"] = {
                "value": candidate["isbn13"],
                "source": f"isbn_resolver:{sources}",
            }
            work_match = self._work_match(session, book_id, candidate)
            book.isbn = candidate["isbn13"]
            book.reference_isbn = None
            session.execute(
                text("UPDATE books_fts SET isbn = :isbn WHERE book_id = :book_id"),
                {"isbn": candidate["isbn13"], "book_id": book_id},
            )
            self._write_identity(session, book, document, work_match)
            return candidate

    def apply_reference(self, book_id: str, isbn: str) -> dict[str, Any]:
        """Store a work-level reference without claiming it is this file's ISBN."""
        with self.session_factory() as session:
            book, _record, candidate = self._selected_candidate(session, book_id, isbn)
            document = json.loads(book.metadata_json)
            work_match = self._work_match(session, book_id, candidate)
            book.reference_isbn = candidate["isbn13"]
            self._write_identity(session, book, document, work_match)
            return {"candidate": candidate, "work_match": work_match}


def make_isbn_resolver(
    settings: Settings,
    session_factory: Callable[[], Session],
    timeout: float,
) -> IsbnResolver:
    client = httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "GoblinArchivar/0.1"},
    )
    return IsbnResolver(
        settings,
        session_factory,
        [LobidIsbnProvider(client), OpenLibraryIsbnProvider(client)],
    )
