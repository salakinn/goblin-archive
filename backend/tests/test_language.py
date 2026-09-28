import json
import struct
from unittest.mock import MagicMock

import pytest
from ebooklib import epub
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend import main
from backend.ai import AIError, AIResult
from backend.database import get_db
from backend.language import (LanguageOutput, SampleLanguage, TextExtractionError,
                              TextSample, _palmdoc, agreed_language, detect_language,
                              extract_language_samples)
from backend.repository import get_book, list_books
from backend.usage import records
from backend.tests.conftest import add_book


def prose(language, chapter):
    sentence = (
        "Der Wanderer ging durch den Wald und betrachtete die alten Bäume. Am Abend erreichte er das Dorf und erzählte von seiner Reise."
        if language == "de" else
        "The traveller walked through the forest and looked at the ancient trees. In the evening he reached the village and told them about his journey."
    )
    return " ".join(f"{sentence} {'Abschnitt' if language == 'de' else 'Section'} {chapter}-{i}." for i in range(45))


def write_epub(path, languages=("en", "en", "en")):
    book = epub.EpubBook()
    book.set_identifier("test-language")
    book.set_title("Deutscher Titel")
    book.set_language("de")  # Intentionally wrong for the English fixture.
    front = epub.EpubHtml(title="Impressum", file_name="copyright.xhtml")
    front.content = f"<p>{prose('de', 0)}</p>"
    book.add_item(front)
    chapters = []
    for index, language in enumerate(languages):
        chapter = epub.EpubHtml(title=f"Chapter {index}", file_name=f"chapter{index}.xhtml")
        chapter.content = f"<p>{prose(language, index + 1)}</p>"
        book.add_item(chapter)
        chapters.append(chapter)
    book.add_item(epub.EpubNav())
    book.spine = ["nav", front, *chapters]
    book.toc = chapters
    epub.write_epub(str(path), book)


def assessment(codes=("en", "en", "en"), statuses=("clear", "clear", "clear")):
    return LanguageOutput(samples=[SampleLanguage(sample_id=i + 1, language=code,
        status=statuses[i], reason="Sprachmerkmale der Textprobe") for i, code in enumerate(codes)])


def test_epub_samples_follow_main_text_and_exclude_metadata(tmp_path):
    path = tmp_path / "book.epub"
    write_epub(path)
    samples = extract_language_samples(path, "epub")
    assert len(samples) == 3
    assert len({s.text for s in samples}) == 3
    assert all("traveller" in s.text and "Wanderer" not in s.text for s in samples)
    assert all("chapter" in s.location and len(s.text) <= 1600 for s in samples)
    assert "chapter0" in samples[0].location and "chapter2" in samples[-1].location


def test_language_detection_obeys_global_cost_limit(language_api):
    client, provider, _path, factory = language_api
    main.settings.ai_language_input_usd_per_million = 100
    main.settings.ai_language_output_usd_per_million = 100
    main.settings.ai_daily_limit_usd = 0.001
    assert client.post('/api/books/bk_test0001/ai/language').status_code == 409
    provider.generate.assert_not_called()
    main.settings.ai_daily_limit_usd = 1
    assert client.post('/api/books/bk_test0001/ai/language').status_code == 200
    assert records(factory)[0]['cost_usd'] == 0.013


def test_short_epub_has_insufficient_samples(tmp_path):
    path = tmp_path / "short.epub"
    from backend.tests.test_imports import make_epub
    make_epub(path)
    assert extract_language_samples(path, "epub") == []


def test_pdf_text_and_scan_without_text(tmp_path):
    path = tmp_path / "book.pdf"
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    font_ref = writer._add_object(font)
    for index in range(8):
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 10 Tf 10 700 Td ({prose('en', index)}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    samples = extract_language_samples(path, "pdf")
    assert len(samples) == 3
    assert all("traveller" in s.text for s in samples)
    assert all(not s.location.startswith(("Seite 1 ", "Seite 2 ")) for s in samples)
    scan = PdfWriter()
    scan.add_blank_page(width=600, height=800)
    scan.write(path)
    assert extract_language_samples(path, "pdf") == []


def write_mobi(path, encryption=0):
    text = f"<html><body><p>{prose('en', 1)} {prose('en', 2)}</p></body></html>".encode()
    first = 94
    record = bytearray(248)
    struct.pack_into(">HHIHHH", record, 0, 1, 0, len(text), 1, 4096, encryption)
    record[16:20] = b"MOBI"
    struct.pack_into(">I", record, 20, 232)
    struct.pack_into(">I", record, 28, 65001)
    header = bytearray(first)
    header[60:68] = b"BOOKMOBI"
    struct.pack_into(">H", header, 76, 2)
    struct.pack_into(">I", header, 78, first)
    struct.pack_into(">I", header, 86, first + len(record))
    path.write_bytes(header + record + text)


def test_uncompressed_kindle_and_drm(tmp_path):
    path = tmp_path / "book.mobi"
    write_mobi(path)
    assert len(extract_language_samples(path, "mobi")) == 3
    write_mobi(path, encryption=1)
    with pytest.raises(TextExtractionError, match="Verschlüsselte"):
        extract_language_samples(path, "mobi")


def test_palmdoc_decodes_literals_and_overlapping_backreferences():
    assert _palmdoc(b"hello\xc1") == b"hello A"
    assert _palmdoc(b"abc\x80\x1b") == b"abcabcabc"
    with pytest.raises(ValueError):
        _palmdoc(b"\x80\x01")


@pytest.mark.parametrize("codes,statuses,expected", [
    (("de", "de", "de"), ("clear",) * 3, "de"),
    (("en", "en", "en"), ("clear",) * 3, "en"),
    (("de", "en", "de"), ("clear",) * 3, None),
    (("de", "xx", "de"), ("clear", "unclear", "clear"), None),
    (("xx",) * 3, ("multilingual",) * 3, None),
])
def test_agreement_rules(codes, statuses, expected):
    assert agreed_language(assessment(codes, statuses)) == expected


@pytest.fixture
def language_api(db_context, monkeypatch):
    settings, factory = db_context
    monkeypatch.setattr(main, "settings", settings)
    with factory() as session:
        book = add_book(session)
        file = settings.library_dir / book.library_path
        file.parent.mkdir(parents=True)
        write_epub(file)
        metadata_path = file.parent / "metadata.json"
        metadata_path.write_text(book.metadata_json)
    provider = MagicMock()
    provider.generate.return_value = AIResult(assessment(), 100, 30)
    monkeypatch.setattr(main, "make_ai_provider", lambda _: provider)

    def override():
        with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = override
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        yield client, provider, metadata_path, factory
    finally:
        client.close()
        main.app.dependency_overrides.pop(get_db, None)


URL = "/api/books/bk_test0001/ai/language"


def test_direct_language_detection_updates_metadata_and_filters(language_api):
    client, provider, path, factory = language_api
    response = client.post(URL)
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is True
    assert response.json()["book"]["language"] == "en"
    document = json.loads(path.read_text())
    assert document["metadata"]["language"]["source"] == "ai"
    assert document["language_detection"]["previous_language"] == "de"
    assert len(document["language_detection"]["samples"]) == 3
    assert all("text" not in s for s in document["language_detection"]["samples"])
    with factory() as session:
        assert json.loads(get_book(session, "bk_test0001").metadata_json) == document
        assert len(list_books(session, language="en")) == 1
        assert not list_books(session, language="de")
    context = provider.generate.call_args.kwargs["context"]
    assert set(context) == {"samples"}
    assert all(set(s) == {"sample_id", "text"} for s in context["samples"])
    assert client.post(URL).json()["cached"] is True
    assert provider.generate.call_count == 1


def test_unclear_result_is_saved_without_replacing_language(language_api):
    client, provider, path, _ = language_api
    provider.generate.return_value = AIResult(assessment(("en", "de", "en")), 100, 30)
    response = client.post(URL)
    assert response.json()["status"] == "unclear"
    assert response.json()["book"]["language"] == "de"
    assert "language" not in json.loads(path.read_text())["metadata"]
    assert client.post(URL).json()["cached"] is True


def test_confirmed_language_is_protected(language_api):
    client, provider, _, factory = language_api
    with factory() as session:
        book = get_book(session, "bk_test0001")
        book.metadata_json = json.dumps({"metadata": {"language": {"value": "de", "source": "manual"}}})
        session.commit()
    assert client.post(URL).json()["status"] == "protected"
    provider.generate.assert_not_called()


def test_no_text_no_paid_request(language_api):
    client, provider, path, _ = language_api
    from backend.tests.test_imports import make_epub
    make_epub(path.parent / "Der Hobbit.epub")
    before = path.read_bytes()
    assert client.post(URL).json()["status"] == "insufficient_text"
    assert path.read_bytes() == before
    provider.generate.assert_not_called()


def test_failure_is_not_cached(language_api):
    client, provider, path, _ = language_api
    before = path.read_bytes()
    provider.generate.side_effect = AIError("Offline")
    assert client.post(URL).status_code == 503
    assert path.read_bytes() == before
    provider.generate.side_effect = None
    assert client.post(URL).json()["applied"] is True


def test_invalid_sample_ids_and_language_are_rejected(language_api):
    client, provider, path, _ = language_api
    before = path.read_bytes()
    for invalid in (assessment(("xx", "xx", "xx")), assessment()):
        if invalid.samples[0].language == "en":
            invalid.samples[0].sample_id = 2
        provider.generate.return_value = AIResult(invalid, 0, 0)
        assert client.post(URL).status_code == 503
        assert path.read_bytes() == before


def test_commit_failure_restores_language_and_file(language_api, monkeypatch):
    client, _, path, factory = language_api
    before = path.read_bytes()

    def fail(self):
        raise RuntimeError("disk full")

    monkeypatch.setattr(factory.class_, "commit", fail)
    assert client.post(URL).status_code == 500
    assert path.read_bytes() == before
    with factory() as session:
        assert get_book(session, "bk_test0001").language == "de"


def test_concurrent_language_edit_is_preserved(language_api):
    client, provider, path, factory = language_api
    before = path.read_bytes()

    def change(**kwargs):
        with factory() as session:
            get_book(session, "bk_test0001").language = "fr"
            session.commit()
        return AIResult(assessment(), 0, 0)

    provider.generate.side_effect = change
    assert client.post(URL).status_code == 409
    assert path.read_bytes() == before
    with factory() as session:
        assert get_book(session, "bk_test0001").language == "fr"


def test_fingerprint_changes_with_model(language_api, monkeypatch):
    client, provider, _, _ = language_api
    assert client.post(URL).status_code == 200
    monkeypatch.setattr(main.settings, "ai_language_model", "another-model")
    assert client.post(URL).json()["cached"] is False
    assert provider.generate.call_count == 2
