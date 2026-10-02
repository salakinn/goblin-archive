"""Immutable input bundle preparation and validation."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHUNK = 1024 * 1024
MAX_BYTES = 200 * CHUNK
MAX_FILES = 20
PROTOCOL = "import-10k-v1"
EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3", ".fb2"}


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def data_hash(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8")


def separate(*paths: Path) -> None:
    resolved = [path.resolve() for path in paths]
    for i, left in enumerate(resolved):
        for right in resolved[i + 1:]:
            if left == right or left in right.parents or right in left.parents:
                raise ValueError(f"Überlappende Pfade: {left} und {right}")


def manifest(folder: Path) -> list[dict]:
    entries = []
    for path in sorted((p for p in folder.rglob("*") if p.is_file()),
                       key=lambda p: p.relative_to(folder).as_posix().encode("utf-8")):
        relative = path.relative_to(folder).as_posix()
        if relative in {"goblin.db-wal", "goblin.db-shm", "auth.db-wal", "auth.db-shm"}:
            continue
        entries.append({"path": relative, "bytes": path.stat().st_size,
                        "sha256": digest(path)})
    return entries


def verify_files(folder: Path, entries: list[dict]) -> None:
    actual = manifest(folder)
    if actual != entries:
        expected_by_path = {entry["path"]: entry for entry in entries}
        actual_by_path = {entry["path"]: entry for entry in actual}
        differences = [name for name in sorted(set(expected_by_path) | set(actual_by_path))
                       if expected_by_path.get(name) != actual_by_path.get(name)]
        raise ValueError(f"Dateimanifest weicht ab: {differences[:10]}")


def db_check(db_path: Path, library_dir: Path, expected_books: int) -> dict:
    if not db_path.is_file():
        raise ValueError(f"Datenbank fehlt: {db_path}")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        count = db.execute("SELECT count(*) FROM books").fetchone()[0]
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
        fts_missing = db.execute("SELECT count(*) FROM books b LEFT JOIN books_fts f "
                                "ON f.book_id=b.id WHERE f.book_id IS NULL").fetchone()[0]
        fts_orphaned = db.execute("SELECT count(*) FROM books_fts f LEFT JOIN books b "
                                 "ON b.id=f.book_id WHERE b.id IS NULL").fetchone()[0]
        paths = db.execute("SELECT library_path, cover_path FROM books").fetchall()
        counts = {}
        for table in ("book_comparisons", "book_fingerprints", "book_similarity_buckets"):
            counts[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    missing_files = [value for book, cover in paths for value in (book, cover)
                     if value and not (library_dir / value).is_file()]
    result = {"books": count, "integrity": integrity, "foreign_key_violations": len(foreign_keys),
              "fts_missing": fts_missing, "fts_orphaned": fts_orphaned,
              "missing_library_files": missing_files[:10], "analysis_counts": counts}
    if count != expected_books or integrity != "ok" or foreign_keys or fts_missing or fts_orphaned or missing_files:
        raise ValueError(f"Archivprüfung fehlgeschlagen: {result}")
    return result


def _backup_database(source: Path, target: Path) -> None:
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_db:
        with sqlite3.connect(target) as target_db:
            source_db.backup(target_db)
            target_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _select_books(snapshot: Path, count: int) -> None:
    db_path = snapshot / "goblin.db"
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        total = db.execute("SELECT count(*) FROM books").fetchone()[0]
        if total == count:
            return
        if total < count:
            raise ValueError(f"Nur {total} Bücher vorhanden; {count} benötigt")
        selected = db.execute("SELECT id FROM books ORDER BY sha256, id LIMIT ?", (count,)).fetchall()
        db.execute("CREATE TEMP TABLE benchmark_keep(id TEXT PRIMARY KEY)")
        db.executemany("INSERT INTO benchmark_keep(id) VALUES (?)", selected)
        removed = db.execute("SELECT library_path FROM books WHERE id NOT IN "
                             "(SELECT id FROM benchmark_keep)").fetchall()
        db.execute("DELETE FROM books_fts WHERE book_id NOT IN (SELECT id FROM benchmark_keep)")
        db.execute("DELETE FROM books WHERE id NOT IN (SELECT id FROM benchmark_keep)")
        db.execute("DROP TABLE benchmark_keep")
        db.commit()
        db.execute("VACUUM")
    library = (snapshot / "library").resolve()
    for (relative,) in removed:
        directory = (library / relative).parent.resolve()
        if library not in directory.parents or directory == library:
            raise ValueError(f"Archivpfad außerhalb des Testarchivs: {relative}")
        shutil.rmtree(directory)


def prepare(config_path: Path) -> Path:
    config = read_json(config_path)
    source = Path(config["snapshot_source"]).resolve()
    inputs = Path(config["input_source"]).resolve()
    bundle = Path(config["bundle_dir"]).resolve()
    production = Path(config.get("production_dir", ROOT / "goblin-data")).resolve()
    scratch = Path(config.get("scratch_dir", ROOT / "benchmarks/.local/run-archives")).resolve()
    expected = int(config.get("expected_books", 10_000))
    separate(source, inputs, bundle, production, scratch)
    if expected != 10_000 and not config.get("smoke_test"):
        raise ValueError("Für import-10k-v1 sind exakt 10.000 Ausgangsbücher erforderlich")
    if not source.is_dir() or not inputs.is_dir() or not (source / "goblin.db").is_file():
        raise ValueError("snapshot_source oder input_source fehlt")
    if bundle.exists():
        raise FileExistsError(f"Paket existiert bereits: {bundle}")
    with sqlite3.connect(f"file:{source / 'goblin.db'}?mode=ro", uri=True) as source_db:
        source_count = source_db.execute("SELECT count(*) FROM books").fetchone()[0]
    if source_count < expected:
        raise ValueError(f"Nur {source_count} Bücher vorhanden; {expected} benötigt")
    staging = source / "staging"
    if staging.exists() and any(staging.iterdir()):
        raise ValueError("Ausgangsbestand hat nichtleeres Staging; zuerst abschließen/bereinigen")
    db_check(source / "goblin.db", source / "library", source_count)
    bundle.mkdir(parents=True)
    try:
        shutil.copytree(source, bundle / "snapshot", ignore=shutil.ignore_patterns("*.db-wal", "*.db-shm", "*.goblin.lock"))
        _backup_database(source / "goblin.db", bundle / "snapshot" / "goblin.db")
        _select_books(bundle / "snapshot", expected)
        for path in (bundle / "snapshot").glob("auth*code"):
            path.unlink()
        for path in (bundle / "snapshot").glob("auth.db*"):
            path.unlink()
        subdirectories = config.get("input_subdirectories")
        if subdirectories is None:
            shutil.copytree(inputs, bundle / "inputs")
        else:
            (bundle / "inputs").mkdir()
            if not subdirectories or len(subdirectories) != len(set(subdirectories)):
                raise ValueError("input_subdirectories muss eindeutige Verzeichnisse enthalten")
            for relative in subdirectories:
                path = Path(relative)
                if path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
                    raise ValueError(f"Ungültiges Eingabeverzeichnis: {relative}")
                source_dir = (inputs / path).resolve()
                if inputs not in source_dir.parents or not source_dir.is_dir():
                    raise ValueError(f"Eingabeverzeichnis fehlt oder liegt außerhalb der Quelle: {relative}")
                shutil.copytree(source_dir, bundle / "inputs" / path)
        snapshot_entries = manifest(bundle / "snapshot")
        all_entries = manifest(bundle / "inputs")
        input_entries = [entry for entry in all_entries
                         if Path(entry["path"]).suffix.lower() in EXTENSIONS]
        if not input_entries:
            raise ValueError("Keine unterstützten Importdateien gefunden")
        batches, current, current_size = [], [], 0
        for number, entry in enumerate(input_entries, 1):
            if entry["bytes"] > MAX_BYTES:
                raise ValueError(f"Einzeldatei überschreitet 200 MiB: {entry['path']}")
            entry["input_id"] = f"input_{number:05d}"
            if len(current) == MAX_FILES or current_size + entry["bytes"] > MAX_BYTES:
                batches.append(current)
                current, current_size = [], 0
            current.append(entry["input_id"])
            current_size += entry["bytes"]
        if current:
            batches.append(current)
        write_json(bundle / "snapshot-manifest.json", {"files": snapshot_entries})
        input_paths = {entry["path"] for entry in input_entries}
        write_json(bundle / "input-manifest.json", {"files": input_entries,
                                                      "companions": [e for e in all_entries if e["path"] not in input_paths]})
        db_info = db_check(bundle / "snapshot" / "goblin.db", bundle / "snapshot" / "library", expected)
        dataset = {"protocol_id": PROTOCOL if expected == 10_000 else "import-smoke-v1",
                   "snapshot_manifest_sha256": digest(bundle / "snapshot-manifest.json"),
                   "input_manifest_sha256": digest(bundle / "input-manifest.json"),
                   "expected_books": expected, "input_count": len(input_entries),
                   "input_bytes": sum(e["bytes"] for e in input_entries),
                   "formats": dict(Counter(Path(e["path"]).suffix.lower() for e in input_entries)),
                   "batches": batches, "snapshot_source": str(source),
                   "analysis_counts": db_info["analysis_counts"]}
        dataset["dataset_id"] = data_hash(dataset)
        write_json(bundle / "dataset.json", dataset)
        concurrency = int(config.get("import_concurrency", 3))
        if concurrency != 3 and not config.get("smoke_test"):
            raise ValueError("import-10k-v1 benötigt drei Importarbeiter")
        if concurrency < 1:
            raise ValueError("import_concurrency muss positiv sein")
        protocol = {"protocol_id": dataset["protocol_id"], "import_concurrency": concurrency,
                    "provider_concurrency": 2, "max_upload_files": 20,
                    "max_upload_bytes": MAX_BYTES, "max_queued_import_files": 100,
                    "max_staging_bytes": 2 * 1024 * CHUNK, "backend_workers": 1,
                    "resource_interval_seconds": 0.1, "completion_interval_seconds": 0.01,
                    "ready_timeout_seconds": 120, "upload_timeout_seconds": 300,
                    "run_timeout_seconds": 1800, "pre_start_pause_seconds": 5,
                    "post_memory_seconds": 10, "required_runs": 10,
                    "scratch_dir": str(scratch),
                    "runner_version": 1}
        write_json(bundle / "protocol.json", protocol)
        write_json(bundle / "expected-results.json", {"status": "pending_pilots", "items": []})
        return bundle
    except BaseException:
        shutil.rmtree(bundle)
        raise


def verify(bundle: Path, *, require_expectations: bool = False) -> dict:
    bundle = bundle.resolve()
    dataset = read_json(bundle / "dataset.json")
    snapshot_manifest = bundle / "snapshot-manifest.json"
    input_manifest = bundle / "input-manifest.json"
    if digest(snapshot_manifest) != dataset["snapshot_manifest_sha256"]:
        raise ValueError("Snapshot-Manifest-Hash weicht ab")
    if digest(input_manifest) != dataset["input_manifest_sha256"]:
        raise ValueError("Import-Manifest-Hash weicht ab")
    identity = {k: v for k, v in dataset.items() if k != "dataset_id"}
    if data_hash(identity) != dataset["dataset_id"]:
        raise ValueError("Datensatzkennung weicht ab")
    protocol = read_json(bundle / "protocol.json")
    if protocol["protocol_id"] != dataset["protocol_id"]:
        raise ValueError("Protokoll und Datensatz verwenden unterschiedliche Kennungen")
    if dataset["protocol_id"] == PROTOCOL:
        fixed = {"import_concurrency": 3, "provider_concurrency": 2,
                 "max_upload_files": MAX_FILES, "max_upload_bytes": MAX_BYTES,
                 "max_queued_import_files": 100, "max_staging_bytes": 2 * 1024 * CHUNK,
                 "backend_workers": 1, "required_runs": 10}
        if dataset["expected_books"] != 10_000 or any(protocol.get(k) != v for k, v in fixed.items()):
            raise ValueError("Feste Werte von import-10k-v1 wurden verändert")
    verify_files(bundle / "snapshot", read_json(snapshot_manifest)["files"])
    expected_inputs = read_json(input_manifest)
    input_ids = [entry["input_id"] for entry in expected_inputs["files"]]
    flattened = [item_id for batch in dataset["batches"] for item_id in batch]
    sizes = {entry["input_id"]: entry["bytes"] for entry in expected_inputs["files"]}
    if (flattened != input_ids or any(len(batch) > MAX_FILES or
        sum(sizes[item_id] for item_id in batch) > MAX_BYTES for batch in dataset["batches"]) or
        dataset["input_count"] != len(input_ids) or
        dataset["input_bytes"] != sum(sizes.values())):
        raise ValueError("Batchliste oder Eingabestatistik weicht vom Manifest ab")
    verify_files(bundle / "inputs", sorted(
        [{k: v for k, v in entry.items() if k != "input_id"}
         for entry in expected_inputs["files"]] + expected_inputs.get("companions", []),
        key=lambda entry: entry["path"].encode("utf-8")))
    db_info = db_check(bundle / "snapshot" / "goblin.db", bundle / "snapshot" / "library",
                       dataset["expected_books"])
    if require_expectations:
        if read_json(bundle / "expected-results.json")["status"] != "frozen":
            raise ValueError("Ergebnisreferenz fehlt; zuerst zwei Piloten ausführen")
        seal = read_json(bundle / "bundle-seal.json")
        for name, expected_hash in seal["sha256"].items():
            if digest(bundle / name) != expected_hash:
                raise ValueError(f"Versiegelte Paketdatei verändert: {name}")
    return {"dataset": dataset, "protocol": protocol,
            "inputs": expected_inputs["files"], "snapshot": read_json(snapshot_manifest)["files"],
            "database": db_info}


def seal(bundle: Path) -> None:
    names = ("dataset.json", "protocol.json", "snapshot-manifest.json",
             "input-manifest.json", "expected-results.json")
    write_json(bundle / "bundle-seal.json", {"sha256": {name: digest(bundle / name) for name in names}})
