"""Run manually on the archive filesystem: python -m backend.tests.benchmark_archive_paths."""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from backend.archive_paths import reserve_book_location


def measure(library_dir: Path, count: int) -> dict:
    started = time.perf_counter()
    ids = set()
    paths = []
    for _ in range(count):
        location = reserve_book_location(library_dir, "epub", ids.__contains__)
        ids.add(location.book_id)
        path = library_dir / location.relative_file
        path.write_bytes(b"x")
        paths.append(path)
    created = time.perf_counter()
    assert len(ids) == count
    assert all(path.read_bytes() == b"x" for path in paths)
    resolved = time.perf_counter()
    groups = list(library_dir.iterdir())
    listed = time.perf_counter()
    assert all(group.is_dir() for group in groups)
    return {"books": count, "groups": len(groups),
            "creation_seconds": round(created - started, 3),
            "resolution_seconds": round(resolved - created, 3),
            "root_listing_seconds": round(listed - resolved, 3)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("goblin-data"))
    parser.add_argument("--count", type=int, default=100_000)
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".archive-benchmark-", dir=args.data_dir) as root:
        print(json.dumps(measure(Path(root), args.count), indent=2))
