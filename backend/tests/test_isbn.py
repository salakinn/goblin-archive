from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from backend.isbn import (
    IsbnResolver,
    RawIsbnCandidate,
    canonical_isbn13,
    isbn10_to_isbn13,
    isbn13_to_isbn10,
    valid_isbn10,
    valid_isbn13,
)
from backend.repository import get_book
from backend.tests.conftest import add_book


class StaticIsbnProvider:
    def __init__(self, name: str, candidates: list[RawIsbnCandidate]):
        self.name = name
        self.candidates = candidates
        self.calls = 0

    async def search(self, _title: str, _author: str | None):
        self.calls += 1
        return self.candidates


def candidate(isbn: str, source: str) -> RawIsbnCandidate:
    isbn13 = canonical_isbn13(isbn)
    assert isbn13
    return RawIsbnCandidate(
        isbn13=isbn13,
        isbn10=isbn13_to_isbn10(isbn13),
        title="Der Hobbit",
        authors=["J.R.R. Tolkien"],
        publisher="Klett-Cotta",
        publication_year=1937,
        language="de",
        format="Book",
        source=source,
        source_id=f"/{source}/hobbit",
    )


def test_isbn_validation_and_conversion():
    assert valid_isbn10("3-404-10531-1")
    assert valid_isbn13("978-3-404-10531-1")
    assert isbn10_to_isbn13("3404105311") == "9783404105311"
    assert isbn13_to_isbn10("9783404105311") == "3404105311"
    assert canonical_isbn13("not-an-isbn") is None


@pytest.mark.asyncio
async def test_resolver_merges_sources_persists_candidates_and_uses_cache(db_context):
    settings, factory = db_context
    with factory() as session:
        book = add_book(session)
        book_id = book.id
    lobid = StaticIsbnProvider("lobid", [candidate("9783608938142", "lobid")])
    openlibrary = StaticIsbnProvider(
        "openlibrary", [candidate("9783608938142", "openlibrary")],
    )
    resolver = IsbnResolver(settings, factory, [lobid, openlibrary])

    first = await resolver.search(book_id)
    second = await resolver.search(book_id)

    assert first["auto_candidate"]["isbn13"] == "9783608938142"
    assert first["auto_candidate"]["sources"] == ["lobid", "openlibrary"]
    assert second["cache_hit"]
    assert lobid.calls == openlibrary.calls == 1
    assert resolver.list_candidates(book_id)[0]["score"] == 100


@pytest.mark.asyncio
async def test_ambiguous_editions_are_not_automatically_applied(db_context):
    settings, factory = db_context
    with factory() as session:
        book_id = add_book(session).id
    provider = StaticIsbnProvider("lobid", [
        candidate("9783608938142", "lobid"),
        candidate("9783404105311", "lobid"),
    ])
    resolver = IsbnResolver(settings, factory, [provider])

    result = await resolver.search(book_id)

    assert result["auto_candidate"] is None
    assert len(result["candidates"]) == 2


@pytest.mark.asyncio
async def test_work_match_is_stored_as_reference_without_claiming_exact_edition(db_context):
    settings, factory = db_context
    with factory() as session:
        book = add_book(session, title="Der schwarze Stein", year=None)
        book.publisher = None
        book.isbn = None
        book.authors[0].name = "Robert E. Howard"
        session.commit()
        book_id = book.id
        book_dir = (settings.library_dir / book.library_path).parent
        metadata_text = book.metadata_json
    book_dir.mkdir(parents=True)
    (book_dir / "metadata.json").write_text(metadata_text + "\n", encoding="utf-8")
    reference = RawIsbnCandidate(
        isbn13="9783959450607",
        isbn10="3959450605",
        title="Der schwarze Stein",
        authors=["Howard, Robert E."],
        publisher="JMB Verlag",
        publication_year=2024,
        language="de",
        format="Paperback",
        source="openlibrary",
        source_id="/works/OL24526598W",
        subjects=["Horror", "Short stories"],
    )
    resolver = IsbnResolver(
        settings, factory, [StaticIsbnProvider("openlibrary", [reference])],
    )

    result = await resolver.search(book_id)
    assert result["auto_candidate"] is None
    assert result["auto_reference"]["isbn13"] == "9783959450607"
    resolver.apply_reference(book_id, "9783959450607")

    with factory() as session:
        book = get_book(session, book_id)
        assert book and book.isbn is None
        assert book.reference_isbn == "9783959450607"
        work_match = json.loads(book.work_match_json)
        assert work_match["confidence"] == 100
        assert work_match["suggested_genres"] == ["Horror", "Short stories"]
    document = json.loads((book_dir / "metadata.json").read_text(encoding="utf-8"))
    assert document["work_match"]["reference_isbns"] == ["9783959450607"]


@pytest.mark.asyncio
async def test_complementary_provider_evidence_can_identify_same_work(db_context):
    settings, factory = db_context
    with factory() as session:
        book = add_book(session, title="Der schwarze Stein", year=None)
        book.publisher = None
        book.authors[0].name = "Robert E. Howard"
        session.commit()
        book_id = book.id
    lobid = RawIsbnCandidate(
        isbn13="9783785753811", isbn10="3785753810",
        title="Der schwarze Stein", authors=["Gruppe, Marc"],
        publisher="Titania Medien", publication_year=2016, language="de",
        format="Audio", source="lobid", source_id="lobid:audio",
    )
    openlibrary = RawIsbnCandidate(
        isbn13="9783785753811", isbn10="3785753810",
        title="Black Stone", authors=["Robert E. Howard"],
        publisher=None, publication_year=None, language="de",
        format=None, source="openlibrary", source_id="/works/OL24526598W",
    )
    resolver = IsbnResolver(settings, factory, [
        StaticIsbnProvider("lobid", [lobid]),
        StaticIsbnProvider("openlibrary", [openlibrary]),
    ])

    result = await resolver.search(book_id)

    assert result["auto_candidate"] is None
    assert result["auto_reference"]["work_score"] == 100
    assert result["auto_reference"]["authors"] == ["Gruppe, Marc", "Robert E. Howard"]


@pytest.mark.asyncio
async def test_apply_candidate_updates_database_fts_and_metadata_file(db_context):
    settings, factory = db_context
    with factory() as session:
        book = add_book(session)
        book.isbn = None
        session.commit()
        book_id = book.id
        metadata_text = book.metadata_json
        book_dir = (settings.library_dir / book.library_path).parent
    book_dir.mkdir(parents=True)
    (book_dir / "metadata.json").write_text(metadata_text + "\n", encoding="utf-8")
    resolver = IsbnResolver(
        settings,
        factory,
        [StaticIsbnProvider("lobid", [candidate("9783608938142", "lobid")])],
    )
    await resolver.search(book_id)

    resolver.apply(book_id, "3-608-93814-1")

    with factory() as session:
        book = get_book(session, book_id)
        assert book and book.isbn == "9783608938142"
        stored_document = json.loads(book.metadata_json)
        assert stored_document["metadata"]["isbn"] == {
            "value": "9783608938142",
            "source": "isbn_resolver:lobid",
        }
        fts_isbn = session.execute(
            text("SELECT isbn FROM books_fts WHERE book_id = :book_id"), {"book_id": book_id},
        ).scalar_one()
        assert fts_isbn == "9783608938142"
    document = json.loads((book_dir / "metadata.json").read_text(encoding="utf-8"))
    assert document["metadata"]["isbn"] == {
        "value": "9783608938142",
        "source": "isbn_resolver:lobid",
    }


@pytest.mark.asyncio
async def test_negative_results_are_cached(db_context):
    settings, factory = db_context
    with factory() as session:
        book_id = add_book(session).id
    provider = StaticIsbnProvider("lobid", [])
    resolver = IsbnResolver(settings, factory, [provider])

    assert (await resolver.search(book_id))["candidates"] == []
    assert (await resolver.search(book_id))["cache_hit"]
    assert provider.calls == 1
