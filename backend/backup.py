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
import zipfile
import stat
from importlib.metadata import PackageNotFoundError, version as package_version
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

FORMAT_VERSION = 1
SCHEMA_VERSION = 0
MANIFEST = "manifest.json"
DATABASES = ("goblin.db", "auth.db")


class BackupError(ValueError):
    pass


@contextmanager
def archive_lock(data_dir: Path):
    """A stable lock path outside data_dir survives an atomic restore rename."""
    data_dir = data_dir.resolve()
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    path = (data_dir.parent / f".{data_dir.name}.goblin.lock"
            if os.access(data_dir.parent, os.W_OK) else data_dir / ".goblin.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
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
    if not isinstance(manifest.get("schema_version", 0), int) or manifest.get("schema_version", 0) > SCHEMA_VERSION:
        raise BackupError("Backup-Schema ist neuer als diese Installation")
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


def create_backup(data_dir: Path, output: Path, *, locked: bool = False) -> dict:
    data_dir = data_dir.resolve()
    output = output.resolve()
    service_output = locked and data_dir / ".backup-jobs" in output.parents
    if (output == data_dir or output in data_dir.parents or
            (data_dir in output.parents and not service_output)):
        raise BackupError("Backup-Ziel und Datenverzeichnis dürfen nicht ineinander liegen")
    with (nullcontext() if locked else archive_lock(data_dir)):
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
            with sqlite3.connect(temp / "goblin.db") as db:
                book_count = db.execute("SELECT count(*) FROM books").fetchone()[0]
                schema_version = db.execute("PRAGMA user_version").fetchone()[0]
            try:
                app_version = package_version("goblin-archivar")
            except PackageNotFoundError:
                app_version = "0.1.0"
            manifest = {"format": FORMAT_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                        "app_version": app_version,
                        "book_count": book_count, "schema_version": schema_version,
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


def pack_backup(source: Path, output: Path, progress=None) -> None:
    """Stream a verified directory into a ZIP64 package."""
    verify_backup(source)
    if output.exists():
        raise BackupError("Backup-Datei existiert bereits")
    temporary = output.with_name(f".{output.name}-{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
            for path in _regular_files(source):
                relative = path.relative_to(source).as_posix()
                info = zipfile.ZipInfo(relative)
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = 0o600 << 16
                with path.open("rb") as reader, archive.open(info, "w", force_zip64=True) as writer:
                    while chunk := reader.read(1024 * 1024):
                        writer.write(chunk)
                        if progress:
                            progress(len(chunk))
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary.rename(output)
    finally:
        temporary.unlink(missing_ok=True)


def unpack_backup(source: Path, output: Path, *, max_bytes: int, max_files: int = 200000) -> dict:
    """Reject unsafe ZIP members before extracting into a new directory."""
    if output.exists():
        raise BackupError("Arbeitsverzeichnis existiert bereits")
    with zipfile.ZipFile(source, "r", allowZip64=True) as archive:
        entries = archive.infolist()
        extracted_bytes = sum(item.file_size for item in entries)
        if len(entries) > max_files or extracted_bytes > max_bytes:
            raise BackupError("Backup überschreitet die zulässige entpackte Größe")
        if shutil.disk_usage(output.parent).free < extracted_bytes + 64 * 1024 * 1024:
            raise BackupError("Nicht genug freier Speicher zum Entpacken")
        seen = set()
        for item in entries:
            name = _safe_relative(item.filename)
            kind = (item.external_attr >> 16) & 0o170000
            if (name in seen or item.is_dir() or kind not in (0, stat.S_IFREG) or
                    item.flag_bits & 1 or item.file_size > max_bytes):
                raise BackupError(f"Ungültiger ZIP-Eintrag: {name}")
            seen.add(name)
        output.mkdir(mode=0o700, parents=True)
        try:
            for item in entries:
                target = output / item.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, 1024 * 1024)
                os.chmod(target, 0o600)
            return verify_backup(output)
        except Exception:
            shutil.rmtree(output)
            raise


def restore_backup(source: Path, data_dir: Path, *, replace: bool = False) -> Path | None:
    source = source.resolve()
    data_dir = data_dir.resolve()
    if source == data_dir or source in data_dir.parents or data_dir in source.parents:
        raise BackupError("Backup und Ziel dürfen nicht ineinander liegen")
    with archive_lock(data_dir):
        verify_backup(source)
        if data_dir.exists() and any(data_dir.iterdir()) and not replace:
            raise BackupError("Ziel ist nicht leer; für einen Austausch --replace angeben")
        if data_dir.exists() and (data_dir.is_mount() or not os.access(data_dir.parent, os.W_OK)):
            return restore_in_place(source, data_dir)
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


def recover_interrupted_restore(data_dir: Path) -> None:
    """Finish rollback before opening any database after a process crash."""
    marker = data_dir / ".restore-journal"
    if not marker.exists():
        if data_dir.is_dir():
            for stage in data_dir.glob(".restore-stage-*"):
                if stage.is_dir() and not stage.is_symlink():
                    shutil.rmtree(stage)
        return
    state = json.loads(marker.read_text(encoding="utf-8"))
    previous = data_dir / state["previous"]
    if previous.is_dir():
        moved = {path.name for path in previous.iterdir()}
        for name in moved | (set(state["new_names"]) - set(state["old_names"])):
            path = data_dir / name
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        for path in previous.iterdir():
            path.rename(data_dir / path.name)
        previous.rmdir()
    marker.unlink()
    for stage in data_dir.glob(".restore-stage-*"):
        if stage.is_dir() and not stage.is_symlink():
            shutil.rmtree(stage)


def restore_in_place(source: Path, data_dir: Path, *, validate=None) -> Path:
    """Replace contents without renaming a mounted data directory."""
    source = source.resolve()
    data_dir = data_dir.resolve()
    manifest = verify_backup(source)
    data_dir.mkdir(parents=True, exist_ok=True)
    service_source = data_dir / ".backup-jobs" in source.parents
    if source == data_dir or source in data_dir.parents or (data_dir in source.parents and not service_source):
        raise BackupError("Backup und Ziel dürfen nicht ineinander liegen")
    recover_interrupted_restore(data_dir)
    token = uuid.uuid4().hex
    stage = data_dir / f".restore-stage-{token}"
    previous = data_dir / f".before-restore-{token}"
    marker = data_dir / ".restore-journal"
    stage.mkdir(mode=0o700)
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
        previous.mkdir(mode=0o700)
        names = [path.name for path in data_dir.iterdir()
                 if path not in (stage, previous) and path.name not in (".backup-jobs", ".restore-journal", ".goblin.lock")]
        new_names = [path.name for path in stage.iterdir()]
        marker.write_text(json.dumps({"previous": previous.name, "old_names": names,
                                      "new_names": new_names}), encoding="utf-8")
        os.chmod(marker, 0o600)
        try:
            for name in names:
                (data_dir / name).rename(previous / name)
            for path in list(stage.iterdir()):
                path.rename(data_dir / path.name)
            _check_database(data_dir)
            if validate:
                validate()
                _check_database(data_dir)
            marker.unlink()
            return previous
        except Exception:
            recover_interrupted_restore(data_dir)
            raise
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
