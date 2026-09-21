from backend.metadata import (
    BookMetadata,
    FieldValue,
    language_label,
    normalize_tag_name,
    normalize_language,
    normalize_text,
    plausible_match,
    sanitize_component,
)


def test_sanitize_component_is_cross_platform_safe():
    assert sanitize_component('../CON:<Der "Hobbit">?*') == 'CON Der Hobbit'
    assert sanitize_component("NUL") == "_NUL"
    assert len(sanitize_component("x" * 500)) == 120
    assert sanitize_component("\x00 / ") == "Unbekannt"


def test_normalization_and_plausible_match():
    assert normalize_text("  DÉR   Hobbit! ") == "der hobbit"
    candidate = BookMetadata(
        title=FieldValue("Der Hobbit oder Hin und zurück"),
        authors=FieldValue(["J. R. R. Tolkien"]),
    )
    assert plausible_match(candidate, "Der Hobbit", "J R R Tolkien")
    assert not plausible_match(candidate, "Dune", "Frank Herbert")


def test_language_normalization():
    for value in ("de", "de-DE", "de_DE", "de de", "deu", "ger", "Deutsch", "German"):
        assert normalize_language(value) == "de"
        assert language_label(value) == "Deutsch"
    assert normalize_language("spa") == "es"
    assert language_label("spa") == "Spanisch"
    assert normalize_language(None) is None


def test_tag_normalization_preserves_punctuation():
    assert normalize_tag_name("  Science   Fiction ") == "science fiction"
    assert normalize_tag_name("C++") == "c++"
