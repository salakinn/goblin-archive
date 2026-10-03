"""Run the frozen HTTP import workload against isolated backend processes."""
from __future__ import annotations

import json
import os
import secrets
import signal
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from contextlib import suppress

import httpx

from benchmarks.tools.bundle import CHUNK, ROOT, db_check, digest, read_json, verify, write_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jsonl(path: Path, data: dict) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def warm_files(bundle: Path, checked: dict) -> dict:
    start = time.perf_counter_ns()
    total = 0
    for folder, entries in (("snapshot", checked["snapshot"]), ("inputs", checked["inputs"])):
        for entry in entries:
            with (bundle / folder / entry["path"]).open("rb") as file:
                for block in iter(lambda: file.read(CHUNK), b""):
                    total += len(block)
    return {"bytes": total, "seconds": (time.perf_counter_ns() - start) / 1e9}


def _proc(pid: int) -> dict | None:
    root = Path(f"/proc/{pid}")
    try:
        stat = (root / "stat").read_text().rsplit(") ", 1)[1].split()
        status = (root / "status").read_text()
        fields = {}
        for line in status.splitlines():
            if line.startswith(("VmRSS:", "PPid:")):
                key, value = line.split(":", 1)
                fields[key] = int(value.split()[0])
        io = {}
        try:
            for line in (root / "io").read_text().splitlines():
                key, value = line.split(": ", 1)
                if key in {"read_bytes", "write_bytes"}:
                    io[key] = int(value)
        except (FileNotFoundError, PermissionError):
            pass
        return {"pid": pid, "ppid": fields.get("PPid"), "start_ticks": int(stat[19]),
                "rss_bytes": fields.get("VmRSS", 0) * 1024,
                "cpu_ticks": int(stat[11]) + int(stat[12]), **io}
    except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError):
        return None


def _descendants(root_pid: int) -> list[int]:
    info = {}
    for path in Path("/proc").iterdir():
        if path.name.isdecimal():
            item = _proc(int(path.name))
            if item:
                info[item["pid"]] = item["ppid"]
    result = {root_pid}
    while True:
        newer = result | {pid for pid, parent in info.items() if parent in result}
        if newer == result:
            return sorted(result)
        result = newer


class ResourceMonitor:
    def __init__(self, pid: int, path: Path):
        self.pid, self.path = pid, path
        self.stop_flag = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.backend_peak = 0
        self.samples = 0
        self.pss_samples = 0
        self.started = False

    def start(self):
        self.thread.start()
        self.started = True

    def stop(self):
        self.stop_flag.set()
        if self.started:
            self.thread.join(timeout=2)

    def _loop(self):
        counter = 0
        with self.path.open("w", encoding="utf-8") as file:
            while not self.stop_flag.is_set():
                now = time.perf_counter_ns()
                processes = []
                for pid in _descendants(self.pid):
                    item = _proc(pid)
                    if item:
                        if counter % 10 == 0:
                            try:
                                pss = next(line for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines()
                                           if line.startswith("Pss:"))
                                item["pss_bytes"] = int(pss.split()[1]) * 1024
                                self.pss_samples += 1
                            except (FileNotFoundError, PermissionError, StopIteration):
                                pass
                        processes.append(item)
                total = sum(item["rss_bytes"] for item in processes)
                self.backend_peak = max(self.backend_peak, total)
                self.samples += 1
                file.write(json.dumps({"at_ns": now, "backend_rss_bytes": total,
                                       "client": _proc(os.getpid()),
                                       "processes": processes}, separators=(",", ":")) + "\n")
                file.flush()
                counter += 1
                self.stop_flag.wait(0.1)


class ReadLoad:
    """Fixed one-request-per-second-per-endpoint load on a second client."""

    def __init__(self, base_url: str, cookies: httpx.Cookies, output: Path):
        self.client = httpx.Client(base_url=base_url, cookies=cookies, timeout=10, trust_env=False)
        self.output = output
        self.stop_flag = threading.Event()
        self.thread: threading.Thread | None = None
        self.executor = ThreadPoolExecutor(max_workers=20)
        self.futures = set()
        self.records: list[dict] = []
        self.lock = threading.Lock()

    def _request(self, endpoint: str, planned_ns: int):
        start = time.perf_counter_ns()
        try:
            response = self.client.get(endpoint)
            record = {"endpoint": endpoint, "planned_ns": planned_ns,
                      "started_ns": start, "ended_ns": time.perf_counter_ns(),
                      "status": response.status_code, "error": None}
        except Exception as exc:
            record = {"endpoint": endpoint, "planned_ns": planned_ns,
                      "started_ns": start, "ended_ns": time.perf_counter_ns(),
                      "status": None, "error": f"{type(exc).__name__}: {exc}"}
        with self.lock:
            self.records.append(record)

    def start(self, start_ns: int):
        def schedule():
            index = 0
            while not self.stop_flag.is_set():
                planned = start_ns + (index // 2) * 1_000_000_000 + (index % 2) * 500_000_000
                delay = (planned - time.perf_counter_ns()) / 1e9
                if delay > 0 and self.stop_flag.wait(delay):
                    break
                endpoint = "/api/books?limit=50&offset=0" if index % 2 == 0 else "/api/search?q=ABAP"
                self.futures = {future for future in self.futures if not future.done()}
                if len(self.futures) >= 20:
                    with self.lock:
                        self.records.append({"endpoint": endpoint, "planned_ns": planned,
                                             "started_ns": None, "ended_ns": None,
                                             "status": None, "error": "capacity_exceeded"})
                else:
                    self.futures.add(self.executor.submit(self._request, endpoint, planned))
                index += 1
        self.thread = threading.Thread(target=schedule, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.executor.shutdown(wait=True)
        self.client.close()
        with self.output.open("w", encoding="utf-8") as file:
            for record in sorted(self.records, key=lambda entry: entry["planned_ns"]):
                file.write(json.dumps(record, separators=(",", ":")) + "\n")


def _stream_multipart(files: list[tuple[str, Path]], boundary: str):
    for _input_id, path in files:
        name = path.name
        if any(c in name for c in '\r\n"'):
            raise ValueError(f"Unsicherer Multipart-Dateiname: {name!r}")
        yield (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
               f"filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        with path.open("rb") as file:
            for block in iter(lambda: file.read(CHUNK), b""):
                yield block
        yield b"\r\n"
    yield f"--{boundary}--\r\n".encode()


def _ready(client: httpx.Client, process: subprocess.Popen, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Backend beendet mit Code {process.returncode}")
        try:
            if client.get("/api/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise TimeoutError("Backend nicht rechtzeitig bereit")


def _authenticate(client: httpx.Client, data_dir: Path) -> str:
    password = secrets.token_urlsafe(32)
    code = (data_dir / "auth-setup-code").read_text().strip()
    response = client.post("/api/auth/setup", json={"code": code, "password": password})
    response.raise_for_status()
    return response.json()["csrf_token"]


def _completion(path: Path, expected_ids: set[str], deadline: float) -> dict:
    items, previews, registered = {}, {}, set()
    offset = 0
    intervals = []
    while time.monotonic() < deadline:
        began = time.perf_counter_ns()
        if path.exists():
            with path.open(encoding="utf-8") as file:
                file.seek(offset)
                for line in file:
                    event = json.loads(line)
                    kind = event["kind"]
                    if kind == "item_complete":
                        items[event["item_id"]] = event
                    elif kind == "preview_registered":
                        registered.add(event["preview_id"])
                    elif kind == "preview_complete":
                        previews[event["preview_id"]] = event
                offset = file.tell()
        if expected_ids <= items.keys():
            needed = {item["preview_id"] for item in items.values() if item["preview_id"]}
            if needed <= previews.keys() and needed <= registered:
                return {"items": items, "previews": previews,
                        "observation_intervals_ms": intervals,
                        "end_ns": time.perf_counter_ns()}
        time.sleep(0.01)
        intervals.append((time.perf_counter_ns() - began) / 1e6)
    raise TimeoutError(f"Abschlussnachweis fehlt: items={len(items)}/{len(expected_ids)}, previews={len(previews)}")


def _completion_poll(client: httpx.Client, jobs: list[str], data_dir: Path,
                     expected_ids: set[str], deadline: float) -> dict:
    """Coarse calibration control. It is never used for official timings."""
    while time.monotonic() < deadline:
        items = {}
        for job_id in jobs:
            response = client.get(f"/api/imports/{job_id}", timeout=10)
            response.raise_for_status()
            for item in response.json()["items"]:
                items[item["id"]] = {"item_id": item["id"], "status": item["status"],
                                      "preview_id": item.get("preview_id"),
                                      "book_id": item.get("book_id"), "sha256": item.get("sha256")}
        if expected_ids <= items.keys() and all(items[item_id]["status"] in
           {"finished", "duplicate", "needs_review", "failed"} for item_id in expected_ids):
            needed = {items[item_id]["preview_id"] for item_id in expected_ids
                      if items[item_id]["preview_id"]}
            previews = {}
            if needed:
                with sqlite3.connect(f"file:{data_dir / 'goblin.db'}?mode=ro", uri=True) as db:
                    for preview_id in needed:
                        row = db.execute("SELECT status,error FROM import_previews WHERE id=?",
                                         (preview_id,)).fetchone()
                        if row:
                            previews[preview_id] = {"preview_id": preview_id,
                                                    "status": row[0], "error": row[1]}
            if needed <= previews.keys() and all(value["status"] in {"ready", "failed"}
                                                  for value in previews.values()):
                return {"items": items, "previews": previews, "observation_intervals_ms": [100],
                        "end_ns": time.perf_counter_ns()}
            if not needed:
                return {"items": items, "previews": {}, "observation_intervals_ms": [100],
                        "end_ns": time.perf_counter_ns()}
        time.sleep(0.1)
    raise TimeoutError("Grobe Abschlusskontrolle erreichte den Endzustand nicht")


def _results(data_dir: Path, mapped: dict[str, dict]) -> dict:
    result = {}
    with sqlite3.connect(data_dir / "goblin.db") as db:
        db.row_factory = sqlite3.Row
        for input_id, event in mapped.items():
            status = event["status"]
            value = {"status": status, "sha256": event["sha256"]}
            if status == "finished":
                row = db.execute("SELECT * FROM books WHERE id=?", (event["book_id"],)).fetchone()
                if row is None:
                    raise ValueError(f"Archiviertes Buch fehlt: {input_id}")
                cover = data_dir / "library" / row["cover_path"] if row["cover_path"] else None
                fp = db.execute("SELECT version,text_hash,word_count,status,signature_json "
                                "FROM book_fingerprints WHERE book_id=?", (row["id"],)).fetchone()
                metadata = json.loads(row["metadata_json"])
                metadata.pop("id", None)
                metadata.get("import", {}).pop("imported_at", None)
                metadata.get("file", {}).pop("library_path", None)
                for record in metadata.get("title_cleanup_history", []):
                    record.pop("created_at", None)
                value.update({"title": row["title"], "format": row["format"],
                              "metadata": metadata,
                              "archived_file_sha256": digest(data_dir / "library" / row["library_path"]),
                              "cover_sha256": digest(cover) if cover and cover.is_file() else None,
                              "fingerprint": dict(fp) if fp else None})
            elif status == "needs_review":
                row = db.execute("SELECT * FROM import_previews WHERE id=?", (event["preview_id"],)).fetchone()
                if row is None:
                    raise ValueError(f"Vorschau fehlt: {input_id}")
                cover = Path(row["cover_path"]) if row["cover_path"] else None
                metadata = json.loads(row["metadata_json"] or "{}")
                for record in metadata.get("_title_cleanup_history", []):
                    record.pop("created_at", None)
                value.update({"preview_status": row["status"],
                              "metadata": metadata,
                              "fingerprint": json.loads(row["fingerprint_json"] or "{}"),
                              "cover_sha256": digest(cover) if cover and cover.is_file() else None})
            elif status == "duplicate":
                row = db.execute("SELECT sha256 FROM books WHERE id=?", (event["book_id"],)).fetchone()
                value["duplicate_sha256"] = row["sha256"] if row else None
            result[input_id] = value
    return result


def run_one(bundle: Path, output: Path, *, revision_dir: Path = ROOT,
            expectations: dict | None = None, pause: float | None = None,
            phases: bool = False, read_load: bool = False,
            observation: str = "events", resource_monitor: bool = True) -> dict:
    bundle, output, revision_dir = bundle.resolve(), output.resolve(), revision_dir.resolve()
    checked = verify(bundle, require_expectations=False)
    protocol, dataset = checked["protocol"], checked["dataset"]
    scratch_root = Path(protocol.get("scratch_dir", ROOT / "benchmarks/.local/run-archives")).resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)
    data_dir = scratch_root / f"{output.parent.parent.name}-{output.name}"
    if data_dir.exists():
        raise FileExistsError(f"Laufarchiv existiert bereits: {data_dir}")
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    run_id = output.name
    started = utc_now()
    run = {"run_id": run_id, "started_at_utc": started, "status": "running",
           "dataset_id": dataset["dataset_id"], "revision_dir": str(revision_dir)}
    write_json(output / "run.json", run)
    run["scratch_dir"] = str(data_dir)
    try:
        shutil.copytree(bundle / "snapshot", data_dir)
        verify_files_for_run(data_dir, checked["snapshot"])
        run["cache_preparation"] = warm_files(bundle, checked)
    except BaseException as exc:
        run.update(status="invalid", reason=str(exc), finished_at_utc=utc_now())
        write_json(output / "run.json", run)
        if data_dir.exists():
            shutil.rmtree(data_dir)
        raise
    log_file = (output / "backend.log").open("w", encoding="utf-8")
    port = free_port()
    env = os.environ.copy()
    env.update({"GOBLIN_DATA_DIR": str(data_dir), "GOBLIN_IMPORT_CONCURRENCY": str(protocol["import_concurrency"]),
                "GOBLIN_PROVIDER_CONCURRENCY": str(protocol["provider_concurrency"]),
                "GOBLIN_MAX_UPLOAD_BYTES": str(protocol["max_upload_bytes"]),
                "GOBLIN_MAX_UPLOAD_FILES": str(protocol["max_upload_files"]),
                "GOBLIN_MAX_QUEUED_IMPORT_FILES": str(protocol["max_queued_import_files"]),
                "GOBLIN_MAX_STAGING_BYTES": str(protocol["max_staging_bytes"]),
                "BENCH_COMPLETIONS": str(output / "completions.jsonl"), "BENCH_RUN_ID": run_id,
                "PYTHONPATH": os.pathsep.join([str(revision_dir), str(ROOT)])})
    if phases:
        env["BENCH_PHASES"] = str(output / "phases.jsonl")
    if observation == "poll":
        env["BENCH_NO_EVENTS"] = "1"
    elif observation != "events":
        raise ValueError(f"Unbekannte Abschlussbeobachtung: {observation}")
    try:
        process = subprocess.Popen([sys.executable, str(ROOT / "benchmarks/tools/server.py"),
                                    "--port", str(port)], cwd=revision_dir, env=env,
                                   stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
    except BaseException as exc:
        log_file.close()
        shutil.rmtree(data_dir)
        run.update(status="aborted", reason=f"Backendstart fehlgeschlagen: {exc}",
                   finished_at_utc=utc_now())
        write_json(output / "run.json", run)
        return run
    monitor = ResourceMonitor(process.pid, output / "resources.jsonl")
    load = None
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=300,
                          trust_env=False) as client:
            _ready(client, process, protocol["ready_timeout_seconds"])
            csrf = _authenticate(client, data_dir)
            limits = client.get("/api/import/limits").json()
            if limits != {"max_files": protocol["max_upload_files"],
                          "max_bytes": protocol["max_upload_bytes"]}:
                raise ValueError(f"Uploadlimits weichen ab: {limits}")
            if pause is None:
                pause = protocol["pre_start_pause_seconds"]
            if resource_monitor:
                monitor.start()
            time.sleep(pause)
            id_to_entry = {entry["input_id"]: entry for entry in checked["inputs"]}
            mapped, ids, jobs = {}, set(), []
            first = last = None
            wave_files = protocol.get("batch_wave_files")
            wave_count = 0
            for batch_index, batch in enumerate(dataset["batches"]):
                paths = [(item_id, bundle / "inputs" / id_to_entry[item_id]["path"])
                         for item_id in batch]
                boundary = f"goblin-benchmark-{secrets.token_hex(12)}"
                began = time.perf_counter_ns()
                if first is None:
                    first = began
                    if read_load:
                        load = ReadLoad(f"http://127.0.0.1:{port}", client.cookies,
                                        output / "requests.jsonl")
                        load.start(first)
                response = client.post("/api/import", content=_stream_multipart(paths, boundary),
                                       headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                                                "X-CSRF-Token": csrf},
                                       timeout=protocol["upload_timeout_seconds"])
                ended = time.perf_counter_ns()
                jsonl(output / "uploads.jsonl", {"batch": batch, "start_ns": began,
                                                    "end_ns": ended, "status": response.status_code,
                                                    "bytes": sum(id_to_entry[i]["bytes"] for i in batch)})
                if response.status_code != 202:
                    raise RuntimeError(f"Upload HTTP {response.status_code}: {response.text[:500]}")
                job = response.json()
                jobs.append(job["id"])
                if len(job["items"]) != len(batch):
                    raise ValueError("Itemzahl in Uploadantwort stimmt nicht")
                for input_id, item in zip(batch, job["items"], strict=True):
                    mapped[input_id] = item["id"]
                    ids.add(item["id"])
                write_json(output / "item-map.json", mapped)
                last = ended
                wave_count += len(batch)
                if (wave_files and wave_count >= wave_files and
                        batch_index + 1 < len(dataset["batches"])):
                    deadline = time.monotonic() + protocol["run_timeout_seconds"] - (last - first) / 1e9
                    if observation == "events":
                        _completion(output / "completions.jsonl", ids, deadline)
                    else:
                        _completion_poll(client, jobs, data_dir, ids, deadline)
                    wave_count = 0
            if first is None or last is None:
                raise ValueError("Leere Batchliste")
            deadline = time.monotonic() + protocol["run_timeout_seconds"] - (last - first) / 1e9
            completion = (_completion(output / "completions.jsonl", ids, deadline)
                          if observation == "events" else
                          _completion_poll(client, jobs, data_dir, ids, deadline))
            if load:
                load.stop()
                load = None
            by_input = {key: completion["items"][value] for key, value in mapped.items()}
            results = _results(data_dir, by_input)
            run.update(import_total_seconds=(completion["end_ns"] - first) / 1e9,
                       upload_seconds=(last - first) / 1e9,
                       tail_seconds=(completion["end_ns"] - last) / 1e9,
                       input_count=len(mapped), input_bytes=dataset["input_bytes"],
                       files_per_minute=60 * len(mapped) / ((completion["end_ns"] - first) / 1e9),
                       observation_intervals_ms=completion["observation_intervals_ms"])
            run["read_load"] = read_load
            run["phases"] = phases
            run["observation"] = observation
            run["resource_monitor"] = resource_monitor
            validation = validate_result(data_dir, dataset, results, by_input,
                                         completion["previews"], expectations)
            write_json(output / "validation.json", validation)
            write_json(output / "items.json", results)
            run["status"] = "completed" if validation["passed"] else "invalid"
            if not validation["passed"]:
                run["reason"] = "; ".join(validation["errors"][:4])
            time.sleep(protocol["post_memory_seconds"])
    except BaseException as exc:
        run.update(status="aborted", reason=f"{type(exc).__name__}: {exc}")
        write_json(output / "validation.json", {"passed": False, "errors": [run["reason"]]})
    finally:
        if load:
            load.stop()
        monitor.stop()
        run["backend_peak_rss_bytes"] = monitor.backend_peak
        run["resource_samples"] = monitor.samples
        run["pss_samples"] = monitor.pss_samples
        run["finished_at_utc"] = utc_now()
        write_json(output / "run.json", run)
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        log_file.close()
        shutil.rmtree(data_dir)
    return run


def verify_files_for_run(data_dir: Path, entries: list[dict]):
    from benchmarks.tools.bundle import verify_files
    verify_files(data_dir, entries)


def validate_result(data_dir: Path, dataset: dict, results: dict, events: dict,
                    previews: dict, expectations: dict | None) -> dict:
    errors = []
    expected_failed = set(dataset.get("expected_failed_input_ids", []))
    if len(results) != dataset["input_count"]:
        errors.append("Nicht alle Eingaben haben ein Ergebnis")
    for input_id, result in results.items():
        if result["status"] == "failed" and input_id in expected_failed:
            if not events[input_id].get("error"):
                errors.append(f"{input_id}: erwarteter Fehler ohne Fehlermeldung")
        elif result["status"] not in {"finished", "duplicate", "needs_review"}:
            errors.append(f"{input_id}: Status {result['status']}")
        elif input_id in expected_failed:
            errors.append(f"{input_id}: erwarteter Importfehler blieb aus")
        if result["status"] == "needs_review":
            preview = previews.get(events[input_id]["preview_id"])
            if not preview or preview["status"] != "ready" or preview["error"]:
                errors.append(f"{input_id}: Vorschau nicht vollständig bereit")
        if result["status"] == "finished" and result.get("archived_file_sha256") != result["sha256"]:
            errors.append(f"{input_id}: Archivdatei weicht vom Eingabehash ab")
    with sqlite3.connect(data_dir / "goblin.db") as db:
        final_count = db.execute("SELECT count(*) FROM books").fetchone()[0]
    expected_count = dataset["expected_books"] + sum(v["status"] == "finished" for v in results.values())
    if final_count != expected_count:
        errors.append(f"Buchzahl {final_count}, erwartet {expected_count}")
    try:
        db_check(data_dir / "goblin.db", data_dir / "library", final_count)
    except ValueError as exc:
        errors.append(str(exc))
    if expectations is not None and results != expectations["items"]:
        errors.append("Fachliche Ergebnisse weichen von der eingefrorenen Referenz ab")
    return {"passed": not errors, "errors": errors, "books_after": final_count,
            "result_counts": {kind: sum(item["status"] == kind for item in results.values())
                              for kind in ("finished", "duplicate", "needs_review", "failed")}}
