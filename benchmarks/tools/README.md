# Importbenchmark-Runner

Der Runner ist [benchmark_import.py](benchmark_import.py). Er implementiert
`prepare`, `verify`, `baseline`, `compare` und `report` gemäß
[Messvorschrift](../import-10k.md). Die Backend-Anpassung für lokale Messungen
liegt in `server.py`. Sie deaktiviert externe Provider und Nachbearbeitung und
schreibt Abschlussnachweise. Große Testarchive und Piloten bleiben unter
`benchmarks/.local/`.

Voraussetzung: `.venv/bin/pip install -e '.[dev]'`. `numpy` wird für das
reproduzierbare Bootstrap-Intervall benötigt. Alle Befehle werden vom
Repositorywurzelverzeichnis aus gestartet.

Konfiguration für `prepare` als `benchmarks/.local/benchmark-config.json`:

```json
{
  "snapshot_source": "/absoluter/pfad/zum/ruhenden/ausgangsarchiv",
  "input_source": "/home/steffen/Work/Testdaten",
  "bundle_dir": "/absoluter/pfad/zu/benchmarks/.local/import-10k-v1",
  "scratch_dir": "/absoluter/pfad/fuer/isolierte/laufarchive",
  "reference_revision": "HEAD",
  "production_dir": "/home/steffen/Work/goblin-archive/goblin-data"
}
```

`snapshot_source` muss eine konsistente `goblin.db` und die zugehörige
`library/` enthalten. Bei mehr als 10.000 Büchern wählt `prepare` anhand
von SHA-256 und Buch-ID exakt 10.000 aus. Bei weniger als 10.000 bricht es ab.
`input_source` darf Begleitdateien enthalten; nur EPUB, PDF, MOBI, AZW3 und FB2
werden in die Import-Batches aufgenommen. `prepare` kopiert den Bestand,
erstellt Manifeste und friert nach zwei gleichen Probeläufen die fachlichen
Erwartungen ein. Die beiden Piloten verwenden `reference_revision` in einem
isolierten Git-Checkout. `scratch_dir` bestimmt das tatsächlich gemessene
Speichermedium und liegt außerhalb des versionierten Ergebnisordners.
Quelldaten bleiben erhalten.

```bash
.venv/bin/python benchmarks/tools/benchmark_import.py prepare --config benchmarks/.local/benchmark-config.json
.venv/bin/python benchmarks/tools/benchmark_import.py verify --bundle benchmarks/.local/import-10k-v1
.venv/bin/python benchmarks/tools/benchmark_import.py baseline --bundle benchmarks/.local/import-10k-v1 --revision HEAD --output benchmarks/results/2026-10-02T14-30-00Z-a1b2c3d
.venv/bin/python benchmarks/tools/benchmark_import.py compare --bundle benchmarks/.local/import-10k-v1 --baseline-revision REF_COMMIT --candidate-revision TEST_COMMIT --output benchmarks/results/2026-10-03T14-30-00Z-d4e5f6a
.venv/bin/python benchmarks/tools/benchmark_import.py report --results benchmarks/results/2026-10-03T14-30-00Z-d4e5f6a
```

Ersetze Zeit, Commit und Revisionen durch echte Werte. `baseline` prüft zunächst
den Aufwand von Abschlussbeobachter und Ressourcenmonitor mit drei abwechselnden
Laufpaaren. Danach folgen ein Aufwärmlauf, zehn gemessene Läufe, drei
Diagnoseläufe und drei Läufe mit
Leselast aus. `compare` nutzt zehn A/B-Paare und Diagnose-/Leselastläufe für
beide Revisionen sowie die Kalibrierung der passenden Referenz. Beide Befehle
benötigen ein mit `prepare` eingefrorenes Paket.
`report` berechnet Kennzahlen erneut aus gespeicherten Rohdaten; es startet
kein Backend. Ergebnisberichte liegen unter `benchmarks/results/`.

Getestet wurde die Befehlsstruktur mit einem isolierten Smoke-Datensatz von
null Ausgangsbüchern und einem EPUB: Paketprüfung, zwei Piloten, HTTP-Import,
Backendabschluss, fachliche Validierung, Ressourcenaufnahme, Phaseninstrumentierung,
Leselast, grobe Abschlusskontrolle, einen verkürzten A/B-Durchlauf und
Offline-Bericht. Dieser Test ist keine 10k-Referenz. Der offizielle
Zehn-Lauf-Benchmark benötigt einen geeigneten 10.000-Bücher-Ausgangsbestand.
