from __future__ import annotations

import json
import time

import pytest

from benchmarks.tools.analysis import comparison, metrics
from benchmarks.tools.bundle import _select_books, data_hash, db_check, manifest, verify_files
from benchmarks.tools.engine import _completion
from backend.tests.conftest import add_book


def test_bundle_hashes_detect_changed_input(tmp_path):
    file = tmp_path / "one.epub"
    file.write_bytes(b"first")
    expected = manifest(tmp_path)
    verify_files(tmp_path, expected)
    assert data_hash({"a": 1, "b": 2}) == data_hash({"b": 2, "a": 1})
    file.write_bytes(b"second")
    with pytest.raises(ValueError, match="Dateimanifest"):
        verify_files(tmp_path, expected)


def test_completion_requires_ready_preview_after_item(tmp_path):
    path = tmp_path / "completions.jsonl"
    events = [
        {"kind": "item_registered", "item_id": "item_1"},
        {"kind": "item_complete", "item_id": "item_1", "status": "needs_review",
         "preview_id": "preview_1"},
        {"kind": "preview_registered", "preview_id": "preview_1"},
    ]
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    with pytest.raises(TimeoutError):
        _completion(path, {"item_1"}, time.monotonic() + 0.03)
    with path.open("a") as file:
        file.write(json.dumps({"kind": "preview_complete", "preview_id": "preview_1",
                               "status": "ready", "error": None}) + "\n")
    result = _completion(path, {"item_1"}, time.monotonic() + 0.2)
    assert result["previews"]["preview_1"]["status"] == "ready"


def test_paired_statistics_use_complete_paired_runs():
    pairs = []
    for number in range(10):
        a = {"status": "completed", "import_total_seconds": 10 + number / 100,
             "files_per_minute": 6, "backend_peak_rss_bytes": 100}
        b = {"status": "completed", "import_total_seconds": 8 + number / 100,
             "files_per_minute": 7, "backend_peak_rss_bytes": 110}
        pairs.append({"a": a, "b": b})
    stats = comparison(pairs, metrics([p["a"] for p in pairs]),
                       metrics([p["b"] for p in pairs]))
    assert stats["reliably_faster"] is True
    assert stats["paired_time_saved_percent_ci95"][0] > 0
    assert comparison(pairs[:-1], metrics([p["a"] for p in pairs[:-1]]),
                      metrics([p["b"] for p in pairs[:-1]]))["reliably_faster"] is None


def test_snapshot_selection_removes_unselected_books_and_files(db_context):
    settings, factory = db_context
    with factory() as session:
        books = [add_book(session, book_id=f"bk_test{i}", sha=sha)
                 for i, sha in enumerate(("a" * 64, "b" * 64))]
    for book in books:
        path = settings.library_dir / book.library_path
        path.parent.mkdir(parents=True)
        path.write_bytes(b"book")
    _select_books(settings.data_dir, 1)
    assert (settings.library_dir / books[0].library_path).exists()
    assert not (settings.library_dir / books[1].library_path).exists()
    assert db_check(settings.database_path, settings.library_dir, 1)["books"] == 1
