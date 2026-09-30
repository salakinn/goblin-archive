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

    id: Mapped[str] = mapped_column(String(35), primary_key=True)
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


class BookFingerprint(Base):
    __tablename__ = "book_fingerprints"
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    text_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    signature_json: Mapped[str | None] = mapped_column(Text)


class BookSimilarityBucket(Base):
    __tablename__ = "book_similarity_buckets"
    __table_args__ = (Index("ix_book_similarity_bucket", "bucket"),)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), primary_key=True)
    band: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket: Mapped[str] = mapped_column(String(64), nullable=False)


class BookComparison(Base):
    __tablename__ = "book_comparisons"
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title_search: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    isbn: Mapped[str | None] = mapped_column(String(13), index=True)
    authors_json: Mapped[str] = mapped_column(Text, nullable=False)


class DuplicateMatch(Base):
    __tablename__ = "duplicate_matches"
    __table_args__ = (Index("ix_duplicate_matches_right", "right_book_id"),)
    left_book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), primary_key=True)
    right_book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str | None] = mapped_column(String(24))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DuplicateDecisionEvent(Base):
    __tablename__ = "duplicate_decision_events"
    __table_args__ = (Index("ix_duplicate_decision_events_pair", "left_book_id", "right_book_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    left_book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    right_book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MetadataAlias(Base):
    __tablename__ = "metadata_aliases"
    __table_args__ = (Index("ux_metadata_alias_field_variant", "field", "variant_key", unique=True),)
    id: Mapped[int] = mapped_column(primary_key=True)
    field: Mapped[str] = mapped_column(String(20), nullable=False)
    variant: Mapped[str] = mapped_column(String(300), nullable=False)
    canonical: Mapped[str] = mapped_column(String(300), nullable=False)
    variant_key: Mapped[str] = mapped_column(String(300), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MetadataSourceValue(Base):
    __tablename__ = "metadata_source_values"
    __table_args__ = (Index("ix_metadata_source_values_book_field", "book_id", "field"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(100))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DuplicateScanJob(Base):
    __tablename__ = "duplicate_scan_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    cursor: Mapped[str | None] = mapped_column(String(32))
    processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
    duplicate_book_id: Mapped[str | None] = mapped_column(String(35))
    book_id: Mapped[str | None] = mapped_column(String(35))
    error: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fingerprint_json: Mapped[str | None] = mapped_column(Text)
    duplicate_decision_json: Mapped[str | None] = mapped_column(Text)
    source_values_json: Mapped[str | None] = mapped_column(Text)


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
    book_id: Mapped[str | None] = mapped_column(String(35), index=True)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    reserved_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    input_rate: Mapped[float | None] = mapped_column(Float)
    output_rate: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    error: Mapped[str | None] = mapped_column(Text)
