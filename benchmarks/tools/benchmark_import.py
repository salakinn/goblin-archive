#!/usr/bin/env python3
"""CLI for the versioned Goblin import benchmark protocols."""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from benchmarks.tools.analysis import environment, report  # noqa: E402
from benchmarks.tools.bundle import (data_hash, digest, prepare, read_json, verify,  # noqa: E402
                                     write_json, seal)
from benchmarks.tools.engine import run_one  # noqa: E402


def revision(ref: str) -> str:
    return subprocess.run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
                          cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()


@contextmanager
def checkout(ref: str):
    target = ROOT / "benchmarks/.local/checkouts" / f"{ref[:12]}-{time.time_ns()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "worktree", "add", "--detach", str(target), ref],
                   cwd=ROOT, check=True, capture_output=True, text=True)
    try:
        yield target
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(target)],
                       cwd=ROOT, check=True, capture_output=True, text=True)


def runner_hash() -> str:
    files = [ROOT / "benchmarks/tools" / name for name in
             ("bundle.py", "engine.py", "server.py", "analysis.py", "benchmark_import.py")]
    return data_hash({path.name: digest(path) for path in files})


def series_start(output: Path, bundle: Path, mode: str, rev_a: str,
                 rev_b: str | None = None, required_runs: int = 10) -> dict:
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    try:
        checked = verify(bundle, require_expectations=True)
        protocol = checked["protocol"]
        storage = Path(protocol.get("scratch_dir", ROOT / "benchmarks/.local/run-archives"))
        storage.mkdir(parents=True, exist_ok=True)
        env = environment(storage)
        protocol_hash = digest(bundle / "protocol.json")
        identity = data_hash({"dataset_id": checked["dataset"]["dataset_id"],
                              "protocol_sha256": protocol_hash, "runner_sha256": runner_hash(),
                              "expected_results_sha256": digest(bundle / "expected-results.json"),
                              "environment": env})
        metadata = {"mode": mode, "started_at_utc": datetime.now(timezone.utc).isoformat(),
                    "protocol_id": protocol["protocol_id"], "protocol_sha256": protocol_hash,
                    "dataset_id": checked["dataset"]["dataset_id"], "runner_sha256": runner_hash(),
                    "environment_fingerprint": data_hash(env), "comparison_group": identity,
                    "revision_a": rev_a, "revision_b": rev_b, "required_runs": required_runs}
        official = checked["dataset"]["protocol_id"] == "import-10k-v1"
        if mode == "baseline":
            planned = ([f"cal_{i:02d}_{mode_name}"
                        for i in range(1, 4)
                        for mode_name in (("events", "poll") if i % 2 else ("poll", "events"))]
                       if official else [])
            planned += ["warmup_ref"] + [f"{i:02d}_ref" for i in range(1, required_runs + 1)]
            if official:
                planned += [f"diag_{i:02d}_ref" for i in range(1, 4)]
                planned += [f"load_{i:02d}_ref" for i in range(1, 4)]
        else:
            planned = ["warmup_a", "warmup_b"]
            planned += [f"{i:02d}_{letter}" for i in range(1, required_runs + 1)
                        for letter in (("a", "b") if i % 2 else ("b", "a"))]
            if official:
                planned += [f"diag_{i:02d}_{letter}" for letter in ("a", "b") for i in range(1, 4)]
                planned += [f"load_{i:02d}_{letter}" for letter in ("a", "b") for i in range(1, 4)]
        metadata["planned_run_order"] = planned
        history = []
        for candidate in (ROOT / "benchmarks/results").glob("*/summary.json"):
            try:
                older = read_json(candidate)
            except (OSError, ValueError):
                continue
            if (older.get("comparison_group") == identity and older.get("status") == "completed"
                    and older.get("run_id") != output.name):
                history.append(older)
        history.sort(key=lambda value: value["started_at_utc"])
        references = [value for value in history if value["mode"] == "baseline"]
        fixed = references[0] if references else None
        recent = history[-1] if history else None
        metadata["reference_run_id"] = fixed["run_id"] if fixed else None
        metadata["previous_compatible_run_id"] = recent["run_id"] if recent else None
        metadata["reference_median_seconds"] = (fixed["metrics_a"]["import_total_seconds_median"]
                                                if fixed else None)
        metadata["previous_median_seconds"] = ((recent["metrics_b"] or recent["metrics_a"])
                                               ["import_total_seconds_median"] if recent else None)
        write_json(output / "series.json", metadata)
        write_json(output / "environment.json", env)
        shutil.copy2(bundle / "protocol.json", output / "protocol.json")
        shutil.copy2(bundle / "dataset.json", output / "dataset.json")
        (output / "runs").mkdir()
        return metadata
    except BaseException:
        shutil.rmtree(output)
        raise


def _update_history(output: Path, summary: dict):
    if output.parent.resolve() != (ROOT / "benchmarks/results").resolve():
        return
    history = ROOT / "benchmarks/results/HISTORY.md"
    text = history.read_text(encoding="utf-8")
    role = "Referenz" if summary["mode"] == "baseline" else "A/B-Vergleich"
    if summary.get("reference_run_id"):
        role += f" / [{summary['reference_run_id']}]({summary['reference_run_id']}/result.md)"
    metric = summary["metrics_b"] or summary["metrics_a"]
    median = metric["import_total_seconds_median"]
    peak = metric["backend_peak_rss_bytes_max"]
    saved = summary["comparison"]["paired_time_saved_percent_median"]
    row = (f"| [{output.name}]({output.name}/result.md) | {summary['status']} | "
           f"`{summary['comparison_group'][:12]}` | {role} | "
           f"`{(summary['revision_b'] or summary['revision_a'])[:12]}` | "
           f"{summary['metrics_a']['valid_runs']}/{summary['metrics_b']['valid_runs'] if summary['metrics_b'] else '—'} | "
           f"{round(median, 3) if median is not None else '—'} | "
           f"{round(saved, 2) if saved is not None else '—'} % | "
           f"{round(peak / 1048576, 1) if peak is not None else '—'} |")
    marker = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    if marker not in text:
        raise RuntimeError("Historientabelle nicht gefunden")
    text = text.replace(marker, marker + "\n" + row, 1)
    if summary["mode"] == "baseline" and summary["status"] == "completed":
        text = text.replace("**Aktueller Stand:** Noch keine Messreihe nach `import-10k-v1` durchgeführt.\n"
                            "Noch keine eingefrorene 10k-Referenz vorhanden.",
                            f"**Aktueller Stand:** Referenz für `{summary['comparison_group'][:12]}`: "
                            f"[{output.name}]({output.name}/result.md).")
    history.write_text(text, encoding="utf-8")


def _pilot(bundle: Path, revision_dir: Path, reference_revision: str):
    pilot_dir = bundle / ".pilots"
    pilot_dir.mkdir()
    runs = []
    for i in range(2):
        result = run_one(bundle, pilot_dir / f"pilot_{i+1}", revision_dir=revision_dir, pause=0)
        if result["status"] != "completed":
            raise RuntimeError(f"Pilot {i+1} ungültig: {result.get('reason')}")
        runs.append(read_json(pilot_dir / f"pilot_{i+1}" / "items.json"))
    if runs[0] != runs[1]:
        raise RuntimeError("Pilot-Ergebnisse unterscheiden sich; Referenz nicht eingefroren")
    write_json(bundle / "expected-results.json", {"status": "frozen", "items": runs[0],
                                                  "pilots": 2, "revision": reference_revision})
    seal(bundle)


def _calibrate(bundle: Path, output: Path, tree: Path, expected: dict) -> dict:
    pairs = []
    for i in range(1, 4):
        order = ("events", "poll") if i % 2 else ("poll", "events")
        results = {}
        for mode in order:
            result = run_one(bundle, output / "runs" / f"cal_{i:02d}_{mode}",
                             revision_dir=tree, expectations=expected,
                             observation=mode, resource_monitor=(mode == "events"))
            if result["status"] != "completed":
                raise RuntimeError(f"Kalibrierlauf {i}/{mode} ungültig: {result.get('reason')}")
            results[mode] = result
        pairs.append({"pair": i, "events_seconds": results["events"]["import_total_seconds"],
                      "poll_seconds": results["poll"]["import_total_seconds"],
                      "relative_difference_percent": 100 * (results["events"]["import_total_seconds"] /
                                                        results["poll"]["import_total_seconds"] - 1)})
    median = statistics.median(pair["relative_difference_percent"] for pair in pairs)
    result = {"method": "events_and_100ms_resource_monitor_vs_100ms_HTTP_status_poll_without_monitor",
              "pairs": pairs, "median_relative_difference_percent": median,
              "passed": abs(median) <= 1,
              "limitations": "Die grobe Kontrolle erzeugt selbst HTTP-/SQLite-Last; dies ist eine Plausibilitätsprüfung, keine exakte Messung des Instrumentierungsaufwands."}
    write_json(output / "calibration.json", result)
    return result


def baseline(args):
    bundle, output = args.bundle.resolve(), args.output.resolve()
    checked = verify(bundle, require_expectations=True)
    target = revision(args.revision)
    if target != read_json(bundle / "expected-results.json")["revision"]:
        raise ValueError("Referenzrevision weicht von den eingefrorenen Piloten ab")
    count = args.smoke_runs or (3 if checked["dataset"]["protocol_id"] == "import-folder-v1" else 10)
    if args.smoke_runs and checked["dataset"]["protocol_id"] != "import-smoke-v1":
        raise ValueError("--smoke-runs nur mit Smoke-Datensatz erlaubt")
    with checkout(target) as tree:
        series_start(output, bundle, "baseline", target, required_runs=count)
        expected = read_json(bundle / "expected-results.json")
        try:
            if checked["dataset"]["protocol_id"] == "import-10k-v1":
                calibration = _calibrate(bundle, output, tree, expected)
                if not calibration["passed"]:
                    raise RuntimeError("Messaufwand-Kalibrierung überschreitet 1 %")
            warmup = run_one(bundle, output / "runs" / "warmup_ref", revision_dir=tree,
                             expectations=expected)
            if warmup["status"] != "completed":
                raise RuntimeError(f"Aufwärmlauf ungültig: {warmup.get('reason')}")
            for i in range(count):
                run = run_one(bundle, output / "runs" / f"{i+1:02d}_ref", revision_dir=tree,
                              expectations=expected)
                if run["status"] != "completed":
                    raise RuntimeError(f"Referenzlauf ungültig: {run.get('reason')}")
            if checked["dataset"]["protocol_id"] == "import-10k-v1":
                for i in range(1, 4):
                    run_one(bundle, output / "runs" / f"diag_{i:02d}_ref", revision_dir=tree,
                            expectations=expected, phases=True)
                for i in range(1, 4):
                    run_one(bundle, output / "runs" / f"load_{i:02d}_ref", revision_dir=tree,
                            expectations=expected, read_load=True)
        finally:
            summary = report(output)
            _update_history(output, summary)
        print(json.dumps({"report": str(output / "result.md"), "status": summary["status"],
                          "median_seconds": summary["metrics_a"]["import_total_seconds_median"]}))


def compare(args):
    bundle, output = args.bundle.resolve(), args.output.resolve()
    checked = verify(bundle, require_expectations=True)
    count = args.smoke_pairs or 10
    if args.smoke_pairs and checked["dataset"]["protocol_id"] != "import-smoke-v1":
        raise ValueError("--smoke-pairs nur mit Smoke-Datensatz erlaubt")
    a, b = revision(args.baseline_revision), revision(args.candidate_revision)
    if a != read_json(bundle / "expected-results.json")["revision"]:
        raise ValueError("A-Revision weicht von den eingefrorenen Piloten ab")
    if a == b and not args.smoke_pairs:
        raise ValueError("Für compare zwei unterschiedliche Revisionen angeben")
    with checkout(a) as left, checkout(b) as right:
        metadata = series_start(output, bundle, "compare", a, b, required_runs=count)
        expected = read_json(bundle / "expected-results.json")
        try:
            if checked["dataset"]["protocol_id"] == "import-10k-v1":
                if not metadata["reference_run_id"]:
                    raise ValueError("Keine kompatible abgeschlossene Referenz vorhanden")
                calibration = read_json(ROOT / "benchmarks/results" /
                                        metadata["reference_run_id"] / "calibration.json")
                if not calibration["passed"]:
                    raise ValueError("Referenz-Kalibrierung nicht bestanden")
                shutil.copy2(ROOT / "benchmarks/results" / metadata["reference_run_id"] /
                             "calibration.json", output / "calibration.json")
            for letter, tree in (("a", left), ("b", right)):
                warmup = run_one(bundle, output / "runs" / f"warmup_{letter}",
                                 revision_dir=tree, expectations=expected)
                if warmup["status"] != "completed":
                    raise RuntimeError(f"Aufwärmlauf {letter} ungültig: {warmup.get('reason')}")
            for i in range(1, count + 1):
                order = (("a", left), ("b", right)) if i % 2 else (("b", right), ("a", left))
                for letter, tree in order:
                    run = run_one(bundle, output / "runs" / f"{i:02d}_{letter}",
                                  revision_dir=tree, expectations=expected)
                    if run["status"] != "completed":
                        raise RuntimeError(f"Vergleichspaar {i} ungültig: {run.get('reason')}")
            if checked["dataset"]["protocol_id"] == "import-10k-v1":
                for letter, tree in (("a", left), ("b", right)):
                    for i in range(1, 4):
                        run_one(bundle, output / "runs" / f"diag_{i:02d}_{letter}",
                                revision_dir=tree, expectations=expected, phases=True)
                    for i in range(1, 4):
                        run_one(bundle, output / "runs" / f"load_{i:02d}_{letter}",
                                revision_dir=tree, expectations=expected, read_load=True)
        finally:
            summary = report(output)
            _update_history(output, summary)
        print(json.dumps({"report": str(output / "result.md"), "status": summary["status"],
                          "paired_saved_percent": summary["comparison"]["paired_time_saved_percent_median"]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="Benchmark-Paket kopieren, prüfen und zwei Piloten ausführen")
    prep.add_argument("--config", type=Path, required=True)
    check = commands.add_parser("verify", help="Hashes und Datenbankintegrität prüfen")
    check.add_argument("--bundle", type=Path, required=True)
    base = commands.add_parser("baseline", help="Unveränderte Referenz gemäß Protokoll messen")
    base.add_argument("--bundle", type=Path, required=True)
    base.add_argument("--revision", required=True)
    base.add_argument("--output", type=Path, required=True)
    base.add_argument("--smoke-runs", type=int, choices=range(1, 4), help="Nur für Smoke-Datensatz")
    comp = commands.add_parser("compare", help="Zehn gepaarte A/B-Vergleiche ausführen")
    comp.add_argument("--bundle", type=Path, required=True)
    comp.add_argument("--baseline-revision", required=True)
    comp.add_argument("--candidate-revision", required=True)
    comp.add_argument("--output", type=Path, required=True)
    comp.add_argument("--smoke-pairs", type=int, choices=range(1, 4), help="Nur für Smoke-Datensatz")
    rep = commands.add_parser("report", help="Bericht aus vorhandenen Rohdaten neu berechnen")
    rep.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        config = read_json(args.config)
        reference = revision(config.get("reference_revision", "HEAD"))
        bundle = prepare(args.config)
        try:
            with checkout(reference) as tree:
                _pilot(bundle, tree, reference)
            print(json.dumps({"bundle": str(bundle), "dataset_id": verify(bundle)["dataset"]["dataset_id"],
                              "pilots": 2}))
        except BaseException:
            print(f"Paket liegt zur Diagnose unter {bundle}; Piloten unvollständig", file=sys.stderr)
            raise
    elif args.command == "verify":
        value = verify(args.bundle, require_expectations=True)
        print(json.dumps({"dataset_id": value["dataset"]["dataset_id"],
                          "books": value["database"]["books"],
                          "input_count": value["dataset"]["input_count"]}))
    elif args.command == "baseline":
        baseline(args)
    elif args.command == "compare":
        compare(args)
    else:
        value = report(args.results)
        print(json.dumps({"report": str(args.results / "result.md"), "status": value["status"]}))


if __name__ == "__main__":
    main()
