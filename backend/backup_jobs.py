"""Server owned backup jobs and a write gate shared with HTTP requests."""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.backup import (BackupError, create_backup, pack_backup, restore_in_place,
                            unpack_backup, verify_backup)


class BackupJobs:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.root = self.data_dir / ".backup-jobs"
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.tasks: set[asyncio.Task] = set()
        self.maintenance = False
        self.switching = False
        self.active_writes = 0
        self.busy = False
        for record in self.root.glob("*.json"):
            try:
                job = json.loads(record.read_text())
                if job["status"] in {"waiting", "running", "uploading"}:
                    job["status"] = "failed"
                    job["error"] = "Dienst während des Auftrags beendet"
                    record.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
                    (self.root / f"{job['id']}.zip").unlink(missing_ok=True)
                    if job["kind"] == "backup":
                        shutil.rmtree(self.root / job["id"], ignore_errors=True)
                self.jobs[job["id"]] = job
            except (ValueError, KeyError, OSError):
                continue
        for path in self.root.glob("upload-*.zip"):
            path.unlink(missing_ok=True)
        for path in self.root.glob(".*.tmp"):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

    def _save(self, job: dict) -> None:
        path = self.root / f"{job['id']}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def _new(self, kind: str) -> dict:
        job = {"id": uuid.uuid4().hex, "kind": kind, "status": "waiting",
               "phase": "Wartet", "processed_bytes": 0,
               "created_at": datetime.now(timezone.utc).isoformat()}
        self.jobs[job["id"]] = job
        self._save(job)
        return job

    def get(self, job_id: str) -> dict:
        try:
            return self.jobs[job_id]
        except KeyError as exc:
            raise BackupError("Auftrag nicht gefunden") from exc

    def list(self) -> list[dict]:
        return sorted(self.jobs.values(), key=lambda item: item["created_at"], reverse=True)

    def _track(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _quiet(self, app) -> None:
        self.maintenance = True
        try:
            try:
                await asyncio.wait_for(app.state.duplicate_scanner.stop(), timeout=120)
            except asyncio.TimeoutError as exc:
                raise BackupError("Duplikatsuche konnte nicht rechtzeitig angehalten werden") from exc
            for _ in range(1200):
                imports = app.state.import_manager
                translations = app.state.translation_manager
                previews = app.state.preview_manager
                if (self.active_writes == 0 and not imports.has_active_imports() and
                    not imports.tasks and not previews.tasks and not translations.running and
                    not translations.repairing):
                    return
                await asyncio.sleep(0.1)
            raise BackupError("Laufende Arbeiten konnten nicht innerhalb von zwei Minuten abgeschlossen werden")
        except Exception:
            self.maintenance = False
            raise

    def create(self, app) -> dict:
        if self.busy:
            raise BackupError("Ein Sicherungsauftrag läuft bereits")
        job = self._new("backup")
        self.busy = True
        self._track(self._run_backup(app, job))
        return job

    async def _run_backup(self, app, job: dict) -> None:
        directory = self.root / job["id"]
        package = self.root / f"{job['id']}.zip"
        try:
            job.update(status="running", phase="Wartet auf laufende Arbeiten")
            self._save(job)
            await self._quiet(app)
            source_bytes = sum(path.stat().st_size for directory_name in ("library", "staging", "metadata-recovery")
                               for path in (self.data_dir / directory_name).rglob("*") if path.is_file())
            source_bytes += sum((self.data_dir / name).stat().st_size for name in ("goblin.db", "auth.db"))
            if shutil.disk_usage(self.root).free < source_bytes * 2 + 64 * 1024 * 1024:
                raise BackupError("Nicht genug freier Speicher für Backup und ZIP-Datei")
            job["phase"] = "Archiv wird gesichert"
            self._save(job)
            manifest = await asyncio.to_thread(create_backup, self.data_dir, directory, locked=True)
            job["total_bytes"] = sum(item["size"] for item in manifest["files"].values())
            job["phase"] = "ZIP wird erstellt und geprüft"
            self._save(job)
            def progress(amount):
                job["processed_bytes"] += amount
            await asyncio.to_thread(pack_backup, directory, package, progress)
            job.update(status="ready", phase="Fertig", book_count=manifest["book_count"],
                       size=package.stat().st_size)
            self._save(job)
        except Exception as exc:
            job.update(status="failed", phase="Fehlgeschlagen", error=str(exc))
            self._save(job)
            package.unlink(missing_ok=True)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
            self.maintenance = False
            self.busy = False
            app.state.duplicate_scanner.resume()

    def prepared_restore(self, package: Path, max_bytes: int) -> dict:
        if self.busy:
            raise BackupError("Ein Sicherungsauftrag läuft bereits")
        job = self._new("restore")
        directory = self.root / job["id"]
        try:
            manifest = unpack_backup(package, directory, max_bytes=max_bytes)
            if shutil.disk_usage(self.data_dir).free < sum(item["size"] for item in manifest["files"].values()) + 64 * 1024 * 1024:
                raise BackupError("Nicht genug freier Speicher für die vorbereitete Wiederherstellung")
            with sqlite3.connect(self.data_dir / "goblin.db") as db:
                current_count = db.execute("SELECT count(*) FROM books").fetchone()[0]
            with sqlite3.connect(directory / "goblin.db") as db:
                incoming_count = db.execute("SELECT count(*) FROM books").fetchone()[0]
            job.update(status="ready", phase="Vorprüfung abgeschlossen",
                       created_backup_at=manifest["created_at"], format=manifest["format"],
                       book_count=manifest.get("book_count", incoming_count), current_book_count=current_count,
                       total_bytes=sum(item["size"] for item in manifest["files"].values()))
            self._save(job)
            return job
        except Exception:
            self.jobs.pop(job["id"], None)
            (self.root / f"{job['id']}.json").unlink(missing_ok=True)
            shutil.rmtree(directory, ignore_errors=True)
            raise
        finally:
            package.unlink(missing_ok=True)

    def restore(self, app, job_id: str) -> dict:
        job = self.get(job_id)
        if job["kind"] != "restore" or job["status"] != "ready":
            raise BackupError("Wiederherstellung ist nicht bereit")
        if self.busy:
            raise BackupError("Ein Sicherungsauftrag läuft bereits")
        self.busy = True
        self._track(self._run_restore(app, job))
        return job

    async def _run_restore(self, app, job: dict) -> None:
        from backend.database import engine, init_db, SessionLocal
        from backend.metadata_store import recover_metadata
        from backend.translation import TranslationManager
        from backend import auth
        from backend.repository import invalidate_filter_cache
        try:
            job.update(status="running", phase="Wartet auf laufende Arbeiten")
            self._save(job)
            await self._quiet(app)
            directory = self.root / job["id"]
            await asyncio.to_thread(verify_backup, directory)
            if shutil.disk_usage(self.data_dir).free < job["total_bytes"] + 64 * 1024 * 1024:
                raise BackupError("Nicht genug freier Speicher für die Wiederherstellung")
            job["phase"] = "Archiv wird ersetzt"
            self._save(job)
            self.switching = True
            engine.dispose()
            restored_translation = []
            def validate_restored():
                try:
                    init_db()
                    recover_metadata(app.state.import_manager.settings, SessionLocal)
                    auth.ensure_setup_code(self.data_dir)
                    restored_translation.append(TranslationManager(app.state.import_manager.settings,
                                                                   SessionLocal))
                except Exception:
                    engine.dispose()
                    raise
                engine.dispose()
            previous = await asyncio.to_thread(restore_in_place, directory, self.data_dir,
                                               validate=validate_restored)
            app.state.translation_manager = restored_translation[0]
            invalidate_filter_cache()
            app.state.import_manager.jobs.clear()
            job.update(status="done", phase="Fertig", previous=str(previous),
                       previous_bytes=sum(p.stat().st_size for p in previous.rglob("*") if p.is_file()))
            self._save(job)
            shutil.rmtree(directory, ignore_errors=True)
        except Exception as exc:
            job.update(status="failed", phase="Fehlgeschlagen", error=str(exc))
            self._save(job)
        finally:
            self.switching = False
            self.maintenance = False
            self.busy = False
            app.state.duplicate_scanner.resume()

    def delete(self, job_id: str) -> None:
        job = self.get(job_id)
        if job["status"] in {"waiting", "running"}:
            raise BackupError("Laufender Auftrag kann nicht gelöscht werden")
        (self.root / f"{job_id}.zip").unlink(missing_ok=True)
        shutil.rmtree(self.root / job_id, ignore_errors=True)
        (self.root / f"{job_id}.json").unlink(missing_ok=True)
        del self.jobs[job_id]

    def delete_previous(self, job_id: str) -> None:
        job = self.get(job_id)
        path = Path(job.get("previous", ""))
        if job["kind"] != "restore" or job["status"] != "done" or path.parent != self.data_dir or not path.name.startswith(".before-restore-"):
            raise BackupError("Rückfallkopie nicht gefunden")
        shutil.rmtree(path)
        job.pop("previous")
        job.pop("previous_bytes", None)
        self._save(job)
