import asyncio
import base64
import json

import pytest

from backend.extractors import InvalidBookError, detect_and_extract
from backend.imports import ImportManager
from backend.language import TextExtractionError, extract_language_samples
from backend.models import Book
from backend.previews import PreviewManager
from backend.providers import ProviderChain
from backend.tests.test_covers import png


def make_fb2(path):
    chapters = "".join(
        f"<section><title><p>Kapitel {index}</p></title><p>"
        + " ".join(f"Der Wanderer erreichte im Frühling das Dorf und erzählte seine Geschichte. {index}-{part}"
                   for part in range(60))
        + "</p></section>"
        for index in range(1, 4)
    )
    path.write_text(f'''<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"
             xmlns:l="http://www.w3.org/1999/xlink">
  <description>
    <title-info>
      <genre>fiction</genre><author><first-name>Anna</first-name><last-name>Muster</last-name></author>
      <book-title>Ein FB2-Buch</book-title><annotation><p>Eine kurze Beschreibung.</p></annotation>
      <coverpage><image l:href="#cover"/></coverpage><lang>de</lang>
      <date value="2020-01-01">2020</date><sequence name="Beispielreihe" number="2"/>
    </title-info>
    <publish-info><publisher>Beispielverlag</publisher><year>2021</year>
      <isbn>978-3-608-93814-5</isbn></publish-info>
  </description>
  <body>{chapters}</body>
  <body name="notes"><section><p>Diese Fußnote ist keine Buchprobe.</p></section></body>
  <binary id="cover" content-type="image/png">{base64.b64encode(png()).decode()}</binary>
</FictionBook>''', encoding="utf-8")


def test_fb2_metadata_cover_and_main_text(tmp_path):
    path = tmp_path / "buch.fb2"
    make_fb2(path)
    file_format, extracted = detect_and_extract(path, path.name)
    metadata = extracted.metadata
    assert file_format == "fb2"
    assert metadata.title.value == "Ein FB2-Buch"
    assert metadata.authors.value == ["Anna Muster"]
    assert metadata.language.value == "de"
    assert metadata.publication_year.value == 2021
    assert metadata.publisher.value == "Beispielverlag"
    assert metadata.isbn.value == "9783608938145"
    assert metadata.genres.value == ["fiction"]
    assert metadata.series.value == "Beispielreihe"
    assert metadata.description.value == "Eine kurze Beschreibung."
    assert extracted.cover == png()
    samples = extract_language_samples(path, "fb2")
    assert len(samples) == 3
    assert len({sample.text for sample in samples}) == 3
    assert all("Wanderer" in sample.text and "Fußnote" not in sample.text for sample in samples)


@pytest.mark.parametrize("content", [
    '<!DOCTYPE FictionBook [<!ENTITY title "falsch">]><FictionBook><description><title-info><book-title>&title;</book-title></title-info></description></FictionBook>',
    '<NotFictionBook><body/></NotFictionBook>',
    '<FictionBook><body>',
])
def test_fb2_rejects_unsafe_or_invalid_xml(tmp_path, content):
    path = tmp_path / "bad.fb2"
    path.write_text(content)
    with pytest.raises(InvalidBookError):
        detect_and_extract(path, path.name)
    with pytest.raises(TextExtractionError):
        extract_language_samples(path, "fb2")


@pytest.mark.asyncio
async def test_fb2_preview_and_archive(db_context):
    settings, factory = db_context
    path = settings.staging_dir / "example.fb2"
    make_fb2(path)
    manager = PreviewManager(ImportManager(settings, factory, ProviderChain([])))
    preview_id = manager.create([(path.name, path)])[0]["id"]
    await asyncio.gather(*manager.tasks)
    preview = manager.get(preview_id)
    assert preview["status"] == "ready"
    assert preview["format"] == "fb2"
    assert preview["metadata"]["title"] == {"value": "Ein FB2-Buch", "source": "embedded"}
    assert preview["embedded_cover"]["mime_type"] == "image/png"
    archived = await manager.confirm(preview_id)
    with factory() as db:
        book = db.get(Book, archived["book_id"])
        document = json.loads((settings.library_dir / book.library_path).parent.joinpath("metadata.json").read_text())
        assert book.format == "fb2"
        assert book.title == "Ein FB2-Buch"
        assert book.has_cover
        assert [author.name for author in book.authors] == ["Anna Muster"]
        assert document["metadata"]["language"]["value"] == "de"
