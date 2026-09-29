"""Offline, verified backups of the archive and its two SQLite databases."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

FORMAT_VERSION = 1
MANIFEST = "manifest.json"
DATABASES = ("goblin.db", "auth.db")


class BackupError(ValueError):
    pass


@contextmanager
def archive_lock(data_dir: Path):
    """A stable lock path outside data_dir survives an atomic restore rename."""
    data_dir = data_dir.resolve()
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    path = data_dir.parent / f".{data_dir.name}.goblin.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("Archiv wird vom Dienst oder einem anderen Backup verwendet") from exc
        yield
    finally:
        os.close(fd)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_files(directory: Path):
    if not directory.exists():
        return
    if directory.is_symlink() or not directory.is_dir():
        raise BackupError("Archivverzeichnis ist kein reguläres Verzeichnis")
    for root, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs:
            if (Path(root) / name).is_symlink():
                raise BackupError("Archiv enthält einen symbolischen Link")
        for name in files:
            path = Path(root) / name
            if not path.is_file() or path.is_symlink():
                raise BackupError("Archiv enthält eine nicht reguläre Datei")
            yield path


def _sqlite_snapshot(source: Path, target: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise BackupError(f"Datenbank fehlt: {source.name}")
    with sqlite3.connect(source) as input_db, sqlite3.connect(target) as output_db:
        input_db.backup(output_db)
    os.chmod(target, 0o600)


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise BackupError("Backup enthält einen ungültigen Dateipfad")
    path = PurePosixPath(value)
    if (not path.parts or path.is_absolute() or ".." in path.parts or "\\" in value or
            ":" in path.parts[0] or path.as_posix() != value):
        raise BackupError("Backup enthält einen ungültigen Dateipfad")
    return path.as_posix()


def _check_database(root: Path) -> None:
    for name in DATABASES:
        with sqlite3.connect(f"{(root / name).as_uri()}?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError(f"Datenbank ist beschädigt: {name}")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise BackupError(f"Datenbank enthält ungültige Verknüpfungen: {name}")
    with sqlite3.connect(f"{(root / 'goblin.db').as_uri()}?mode=ro", uri=True) as db:
        books = db.execute("SELECT id, library_path, sha256, cover_path, metadata_json FROM books").fetchall()
        previews = db.execute("SELECT staging_path, cover_path, embedded_cover_path, external_cover_path "
                              "FROM import_previews WHERE status NOT IN ('archived', 'discarded')").fetchall()
    for book_id, library_path, digest, cover_path, metadata_json in books:
        relative = _safe_relative(library_path)
        book_file = root / "library" / relative
        if not book_file.is_file() or _digest(book_file) != digest:
            raise BackupError(f"Buchdatei fehlt oder hat eine falsche Prüfsumme: {relative}")
        metadata_file = book_file.parent / "metadata.json"
        if not metadata_file.is_file():
            raise BackupError(f"Metadaten-Datei fehlt: {relative}")
        journal = root / "metadata-recovery" / f"{book_id}.json"
        if not journal.exists():
            try:
                file_metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
                database_metadata = json.loads(metadata_json)
            except (OSError, ValueError) as exc:
                raise BackupError(f"Metadaten-Datei ist ungültig: {relative}") from exc
            if file_metadata != database_metadata:
                raise BackupError(f"Metadaten-Datei und Datenbank widersprechen sich: {relative}")
        if cover_path and not (root / "library" / _safe_relative(cover_path)).is_file():
            raise BackupError(f"Cover fehlt: {cover_path}")
    for paths in previews:
        for value in paths:
            if value:
                name = Path(value).name
                if name != value.split("/")[-1] or not (root / "staging" / _safe_relative(name)).is_file():
                    raise BackupError(f"Vorschaudatei fehlt: {name}")


def verify_backup(source: Path) -> dict:
    source = source.resolve()
    try:
        manifest = json.loads((source / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError("Backup-Manifest fehlt oder ist ungültig") from exc
    if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT_VERSION or
            not isinstance(manifest.get("files"), dict)):
        raise BackupError("Unbekanntes Backup-Format")
    expected = set(manifest["files"])
    actual = {path.relative_to(source).as_posix() for path in _regular_files(source)} - {MANIFEST}
    if actual != expected or not set(DATABASES).issubset(expected):
        raise BackupError("Backup ist unvollständig oder enthält unerwartete Dateien")
    for relative, details in manifest["files"].items():
        if _safe_relative(relative) != relative or relative == MANIFEST:
            raise BackupError("Backup enthält einen ungültigen Dateipfad")
        path = source / relative
        if not isinstance(details, dict) or path.stat().st_size != details.get("size") or _digest(path) != details.get("sha256"):
            raise BackupError(f"Prüfsumme falsch: {relative}")
    _check_database(source)
    return manifest


def create_backup(data_dir: Path, output: Path) -> dict:
    data_dir = data_dir.resolve()
    output = output.resolve()
    if output == data_dir or data_dir in output.parents or output in data_dir.parents:
        raise BackupError("Backup-Ziel und Datenverzeichnis dürfen nicht ineinander liegen")
    with archive_lock(data_dir):
        if output.exists():
            raise BackupError("Backup-Ziel existiert bereits")
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.parent / f".{output.name}-{uuid.uuid4().hex}.tmp"
        temp.mkdir(mode=0o700)
        try:
            for name in DATABASES:
                _sqlite_snapshot(data_dir / name, temp / name)
            for directory in ("library", "staging", "metadata-recovery"):
                for source in _regular_files(data_dir / directory):
                    target = temp / source.relative_to(data_dir)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    os.chmod(target, 0o600)
            config = data_dir / "ai-settings.json"
            if config.exists():
                data = json.loads(config.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise BackupError("KI-Einstellungen sind ungültig")
                data["api_key"] = ""
                data["custom_api_key"] = ""
                (temp / "ai-settings.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                os.chmod(temp / "ai-settings.json", 0o600)
            _check_database(temp)
            files = {path.relative_to(temp).as_posix(): {"size": path.stat().st_size, "sha256": _digest(path)}
                     for path in _regular_files(temp)}
            manifest = {"format": FORMAT_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                        "files": files, "ai_keys_included": False}
            (temp / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.chmod(temp / MANIFEST, 0o600)
            verify_backup(temp)
            temp.rename(output)
            return manifest
        finally:
            if temp.exists():
                shutil.rmtree(temp)


def _relocate_previews(database: Path, staging_dir: Path) -> None:
    with sqlite3.connect(database) as db:
        rows = db.execute("SELECT id, staging_path, cover_path, embedded_cover_path, external_cover_path FROM import_previews").fetchall()
        for row in rows:
            values = [(str(staging_dir / Path(value).name) if value else None) for value in row[1:]]
            db.execute("UPDATE import_previews SET staging_path=?, cover_path=?, embedded_cover_path=?, external_cover_path=? WHERE id=?",
                       (*values, row[0]))
        db.execute("UPDATE ai_usage SET status='interrupted', reserved_usd=0, "
                   "error='Backup wiederhergestellt; Kosten der unterbrochenen Anfrage unbekannt' "
                   "WHERE status='reserved'")
        db.commit()
    with sqlite3.connect(database.parent / "auth.db") as db:
        db.execute("DELETE FROM sessions")
        db.commit()


def restore_backup(source: Path, data_dir: Path, *, replace: bool = False) -> Path | None:
    source = source.resolve()
    data_dir = data_dir.resolve()
    if source == data_dir or source in data_dir.parents or data_dir in source.parents:
        raise BackupError("Backup und Ziel dürfen nicht ineinander liegen")
    with archive_lock(data_dir):
        verify_backup(source)
        if data_dir.exists() and any(data_dir.iterdir()) and not replace:
            raise BackupError("Ziel ist nicht leer; für einen Austausch --replace angeben")
        stage = data_dir.parent / f".{data_dir.name}-restore-{uuid.uuid4().hex}.tmp"
        stage.mkdir(mode=0o700)
        previous = None
        try:
            for path in _regular_files(source):
                if path.name == MANIFEST:
                    continue
                target = stage / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                os.chmod(target, 0o600)
            _check_database(stage)
            _relocate_previews(stage / "goblin.db", data_dir / "staging")
            if data_dir.exists():
                previous = data_dir.parent / f"{data_dir.name}.before-restore-{uuid.uuid4().hex[:8]}"
                data_dir.rename(previous)
            try:
                stage.rename(data_dir)
            except OSError:
                if previous:
                    previous.rename(data_dir)
                raise
            return previous
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def main() -> int:
    parser = argparse.ArgumentParser(description="Goblin-Archiv sichern, prüfen oder wiederherstellen")
    parser.add_argument("action", choices=("create", "verify", "restore"))
    parser.add_argument("path", type=Path, help="Backup-Verzeichnis")
    parser.add_argument("--data-dir", type=Path, default=Path("goblin-data"))
    parser.add_argument("--replace", action="store_true", help="Vorhandenes Archiv austauschen; altes Archiv bleibt erhalten")
    args = parser.parse_args()
    try:
        if args.action == "create":
            result = create_backup(args.data_dir, args.path)
            print(f"Backup erstellt: {args.path} ({len(result['files'])} Dateien)")
        elif args.action == "verify":
            result = verify_backup(args.path)
            print(f"Backup geprüft: {args.path} ({len(result['files'])} Dateien)")
        else:
            previous = restore_backup(args.path, args.data_dir, replace=args.replace)
            print(f"Archiv wiederhergestellt: {args.data_dir}")
            if previous:
                print(f"Vorheriges Archiv: {previous}")
        return 0
    except (BackupError, OSError, sqlite3.Error) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
