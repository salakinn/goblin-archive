# Feature 04: Werke und Reihendetails
Umsetzungsaufwand: 6/10

## Ziel

Gleiche Werke und alternative Ausgaben sollen sichtbar zusammengehören, ohne
Hardcover, Taschenbuch und Übersetzungen fälschlich als dieselbe Ausgabe zu
behandeln.

## Umfang

- Tabelle `book_reference_isbns(book_id, isbn13)` mit Index auf `isbn13`.
- Bestehende `work_match`-Daten in diese Struktur übernehmen.
- Detailansicht „Weitere Ausgaben“ mit Format, Jahr, Verlag, ISBN und Größe.
- `edition` und `reference` sichtbar unterscheiden.
- Confidence und Quellen anzeigen.
- Reihenansicht und Navigation durch alle Bücher einer Reihe.
- Hintergrundmigration für bereits gematchte Bücher.

## Akzeptanzkriterien

- Bücher ohne Werkmatch zeigen leere Zustände statt Fehler.
- Alternative Ausgaben sind über mindestens eine gemeinsame Referenz-ISBN
  auffindbar.
- Edition und Werk werden unterschiedlich bezeichnet.
- Reihen-Navigation findet auch Bücher ohne vollständigen Werkmatch.
- Migration ist fortsetzbar und blockiert den normalen Import nicht dauerhaft.

## Offene Entscheidungen

- Bleibt `books.series` führend oder wird eine eigene `book_series`-Tabelle
  eingeführt?
- Wie werden widersprüchliche Provider-Reihen zusammengeführt?
