from concurrent.futures import ThreadPoolExecutor
from itertools import count
from types import SimpleNamespace

import pytest

from backend import archive_paths


def test_id_collision_preserves_existing_book(tmp_path, monkeypatch):
    values = iter([
        SimpleNamespace(hex="a1b20000000040008000000000000001"),
        SimpleNamespace(hex="a1b20000000040008000000000000001"),
        SimpleNamespace(hex="a1b20000000040008000000000000002"),
    ])
    monkeypatch.setattr(archive_paths.uuid, "uuid4", lambda: next(values))
    first = archive_paths.reserve_book_location(tmp_path, "epub", lambda _: False)
    (first.directory / "book.epub").write_bytes(b"original")
    second = archive_paths.reserve_book_location(tmp_path, "epub", lambda _: False)
    assert first.directory != second.directory
    assert (first.directory / "book.epub").read_bytes() == b"original"
    assert second.relative_file == f"a1b2/{second.book_id}/book.epub"


def test_parallel_reservation_in_same_group(tmp_path, monkeypatch):
    sequence = count(1)
    monkeypatch.setattr(archive_paths.uuid, "uuid4", lambda: SimpleNamespace(
        hex=f"a1b2{next(sequence):08x}40008000000000000000"))
    with ThreadPoolExecutor(max_workers=12) as executor:
        locations = list(executor.map(
            lambda _: archive_paths.reserve_book_location(tmp_path, "pdf", lambda _id: False),
            range(100),
        ))
    assert len({location.book_id for location in locations}) == 100
    assert {location.directory.parent.name for location in locations} == {"a1b2"}
    for location in locations:
        staged = tmp_path / f".{location.book_id}"
        staged.mkdir()
        (staged / "book.pdf").write_bytes(b"pdf")
        archive_paths.publish_book_directory(staged, location)
        assert (tmp_path / location.relative_file).read_bytes() == b"pdf"


@pytest.mark.parametrize("book_id,file_format", [
    ("bk_123", "epub"), ("bk_" + "A" * 32, "epub"),
    ("bk_" + "a" * 32, "../pdf"),
])
def test_invalid_storage_components(book_id, file_format):
    with pytest.raises(ValueError):
        archive_paths.relative_book_file(book_id, file_format)
