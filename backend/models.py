from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Table, Text, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


book_authors = Table(
    "book_authors",
    Base.metadata,
    Column("book_id", ForeignKey("books.id", ondelete="CASCADE"), primary_key=True),
    Column("author_id", ForeignKey("authors.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_book_authors_author_id", "author_id"),
)

book_genres = Table(
    "book_genres",
    Base.metadata,
    Column("book_id", ForeignKey("books.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_book_genres_genre_id", "genre_id"),
)

book_tags = Table(
    "book_tags",
    Base.metadata,
    Column("book_id", ForeignKey("books.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_book_tags_tag_id", "tag_id"),
)


class Author(Base):
    __tablename__ = "authors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300), unique=True, index=True)


class Genre(Base):
    __tablename__ = "genres"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)


class Tag(Base):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (
        Index("ix_books_publication_year", "publication_year"),
        Index("ix_books_language", "language"),
        Index("ix_books_publisher", "publisher"),
        Index("ix_books_isbn", "isbn"),
        Index("ix_books_format", "format"),
        Index("ix_books_series", "series"),
        Index("ix_books_imported_id", "imported_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    publication_year: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(30))
    publisher: Mapped[str | None] = mapped_column(String(300))
    isbn: Mapped[str | None] = mapped_column(String(20))
    reference_isbn: Mapped[str | None] = mapped_column(String(20))
    work_match_json: Mapped[str | None] = mapped_column(Text)
    series: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    format: Mapped[str] = mapped_column(String(10))
    file_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    library_path: Mapped[str] = mapped_column(Text, unique=True)
    has_cover: Mapped[bool] = mapped_column(Boolean, default=False)
    cover_path: Mapped[str | None] = mapped_column(Text)
    cover_source: Mapped[str | None] = mapped_column(String(20))
    cover_provider: Mapped[str | None] = mapped_column(String(30))
    original_filename: Mapped[str] = mapped_column(String(500))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[str] = mapped_column(Text)

    authors: Mapped[list[Author]] = relationship(secondary=book_authors, lazy="selectin")
    genres: Mapped[list[Genre]] = relationship(secondary=book_genres, lazy="selectin")
    tags: Mapped[list[Tag]] = relationship(secondary=book_tags, lazy="selectin")


class IsbnCandidateRecord(Base):
    __tablename__ = "isbn_candidates"
    __table_args__ = (
        Index("ix_isbn_candidates_book_id", "book_id"),
        Index("ix_isbn_candidates_isbn13", "isbn13"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[str] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False,
    )
    isbn13: Mapped[str] = mapped_column(String(13), nullable=False)
    isbn10: Mapped[str | None] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    authors_json: Mapped[str] = mapped_column(Text, nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(300))
    publication_year: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(30))
    format: Mapped[str | None] = mapped_column(String(50))
    sources_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    work_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    match_type: Mapped[str] = mapped_column(String(20), nullable=False, default="reference")
    subjects_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    series_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class IsbnLookupCache(Base):
    __tablename__ = "isbn_lookup_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False, index=True)


class TranslationJob(Base):
    __tablename__ = "translation_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ImportPreview(Base):
    __tablename__ = "import_previews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(32), index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    staging_path: Mapped[str] = mapped_column(Text, nullable=False)
    cover_path: Mapped[str | None] = mapped_column(Text)
    embedded_cover_path: Mapped[str | None] = mapped_column(Text)
    embedded_cover_json: Mapped[str | None] = mapped_column(Text)
    external_cover_path: Mapped[str | None] = mapped_column(Text)
    external_cover_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    metadata_json: Mapped[str | None] = mapped_column(Text)
    cover_json: Mapped[str | None] = mapped_column(Text)
    providers_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    file_format: Mapped[str | None] = mapped_column(String(10))
    sha256: Mapped[str | None] = mapped_column(String(64))
    duplicate_book_id: Mapped[str | None] = mapped_column(String(32))
    book_id: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TranslationGlossary(Base):
    __tablename__ = "translation_glossaries"
    __table_args__ = (Index("ix_translation_glossaries_languages", "source_language", "target_language"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_language: Mapped[str] = mapped_column(String(30), nullable=False)
    target_language: Mapped[str] = mapped_column(String(30), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    entries_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    style: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AIUsage(Base):
    __tablename__ = "ai_usage"
    __table_args__ = (Index("ix_ai_usage_created_at", "created_at"),
                      Index("ix_ai_usage_feature", "feature"))

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature: Mapped[str] = mapped_column(String(50), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(32), index=True)
    book_id: Mapped[str | None] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    reserved_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    input_rate: Mapped[float | None] = mapped_column(Float)
    output_rate: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    error: Mapped[str | None] = mapped_column(Text)
