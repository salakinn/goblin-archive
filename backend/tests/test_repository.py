from backend.repository import (
    add_book_tag,
    count_books,
    find_by_hash,
    get_book,
    list_authors,
    list_books,
    list_filter_options,
    remove_book_tag,
    search_books,
)
from backend.tests.conftest import add_book


def test_sqlite_crud_and_hash_duplicate(db_context):
    _settings, factory = db_context
    with factory() as session:
        created = add_book(session, sha="a" * 64)
        assert get_book(session, created.id).title == "Der Hobbit"
        assert find_by_hash(session, "a" * 64).id == created.id


def test_year_and_tag_filters(db_context):
    _settings, factory = db_context
    with factory() as session:
        add_book(session, book_id="bk_old0001", year=1937, genres=["Fantasy"])
        add_book(session, book_id="bk_new0001", title="Sachbuch", year=2022, genres=["Wissen"])
        assert [book.id for book in list_books(session, year_from=2000)] == ["bk_new0001"]
        assert [book.id for book in list_books(session, tag="Fantasy")] == ["bk_old0001"]


def test_book_pagination_and_count_keep_stable_order(db_context):
    _settings, factory = db_context
    with factory() as session:
        add_book(session, book_id="bk_first001", title="Erstes")
        add_book(session, book_id="bk_second01", title="Zweites")
        add_book(session, book_id="bk_third001", title="Drittes")
        page = list_books(session, limit=2, offset=1)
        assert len(page) == 2
        assert count_books(session) == 3
        assert [book.id for book in page] == ["bk_second01", "bk_first001"]


def test_full_text_search(db_context):
    _settings, factory = db_context
    with factory() as session:
        add_book(session)
        assert search_books(session, "Tolkien Fantasy")[0].title == "Der Hobbit"
        assert search_books(session, "Mittelerde")[0].title == "Der Hobbit"


def test_list_authors_with_book_counts(db_context):
    _settings, factory = db_context
    with factory() as session:
        add_book(session, book_id="bk_first001")
        add_book(session, book_id="bk_second01", title="Die zwei Türme")

        assert list_authors(session) == [
            {"id": 1, "name": "J.R.R. Tolkien", "book_count": 2},
        ]


def test_list_filter_options_with_book_counts(db_context):
    _settings, factory = db_context
    with factory() as session:
        add_book(session, book_id="bk_first001", genres=["Fantasy", "Abenteuer"])
        add_book(session, book_id="bk_second01", title="Die zwei Türme", genres=["Fantasy"])

        options = list_filter_options(session)

        assert options["authors"] == [
            {"value": "J.R.R. Tolkien", "label": "J.R.R. Tolkien", "book_count": 2},
        ]
        assert options["tags"] == [
            {"value": "Abenteuer", "label": "Abenteuer", "book_count": 1},
            {"value": "Fantasy", "label": "Fantasy", "book_count": 2},
        ]
        assert options["languages"] == [
            {"value": "de", "label": "Deutsch", "book_count": 2},
        ]
        assert options["publishers"] == [
            {"value": "Klett-Cotta", "label": "Klett-Cotta", "book_count": 2},
        ]
        assert options["formats"] == [
            {"value": "epub", "label": "epub", "book_count": 2},
        ]
        assert options["series"] == [
            {"value": "Mittelerde", "label": "Mittelerde", "book_count": 2},
        ]
        assert options["years"] == [
            {"value": "1937", "label": "1937", "book_count": 2},
        ]


def test_add_remove_and_normalize_tags(db_context):
    _settings, factory = db_context
    with factory() as session:
        book = add_book(session)
        add_book_tag(session, book, "  Science   Fiction ")
        add_book_tag(session, book, "science fiction")
        session.commit()

        assert [(tag.name, tag.normalized_name) for tag in book.tags] == [
            ("Fantasy", "fantasy"),
            ("Science Fiction", "science fiction"),
        ]

        remove_book_tag(session, book, book.tags[1].id)
        session.commit()
        assert [tag.name for tag in book.tags] == ["Fantasy"]
