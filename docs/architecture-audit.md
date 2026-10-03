# Architekturanalyse

Status: Historische Bestandsaufnahme vom 28.09.2026 vor den anschließenden
Korrekturen. Die beschriebenen Risiken und Zeilennummern beziehen sich auf
diesen damaligen Stand; insbesondere Kostenverfolgung, Segmentreparatur und
Metadatenpflege wurden danach geändert. Für den aktuellen Zustand den Code
und die Tests prüfen.

**Zu den Verweisen:** `backend/main.py` wurde während dieser Analyse aktiv
umgebaut (845 → 850 → 920 Zeilen, +143 Zeilen: KI-Nutzungs-Endpunkt,
Glossare, Segmentreparatur, `PUT /api/books/{book_id}/metadata`). Zeilennummern
aus dieser Datei wären daher wertlos. Verweise auf `main.py` sind deshalb auf
**Funktionsnamen** umgestellt (`def books`, `_persist_book_tags`,
`install_available_update` …). Verweise auf alle übrigen Module sind
Zeilennummern des unten genannten Stands. Vor der Umsetzung sind sie zu
prüfen.

## Vorwarnung zum Performanceaudit

`docs/performance-audit.md` beschreibt **einen Zustand, den es nicht mehr
gibt.** Das Audit wurde gegen eine Fassung von `list_books` ohne
`limit`/`offset` geschrieben. Im aktuellen Stand ist die Paginierung
implementiert:

- `def books` in `backend/main.py` — `limit: int = Query(50, ge=1, le=100)` und
  `offset: int = Query(0, ge=0)`
- die Rückgabe von `def books` in `backend/main.py` — die Antwort liefert `total`, `limit` und `offset`
- `backend/repository.py:295-297` — `list_books` wendet Offset und Limit an
- `backend/repository.py:274-277` — `count_books` für die Gesamtzahl
- `frontend/src/App.tsx:316` — `pageSize = 50`, `frontend/src/App.tsx:340-341`
  setzt `limit` und `offset` in die Anfrage
- `backend/database.py:57-61` und `backend/models.py:57-64` — die im Audit
  empfohlenen Indizes sind angelegt
- `backend/repository.py:179-189` — `cached_filter_options` mit 60 Sekunden
  TTL für den zweiten Engpass

Die Kernbefunde des Audits — insbesondere die 10,26 Sekunden und 620 MiB für
eine ungefilterte Vollabfrage — sind damit **historisch und dürfen nicht mehr
als Beschreibung des Ist-Zustands zitiert werden.** Sie bleiben als Begründung
für die getroffenen Entscheidungen gültig. Die im Audit genannten Messwerte
wurden gegen die alte Fassung erhoben und sind nicht neu gemessen.

## Systemkontext

Goblin ist ein lokaler E-Book-Archivar: eine FastAPI-Anwendung, die Dateien
importiert, Metadaten anreichert, ein Archiv auf der Platte hält und eine
Weboberfläche aus demselben Prozess ausliefert. Es gibt keine Nachrichtenqueue,
keinen externen Job-Runner, keinen Cache-Server und keine horizontale
Skalierung. Alles läuft in einem uvicorn-Prozess.

### Betriebsformen

| Aspekt | Container (`Dockerfile`) | systemd (`deploy/`) |
| --- | --- | --- |
| Start | `uvicorn backend.main:app --workers 1` | `deploy/goblin-archive.service` |
| Datenverzeichnis | `GOBLIN_DATA_DIR=/data` | konfigurierbar |
| Update | In-App gegen GitHub-Release, Container-Recreate | `deploy/update-systemd.sh` |
| Healthcheck | `HEALTHCHECK` auf `/api/health` | systemd |
| Weitere Variante | `deploy/truenas-compose.yml` | — |

Die dritte Form ist TrueNAS SCALE: dort wird über eine WebSocket-RPC
(`backend/updater.py:85-107`) ein App-Update angestoßen, statt über Shell-
Skripte. Der Updater kennt damit zwei Installationsmodelle und hält sie in
`install_mode` auseinander (`backend/updater.py:41-48`).

### Statisch entscheidende Randbedingung: ein Prozess

`--workers 1` im `Dockerfile` ist keine Performance-Entscheidung, sondern eine
Korrektheitsvoraussetzung. Vier Zustände im Code sind prozesslokal und würden
bei mehreren Workern auseinanderlaufen:

1. `ImportManager.jobs` — ein `dict` im Speicher (`backend/imports.py:113`)
2. `EventBroker.subscribers` — SSE-Abonnenten je Prozess (`backend/imports.py:74`)
3. `_filter_cache` — ein Modul-Global mit Lock (`backend/repository.py:15-16`)
4. `ai_tagging_lock` — ein prozessweiter `threading.Lock` (`backend/main.py` — Modulkopf, `ai_tagging_lock`)

Punkt 2 ist der schärfste: ein zweiter Worker würde Import-Ereignisse nur an
Clients senden, die mit diesem Worker verbunden sind. Ein Importergebnis
erschiene dann als „verschwunden“. Das ist an keiner Stelle dokumentiert und
steht in keinem Test.

### Datenverzeichnis

Alles Zustandsbehaftete liegt unter `data_dir` (`backend/config.py:13`, per
`GOBLIN_DATA_DIR` überschreibbar):

```
data_dir/
├── goblin.db            SQLAlchemy-Anwendungsdatenbank
├── auth.db              separate SQLite für Admin und Sessions
├── ai-settings.json     OpenAI-Schlüssel im Klartext, Modus 0600
├── auth-setup-code      Einmalcode, wird nach Setup gelöscht
├── logs/goblin.log
├── staging/             Upload-Zwischenablage
└── library/<Autor>/<Titel>/<book_id>/
    ├── <Titel>.epub
    ├── cover.jpg
    └── metadata.json
```

Die Bibliothek ist als Dateisystem organisiert, nicht nur in der Datenbank.
Jedes Buchverzeichnis trägt ein vollständiges `metadata.json`; die Datenbank
hält dieselben Daten in den Spalten und in `books.metadata_json`. Diese
Dreifachhaltung ist der zentrale Architekturzug des Projekts und zugleich seine
größte Fehlerquelle — siehe „Datenschutz“ unten.

## Schichten und Modulstruktur

Gemessen mit einem AST-Importscan. Spalte „ein“ = eingehende interne Abhängigkeiten,
„aus“ = Module, die dieses Modul benutzen.

| Modul | LOC | ein | aus | Abhängigkeiten |
| --- | ---: | ---: | ---: | --- |
| `main` | 920 | 16 | 0 | alle Fachmodule |
| `translation` | 654 | 7 | 1 | ai, config, **imports**, metadata, models, repository, usage |
| `isbn` | 674 | 3 | 2 | config, metadata, models |
| `imports` | 442 | 9 | 2 | config, covers, extractors, isbn, maintenance, metadata, models, providers, repository |
| `repository` | 318 | 2 | 3 | metadata, models |
| `language` | 291 | 3 | 1 | ai, config, extractors |
| `extractors` | 298 | 1 | 2 | metadata |
| `covers` | 250 | 1 | 2 | metadata |
| `auth` | 195 | 1 | 0 | config |
| `updater` | 180 | 1 | 1 | config |
| `providers` | 171 | 1 | 2 | metadata |
| `models` | 169 | 1 | 7 | database |
| `metadata` | 152 | 0 | 10 | — (Basis) |
| `ai` | 123 | 2 | 3 | config, metadata |
| `ai_config` | 118 | 1 | 2 | config |
| `config` | 77 | 1 | 11 | ai_config |
| `usage` | 87 | 1 | 2 | models |
| `database` | 137 | 2 | 2 | config, metadata |
| `maintenance` | 53 | 2 | 1 | config, models |

Die Schichtung ist im Kern sauber und hat keine Zyklen:

```
main            Composition Root, HTTP, Auth-Middleware
 ├── repository     Lesen/Schreiben, Abfragen, FTS, Filter-Cache
 ├── imports        Import-Orchestrator
 ├── translation    Übersetzungs-Orchestrator
 ├── isbn           Werk- und Ausgabe-Auflösung
 ├── providers      externe Metadatenquellen
 ├── covers         Coverbeschaffung
 ├── ai             KI-Provider-Abstraktion
 ├── language       Textextraktion + Spracherkennung
 ├── extractors     Metadaten aus Dateiformaten
 ├── updater        Self-Update
 ├── auth           Anmeldung
 └── usage          KI-Kosten
metadata         gemeinsames Vokabular: FieldValue, BookMetadata, Normalisierung
models           ORM-Modelle
database         Engine, Migrationen, FTS-Tabelle
config           Settings
```

`metadata` ist die Basis: null eingehende Abhängigkeiten, von zehn Modulen
genutzt. Es ist bewusst frei von I/O und enthält die Normalisierungslogik
(`normalize_text`, `clean_tag_name`, `normalize_tag_name`, `normalize_language`).
Diese Trennung ist der Grund, warum sich Metadatenlogik unabhängig testen lässt
(`backend/tests/test_metadata.py`).

### Zwei Kopplungen, die auffallen

**`translation` importiert `imports`.** `backend/translation.py:27` holt sich
`sha256_file` — eine sechszeilige, seiteneffektfreie Funktion
(`backend/imports.py:36-41`). Damit hängt das 654-zeilige Übersetzungsmodul am
442-zeiligen Import-Orchestrator, nur für einen Hash. Wird sie dreimal benutzt
(`backend/translation.py:297`, `:560`, `:614`), ist das eine Frage der
Modulplatzierung, nicht der Architektur. `sha256_file` gehört in ein
Werkzeugmodul neben `metadata`; das löst die Kopplung in einem Schritt.

**`config` und `ai_config` bilden einen Zyklus.** `backend/config.py:75` importiert
`load_ai_config` aus `ai_config`, und `backend/ai_config.py:13` importiert
`Settings` aus `config`. Aufgelöst ist der Zyklus durch einen Import **innerhalb**
der Funktion — eine Technik, die auftritt, sobald der Modulimport scheitert, und
im Stack-Trace nicht als Zyklus erkennbar ist. Die saubere Auflösung: `Settings`
ohne KI-Felder, und `ai_config` erweitert die bereits fertige Instanz.

## Datenhaltung

### Zwei Datenbanken

| | `goblin.db` | `auth.db` |
| --- | --- | --- |
| Zugriff | SQLAlchemy ORM | rohes `sqlite3` (`backend/auth.py:21-32`) |
| Zweck | Fachdaten | Admin-Passwort, Sessions, Login-Versuche |
| Schema | deklarativ in `models.py` | imperativ beim Verbindungsaufbau |
| Transaktionen | Session-Kontexte | implizit über `with` |
| Tabellen | 12 | 3 |

Die Trennung ist fachlich richtig: Anmeldedaten sollen nicht im Archiv liegen,
das man exportiert. Der Nachteil ist ein zweiter Verbindungs- und
Migrationspfad ohne Framework, und `auth.db` kennt weder WAL noch
`foreign_keys`-PRAGMA, während `goblin.db` beides setzt
(`backend/database.py:26-27`). Zwei Konsistenzmodelle in einem Verzeichnis.

### Schema und Migration

`init_db` (`backend/database.py:37-129`) macht drei Dinge gleichzeitig: Schema
anlegen, Spalten nachziehen, Daten umformen.

- **`Base.metadata.create_all`** für neue Tabellen
- **`PRAGMA table_info` + `ALTER TABLE ADD COLUMN`** für fünf nachgezogene
  Spalten: `has_cover`, `cover_source`, `cover_provider`, `reference_isbn`,
  `work_match_json` (`database.py:45-56`)
- **`app_migrations`** als eigene Tabelle mit genau einem bislang verwendeten
  Eintrag, `genres_to_tags_v1` (`database.py:72-103`)
- Eine **Datenmigration über alle Bücher**, die `language` normalisiert (`:62-70`)

Diese Mischform ist funktional, hat aber drei Eigenschaften, die bei wachsender
Archivgröße zählen:

1. Der Normalisierungslauf über `books` läuft bei **jedem Start**, nicht nur
   einmal. Bei 100.000 Büchern ist das eine Vollabfrage pro Boot-Vorgang. Das
   AUDIT hat diesen Pfad nie gemessen.
2. `ALTER TABLE` ist nicht in eine Transaktion mit dem Datenlauf gepackt. Ein
   Abbruch dazwischen lässt die Datenbank in einem halb migrierten Zustand.
3. Es gibt keinen Downgrade-Pfad und kein Versionsschema, das Ausgaben und
   Eingaben unterscheidet. `metadata.json` trägt zwar ein `schema_version`
   (`backend/imports.py:254`), die Datenbank nicht.

Es gibt kein Migrationswerkzeug (Alembic ist keine Abhängigkeit, `pyproject.toml`).
Bei fünf nachgezogenen Spalten ist das vertretbar, aber die Grenze liegt
niedrig.

### Indizes

`books` hat sieben Indizes (`backend/models.py:57-64`) auf `publication_year`,
`language`, `publisher`, `isbn`, `format`, `series` und den Sortierschlüssel
`(imported_at, id)`. Die drei Assoziationstabellen tragen je einen Index auf der
Fremdspalte (`backend/models.py:16`, `:24`, `:32`). `books.sha256` und
`books.library_path` sind unique indiziert, weil sie Duplikaterkennung und
Dateizugriff tragen.

Der Sortierschlüssel `(imported_at, id)` ist die Voraussetzung dafür, dass
`offset` bei 100.000 Büchern im zweistelligen Millisekundenbereich bleibt
(`backend/repository.py:295`). Die zweite Spalte macht die Sortierung bei
gleichen Zeitstempeln eindeutig und den Index stabil — ohne sie wäre
`ORDER BY imported_at DESC` bei mehreren Importen am selben Sekundenwert
nicht deterministisch, und SQLite müsste die Restreihenfolge im Speicher
sortieren.

### Redundanz und ihre Kosten

Metadaten existieren an drei Orten gleichzeitig: typisierte Spalten auf `books`,
das `metadata_json`-Blob und die relationalen Tabellen. Zusätzlich liegt auf der
Platte je Buch ein vollständiges `metadata.json`.

Das ist keine Redundanz im Sinne von Verschwendung, sondern eine
bewusste Entscheidung: die Datei muss ohne Datenbank lesbar sein, damit das
Archiv sichern, verschieben und auf einem anderen System wiederherstellen kann,
ohne Goblin zu kennen. Die Spalten existieren für Indizierung und Filterung,
das Blob für Herkunft und Kontext, die Relationen für Join-Filter.

Der Preis ist, dass **jeder Schreibpfad alle Ebenen synchronisieren muss.** Das
ist im Code überall getan und überall von Hand:

| Ort | Muster |
| --- | --- |
| `imports._archive` | `backend/imports.py:225-262` |
| `imports._store_refreshed_cover` | `backend/imports.py:342-381` |
| `isbn._write_identity` | `backend/isbn.py:603-624` |
| `main._persist_book_tags` | `_persist_book_tags` |
| `main.update_book_metadata` | `update_book_metadata` (Metadaten-Editor, **im Arbeitsbaum neu**) |

Alle fünf implementieren dasselbe: temporär schreiben, `os.replace`, Datenbank
committen, im Fehlerfall die alte Datei zurückschreiben und die Transaktion
zurückrollen. **Diese Logik ist fünfmal kopiert.** Sie ist in jedem Einzelfall
korrekt und in jedem Einzelfall eine Fehlerquelle, weil die Kopien
unabhängig voneinander veralten können. Die fünfte Kopie (`update_book_metadata`)
ist während dieser Analyse entstanden — das ist der direkte Beleg dafür, dass
der Kopiervorgang beim Erweitern billiger erscheint als eine Extraktion. Sie ist das wichtigste Argument für
einen gemeinsamen Schreibpfad in diesem Projekt.

Der Volltextindex ist die fünfte Ebene: `replace_book_fts`
(`backend/repository.py:213-218`) wird nach jeder Tag-Änderung aufgerufen
(`:226`, `:235`), aber **nicht** nach einer Änderung von Titel, Verlag, Serie
oder ISBN. Diese Felder lassen sich derzeit ohnehin nur über den Importpfad
setzen, also ist die Lücke theoretisch — sie wird aber sofort relevant, sobald
Feature 02 (Metadaten-Editor) umgesetzt wird. Das ist ein Grund, den
Metadaten-Editor nicht als Formular-Patch zu bauen, sondern als Aufruf
desselben Schreibpfads, den der Import benutzt.

## Request-Lebenszyklus und Nebenläufigkeit

### Middleware-Kette

```
CORS (`app.add_middleware(CORSMiddleware, …)`)
  └── authenticate (`@app.middleware("http") authenticate`)
        ├── auth.require_request_auth für /api/*  (nicht die vier öffentlichen Pfade)
        ├── auth.check_csrf bei schreibenden Methoden
        └── Cache-Control: no-store für /api/auth/*
  └── SQLAlchemyError → 500 (`@app.exception_handler(SQLAlchemyError)`)
  └── StaticFiles auf "/" (der `app.mount("/", StaticFiles(...))` am Dateiende)
```

Die öffentliche Pfadliste steht als Literal im `authenticate`-Middleware. Neue Endpunkte
erben automatisch den Schutz, weil die Bedingung auf `/api/`-Präfix lautet —
das ist die richtige Voreinstellung. Umgekehrt gibt es keine Mechanik, die
einen Endpunkt bewusst öffentlich macht, außer durch Aufnahme in das Literal.

Der Session-Kontext kommt über `Depends(get_db)` (`backend/database.py:132-137`)
mit Yield und `close()` im `finally`. Das ist sauber: keine geteilten Sessions,
jeder Request bekommt eine eigene. Der Preis ist, dass Endpunkte ohne
`Depends(get_db)` sich selbst eine Session beschaffen müssen — und das tun
several: die Übersetzungsendpunkte arbeiten über
`request.app.state.translation_manager` (siehe die Übersetzungsendpunkte in `backend/main.py`), und
`/api/ai/usage` erzeugt seine Session direkt (`def ai_usage`). Zwei
Session-Disziplinen im selben Modul.

### Sync im Async: die zentrale Entscheidung

Die meisten Endpunkte sind mit `def` deklariert, nicht `async def`
(`def books`, `:289`, `:295`, `:475`, `:490`, …). FastAPI führt sie im
anyio-Threadpool aus, dessen Default-Kapazität bei 40 Threads liegt. Nur
wenige sind `async`: Upload (`def import_files`), Cover-Refresh (`:696`),
ISBN-Operationen (`:720`, `:729`, `:740`), Archiv löschen (`:804`).

Das ist eine bewusste und im Grunde richtige Wahl: Datenbankzugriff und
Dateisystemzugriff sind blockierend, und `async def` mit synchronem SQLite
hätte den Event-Loop blockiert. Der Preis ist der 40-Thread-Pool: jede
gleichzeitige Anfrage braucht einen Thread, und alle teilen sich den GIL.

Für CPU-gebundene Arbeit im Threadpool ist das keine Verbesserung gegenüber
einem Block. Genau das war der Gegenstand des Performanceaudits: vier parallele
Vollabfragen brauchten 89,86 Sekunden statt 41 — superlinear. Der Grund ist,
dass JSON-Serialisierung von 53 MiB in Python GIL-Zeit ist, die sich nicht
parallelisieren lässt. **Die Ursache dieser Superlinearität war die fehlende
Paginierung, nicht der Threadpool.** Mit Paginierung sind die Antworten 27 KiB
statt 53 MiB, und der GIL-Konflikt entfällt. Der Threadpool ist damit kein
Problem mehr, solange Antworten paginiert sind.

### Locks: einer pro Prozess, zwei insgesamt

| Lock | Ort | Schützt |
| --- | --- | --- |
| `ai_tagging_lock` | `backend/main.py` — Modulkopf, `ai_tagging_lock` | alle KI-Endpunkte, `blocking=False` |
| `_filter_cache_lock` | `backend/repository.py:16` | der Filter-Cache |
| `ai_tagging_lock` (2. Nutzung) | `ai_tagging_lock.acquire` in `detect_book_language` | Spracherkennung |

`ai_tagging_lock` wird an zwei Stellen mit `acquire(blocking=False)` genutzt: für
KI-Tagging (`:492`) und für Spracherkennung (`:576`). Beide Endpunkte sind damit
**prozessweit gegenseitig ausgeschlossen** und antworten im Konfliktfall mit
HTTP 409. Das ist eine bewusste Drosselung gegen OpenAI-Limits und Kosten, aber
sie hat eine unbeabsichtigte Folge: ein Buch, das gerade seine Sprache per KI
bestimmen lässt, blockiert das Tagging eines völlig anderen Buchs. Für
Einzelnutzung irrelevant, für eine Automatisierung über mehrere Browser relevant.

Bemerkenswert ist, dass beide Endpunkte das Lock über ein `try`/`finally` mit
`release()` sichern (`:545-546`, `:657-658`) — korrekt.

### Der Filter-Cache

`cached_filter_options` (`backend/repository.py:179-189`) hält das Ergebnis der
Filteroptionen in einem Modul-Global mit 60 Sekunden TTL, geschützt von einem
Lock. Der Cache-Key ist die Datenbank-URL (`backend/repository.py:181`), was in
Tests mit mehreren Datenbanken wichtig ist.

Der Lock umfasst nur den Lesen und Schreiben des Caches, **nicht** die
teure Berechnung. Zwei gleichzeitige Aufrufe bei leerem Cache berechnen beide
`list_filter_options` — korrekt, aber doppelt teuer. Bei 100.000 Büchern ist
das genau die teure Operation, die der Cache vermeiden soll. Ein
Doppel-Checked-Locking oder ein `functools`-Cache wäre die Korrektur; der
Nutzen ist real, aber begrenzt, weil der Pfad nach 60 Sekunden ohnehin wieder
anfällt.

Die Invalidierung ist an vier Stellen aufgerufen und für alle Filter relevanten
Schreibvorgänge vorhanden: `add_book_tag`/`remove_book_tag` über die Endpunkte
(`invalidate_filter_cache()` in `create_book_tag`, `:564`), `_persist_book_tags` (`:477`), Import
(`backend/imports.py:210`, `:392`, `:407`, `:421`, `:434`) und Archiv löschen
(`:139`). Das ist vollständig.

## Import-Pipeline

Die fachlich wichtigste Sequenz des Projekts. Sie läuft als eine
`asyncio.Task`, die Dateien **streng nacheinander** verarbeitet
(`backend/imports.py:152-153`).

```
POST /api/import  (`def import_files`)
  ├── Limits prüfen: max_upload_files, content-length, max_upload_bytes
  ├── je Datei chunkweise in staging/ schreiben (1 MiB-Blöcke, to_thread)
  └── create_job()  (imports.py:120) → asyncio.create_task(_run)
        └── für jedes Item: _process_one()  (imports.py:156)
              ├── sha256_file            (to_thread)
              ├── Duplikatprüfung über find_book_id_by_hash
              ├── detect_and_extract     (to_thread)  → extractors.py
              ├── provider_chain.enrich  (async HTTP) → providers.py
              ├── cover_service.resolve  (async HTTP) → covers.py
              ├── _archive               (to_thread)  → Datei + DB
              └── finally: staging-Datei löschen
```

### Was daran gut ist

**Fehlerisolierung ist vollständig.** `_process_one` fängt vier erwartete
Fehlerklassen (`backend/imports.py:212`) und zusätzlich eine generische Klausel
(`:217`), mit der Begründung im Kommentar, ein fehlgeschlagener Import darf
nicht die ganze Task beenden. Die `finally`-Klausel löscht die Staging-Datei
immer (`:222-223`). Das ist genau die Sorgfalt, die man sich in einer
Hintergrundpipeline wünscht.

**Blockierende Arbeit ist ausgelagert.** Hashing, Extraktion und Archivierung
laufen über `asyncio.to_thread` (`:159`, `:171`, `:203`). Der Event-Loop
blockiert nicht.

**Das Dateisystem wird atomar befüllt.** `_archive` schreibt zuerst in ein
verstecktes temporäres Verzeichnis `.incoming-<uuid>` (`:270`), kopiert die
Buchdatei, schreibt `metadata.json`, und verschiebt dann das **ganze
Verzeichnis** mit `os.rename` an seinen endgültigen Platz (`:280`). Ein
Beobachter sieht entweder nichts oder ein vollständiges Buch. Wird die
Transaktion der Datenbank afterwards verworfen, räumt der `except`-Zweig
auf (`:311-316`) — in beiden Fällen bleibt kein halbes Buch zurück.

**Die Verzeichniskollision ist gelöst.** `book_id` ist `bk_` plus 8 Hex-Zeichen
(`:244`), also 32 Bit. Bei 100.000 Büchern ist die Kollisionswahrscheinlichkeit
vernachlässigbar, aber nicht null; der Code prüft bis zu 20 Versuche und
verwirft (`:243-249`). Angemessen.

**Cover-Fehler sind isoliert.** Ein Fehler in der Coverbeschaffung wird
abgefangen und protokolliert, ohne den Import zu gefährden
(`backend/imports.py:197-201`). Richtig, denn ein Cover ist optional.

### Was problematisch ist

**Sequenzielle Verarbeitung.** Für 400 Dateien mit je drei HTTP-Aufrufen an
Provider und Cover-Dienste ist das der Engpass. `provider_chain.enrich` und
`cover_service.resolve` sind `async` und werden mit `await` in einer Schleife
aufgerufen, nicht gebündelt (`backend/imports.py:177`, `:186`). Bei
5 Sekunden Timeout pro Provider (`provider_timeout`, `backend/config.py:15`) und
drei Providern im Default (`provider_order`, `:14`) kann ein einzelnes Buch bis
zu 15 Sekunden für Metadaten plus weitere 10 für Cover blockieren. Bei 400
Büchern sind das Stunden.

Hier wäre `asyncio.gather` über die Provider-Ergebnisse — begrenzt auf
begrenzte Parallelität — der naheliegende Gewinn, ohne die Pipeline umzubauen.
**Aber:** die Verarbeitung ist absichtlich streng seriell, vermutlich um
Provider-Rate-Limits einzuhalten und die Ereignisreihenfolge für das Frontend
deterministisch zu halten. Vor einer Umstellung ist zu klären, ob die
Reihenfolge der SSE-Ereignisse eine Zusage ist. Das steht nirgends.

**Importjobs sind flüchtig.** `self.jobs` ist ein `dict` im Speicher
(`backend/imports.py:113`), die Task existiert nur im Prozess. Ein Neustart
verliert den Job. Das eigene Update startet den Container neu und ist deshalb
gegen laufende Importe gesperrt (der Laufzeitcheck in `install_available_update`) — der Schaden wird
also erkannt und von Hand vermieden, aber nicht aufgelöst. Ausgearbeitet in
`docs/backlog/feature-01-importjobs.md`.

**Die Filter-Cache-Invalidierung ist an den Import gekoppelt.** `imports.py` ruft
`invalidate_filter_cache()` fünfmal auf. Das ist korrekt, aber es zeigt, wie
implizit die Kopplung zwischen Import und Filteransicht ist: ein neuer Import
muss die Oberfläche aktualisieren, und das passiert über einen
Modul-Globalen, nicht über ein Ereignis.

**Die `metadata.json` wird vor dem Verschreiben der Datei gelesen.**
`_store_refreshed_cover` liest `metadata_path.read_bytes()` (`:352`), bevor der
Pfad verifiziert ist. Fehlt die Datei, gibt es eine `FileNotFoundError` statt
einer verständlichen Meldung. Ein Randfall, aber bei einem ausgelagerten oder
 teilweise beschädigten Archiv wahrscheinlicher als man denkt.

## ISBN- und Werkauflösung

`backend/isbn.py` (674 Zeilen) ist das fachlich am dichtesten Modul und das
einzige, das eine echte Entscheidungslogik enthält. Es verdient eine eigene
Betrachtung, weil die Trennung von Werk und Ausgabe der eleganteste Gedanke im
Projekt ist.

### Zwei Bewertungen, zwei Bedeutungen

`_score` und `_work_score` (`backend/isbn.py:277-290`) unterscheiden sich genau
darin, dass `_work_score` **Verlag und Jahr nicht einbezieht**. Das ist keine
Versehen, sondern die Korrektheitsbedingung: Taschenbuch und Hardcover eines
Titels sind verschiedene Ausgaben, aber dasselbe Werk. Wer die beiden nicht
trennt, produziert bei jeder Reihe Duplikate.

Dazu passt `_safe_edition_match` (`:292`), die für `match_type = "edition"`
einen Score ≥ 95, vorhandene Erscheinungsjahre und eine Abweichung von höchstens
einem Jahr verlangt. Ein `edition`-Match ist damit eine belegte Aussage, ein
`reference`-Match eine schwächere.

Die Auto-Annahme nutzt beides: `work_score >= 90` nimmt still an, aber nur wenn
der Abstand zur zweiten Kandidatin mindestens 5 Punkte beträgt (`:463-476`). Bei
100.000 Büchern ist die Frage, wie oft diese 5-Punkte-Schranke in der Praxis
greift, eine Messfrage — sie ist es nicht.

### Der Resultatcache

`IsbnLookupCache` (`backend/models.py:122-127`) speichert Provider-Antworten mit
`expires_at`, indiziert auf die Ablaufzeit. Das ist die einzige Caching-Schicht
im Projekt außer dem Filter-Cache und richtig platziert, weil Provider-Anfragen
die teuersten externen Aufrufe sind.

### Kopplung

`isbn` hängt an `config`, `metadata` und `models` — eine schmale Basis. Die
Richtung ist bemerkenswert: `imports` importiert `isbn`
(`backend/imports.py:21`), aber `isbn` importiert `imports` nicht. Die
Auflösung läuft also durch den Import-Orchestrator, nicht umgekehrt.

## KI-Subsystem

### Providerabstraktion

`AIProvider` ist ein `Protocol` (`backend/ai.py:27-32`) mit genau einer Methode
`generate`. `OpenAIProvider` ist die einzige Implementierung; die Fabrik
`make_ai_provider` (`:68`) wählt danach. Das ist eine saubere, wenn auch
einstellige Abstraktion: die Trennung erlaubt Testdoubles
(`backend/tests/test_ai.py` ist 175 Zeilen und prüft gegen ein Fake), und der
Aufwand dafür ist gering.

Drei Funktionen nutzen sie: `generate_tags` (`backend/ai.py:112`),
`detect_language` (`backend/language.py:259`) und die Übersetzung. Alle drei
geben `AIResult` mit `input_tokens` und `output_tokens` zurück.

### Kostenverfolgung

`backend/usage.py` (87 Zeilen, uncommittet) sammelt Verwendungen in der Tabelle
`ai_usage` (`backend/models.py:154-169`) mit Indizes auf `created_at` und
`feature`. `enforce_limit` (`:30-40`) prüft Tages- und Monatsgrenzen.

Die Verdrahtung ist **lückenhaft**, und zwar an einer Stelle, die auffällt:

| Endpunkt | `record` | Kosten | Limit geprüft |
| --- | --- | --- | --- |
| Übersetzung | `translation.py` | echte Berechnung aus konfigurierten Raten | ja (`:382`, `:487`) |
| KI-Tagging | `record_ai_usage` in `generate_book_tags` | **`cost_usd=0`** | **nein** |
| Spracherkennung | `record_ai_usage` in `detect_book_language` | **`cost_usd=0`** | **nein** |

Tagging und Spracherkennung schreiben also Tokenmengen, aber keine Kosten, und
umgehen die Obergrenzen. Der Grund ist strukturell: `config.py` kennt
Preise nur für die Übersetzungsmodelle
(`ai_translation_input_usd_per_million` und Verwandte, `:23-28`), nicht für
`ai_tagging_model` oder `ai_language_model`. Ohne Raten ist die Kostenangabe
nicht berechenbar.

**Konsequenz für die Übersicht:** Die KI-Kostenübersicht zeigt korrekt die
Übersetzungskosten und **0 für Tagging und Spracherkennung**. Das ist eine
tatsächliche Kostenstelle, die als null erscheint. Bei 100.000 Büchern, die
alle einmal getaggt werden sollen, ist das der wahrscheinlich größere
Kostenblock. Vor dem Feature als fertig zu melden, sollten
Tagging- und Sprachraten konfigurierbar sein und die Limits für beide Pfade
gelten.

### Promptversionierung

Tagging, Spracherkennung und Übersetzung führen jeweils eine `VERSION` und
schreiben sie in das `metadata_json` (`prompt_version`, `prompt_version` in `generate_book_tags`,
`:640`). Zusammen mit dem Fingerprint-Vergleich
(der Fingerprint-Vergleich in `generate_book_tags` und `detect_book_language`) ergibt ein brauchbares Caching: eine
Wiederholung ohne Datenänderung und ohne Promptänderung kostet nichts. Das ist
die einzige Stelle im Projekt, an der Kostenvermeidung systematisch betrieben
wird, und sie funktioniert.

## Übersetzung

`backend/translation.py` (654 Zeilen) ist das größte Fachmodul. Die Struktur ist
in drei Teile geteilt:

1. **EPUB-Manipulation** (`:56-238`) — ZIP-Sicherheit (`_safe_zip` `:60`,
   mit Größen- und Pfadprüfung), XML mit `defusedxml` (`_xml` `:69`),
   Segmentierung (`_segment` `:153`), Validierung (`validate_epub` `:113`,
   `validate_translation` `:229`)
2. **Auftragsverwaltung** (`:248-450`) — `TranslationManager` mit
   `create`, `start`, `stop`, `update_glossary`, `update_budget`,
   `repair_segment`
3. **Ablauf** (`:452-654`) — `_run` und `_assemble`, inklusive optionaler
   EPUBCheck-Validierung über `epubcheck_command` (`:601-602`)

### Persistenz

`TranslationJob` (`backend/models.py:130-136`) speichert den **kompletten
Auftragszustand als JSON-Blob** in `data_json` — Segmente, Übersetzungen,
Kosten, Fortschritt. Der Zustand ist damit datenbankresident und übersteht
einen Neustart. Das ist der Gegenentwurf zu den Importjobs und zeigt, dass das
Muster im Projekt bekannt ist: Importjobs sind flüchtig, Übersetzungsaufträge
nicht. Diese Inkonsistenz ist das stärkste Argument für Feature 01.

### Budget

Drei Ebenen: ein Budget je Auftrag (`update_budget` `:409`), Raten
(`_rates` `:336`) und globale Tages-/Monatsgrenzen über `usage.enforce_limit`
(`:382` für die Schätzung, `:487` für die Reservierung während des Laufs). Die
doppelte Prüfung — einmal vor dem Start, einmal pro Segment — ist richtig, weil
die Schätzung vom tatsächlichen Verbrauch abweichen kann.

`repair_segment` (`:372`) erlaubt die gezielte Nacharbeit an einem einzelnen
Segment und ist im Arbeitsbaum neu. Das ist die richtige Ergänzung zu einem
Budget, das sonst nur Abbruch kennt.

### Kopplungsproblem

Die Kopplung an `imports` für `sha256_file` ist oben beschrieben. Sie ist
kosmetisch, aber sie steht in einem Modul, das ansonsten sauber aufgebaut ist,
und sie erzeugt eine Importkette `translation → imports → isbn → …`, die im
Werkzeugbau bemerkbar wird.

## Anmeldung

`backend/auth.py` (195 Zeilen) ist bewusst außerhalb der ORM-Schicht und
verwaltet `auth.db` mit rohem `sqlite3`. Die Bausteine:

- **Passwort** mit `scrypt`, `n=2**15`, `r=8`, `p=1`, `maxmem=64 MiB`,
  `dklen=32` (`:56-59`) — parameterstark und mit Speicherkosten, um
  GPU-Angriffe unattraktiv zu machen
- **Setup-Code** als Datei `auth-setup-code` mit `O_EXCL` und Modus 0600
  (`:43-53`), nach dem Setup sofort gelöscht
- **Sessions** als **Hash** des Tokens in der Datenbank (`:110-127`), nicht im
  Klartext
- **Throttling** mit 5 Versuchen in 15 Minuten (`:16-17`, `:61-76`)
- **CSRF-Token**, abgeleitet vom Session-Token (`:163-165`)

Die Sicherheitsdetails sind im [Sicherheitsaudit](security-audit.md) beschrieben;
die hier relevante architektonische Beobachtung ist die Trennung: Goblin ist
für den Einzelplatzbetrieb gebaut, und die Anmeldung ist entsprechend
**Einbenutzer** — eine `admin`-Zeile mit `CHECK (id = 1)`
(`backend/auth.py:26`). Mehrbenutzerfähigkeit wäre ein anderes Datenmodell,
nicht nur eine Einstellungsoption. Die README nennt das unter den MVP-Grenzen
korrekt.

## Update-Mechanismus

`backend/updater.py` (180 Zeilen) prüft GitHub-Releases und installiert über
zwei Wege: TrueNAS-WebSocket-RPC (`:85-107`) oder ein systemd-Skript
(`:145`). Die Installationsentscheidung fällt in `install_mode` (`:41-48`),
das Update selbst startet einen Hintergrundprozess und gibt sofort zurück
(`@app.post("/api/update/install")`, Status 202).

Zwei Architekturbeobachtungen:

1. **Das Update prüft Laufzeitbedingungen vor der Installation**
   (der Laufzeitcheck in `install_available_update`): keine laufenden Importe, keine Übersetzung,
   keine Wartung. Das ist ein manuell gepflegter Satz von
   `has_active_imports()`, `translation_manager.running` und
   `maintenance_active` — drei Attribute aus drei Modulen, an einer Stelle
   zusammengetragen. Sobald ein vierter Langläufer dazukommt, wird die Liste
   hier vergessen.
2. **Das Update verlangt ein Passwort** (`class InstallUpdateRequest`, `:215`).
   Beabsichtigt, damit ein CSRF-geschützter Browser-Request nicht allein
   genügt. Ob es dasselbe Passwort wie für die Anmeldung prüft, geht aus dem
   Aufruf hervor; es ist eine zweite Autorisierungsquelle für dieselbe Person.

## Frontend

Kein Framework-Zustand, kein Router, keine Data-Fetching-Bibliothek: React mit
`useState`/`useEffect`/`useCallback` (`frontend/src/App.tsx:1`) und einem
handgeschriebenen API-Client (`frontend/src/api.ts`). Das ist für 1511 Zeilen
Frontend vertretbar und hält die Abhängigkeiten auf React und Vite.

### Zustandshaltung

`App.tsx` ist ein einzelnes Modul mit rund 30 `useState`-Aufrufen, davon 13 in
der Buchdetail-Komponente (`:81-93`) und 17 in der Listenkomponente
(`:287-303`). Es gibt keine Unterteilung in Store oder Reducer; die
Detailansicht lebt als Funktion innerhalb derselben Datei.

Für den Umfang ist das aufwendig, aber nicht unüblich. Der Punkt, an dem es
kippt, ist die Kopplung von `loadBooks` an Ereignisse: der SSE-Handler
(`:367-386`) löst bei mehreren Ereignistypen einen Neuladen aus. Mit
Paginierung ist das eine Anfrage mit 27 KiB statt einer mit 53 MiB — der
Paginierungsentscheid hat also nicht nur den Server entlastet, sondern auch das
Frontend reaktionsfähiger gemacht.

### Such- und Filterlogik

`params` wird per `useMemo` aus Query, Filtern und Seite gebaut
(`App.tsx:334-343`), mit 180 Millisekunden Debounce (`App.tsx:385`). Der Effekt bei Zeile 388
korrigiert die Seite, wenn ein Filter die Ergebnismenge so verkleinert, dass die
aktuelle Seite leer würde — ein Detail, das zeigt, dass die Paginierung
durchdacht eingeführt wurde.

Die Suchanfrage verzweigt im Client: mit `q` geht es an `/api/search`, sonst an
`/api/books` (`frontend/src/api.ts:24-25`). Das ist eine Architekturentscheidung
mit Folgen: **`/api/search` kennt weder Filter noch Paginierung**
(`def search`). Eine Volltextsuche in einer 100.000-Bücher-
Bibliothek liefert damit immer 100 Treffer, sortiert nach FTS-Rang, ohne die
aktiven Filter. Wer in einer gefilterten Ansicht sucht, bekommt eine andere
Menge als die Filter vorgeben. Bei 33 ms Antwortzeit fällt es nicht auf; die
Inkonsistenz ist trotzdem da und gehört zu den Dingen, die Feature 04 oder ein
späterer Ausbau angeht.

### CSRF und Fehlerbehandlung

Der CSRF-Token liegt in einer Modulvariablen (`frontend/src/api.ts:3`) und wird
von `apiFetch` bei jeder schreibenden Methode gesetzt (`:9`). Bei 401 feuert das
Modul ein DOM-Ereignis (`api.ts:10`), auf das `AuthGate` reagiert. Fehler werden
zentral in `json<T>` übersetzt (`:16-21`), und der Meldungstext wird aus drei
möglichen Formen extrahiert — ein Hinweis, dass die Fehlerstruktur uneinheitlich
ist: `detail` als Objekt, `detail` als String, oder `error.message`.

## Fehlerbehandlung und Observability

Einheitlich und gut: Fehlerantworten tragen `{"code", "message"}` unter
`detail`, und alle nicht behandelten SQLAlchemy-Fehler werden mit
`exc_info` protokolliert und zu einem generischen 500 mit Code
`database_error` übersetzt (`@app.exception_handler(SQLAlchemyError)`). Interne Meldungen
gelangen nicht nach außen.

Die Protokollierung ist auf zwei Kanäle verteilt: ein `FileHandler` in
`logs/goblin.log` plus `stderr` (`configure_logging()`), und `logger`-Aufrufe
in den Modulen. Die wichtigsten Fehlerpfade sind mit Kontext protokolliert:
Importfehler mit Dateiname (`:215`), Cover-Fehler als Warnung (`:200`),
Duplikate als Info (`:168`).

Ein Detail, das mehr ist als Kosmetik: `configure_logging()` wird im
`lifespan` aufgerufen (`:80`), **nach** dem Modulimport. `settings` wird auf
Modulebene initialisiert (`:56`), und `get_settings()` ist mit `lru_cache`
verzögert (`backend/config.py:73`). Beides ist unkritisch, aber die Reihenfolge
bedeutet, dass Fehler beim Settings-Laden vor dem Logger-Setup stattfinden und
damit nur über `stderr` sichtbar sind.

Es gibt **keine Metriken und kein Request-Logging.** Für eine Anwendung, die
sich als lokal und单机 bewirbt, ist das vertretbar. Für die Frage „warum dauerte
dieser Import vierzig Minuten" gibt es keine Antwort außer einem Logfile ohne
Zeitbezug zwischen den Phasen.

## Tests

1777 Zeilen Tests bei 5311 Zeilen Produktionscode — ein Verhältnis von rund
1:3, das für ein Nebenprojekt dieser Größe ungewöhnlich gut ist. 79 Tests bestehen
mit einem Starlette-Hinweis.

| Testdatei | Zeilen | Gegenstand |
| --- | ---: | --- |
| `test_covers.py` | 347 | Bildformat-Erkennung, Größen, Provider |
| `test_language.py` | 280 | Extraktion je Format, PalmDOC, Fingerprint |
| `test_isbn.py` | 226 | Scoring, Work-Match, Kandidaten |
| `test_ai.py` | 175 | Provider gegen Fake, Promptversion |
| `test_translation.py` | 164 | EPUB-Segmentierung, Validierung |
| `test_updater.py` | 102 | Versionsvergleich, Installationsmodi |
| `test_providers.py` | 47 | Provider-Auswahl und -Fallback |
| `test_metadata.py` | 41 | Metadaten-Normalisierung |
| `test_maintenance.py` | 32 | Wartungsläufe |
| `test_repository.py` | 110 | Abfragen, Tags, FTS |
| `test_imports.py` | 74 | Jobablauf, Fehlerisolierung |
| `test_ai_config.py` | 64 | Konfiguration, Secretbehandlung |
| `test_auth.py` | 59 | Anmeldung, Throttling, CSRF |
| `conftest.py` | 56 | Fixtures |

Die Abdeckung folgt der Fachlogik: dort, wo Entscheidungen fallen — Scoring,
Bildvalidierung, Textextraktion, Anmeldung — ist sie dicht.

### Was fehlt

1. **Kein Test der Nebenläufigkeit.** Weder `ai_tagging_lock` noch der
   Filter-Cache noch das Verhalten bei parallelen Schreibvorgängen werden
   geprüft. Der Filter-Cache mit Lock und TTL ist die Stelle, an der ein
   Datenrennen schwer zu finden wäre.
2. **Kein Test der SSE-Zustellung.** `EventBroker.publish` und `stream`
   (`backend/imports.py:76-96`) sind nicht getestet, obwohl das Verhalten bei
   voller Queue (`QueueFull` wird still geschluckt, `:81-82`) eine
   Datenverlustroute ist.
3. **Kein Test des Dateisystem- und Datenbank-Rollbacks.** Die vier
   Schreibpfade mit Datei und Transaktion sind der fehlerträchtigste Teil des
   Projekts und haben keine Tests für den Fehlerfall.
4. **Kein Performance- oder Lasttest.** Das AUDIT hat gezeigt, dass das
   Verhalten bei 100.000 Büchern von der Antwortgröße abhängt; das ist nicht
   regressionsgeschützt.
5. **Kein Test für Mehrbenutzerbetrieb oder mehrere Browser** — was angesichts
   des `ai_tagging_lock` und der Einbenutzer-Anmeldung auch keiner ist.

## Kopplungshotspots

Nach Auswirkung geordnet, nicht nach Zeilenzahl.

### 1. Der vierfach kopierte Schreibpfad (kritisch)

`_archive`, `_store_refreshed_cover`, `_write_identity` und `_persist_book_tags`
implementieren dieselbe Sequenz: temporäre Datei, `os.replace`, Commit, im
Fehlerfall Datei zurückschreiben und zurückrollen. Vier Stellen, dieselbe
Logik, kein gemeinsamer Code. Jede ist in sich korrekt; die Wahrscheinlichkeit,
dass eine der vier einen Randfall übersieht, ist hoch, und der Fehler ist
still — die Bibliothek ist dann inkonsistent statt kaputt.

**Empfehlung:** Einen Baustein `write_metadata_atomically(book_dir, document)`
extrahieren, der die Datei-Sequenz kapselt und den alten Inhalt für den
Rollback zurückgibt. Danach alle vier Aufrufer darauf umstellen. Rein intern,
keine Verhaltensänderung, gut testbar.

### 2. Metadatenpflege an drei Orten (hoch)

`_fts_text` (`:63-72`) bildet den FTS-Text; `book_to_dict` (`:25-60`) die
API-Antwort; `_archive` und `isbn._write_identity` die Spalten. Fällt eine neue
Metadatenquelle hinzu, muss an mehreren Stellen nachgezogen werden. Der
FTS-Index ist der empfindlichste: `replace_book_fts` wird nach Tag-Änderungen
gerufen, aber nicht nach Titel-, Verlags- oder Serienänderungen — siehe oben.

### 3. Laufzeitbedingungen an einer Stelle (mittel)

Der Laufzeitcheck in `install_available_update` prüft drei Attribute aus drei Modulen für die Update-Sperre,
die Übersetzungsprüfung in `delete_archive` noch mal für das Löschen. Die Liste ist überall, die
Wahrheit verteilt.

### 4. `translation → imports` für `sha256_file` (niedrig, aber billig zu lösen)

Beschrieben oben. `sha256_file` nach `metadata` oder in ein Werkzeugmodul.

### 5. Zwei Session-Disziplinen (niedrig)

`Depends(get_db)` und direkte `SessionLocal()`-Aufrufe im selben Modul, mit
unterschiedlichen Lebenszyklen. `def ai_usage` und `:511` sind Beispiele.

## Risiken

| Risiko | Eintritt | Auswirkung | Beleg |
| --- | --- | --- | --- |
| Inklonsistenz zwischen DB und Dateisystem | mittel | hoch, manuelle Reparatur nötig | vier Schreibpfade ohne gemeinsamen Code |
| Mehrere Uvicorn-Worker | niedrig | hoch, Importereignisse gehen verloren | `jobs`-Dict, `EventBroker` prozesslokal; `--workers 1` nur im Dockerfile |
| Importjob-Verlust bei Neustart | **hoch** | mittel, Fortschritt weg | `imports.py:113`; Update startet Container neu |
| Still verworfene SSE-Ereignisse | mittel | niedrig, UI zeigt alten Stand | `imports.py:81-82` schluckt `QueueFull` |
| KI-Kosten für Tagging nicht erfasst | **hoch** | niedrig, aber irreführende Übersicht | `record_ai_usage` in `generate_book_tags`, `:621-623` mit `cost_usd=0`; keine Raten in `config.py` |
| Migration ohne Transaktion | niedrig | mittel, halb migrierter Zustand | `database.py:45-70` |
| Suche ignoriert Filter und Paginierung | mittel | niedrig | `/api/search` ohne Filter, `def search` |
| Normalisierungslauf bei jedem Start | hoch bei 100k | niedrig, aber wächst | `database.py:62-70` |

## Empfehlungen, priorisiert

**Zuerst, weil klein und datenschutzrelevant:**

1. Gemeinsamen Schreibpfad für `metadata.json` extrahieren und die vier
   Aufrufer umstellen.
2. `--workers 1` als dokumentierte Voraussetzung festhalten — in der README,
   in `lifespan` als Kommentar und als Prüfung beim Start. Besser: eine
   Prüfung, die abbricht, wenn mehrere Worker laufen.
3. Raten für Tagging- und Sprachmodelle konfigurierbar machen und
   `enforce_limit` auch in diesen Pfaden aufrufen.

**Dann, weil Nutzen und Aufwand im guten Verhältnis:**

4. Importjobs persistieren (Feature 01) — das Muster existiert bereits in
   `TranslationJob`, es ist also eine Übertragung, keine Erfindung.
5. Tests für den Rollback-Pfad und den Filter-Cache.
6. SSE-`QueueFull` nicht mehr still schlucken, sondern zählen und melden.

**Später, weil Größe oder Entscheidung offen:**

7. `/api/search` um Filter und Paginierung erweitern, oder die Verzweigung im
   Client auflösen.
8. Startup-Normalisierung auf einmalige Migration umstellen.
9. Content-Volltextsuche (Feature 03) — vorher die Speicherfrage entscheiden.

## Anhang: Modulverzeichnis

| Modul | LOC | Zweck |
| --- | ---: | --- |
| `main.py` | 920 | HTTP-Schicht, Middleware, Endpunkte |
| `isbn.py` | 674 | Werk- und Ausgabe-Auflösung |
| `translation.py` | 654 | Übersetzungsaufträge, EPUB |
| `imports.py` | 442 | Import-Orchestrator, EventBroker |
| `repository.py` | 318 | Abfragen, FTS, Filter-Cache |
| `extractors.py` | 298 | Metadaten aus EPUB, PDF, MOBI |
| `language.py` | 291 | Textextraktion, Spracherkennung |
| `covers.py` | 250 | Coverbeschaffung, Bildprüfung |
| `auth.py` | 195 | Anmeldung, Sessions, Throttling |
| `updater.py` | 180 | Release-Prüfung, Self-Update |
| `providers.py` | 171 | lobid, OpenLibrary, Google Books |
| `models.py` | 169 | ORM-Modelle |
| `metadata.py` | 152 | FieldValue, Normalisierung |
| `database.py` | 137 | Engine, Migrationen, FTS |
| `ai.py` | 123 | KI-Providerabstraktion, Tagging |
| `ai_config.py` | 118 | KI-Konfiguration, Secret |
| `usage.py` | 60 | KI-Kosten, Obergrenzen |
| `config.py` | 77 | Settings |
| `maintenance.py` | 53 | Archiv leeren |
| **Backend gesamt** | **5207** | |
| `App.tsx` | 526 | gesamte Oberfläche |
| `styles.css` | 223 | Styling |
| `types.ts` | 177 | API-Typen |
| `api.ts` | 155 | API-Client, CSRF |
| `TranslationPanel.tsx` | 116 | Übersetzungssteuerung |
| `AiSettingsPanel.tsx` | 107 | KI-Einstellungen |
| `UpdatePrompt.tsx` | 89 | Update-Dialog |
| `AuthGate.tsx` | 63 | Anmeldeschirm |
| `drop.ts` | 46 | Datei-Ablage |
| `main.tsx` | 8 | Einstieg |
| **Frontend gesamt** | **1511** | |
| **Tests gesamt** | **1777** | 79 Tests |
