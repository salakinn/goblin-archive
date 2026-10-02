"""Regressions for the import performance work.

Covers the vectorised MinHash signature, PDFium text extraction including
encrypted files, and the concurrency-safe get-or-create helpers.
"""
from __future__ import annotations

import random
import threading

import pytest
from sqlalchemy import func, select

from backend.duplicates import SIGNATURE_CHUNK, normalized_text, shingles, signature_for
from backend.models import Author, Genre, Tag
from backend.repository import get_or_create_author, get_or_create_genre, get_or_create_tag

MASK = (1 << 64) - 1


def reference_signature(shingle_values: set[int]) -> tuple[int, ...]:
    """The pre-optimisation implementation, kept as the comparison oracle."""
    if not shingle_values:
        return ()
    return tuple(min(((value * (2 * seed + 1) + seed * 0x9E3779B97F4A7C15) & MASK)
                     for value in shingle_values) for seed in range(32))


@pytest.mark.parametrize("size", [0, 1, 2, 17, 1000, SIGNATURE_CHUNK,
                                  SIGNATURE_CHUNK + 1, 2 * SIGNATURE_CHUNK + 7])
def test_signature_matches_reference_across_chunk_boundaries(size):
    generator = random.Random(11)
    values = {generator.getrandbits(64) for _ in range(size)}
    assert signature_for(values) == reference_signature(values)


@pytest.mark.parametrize("values", [{0}, {MASK}, {0, MASK}, {1, 2, 3}, {MASK - 1, MASK}])
def test_signature_handles_unsigned_64_bit_extremes(values):
    """Overflow must wrap exactly like the former explicit bit mask."""
    assert signature_for(values) == reference_signature(values)


def test_signature_shape_is_stable():
    values = {random.Random(3).getrandbits(64) for _ in range(500)}
    signature = signature_for(values)
    assert len(signature) == 32
    assert all(0 <= entry <= MASK for entry in signature)
    assert signature_for(set()) == ()


def _pdf_bytes(words: int = 1500, *, encrypt: bool = False) -> bytes:
    import io

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    font_ref = writer._add_object(font)
    page = writer.add_blank_page(width=600, height=800)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
    stream = DecodedStreamObject()
    text = " ".join(f"wort{index:04d}" for index in range(words))
    stream.set_data(f"BT /F1 10 Tf 10 700 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypt:
        # Empty user password: readers may open it without asking for input.
        writer.encrypt(user_password="", owner_password="owner")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_text_extraction_returns_content(tmp_path):
    path = tmp_path / "plain.pdf"
    path.write_bytes(_pdf_bytes())
    text = normalized_text(path, "pdf")
    assert "wort0000" in text and "wort1499" in text
    assert len(shingles(text)) > 100


def test_encrypted_pdf_with_empty_user_password_is_analysed(tmp_path):
    """Previously rejected via `reader.is_encrypted`; PDFium reads these."""
    path = tmp_path / "encrypted.pdf"
    path.write_bytes(_pdf_bytes(encrypt=True))
    text = normalized_text(path, "pdf")
    assert "wort0000" in text


def test_damaged_pdf_raises_value_error(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7\nnicht wirklich ein PDF\n")
    with pytest.raises(ValueError):
        normalized_text(path, "pdf")


def test_oversized_pdf_text_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.duplicates.MAX_TEXT_CHARS", 50)
    path = tmp_path / "long.pdf"
    path.write_bytes(_pdf_bytes())
    with pytest.raises(ValueError, match="Analyselimit"):
        normalized_text(path, "pdf")


def test_concurrent_get_or_create_keeps_one_row_without_errors(db_context):
    """The import must not fail with `UNIQUE constraint failed`."""
    _settings, factory = db_context
    failures: list[str] = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait(timeout=10)
            with factory() as session:
                get_or_create_author(session, "Kirkman, Robert")
                get_or_create_genre(session, "Horror")
                get_or_create_tag(session, "Graphic Novel")
                session.commit()
        except Exception as error:  # noqa: BLE001 - recorded and asserted below
            failures.append(f"{type(error).__name__}: {error}")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Author)) == 1
        assert session.scalar(select(func.count()).select_from(Genre)) == 1
        assert session.scalar(select(func.count()).select_from(Tag)) == 1


def test_get_or_create_returns_persisted_rows(db_context):
    _settings, factory = db_context
    with factory() as session:
        author = get_or_create_author(session, "Autorin A")
        session.commit()
        assert author.id is not None
    with factory() as session:
        again = get_or_create_author(session, "Autorin A")
        assert again.id == author.id
        assert session.scalar(select(func.count()).select_from(Author)) == 1
