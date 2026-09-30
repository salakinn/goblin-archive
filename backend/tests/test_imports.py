from __future__ import annotations

import json
import re
import shutil
import uuid

import pytest
from ebooklib import epub

from backend.imports import ImportItem, ImportJob, ImportManager, sha256_file
from backend.providers import ProviderChain
from backend.repository import list_books


def make_epub(path):
    book = epub.EpubBook()
    book.set_identifier("9783608938145")
    book.set_title("Der Hobbit")
    book.set_language("de")
    book.add_author("J.R.R. Tolkien")
    book.add_metadata("DC", "publisher", "Klett-Cotta")
    book.add_metadata("DC", "date", "1937")
    book.add_metadata("DC", "subject", "Fantasy")
    chapter = epub.EpubHtml(title="Kapitel", file_name="chapter.xhtml", lang="de")
    chapter.content = "<h1>Eine unerwartete Gesellschaft</h1>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


@pytest.mark.asyncio
async def test_success_metadata_serialization_and_duplicate(db_context, tmp_path, monkeypatch):
    settings, factory = db_context
    manager = ImportManager(settings, factory, ProviderChain([]))
    first = tmp_path / "first.epub"
    make_epub(first)
    expected_hash = sha256_file(first)
    item = ImportItem("hobbit_final_NEU_2.epub")
    job = ImportJob("imp_test", "now", [item])
    await manager._process_one(job, item, first)
    assert item.status == "finished"
    with factory() as session:
        books = list_books(session)
        assert len(books) == 1
        assert books[0].sha256 == expected_hash
        metadata_path = settings.library_dir / books[0].library_path
        assert re.fullmatch(r"bk_[0-9a-f]{32}", books[0].id)
        assert uuid.UUID(hex=books[0].id[3:]).version == 4
        assert books[0].library_path == f"{books[0].id[3:7]}/{books[0].id}/book.epub"
        assert metadata_path.is_file()
        assert sha256_file(metadata_path) == expected_hash
        document = json.loads((metadata_path.parent / "metadata.json").read_text())
        assert document["schema_version"] == 1
        assert document["import"]["original_filename"] == "hobbit_final_NEU_2.epub"
        assert document["file"]["library_path"] == books[0].library_path
        assert document["metadata"]["title"] == {"value": "Der Hobbit", "source": "embedded"}
        original_path = books[0].library_path
        books[0].title = "Ein anderer Titel"
        session.commit()
        from backend import main
        monkeypatch.setattr(main, "settings", settings)
        response = main.download_book(books[0].id, session)
        assert response.filename == "Ein anderer Titel.epub"
        assert books[0].library_path == original_path

    second = tmp_path / "again.epub"
    shutil.copy2(settings.library_dir / books[0].library_path, second)
    duplicate = ImportItem("again.epub")
    await manager._process_one(ImportJob("imp_again", "now", [duplicate]), duplicate, second)
    assert duplicate.status == "duplicate"
    with factory() as session:
        assert len(list_books(session)) == 1


@pytest.mark.asyncio
async def test_failed_import_leaves_no_archive_or_database_rows(db_context, tmp_path):
    settings, factory = db_context
    manager = ImportManager(settings, factory, ProviderChain([]))
    broken = tmp_path / "broken.epub"
    broken.write_bytes(b"this is not an epub")
    item = ImportItem("broken.epub")
    await manager._process_one(ImportJob("imp_broken", "now", [item]), item, broken)
    assert item.status == "failed"
    assert not any(settings.library_dir.rglob("metadata.json"))
    with factory() as session:
        assert list_books(session) == []
