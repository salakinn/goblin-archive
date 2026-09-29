import zipfile

import pytest

from backend.extractors import InvalidBookError, extract_epub
from backend.language import TextExtractionError, _epub_units


@pytest.mark.parametrize("name", ["huge.xhtml", "../escape.xhtml", "/absolute.xhtml"])
def test_untrusted_epub_is_rejected_before_parsing(tmp_path, name):
    path = tmp_path / "unsafe.epub"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(name, b"x" * (33 * 1024 * 1024) if name == "huge.xhtml" else b"x")
    with pytest.raises(InvalidBookError):
        extract_epub(path)
    with pytest.raises(TextExtractionError):
        _epub_units(path)
