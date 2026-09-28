from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from ebooklib import epub

from backend.imports import ImportManager
from backend.covers import CoverAsset
from backend.models import Book, ImportPreview
from backend.previews import PreviewConflict, PreviewManager, _now
from backend.providers import ProviderChain


def make_epub(path):
    book = epub.EpubBook()
    book.set_identifier("9783608938145")
    book.set_title("Der Hobbit")
    book.set_language("de")
    book.add_author("J.R.R. Tolkien")
    chapter = epub.EpubHtml(title="Kapitel", file_name="chapter.xhtml", lang="de")
    chapter.content = "<h1>Eine unerwartete Gesellschaft</h1>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


async def ready(manager, path):
    item = manager.create([("buch.epub", path)])[0]
    await asyncio.gather(*manager.tasks)
    return manager.get(item["id"])


@pytest.mark.asyncio
async def test_preview_edit_confirm_and_duplicate(db_context):
    settings, factory = db_context
    manager = PreviewManager(ImportManager(settings, factory, ProviderChain([])))
    path = settings.staging_dir / "first.epub"
    make_epub(path)
    preview = await ready(manager, path)
    assert preview["status"] == "ready"
    assert preview["metadata"]["title"]["source"] == "embedded"
    with factory() as db:
        assert db.query(Book).count() == 0
    edited = manager.edit(preview["id"], {"title": "Mein Hobbit"}, preview["revision"], None)
    assert edited["metadata"]["title"] == {"value": "Mein Hobbit", "source": "manual"}
    assert edited["metadata"]["language"]["source"] == "embedded"
    with pytest.raises(PreviewConflict):
        manager.edit(preview["id"], {"title": "Veraltet"}, preview["revision"], None)
    archived = await manager.confirm(preview["id"])
    assert archived["status"] == "archived"
    assert not path.exists()
    with factory() as db:
        book = db.get(Book, archived["book_id"])
        assert book.title == "Mein Hobbit"
        document = json.loads((settings.library_dir / book.library_path).parent.joinpath("metadata.json").read_text())
        assert document["metadata"]["title"]["source"] == "manual"
    second = settings.staging_dir / "second.epub"
    second.write_bytes((settings.library_dir / book.library_path).read_bytes())
    duplicate = await ready(manager, second)
    assert duplicate["duplicate_book_id"] == book.id
    with pytest.raises(PreviewConflict):
        await manager.confirm(duplicate["id"])
    manager.discard(duplicate["id"])
    assert not second.exists()


@pytest.mark.asyncio
async def test_preview_survives_restart_and_expires(db_context):
    settings, factory = db_context
    imports = ImportManager(settings, factory, ProviderChain([]))
    manager = PreviewManager(imports)
    path = settings.staging_dir / "restart.epub"
    make_epub(path)
    preview = await ready(manager, path)
    restarted = PreviewManager(imports)
    restarted.resume()
    assert restarted.get(preview["id"])["status"] == "ready"
    with factory() as db:
        row = db.get(ImportPreview, preview["id"])
        row.expires_at = _now() - timedelta(seconds=1)
        db.commit()
    restarted.cleanup()
    with pytest.raises(KeyError):
        restarted.get(preview["id"])
    assert not path.exists()


@pytest.mark.asyncio
async def test_duplicate_in_same_preview_batch_and_skip(db_context):
    settings, factory = db_context
    manager = PreviewManager(ImportManager(settings, factory, ProviderChain([])))
    first = settings.staging_dir / "one.epub"
    second = settings.staging_dir / "two.epub"
    make_epub(first)
    second.write_bytes(first.read_bytes())
    manager.create([("one.epub", first), ("two.epub", second)])
    await asyncio.gather(*manager.tasks)
    items = manager.list()
    assert sum(bool(item["duplicate_preview_id"]) for item in items) == 1
    original = next(item for item in items if not item["duplicate_preview_id"])
    manager.skip(original["id"])
    assert manager.get(original["id"])["status"] == "skipped"
    manager.resume_one(original["id"])
    assert manager.get(original["id"])["status"] == "ready"


def test_preview_api_upload_edit_confirm(db_context, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main
    settings, factory = db_context
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "make_provider_chain", lambda *_: ProviderChain([]))
    source = tmp_path / "api.epub"
    make_epub(source)
    with TestClient(main.app) as client:
        response = client.post("/api/import/previews", files={"files": ("api.epub", source.read_bytes(), "application/epub+zip")})
        assert response.status_code == 202, response.text
        preview_id = response.json()["items"][0]["id"]
        for _ in range(100):
            preview = client.get(f"/api/import/previews/{preview_id}").json()
            if preview["status"] != "queued" and preview["status"] != "analyzing":
                break
            import time
            time.sleep(0.02)
        assert preview["status"] == "ready", preview
        edited = client.put(f"/api/import/previews/{preview_id}", json={
            "revision": preview["revision"], "changes": {"title": "API Titel"}})
        assert edited.status_code == 200, edited.text
        archived = client.post(f"/api/import/previews/{preview_id}/confirm")
        assert archived.status_code == 200, archived.text
        assert archived.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_cover_search_requires_explicit_selection(db_context):
    settings, factory = db_context
    imports = ImportManager(settings, factory, ProviderChain([]))

    class FakeCoverService:
        async def resolve(self, _embedded, _isbn):
            return CoverAsset(b"test-image", ".png", "image/png", 2, 3, "external", "test")

    imports.cover_service = FakeCoverService()
    manager = PreviewManager(imports)
    path = settings.staging_dir / "cover.epub"
    make_epub(path)
    preview = await ready(manager, path)
    assert not preview["cover_selected"]
    found = await manager.search_external(preview["id"])
    assert found["external_cover"]["provider"] == "test"
    assert not found["cover_selected"]
    selected = manager.edit(preview["id"], {}, found["revision"], "external")
    assert selected["cover_selected"]
    assert selected["cover"]["source"] == "external"
    removed = manager.edit(preview["id"], {}, selected["revision"], "none")
    assert not removed["cover_selected"]
