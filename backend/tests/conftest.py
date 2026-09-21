from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from backend.config import Settings
from backend.database import init_db, make_engine
from backend.models import Book
from backend.repository import get_or_create_author, get_or_create_tag, insert_book


@pytest.fixture
def db_context(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    engine = make_engine(settings)
    init_db(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield settings, factory
    engine.dispose()


def add_book(session, *, book_id="bk_test0001", title="Der Hobbit", year=1937, genres=None, sha=None):
    book = Book(
        id=book_id,
        title=title,
        publication_year=year,
        language="de",
        publisher="Klett-Cotta",
        isbn="9783608938145",
        series="Mittelerde",
        description="Ein Abenteuer",
        format="epub",
        file_size=123,
        sha256=sha or (book_id.encode().hex() + "0" * 64)[:64],
        library_path=f"Tolkien/{book_id}/{title}.epub",
        original_filename="hobbit_final_NEU_2.epub",
        imported_at=datetime.now(timezone.utc),
        metadata_json='{"schema_version": 1, "metadata": {}}',
    )
    book.authors = [get_or_create_author(session, "J.R.R. Tolkien")]
    book.tags = [get_or_create_tag(session, value) for value in (genres or ["Fantasy"])]
    insert_book(session, book)
    session.commit()
    return book
