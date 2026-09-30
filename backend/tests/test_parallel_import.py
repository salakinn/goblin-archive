from __future__ import annotations

import asyncio
import shutil

import pytest

from backend.config import Settings
from backend.imports import ArchiveBusyError, EventBroker, ImportManager
from backend.providers import ProviderChain
from backend.previews import PreviewManager
from backend.repository import list_books
from backend.tests.test_imports import make_epub


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 3])
async def test_shared_import_limit_and_independent_jobs(db_context, tmp_path, concurrency):
    settings, factory = db_context
    settings = Settings(data_dir=settings.data_dir, import_concurrency=concurrency)
    manager = ImportManager(settings, factory, ProviderChain([]))
    active = peak = 0

    async def process(job, item, _path):
        nonlocal active, peak
        item.status = "running"
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.03)
        active -= 1
        item.status = "finished"
        await manager._emit(job, item, "import.finished")

    manager._process_one = process
    jobs = [manager.create_job([(f"same-name-{i}.epub", tmp_path / f"{n}-{i}")
                                for i in range(4)]) for n in range(2)]
    for _ in range(100):
        if all(job.status == "finished" for job in jobs):
            break
        await asyncio.sleep(0.02)
    await manager.stop()
    assert all(job.status == "finished" for job in jobs)
    assert peak == concurrency
    assert len({item.id for job in jobs for item in job.items}) == 8
    assert all(job.as_dict()["completed"] == 4 for job in jobs)


@pytest.mark.asyncio
async def test_parallel_identical_imports_keep_one_archive_record(db_context, tmp_path):
    settings, factory = db_context
    settings = Settings(data_dir=settings.data_dir, import_concurrency=2)
    manager = ImportManager(settings, factory, ProviderChain([]))
    first = tmp_path / "a.epub"
    second = tmp_path / "b.epub"
    make_epub(first)
    shutil.copy2(first, second)
    job = manager.create_job([("a.epub", first), ("b.epub", second)])
    for _ in range(100):
        if job.status == "finished":
            break
        await asyncio.sleep(0.02)
    await manager.stop()
    assert sorted(item.status for item in job.items) == ["duplicate", "finished"]
    assert job.items[0].book_id == job.items[1].book_id
    with factory() as db:
        assert len(list_books(db)) == 1
    assert len(list(settings.library_dir.rglob("metadata.json"))) == 1


@pytest.mark.asyncio
async def test_admission_limit_and_broken_file_isolation(db_context, tmp_path):
    settings, factory = db_context
    settings = Settings(data_dir=settings.data_dir, import_concurrency=2,
                        max_queued_import_files=2)
    manager = ImportManager(settings, factory, ProviderChain([]))
    good = tmp_path / "good.epub"
    broken = tmp_path / "broken.epub"
    make_epub(good)
    broken.write_bytes(b"not an epub")
    job = manager.create_job([("good.epub", good), ("broken.epub", broken)])
    with pytest.raises(ArchiveBusyError, match="warteschlange"):
        manager.create_job([("extra.epub", tmp_path / "extra.epub")])
    for _ in range(100):
        if job.status in {"finished", "failed"}:
            break
        await asyncio.sleep(0.02)
    await manager.stop()
    assert sorted(item.status for item in job.items) == ["failed", "finished"]
    with factory() as db:
        assert len(list_books(db)) == 1


@pytest.mark.asyncio
async def test_postprocessing_retry_only_failed_step(db_context):
    settings, factory = db_context
    manager = ImportManager(settings, factory, ProviderChain([]))
    calls = []

    async def step(_book_id, name):
        calls.append(name)
        if name == "tags" and calls.count("tags") == 1:
            raise RuntimeError("Dienst vorübergehend nicht erreichbar")
        return name

    manager.postprocess_step = step
    manager.start_postprocessing("bk_test", "test.epub")
    job = next(iter(manager.jobs.values()))
    for _ in range(100):
        if job.status == "finished":
            break
        await asyncio.sleep(0.01)
    assert job.items[0].failed_steps == ["tags"]
    assert job.items[0].status == "finished"
    manager.retry_postprocessing(job.id, job.items[0].id)
    for _ in range(100):
        if job.status == "finished":
            break
        await asyncio.sleep(0.01)
    await manager.stop()
    assert calls == ["isbn", "authors", "language", "tags", "tags"]
    assert job.items[0].failed_steps == []
    assert job.items[0].warnings == []


@pytest.mark.asyncio
async def test_events_reach_two_tabs():
    broker = EventBroker()
    first, second = broker.stream(), broker.stream()
    left, right = asyncio.create_task(anext(first)), asyncio.create_task(anext(second))
    await asyncio.sleep(0)
    await broker.publish("import.finished", {"import_id": "imp_one"})
    assert "event: import.finished" in await left
    assert "event: import.finished" in await right
    await first.aclose()
    await second.aclose()


@pytest.mark.asyncio
async def test_shutdown_finishes_accepted_postprocessing(db_context):
    settings, factory = db_context
    manager = ImportManager(settings, factory, ProviderChain([]))
    async def step(_book_id, name):
        await asyncio.sleep(0.01)
        return name
    manager.postprocess_step = step
    manager.start_postprocessing("bk_test", "test.epub")
    job = next(iter(manager.jobs.values()))
    await manager.stop()
    assert job.status == "finished"
    assert job.items[0].results == ["isbn", "authors", "language", "tags"]


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["keep_both", "use_existing"])
async def test_reviewed_direct_import_finishes_same_job(db_context, tmp_path, decision):
    settings, factory = db_context
    manager = ImportManager(settings, factory, ProviderChain([]))
    first = tmp_path / "first.epub"
    second = tmp_path / "second.epub"
    make_epub(first)
    item = manager.create_job([("first.epub", first)])
    for _ in range(100):
        if item.status == "finished":
            break
        await asyncio.sleep(0.01)
    shutil.copy2(next(settings.library_dir.rglob("*.epub")), second)
    with second.open("ab") as stream:
        stream.write(b"repacked")
    previews = PreviewManager(manager)
    manager.preview_manager = previews
    async def step(_book_id, name):
        return name
    manager.postprocess_step = step
    job = manager.create_job([("second.epub", second)])
    for _ in range(100):
        if job.status == "needs_review" and job.items[0].preview_id:
            break
        await asyncio.sleep(0.01)
    assert job.items[0].preview_id
    for _ in range(100):
        preview = previews.get(job.items[0].preview_id)
        if preview["status"] == "ready":
            break
        await asyncio.sleep(0.01)
    book_id = preview["duplicate_matches"][0]["book_id"] if decision == "use_existing" else None
    previews.decide(preview["id"], preview["revision"], decision, book_id)
    if decision == "keep_both":
        await previews.confirm(preview["id"])
    for _ in range(100):
        if job.status == "finished":
            break
        await asyncio.sleep(0.01)
    await manager.stop()
    assert job.status == "finished"
    assert job.items[0].status == ("finished" if decision == "keep_both" else "duplicate")
    assert len(manager.jobs) == 2
    with factory() as db:
        assert len(list_books(db)) == (2 if decision == "keep_both" else 1)


def test_staging_byte_limit_rejects_upload_and_cleans_partial_file(db_context, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main
    settings, factory = db_context
    source = tmp_path / "large.epub"
    make_epub(source)
    settings = Settings(data_dir=settings.data_dir, max_staging_bytes=source.stat().st_size - 1)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "make_provider_chain", lambda *_: ProviderChain([]))
    with TestClient(main.app) as client:
        response = client.post("/api/import", files={"files": ("large.epub", source.read_bytes(),
                                                     "application/epub+zip")})
        assert response.status_code == 507
        assert response.json()["detail"]["code"] == "staging_full"
    assert list(settings.staging_dir.iterdir()) == []
