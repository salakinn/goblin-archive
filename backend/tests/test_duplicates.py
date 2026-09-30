from __future__ import annotations

import asyncio
import json
import pytest
from ebooklib import epub

from backend.duplicates import (comparison_for_metadata, evidence, find_matches, fingerprint,
                                text_relation_paths)
from backend.duplicate_scanner import DuplicateScanner
from backend.imports import ImportItem, ImportJob, ImportManager
from backend.metadata_normalization import normalize
from backend.previews import PreviewConflict, PreviewManager
from backend.providers import ProviderChain
from backend.tests.test_previews import make_epub, ready


def test_normalization_preserves_distinctions():
    first = normalize(title="  Der\u00a0Hobbit ", authors=["Tolkien, J. R. R."],
                      isbn="3-608-93814-2", language="Deutsch")
    second = normalize(title="DER HOBBIT", authors=["J. R. R. Tolkien"],
                       isbn="9783608938145", language="deu")
    assert first == second
    assert normalize(title="Band 1").title != normalize(title="Band 2").title
    assert normalize(title="Test", isbn="123").isbn is None


def test_weak_metadata_does_not_identify_other_books():
    first = normalize(title="Der lange Weg", authors=["Anna Autorin"], language="de")
    other_author = normalize(title="Der lange Weg", authors=["Berta Autorin"], language="de")
    translation = normalize(title="Der lange Weg", authors=["Anna Autorin"], language="en")
    assert evidence(first, other_author, None, None) is None
    assert evidence(first, translation, None, None) is None


def test_confirmed_author_alias_changes_metadata_match(db_context):
    from backend.tests.conftest import add_book

    settings, factory = db_context
    with factory() as db:
        add_book(db)
        candidate = comparison_for_metadata({"title": {"value": "Der Hobbit"},
                                             "authors": {"value": ["John Ronald Reuel Tolkien"]}})
        assert find_matches(db, candidate, None) == []
    scanner = DuplicateScanner(settings, factory)
    scanner.save_alias("author", "John Ronald Reuel Tolkien", "J.R.R. Tolkien")
    with factory() as db:
        assert find_matches(db, candidate, None)[0]["kind"] == "metadata"


@pytest.mark.asyncio
async def test_repacked_epub_requires_decision_and_can_keep_both(db_context):
    settings, factory = db_context
    manager = PreviewManager(ImportManager(settings, factory, ProviderChain([])))
    first_path = settings.staging_dir / "first.epub"
    make_epub(first_path)
    first = await ready(manager, first_path)
    original_id = (await manager.confirm(first["id"]))["book_id"]

    repacked = settings.staging_dir / "repacked.epub"
    make_epub(repacked)
    # Changing ZIP container bytes does not change the book text.
    with repacked.open("ab") as stream:
        stream.write(b"different-container")
    second = await ready(manager, repacked)
    assert second["duplicate_book_id"] is None
    assert second["duplicate_matches"][0]["book_id"] == original_id
    assert second["duplicate_matches"][0]["kind"] == "short_content"
    with pytest.raises(PreviewConflict):
        await manager.confirm(second["id"])
    second = manager.get(second["id"])
    approved = manager.decide(second["id"], second["revision"], "keep_both")
    assert approved["duplicate_decision"]["action"] == "keep_both"
    assert (await manager.confirm(second["id"]))["status"] == "archived"


@pytest.mark.asyncio
async def test_use_existing_removes_staging_file(db_context):
    settings, factory = db_context
    manager = PreviewManager(ImportManager(settings, factory, ProviderChain([])))
    first_path = settings.staging_dir / "first.epub"
    make_epub(first_path)
    first = await ready(manager, first_path)
    book_id = (await manager.confirm(first["id"]))["book_id"]
    second_path = settings.staging_dir / "second.epub"
    make_epub(second_path)
    with second_path.open("ab") as stream:
        stream.write(b"other")
    second = await ready(manager, second_path)
    result = manager.decide(second["id"], second["revision"], "use_existing", book_id)
    assert result["book_id"] == book_id
    assert result["status"] == "archived"
    assert not second_path.exists()


@pytest.mark.asyncio
async def test_import_keeps_embedded_and_manual_source_values(db_context):
    from sqlalchemy import select
    from backend.models import MetadataSourceValue

    settings, factory = db_context
    manager = PreviewManager(ImportManager(settings, factory, ProviderChain([])))
    path = settings.staging_dir / "sources.epub"
    make_epub(path)
    preview = await ready(manager, path)
    edited = manager.edit(preview["id"], {"title": "Manueller Titel"}, preview["revision"], None)
    archived = await manager.confirm(edited["id"])
    with factory() as db:
        rows = db.scalars(select(MetadataSourceValue).where(
            MetadataSourceValue.book_id == archived["book_id"],
            MetadataSourceValue.field == "title").order_by(MetadataSourceValue.id)).all()
    assert [(json.loads(row.value_json), row.source) for row in rows] == [
        ("Der Hobbit", "embedded"), ("Manueller Titel", "manual")]


@pytest.mark.asyncio
async def test_direct_import_routes_suspected_duplicate_to_preview(db_context):
    settings, factory = db_context
    imports = ImportManager(settings, factory, ProviderChain([]))
    previews = PreviewManager(imports)
    imports.preview_manager = previews
    first_path = settings.staging_dir / "first.epub"
    make_epub(first_path)
    first = await ready(previews, first_path)
    await previews.confirm(first["id"])
    other = settings.staging_dir / "direct.epub"
    make_epub(other)
    with other.open("ab") as stream:
        stream.write(b"direct")
    item = ImportItem("direct.epub")
    await imports._process_one(ImportJob("imp_test", "now", [item]), item, other)
    await asyncio.gather(*previews.tasks)
    assert item.status == "needs_review"
    assert other.exists()
    assert any(row["duplicate_matches"] for row in previews.list() if row["status"] == "ready")


@pytest.mark.asyncio
async def test_archive_scan_persists_matches_and_decisions(db_context):
    from sqlalchemy import func, select
    from backend.models import DuplicateDecisionEvent
    settings, factory = db_context
    manager = PreviewManager(ImportManager(settings, factory, ProviderChain([])))
    first = settings.staging_dir / "a.epub"
    make_epub(first)
    first_preview = await ready(manager, first)
    await manager.confirm(first_preview["id"])
    second = settings.staging_dir / "b.epub"
    make_epub(second)
    with second.open("ab") as stream:
        stream.write(b"repacked")
    second_preview = await ready(manager, second)
    manager.decide(second_preview["id"], second_preview["revision"], "keep_both")
    await manager.confirm(second_preview["id"])
    scanner = DuplicateScanner(settings, factory)
    job = scanner.start()
    await scanner.task
    assert scanner.get(job["id"])["status"] == "finished"
    match = scanner.list_matches()["items"][0]
    assert match["kind"] == "short_content"
    scanner.decide(match["left_book_id"], match["right_book_id"], "keep_both")
    assert DuplicateScanner(settings, factory).list_matches()["items"][0]["decision"] == "keep_both"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DuplicateDecisionEvent)) == 2


@pytest.mark.asyncio
async def test_existing_archive_is_backfilled_only_on_requested_scan(db_context):
    from sqlalchemy import delete, select
    from backend.models import BookComparison
    from backend.tests.conftest import add_book

    settings, factory = db_context
    with factory() as db:
        add_book(db)
        db.execute(delete(BookComparison))
        db.commit()
    scanner = DuplicateScanner(settings, factory)
    scanner.resume()
    assert scanner.task is None
    with factory() as db:
        assert db.scalar(select(BookComparison.book_id)) is None
    scanner.start()
    await scanner.task
    with factory() as db:
        assert db.scalar(select(BookComparison.book_id)) == "bk_test0001"


def test_unsupported_content_is_reported(tmp_path):
    result = fingerprint(tmp_path / "nothing.mobi", "mobi")
    assert result.status == "failed" or result.status == "unavailable"


def test_epub_and_pdf_with_same_text_are_comparable(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    epub_path = tmp_path / "book.epub"
    pdf_path = tmp_path / "book.pdf"
    _long_epub(epub_path, "EPUB Titel")
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    font_ref = writer._add_object(font)
    page = writer.add_blank_page(width=600, height=800)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
    stream = DecodedStreamObject()
    stream.set_data(("BT /F1 10 Tf 10 700 Td (" + " ".join(f"wort{i:04d}" for i in range(1500)) + ") Tj ET").encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(pdf_path)
    epub_fp, pdf_fp = fingerprint(epub_path, "epub"), fingerprint(pdf_path, "pdf")
    assert epub_fp.word_count >= 1000 and pdf_fp.word_count >= 1000
    assert text_relation_paths(epub_path, pdf_path, "epub", "pdf",
                               epub_fp.word_count, pdf_fp.word_count) == "near"


def test_duplicate_scan_api_starts_and_lists(db_context, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main
    settings, factory = db_context
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "make_provider_chain", lambda *_: ProviderChain([]))
    with TestClient(main.app) as client:
        started = client.post("/api/duplicates/scans")
        assert started.status_code == 202, started.text
        scan_id = started.json()["id"]
        assert client.get(f"/api/duplicates/scans/{scan_id}").status_code == 200
        assert client.get("/api/duplicates").json() == {"items": []}


def _long_epub(path, title, changed=False, count=1500):
    book = epub.EpubBook()
    book.set_identifier(title)
    book.set_title(title)
    book.set_language("de")
    book.add_author("Unbekannt")
    words = [f"wort{i:04d}" for i in range(count)]
    if changed:
        words[700] = "ersetzt"
    chapter = epub.EpubHtml(title="Kapitel", file_name="chapter.xhtml", lang="de")
    chapter.content = "<p>" + " ".join(words) + "</p>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


@pytest.mark.asyncio
async def test_near_same_text_found_without_matching_metadata(db_context):
    settings, factory = db_context
    manager = PreviewManager(ImportManager(settings, factory, ProviderChain([])))
    first = settings.staging_dir / "long1.epub"
    second = settings.staging_dir / "long2.epub"
    _long_epub(first, "Erster Titel")
    _long_epub(second, "Ganz anderer Titel", changed=True)
    original = await ready(manager, first)
    await manager.confirm(original["id"])
    candidate = await ready(manager, second)
    assert candidate["duplicate_matches"][0]["kind"] == "content"
    assert "Nahezu gleicher vollständiger Text" in candidate["duplicate_matches"][0]["reasons"]


@pytest.mark.asyncio
async def test_similar_previews_must_be_resolved_in_order(db_context):
    settings, factory = db_context
    manager = PreviewManager(ImportManager(settings, factory, ProviderChain([])))
    first = settings.staging_dir / "batch_a.epub"
    second = settings.staging_dir / "batch_b.epub"
    make_epub(first)
    make_epub(second)
    with second.open("ab") as stream:
        stream.write(b"other-container")
    first_preview = await ready(manager, first)
    second_preview = await ready(manager, second)
    assert second_preview["similar_previews"][0]["preview_id"] == first_preview["id"]
    with pytest.raises(PreviewConflict):
        await manager.confirm(second_preview["id"])
    await manager.confirm(first_preview["id"])
    refreshed = manager.get(second_preview["id"])
    assert refreshed["similar_previews"] == []
    assert refreshed["duplicate_matches"]


@pytest.mark.asyncio
async def test_sample_is_only_a_partial_content_hint(db_context):
    settings, factory = db_context
    manager = PreviewManager(ImportManager(settings, factory, ProviderChain([])))
    full = settings.staging_dir / "full.epub"
    sample = settings.staging_dir / "sample.epub"
    _long_epub(full, "Gemeinsamer Titel")
    _long_epub(sample, "Gemeinsamer Titel", count=400)
    original = await ready(manager, full)
    await manager.confirm(original["id"])
    candidate = await ready(manager, sample)
    assert candidate["duplicate_matches"][0]["kind"] == "partial_content"
