import pytest
from ebooklib import epub
import asyncio
import json

from backend.metadata import FieldValue
from backend.title_cleanup import clean_field, clean_title
from backend.imports import ImportItem, ImportJob, ImportManager
from backend.previews import PreviewManager, PreviewConflict
from backend.providers import ProviderChain
from backend.models import Book


@pytest.mark.parametrize(("raw", "expected"), [
    ("  Der   Zauberberg  ", "Der Zauberberg"),
    ("Der\nHerr\tder Ringe", "Der Herr der Ringe"),
    ("Die\u00a0Verwandlung", "Die Verwandlung"),
    ("Cafe\u0301", "Café"),
    ("\ufeff\ufeffFaust", "Faust"),
    ("DIE UNENDLICHE GESCHICHTE", "DIE UNENDLICHE GESCHICHTE"),
    ("iRobot", "iRobot"),
    ("Der Herr der Rigne", "Der Herr der Rigne"),
    ("Warum?", "Warum?"),
    ("Roman – Band 02 (ungekürzt)", "Roman – Band 02 (ungekürzt)"),
    ("¿Qué?", "¿Qué?"),
    ("नमस्ते\u200d😊", "नमस्ते\u200d😊"),
    ("A\u200cB\u00adC\ufeffD", "A\u200cB\u00adC\ufeffD"),
])
def test_conservative_title(raw, expected):
    result, _ = clean_title(raw)
    assert result == expected
    assert clean_title(result) == (result, [])


@pytest.mark.parametrize("raw", [" \t\u00a0", "X\x01Y", "X" * 501])
def test_invalid_title(raw):
    with pytest.raises(ValueError):
        clean_title(raw)


def test_history_is_idempotent_and_manual_is_protected():
    history = []
    field = FieldValue("  Titel  ", "embedded")
    clean_field(field, history)
    clean_field(field, history)
    assert field == FieldValue("Titel", "embedded")
    assert len(history) == 1
    assert history[0]["rules"] == ["whitespace"]
    manual = FieldValue("  Bewusst  ", "manual")
    clean_field(manual, history)
    assert manual.value == "  Bewusst  "
    assert len(history) == 1


def _epub(path, title):
    book = epub.EpubBook()
    book.set_identifier(path.stem)
    book.set_title(title)
    book.set_language("de")
    chapter = epub.EpubHtml(title="Kapitel", file_name="chapter.xhtml", lang="de")
    chapter.content = "<p>Ein Testbuch.</p>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


@pytest.mark.asyncio
async def test_direct_and_preview_archive_same_clean_title(db_context):
    settings, factory = db_context
    manager = ImportManager(settings, factory, ProviderChain([]))
    raw = "  Cafe\u0301   – Band 02?  "
    direct_path = settings.staging_dir / "direct.epub"
    _epub(direct_path, raw)
    item = ImportItem("direct.epub")
    await manager._process_one(ImportJob("imp_direct", "now", [item]), item, direct_path)
    assert item.status == "finished"
    preview_path = settings.staging_dir / "preview.epub"
    _epub(preview_path, raw)
    previews = PreviewManager(manager)
    preview_id = previews.create([("preview.epub", preview_path)])[0]["id"]
    await asyncio.gather(*previews.tasks)
    preview = previews.get(preview_id)
    assert preview["metadata"]["title"] == {"value": "Café – Band 02?", "source": "embedded"}
    assert len(preview["metadata"]["_title_cleanup_history"]) == 1
    with factory() as db:
        direct = db.get(Book, item.book_id)
        assert direct.title == preview["metadata"]["title"]["value"]
        document = json.loads(direct.metadata_json)
        assert document["metadata"]["title"]["value"] == direct.title
        assert len(document["title_cleanup_history"]) == 1
    with pytest.raises(PreviewConflict):
        previews.accept_title(preview_id, preview["revision"] - 1, raw, "Anders", "test", "test")
    accepted = previews.accept_title(preview_id, preview["revision"], "Café – Band 02?",
                                     "¿Café – Band 02?", "test", "test-model")
    assert accepted["metadata"]["title"] == {
        "value": "¿Café – Band 02?", "source": "manual", "confirmed": True}
    assert accepted["metadata"]["_title_cleanup_history"][-1]["source"] == "ai"
    with pytest.raises(PreviewConflict):
        previews.accept_title(preview_id, preview["revision"], "Café – Band 02?",
                              "Veraltet", "test", "test-model")
