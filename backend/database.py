from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


def make_engine(settings: Settings | None = None):
    settings = settings or get_settings()
    settings.ensure_directories()
    engine = create_engine(
        f"sqlite:///{settings.database_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(db_engine=engine) -> None:
    from backend import models  # noqa: F401
    from backend.metadata import clean_tag_name, normalize_language, normalize_tag_name

    Base.metadata.create_all(db_engine)
    with db_engine.begin() as connection:
        # Lightweight idempotent migration for archives created before cover metadata
        # was stored in dedicated columns.
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(books)"))}
        if "has_cover" not in columns:
            connection.execute(text("ALTER TABLE books ADD COLUMN has_cover BOOLEAN NOT NULL DEFAULT 0"))
            connection.execute(text("UPDATE books SET has_cover = 1 WHERE cover_path IS NOT NULL"))
        if "cover_source" not in columns:
            connection.execute(text("ALTER TABLE books ADD COLUMN cover_source VARCHAR(20)"))
        if "cover_provider" not in columns:
            connection.execute(text("ALTER TABLE books ADD COLUMN cover_provider VARCHAR(30)"))
        if "reference_isbn" not in columns:
            connection.execute(text("ALTER TABLE books ADD COLUMN reference_isbn VARCHAR(20)"))
        if "work_match_json" not in columns:
            connection.execute(text("ALTER TABLE books ADD COLUMN work_match_json TEXT"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_books_series ON books (series)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_books_imported_id ON books (imported_at DESC, id DESC)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_book_authors_author_id ON book_authors (author_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_book_genres_genre_id ON book_genres (genre_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_book_tags_tag_id ON book_tags (tag_id)"))
        for book_id, language in connection.execute(text(
            "SELECT id, language FROM books WHERE language IS NOT NULL"
        )):
            normalized = normalize_language(language)
            if normalized != language:
                connection.execute(
                    text("UPDATE books SET language = :language WHERE id = :book_id"),
                    {"language": normalized, "book_id": book_id},
                )
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS app_migrations (
                name VARCHAR(100) PRIMARY KEY,
                applied_at VARCHAR(40) NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        tag_migration_done = connection.execute(text(
            "SELECT 1 FROM app_migrations WHERE name = 'genres_to_tags_v1'"
        )).scalar_one_or_none()
        if not tag_migration_done:
            # Genres from older archives become ordinary user-editable tags once.
            # They are not re-created after a user removes them later.
            for genre_id, raw_name in connection.execute(text("SELECT id, name FROM genres")):
                name = clean_tag_name(raw_name)
                normalized_name = normalize_tag_name(name)
                if not normalized_name:
                    continue
                tag_id = connection.execute(
                    text("SELECT id FROM tags WHERE normalized_name = :normalized_name"),
                    {"normalized_name": normalized_name},
                ).scalar_one_or_none()
                if tag_id is None:
                    result = connection.execute(
                        text("INSERT INTO tags(name, normalized_name) VALUES (:name, :normalized_name)"),
                        {"name": name, "normalized_name": normalized_name},
                    )
                    tag_id = result.lastrowid
                connection.execute(text("""
                    INSERT OR IGNORE INTO book_tags(book_id, tag_id)
                    SELECT book_id, :tag_id FROM book_genres WHERE genre_id = :genre_id
                """), {"tag_id": tag_id, "genre_id": genre_id})
            connection.execute(text(
                "INSERT INTO app_migrations(name) VALUES ('genres_to_tags_v1')"
            ))
        candidate_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(isbn_candidates)"))
        }
        if "work_score" not in candidate_columns:
            connection.execute(text(
                "ALTER TABLE isbn_candidates ADD COLUMN work_score FLOAT NOT NULL DEFAULT 0"
            ))
        if "match_type" not in candidate_columns:
            connection.execute(text(
                "ALTER TABLE isbn_candidates ADD COLUMN match_type VARCHAR(20) NOT NULL DEFAULT 'reference'"
            ))
        if "subjects_json" not in candidate_columns:
            connection.execute(text(
                "ALTER TABLE isbn_candidates ADD COLUMN subjects_json TEXT NOT NULL DEFAULT '[]'"
            ))
        if "series_json" not in candidate_columns:
            connection.execute(text(
                "ALTER TABLE isbn_candidates ADD COLUMN series_json TEXT NOT NULL DEFAULT '[]'"
            ))
        connection.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
                book_id UNINDEXED, title, authors, publisher, series, genres, isbn,
                tokenize='unicode61 remove_diacritics 2'
            )
        """))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
