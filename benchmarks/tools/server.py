"""Isolated backend entry point for the import benchmark.

Run as a separate process with GOBLIN_DATA_DIR and BENCH_COMPLETIONS set.
Product code is imported only after the environment has been set by the runner.
"""
from __future__ import annotations

import asyncio
import json
import os
import contextvars
import functools
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, select

from backend import main
from backend import duplicates, imports, previews
from backend.covers import CoverService
from backend.imports import ImportManager
from backend.isbn import IsbnResolver
from backend.models import ImportPreview
from backend.previews import PreviewManager
from backend.providers import ProviderChain

LOG = Path(os.environ["BENCH_COMPLETIONS"])
RUN_ID = os.environ["BENCH_RUN_ID"]
NO_EVENTS = os.environ.get("BENCH_NO_EVENTS") == "1"
LOG.parent.mkdir(parents=True, exist_ok=True)
log_file = LOG.open("a", encoding="utf-8", buffering=1)
phase_path = os.environ.get("BENCH_PHASES")
phase_file = Path(phase_path).open("a", encoding="utf-8", buffering=1) if phase_path else None
current_item = contextvars.ContextVar("benchmark_item", default=None)
current_span = contextvars.ContextVar("benchmark_parent_span", default=None)
phase_counts: dict[str, dict[str, int]] = {}
phase_lock = threading.Lock()


def emit(kind: str, **data) -> None:
    if NO_EVENTS:
        return
    log_file.write(json.dumps({"run_id": RUN_ID, "kind": kind, **data},
                              ensure_ascii=False, separators=(",", ":")) + "\n")
    log_file.flush()


def timed(name, function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        if phase_file is None:
            return function(*args, **kwargs)
        span_id = uuid.uuid4().hex[:12]
        parent = current_span.get()
        token = current_span.set(span_id)
        start = time.perf_counter_ns()
        error = None
        try:
            return function(*args, **kwargs)
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            end = time.perf_counter_ns()
            phase_file.write(json.dumps({"item_id": current_item.get(), "phase": name,
                                         "span_id": span_id, "parent_span_id": parent,
                                         "start_ns": start, "end_ns": end,
                                         "pid": os.getpid(), "thread_id": threading.get_ident(),
                                         "error": error}, separators=(",", ":")) + "\n")
            phase_file.flush()
            current_span.reset(token)
    return wrapped


def count_metric(name: str) -> None:
    identifier = current_item.get()
    if phase_file is None or identifier is None:
        return
    with phase_lock:
        counts = phase_counts.setdefault(identifier, {})
        counts[name] = counts.get(name, 0) + 1


def counted(name, function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        count_metric(name)
        return function(*args, **kwargs)
    return wrapped


def emit_phase_counts(identifier: str):
    with phase_lock:
        counts = phase_counts.pop(identifier, {})
    phase_file.write(json.dumps({"item_id": identifier, "phase": "counts", "counts": counts,
                                 "at_ns": time.perf_counter_ns(), "pid": os.getpid()},
                                separators=(",", ":")) + "\n")
    phase_file.flush()


if phase_file is not None:
    @event.listens_for(main.SessionLocal.kw["bind"], "before_cursor_execute")
    def count_query(_connection, _cursor, statement, _parameters, _context, _many):
        count_metric("sql_queries")
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            count_metric("sql_writes")

    imports.sha256_file = timed("sha256", imports.sha256_file)
    imports.detect_and_extract = timed("metadata_and_cover", imports.detect_and_extract)
    imports.fingerprint = timed("fingerprint", imports.fingerprint)
    imports.find_matches = timed("candidate_search_and_comparison", imports.find_matches)
    previews.sha256_file = timed("preview_sha256", previews.sha256_file)
    previews.detect_and_extract = timed("preview_metadata_and_cover", previews.detect_and_extract)
    previews.fingerprint = timed("preview_fingerprint", previews.fingerprint)
    previews.find_matches = timed("preview_candidate_search_and_comparison", previews.find_matches)
    duplicates.normalized_text = counted(
        "text_extractions", timed("text_extraction_and_normalization", duplicates.normalized_text))
    duplicates.shingles = timed("shingles", duplicates.shingles)
    duplicates.signature_for = timed("signature", duplicates.signature_for)
    duplicates.text_relation_paths = counted(
        "detail_text_comparisons", timed("text_relation", duplicates.text_relation_paths))
    imports.ImportManager._archive = timed("archive_database_fts", imports.ImportManager._archive)


# No external provider instance is created. The local embedded-cover path remains
# active. An ISBN resolver without providers exists for normal startup/close.
main.make_provider_chain = lambda *_: ProviderChain([])
main.make_cover_service = lambda *_: CoverService([])
main.make_isbn_resolver = lambda settings, factory, _timeout: IsbnResolver(settings, factory, [])

_create_job = ImportManager.create_job
_process_one = ImportManager._process_one
_create_preview = PreviewManager.create
_analyze_preview = PreviewManager.analyze


def create_job(self, uploads):
    job = _create_job(self, uploads)
    for item in job.items:
        emit("item_registered", job_id=job.id, item_id=item.id, filename=item.filename)
    return job


async def process_one(self, job, item, staging_path):
    token = current_item.set(item.id)
    try:
        return await _process_one(self, job, item, staging_path)
    finally:
        if phase_file is not None:
            emit_phase_counts(item.id)
        emit("item_complete", job_id=job.id, item_id=item.id, status=item.status,
             book_id=item.book_id, preview_id=item.preview_id, sha256=item.sha256,
             error=item.message if item.status == "failed" else None)
        current_item.reset(token)


def create_preview(self, uploads, *, already_accepted=False):
    result = _create_preview(self, uploads, already_accepted=already_accepted)
    for row in result:
        emit("preview_registered", preview_id=row["id"])
    return result


async def analyze_preview(self, preview_id, *, enrich=False, reserved=False):
    token = current_item.set(preview_id)
    error = None
    try:
        return await _analyze_preview(self, preview_id, enrich=enrich, reserved=reserved)
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if phase_file is not None:
            emit_phase_counts(preview_id)
        with self.session_factory() as db:
            status = db.scalar(select(ImportPreview.status).where(ImportPreview.id == preview_id))
        emit("preview_complete", preview_id=preview_id, status=status, error=error)
        current_item.reset(token)


ImportManager.create_job = create_job
ImportManager._process_one = process_one
PreviewManager.create = create_preview
PreviewManager.analyze = analyze_preview

_lifespan = main.app.router.lifespan_context


@asynccontextmanager
async def benchmark_lifespan(app):
    async with _lifespan(app):
        app.state.import_manager.postprocess_step = None
        emit("ready", external_providers=0, postprocess=False)
        yield


main.app.router.lifespan_context = benchmark_lifespan
app = main.app


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    arguments = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=arguments.port, workers=1, reload=False,
                log_level="warning")
