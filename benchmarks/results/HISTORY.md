# Benchmark-Historie

Hier wird jede Messreihe nach ihrem Abschluss mit Ergebnislink eingetragen.
Die neueste Reihe steht oben. Zeiten in Sekunden, Speicher in MiB; `—` bedeutet
nicht gemessen oder nicht vergleichbar. Referenzen gelten nur für ihre Gruppe.

**Aktueller Stand:** Die Importoptimierung aus `95dace6` ist gemessen. Direkt
nacheinander erhoben: alte Revision `78f780b` **108,41 s**, neue Revision
`95dace6` **53,43 s** — **50,7 % kürzer** bei gleichzeitig besserer
Analyseabdeckung (vollständige Fingerprints 21 → 27, fehlende 13 → 2). Details
und Einordnung: [Vergleichsnotiz](2026-10-02-vergleich-95dace6.md).
Der 10.000-Bücher-Benchmark bleibt mangels Ausgangsbestand blockiert; eine
Referenz nach `import-10k-v1` fehlt weiterhin.

| Messreihe / Bericht | Status | Vergleichsgruppe | Rolle / Referenz | Commit | Gültige Läufe A/B | Importmedian B bzw. Referenz (s) | Änderung zur Referenz | Speicherpeak (MiB) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [2026-10-02T13-41-53Z-95dace6](2026-10-02T13-41-53Z-95dace6/result.md) | invalid | `b7cc34a98594` | Referenz | `95dace688c26` | 3/— | 53.433 | — % | 559.8 |
| [2026-10-02T13-32-53Z-78f780b-recheck](2026-10-02T13-32-53Z-78f780b-recheck/result.md) | completed | `c291b6d3dd47` | Referenz | `78f780b3c916` | 3/— | 108.407 | — % | 573.2 |
| [2026-10-02T13-26-51Z-95dace6](2026-10-02T13-26-51Z-95dace6/result.md) | invalid | `85bc8d322208` | Referenz | `95dace688c26` | 3/— | 53.519 | — % | 569.8 |
| [2026-10-02T13-20-23Z-95dace6](2026-10-02T13-20-23Z-95dace6/result.md) | invalid | `dfb9a9df7310` | Referenz | `95dace688c26` | 3/— | 65.626 | — % | 552.7 |
| [2026-10-02T13-05-42Z-95dace6](2026-10-02T13-05-42Z-95dace6/result.md) | invalid | `12f85a385648` | Referenz | `95dace688c26` | 3/— | 79.052 | — % | 548.0 |
| [2026-10-02T11-42-36Z-78f780b-dirty](2026-10-02T11-42-36Z-78f780b-dirty/result.md) | completed | `6977a356bf93` (`import-smoke-v1`, 0 Bücher) | erste 43-Dateien-Messung; keine 10k-Referenz | `78f780b-dirty` | 3/— | 165.434 | — | 545.1 |
| [2026-10-02T11-08-22Z-78f780b-dirty](2026-10-02T11-08-22Z-78f780b-dirty/result.md) | blocked | — | Referenz geplant; keine vorhanden | `78f780b-dirty` | —/— | — | — | — |

Verlinke in „Rolle / Referenz“ auf die verwendete Referenzmessreihe. Kennzeichne
rein historische Prozentvergleiche als solche; A/B-Nachweise stehen im Bericht.
