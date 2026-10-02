# Importbenchmark 2026-10-02T13-26-51Z-95dace6

Status: **invalid** · Protokoll: `import-smoke-v1` · Datensatz: `6d4cdf1b98258a171cc16bdbd925952ac3b68e49312e54189675fa4838e8943f`

Referenzrevision: `95dace688c26484df7ab7a212cc52685fee5f0da`

## A / Referenz

Gültige Läufe: 3; ungültige Läufe: 0.

Median der Importzeit: 53.519356894 s; Variationskoeffizient: 0.2096143381829881.

Backend-RSS-Peak, Median: 579330048 B; Maximum: 597450752 B.

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
| 01_ref | A | 72.908719453 | 552.5 | completed | [Rohdaten](runs/01_ref/run.json) |
| 02_ref | A | 53.519356894 | 525.6 | completed | [Rohdaten](runs/02_ref/run.json) |
| 03_ref | A | 50.021710055 | 569.8 | completed | [Rohdaten](runs/03_ref/run.json) |

## Reproduktion

[Maschinenlesbare Kennzahlen](summary.json) · [Umgebung](environment.json) · [Protokoll](protocol.json) · [Datensatz](dataset.json) · [Laufdaten](runs/)

Siehe `series.json` für Revisionen und Ausführungsmodus. Die Auswertung wurde ausschließlich aus gespeicherten Laufdaten berechnet.

## Einschränkung

Zu wenige gültige Läufe, fehlende Diagnostik/Leselast oder Variationskoeffizient über 5 %
