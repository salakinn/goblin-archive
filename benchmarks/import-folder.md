# Importbenchmark mit lokalem Testdatenordner (`import-folder-v1`)

Der Standardbenchmark importiert **alle unterstützten Dateien** des angegebenen
Testdatenordners in ein leeres, isoliertes Goblin-Archiv. 10.000 vorhandene Bücher
sind ein späteres Skalierungsziel und gehören nicht zu diesem Protokoll.

## Datensatz und Ablauf

- Unterstützte Endungen: EPUB, PDF, MOBI, AZW3 und FB2. Begleitdateien werden
  mit dem Paket eingefroren, aber nicht importiert.
- Pfade werden nach ihren relativen UTF-8-Bytes sortiert. Das Paket speichert
  SHA-256, Größe und `input_id` jeder Datei sowie die feste Batchfolge.
- Ein Batch enthält höchstens 20 Dateien und 200 MiB Nutzdaten. Der Client
  sendet sequenziell über eine persistente HTTP-Verbindung. Nach jeweils
  mindestens 80 Dateien wartet er auf vollständige automatische Verarbeitung
  einschließlich Vorschau, bevor die nächste Welle beginnt. Diese Wartezeit
  gehört zur Importzeit und hält Warteschlangen- und Staginglimits ein.
- Das Backend hat einen Worker, einen Importarbeiter und keine externen Provider
  oder Nachbearbeitung. Lokale Analyse und Duplikatprüfung bleiben aktiv.
  Die serielle Importverarbeitung sorgt auch bei bytegleichen Dateien im selben
  Paket für eine feste Entscheidungsreihenfolge.
- Zwei Piloten aus frischen Archivkopien müssen gleiche fachliche Ergebnisse
  liefern. Vorab dokumentierte defekte Dateien dürfen als erwartete Fehler im
  Paket bleiben; jeder zusätzliche Fehler macht den Lauf ungültig. Ein
  ungewerteter Aufwärmlauf und drei gültige Messläufe folgen.
  Jeder Lauf startet mit einer frischen Kopie des leeren Snapshots.
- `import_total_seconds` reicht vom Beginn des ersten HTTP-Uploads bis zum
  letzten automatischen Abschluss einschließlich Vorschau. Ressourcen werden
  alle 100 ms gemessen. Rohdaten und fachliche Validierung bleiben je Lauf
  erhalten. Berichtet werden Einzelwerte, Median, Streuung und Speicherpeak.
  Bei Variationskoeffizient über 5 % wird die Reihe als instabil gekennzeichnet.
- Eine neue Hardware, andere Dateien oder andere Abhängigkeiten bilden eine
  neue Vergleichsgruppe. Die frühere 43-Dateien-Messung ist keine Referenz.

## Ausführung

Eine lokale Konfiguration unter `benchmarks/.local/benchmark-folder-config.json`
enthält `protocol_id: "import-folder-v1"`, `expected_books: 0`, die absoluten
Pfade `snapshot_source`, `input_source`, `bundle_dir`, `scratch_dir` und
`production_dir` sowie `reference_revision`. `snapshot_source` ist ein
initialisiertes leeres Archiv mit `goblin.db` und `library/`. Archiv und
Testdaten bleiben vom Produktivarchiv getrennt. Der Runner kopiert die Quellen
in das ignorierte Paketverzeichnis und verändert sie nicht.

```bash
.venv/bin/python benchmarks/tools/benchmark_import.py prepare --config benchmarks/.local/benchmark-folder-config.json
.venv/bin/python benchmarks/tools/benchmark_import.py verify --bundle benchmarks/.local/import-folder-v1
.venv/bin/python benchmarks/tools/benchmark_import.py baseline --bundle benchmarks/.local/import-folder-v1 --revision HEAD --output benchmarks/results/ZEIT-COMMIT
.venv/bin/python benchmarks/tools/benchmark_import.py report --results benchmarks/results/ZEIT-COMMIT
```

`prepare` friert erst nach zwei gleichen Piloten die fachlichen Erwartungen ein.
Ein abgebrochener oder ungültiger Lauf wird mit Rohdaten gespeichert und darf
nicht als gültige Importzeit ausgegeben werden. Die 10k-Vorschrift bleibt in
[import-10k.md](import-10k.md) unverändert erhalten.
