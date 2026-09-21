from sqlalchemy import select, text

from backend.maintenance import clear_archive
from backend.models import Author, Book, Genre

from backend.tests.conftest import add_book


def test_clear_archive_removes_books_files_and_staging(db_context):
    settings, factory = db_context
    with factory() as session:
        add_book(session)

    archived = settings.library_dir / "Tolkien" / "book.epub"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"book")
    staged = settings.staging_dir / "upload.epub"
    staged.write_bytes(b"upload")
    log = settings.logs_dir / "goblin.log"
    log.write_text("keep", encoding="utf-8")

    result = clear_archive(settings, factory)

    assert result == {"deleted_books": 1}
    assert list(settings.library_dir.iterdir()) == []
    assert list(settings.staging_dir.iterdir()) == []
    assert log.read_text(encoding="utf-8") == "keep"
    with factory() as session:
        assert session.scalars(select(Book)).all() == []
        assert session.scalars(select(Author)).all() == []
        assert session.scalars(select(Genre)).all() == []
        assert session.execute(text("SELECT count(*) FROM books_fts")).scalar_one() == 0
