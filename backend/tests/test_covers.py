from __future__ import annotations

import asyncio
import json
import shutil
import struct
import zlib
from pathlib import Path

import httpx
import pytest
from ebooklib import epub
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from backend import main
from backend.covers import (
    CoverAsset,
    CoverService,
    GoogleBooksCoverProvider,
    OpenLibraryCoverProvider,
    embedded_cover,
)
from backend.database import get_db
from backend.extractors import extract_epub, extract_pdf
from backend.imports import ImportItem, ImportJob, ImportManager
from backend.metadata import BookMetadata, FieldValue
from backend.previews import PreviewManager
from backend.providers import ProviderChain
from backend.repository import get_book
from backend.tests.conftest import add_book


def png(width: int = 2, height: int = 3, color: bytes = b"\x4a\x7c\x32") -> bytes:
    def chunk(kind: bytes, value: bytes) -> bytes:
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + color * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def make_epub(
    path: Path,
    *,
    with_cover: bool = False,
    isbn: str | None = "9783608938145",
) -> None:
    book = epub.EpubBook()
    book.set_identifier(isbn or "urn:uuid:goblin-cover-test")
    book.set_title("Der Hobbit")
    book.set_language("de")
    book.add_author("J.R.R. Tolkien")
    if with_cover:
        book.set_cover("images/jacket.png", png())
    chapter = epub.EpubHtml(title="Kapitel", file_name="chapter.xhtml", lang="de")
    chapter.content = "<h1>Kapitel</h1>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


def make_epub2_wrapped_cover(path: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("3404105311")
    book.set_title("Der Mann aus St. Petersburg")
    book.set_language("de")
    book.add_author("Ken Follett")
    image = epub.EpubItem(
        uid="title-image",
        file_name="Images/book-art.png",
        media_type="image/png",
        content=png(4, 6),
    )
    titlepage = epub.EpubHtml(
        uid="titlepage",
        title="Cover",
        file_name="Text/titlepage.xhtml",
        lang="de",
    )
    titlepage.content = '<div><img alt="Titelbild" src="../Images/book-art.png" />&nbsp;</div>'
    chapter = epub.EpubHtml(title="Kapitel", file_name="Text/chapter.xhtml", lang="de")
    chapter.content = "<h1>Kapitel</h1>"
    book.add_item(image)
    book.add_item(titlepage)
    book.add_item(chapter)
    book.guide = [{"type": "cover", "title": "Cover", "href": "Text/titlepage.xhtml"}]
    book.toc = (chapter,)
    book.spine = ["nav", titlepage, chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


def make_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=450)
    writer.add_blank_page(width=450, height=300)
    writer.add_metadata({"/Title": "PDF mit Titelseite"})
    writer.write(path)


def asset(data: bytes, provider: str = "openlibrary") -> CoverAsset:
    parsed = embedded_cover(data)
    assert parsed
    return CoverAsset(
        parsed.content, parsed.extension, parsed.mime_type, parsed.width, parsed.height,
        "external", provider, "9783608938145", f"https://example.test/{provider}.png", "2026-01-01T00:00:00+00:00",
    )


class StaticProvider:
    def __init__(self, name: str, result: CoverAsset | None = None, error: Exception | None = None):
        self.name, self.result, self.error = name, result, error
        self.calls = 0

    async def fetch(self, _isbn: str):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class InferredIsbnMetadataProvider:
    name = "metadata"

    async def search(self, _isbn, _title, _author):
        return BookMetadata(isbn=FieldValue("9783608938145", self.name))


def test_embedded_epub_cover_is_found_and_extracted_without_recompression(tmp_path):
    path = tmp_path / "covered.epub"
    make_epub(path, with_cover=True)
    extracted = extract_epub(path)
    assert extracted.cover == png()
    cover = embedded_cover(extracted.cover)
    assert cover and (cover.extension, cover.width, cover.height) == (".png", 2, 3)


def test_epub2_guide_cover_page_resolves_its_nested_image(tmp_path):
    path = tmp_path / "epub2-guide-cover.epub"
    make_epub2_wrapped_cover(path)

    extracted = extract_epub(path)

    assert extracted.cover == png(4, 6)


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="Poppler fehlt")
def test_pdf_first_page_is_rendered_as_cover(tmp_path):
    path = tmp_path / "covered.pdf"
    make_pdf(path)

    extracted = extract_pdf(path)
    cover = embedded_cover(extracted.cover)

    assert extracted.metadata.title.value == "PDF mit Titelseite"
    assert cover and cover.extension == ".jpg"
    assert cover.height == 1600
    assert 1000 <= cover.width <= 1100


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="Poppler fehlt")
@pytest.mark.asyncio
async def test_pdf_first_page_cover_is_archived(db_context):
    settings, factory = db_context
    manager = ImportManager(settings, factory, ProviderChain([]), CoverService([]))
    path = settings.staging_dir / "covered.pdf"
    make_pdf(path)
    item = ImportItem(path.name)

    await manager._process_one(ImportJob("imp_pdf_cover", "now", [item]), item, path)

    assert item.status == "finished"
    with factory() as session:
        book = get_book(session, item.book_id)
        assert book and book.cover_source == "embedded"
        cover = embedded_cover((settings.library_dir / book.cover_path).read_bytes())
        assert cover and cover.height == 1600


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="Poppler fehlt")
@pytest.mark.asyncio
async def test_pdf_first_page_cover_is_selected_in_preview(db_context):
    settings, factory = db_context
    manager = PreviewManager(ImportManager(settings, factory, ProviderChain([]), CoverService([])))
    path = settings.staging_dir / "preview.pdf"
    make_pdf(path)

    preview_id = manager.create([("preview.pdf", path)])[0]["id"]
    await asyncio.gather(*manager.tasks)
    preview = manager.get(preview_id)

    assert preview["status"] == "ready"
    assert preview["cover_selected"]
    assert preview["cover"]["source"] == "embedded"
    archived = await manager.confirm(preview_id)
    with factory() as session:
        book = get_book(session, archived["book_id"])
        assert book and book.cover_source == "embedded"


@pytest.mark.asyncio
async def test_embedded_cover_is_archived_with_source_metadata(db_context, tmp_path):
    settings, factory = db_context
    manager = ImportManager(settings, factory, ProviderChain([]), CoverService([]))
    path = tmp_path / "covered.epub"
    make_epub(path, with_cover=True)
    item = ImportItem(path.name)

    await manager._process_one(ImportJob("imp_embedded_cover", "now", [item]), item, path)

    assert item.status == "finished"
    with factory() as session:
        book = get_book(session, item.book_id)
        assert book and book.cover_source == "embedded" and book.cover_provider is None
        cover_path = settings.library_dir / book.cover_path
        assert cover_path.name == "cover.png"
        assert cover_path.read_bytes() == png()
        document = json.loads((cover_path.parent / "metadata.json").read_text(encoding="utf-8"))
        assert document["cover"] == {
            "filename": "cover.png",
            "source": "embedded",
            "provider": None,
            "source_id": None,
            "width": 2,
            "height": 3,
            "mime_type": "image/png",
        }


@pytest.mark.asyncio
async def test_open_library_cover_lookup_uses_large_isbn_image():
    seen: list[str] = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        return httpx.Response(200, content=png(), headers={"content-type": "image/png"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cover = await OpenLibraryCoverProvider(client).fetch("9783608938145")
    assert cover and cover.provider == "openlibrary"
    assert "/9783608938145-L.jpg" in seen[0] and "default=false" in seen[0]


@pytest.mark.asyncio
async def test_open_library_miss_falls_back_to_google_books_largest_image():
    requested: list[str] = []

    def handler(request: httpx.Request):
        requested.append(str(request.url))
        if request.url.host == "covers.openlibrary.org":
            return httpx.Response(404, request=request)
        if request.url.host == "www.googleapis.com":
            return httpx.Response(200, json={"items": [{"volumeInfo": {
                "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9783608938145"}],
                "imageLinks": {"thumbnail": "http://books.google.com/thumb", "extraLarge": "http://books.google.com/xl"},
            }}]}, request=request)
        return httpx.Response(200, content=png(4, 6), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = CoverService([OpenLibraryCoverProvider(client), GoogleBooksCoverProvider(client)])
        cover = await service.resolve(None, "978-3-608-93814-5")
    assert cover and cover.provider == "googlebooks" and (cover.width, cover.height) == (4, 6)
    assert any(url == "https://books.google.com/xl" for url in requested)


@pytest.mark.asyncio
async def test_google_cover_rejects_untrusted_host_and_redirect():
    requested = []

    def handler(request: httpx.Request):
        requested.append(str(request.url))
        if request.url.host == "www.googleapis.com":
            return httpx.Response(200, json={"items": [{"volumeInfo": {
                "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9783608938145"}],
                "imageLinks": {"thumbnail": "https://127.0.0.1/private"},
            }}]}, request=request)
        return httpx.Response(200, content=png(), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await GoogleBooksCoverProvider(client).fetch("9783608938145") is None
    assert len(requested) == 1

    def redirect(request: httpx.Request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"}, request=request)

    requested.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
        assert await OpenLibraryCoverProvider(client).fetch("9783608938145") is None
    assert len(requested) == 1


@pytest.mark.asyncio
async def test_no_provider_result_and_timeout_are_non_fatal_to_import(db_context, tmp_path):
    settings, factory = db_context
    timeout = StaticProvider("openlibrary", error=httpx.ReadTimeout("slow"))
    google = StaticProvider("googlebooks")
    manager = ImportManager(settings, factory, ProviderChain([]), CoverService([timeout, google]))
    path = tmp_path / "book.epub"
    make_epub(path)
    item = ImportItem(path.name)
    await manager._process_one(ImportJob("imp_coverless", "now", [item]), item, path)
    assert item.status == "finished" and timeout.calls == google.calls == 1
    with factory() as session:
        book = get_book(session, item.book_id)
        assert book and book.cover_path is None
        document = json.loads(book.metadata_json)
        assert document["cover"] is None


@pytest.mark.asyncio
async def test_both_cover_providers_without_result_keep_import_successful(db_context, tmp_path):
    settings, factory = db_context
    openlibrary = StaticProvider("openlibrary")
    google = StaticProvider("googlebooks")
    manager = ImportManager(
        settings, factory, ProviderChain([]), CoverService([openlibrary, google]),
    )
    path = tmp_path / "book.epub"
    make_epub(path)
    item = ImportItem(path.name)

    await manager._process_one(ImportJob("imp_no_cover_results", "now", [item]), item, path)

    assert item.status == "finished"
    assert openlibrary.calls == google.calls == 1
    with factory() as session:
        book = get_book(session, item.book_id)
        assert book and not book.has_cover and book.cover_path is None


@pytest.mark.asyncio
async def test_automatic_external_cover_does_not_use_fuzzy_inferred_isbn(db_context, tmp_path):
    settings, factory = db_context
    cover_provider = StaticProvider("openlibrary", asset(png()))
    manager = ImportManager(
        settings,
        factory,
        ProviderChain([InferredIsbnMetadataProvider()]),
        CoverService([cover_provider]),
    )
    path = tmp_path / "book-without-isbn.epub"
    make_epub(path, isbn=None)
    item = ImportItem(path.name)

    await manager._process_one(ImportJob("imp_no_embedded_isbn", "now", [item]), item, path)

    assert item.status == "finished"
    assert cover_provider.calls == 0
    with factory() as session:
        book = get_book(session, item.book_id)
        assert book and book.isbn is None and book.cover_path is None


@pytest.mark.asyncio
async def test_external_cover_is_archived_and_metadata_records_source(db_context, tmp_path):
    settings, factory = db_context
    openlibrary = StaticProvider("openlibrary", asset(png(5, 7)))
    manager = ImportManager(settings, factory, ProviderChain([]), CoverService([openlibrary]))
    path = tmp_path / "book.epub"
    make_epub(path)
    item = ImportItem(path.name)
    await manager._process_one(ImportJob("imp_cover", "now", [item]), item, path)
    with factory() as session:
        book = get_book(session, item.book_id)
        assert book and book.cover_source == "external" and book.cover_provider == "openlibrary"
        document = json.loads(book.metadata_json)
        assert document["cover"]["source"] == "external"
        assert document["cover"]["provider"] == "openlibrary"
        assert (settings.library_dir / book.cover_path).read_bytes() == png(5, 7)


@pytest.mark.asyncio
async def test_refresh_replaces_only_after_success_and_keeps_cover_on_failure(db_context, tmp_path):
    settings, factory = db_context
    first = png(2, 3, b"\x10\x20\x30")
    second = png(4, 5, b"\x30\x20\x10")
    manager = ImportManager(
        settings, factory, ProviderChain([]),
        CoverService([StaticProvider("openlibrary", asset(first))]),
    )
    path = tmp_path / "book.epub"
    make_epub(path)
    item = ImportItem(path.name)
    await manager._process_one(ImportJob("imp_refresh", "now", [item]), item, path)
    manager.cover_service = CoverService([StaticProvider("openlibrary", asset(second))])
    result = await manager.refresh_cover(item.book_id)
    assert result["replaced"] and result["cover"]["width"] == 4
    with factory() as session:
        book = get_book(session, item.book_id)
        cover_path = settings.library_dir / book.cover_path
        assert cover_path.read_bytes() == second
    manager.cover_service = CoverService([
        StaticProvider("openlibrary", error=httpx.ConnectTimeout("offline")),
        StaticProvider("googlebooks"),
    ])
    result = await manager.refresh_cover(item.book_id)
    assert not result["replaced"]
    assert cover_path.read_bytes() == second


def test_cover_api_serves_local_content_type_and_returns_404(db_context):
    settings, factory = db_context
    with factory() as session:
        covered = add_book(session, book_id="bk_covered1")
        uncovered = add_book(session, book_id="bk_nocover1")
        cover_file = settings.library_dir / "covers" / "cover.png"
        cover_file.parent.mkdir(parents=True)
        cover_file.write_bytes(png())
        covered.has_cover = True
        covered.cover_path = cover_file.relative_to(settings.library_dir).as_posix()
        covered.cover_source = "embedded"
        session.commit()

    def override_db():
        with factory() as session:
            yield session

    previous_settings = main.settings
    main.settings = settings
    main.app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(main.app)
        response = client.get("/api/books/bk_covered1/cover")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert response.content == png()
        assert client.get(f"/api/books/{uncovered.id}/cover").status_code == 404
    finally:
        main.settings = previous_settings
        main.app.dependency_overrides.clear()
