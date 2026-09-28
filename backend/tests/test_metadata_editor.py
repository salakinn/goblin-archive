import json

import pytest
from fastapi import HTTPException

from backend import main
from backend.metadata_store import MetadataConflict, persist_metadata, recover_metadata
from backend.models import Book
from backend.repository import search_books
from backend.tests.conftest import add_book


def _book_with_file(settings, factory):
    with factory() as db:
        book = add_book(db)
        document = {"schema_version": 1, "metadata": {
            "title": {"value": book.title, "source": "provider"},
            "language": {"value": book.language, "source": "embedded"}}}
        book.metadata_json = json.dumps(document, ensure_ascii=False)
        path = settings.library_dir / book.library_path
        path.parent.mkdir(parents=True)
        path.write_bytes(b"book")
        (path.parent / "metadata.json").write_text(book.metadata_json + "\n", encoding="utf-8")
        db.commit()
        return book.id, path.parent / "metadata.json"


def test_edit_only_marks_changed_fields_manual_and_updates_search(db_context, monkeypatch):
    settings, factory = db_context
    book_id, metadata_path = _book_with_file(settings, factory)
    monkeypatch.setattr(main, "settings", settings)
    with factory() as db:
        result = main.update_book_metadata(book_id, main.BookMetadataUpdate(
            title="Der neue Hobbit", authors=["J.R.R. Tolkien"], publication_year=1937,
            language="de", publisher="Klett-Cotta", isbn="9783608938145",
            series="Mittelerde", description="Ein Abenteuer"), db)
        assert result["metadata"]["title"]["source"] == "manual"
        assert result["metadata"]["language"]["source"] == "embedded"
        assert search_books(db, "neue Hobbit")[0].id == book_id
        assert json.loads(metadata_path.read_text()) == json.loads(db.get(Book, book_id).metadata_json)


def test_invalid_isbn_and_blank_title_are_rejected(db_context, monkeypatch):
    settings, factory = db_context
    book_id, _path = _book_with_file(settings, factory)
    monkeypatch.setattr(main, "settings", settings)
    with factory() as db:
        with pytest.raises(HTTPException) as error:
            main.update_book_metadata(book_id, main.BookMetadataUpdate(title="  "), db)
        assert error.value.status_code == 400
        with pytest.raises(HTTPException) as error:
            main.update_book_metadata(book_id, main.BookMetadataUpdate(title="Der Hobbit", isbn="123"), db)
        assert error.value.status_code == 400


def test_crash_recovery_restores_file_from_database(db_context):
    settings, factory = db_context
    book_id, metadata_path = _book_with_file(settings, factory)
    journal = settings.data_dir / "metadata-recovery" / f"{book_id}.json"
    journal.parent.mkdir()
    journal.write_text(json.dumps({"book_id": book_id}), encoding="utf-8")
    metadata_path.write_text('{"metadata": {"title": "uncommitted"}}', encoding="utf-8")
    recover_metadata(settings, factory)
    with factory() as db:
        assert json.loads(metadata_path.read_text()) == json.loads(db.get(Book, book_id).metadata_json)
    assert not journal.exists()


def test_stale_metadata_write_does_not_overwrite_newer_changes(db_context):
    settings, factory = db_context
    book_id, metadata_path = _book_with_file(settings, factory)
    with factory() as first, factory() as second:
        old = first.get(Book, book_id)
        stale = second.get(Book, book_id)
        first_doc = json.loads(old.metadata_json)
        first_doc['metadata']['title'] = {'value': 'Neue Fassung', 'source': 'manual'}
        old.title = 'Neue Fassung'
        persist_metadata(first, old, first_doc, settings)
        with pytest.raises(MetadataConflict):
            persist_metadata(second, stale, json.loads(stale.metadata_json), settings)
    assert json.loads(metadata_path.read_text())['metadata']['title']['value'] == 'Neue Fassung'


def test_failed_database_commit_restores_metadata_file(db_context, monkeypatch):
    settings, factory = db_context
    book_id, metadata_path = _book_with_file(settings, factory)
    before = metadata_path.read_bytes()
    with factory() as db:
        book = db.get(Book, book_id)
        document = json.loads(book.metadata_json)
        document['metadata']['title'] = {'value': 'Nicht gespeichert', 'source': 'manual'}
        book.title = 'Nicht gespeichert'

        def fail_commit():
            raise RuntimeError('injected database failure')

        monkeypatch.setattr(db, 'commit', fail_commit)
        with pytest.raises(RuntimeError, match='injected'):
            persist_metadata(db, book, document, settings)
    assert metadata_path.read_bytes() == before
    assert not (settings.data_dir / 'metadata-recovery' / f'{book_id}.json').exists()
