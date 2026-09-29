from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from backend import auth
from backend.backup import BackupError, archive_lock, create_backup, restore_backup, verify_backup
from backend.models import ImportPreview
from backend.tests.conftest import add_book


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
