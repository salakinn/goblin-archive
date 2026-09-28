# Feature 02: Metadaten-Editor

## Ziel

Falsche Provider-Treffer sollen korrigiert werden können, ohne ein Buch zu
löschen und erneut zu importieren.

## Umfang

- `PATCH /api/books/{id}` für Titel, Autoren, Jahr, Sprache, Verlag, ISBN,
  Reihe, Beschreibung und Tags.
- Editor in der Detailansicht, zunächst als Gesamtformular.
- Manuelle Werte erhalten `source: "manual"`.
- Datenbankspalten, `metadata_json`, `metadata.json`, Relationen und FTS in
  einem atomaren Schreibvorgang aktualisieren.
- FTS nach Änderungen neu aufbauen.
- Validierung für Sprache, Jahr, ISBN, Feldlängen und Tags.
- Optional Undo über den vorherigen Feldwert.

## Akzeptanzkriterien

- Änderung bleibt nach KI-Tagging und Spracherkennung erhalten.
- Alle vier Speicherorte bleiben konsistent.
- Fehler beim Dateischreiben rollen Datenbank und Datei zurück.
- `GET /api/books/{id}` liefert die Quelle `manual`.
- Autoren- und Tag-Änderungen aktualisieren Filter und Suche.

## Offene Entscheidungen

- Gesamtformular oder zuerst einzelne Felder?
- Sollen Providerwerte als auswählbare Alternativen angezeigt werden?
- Wie soll der Undo-Verlauf begrenzt werden?
