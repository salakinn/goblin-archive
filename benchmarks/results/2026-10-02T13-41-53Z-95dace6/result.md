# Importbenchmark 2026-10-02T13-41-53Z-95dace6

Status: **invalid** · Protokoll: `import-smoke-v1` · Datensatz: `6d4cdf1b98258a171cc16bdbd925952ac3b68e49312e54189675fa4838e8943f`

Referenzrevision: `95dace688c26484df7ab7a212cc52685fee5f0da`

## A / Referenz

Gültige Läufe: 3; ungültige Läufe: 0.

Median der Importzeit: 53.433173908 s; Variationskoeffizient: 0.05480823303735821.

Backend-RSS-Peak, Median: 578580480 B; Maximum: 587042816 B.

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
| 01_ref | A | 50.329458933 | 559.8 | completed | [Rohdaten](runs/01_ref/run.json) |
| 02_ref | A | 56.16933007 | 551.8 | completed | [Rohdaten](runs/02_ref/run.json) |
| 03_ref | A | 53.433173908 | 531.9 | completed | [Rohdaten](runs/03_ref/run.json) |

## Reproduktion

[Maschinenlesbare Kennzahlen](summary.json) · [Umgebung](environment.json) · [Protokoll](protocol.json) · [Datensatz](dataset.json) · [Laufdaten](runs/)

Siehe `series.json` für Revisionen und Ausführungsmodus. Die Auswertung wurde ausschließlich aus gespeicherten Laufdaten berechnet.

## Einschränkung

Zu wenige gültige Läufe, fehlende Diagnostik/Leselast oder Variationskoeffizient über 5 %
