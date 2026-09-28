# Sicherheitsaudit Backend

Status: Statische Analyse plus Laufzeitverifikation am 28.09.2026 gegen
`backend/` (18 Module, rund 3.600 Zeilen) und die laufende FastAPI-App.
Alle Befunde wurden reproduziert; die Testsuite (76 Tests) besteht unverändert.

Der Bericht beschreibt den Ist-Zustand, nicht geplante Änderungen. Die
Schwerpunkte liegen auf Flächen, die unvertrautes Dateimaterial verarbeiten
(Import, Cover, Übersetzung) und auf den Endpunkten, die ohne Anmeldedaten
erreichbar sind.

## Überblick

| Schweregrad | Befund | Ort |
| --- | --- | --- |
| Kritisch | Keine Authentifizierung auf allen Endpunkten | `backend/main.py` |
| Hoch | ZIP-Bomb: fehlende Größenlimits beim EPUB-Zugriff | `backend/extractors.py:53` |
| Hoch | Unbegrenzter Upload auf die Festplatte | `backend/main.py:596` |
| Mittel | Lucene-Query-Injection über EPUB-Metadaten | `backend/providers.py:50` |
| Mittel | Indirekter SSRF über Google-Books-Cover-URL | `backend/covers.py:213` |
| Mittel | Kostenexplosion über unbegrenzte Übersetzungsbudgets | `backend/main.py:222` |
| Niedrig | Unbegrenzte SSE-Abonnenten | `backend/imports.py:73` |
| Niedrig | Kein Rate-Limit für das Update-Passwort | `backend/updater.py:125` |

Die Beispiele unten nutzen `http://127.0.0.1:8000` und einen separaten
Datenverzeichnis, nicht die produktive Installation.

## Kritisch

### 1. Keine Authentifizierung auf allen Endpunkten

Es gibt keine Authentifizierungsschicht: kein `HTTPBearer`, keine Session, kein
`Depends(get_current_user)`. Die einzigen `Depends`-Aufrufe sind `Depends(get_db)`
in neun Endpunkten. Damit sind alle 35 Routen frei erreichbar, auch die
verwaltenden.

Verifiziert gegen eine laufende Instanz:

```
GET    /api/settings/ai                        → HTTP 200, Konfiguration ausgelesen
PUT    /api/settings/ai  {"api_key":"sk-…"}     → HTTP 200
       cat goblin-data/ai-settings.json
       "api_key": "sk-ATTACKER-INJECTED-KEY-xyz999"    ← Original-Key ersetzt
DELETE /api/archive?confirmation=LÖSCHEN        → HTTP 200
```

Auswirkungen:

- **API-Key-Übernahme.** `PUT /api/settings/ai` (`main.py:136`) überschreibt den
  OpenAI-Schlüssel im Klartext auf der Platte. Danach laufen `test_ai_settings`
  und die KI-Endpunkte entweder mit dem Schlüssel des Angreifers oder das Opfer
  zahlt mit.
- **Archiv-Löschung.** `DELETE /api/archive` (`main.py:619`) führt
  `clear_archive` aus, das `library/` und `staging/` per `rmtree` entfernt
  (`maintenance.py:45,51`). Der Query-Parameter `confirmation=LÖSCHEN` ist kein
  Sicherheitsmerkmal, sondern nur eine UI-Bestätigung; das Literal steht im
  Quelltext.
- **Kostenexplosion.** `TranslationRequest.budget_usd` ist `float | None = None`
  (`main.py:222`), `None` bedeutet unbegrenzt. Die Grenze wird in
  `translation.py:428` nur geprüft, wenn sie gesetzt ist, und lässt sich über
  `PUT /api/translations/{id}/budget` wieder anheben.
- **Datenabfluss.** `GET /api/books`, `/api/books/{id}` (inklusive
  `metadata_json`) und `/api/books/{id}/download` geben Bibliothek und
  Originaldateien frei aus.

`updater.py:125` ist die einzige Stelle mit einer echten Prüfung. Der Vergleich
nutzt korrekt `hmac.compare_digest`, hat aber weder Rate-Limit noch Lockout.

Das Fehlen von Konten ist in `README.md` unter „Aktuelle MVP-Grenzen“ als
bewusste Entscheidung dokumentiert. Solange die App ausschließlich im lokalen
Netz laufen soll, ist das vertretbar — die Kombination aus Portfreigabe
(`deploy/truenas-compose.yml` veröffentlicht `30080`) und fehlender
Absicherung ist es nicht.

**Empfehlung:** Globale Authentifizierungsabhängigkeit für alle `/api/*`-Routen,
Session-Token oder HTTP-Basic-Auth gegen ein Secret aus Docker Secrets. Der
Lösch-Endpoint sollte ein serverseitig erzeugtes, nicht vorhersagbares
Bestätigungstoken verlangen statt eines Literals. Für die Übersetzung ein
serverseitiges Standardbudget vorsehen, das der Client nur senken, nicht
aufheben kann.

## Hoch

### 2. ZIP-Bomb: fehlende Größenlimits beim EPUB-Zugriff

`translation.py:58-64` implementiert mit `_safe_zip` Limits für Eintragsanzahl,
Gesamtgröße und Einzeldateigröße. **`extractors.py` und `language.py` prüfen das
nicht.** `_zip_member` ruft direkt `archive.read()` auf (`extractors.py:53`),
ebenso `epub.read_epub` intern (`extractors.py:180`).

Reproduktion mit einer 2 MB großen EPUB, die auf 2 GiB entpackt:

```
on-disk EPUB size: 2039 KiB  (expands to 2 GiB)
  File "backend/extractors.py", line 53, in _zip_member
    return archive.read(normalized)
MemoryError: Unable to allocate output buffer.
```

`MemoryError` ist eine `Exception` und wird von `imports.py:214` abgefangen, die
2 GiB sind zu diesem Zeitpunkt aber bereits alloziert. Bei mehreren gleichzeitigen
Importen greift der OOM-Killer. `language.py:94,97` liest EPUBs auf demselben
unbegrenzten Weg.

**Empfehlung:** `_safe_zip` aus `translation.py` in `extractors.py` und
`language.py` aufrufen, vor jedem Zugriff auf ein hochgeladenes Archiv.

### 3. Unbegrenzter Upload

`main.py:596-604` schreibt in 1-MiB-Blöcken ohne Obergrenze; `File(...)` setzt
keine `max_length`. Verifiziert:

```
POST /api/import  500MB -> HTTP 202 in 2.0s
staging: 500M          ← keine Ablehnung
```

**Empfehlung:** `Content-Length` vor dem Streamen prüfen, den Schreibvorgang
abbrechen, sobald ein kumulatives Limit (etwa 200 MB) überschritten wird, und die
Anzahl der Dateien je Anfrage begrenzen.

## Mittel

### 4. Lucene-Query-Injection über EPUB-Metadaten

`providers.py:50` und `providers.py:98` setzen Titel und Autor per
String-Interpolation in die Suchanfrage, ohne zu escapen. Beide Felder stammen
aus der Dublin-Core-Struktur der hochgeladenen Datei und sind damit frei
kontrollierbar. `isbn.py:140-144` escaped korrekt, `providers.py` nicht.
Nachgewiesen:

```
lobid   q = title:"x" OR *:*  #" AND contribution.agent.label:"a" && (*:* "
google  q = intitle:"x" OR *:*  #"+inauthor:"a" && (*:* "
```

Der Angreifer bricht aus der Zitatphrase aus und steuert die Query.

**Empfehlung:** `"` und `\` in Titel und Autor escapen, nach dem Muster aus
`isbn.py:140-144`.

### 5. Indirekter SSRF über Google-Books-Cover-URL

`covers.py:213` lädt die `imageLinks`-URL aus der Google-Books-Antwort ohne
Hostprüfung, und `follow_redirects=True` (`covers.py:247`) erlaubt die
Weiterleitung auf beliebige interne Ziele. Wer ein Buch bei Google Books anlegt,
kontrolliert dieses Feld. Es gibt keinen direkten URL-Eingang von Clients, was
den schlimmsten Fall ausschließt; der vorhandene `http://`→`https://`-Rewrite
zeigt aber, dass die Gefahr erkannt und der Host nicht geprüft wurde.

**Empfehlung:** Allowlist auf die Google-Books-Bildhosts, `follow_redirects=False`
und manuell mit erneuter Hostprüfung weiterleiten.

### 6. SSE-Abonnenten unbegrenzt

`imports.py:83-84` begrenzt die Warteschlange auf 100 Einträge und verwirft
Ereignisse bei voller Queue — das ist richtig. `self.subscribers`
(`imports.py:73`) wächst jedoch unbegrenzt, weil `/api/events` ohne Auth
geöffnet werden kann. Jede offene Verbindung belegt eine Queue und einen Task.

**Empfehlung:** Anzahl gleichzeitiger Streams begrenzen, Nutzer auf `/api/events`
authentifizieren.

## Niedrig

- `updater.py:125` — Passwortvergleich ohne Rate-Limit und ohne Sperre nach
  Fehlversuchen. Die Timing-Seite ist durch `compare_digest` korrekt.
- `main.py:220-227` — `target_language`, `profile` und `glossary` haben keine
  Längenlimits und gelangen unverändert in die Prompts.
- `imports.py:214` — `except Exception` fängt `MemoryError` mit und verbucht ihn
  als normalen Importfehler, was die Ursache in den Logs verschleiert.

## Bewusst gute Vorkehrungen

Diese Punkte wurden geprüft und waren nicht zu beanstanden:

- **Keine SQL-Injection.** Alle Zugriffe sind parameterisiert. `repository.py:257`
  zerlegt die Suchanfrage mit `re.findall(r"[\w]+")` auf maximal 12 Token; FTS5-
  Operatoren (`*`, `:`, `"`, `NEAR`) können die Query nicht verlassen.
- **Keine Path-Traversal.** `sanitize_component` (`metadata.py:128`) wandelt
  `../../../etc/passwd` in `etc passwd` um. `_safe_library_file` (`main.py:405`)
  prüft mit `resolve()` plus `relative_to()` die Bindung an das Bibliotheksverzeichnis.
- **Keine XXE.** `xml.etree.ElementTree` löst externe Entitäten nicht auf
  (verifiziert: `ParseError undefined entity`), und der Billion-Laughs-Schutz
  von libexpat greift ab neun Ebenen (verifiziert: `limit on input amplification
  factor breached`). `translation.py:71` nutzt zusätzlich `defusedxml`.
- **Keine Command-Injection.** Kein `subprocess`, `os.system`, `eval` oder
  vergleichbares im gesamten Backend.
- **CORS korrekt konfiguriert.** `http://evil.example` wird nicht reflektiert
  (verifiziert: kein `access-control-allow-origin`), trotz `allow_credentials=True`.
- **Prompt-Injection adressiert.** `ai.py:85`, `language.py:249` und
  `translation.py:445` weisen Eingaben ausdrücklich als Daten aus; Ergebnisse
  laufen durch Pydantic-Schemas mit festen Längen.
- **Atomare Schreibvorgänge** mit Rollback für Import, Metadaten und Cover.
  `ai-settings.json` wird mit `O_EXCL` und Modus `0600` geschrieben, der Upload
  ebenfalls mit `O_EXCL`, was Symlink-Races verhindert.
- **Container-Härtung** — Nicht-Root-Benutzer UID 568, `cap_drop: ALL`,
  `no-new-privileges`.

## Empfohlene Reihenfolge

1. Authentifizierung einführen und auf alle `/api/*`-Routen anwenden.
2. `_safe_zip` nach `extractors.py` und `language.py` übernehmen.
3. Upload-Limit für `/api/import` einführen.
4. Query-Escaping in `providers.py` und Host-Allowlist in `covers.py` ergänzen.
5. Restliche Punkte aus „Niedrig“ abarbeiten.
