"""Recompute import benchmark statistics from recorded runs."""
from __future__ import annotations

import json
import platform
import statistics
import subprocess
import sys
from pathlib import Path

from benchmarks.tools.bundle import ROOT, data_hash, digest, read_json, write_json


def environment(storage: Path) -> dict:
    packages = subprocess.run([sys.executable, "-m", "pip", "freeze"], text=True,
                              capture_output=True, check=True).stdout.splitlines()
    cpu = Path("/proc/cpuinfo").read_text().split("\n\n", 1)[0] if Path("/proc/cpuinfo").exists() else ""
    try:
        mount = subprocess.run(["findmnt", "-no", "SOURCE,FSTYPE,OPTIONS", "--target", str(storage)],
                               text=True, capture_output=True, check=False)
        mount_description = mount.stdout.strip() if mount.returncode == 0 else None
    except FileNotFoundError:
        mount_description = None
    return {"platform": platform.platform(), "machine": platform.machine(),
            "python": sys.version, "sqlite": __import__("sqlite3").sqlite_version,
            "cpu": cpu, "cpu_count": __import__("os").cpu_count(),
            "storage_path": str(storage.resolve()),
            "storage_mount": mount_description,
            "packages": packages}


def metrics(runs: list[dict]) -> dict:
    good = [r for r in runs if r["status"] == "completed"]
    result = {"valid_runs": len(good), "invalid_runs": len(runs) - len(good),
              "import_total_seconds_median": None, "import_total_seconds_min": None,
              "import_total_seconds_max": None, "import_total_seconds_mean": None,
              "import_total_seconds_sample_stddev": None, "coefficient_of_variation": None,
              "files_per_minute_median": None, "backend_peak_rss_bytes_median": None,
              "backend_peak_rss_bytes_max": None}
    if good:
        times = [r["import_total_seconds"] for r in good]
        peaks = [r["backend_peak_rss_bytes"] for r in good]
        result.update(import_total_seconds_median=statistics.median(times),
                      import_total_seconds_min=min(times), import_total_seconds_max=max(times),
                      import_total_seconds_mean=statistics.mean(times),
                      import_total_seconds_sample_stddev=statistics.stdev(times) if len(times) > 1 else None,
                      coefficient_of_variation=(statistics.stdev(times) / statistics.mean(times))
                      if len(times) > 1 else None,
                      files_per_minute_median=statistics.median(r["files_per_minute"] for r in good),
                      backend_peak_rss_bytes_median=statistics.median(peaks),
                      backend_peak_rss_bytes_max=max(peaks))
    return result


def comparison(pairs: list[dict], left: dict, right: dict) -> dict:
    result = {"paired_time_saved_percent_median": None,
              "paired_time_saved_percent_ci95": None,
              "historical_time_saved_percent_vs_reference": None,
              "historical_time_saved_percent_vs_previous": None,
              "reliably_faster": None}
    if len(pairs) != 10 or any(pair["a"]["status"] != "completed" or
                               pair["b"]["status"] != "completed" for pair in pairs):
        return result
    import numpy as np

    values = np.array([100 * (1 - p["b"]["import_total_seconds"] /
                            p["a"]["import_total_seconds"]) for p in pairs])
    generator = np.random.Generator(np.random.PCG64(10000))
    samples = np.median(values[generator.integers(0, 10, size=(10_000, 10))], axis=1)
    lo, hi = np.quantile(samples, [0.025, 0.975], method="linear")
    median = float(np.quantile(values, 0.5, method="linear"))
    stable = (left["coefficient_of_variation"] is not None and
              right["coefficient_of_variation"] is not None and
              left["coefficient_of_variation"] <= 0.05 and
              right["coefficient_of_variation"] <= 0.05)
    return {**result, "paired_time_saved_percent_median": median,
            "paired_time_saved_percent_ci95": [float(lo), float(hi)],
            "reliably_faster": bool(stable and median >= 5 and lo > 0)}


def _find_runs(folder: Path, letter: str) -> list[dict]:
    return [read_json(path) for path in sorted((folder / "runs").glob(f"[0-9][0-9]_{letter}/run.json"))]


def _read_load(folder: Path, suffix: str) -> dict:
    records = []
    paths = sorted((folder / "runs").glob(f"load_*_{suffix}/requests.jsonl"))
    if not paths:
        return {}
    for path in paths:
        records.extend(json.loads(line) for line in path.read_text().splitlines() if line)
    result = {}
    for endpoint in ("/api/books?limit=50&offset=0", "/api/search?q=ABAP"):
        selected = [row for row in records if row["endpoint"] == endpoint]
        success = [row for row in selected if row["status"] == 200]
        if success:
            import numpy as np
            actual = [(row["ended_ns"] - row["started_ns"]) / 1e6 for row in success]
            planned = [(row["ended_ns"] - row["planned_ns"]) / 1e6 for row in success]
            p50, p95 = np.quantile(actual, [0.5, 0.95], method="linear")
            scheduled_p50, scheduled_p95 = np.quantile(planned, [0.5, 0.95], method="linear")
        else:
            p50 = p95 = scheduled_p50 = scheduled_p95 = None
        result[endpoint] = {"planned": len(selected), "success": len(success),
                            "errors": len(selected) - len(success),
                            "skipped": sum(row["error"] == "capacity_exceeded" for row in selected),
                            "p50_ms": float(p50) if p50 is not None else None,
                            "p95_ms": float(p95) if p95 is not None else None,
                            "scheduled_p50_ms": float(scheduled_p50) if scheduled_p50 is not None else None,
                            "scheduled_p95_ms": float(scheduled_p95) if scheduled_p95 is not None else None,
                            "p95_weak": len(success) < 200}
    return result


def _phases(folder: Path, suffix: str) -> dict:
    durations: dict[str, list[float]] = {}
    for path in sorted((folder / "runs").glob(f"diag_*_{suffix}/phases.jsonl")):
        for line in path.read_text().splitlines():
            entry = json.loads(line)
            if entry["phase"] == "counts":
                continue
            durations.setdefault(entry["phase"], []).append((entry["end_ns"] - entry["start_ns"]) / 1e6)
    return {phase: {"calls": len(values), "median_ms": statistics.median(values),
                    "total_ms_overlapping": sum(values)} for phase, values in sorted(durations.items())}


def _phase_counters(folder: Path, suffix: str) -> dict:
    counts: dict[str, int] = {}
    for path in sorted((folder / "runs").glob(f"diag_*_{suffix}/phases.jsonl")):
        for line in path.read_text().splitlines():
            entry = json.loads(line)
            if entry["phase"] == "counts":
                for name, value in entry["counts"].items():
                    counts[name] = counts.get(name, 0) + value
    return counts


def report(folder: Path) -> dict:
    folder = folder.resolve()
    metadata = read_json(folder / "series.json")
    a = _find_runs(folder, "a")
    b = _find_runs(folder, "b")
    if metadata["mode"] == "baseline":
        a = [read_json(path) for path in sorted((folder / "runs").glob("[0-9][0-9]_ref/run.json"))]
    meta_a, meta_b = metrics(a), metrics(b) if b else None
    diagnostics = [read_json(path) for path in sorted((folder / "runs").glob("diag_*/run.json"))]
    load_runs = [read_json(path) for path in sorted((folder / "runs").glob("load_*/run.json"))]
    pairs = []
    if b:
        for index in range(1, 11):
            pair_a = next((r for r in a if r["run_id"].startswith(f"{index:02d}_")), None)
            pair_b = next((r for r in b if r["run_id"].startswith(f"{index:02d}_")), None)
            if pair_a and pair_b:
                pairs.append({"a": pair_a, "b": pair_b})
    contrast = comparison(pairs, meta_a, meta_b) if b else {
        "paired_time_saved_percent_median": None, "paired_time_saved_percent_ci95": None,
        "historical_time_saved_percent_vs_reference": None,
        "historical_time_saved_percent_vs_previous": None, "reliably_faster": None}
    expected = metadata["required_runs"]
    valid = (meta_a["valid_runs"] == expected and meta_a["invalid_runs"] == 0 and
             (not b or (meta_b["valid_runs"] == expected and meta_b["invalid_runs"] == 0)))
    stable = (meta_a["coefficient_of_variation"] is not None and
              meta_a["coefficient_of_variation"] <= 0.05 and
              (not b or (meta_b["coefficient_of_variation"] is not None and
                         meta_b["coefficient_of_variation"] <= 0.05)))
    extra_expected = 0 if metadata["protocol_id"] in {"import-smoke-v1", "import-folder-v1"} else (6 if b else 3)
    extra_valid = (len(diagnostics) == extra_expected and len(load_runs) == extra_expected and
                   all(row["status"] == "completed" for row in diagnostics + load_runs))
    if metadata["protocol_id"] == "import-10k-v1":
        try:
            calibration_ok = read_json(folder / "calibration.json")["passed"] is True
        except (OSError, ValueError, KeyError):
            calibration_ok = False
        extra_valid = extra_valid and calibration_ok
    template = read_json(ROOT / "benchmarks/templates/summary.json")
    all_recorded = [read_json(path) for path in (folder / "runs").glob("*/run.json")]
    latest_end = max((entry["finished_at_utc"] for entry in all_recorded),
                     default=metadata["started_at_utc"])
    if any(entry["status"] == "aborted" for entry in all_recorded) or not all_recorded:
        series_status = "aborted"
        reason = next((entry.get("reason") for entry in all_recorded
                       if entry["status"] == "aborted"), "Keine Laufdaten vorhanden")
    elif valid and (stable or metadata["protocol_id"] == "import-folder-v1") and extra_valid:
        series_status, reason = "completed", None
    else:
        series_status = "invalid"
        reason = "Zu wenige gültige Läufe, fehlende Diagnostik/Leselast oder Variationskoeffizient über 5 %"
    template.update(run_id=folder.name, mode=metadata["mode"],
                    started_at_utc=metadata["started_at_utc"],
                    finished_at_utc=latest_end,
                    protocol_id=metadata["protocol_id"], protocol_sha256=metadata["protocol_sha256"],
                    dataset_id=metadata["dataset_id"], runner_sha256=metadata["runner_sha256"],
                    environment_fingerprint=metadata["environment_fingerprint"],
                    comparison_group=metadata["comparison_group"],
                    revision_a=metadata["revision_a"], revision_b=metadata.get("revision_b"),
                    reference_run_id=metadata.get("reference_run_id"),
                    previous_compatible_run_id=metadata.get("previous_compatible_run_id"),
                    metrics_a=meta_a, metrics_b=meta_b, comparison=contrast,
                    validation_passed=valid,
                    status=series_status, reason=reason)
    template["planned_run_order"] = metadata.get("planned_run_order", [])
    template["executed_run_order"] = [entry["run_id"] for entry in sorted(
        all_recorded, key=lambda value: (value["started_at_utc"], value["run_id"]))]
    suffix = "a" if b else "ref"
    template["read_load"] = {"a": _read_load(folder, suffix),
                             "b": _read_load(folder, "b") if b else None}
    template["phases"] = {"a": _phases(folder, suffix),
                          "b": _phases(folder, "b") if b else None}
    template["phase_counters"] = {"a": _phase_counters(folder, suffix),
                                  "b": _phase_counters(folder, "b") if b else None}
    current = meta_b or meta_a
    current_median = current["import_total_seconds_median"]
    for key, old in (("historical_time_saved_percent_vs_reference", metadata.get("reference_median_seconds")),
                     ("historical_time_saved_percent_vs_previous", metadata.get("previous_median_seconds"))):
        if current_median is not None and old:
            template["comparison"][key] = 100 * (1 - current_median / old)
    write_json(folder / "summary.json", template)
    _render(folder, template, a, b)
    return template


def _render(folder: Path, summary: dict, a: list[dict], b: list[dict]) -> None:
    lines = [f"# Importbenchmark {folder.name}", "",
             f"Status: **{summary['status']}** · Protokoll: `{summary['protocol_id']}` · "
             f"Datensatz: `{summary['dataset_id']}`", "",
             f"Referenzrevision: `{summary['revision_a']}`" +
             (f" · Variante: `{summary['revision_b']}`" if summary["revision_b"] else ""), ""]
    for label, value in (("A / Referenz", summary["metrics_a"]), ("B / Variante", summary["metrics_b"])):
        if value:
            lines += [f"## {label}", "",
                      f"Gültige Läufe: {value['valid_runs']}; ungültige Läufe: {value['invalid_runs']}.", "",
                      f"Median der Importzeit: {value['import_total_seconds_median']} s; "
                      f"Variationskoeffizient: {value['coefficient_of_variation']}.", "",
                      f"Backend-RSS-Peak, Median: {value['backend_peak_rss_bytes_median']} B; "
                      f"Maximum: {value['backend_peak_rss_bytes_max']} B.", ""]
    if b:
        c = summary["comparison"]
        lines += ["## Gepaarter Vergleich", "",
                  f"Mediane Zeitersparnis: {c['paired_time_saved_percent_median']} %; "
                  f"95-%-Intervall: {c['paired_time_saved_percent_ci95']} %.", "",
                  f"Belastbar schneller nach Protokoll: {c['reliably_faster']}.", ""]
    if summary.get("read_load"):
        lines += ["## API während des Imports", "",
                  "| Variante | Endpunkt | Erfolgreiche Anfragen | Fehler/Ausfälle | p50 (ms) | p95 (ms) | p95 schwach belegt |",
                  "| --- | --- | ---: | ---: | ---: | ---: | --- |"]
        for variant, entries in summary["read_load"].items():
            for endpoint, value in (entries or {}).items():
                lines.append(f"| {variant} | `{endpoint}` | {value['success']} | {value['errors']} | "
                             f"{value['p50_ms']} | {value['p95_ms']} | {value['p95_weak']} |")
        lines.append("")
    if summary.get("phases"):
        lines += ["## Diagnostische Phasen", "",
                  "Die Summen können parallele und geschachtelte Zeitspannen enthalten.", "",
                  "| Variante | Phase | Aufrufe | Median (ms) | Summe der Spannen (ms) |",
                  "| --- | --- | ---: | ---: | ---: |"]
        for variant, entries in summary["phases"].items():
            for phase, value in (entries or {}).items():
                lines.append(f"| {variant} | {phase} | {value['calls']} | {value['median_ms']:.2f} | "
                             f"{value['total_ms_overlapping']:.2f} |")
        lines.append("")
    if summary.get("phase_counters"):
        lines += ["## Diagnosezähler", "",
                  "| Variante | Zähler | Summe über Diagnoseläufe |",
                  "| --- | --- | ---: |"]
        for variant, entries in summary["phase_counters"].items():
            for name, value in (entries or {}).items():
                lines.append(f"| {variant} | {name} | {value} |")
        lines.append("")
    lines += ["## Einzelmessungen", "",
              "| Lauf | Revision | Importzeit (s) | Backend-RSS-Peak (MiB) | Status | Daten |",
              "| --- | --- | ---: | ---: | --- | --- |"]
    for label, runs in (("A", a), ("B", b)):
        for item in runs:
            peak = item.get("backend_peak_rss_bytes", 0) / 1048576
            lines.append(f"| {item['run_id']} | {label} | {item.get('import_total_seconds', '—')} | "
                         f"{peak:.1f} | {item['status']} | [Rohdaten](runs/{item['run_id']}/run.json) |")
    lines += ["", "## Reproduktion", "",
              "[Maschinenlesbare Kennzahlen](summary.json) · "
              "[Umgebung](environment.json) · [Protokoll](protocol.json) · "
              "[Datensatz](dataset.json) · [Laufdaten](runs/)", "",
              "Siehe `series.json` für Revisionen und Ausführungsmodus. "
              "Die Auswertung wurde ausschließlich aus gespeicherten Laufdaten berechnet.", ""]
    if (folder / "calibration.json").exists():
        lines += ["[Kalibrierung des Messaufwands](calibration.json)", ""]
    if summary["reason"]:
        lines += ["## Einschränkung", "", summary["reason"], ""]
    (folder / "result.md").write_text("\n".join(lines), encoding="utf-8")
