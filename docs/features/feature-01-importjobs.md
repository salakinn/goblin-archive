# Feature 01: Importjobs persistent und wiederaufnehmbar

## Ziel

Importjobs sollen Neustarts und Updates überleben. Bereits archivierte Dateien
dürfen nicht erneut importiert werden; offene Dateien sollen fortgesetzt oder
gezielt pausiert werden können.

## Umfang

- Tabellen `import_jobs` und `import_items` für Status, Dateien und Fortschritt.
- Job vor dem Start der Hintergrundtask persistieren.
- Status je Datei aktualisieren.
- Beim Start `queued`/`running` auf Fortsetzbarkeit prüfen.
- `POST /api/imports/{id}/resume` und optional Pause/Abbruch anbieten.
- Staging-Pfade persistieren und nach Ablauf bereinigen.
- Abgeschlossene Jobs begrenzen oder automatisch archivieren.

## Akzeptanzkriterien

- Container-Neustart verliert keinen Importfortschritt.
- `GET /api/imports/{id}` stimmt mit Datenbank und Dateisystem überein.
- Fortsetzen benötigt keinen erneuten Upload.
- Duplikate werden beim Fortsetzen erkannt und nicht erneut archiviert.
- Fehlerhafte Einzeldateien können erneut versucht werden.

## Offene Entscheidungen

- Sollen laufende Jobs automatisch fortgesetzt oder zunächst pausiert werden?
- Wie lange bleiben Staging-Dateien und abgeschlossene Jobs erhalten?
- Welche Daten müssen bei einem Update zwingend im persistenten Dataset liegen?
