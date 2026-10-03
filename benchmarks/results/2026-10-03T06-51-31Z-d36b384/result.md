# Importbenchmark 2026-10-03T06-51-31Z-d36b384

Status: **completed** · Protokoll: `import-folder-v1` · Datensatz: `b31ada7982450cc388dc1caffd9b4af7d6cd772bc1f511fe4e7c9d317dd6c37c`

Referenzrevision: `d36b384c018d7f7214f2f40cc670034df914415e`

## A / Referenz

Gültige Läufe: 3; ungültige Läufe: 0.

Median der Importzeit: 70.226021225 s; Variationskoeffizient: 0.0006258228019898797.

Backend-RSS-Peak, Median: 1468551168 B; Maximum: 1488109568 B.

## API während des Imports

| Variante | Endpunkt | Erfolgreiche Anfragen | Fehler/Ausfälle | p50 (ms) | p95 (ms) | p95 schwach belegt |
| --- | --- | ---: | ---: | ---: | ---: | --- |

## Diagnostische Phasen

Die Summen können parallele und geschachtelte Zeitspannen enthalten.

| Variante | Phase | Aufrufe | Median (ms) | Summe der Spannen (ms) |
| --- | --- | ---: | ---: | ---: |

## Diagnosezähler

| Variante | Zähler | Summe über Diagnoseläufe |
| --- | --- | ---: |

## Einzelmessungen

| Lauf | Revision | Importzeit (s) | Backend-RSS-Peak (MiB) | Status | Daten |
| --- | --- | ---: | ---: | --- | --- |
| 01_ref | A | 70.22659689 | 1400.5 | completed | [Rohdaten](runs/01_ref/run.json) |
| 02_ref | A | 70.150215893 | 1419.2 | completed | [Rohdaten](runs/02_ref/run.json) |
| 03_ref | A | 70.226021225 | 1327.8 | completed | [Rohdaten](runs/03_ref/run.json) |

## Reproduktion

[Maschinenlesbare Kennzahlen](summary.json) · [Umgebung](environment.json) · [Protokoll](protocol.json) · [Datensatz](dataset.json) · [Laufdaten](runs/)

Siehe `series.json` für Revisionen und Ausführungsmodus. Die Auswertung wurde ausschließlich aus gespeicherten Laufdaten berechnet.
