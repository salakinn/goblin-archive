# Performanceaudit Backend und Frontend

Status: Messung am 28.09.2026 gegen `backend/` und `frontend/src`.
Redaktionell am 28.09.2026 mit dem aktuellen Code abgeglichen. Ältere
Zeilenangaben im Messprotokoll können durch spätere Änderungen, etwa die
Admin-Anmeldung in `main.py`, verschoben sein. Die Messwerte
sind historische Benchmark-Ergebnisse, keine erneute Messung nach Einführung
der Admin-Anmeldung oder der inzwischen umgesetzten Performance-Schritte.
Paginierung, Indizes, Upload-Auslagerung, Refresh-Zusammenführung und der
Filter-Cache sind im aktuellen Code umgesetzt; die historischen Tabellen
zeigen weiterhin den Vorher/Nachher-Benchmark aus der ursprünglichen Messung.
Testumgebung: 4 Kerne, 7 GiB RAM, Python 3.14.7, SQLAlchemy 2.1.1,
SQLite 3.53.4, WAL. Die Datenbank lag für die SQL-Benchmarks auf tmpfs;
der Upload-Test nennt gesondert eine SSD. Absolute Zeiten lassen sich
nicht auf SSD oder einen TrueNAS-Pool übertragen: Speichermedium, Cache,
CPU, konkurrierende Dienste und Datenverteilung können sie verändern.
Auch die Verhältnisse zwischen Varianten gelten zunächst für diese Testdaten.
Alle Angaben sind Median aus mindestens fünf Läufen mit frischer Session
pro Lauf.

Die Zahlen stammen aus einer synthetischen Bibliothek mit 1.000, 5.000,
20.000 und 100.000 Büchern. Der Abschnitt „100.000 Bücher“ ist der
Referenzfall, weil 100.000 Bücher das Ziel für den Produktivbetrieb sind.

## Ziel 100.000 Bücher

### Umsetzungsstand

Die fünf priorisierten Maßnahmen sind inzwischen umgesetzt: `GET /api/books`
liefert standardmäßig 50 Bücher mit `limit`, `offset` und `total`, die
Sortier- und Beziehungstabellen sind indiziert, konkurrierende UI-Requests
werden abgebrochen und Importabschlüsse zusammengeführt, Uploads werden in
Worker-Threads geschrieben und auf 200 MiB beziehungsweise 20 Dateien
begrenzt. Die Filteroptionen werden für 60 Sekunden gecacht und bei den
relevanten Mutationen invalidiert. Die Werte darunter sind noch keine neue
Messung dieser Version.

**Kurzurteil: Die Messung spricht dafür, dass 100.000 Bücher mit SQLite
erreichbar sind.** Die synthetische Datenbank brauchte 157 MiB inklusive
aller Indizes und des Volltextindex. Bei der getesteten Paginierung lag
die Buchliste im zweistelligen Millisekundenbereich, die Volltextsuche bei
bis zu 34 ms. `filter-options` blieb dagegen ohne Cache bei 1,46 s.
Der größte Engpass im Lesepfad ist, dass `list_books` die ganze Bibliothek
materialisiert. Die Werte sind keine garantierten Antwortzeiten für TrueNAS.

| Abfrage bei 100.000 Büchern | Ist | Nach Paginierung + Indizes |
| --- | ---: | ---: |
| `GET /api/books` Seite 1 | 10,26 s | 9,1 ms |
| `GET /api/books` Seite 1001 (Offset 50.000) | — | 11,3 ms |
| `GET /api/books` Seite 1999 (Offset 99.900) | — | 15,1 ms |
| Antwort-Body einer Seite | 53,0 MiB | 27 KiB |
| Speicherbedarf einer Anfrage | 620 MiB | 71 MiB |
| `GET /api/books?tag=fantasy` | 1,40 s | 14,0 ms |
| `COUNT(*)` für die getestete Paginierung | — | 2,2 ms |
| `GET /api/search` | 33 ms | 33 ms |
| `GET /api/books/{id}` | 2,0 ms | 2,0 ms |

Der Sprung liegt bei **1127-fach** für die Hauptliste und **11-fach** beim
Speicher. Die vier Indizes kosten zusammen 0,4 Sekunde Anlegezeit und
5 MiB zusätzlichen Speicher.

### Der eigentliche Blocker ist Nebenläufigkeit, nicht Latenz

10,26 Sekunden für eine Anfrage sind lästig. Der Grund, warum 100.000 Bücher
sofort und nicht erst bei einer Lastspitze zum Problem werden, ist das
Verhalten unter Parallelität. FastAPI führt die synchronen Endpoints
(`def books(...)`, `main.py:164`) im anyio-Threadpool aus, dessen Default
40 Threads beträgt:

| parallele Vollabfragen | Gesamtdauer | RSS-Spitze | je Anfrage |
| ---: | ---: | ---: | ---: |
| 1 | 10,26 s | 620 MiB | 10,3 s |
| 2 | 30,12 s | 1178 MiB | 15,1 s |
| 3 | 51,47 s | 1741 MiB | 17,2 s |
| 4 | 89,86 s | 2306 MiB | 22,5 s |

Das ist **superlineares** Verhalten: vier Anfragen dauern 8,8-mal so lange
wie eine, statt viermal. GIL-Konkurrenz bei der Objektmaterialisierung
und Speicherdruck sind plausible Ursachen, aber hier nicht getrennt
gemessen. Bei vier gleichzeitigen Anfragen wurden 2,3 GB RSS beobachtet;
weitere gleichzeitige Vollabfragen erhöhen das OOM-Risiko deutlich.
Das Frontend ruft `loadBooks()` bei Filteränderungen und nach bestimmten
Mutationen auf. Bei Import-SSE-Ereignissen geschieht das nur für
`import.finished` und `import.duplicate`, nicht für jedes Zwischenereignis.

Mit Paginierung lag der Speicherbedarf im Test bei 71 MiB für den gesamten
Prozess, und die Abfragezeit sank von 10,26 s auf 9,1 ms. Das beseitigt
den hier gemessenen Hauptengpass ohne Änderung des Buch-Datenmodells.
Parallelität und UI-Verhalten wurden für die paginierte Variante noch
nicht in derselben Breite nachgemessen.

### Was nach der Paginierung der neue Engpass ist

`list_filter_options()` kostet bei 100.000 Büchern **1463 ms**. Das
Frontend ruft es beim Start sowie nach Importabschluss und Mutationen
auf, nicht bei reinen Filterwechseln (`App.tsx:322, 337, 356, 408, 470`).
Einzelne Bestandteile:

| Bestandteil | Ist |
| --- | ---: |
| `years` (`GROUP BY publication_year`) | 287,5 ms |
| `_book_value_counts(publisher)` | 235,2 ms |
| `_language_counts()` | 224,7 ms |
| `list_authors()` | 202,4 ms |
| `_book_value_counts(format)` | 195,2 ms |
| `_book_value_counts(series)` | 140,6 ms |
| `tags` (outerjoin auf `tag_id`) | 19,6 ms |

Query-Tuning bringt hier nichts. Ein Test ohne `trim(publisher) != ''` und
mit Sortierung in Python statt `COLLATE NOCASE` sparte 8 Prozent
(240,7 ms auf 221,2 ms). Der Plan zeigt:

```
SEARCH books USING INDEX ix_books_publisher (publisher>?)
```

Der Index wird bereits für die Gruppierung genutzt, es fehlt keine
temporäre B-Tree-Struktur mehr. Die verbleibenden ~200 ms je Spalte sind
der Aufwand, 100.000 Indexeinträge zu durchlaufen, und der ist intrinsisch.

Falls dieser Abruf im Produktivbetrieb störend ist, kann ein Cache helfen:
Die Werte ändern sich durch Buch- und Tagmutationen, nicht durch
Filterklicks. Zuerst sollten aber Anzahl und Größe der zurückgegebenen
Filterwerte in einer realen Bibliothek gemessen werden.
Ein serverseitiger Cache kann wiederholte Berechnungen vermeiden. Er muss
nach jeder Mutation relevanter Buch- oder Tagfelder invalidiert werden:
Import einschließlich Übersetzungs-Ergebnis, Tag-Änderung, ISBN-Anwendung,
Spracherkennung und Archiv-Löschung. Ein Prozesscache gilt nur pro Worker;
bei mehreren Workern wäre ein gemeinsamer Versionszähler oder eine andere
prozessübergreifende Strategie nötig. Der erste Aufruf nach einem Neustart
bleibt ohne Vorwärmen bei etwa 1,5 Sekunden. Ein Cache-Hit würde zusätzlich
weiterhin JSON-Serialisierung und Netzwerkübertragung kosten; „praktisch null“
beschreibt nur den entfallenden Datenbankteil.

### Korrektur zur Volltextsuche

Die erste Fassung dieses Abschnitts nannte für `GET /api/search` bei
100.000 Büchern 4,4 ms. **Diese Zahl war falsch.** Die Testdatenbank enthielt
im `books_fts`-Index nur 100 statt 100.000 Zeilen, weil die Seed-Routine
jedes tausendste Buch indiziert hatte. Gemessen wurde also eine
100-Zeilen-Tabelle.

Nach vollständigem Aufbau des Index (100.000 Zeilen, 53,6 Sekunden bei
1865 Büchern pro Sekunde, `integrity-check` fehlerfrei):

| Suchbegriff | `search_books` | reine FTS-Abfrage | Treffer |
| --- | ---: | ---: | ---: |
| `"nacht"*` | 32,9 ms | 21,2 ms | 100 |
| `"atlas"*` | 30,7 ms | — | 100 |
| `"Fantasy"*` | 34,1 ms | — | 100 |
| `"der"*` | 32,1 ms | — | 100 |
| `"der" AND "saga"` | 17,1 ms | 6,0 ms | 100 |
| `"koenig" AND "turm" AND "garten"` | 16,9 ms | — | 100 |
| `"der" AND "die" AND "das" AND "und" AND "mit"` | 2,3 ms | 0,5 ms | 0 |

Der Query-Plan bleibt `SCAN books_fts VIRTUAL TABLE INDEX 32:M7`, also
reguläre Index-Nutzung von FTS5 ohne unerwarteten Scan. Die rund 21 ms der
reinen FTS-Abfrage entfallen auf `ORDER BY rank`; der Sprung auf 33 ms
kommt durch den ORM-Nachlader, der bis zu 100 Bücher mit je drei
Relationships lädt. Weil `LIMIT 100` in `repository.py:262` greift, ist
das Ergebnis nach oben begrenzt: auch der häufigstedeutsche Artikel
bleibt bei 32 ms.

33 ms für eine Suche ist damit unkritisch, aber die Aussage „FTS5 liegt
im Millisekundenbereich“ aus der 20.000-Bücher-Messung galt nur für
kleinere Bibliotheken und ist für 100.000 Bücher zu revidieren.

### Unkritisch bei 100.000 Büchern

- **Datenbankgröße** 157 MiB mit allen Indizes und vollständigem
  Volltextindex. Bei 100.000 Büchern sind das 1,6 KiB Metadaten pro Buch.
  Davon entfallen rund 22 MiB auf `books_fts`.
- **FTS5-Volltextsuche** 0,5 bis 34 ms, siehe die Korrektur unten.
- **Einzelbuchabruf** 2,0 ms.
- **`clear_archive()`** braucht 16,3 s für 100.000 Bücher und läuft über
  `asyncio.to_thread`, blockiert also den Event-Loop nicht. Der Platz wird
  korrekt wieder freigegeben, der Prüfung nach liegt `freelist_count` bei
  0 und die Datei schrumpft von 136 MiB auf 236 KiB. Nur der belegte
  Worker-Thread ist 16 Sekunden lang belegt; für einen bewusst ausgelösten
  Komplettlöschvorgang ist das in Ordnung.
- **Offset-Paginierung** ist bei 100.000 Büchern unkritisch: die tiefste
  Seite (Offset 99.900) kostet 15,1 ms. Erst weit darüber lohnt der
  Wechsel auf Keyset-Paginierung.

## Überblick

| Schweregrad | Befund | Ist | Mit Fix |
| --- | --- | --- | --- |
| Hoch | `list_books` ohne Paginierung | 2250 ms / 10,5 MiB | 7 ms |
| Hoch | Kein Index auf `books(imported_at)` | `SCAN` + Sortierung | `SCAN USING INDEX` |
| Hoch | Frontend lädt die ganze Bibliothek bei jedem Filterwechsel | 2250 ms je Ereignis | Seite statt Gesamtliste |
| Mittel | `list_filter_options` nach Start und Datenmutationen | 272 ms | Cache prüfen |
| Mittel | Fehlende Indizes auf Assoziationstabellen | `AUTOMATIC COVERING INDEX` | echter Index |
| Mittel | `selectinload` chunkt in 500er-Blöcken | 121 Queries | mit 50er-Seiten etwa 4 Queries |
| Mittel | Blockierende Schreibzugriffe im Event-Loop | 18 ms Stall | 0 ms |
| Niedrig | `TranslationJob.data_json` Vollschreiben je Batch | bis 3,5 MiB | dauerhaftes Teilupdate prüfen |
| Niedrig | Dateien innerhalb eines Importauftrags laufen sequenziell | additiv | global begrenzte Parallelität prüfen |

## Skalierung des Lesepfads

`GET /api/books` ohne Filter, vollständige JSON-Ausgabe:

| Bücher | `list_books` | Queries | Antwort-Body | Bytes/Buch |
| ---: | ---: | ---: | ---: | ---: |
| 1.000 | 68 ms | 7 | 0,52 MiB | 547 B |
| 5.000 | 365 ms | 31 | 2,62 MiB | 549 B |
| 20.000 | 1630 ms | 121 | 10,51 MiB | 551 B |

Die Queryzahl folgt exakt `1 + 3 × ceil(N / 500)`. Das ist kein Zufall:
SQLAlchemy lädt `selectin`-Relationships in Blöcken von 500, und
`models.py:85-87` setzt auf allen drei Beziehungen `lazy="selectin"`.

## Hoch

### 1. `list_books` hat keine Paginierung

`repository.py:223-253` und `main.py:164-191` liefern die komplette Bibliothek
in einem Rutsch. `api.ts:15` ruft genau das auf, ohne `limit`- oder
`offset`-Parameter.

Bei 20.000 Büchern: **2250 ms** und **10,5 MiB** pro Anfrage. Jeder
Requestserialisiert die ganze Sammlung erneut, auch wenn der Nutzer nur eine
Autorin filtern wollte.

Die Zeit zerfällt so (20.000 Bücher, ohne selectin-Ladung):

| Schritt | Zeit | Anteil |
| --- | ---: | ---: |
| Reines SQL, `SELECT *`, `fetchall` | 192 ms | 12 % |
| ORM-Materialisierung der 20.000 Objekte | +1382 ms | 80 % |
| `selectin`-Nachlader (90 Queries) | +291 ms | — |
| `book_to_dict` + `json.dumps` | 532 ms | 30 % |

Bemerkenswert: der reine SQL-Anteil ist vernachlässigbar. Der Flaschenhals
ist die Materialisierung von 20.000 ORM-Objekten samt Identity-Map.

**Wichtig:** Eine naheliegende Gegenmaßnahme bringt nichts. `defer()` auf
`metadata_json`, `work_match_json`, `description`, `library_path` und
`cover_path` wurde gemessen und ergab **0 %** Einsparung (1374,9 ms gegen
1377,3 ms). Die breiten Spalten sind nicht der Engpass, die Objektanzahl schon.
Wer ohne Paginierung auskommen muss, muss am ORM vorbei aus einer
Raw-SQL-Query direkt Dictionaries bauen.

**Empfehlung:** `limit`/`offset` als Query-Parameter mit Default 50 und
festem Maximum; Gesamtzahl für dieselben Filter separat zählen. Den
Sortierschlüssel `(imported_at DESC, id DESC)` stabil machen, damit Bücher
mit gleicher Importzeit nicht zufällig zwischen Seiten wechseln. Für
aktive Änderungen zwischen zwei Seitenaufrufen kann Offset-Paginierung
trotzdem Einträge doppelt zeigen oder überspringen; das ist für die
Bibliotheksansicht voraussichtlich hinnehmbar. Der gemessene `COUNT(*)`-Wert
gilt für die getestete Abfrage, nicht automatisch für jeden komplexen
Filter. Gemessen: 21,9 ms statt 2250 ms, mit Index aus Punkt 2 sogar 6,9 ms.

### 2. Kein Index auf `books(imported_at)`

`models.py:54-61` legt Indizes für Jahr, Sprache, Verlag, ISBN, Format und
Serie an, aber nicht für das Sortierkriterium von `list_books`
(`repository.py:236`, `order_by(Book.imported_at.desc())`).

```
EXPLAIN QUERY PLAN SELECT * FROM books ORDER BY imported_at DESC
  -> SCAN books
  -> USE TEMP B-TREE FOR ORDER BY
```

Jede Listenanfrage liest die gesamte Tabelle und sortiert das Ergebnis
zusätzlich in einer temporären B-Tree-Struktur. Der Filter `?year_from=`
nutzt immerhin `ix_books_publication_year`, braucht danach aber weiterhin
dieselbe temporäre Sortierung.

**Empfehlung:** einen Index passend zur stabilen Sortierung, etwa
`Index("ix_books_imported_id", Book.imported_at.desc(), Book.id.desc())`, in
`__table_args__` aufnehmen und analog in `database.py:57` als
`CREATE INDEX IF NOT EXISTS` für bestehende Installationen migrieren.
Damit entfällt die temporäre B-Tree-Struktur komplett:

```
EXPLAIN QUERY PLAN SELECT * FROM books ORDER BY imported_at DESC LIMIT 50
  -> SCAN books USING INDEX ix_books_imported_id   (erwartet, neu zu prüfen)
```

Die gemessene 324-fache Beschleunigung, 2250 ms auf 6,9 ms, stammt aus
Paginierung plus einem Index auf `imported_at`. Der vorgeschlagene
zusätzliche `id`-Sortierschlüssel muss gesondert mit `EXPLAIN` und einem
Benchmark überprüft werden.

### 3. Das Frontend lädt bei Filterwechseln und Importabschlüssen die ganze Bibliothek neu

`App.tsx:321` entprellt `loadBooks` um 180 ms, was die Tastatureingabe
abdeckt. Der Abruf selbst lädt aber trotzdem alles. Zusätzlich wird
`loadBooks()` erneut ausgelöst bei

- jeder Filteränderung (`App.tsx:343`),
- den SSE-Ereignissen `import.finished` und `import.duplicate`
  (`App.tsx:336`) sowie dem Abschluss der Import-Pollingroutine,
- jedem Aktualisieren einer Buchdetailansicht (`App.tsx:469`).

`loadFilterOptions()` läuft beim Mounten der App sowie bei den genannten
Importabschlüssen und einigen Mutationen, aber **nicht** bei jedem
Filterwechsel (`App.tsx:322, 337, 356, 408, 470`).

Bei 20.000 Büchern bedeutet das: 2,25 s Wartezeit und 10,5 MiB Transfer pro
Filterklick, während der Nutzer nur eine Liste von zehn Werten sieht. Der
Import eines einzigen Buches kann durch `import.finished` und durch die
Pollingroutine zwei vollständige Neuabrufe auslösen. Zwischenereignisse
laden nur den jeweiligen Importauftrag nach.

**Empfehlung:** Paginierung im Frontend umsetzen und `loadBooks` an die
geänderte Seite koppeln. Nach Importabschluss reicht zunächst ein einziger
Neuabruf der aktuellen Seite, damit Filter und Sortierung korrekt bleiben;
SSE- und Polling-Abschluss sollten dazu zusammengeführt werden. Ein
Delta-Update der betroffenen Karte wäre erst nach einer Prüfung von
Filterung, Sortierung und Seitenwechseln sinnvoll. `loadFilterOptions`
ist bereits von reinen Filteränderungen entkoppelt.

## Mittel

### 4. `list_filter_options` kostet 272 ms bei Aufruf

`repository.py:128-165` führt sieben Aggregatabfragen aus. Vier davon
groupen über die gesamte `books`-Tabelle:

| Bestandteil | Zeit |
| --- | ---: |
| `_language_counts()` | 52,5 ms |
| `_book_value_counts(publisher)` | 51,1 ms |
| `years` (`GROUP BY publication_year`) | 58,5 ms |
| `_book_value_counts(format)` | 36,5 ms |
| `_book_value_counts(series)` | 30,8 ms |
| `list_authors()` | 13,4 ms |
| `tags` (outerjoin auf `tag_id`) | 5,3 ms |
| **Gesamt** | **272,1 ms** |

Die Abfragen nutzen die vorhandenen Indizes, scheitern aber an
`WHERE trim(spalte) != ''` (Funktion auf der Spalte) und
`ORDER BY spalte COLLATE NOCASE` (Sortierung kann keinen Index verwenden).
Ergebnis ist `USE TEMP B-TREE FOR ORDER BY` je Abfrage.

Nach Anlage der fehlenden Indizes aus Punkt 5 sinkt der Gesamtwert auf
245 ms, weil die Sortierung und der Join-Ansatz dann stimmen; die
Gruppierung über 20.000 Zeilen bleibt der Restposten.

**Empfehlung:** Bei nachgewiesener Last die Antwort serverseitig
zwischenspeichern und bei allen relevanten Buch- und Tagmutationen
invalidieren. Die Zahlen ändern sich nicht durch Filterklicks.

### 5. Fehlende Indizes auf den Assoziationstabellen

`models.py:11-30` definiert die drei Verknüpfungstabellen nur mit dem
zusammengesetzten Primärschlüssel `(book_id, tag_id)`. Die umgekehrte
Richtung ist nicht indiziert, wird aber gebraucht:

```
SELECT count(*) FROM book_tags WHERE tag_id=1          -- repository.py:213
  -> SCAN book_tags

SELECT authors.id, count(book_authors.book_id) ...     -- repository.py:104
  -> SEARCH book_authors USING AUTOMATIC COVERING INDEX (author_id=?)
```

`AUTOMATIC COVERING INDEX` heißt, dass SQLite die Anfrage zur Laufzeit einen
temporären Index baut. Das geschieht bei jedem Aufruf von `/api/authors` und
`/api/filter-options` neu. Betroffen sind auch `book_genres(genre_id)`,
obwohl `genres` im aktuellen UI nicht mehr befüllt wird.

**Empfehlung:** Sekundärindizes auf `book_authors(author_id)`,
`book_tags(tag_id)` und `book_genres(genre_id)` anlegen. Gemessene Wirkung
für `book_tags(tag_id)`:

```
  -> SEARCH book_tags USING COVERING INDEX ix_book_tags_tag (tag_id=?)
```

### 6. `selectinload` erzeugt 121 Queries bei 20.000 Büchern

Folge von Punkt 1 in Verbindung mit `models.py:85-87`. Bei 5.000 Büchern
sind es 31, bei 20.000 bereits 121 Queries. Mit Paginierung verschwindet das
Problem automatisch, weil die Blockgrenze von 500 dann nicht erreicht wird.
Falls eine große Liste tatsächlich einmal gebraucht wird, hilft
ein separat entworfener Exportpfad mit Streaming oder manuellen Batches.
Ein Chunk-Size-Wert ist keine belegte Option von
`selectinload(...).options(...)`; dieser Vorschlag entfällt.

### 7. `find_by_hash` lädt die ganze Zeile für eine Duplikatprüfung

`repository.py:219-220` macht `select(Book)` ohne Spaltenliste. Wegen
`lazy="selectin"` auf allen Beziehungen löst das **vier** Queries aus, obwohl
`imports.py:160` ausschließlich `duplicate.id` braucht. `metadata_json` und
`description` werden mitgeladen und verworfen.

Bei 20.000 Büchern: 1,4 ms statt 0,4 ms. Absolut gering, aber pro importierter
Datei und ohne Nutzen.

**Empfehlung:** `select(Book.id).where(Book.sha256 == sha256)`; die
Skalarabfrage lädt weder die breite Buchzeile noch Relationships. Der
Aufrufer in `imports.py` muss dann die ID direkt verwenden.
`session.get` passt hier nicht, weil nach SHA-256 und nicht nach dem
Primärschlüssel gesucht wird. `noload` ist für eine reine Spaltenabfrage
ebenfalls unnötig.

### 8. Blockierende Schreibzugriffe im Event-Loop

`main.py:598-602` ist ein `async def`, führt aber `staging_path.open("xb")`
und `target.write(chunk)` direkt im Event-Loop aus. Für einen 256-MiB-Upload
gemessen:

```
256 MiB staging-Schreiben: 333 ms   (echte SSD)
erwartete 5-ms-Ticks:      67
beobachtete Ticks:        44   -> 23 ausgefallen
max. Blockierung:          18,0 ms
95. Perzentil:             16,8 ms
```

Während des Schreibens kommen alle anderen Requests und die SSE-Stream-Verarbeitung
(`imports.py:83-94`) bis zu 18 ms zum Stillstand, über die volle Dauer des
Uploads gestotet. Auf tmpfs war der Effekt geringer (max. 9,5 ms), die
festplattenabhängige Komponente ist also der eigentliche Faktor.

Der Rest des Importpfads ist korrekt: `sha256_file`, `detect_and_extract`
und `_archive` laufen über `asyncio.to_thread` (`imports.py:157,169,201`).

**Empfehlung:** Den synchronen Dateischreibteil außerhalb des Event-Loops
ausführen, beispielsweise pro Block mit `await asyncio.to_thread(...)`.
Dabei Dateihandle, Fehlerbereinigung und Abbruch bei Client-Trennung
sauber behandeln. Gleichzeitig das Upload-Limit aus dem Sicherheitsaudit
einführen. Diese Änderung anhand der Event-Loop-Latenz auf dem Zielsystem
prüfen, nicht nur anhand des Upload-Durchsatzes.

## Niedrig

### 9. `TranslationJob.data_json` wird je Batch vollständig neu geschrieben

`translation.py:269-274` serialisiert den gesamten Auftrag, also alle
Segmente, alle Entwürfe und alle Übersetzungen, bei jedem `_save()`.
Im regulären Batch-Pfad wird vor der Auswertung der KI-Antwort der neue
Kostenstand gespeichert (`translation.py:457`) und danach der übersetzte
Batch (`translation.py:477`): bis zu zwei Vollschreibvorgänge je Batch.
`translation.py:420` fasst höchstens 8 Segmente beziehungsweise 12.000
Zeichen zusammen. Bei 300 Segmenten und drei Stufen wären es etwa
`3 × ceil(300/8) = 114` Batches, wenn acht Segmente stets hineinpassen;
bei sehr langen Segmenten können es bis zu 900 sein. Die bisherige Angabe
„rund 900 Batches“ war als allgemeine Schätzung falsch.

| Fortschritt | `data_json` | `json.dumps` | DB-Update | Summe |
| ---: | ---: | ---: | ---: | ---: |
| 1/300 | 1188 KiB | 4,4 ms | 2,0 ms | 6,4 ms |
| 50/300 | 1573 KiB | 6,2 ms | 2,9 ms | 9,1 ms |
| 150/300 | 2359 KiB | 9,0 ms | 5,1 ms | 14,1 ms |
| 300/300 | 3539 KiB | 14,2 ms | 3,8 ms | 18,0 ms |

Die Tabellenwerte belegen eine zunehmende Kostenkurve je Vollschreiben.
Die bisherige Hochrechnung auf rund 10 s pro Buch ist ohne die tatsächliche
Verteilung von Segmentlängen und Batchgrößen nicht belastbar. Eine einzelne
TEXT-Spalte erreichte im Test 3,5 MiB; die wiederholte Serialisierung
und das anschließende erneute Einlesen verdienen eine eigene Messung.

**Empfehlung:** Erst mit realistischen Büchern Anzahl und Größe der
Vollschreibvorgänge sowie deren Anteil an der Gesamtdauer messen. Falls
relevant, unveränderliche Segmentdaten und veränderliche Ergebnisse
getrennt speichern, etwa Batch-Ergebnisse in einer eigenen Tabelle.
Jeder erfolgreich abgeschlossene Batch muss dauerhaft gespeichert bleiben,
damit Pause, Neustart und Wiederaufnahme funktionieren. Ein bloßes Update
der Statusfelder zwischen Start und Abschluss wäre dafür unzureichend.

### 10. Dateien eines Importauftrags laufen sequenziell

`imports.py:150-151` verarbeitet die Dateien **innerhalb eines Auftrags**
in einer `for`-Schleife mit `await`. Unterschiedliche Aufträge starten
bereits als eigene Tasks (`imports.py:128`) und können gleichzeitig laufen.
Pro Datei folgen Hash, Extraktion, externe Metadaten- und Cover-Abfragen
sowie Archivierung. Wenn Provider langsam sind, kann Wartezeit dominieren;
ein gemessener End-to-End-Vergleich für parallele Importe fehlt noch.

**Empfehlung:** Erst typische Mehrdatei-Importe samt RAM, CPU,
Provider-Latenz und SQLite-Schreibwartezeit messen. Falls die Wartezeit
überwiegt, eine **globale** Nebenläufigkeitsgrenze über alle Importaufträge
testen, nicht nur eine Semaphore pro Auftrag. Dabei gemeinsam genutzte
Provider, Cover-Dienste, Jobstatus, Ereignisreihenfolge, Dateibereinigung
und Duplikat-Rennen prüfen. Die Höhe der Grenze ergibt sich aus Messungen
auf dem Zielsystem; 3 bis 4 ist bislang nur eine Hypothese.

## Ohne Beanstandung

Diese Punkte wurden gemessen und sind unkritisch:

- **FTS5-Volltextsuche.** Bei 20.000 Büchern liegt
  `repository.py:256-273` bei 0,3 bis 4,1 ms. Bei 100.000 Büchern sind es
  0,5 bis 34 ms; siehe die Korrektur im Abschnitt „Ziel 100.000 Bücher“.
  Der Query-Plan zeigt reguläre Index-Nutzung. Der Tokenizer-Schutz aus dem
  Sicherheitsaudit (`re.findall(r"[\w]+")`, maximal 12 Begriffe) kostet
  messbar nichts und begrenzt die Abfrage zusätzlich.
- **Einzelbuchabruf.** `get_book` liegt stabil bei 1,8 ms, die vier Queries
  sind hier unkritisch, weil genau die Daten gebraucht werden, die
  `book_to_dict(detail=True)` ausgibt.
- **Duplikatprüfung per Index.** `ix_books_sha256` wird genutzt
  (`SEARCH books USING INDEX ix_books_sha256`), 0,4 ms.
- **Thread-Auslagerung im Import.** `sha256_file`, `detect_and_extract` und
  `_archive` sind korrekt in `asyncio.to_thread` ausgelagert. Nur der
  Uploadteil von Punkt 8 ist nicht abgedeckt.
- **Commit-Kosten.** `synchronous=FULL` mit WAL kostet 0,36 ms gegen 0,32 ms
  bei `NORMAL`. Der Unterschied liegt in der Messauflösung und ist auf
  tmpfs nicht aussagekräftig; es gibt keinen belastbaren Grund, die
  Durabilität zu senken.
- **`defer()` auf breiten Spalten.** Siehe Punkt 1, gemessen ohne Wirkung.

## Empfohlene Reihenfolge

Für einen ersten benutzbaren Stand mit 100.000 Büchern sind Paginierung
und passende Indizes die wichtigsten Eingriffe. Ob weitere Maßnahmen
nötig sind, hängt von realer Datenverteilung und Zielhardware ab.

1. **Paginierung für `list_books`** im Backend (`limit`, `offset`, `total`)
   und im Frontend (Pager). Das behebt den dominanten Materialisierungs-,
   Transfer- und Speicherdruck. Im synthetischen Test: 10,26 s auf 9,1 ms
   und 620 MiB auf 71 MiB. Verhalten unter Parallelität danach neu messen.
2. **Indizes** für die stabile Sortierung
   `books(imported_at DESC, id DESC)` sowie `book_authors(author_id)`,
   `book_tags(tag_id)` und `book_genres(genre_id)` (Punkte 2, 5), inklusive
   Migration in `init_db` für Bestandsinstallationen. Die ursprünglichen
   vier Indizes kosteten zusammen 0,4 s und 5 MiB; den erweiterten
   Sortierindex separat messen.
3. **Nachmessen auf der Zielinstallation**, einschließlich Anmeldung,
   Antwort-Serialisierung, Browser-Rendering, Speicher und parallelen
   Aufrufen. Besonders `/api/filter-options` und gefilterte `COUNT(*)`-
   Abfragen mit realistischen Kardinalitäten prüfen.
4. **Filterwerte nur bei Bedarf cachen**, mit vollständiger Invalidierung
   oder Versionsprüfung über alle relevanten Mutationen. Bereits heute
   werden sie nicht bei jedem Filterwechsel geladen.
5. **Upload-Schreibvorgang** aus dem Event-Loop nehmen und gleichzeitig
   Größenlimits einführen (Punkt 8 und Sicherheitsaudit).
6. **Übersetzungs-Persistenz** und **Import-Nebenläufigkeit** nur nach
   eigener End-to-End-Messung ändern; Wiederaufnahme und Ressourcenlimits
   müssen erhalten bleiben. `find_by_hash` kann unabhängig davon mit einer
   kleinen, einfachen Spaltenabfrage verschlankt werden.

## Zusätzliche Vorschläge aus dem Codeabgleich

Diese Punkte waren nicht Teil der ursprünglichen Messung und sind deshalb
Hypothesen beziehungsweise konkrete Prüfaufträge, keine ausgewiesenen
Benchmark-Ergebnisse.

1. **Veraltete Listenantworten verhindern.** `loadBooks()` startet nach
   180 ms einen Request, bricht ältere laufende Requests aber nicht ab
   (`App.tsx:310-322`). Bei langsamen Vollabfragen kann eine alte Antwort
   nach einer neuen eintreffen und den aktuellen Filterzustand in der UI
   überschreiben. Beim Umbau auf Seitenabrufe `AbortController` oder eine
   fortlaufende Request-ID einsetzen; dabei Filter- und Seitenwechsel
   gemeinsam behandeln. Das verbessert Korrektheit und spart unnötige
   Übertragung, auch wenn der Server einen schon begonnenen SQL-Request
   nicht immer sofort abbrechen kann.
2. **Doppelte Import-Refreshes zusammenführen.** SSE und das 1,2-Sekunden-
   Polling (`App.tsx:325-360`) können denselben Abschluss melden. Ein
   einziger zentraler Refresh pro Importabschluss vermeidet doppelte
   Buchlisten- und Filteroptionen-Abfragen. Die bestehende Pollingroutine
   kann als Ausfallsicherung für verlorene SSE-Ereignisse bleiben.
3. **Größe der Filterantwort messen.** `/api/filter-options` liefert alle
   Autoren, Verlage, Reihen und Tags samt Zähler (`repository.py:128-165`).
   Bei vielen unterschiedlichen Werten können Antwortgröße und DOM der
   Auswahlfelder zum Engpass werden, selbst wenn die SQL-Abfragen gecacht
   sind. Die synthetische Bibliothek belegt noch nicht, wie sich reale
   Kardinalitäten auswirken. Falls nötig, selten genutzte lange Listen
   durch serverseitige Suche oder begrenzte Vorschläge ersetzen.
4. **Suche separat betrachten.** `/api/search` begrenzt auf 100 Treffer
   (`repository.py:262`), meldet aber `total = len(result)` (`main.py:274`).
   Für häufige Begriffe ist das kein echter Gesamttrefferwert. Vor einer
   späteren Suchpaginierung klären, ob die UI nur „erste 100 Treffer“
   anzeigen soll oder genaue Trefferzahlen braucht; Letztere können eine
   zusätzliche, möglicherweise teure FTS-Abfrage erfordern.
5. **End-to-End-Budget festlegen.** Der Benchmark isoliert überwiegend
   Datenbank und Backend. Die neue Authentifizierung öffnet pro API-Request
   zusätzlich `auth.db`; Browser-Rendering, Cover-Requests, Netzwerk und
   TrueNAS-Speicherpfad fehlen in den historischen Zahlen. Bei 1.000,
   20.000 und 100.000 Büchern jeweils p50/p95 für API und sichtbare UI,
   RSS unter mehreren Nutzern und Import neben Lesen messen. Erst daran
   weitere Optimierungen priorisieren.

### Nicht nötig für 100.000 Bücher

- Kein Wechsel der Datenbank aufgrund dieser Messung. SQLite trug die
  synthetischen 100.000 Zeilen bei 157 MiB Dateigröße mit Indizes und
  Volltextindex; einzelne getestete Leseabfragen lagen bei 2 bis 34 ms.
- Keine Änderung des Buch-Datenmodells allein für die Listenpaginierung.
  Die Assoziationstabellen trugen im Test 230.368 Zeilen bei 100.000
  Büchern ohne auffällige Verzögerung.
- Keine Keyset-Paginierung allein wegen der gemessenen SQL-Zeit. Offset
  99.900 kostete im Test 15,1 ms. Keyset kann bei häufigen gleichzeitigen
  Importen aus Gründen stabiler Navigation früher sinnvoll werden; das
  ist eine Produktentscheidung, keine durch diesen Benchmark belegte
  Grenze.
