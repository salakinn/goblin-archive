# Importbenchmark 2026-10-02T11-42-36Z-78f780b-dirty

Status: **completed** · Protokoll: `import-smoke-v1` · Datensatz: `6d4cdf1b98258a171cc16bdbd925952ac3b68e49312e54189675fa4838e8943f`

Referenzrevision: `78f780b3c91622aa24934fab21303da01500f747`

## Messgegenstand und Aussagegrenze

Gemessen wurde der lokale HTTP-Import der **43 unterstützten E-Books** aus `/home/steffen/Work/Testdaten/Unsortiert/{1_Fachbücher,2_Unterhaltung,3_Autoren}` (18 PDF, 16 EPUB, 7 FB2, 2 MOBI; insgesamt 705.636.721 Bytes / 672,95 MiB). Der Ausgangsbestand ist ein isolierter, leerer Snapshot mit **0 Büchern**. Importzeit bedeutet Beginn des ersten Uploads bis zum Abschluss aller automatischen Import- und Vorschauarbeiten. Externe Anbieter und Nachbearbeitung waren deaktiviert; ein Backend-Worker, kein Browser und keine zusätzliche API-Leselast. Die Dateien wurden in fünf auf 20 Dateien bzw. 200 MiB begrenzten, sequenziellen HTTP-Batches gesendet.

Dies ist ein **43-Dateien-Leerarchiv-Benchmark** mit eigener Konfiguration (`import-smoke-v1`, ein Importarbeiter), keine 10.000-Bücher-Messung und keine Referenz für `import-10k-v1`. Eine feste historische Vergleichsreferenz und ein letzter kompatibler Bericht fehlen; historische Prozentvergleiche sind nicht möglich. Der getestete Produktcode stammt aus dem isolierten Checkout von `78f780b3c91622aa24934fab21303da01500f747`; die Benchmark-Werkzeuge sind lokal noch nicht committet und durch `runner_sha256` in `summary.json` identifiziert.

## A / Referenz

Gültige Läufe: 3; ungültige Läufe: 0.

Median der Importzeit: 165.433910523 s; Variationskoeffizient: 0.04741056990351356.

Backend-RSS-Peak, Median: 565104640 B; Maximum: 571596800 B.

- Median: **165,434 s** (2 min 45 s), Spanne 163,005–177,920 s; Stichproben-Standardabweichung 8,002 s, Variationskoeffizient 4,74 %; drei gültige Messläufe nach einem ungewerteten Aufwärmlauf.
- Durchsatz: 15,60 Eingabedateien/min bezogen auf die Gesamtdauer (einschließlich des Duplikats und der Prüfvorschau); Median Uploadabschnitt 29,798 s, Median Restverarbeitung 135,636 s. Abschnitte überlappen sich mit Backendarbeit.
- Backend-RSS-Peak, Median: **539,0 MiB**, Maximum: **545,1 MiB**; 100-ms-Samples der zeitgleich summierten Backendprozesse. Client separat in `resources.jsonl`; kurzlebige Spitzen können fehlen.
- Pro Lauf: **41 archiviert, 1 identisches Duplikat, 1 Prüffall mit fertiger Vorschau, 0 Fehler**. Integrität, Buchzahl, FTS-Zuordnung, referenzierte Dateien und Vergleich gegen die zwei eingefrorenen Pilotergebnisse: bestanden.

Beim ersten Probelauf mit drei Importarbeitern kam es bei einer der 43 Dateien zu einem Schreibkonflikt beim Anlegen des Autors `Kirkman, Robert` (`UNIQUE constraint failed: authors.name`; 40 archiviert, 2 Prüffälle, 1 Fehler). Dieser **ungültige Pilot** ist ausschließlich unter `benchmarks/.local/example43-bundle/.pilots/pilot_1/` erhalten (nicht Teil der gültigen Messreihe). Für die Messreihe wurde ein neues, getrenntes Paket mit **`GOBLIN_IMPORT_CONCURRENCY=1`** erstellt; zwei identische gültige Piloten gingen der Messung voraus. Der Fehler mit drei Arbeitern ist ein konkreter Hinweis auf eine Parallelitätsrace, kein mit einer geänderten Codeversion verglichener Geschwindigkeitsbefund.

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

## Phasendiagnose (zwei Zusatzläufe)

Nach der Messreihe wurden zwei **gültige** Diagnoseläufe mit Phaseninstrumentierung auf demselben Paket und derselben Revision ausgeführt (157,5 s und 155,3 s; beide fachlich validiert). Rohdaten: [diagnostics/](diagnostics/). Die Instrumentierung kostet selbst Zeit, daher sind diese Läufe nicht Teil der offiziellen Messreihe. Die Spannen sind geschachtelt: `fingerprint` enthält `text_extraction…`, `shingles` und `signature`.

| Phase | Aufrufe | Summe (s) | Anteil Gesamtzeit | Median (s) | Max (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fingerprint` (gesamt) | 42 | 140,9 | **89,5 %** | 0,58 | 25,58 |
| ├ `text_extraction_and_normalization` | 43 | 104,5 | **66,3 %** | 0,22 | 22,59 |
| ├ `signature` | 21 | 31,2 | **19,8 %** | 1,55 | 2,54 |
| └ `shingles` | 21 | 4,8 | 3,1 % | 0,24 | 0,41 |
| `metadata_and_cover` | 42 | 9,6 | 6,1 % | 0,05 | 1,22 |
| `sha256` | 43 | 3,6 | 2,3 % | 0,02 | 0,77 |
| `archive_database_fts` | 41 | 1,0 | 0,7 % | 0,02 | 0,23 |
| `candidate_search_and_comparison` | 42 | 0,5 | 0,3 % | 0,01 | 0,03 |

Zähler je Lauf: **1.430 SQL-Abfragen**, 645 Schreibanweisungen, 42 Textextraktionen. Beide Diagnoseläufe liefern identische Zähler.

Verteilung nach Format (Summe `fingerprint`): **PDF 110,1 s bei 18 Dateien**, EPUB 23,8 s bei 16, FB2 7,0 s bei 7, MOBI 0,0 s bei 2 (keine Textanalyse). Die fünf teuersten Dateien verursachen 93,0 s, also **66 % der gesamten Fingerprintzeit**; alle fünf sind PDFs.

## Optimierungsvorschläge

Priorisiert nach gemessenem Anteil an der Gesamtzeit. Die Einsparungen sind **Hochrechnungen aus Phasendaten und Mikrobenchmarks**, nicht durch einen A/B-Importlauf nachgewiesen. Jede Änderung braucht vor Übernahme eine eigene A/B-Messreihe und einen Ergebnisvergleich gegen `expected-results.json`.

### 1. PDF-Textextraktion ersetzen — größter Hebel

`backend/duplicates.py:73-84` extrahiert Text mit `pypdf` in reinem Python. Gemessen am konkreten Bestand: **104,5 s / 66,3 %** der Gesamtzeit, davon der Großteil in wenigen großen PDFs.

Vergleichsmessung derselben vier vollständig extrahierten PDFs: `pypdf` **65,0 s** gegenüber `pypdfium2` **5,6 s** — Faktor **11,6**, absolute Ersparnis 59,4 s. `pypdfium2` ist ein C-Backend und gibt den GIL während der Extraktion frei.

Erwartete Wirkung: grob **50–60 % kürzere Gesamtzeit** für dieses Paket. Risiko: Der extrahierte Text ändert sich geringfügig, damit ändern sich `text_hash`, Shingles und Signaturen. Das ist eine **Änderung der Analyseversion** und erfordert eine Neuberechnung bestehender Fingerprints sowie eine neue Ergebnisreferenz. Nicht als reine Beschleunigung einführen.

### 2. `signature_for` vektorisieren — risikoarm, bitgenau gleich

`backend/duplicates.py:113-116` bildet 32 MinHash-Werte in 32 getrennten Python-Durchläufen über die Shingle-Menge. Gemessen: **31,2 s / 19,8 %** der Gesamtzeit bei nur 21 Aufrufen (Median 1,55 s).

Mikrobenchmark mit identischem Ergebnis (`assert` auf Gleichheit bestanden):

| Shingles | aktuell | eine Schleife | NumPy |
| ---: | ---: | ---: | ---: |
| 20.000 | 0,347 s | 0,252 s | **0,018 s** (19,2×) |
| 120.000 | 2,389 s | 1,805 s | **0,095 s** (25,1×) |

Erwartete Wirkung: etwa **30 s** weniger, rund **18 %** der Gesamtzeit. Da die Ausgabe **bitgenau identisch** ist, ändern sich weder Fingerprints noch Duplikatentscheidungen — keine Neuberechnung nötig. Das ist der beste Aufwand-Nutzen-Schnitt; NumPy ist als Abhängigkeit bereits vorhanden.

### 3. Importparallelität: Prozesse statt Threads

Die CPU-Arbeit läuft über `asyncio.to_thread` (`backend/imports.py:414,426,446,447`), ist aber durch den GIL serialisiert. Gemessen an vier PDFs:

| Variante | Zeit | Beschleunigung |
| --- | ---: | ---: |
| seriell | 46,5 s | 1,00× |
| 4 Threads | 49,0 s | **0,95×** |
| 4 Prozesse | 25,7 s | **1,81×** |

**Mehr Threads sind messbar wirkungslos bis leicht schädlich.** Das erklärt, warum `GOBLIN_IMPORT_CONCURRENCY=3` den Durchsatz nicht steigert. Nur `fingerprint` in einen Prozesspool auszulagern wäre wirksam (Ein-/Ausgabe sind Pfad und kompakte Signatur, also billig zu übertragen). Zu beachten: höherer Speicherbedarf — der aktuelle Peak liegt bei 539 MiB, und ein Prozesspool vervielfacht ihn. Nach Vorschlag 1 und 2 schrumpft die verbleibende CPU-Zeit so stark, dass dieser Punkt neu bewertet werden sollte.

### 4. Parallelitätsfehler beheben (Korrektheit, nicht Geschwindigkeit)

`backend/repository.py:105-107` liest den Autor und legt ihn ohne Transaktionsschutz neu an; `backend/imports.py:595-597` schreibt ihn. Bei gleichzeitigem Import zweier Bücher desselben Autors schlägt `INSERT` mit `UNIQUE constraint failed: authors.name` fehl, der Lauf endet in `backend/imports.py:501-504` als `failed`, und die Staging-Datei wird gelöscht — **die Datei geht verloren**. Genau das trat im ersten Piloten mit drei Arbeitern auf. Dasselbe Muster betrifft `get_or_create_genre` (`repository.py:110-112`) und `get_or_create_tag` (`repository.py:115-126`).

Abhilfe: `INSERT … ON CONFLICT DO NOTHING` mit anschließendem erneuten `SELECT`, oder ein Savepoint mit Retry. **Diese Korrektur ist Voraussetzung dafür, Parallelität überhaupt wieder einzuschalten.**

### 5. Doppelte Arbeit vermeiden — kleiner, aber billig

- Jede Datei wird **zweimal geparst**: einmal für Metadaten und Cover (`backend/imports.py:426`), einmal für den Volltext (`imports.py:446`). Der Reader wird nicht wiederverwendet (`backend/extractors.py:67-70`). Gemessen entfallen auf `metadata_and_cover` 9,6 s / 6,1 %; ein Teil davon ist einsparbar.
- `text_relation_paths` (`backend/duplicates.py:130-131`) extrahiert **beide** Dateien pro Kandidat erneut. Im leeren Archiv kaum sichtbar (`candidate_search_and_comparison` nur 0,5 s), **bei 10.000 Bestandsbüchern aber potenziell dominant**. Die linke Seite ist bereits extrahiert und sollte übergeben werden.
- Der Review-Pfad wiederholt Hash, Extraktion und Fingerprint vollständig (`backend/previews.py:222-226`), obwohl der Import die Werte schon berechnet hat. Hier nur 0,3 s, weil es genau einen Prüffall gab.
- `1.430 SQL-Abfragen` für 43 Dateien sind etwa 33 pro Datei. Ursachen laut Codeanalyse: `lazy="selectin"` auf drei Relationen (`backend/models.py:89-91`) und ein N+1 in `backend/duplicates.py:274`. Zeitlich hier irrelevant (0,3 %), **aber die Kandidatenmenge wächst mit dem Bestand** — vor dem 10k-Benchmark prüfen.

### Neubewertung: kein Bestandsarchiv vorhanden

Der Nutzer hat bestätigt, dass **noch kein Archiv aufgebaut** wurde. Damit entfällt der Migrationsaufwand, der oben gegen Vorschlag 1 sprach: Es gibt keine bestehenden `text_hash`-, Shingle- oder Signaturwerte, die neu berechnet werden müssten, und keine gespeicherten Duplikatbeziehungen, die sich ändern könnten. Eine Änderung der Analyseversion ist jetzt **kostenlos** und sollte **vor** dem Aufbau des Bestands erfolgen — danach wird sie teuer.

Das verschiebt die Priorität: **Vorschlag 1 wird von „heikel“ zu „jetzt erledigen“.**

Eine Nachmessung aller 18 PDFs des Pakets zeigt zudem einen Befund, der mit reiner Zeitbetrachtung übersehen wurde. Rohdaten: [extractor-pypdf.json](diagnostics/extractor-pypdf.json), [extractor-pdfium.json](diagnostics/extractor-pdfium.json).

**11 der 18 PDFs sind verschlüsselt** (leeres Owner-Passwort). `backend/duplicates.py:75-76` bricht bei `reader.is_encrypted` ab, daher liefern sie heute `fingerprint.status = "unavailable"` mit 0 Wörtern. Insgesamt haben **22 von 43 Dateien** keinen vollständigen Fingerprint: 11 verschlüsselte PDFs, 2 MOBI (keine Textextraktion implementiert), 8 Dateien über dem Shingle-Limit von 150.000 Wörtern, 1 Duplikat. **Die bisher gemessenen 165 s erkaufen sich also teilweise dadurch, dass die Hälfte der Dateien gar nicht inhaltlich analysiert wird.** Ein Vergleich rein über die Laufzeit wäre hier irreführend.

`pypdfium2` entschlüsselt diese Dateien ohne Passwort und extrahiert sie:

| Kennzahl | `pypdf` (heute) | `pypdfium2` |
| --- | ---: | ---: |
| Extraktionszeit, alle 18 PDFs | 772,1 s | **48,3 s** (16,0×) |
| Extraktionszeit, die 7 heute analysierten | 176,1 s | **14,5 s** (12,2×) |
| Erfolgreich analysierte PDFs | 7 | **18** |
| Zusätzlich gewonnener Text | — | **11,9 Mio. Zeichen** |

Bei den nicht verschlüsselten Dateien extrahiert pdfium **mehr** Text (+0,2 % bis +9,4 %, einmal −0,8 %) — die Texte sind also nicht identisch, was ohne Bestand aber folgenlos ist.

**Projektion für dieses Paket** (aus Phasendaten hochgerechnet, nicht per A/B belegt):

| Szenario | erwartete Laufzeit | Analysierte PDFs |
| --- | ---: | ---: |
| Ist-Zustand | 157,5 s (Diagnoselauf) | 7 von 18 |
| nur `pypdfium2` | ~119 s | **18 von 18** |
| `pypdfium2` + NumPy-Signatur | **~67 s (−57 %)** | **18 von 18** |

Bemerkenswert: Die Variante ist trotz **deutlich mehr fachlicher Arbeit** klar schneller. Die zusätzliche Extraktion der 11 bisher übersprungenen PDFs kostet 33,8 s und deren Signaturbildung grob 19 s — genau diesen Zuwachs fängt die NumPy-Signatur wieder auf. Die beiden Änderungen gehören deshalb zusammen.

**Vorschlag 2 wurde an echten Daten verifiziert**, nicht nur an Zufallswerten: Für alle 15 EPUB/FB2-Dateien mit Signatur ist die NumPy-Variante **bitgenau identisch** (15/15), bei 20,39 s → 0,70 s, Faktor **29,2**.

Offene Punkte vor der Umsetzung: `pypdfium2` ist eine neue Abhängigkeit (Apache-2.0/BSD-3, gebündeltes PDFium-Binary) und muss in `pyproject.toml` fixiert werden. Das automatische Entschlüsseln geschützter PDFs sollte eine bewusste Produktentscheidung sein. Die 2 MOBI-Dateien und das 150.000-Wörter-Limit bleiben unabhängig davon offen.

### Nicht empfohlen

`synchronous=NORMAL` oder gebündelte Commits: `archive_database_fts` kostet gemessen nur **1,0 s / 0,7 %**. Eine Lockerung der Haltbarkeitsgarantien wäre hier nicht zu rechtfertigen.

### Reihenfolge (aktualisiert, da kein Bestand existiert)

1. **Vorschlag 1 + 2 gemeinsam** (`pypdfium2` und NumPy-Signatur): zusammen ~57 % kürzere Laufzeit bei **deutlich besserer Analyseabdeckung** (18 statt 7 PDFs). Solange kein Archiv existiert, ist der Analyseversionswechsel kostenlos — das ist das Zeitfenster dafür.
2. **Vorschlag 4** (Autoren-Race): Voraussetzung dafür, Parallelität überhaupt einzuschalten.
3. **Vorschlag 3** (Prozesspool) und **5** (doppelte Arbeit): danach neu bewerten. Nach Schritt 1 ist die CPU-Zeit so weit gesunken, dass sich ein Prozesspool womöglich nicht mehr lohnt.

Anschließend eine neue Referenzmessreihe erheben und **erst danach** den Bestand aufbauen. Alle Zahlen gelten für dieses 43-Dateien-Paket auf leerem Archiv; bei 10.000 Bestandsbüchern verschiebt sich das Gewicht voraussichtlich Richtung Duplikatsuche und Vorschlag 5.

## Einzelmessungen

| Lauf | Revision | Importzeit (s) | Backend-RSS-Peak (MiB) | Status | Daten |
| --- | --- | ---: | ---: | --- | --- |
| 01_ref | A | 165.433910523 | 545.1 | completed | [Rohdaten](runs/01_ref/run.json) |
| 02_ref | A | 163.005488104 | 538.3 | completed | [Rohdaten](runs/02_ref/run.json) |
| 03_ref | A | 177.919529022 | 538.9 | completed | [Rohdaten](runs/03_ref/run.json) |

## Reproduktion

[Maschinenlesbare Kennzahlen](summary.json) · [Umgebung](environment.json) · [Protokoll](protocol.json) · [Datensatz](dataset.json) · [Laufdaten](runs/)

Siehe `series.json` für Revisionen und Ausführungsmodus. Die Auswertung wurde ausschließlich aus gespeicherten Laufdaten berechnet.

Paket und Buchinhalte liegen lokal unter `benchmarks/.local/example43-serial-bundle/` (nicht versioniert). `dataset.json` enthält die Datensatzkennung und Batchliste; das versiegelte Paket enthält Eingabe- und Snapshot-Manifeste, Dateihashes, Protokoll und die fachlichen Erwartungen. Konfiguration: `benchmarks/.local/example43-serial-config.json`; Laufarchive wurden nur unter `benchmarks/.local/run-archives/` angelegt. Die drei Laufordner enthalten `uploads.jsonl`, `completions.jsonl`, `resources.jsonl`, `validation.json`, `items.json`, `run.json` und `backend.log`.

Ausführen und offline erneut auswerten:

```bash
.venv/bin/python benchmarks/tools/benchmark_import.py verify --bundle benchmarks/.local/example43-serial-bundle
.venv/bin/python benchmarks/tools/benchmark_import.py baseline --bundle benchmarks/.local/example43-serial-bundle --revision 78f780b3c91622aa24934fab21303da01500f747 --smoke-runs 3 --output benchmarks/results/<NEUE-KENNUNG>
.venv/bin/python benchmarks/tools/benchmark_import.py report --results benchmarks/results/2026-10-02T11-42-36Z-78f780b-dirty
```

Der Smoke-Modus liefert keine gesonderten Phasen-/Leselastdiagnosen und nur drei statt zehn Messläufe. Aus diesem Lauf lassen sich weder Importzeiten bei 10.000 Bestandsbüchern noch eine Wirkung von Codeänderungen ableiten. Nächster Schritt: Parallelitätskonflikt im Produkt untersuchen und für den 10k-Benchmark einen echten Bestand mit 10.000 archivierten Büchern bereitstellen.
