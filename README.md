# Goblin Archivar

Goblin Archivar ist ein lokal betriebener E-Book-Archivar mit einer dunklen, schlanken Weboberfläche. EPUB-, PDF-, MOBI- und AZW3-Dateien können einzeln, gemeinsam oder als kompletter Ordner in das Browserfenster gezogen werden. Der Goblin liest Metadaten, ergänzt sie über öffentliche Bibliotheksdienste, archiviert jede Datei unter einem sicheren Namen und indexiert die Sammlung in SQLite.

Über „Ordner importieren“ lässt sich alternativ ein Verzeichnis auswählen. Goblin durchsucht auch dessen Unterordner und übernimmt alle kompatiblen EPUB-, PDF-, MOBI- und AZW3-Dateien; andere Dateien werden übersprungen.

Der MVP arbeitet ausschließlich mit **COPY-Semantik**: Die Originaldatei auf dem Rechner wird weder verändert noch gelöscht. Exakte Duplikate werden über den SHA-256-Inhaltshash erkannt.

## Architektur

- `backend/`: FastAPI, SQLAlchemy, SQLite/FTS5, Import-Pipeline und Provider
- `frontend/`: React, TypeScript und Vite ohne UI-Framework
- `goblin-data/`: lokale Nutzdaten, Archiv, Staging, Logs und Datenbank
- REST für Bibliothek und Import, Server-Sent Events für Live-Fortschritt

Die Import-Pipeline schreibt zunächst ins Staging. Anschließend validiert und analysiert sie die Datei, baut einen temporären Archivordner und benennt diesen atomar um. Erst danach wird der Datenbankeintrag in einer Transaktion angelegt. Schlägt die Transaktion fehl, wird der neue Archivordner wieder entfernt. Vorhandene Archivdateien werden nie überschrieben.

Cover durchlaufen eine getrennte, fehlertolerante Pipeline: **embedded → Open Library → Google Books**. EPUB2-/EPUB3-Cover und, soweit zuverlässig verfügbar, PDF-Thumbnails sowie MOBI-/AZW3-Cover werden zuerst aus der Buchdatei gelesen. Externe Treffer werden im MVP ausschließlich per ISBN übernommen. Jedes Bild wird vor der Ablage auf Format, Vollständigkeit, Abmessungen und Größenlimit geprüft; unterstützt werden JPEG, PNG und WebP. Ein fehlendes oder beschädigtes Cover sowie ein Provider-Ausfall blockiert den Buchimport nie.

## Voraussetzungen

- Python 3.12 oder neuer
- Node.js 20 oder neuer und npm
- SQLite mit FTS5 (in üblichen Python-Installationen enthalten)

## Installation

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cd frontend
npm install
cd ..
```

## Entwicklungsstart

Beide Prozesse gemeinsam starten:

```bash
./dev.sh
```

Danach ist die WebUI unter <http://127.0.0.1:5173> erreichbar. Alternativ in zwei Terminals:

```bash
.venv/bin/uvicorn backend.main:app --reload
```

```bash
cd frontend && npm run dev
```

Die API-Dokumentation liegt unter <http://127.0.0.1:8000/docs>.

Für Tests kann das gesamte Archiv interaktiv geleert werden:

```bash
.venv/bin/python clear-archive.py
```

Ohne Rückfrage geht dies mit `--yes`. Alternativ steht die Funktion in der WebUI unter dem Zahnrad bereit. Dabei werden Bibliothek, Staging-Dateien und alle Buchdatensätze gelöscht; Logs und Datenbankschema bleiben bestehen.

## Tests und Build

```bash
.venv/bin/pytest -q
cd frontend && npm run build
```

## Datenverzeichnis

Standardmäßig nutzt Goblin Archivar `./goblin-data`. Ein anderer Ort kann vor dem Start gesetzt werden:

```bash
GOBLIN_DATA_DIR=/pfad/zum/archiv ./dev.sh
```

Die Struktur wird automatisch erzeugt:

```text
goblin-data/
├── library/
│   └── Autor/Titel/bk_a81f92c4/
│       ├── Titel.epub
│       ├── cover.jpg          # falls vorhanden
│       └── metadata.json
├── staging/
├── logs/goblin.log
└── goblin.db
```

`metadata.json` enthält Originaldateiname, Importzeitpunkt, Hash, Dateidaten, alle fachlichen Metadaten und die Quelle jedes Feldes. Der zusätzliche Eintrag `cover` dokumentiert lokalen Dateinamen, Quelle (`embedded` oder `external`), Provider, ISBN, Bildmaße, MIME-Typ sowie bei externen Treffern Abrufzeit und Quell-URL. Ohne Cover ist der Wert `null`. Damit kann die Datenbank später aus dem Archiv rekonstruiert werden.

Cover werden stets lokal beim Buch gespeichert und von der WebUI ausschließlich über Goblin ausgeliefert. Es findet kein dauerhaftes Hotlinking statt. „Cover neu suchen“ wiederholt dieselbe Prioritätskette; ein vorhandenes Bild wird erst ersetzt, wenn der neue Treffer vollständig geladen und validiert ist.

## Werk- und ISBN-Auflösung

Fehlt einem Buch eine verlässliche eingebettete ISBN, kann die Detailansicht über „Werk suchen“ parallel in lobid und Open Library suchen. ISBN-10 und ISBN-13 werden inklusive Prüfziffer validiert und auf ISBN-13 vereinheitlicht. Titel, Autor und Sprache bestimmen die Werkübereinstimmung; Jahr und Verlag liefern davon getrennte Editionsbelege.

Goblin unterscheidet ausdrücklich zwei Identitäten:

- `isbn` ist die sichere ISBN der konkreten importierten Ausgabe. Eine automatische Übernahme erfordert neben mindestens 95 Prozent Übereinstimmung passende editionsspezifische Daten wie Jahr und Verlag.
- `reference_isbn` gehört zu einer werkgleichen Vergleichsausgabe. Sie darf zur Recherche dienen, wird aber niemals als ISBN der importierten Datei ausgegeben oder in den Volltextindex als Editions-ISBN geschrieben.

`work_match` dokumentiert den erkannten Werktitel, Autoren, Sprache, Confidence, Provider-IDs, verwandte Referenz-ISBNs sowie vorgeschlagene Genres und Werkreihen. Diese Hinweise werden noch nicht ungeprüft zu Bibliothekstags gemacht; ihre Quelle bleibt erhalten und ermöglicht eine spätere kontrollierte Tag-Anreicherung. Die UI erlaubt außerdem, einen Kandidaten bewusst als Werkreferenz oder als konkrete Ausgabe zu übernehmen.

Eine Ausgabenübernahme aktualisiert Datenbank, Volltextindex und `metadata.json` atomar und stößt bei fehlendem Titelbild die Cover-Pipeline an. Eine Werkreferenz aktualisiert nur Werkdaten und `metadata.json`; sie verändert weder Editions-ISBN noch Cover. Positive Treffer werden 30 Tage, vollständig negative Treffer sieben Tage in SQLite gecacht. Provider-Ausfälle werden nicht als negative Treffer gespeichert. „Neu abfragen“ umgeht den Cache.

Die ISBN-Auflösung ist vom Dateiimport getrennt, damit große Ordnerimporte nicht durch Kataloganfragen oder Rate-Limits ausgebremst werden. Google Books ist wegen der gegenwärtig nicht verfügbaren API-Quote nicht Bestandteil der ISBN-Kandidatensuche; die Architektur erlaubt einen späteren zusätzlichen Provider.

## Externe Metadatenquellen

Die Standardreihenfolge ist:

1. [lobid](https://lobid.org/)
2. [Open Library](https://openlibrary.org/developers/api)
3. [Google Books](https://developers.google.com/books)

Die Reihenfolge lässt sich zentral konfigurieren, beispielsweise:

```bash
GOBLIN_PROVIDER_ORDER=openlibrary,lobid,googlebooks ./dev.sh
GOBLIN_PROVIDER_TIMEOUT=8 ./dev.sh
```

Zuerst wird per ISBN gesucht; ohne ISBN dienen Titel und Autor als Suchschlüssel. Provider-Fehler oder Timeouts werden protokolliert und brechen einen ansonsten gültigen Import nicht ab. Die Tests verwenden ausschließlich Mocks und benötigen kein Netz.

Für Cover gilt unabhängig von der konfigurierbaren Metadatenreihenfolge fest:

1. eingebettetes Cover
2. Open Library (`ISBN-L`, ohne Standard-Platzhalter)
3. Google Books (größtes verfügbares `imageLinks`-Bild)
4. kein Cover

## API-Auswahl

- `GET /api/health`
- `GET /api/books` mit Filtern für Autor, Tag, Jahr, Sprache, Verlag, Format und Reihe
- `GET /api/books/{id}` und `GET /api/search?q=...`
- `POST /api/books/{id}/tags` und `DELETE /api/books/{id}/tags/{tag_id}`
- `GET /api/books/{id}/cover` (lokales Bild oder `404`)
- `POST /api/books/{id}/cover/refresh` (bestehendes Cover bleibt bei Fehlschlag erhalten)
- `GET /api/books/{id}/isbn/candidates` liefert gespeicherte Editionskandidaten
- `POST /api/books/{id}/isbn/search` sucht und bewertet ISBN-Kandidaten
- `POST /api/books/{id}/isbn/apply` übernimmt eine ausgewählte ISBN als konkrete Ausgabe
- `POST /api/books/{id}/isbn/reference` speichert sie nur als werkgleiche Referenzausgabe
- `POST /api/import` (Multipart, Feldname `files`)
- `GET /api/imports/{id}` und `GET /api/events`
- `GET /api/providers`
- `DELETE /api/archive?confirmation=LÖSCHEN` (Entwicklungsfunktion)

## Aktuelle MVP-Grenzen

- Duplikate bedeuten nur byte-identische Dateien mit gleichem SHA-256.
- Werkmatches und Referenz-ISBNs werden getrennt von konkreten Ausgaben gespeichert; ein automatisches Zusammenführen verschiedener Archivdateien findet weiterhin nicht statt.
- Importjobs leben nur im Speicher und gehen bei einem Backend-Neustart verloren; bereits abgeschlossene Archivdaten bleiben erhalten.
- MOBI/AZW3-Metadaten werden direkt aus gängigen MOBI-/EXTH-Feldern gelesen. Bei exotischen Kindle-Varianten kann der Dateiname als Titel-Fallback dienen.
- PDF-Seiten werden nicht als Cover gerendert. Nur ein explizit eingebettetes PDF-Thumbnail wird verwendet; andernfalls folgen die ISBN-Provider.
- Exotische EPUB-Guide-Seiten oder Kindle-Container ohne direkt zugänglichen Cover-Record können ein eingebettetes Bild enthalten, das im MVP nicht extrahiert werden kann; externe ISBN-Provider bleiben der Fallback.
- Kein Metadaten-Editor, keine Konten, Rechteverwaltung, Cloud-Synchronisation oder Desktop-Hülle.
- Provider-Treffer werden bewusst einfach ausgewählt: erster plausibler Treffer in der konfigurierten Reihenfolge.
