# Goblin Archivar

Goblin Archivar ist ein lokal betriebener E-Book-Archivar mit einer dunklen, schlanken Weboberfläche. EPUB-, PDF-, MOBI- und AZW3-Dateien können einzeln, gemeinsam oder als kompletter Ordner in das Browserfenster gezogen werden. Der Goblin liest Metadaten, ergänzt sie über öffentliche Bibliotheksdienste, archiviert jede Datei unter einem sicheren Namen und indexiert die Sammlung in SQLite.

Über „Ordner importieren“ lässt sich alternativ ein Verzeichnis auswählen. Goblin durchsucht auch dessen Unterordner und übernimmt alle kompatiblen EPUB-, PDF-, MOBI- und AZW3-Dateien; andere Dateien werden übersprungen.

Der MVP arbeitet ausschließlich mit **COPY-Semantik**: Die Originaldatei auf dem Rechner wird weder verändert noch gelöscht. Exakte Duplikate werden über den SHA-256-Inhaltshash erkannt.

## Architektur

Die EPUB-Übersetzung bietet Kapitelvorschau, Glossar, drei Qualitätsprofile
und fortsetzbare Aufträge.

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
`GET /api/books` liefert standardmäßig 50 Einträge und die Gesamtzahl; mit
`limit` (maximal 100) und `offset` kann die Bibliothek seitenweise geladen
werden. Uploads sind standardmäßig auf 200 MiB und 20 Dateien pro Anfrage
begrenzt. Diese Grenzen lassen sich über `GOBLIN_MAX_UPLOAD_BYTES` und
`GOBLIN_MAX_UPLOAD_FILES` anpassen.

## Anmeldung

Beim ersten Start wird im Datenverzeichnis eine Datei `auth-setup-code` mit
Dateimodus `0600` erzeugt. Den Code daraus einmalig in der Weboberfläche
eingeben und ein Admin-Passwort mit mindestens 12 Zeichen festlegen:

```bash
cat goblin-data/auth-setup-code
```

Bei TrueNAS liegt die Datei im gemounteten Dataset
`/mnt/DEIN_POOL/goblin-archive/auth-setup-code`, beim systemd-Dienst unter
`~/goblin-archive/data/auth-setup-code`. Nach der Einrichtung wird der Code
gelöscht. Das Passwort liegt als Scrypt-Hash in `auth.db` im Datenverzeichnis;
aktive Sitzungen liegen dort ebenfalls und überstehen Neustarts. Über das
Zahnrad kann man sich abmelden. Ohne Sitzung sind Buchdaten, Downloads,
Einstellungen und Änderungen an der API gesperrt. `/api/health` bleibt für
Container- und Update-Prüfungen erreichbar.

Wenn das Passwort verloren geht, im laufenden Container beziehungsweise als
Dienstbenutzer im Installationsverzeichnis ausführen:

```bash
python -m backend.auth reset-password
```

Für den systemd-Dienst aus `~/goblin-archive/current` mit
`GOBLIN_DATA_DIR="$HOME/goblin-archive/data" .venv/bin/python -m backend.auth reset-password`
aufrufen; im Container ist `/data` bereits voreingestellt. Der Befehl fragt
das neue Passwort interaktiv ab und beendet alle bestehenden Sitzungen.

Für Zugriff über ein fremdes Netz HTTPS am Reverse Proxy einrichten und
`GOBLIN_AUTH_SECURE_COOKIES=true` setzen. Den Port nicht zusätzlich über HTTP
freigeben. Das Login-Passwort und der Update-Code sind getrennte Geheimnisse.

## Versionen und Updates

Die Weboberfläche prüft alle 15 Minuten auf ein neues veröffentlichtes GitHub
Release. Das gilt für jede Installation. „Später erinnern“ verschiebt das Popup
im jeweiligen Browser um 24 Stunden; „Diese Version ignorieren“ blendet es
bis zur nächsten Version aus. Der Installationsknopf ist aktiv, wenn ein
passender Update-Weg eingerichtet wurde. Ohne ihn zeigt das Popup die neue
Version und einen Link zu den Änderungen.

### TrueNAS SCALE

Der Produktionscontainer liefert Weboberfläche und API über denselben Port `8000`.
Das Image wird bei einem Git-Tag `vX.Y.Z` nach erfolgreichen Tests als
`ghcr.io/salakinn/goblin-archive:X.Y.Z` und `:stable` veröffentlicht. Danach
erstellt der Workflow ein GitHub Release für die Versionsanzeige. Ein Push
ohne Versionstag veröffentlicht kein neues Image. Die Version des Tags muss
`project.version` in `pyproject.toml` entsprechen. Für eine neue Version zuerst
die Versionsnummer dort erhöhen, Änderungen committen und danach den passenden
Tag `vX.Y.Z` erstellen und pushen. Der Workflow baut und veröffentlicht das
Image; der Tag `:stable` zeigt jeweils auf die letzte freigegebene Version.

Für TrueNAS SCALE 25.04 oder neuer unter **Apps → Discover → Install via YAML** die Vorlage
[`deploy/truenas-compose.yml`](deploy/truenas-compose.yml) verwenden. Vorher
`DEIN_POOL` durch den tatsächlichen Poolnamen ersetzen und das Dataset
`/mnt/DEIN_POOL/goblin-archive` anlegen. Für den Update-Knopf zusätzlich
`/mnt/DEIN_POOL/goblin-config` mit drei Dateien anlegen:

- `truenas-api-key`: API-Key eines eigenen TrueNAS-Benutzers mit `APPS_READ`
  und `APPS_WRITE`, als Text ohne Anführungszeichen.
- `truenas-ca.pem`: CA-Zertifikat, dem das HTTPS-Zertifikat von TrueNAS
  vertraut. Der Hostname in `GOBLIN_TRUENAS_WS_URL` muss zum Zertifikat passen.
- `update-password`: ein eigenes, langes Passwort für den Installationsknopf.

Diese Dateien nicht ins Git-Repository legen. `TRUENAS-HOSTNAME` und den
Benutzernamen in der Compose-Vorlage anpassen. Der Container schreibt Bücher,
SQLite-Datenbank, Logs und KI-Einstellungen; er läuft mit UID/GID `568:568`.
Dem Dataset Schreibrechte und den Secret-Dateien Leserechte für diese UID/GID
geben. Die Weboberfläche ist dann
unter `http://TRUENAS-IP:30080` erreichbar. Falls das GitHub-Paket privat ist,
muss TrueNAS Zugang zu GHCR bekommen; bei einem öffentlichen Paket ist keine
Registry-Anmeldung nötig.

TrueNAS unter **Apps → Settings** nach Docker-Image-Updates suchen lassen.
Sobald ein neues GitHub Release und ein neues `:stable`-Image vorliegen, zeigt
Goblin den aktiven Installationsknopf im Update-Popup.
Der Update-Knopf verlangt das getrennte Passwort und startet über die
TrueNAS-API `app.upgrade` mit einem Snapshot der eingebundenen Host-Pfade.
Laufende Importe und Übersetzungen blockieren den Start. Nach dem Neustart
lädt die Weboberfläche neu. Bei einem Fehler in TrueNAS die vorherige
Image-Version wählen; falls das Datenbankschema geändert wurde, zusätzlich
den Dataset-Snapshot wiederherstellen. Ohne die TrueNAS-Zugangsdaten ist der
Installationsknopf deaktiviert; die Versionsmeldung bleibt verfügbar.

### Linux-Server mit systemd

Für eine Installation ohne Container liegt unter
[`deploy/goblin-archive.service`](deploy/goblin-archive.service) ein
`systemd --user`-Dienst und unter
[`deploy/update-systemd.sh`](deploy/update-systemd.sh) der Update-Helfer.
Voraussetzungen sind Python 3.12+, Node.js 20+, npm, Git, curl und systemd
mit User-Diensten. Die Vorlage verwendet `~/goblin-archive` als festen
Installationsordner und Port `127.0.0.1:8000` für einen Reverse Proxy.

Nach dem ersten veröffentlichten Versionstag, aus einem Checkout dieses
Repositories heraus:

```bash
mkdir -p ~/goblin-archive/{releases,data,config} ~/.config/systemd/user
install -m 600 deploy/goblin-archive.service ~/.config/systemd/user/goblin-archive.service
umask 077
openssl rand -base64 36 > ~/goblin-archive/config/update-password
systemctl --user daemon-reload
GOBLIN_UPDATE_ROOT="$HOME/goblin-archive" ./deploy/update-systemd.sh v0.1.0
systemctl --user enable goblin-archive.service
```

`v0.1.0` durch den tatsächlich veröffentlichten Tag ersetzen. Das Passwort
aus `~/goblin-archive/config/update-password` wird beim Update-Klick
eingegeben. Der Helfer lädt den gewählten Tag, installiert Python- und
Frontend-Abhängigkeiten in einem neuen Versionsordner, baut die Oberfläche,
schaltet den Symlink `current` um und startet den Dienst neu. Erst nach einem
erfolgreichen `/api/health`-Check gilt das Update als abgeschlossen. Bei
einem Startfehler werden der vorherige Symlink sowie die zuvor gesicherten
SQLite- und KI-Einstellungen wiederhergestellt. Die Sicherungen liegen unter
`~/goblin-archive/backups`. Bücher und
KI-Einstellungen liegen in `~/goblin-archive/data` außerhalb der Versionen.
Fehler des Update-Helfers stehen in `journalctl --user -u 'goblin-update-*'`.
Für einen Server, der ohne Anmeldung weiterlaufen soll, User-Lingering für
den Dienstbenutzer aktivieren.

Der eigene Login schützt die API. Für Zugriff aus dem Internet HTTPS und
einen Reverse Proxy oder VPN verwenden; der direkte HTTP-Port ist für ein
vertrauenswürdiges lokales Netz gedacht.

Lokal lässt sich das Produktionsimage mit `docker build -t goblin-archive:local .`
bauen. Beim Start `/data` auf ein beschreibbares Hostverzeichnis mounten und
Port `8000` veröffentlichen; ohne Mount wären die Archivdaten beim Entfernen
des Containers verloren.

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

Hintergrund zu Sicherheit und Performance steht im
[Sicherheitsaudit](docs/security-audit.md) und im
[Performanceaudit](docs/performance-audit.md).

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
├── goblin.db
├── auth.db
└── auth-setup-code      # nur bis zur Einrichtung
```

### Backup und Restore

Für ein konsistentes Backup den Dienst zuerst anhalten. Der Dienst und das
Backup-Werkzeug verwenden dieselbe exklusive Archivsperre; ein Backup während
des Betriebs wird mit einer Fehlermeldung abgelehnt. Das Ziel muss außerhalb
des Datenverzeichnisses liegen und noch nicht existieren.

```bash
.venv/bin/python -m backend.backup create /pfad/zum/backup --data-dir /pfad/zum/archiv
.venv/bin/python -m backend.backup verify /pfad/zum/backup
.venv/bin/python -m backend.backup restore /pfad/zum/backup --data-dir /pfad/zum/neuen-archiv
```

Das Backup enthält die Buchdateien, Importvorschauen, beide SQLite-Datenbanken
und KI-Einstellungen **ohne API-Keys**. Es enthält weder `.env` noch externe
Update-Secrets. API-Keys nach einem Restore neu eingeben. Das Backup enthält
weiterhin die Admin-Passwort-Hashes und persönliche Buchdaten: sicher verwahren.
Vor dem Restore werden alle Dateiprüfsummen, die SQLite-Datenbanken und die
SHA-256-Werte der archivierten Bücher geprüft. Ein bestehendes Ziel wird nur mit
`--replace` ausgetauscht; dessen bisheriger Inhalt bleibt unter einem
`before-restore`-Verzeichnis erhalten. Der Dienst muss auch beim Restore
angehalten sein. Anschließend den Dienst neu starten.

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

## KI-Tags

In der Buchansicht ergänzt „Tags per KI setzen“ direkt bis zu fünf passende Tags.
Bestehende Tags bleiben erhalten. Die KI erhält begrenzte Buchmetadaten (Titel,
Autoren, Beschreibung, Sprache, Reihe und Werkhinweise) sowie vorhandene Tags;
Buchdateien werden nicht hochgeladen. Bei unzureichenden Angaben darf sie keine
Tags liefern. Ergebnisse können wie manuelle Tags entfernt werden.

Anbieter, API-Key und Modelle können im KI-Dialog unter dem Zahnrad gespeichert werden. Zur Wahl
stehen OpenAI und ein eigener OpenAI-kompatibler Dienst mit Base URL und API-Key.
Der eigene Dienst muss `POST /chat/completions` am angegebenen API-Stammverzeichnis
unterstützen und ein JSON-Objekt als Antwort liefern. Beispielsweise wird aus
`https://anbieter.example/v1` der Aufruf
`https://anbieter.example/v1/chat/completions`. Ein Anbieter mit einer anderen
API benötigt einen eigenen Adapter. Der Einrichtungsdialog führt durch Verbindung,
Modellwahl und optionale Einstellungen. Zunächst wird ein Modell für alle Aufgaben
gewählt; unter „Optionen“ können Modelle je Aufgabe getrennt eingetragen werden.
Die Verbindung lässt sich vor dem Speichern über die Modellliste prüfen. Danach
testet eine kurze KI-Anfrage das gewählte Modell. Falls der Dienst keine
Modellliste anbietet, kann der Modellname manuell eingegeben und getestet werden.
Bei Diensten mit einem LiteLLM-kompatiblen `GET /v1/model/info` übernimmt der
Dialog die gemeldeten Eingabe- und Ausgabepreise pro Token als USD je Million
Tokens für die gewählten Modelle. Fehlende Preise bleiben manuell einstellbar.
OpenAIs normale Modellliste enthält keine Tokenpreise; dort ist weiterhin eine
manuelle Angabe nötig. Für unterschiedliche Übersetzungsmodelle werden fehlende
Prüfungs- und Lektoratspreise nicht vom Übersetzungsmodell übernommen.
Alternativ können `GOBLIN_AI_PROVIDER=custom`, `GOBLIN_AI_BASE_URL`,
`GOBLIN_AI_CUSTOM_API_KEY` und die Modellvariablen in `.env` gesetzt werden.
Für OpenAI bleibt `GOBLIN_OPENAI_API_KEY` verfügbar. Der Key bleibt im Backend.
`GOBLIN_AI_TIMEOUT` setzt das Zeitlimit je Versuch. Ein vorübergehender Fehler
wird höchstens einmal wiederholt. Kosten fallen beim gewählten Anbieter an.

Herkunft, Modell, Zeitpunkt und Begründung neuer Tags werden pro Buch unter
`tag_sources` in Datenbank-Metadaten und `metadata.json` gespeichert; `ai_tagging`
enthält zusätzlich den Tokenverbrauch des letzten erfolgreichen Laufs. Unveränderte
Buchdaten werden bei erneutem Klick nicht nochmals angefragt. Auch manuell entfernte
KI-Tags werden dadurch nicht sofort erneut gesetzt. Änderungen an Buchkontext,
Modell oder Prompt-Version ermöglichen eine neue Analyse. Fehler werden nicht gecacht.

`POST /api/books/{id}/ai/tags` liefert das aktualisierte Buch, die Anzahl neuer Tags
und einen Cache-Hinweis. Pro Backend-Prozess läuft höchstens eine Tagging-Anfrage
gleichzeitig. Die Verarbeitung erfolgt unabhängig vom Import. Die gemeinsame
Provider-Schnittstelle in `backend/ai.py` kann auch weitere KI-Funktionen bedienen.

## Sprache per KI ermitteln

Die Buchansicht bietet eine getrennte Sprachprüfung über
`POST /api/books/{id}/ai/language`. Goblin liest lokal drei unterschiedliche,
nicht überlappende Textproben aus verschiedenen Stellen der Buchdatei (je höchstens
1.600 Zeichen und mindestens 500 Buchstaben). EPUB-Proben folgen der Lesereihenfolge;
Navigation und erkennbare Titel-/Impressumsdateien werden übersprungen. Bei längeren
PDFs werden die ersten zwei Seiten ausgelassen. Die Stichprobe ist begrenzt und
kann eine Sprache in unberücksichtigten Buchteilen übersehen.

Nur die Textproben gehen an den gewählten KI-Anbieter, ohne Titel, Klappentext oder bisherige
Sprachangabe. Das Modell bewertet jede Probe getrennt. Goblin übernimmt einen
validierten ISO-639-1-Code nur, wenn alle drei Proben eindeutig dieselbe Sprache
ergeben. Bei Mehrsprachigkeit, widersprüchlichen Ergebnissen oder zu wenig Text
bleibt die bisherige Sprache erhalten. Eine manuell bestätigte Angabe
(`metadata.language.source` gleich `manual`/`user` oder `confirmed: true`) ist geschützt.

Unterstützt sind EPUB, PDFs mit Text und unverschlüsselte MOBI/AZW3-Dateien mit
unkomprimiertem oder einfachem PalmDOC-Text. Andere Kindle-Kompressionen und
Textstrukturen werden mit einem Hinweis abgelehnt. Es erfolgt keine DRM-Umgehung
und keine OCR für gescannte PDFs. Datei- und Abschnittsgrößen sind begrenzt.

`GOBLIN_AI_LANGUAGE_MODEL` konfiguriert das Modell unabhängig vom Tagging. API-Key
und Zeitlimit werden gemeinsam verwendet. Wiederholte Aufrufe mit denselben
Proben und derselben Modell-/Prompt-Konfiguration nutzen das gespeicherte Ergebnis.
Fehler werden nicht gecacht. Herkunft, alte Sprachangabe, Bewertungen, Probenpositionen
und Proben-Hashes sowie Tokenverbrauch stehen in `language_detection` und
`language_detection_history` in Datenbank und `metadata.json`; Probenvolltexte
werden dort nicht gespeichert. Bei Fehlern beim Speichern wird die Änderung
zurückgerollt. Die Originalbuchdatei wird nicht verändert.

## EPUB übersetzen

Die KI-Anbindung wird im Zahnrad-Menü der Weboberfläche eingerichtet. Dort
lassen sich Anbieter, Base URL, API-Key, Modelle und optionale Tokenpreise speichern sowie die
Verbindung prüfen. Die Einstellungen gelten sofort für neue KI-Anfragen und
werden lokal in `goblin-data/ai-settings.json` mit Dateirechten `0600` abgelegt.
Der API-Key wird bei späteren Aufrufen der Einstellungs-API nicht zurückgegeben.
Vorhandene `.env`-Werte dienen als Ausgangswerte, bis sie in der UI gespeichert
werden.
OpenAI und der eigene Dienst behalten getrennte Keys. Bei einem Anbieterwechsel
lassen sich bereits vorbereitete Übersetzungsaufträge erst mit ihrer ursprünglichen
Anbindung fortsetzen. Für Kostenlimits müssen die Tokenpreise des gewählten
Anbieters eingetragen werden; wenn dessen Antwort keine Token-Nutzung meldet,
werden die tatsächlichen Kosten als unbekannt gekennzeichnet.

In der Buchansicht eines EPUBs „Buch übersetzen“ öffnen, Zielsprache und Profil
wählen und den Auftrag vorbereiten. Die erste Kapitelvorschau wird separat
übersetzt. Danach lassen sich Glossarregeln im Format `Original => Übersetzung`
und Stilvorgaben speichern und die Restübersetzung starten. Der Fortschritt
bleibt in SQLite erhalten; unterbrochene Aufträge können nach einem Neustart
fortgesetzt werden. Eine fertig validierte Übersetzung erscheint als eigene
Ausgabe mit Verweis auf das Original und ohne dessen Ausgaben-ISBN.

`Schnell` nutzt einen Übersetzungsdurchlauf, `Buch` ergänzt einen Prüf- und
Korrekturdurchlauf und `Literarisch` einen zusätzlichen Lektoratsdurchlauf.
Die Modelle sind über `GOBLIN_AI_TRANSLATION_MODEL`,
`GOBLIN_AI_TRANSLATION_QA_MODEL` und `GOBLIN_AI_TRANSLATION_EDITOR_MODEL`
konfigurierbar. Für Kostenschätzung und optionale Budgetgrenze müssen die
aktuellen Eingabe- und Ausgabepreise je Million Tokens in den Einstellungen oder
in `.env` gesetzt werden. Ohne Preise bleibt die Schätzung unbekannt und eine Budgetgrenze ist
nicht verfügbar. Jeder KI-Aufruf kann Kosten verursachen, auch wenn er abbricht.

Der aktuelle Ablauf unterstützt EPUB-Text und Textattribute, EPUB2-Navigation
und EPUB3-Navigation. Bilder und andere Ressourcen werden übernommen; Text in
Bildern wird nicht übersetzt. Eine technische Prüfung kontrolliert ZIP, XML,
Manifest, interne Verweise und geschützte Formatierungsmarker. Eine sprachliche
Qualitätsgarantie ist damit nicht verbunden.

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
- `GET/PUT /api/settings/ai` liest/speichert die lokale KI-Konfiguration ohne Key-Rückgabe
- `POST /api/settings/ai/test` prüft Key und gewähltes Tagging-Modell
- `GET /api/books` mit Filtern für Autor, Tag, Jahr, Sprache, Verlag, Format und Reihe
- `GET /api/books/{id}` und `GET /api/search?q=...`
- `POST /api/books/{id}/tags` und `DELETE /api/books/{id}/tags/{tag_id}`
- `GET /api/books/{id}/cover` (lokales Bild oder `404`)
- `POST /api/books/{id}/cover/refresh` (bestehendes Cover bleibt bei Fehlschlag erhalten)
- `GET /api/books/{id}/isbn/candidates` liefert gespeicherte Editionskandidaten
- `POST /api/books/{id}/isbn/search` sucht und bewertet ISBN-Kandidaten
- `POST /api/books/{id}/isbn/apply` übernimmt eine ausgewählte ISBN als konkrete Ausgabe
- `POST /api/books/{id}/isbn/reference` speichert sie nur als werkgleiche Referenzausgabe
- `GET/POST /api/books/{id}/translations` listet/erstellt Übersetzungsaufträge
- `GET /api/translations/{id}` liefert Fortschritt und Kapitelvorschau
- `PUT /api/translations/{id}/glossary` speichert Glossar und Stilvorgaben
- `PUT /api/translations/{id}/budget` ändert oder entfernt eine Budgetgrenze
- `POST /api/translations/{id}/start|pause|cancel` steuert den Auftrag
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
- Kein Metadaten-Editor, keine Mehrbenutzer-Konten, Rechteverwaltung, Cloud-Synchronisation oder Desktop-Hülle. Der [Sicherheitsaudit](docs/security-audit.md) beschreibt den Stand vor Einführung der Admin-Anmeldung.
- Provider-Treffer werden bewusst einfach ausgewählt: erster plausibler Treffer in der konfigurierten Reihenfolge.
