from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import zipfile
import asyncio
import shutil
import json
from types import SimpleNamespace

import pytest

from backend import auth
from backend.backup import (BackupError, archive_lock, create_backup, restore_backup, verify_backup,
                            pack_backup, unpack_backup, restore_in_place,
                            recover_interrupted_restore)
from backend.models import ImportPreview
from backend.tests.conftest import add_book
from backend.backup_jobs import BackupJobs


def _archive(db_context):
    settings, factory = db_context
    settings.ensure_directories()
    data = b"archived epub bytes"
    digest = sha256(data).hexdigest()
    with factory() as db:
        book = add_book(db, sha=digest)
        book.file_size = len(data)
        db.commit()
        path = settings.library_dir / book.library_path
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        (path.parent / "metadata.json").write_text(book.metadata_json + "\n")
        now = datetime.now(timezone.utc)
        stage = settings.staging_dir / "pending.epub"
        stage.write_bytes(b"pending")
        db.add(ImportPreview(id="pr_pending", group_id="test", filename="pending.epub",
                             staging_path=str(stage), created_at=now,
                             expires_at=now + timedelta(hours=24), status="ready"))
        db.add(ImportPreview(id="pr_done", group_id="test", filename="done.epub",
                             staging_path=str(settings.staging_dir / "already-removed.epub"),
                             created_at=now, expires_at=now + timedelta(hours=24), status="archived"))
        db.commit()
    auth.ensure_setup_code(settings.data_dir)
    code = (settings.data_dir / "auth-setup-code").read_text().strip()
    auth.setup(settings.data_dir, code, "correct horse battery", "test")
    (settings.data_dir / "ai-settings.json").write_text(
        '{"provider":"custom","base_url":"https://example.test/v1",'
        '"api_key":"openai-secret","custom_api_key":"custom-secret"}')
    return settings, path, digest


def test_backup_restore_verifies_files_and_removes_ai_keys(db_context, tmp_path):
    settings, path, digest = _archive(db_context)
    backup = tmp_path / "backup"
    manifest = create_backup(settings.data_dir, backup)
    assert manifest["ai_keys_included"] is False
    assert verify_backup(backup) == manifest
    assert "custom-secret" not in (backup / "ai-settings.json").read_text()
    assert "openai-secret" not in (backup / "ai-settings.json").read_text()

    restored = tmp_path / "restored"
    assert restore_backup(backup, restored) is None
    assert (restored / "library" / path.relative_to(settings.library_dir)).read_bytes() == path.read_bytes()
    from sqlite3 import connect
    with connect(restored / "goblin.db") as db:
        assert db.execute("SELECT sha256 FROM books").fetchone()[0] == digest
        assert db.execute("SELECT staging_path FROM import_previews WHERE id='pr_pending'").fetchone()[0] == str(restored / "staging" / "pending.epub")
    with connect(restored / "auth.db") as db:
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    assert auth.login(restored, "correct horse battery", "test")


def test_corrupt_backup_cannot_replace_existing_data(db_context, tmp_path):
    settings, path, _digest = _archive(db_context)
    backup = tmp_path / "backup"
    create_backup(settings.data_dir, backup)
    (backup / "library" / path.relative_to(settings.library_dir)).write_bytes(b"damaged")
    with pytest.raises(BackupError):
        verify_backup(backup)
    with pytest.raises(BackupError):
        restore_backup(backup, settings.data_dir, replace=True)
    assert path.read_bytes() == b"archived epub bytes"


def test_backup_requires_stopped_service_and_restore_preserves_old_data(db_context, tmp_path):
    settings, path, _digest = _archive(db_context)
    backup = tmp_path / "backup"
    with archive_lock(settings.data_dir):
        with pytest.raises(BackupError):
            create_backup(settings.data_dir, backup)
    create_backup(settings.data_dir, backup)
    with pytest.raises(BackupError):
        restore_backup(backup, settings.data_dir)
    previous = restore_backup(backup, settings.data_dir, replace=True)
    assert previous and (previous / "library" / path.relative_to(settings.library_dir)).is_file()
    assert path.is_file()


def test_zip_package_roundtrip_and_unsafe_entries(db_context, tmp_path):
    settings, _path, _digest = _archive(db_context)
    directory = tmp_path / "backup"
    create_backup(settings.data_dir, directory)
    package = tmp_path / "archive.zip"
    pack_backup(directory, package)
    extracted = tmp_path / "extracted"
    assert unpack_backup(package, extracted, max_bytes=100_000_000) == verify_backup(directory)
    with pytest.raises(BackupError):
        unpack_backup(package, tmp_path / "too-small", max_bytes=1)
    malicious = tmp_path / "malicious.zip"
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("../escape", b"bad")
    with pytest.raises(BackupError):
        unpack_backup(malicious, tmp_path / "unsafe", max_bytes=1000)
    assert not (tmp_path / "escape").exists()
    import backend.backup as backup_module
    from collections import namedtuple
    original_usage = backup_module.shutil.disk_usage
    Usage = namedtuple("Usage", "total used free")
    try:
        backup_module.shutil.disk_usage = lambda _path: Usage(100, 100, 0)
        with pytest.raises(BackupError, match="Speicher"):
            unpack_backup(package, tmp_path / "no-space", max_bytes=100_000_000)
    finally:
        backup_module.shutil.disk_usage = original_usage
    assert not (tmp_path / "no-space").exists()


def test_in_place_restore_keeps_previous_archive_and_rolls_back(db_context, tmp_path, monkeypatch):
    settings, book_path, _digest = _archive(db_context)
    source = tmp_path / "backup"
    create_backup(settings.data_dir, source)
    (settings.data_dir / "library" / "extra.txt").write_text("old data")
    import backend.backup as backup_module
    original = backup_module._check_database
    def fail_after_swap(root):
        if root == settings.data_dir.resolve():
            raise BackupError("simulierter Abschlussfehler")
        return original(root)
    monkeypatch.setattr(backup_module, "_check_database", fail_after_swap)
    with pytest.raises(BackupError, match="Abschlussfehler"):
        restore_in_place(source, settings.data_dir)
    assert (settings.data_dir / "library" / "extra.txt").read_text() == "old data"
    monkeypatch.setattr(backup_module, "_check_database", original)
    previous = restore_in_place(source, settings.data_dir)
    assert previous.is_dir()
    assert (previous / "library" / "extra.txt").read_text() == "old data"
    assert book_path.is_file()


def test_interrupted_swap_recovers_old_database_before_start(db_context):
    settings, book_path, _digest = _archive(db_context)
    previous = settings.data_dir / ".before-restore-interrupted"
    previous.mkdir()
    stage = settings.data_dir / ".restore-stage-interrupted"
    stage.mkdir()
    (stage / "partial").write_text("new")
    (settings.data_dir / "goblin.db").rename(previous / "goblin.db")
    (settings.data_dir / ".restore-journal").write_text(json.dumps({
        "previous": previous.name,
        "old_names": ["goblin.db", "auth.db", "library"],
        "new_names": ["goblin.db", "auth.db", "library"],
    }))
    recover_interrupted_restore(settings.data_dir)
    assert (settings.data_dir / "goblin.db").is_file()
    assert book_path.is_file()
    assert not previous.exists()
    assert not stage.exists()


@pytest.mark.asyncio
async def test_server_job_survives_client_and_restores(db_context, tmp_path, monkeypatch):
    settings, book_path, digest = _archive(db_context)
    class Scanner:
        async def stop(self): pass
        def resume(self): pass
    app = SimpleNamespace(state=SimpleNamespace(
        import_manager=SimpleNamespace(has_active_imports=lambda: False, tasks=set(),
                                       settings=settings, jobs={}),
        preview_manager=SimpleNamespace(tasks=set()),
        translation_manager=SimpleNamespace(running=set(), repairing=set()),
        duplicate_scanner=Scanner(),
    ))
    jobs = BackupJobs(settings.data_dir)
    monkeypatch.setattr("backend.database.init_db", lambda: None)
    monkeypatch.setattr("backend.metadata_store.recover_metadata", lambda *_: None)
    monkeypatch.setattr("backend.translation.TranslationManager", lambda *_: SimpleNamespace(running=set(), repairing=set()))
    job = jobs.create(app)
    while jobs.tasks:
        await asyncio.gather(*list(jobs.tasks))
    assert jobs.get(job["id"])["status"] == "ready"
    package = jobs.root / f"{job['id']}.zip"
    upload = jobs.root / "upload.zip"
    shutil.copyfile(package, upload)
    prepared = jobs.prepared_restore(upload, 100_000_000)
    book_path.write_bytes(b"modified")
    jobs.restore(app, prepared["id"])
    while jobs.tasks:
        await asyncio.gather(*list(jobs.tasks))
    result = jobs.get(prepared["id"])
    assert result["status"] == "done", result.get("error")
    assert book_path.read_bytes() == b"archived epub bytes"
    assert result["previous_bytes"] > 0
    jobs.delete_previous(prepared["id"])
    assert "previous" not in jobs.get(prepared["id"])
