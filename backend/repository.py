from __future__ import annotations

import json
import re
import time
from threading import Lock
from typing import Any, TypeVar

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, selectinload

from backend.metadata import clean_tag_name, language_label, normalize_language, normalize_tag_name
from backend.models import Author, Book, BookComparison, Genre, Tag, book_authors, book_tags
from backend.metadata_normalization import VERSION as NORMALIZATION_VERSION, normalize as normalize_comparison

_filter_cache: tuple[str, float, dict[str, list[dict[str, Any]]]] | None = None
_filter_cache_lock = Lock()
_Named = TypeVar("_Named", Author, Genre, Tag)


def invalidate_filter_cache() -> None:
    global _filter_cache
    with _filter_cache_lock:
        _filter_cache = None


def book_to_dict(book: Book, detail: bool = False) -> dict[str, Any]:
    data = {
        "id": book.id,
        "title": book.title,
        "authors": [author.name for author in book.authors],
        "publication_year": book.publication_year,
        "language": normalize_language(book.language),
        "language_label": language_label(book.language),
        "publisher": book.publisher,
        "isbn": book.isbn,
        "reference_isbn": book.reference_isbn,
        "genres": [genre.name for genre in book.genres],
        "tags": [{"id": tag.id, "name": tag.name} for tag in book.tags],
        "series": book.series,
        "format": book.format,
        "file_size": book.file_size,
        "original_filename": book.original_filename,
        "imported_at": book.imported_at.isoformat(),
        "has_cover": bool(book.has_cover and book.cover_path),
        "cover_source": book.cover_source,
        "cover_provider": book.cover_provider,
    }
    if detail:
        raw = json.loads(book.metadata_json)
        data.update({
            "description": book.description,
            "library_path": book.library_path,
            "sha256": book.sha256,
            "metadata": raw.get("metadata", {}),
            "tag_sources": raw.get("tag_sources", {}),
            "language_detection": raw.get("language_detection"),
            "cover": raw.get("cover"),
            "work_match": json.loads(book.work_match_json) if book.work_match_json else raw.get("work_match"),
            "translation": raw.get("translation"),
        })
    return data


def _fts_text(book: Book) -> dict[str, str]:
    return {
        "book_id": book.id,
        "title": book.title or "",
        "authors": " ".join(author.name for author in book.authors),
        "publisher": book.publisher or "",
        "series": book.series or "",
        "genres": " ".join(tag.name for tag in book.tags),
        "isbn": book.isbn or "",
    }


def insert_book(session: Session, book: Book) -> None:
    session.add(book)
    session.flush()
    session.execute(text("""
        INSERT INTO books_fts(book_id, title, authors, publisher, series, genres, isbn)
        VALUES (:book_id, :title, :authors, :publisher, :series, :genres, :isbn)
    """), _fts_text(book))
    update_book_comparison(session, book)


def update_book_comparison(session: Session, book: Book) -> None:
    value = normalize_comparison(title=book.title,
                                 authors=[author.name for author in book.authors],
                                 isbn=book.isbn, reference_isbn=book.reference_isbn,
                                 language=book.language, publisher=book.publisher,
                                 series=book.series, year=book.publication_year)
    record = session.get(BookComparison, book.id)
    if record is None:
        record = BookComparison(book_id=book.id, version=NORMALIZATION_VERSION,
                                title_search=value.title_search, isbn=value.isbn,
                                authors_json=json.dumps(value.authors))
        session.add(record)
    else:
        record.version = NORMALIZATION_VERSION
        record.title_search = value.title_search
        record.isbn = value.isbn
        record.authors_json = json.dumps(value.authors)


def _get_or_create(session: Session, model: type[_Named], column: Any, value: Any,
                   **fields: Any) -> _Named:
    """Fetch a row by a unique column, inserting it only if still missing.

    Concurrent importers may insert the same name between the lookup and the
    insert. `ON CONFLICT DO NOTHING` absorbs that race, and the follow-up
    lookup returns the row the other transaction created, so no import fails
    with `UNIQUE constraint failed`.
    """
    existing = session.scalar(select(model).where(column == value))
    if existing is not None:
        return existing
    session.execute(insert(model).values(**fields).on_conflict_do_nothing())
    session.flush()
    created = session.scalar(select(model).where(column == value))
    if created is None:
        raise RuntimeError(f"{model.__name__} konnte nicht angelegt werden: {value!r}")
    return created


def get_or_create_author(session: Session, name: str) -> Author:
    return _get_or_create(session, Author, Author.name, name, name=name)


def get_or_create_genre(session: Session, name: str) -> Genre:
    return _get_or_create(session, Genre, Genre.name, name, name=name)


def get_or_create_tag(session: Session, name: str) -> Tag:
    cleaned = clean_tag_name(name)
    normalized = normalize_tag_name(cleaned)
    if not normalized:
        raise ValueError("Tag darf nicht leer sein")
    return _get_or_create(session, Tag, Tag.normalized_name, normalized,
                          name=cleaned, normalized_name=normalized)


def list_authors(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            Author.id,
            Author.name,
            func.count(book_authors.c.book_id).label("book_count"),
        )
        .outerjoin(book_authors, book_authors.c.author_id == Author.id)
        .group_by(Author.id, Author.name)
        .order_by(Author.name.collate("NOCASE"), Author.id)
    )
    return [
        {"id": author_id, "name": name, "book_count": book_count}
        for author_id, name, book_count in rows
    ]


def _book_value_counts(session: Session, column, *, descending: bool = False) -> list[dict[str, Any]]:
    ordering = column.desc() if descending else column.collate("NOCASE")
    rows = session.execute(
        select(column, func.count(Book.id).label("book_count"))
        .where(column.is_not(None), func.trim(column) != "")
        .group_by(column)
        .order_by(ordering)
    )
    return [
        {"value": str(value), "label": str(value), "book_count": book_count}
        for value, book_count in rows
    ]


def list_filter_options(session: Session) -> dict[str, list[dict[str, Any]]]:
    tag_rows = session.execute(
        select(
            Tag.name,
            func.count(book_tags.c.book_id).label("book_count"),
        )
        .outerjoin(book_tags, book_tags.c.tag_id == Tag.id)
        .group_by(Tag.id, Tag.name)
        .order_by(Tag.name.collate("NOCASE"), Tag.id)
    )
    authors = list_authors(session)
    years = session.execute(
        select(Book.publication_year, func.count(Book.id).label("book_count"))
        .where(Book.publication_year.is_not(None))
        .group_by(Book.publication_year)
        .order_by(Book.publication_year.desc())
    )
    return {
        "authors": [
            {"value": item["name"], "label": item["name"], "book_count": item["book_count"]}
            for item in authors
        ],
        "tags": [
            {"value": name, "label": name, "book_count": book_count}
            for name, book_count in tag_rows
        ],
        "languages": [
            {"value": code, "label": language_label(code), "book_count": count}
            for code, count in _language_counts(session).items()
        ],
        "publishers": _book_value_counts(session, Book.publisher),
        "formats": _book_value_counts(session, Book.format),
        "series": _book_value_counts(session, Book.series),
        "years": [
            {"value": str(year), "label": str(year), "book_count": book_count}
            for year, book_count in years
        ],
    }


def cached_filter_options(session: Session) -> dict[str, list[dict[str, Any]]]:
    global _filter_cache
    cache_key = str(session.get_bind().url)
    with _filter_cache_lock:
        if (_filter_cache is not None and _filter_cache[0] == cache_key and
                time.monotonic() - _filter_cache[1] < 60):
            return _filter_cache[2]
    value = list_filter_options(session)
    with _filter_cache_lock:
        _filter_cache = (cache_key, time.monotonic(), value)
    return value


def _language_counts(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for language, count in session.execute(
        select(Book.language, func.count(Book.id))
        .where(Book.language.is_not(None), func.trim(Book.language) != "")
        .group_by(Book.language)
    ):
        normalized = normalize_language(language)
        if normalized:
            counts[normalized] = counts.get(normalized, 0) + count
    return dict(sorted(counts.items(), key=lambda item: language_label(item[0]) or item[0]))


def get_book(session: Session, book_id: str) -> Book | None:
    return session.scalar(
        select(Book).options(
            selectinload(Book.authors), selectinload(Book.genres), selectinload(Book.tags),
        ).where(Book.id == book_id)
    )


def replace_book_fts(session: Session, book: Book) -> None:
    session.execute(text("DELETE FROM books_fts WHERE book_id = :book_id"), {"book_id": book.id})
    session.execute(text("""
        INSERT INTO books_fts(book_id, title, authors, publisher, series, genres, isbn)
        VALUES (:book_id, :title, :authors, :publisher, :series, :genres, :isbn)
    """), _fts_text(book))
    update_book_comparison(session, book)


def add_book_tag(session: Session, book: Book, name: str) -> None:
    tag = get_or_create_tag(session, name)
    if all(existing.id != tag.id for existing in book.tags):
        book.tags.append(tag)
        session.flush()
        replace_book_fts(session, book)


def remove_book_tag(session: Session, book: Book, tag_id: int) -> None:
    tag = next((item for item in book.tags if item.id == tag_id), None)
    if not tag:
        raise KeyError(tag_id)
    book.tags.remove(tag)
    session.flush()
    replace_book_fts(session, book)
    remaining = session.scalar(
        select(func.count()).select_from(book_tags).where(book_tags.c.tag_id == tag_id)
    )
    if not remaining:
        session.delete(tag)


def find_by_hash(session: Session, sha256: str) -> Book | None:
    book_id = find_book_id_by_hash(session, sha256)
    return session.get(Book, book_id) if book_id else None


def find_book_id_by_hash(session: Session, sha256: str) -> str | None:
    return session.scalar(select(Book.id).where(Book.sha256 == sha256))


def _book_query(*, author=None, tag=None, year_from=None, year_to=None,
                language=None, publisher=None, format=None, series=None):
    query = select(Book)
    if author:
        query = query.where(Book.authors.any(Author.name == author))
    if tag:
        query = query.where(Book.tags.any(Tag.normalized_name == normalize_tag_name(tag)))
    if year_from is not None:
        query = query.where(Book.publication_year >= year_from)
    if year_to is not None:
        query = query.where(Book.publication_year <= year_to)
    if language:
        query = query.where(Book.language == normalize_language(language))
    if publisher:
        query = query.where(Book.publisher == publisher)
    if format:
        query = query.where(Book.format == format.lower())
    if series:
        query = query.where(Book.series == series)
    return query


def count_books(session: Session, **filters) -> int:
    query = _book_query(**filters).with_only_columns(func.count(Book.id)).order_by(None)
    return int(session.scalar(query) or 0)


def list_books(
    session: Session,
    *, author: str | None = None,
    tag: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    language: str | None = None,
    publisher: str | None = None,
    format: str | None = None,
    series: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[Book]:
    query = _book_query(author=author, tag=tag, year_from=year_from, year_to=year_to,
                        language=language, publisher=publisher, format=format, series=series).options(
        selectinload(Book.authors), selectinload(Book.genres), selectinload(Book.tags),
    ).order_by(Book.imported_at.desc(), Book.id.desc()).offset(max(0, offset))
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query).unique())


def search_books(session: Session, query: str) -> list[Book]:
    terms = re.findall(r"[\w]+", query, flags=re.UNICODE)
    if not terms:
        return []
    fts_query = " AND ".join(f'"{term}"*' for term in terms[:12])
    ids = session.execute(
        text("SELECT book_id FROM books_fts WHERE books_fts MATCH :query ORDER BY rank LIMIT 100"),
        {"query": fts_query},
    ).scalars().all()
    if not ids:
        return []
    books = list(session.scalars(
        select(Book).options(
            selectinload(Book.authors), selectinload(Book.genres), selectinload(Book.tags),
        ).where(Book.id.in_(ids))
    ).unique())
    order = {book_id: index for index, book_id in enumerate(ids)}
    return sorted(books, key=lambda book: order[book.id])
