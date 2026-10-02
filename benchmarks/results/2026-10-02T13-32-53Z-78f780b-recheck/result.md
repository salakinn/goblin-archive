# Importbenchmark 2026-10-02T13-32-53Z-78f780b-recheck

Status: **completed** · Protokoll: `import-smoke-v1` · Datensatz: `6d4cdf1b98258a171cc16bdbd925952ac3b68e49312e54189675fa4838e8943f`

Referenzrevision: `78f780b3c91622aa24934fab21303da01500f747`

## A / Referenz

Gültige Läufe: 3; ungültige Läufe: 0.

Median der Importzeit: 108.407292221 s; Variationskoeffizient: 0.028649556387546408.

Backend-RSS-Peak, Median: 586711040 B; Maximum: 601001984 B.

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
| 01_ref | A | 108.407292221 | 559.5 | completed | [Rohdaten](runs/01_ref/run.json) |
| 02_ref | A | 111.445262901 | 529.7 | completed | [Rohdaten](runs/02_ref/run.json) |
| 03_ref | A | 105.236628284 | 573.2 | completed | [Rohdaten](runs/03_ref/run.json) |

## Reproduktion

[Maschinenlesbare Kennzahlen](summary.json) · [Umgebung](environment.json) · [Protokoll](protocol.json) · [Datensatz](dataset.json) · [Laufdaten](runs/)

Siehe `series.json` für Revisionen und Ausführungsmodus. Die Auswertung wurde ausschließlich aus gespeicherten Laufdaten berechnet.
