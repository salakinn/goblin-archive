from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, selectinload

from backend.metadata import clean_tag_name, language_label, normalize_language, normalize_tag_name
from backend.models import Author, Book, Genre, Tag, book_authors, book_tags


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
            "cover": raw.get("cover"),
            "work_match": json.loads(book.work_match_json) if book.work_match_json else raw.get("work_match"),
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


def get_or_create_author(session: Session, name: str) -> Author:
    existing = session.scalar(select(Author).where(Author.name == name))
    return existing or Author(name=name)


def get_or_create_genre(session: Session, name: str) -> Genre:
    existing = session.scalar(select(Genre).where(Genre.name == name))
    return existing or Genre(name=name)


def get_or_create_tag(session: Session, name: str) -> Tag:
    cleaned = clean_tag_name(name)
    normalized = normalize_tag_name(cleaned)
    if not normalized:
        raise ValueError("Tag darf nicht leer sein")
    existing = session.scalar(select(Tag).where(Tag.normalized_name == normalized))
    if existing:
        return existing
    tag = Tag(name=cleaned, normalized_name=normalized)
    session.add(tag)
    session.flush()
    return tag


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
    return session.scalar(select(Book).where(Book.sha256 == sha256))


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
) -> list[Book]:
    query = select(Book).options(
        selectinload(Book.authors), selectinload(Book.genres), selectinload(Book.tags),
    ).order_by(Book.imported_at.desc())
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
