# Feature 03: Volltextsuche im Buchinhalt

## Ziel

Neben Metadaten soll auch der Inhalt von EPUB-, PDF-, MOBI- und AZW3-Dateien
durchsuchbar werden.

## Umfang

- Vollständige, separat begrenzte Textextraktion je Format.
- Eigene FTS-Tabelle für Buchinhalte.
- Indexierung als fortsetzbarer Hintergrundjob, nicht im Importpfad.
- Getrennte Suche nach Metadaten und Inhalt.
- Treffer mit Kapitel, Seitennähe oder Textausschnitt anzeigen.
- Bestand rückwirkend indexieren und Fortschritt anzeigen.
- Fehlerhafte oder geschützte Dateien als nicht indexierbar markieren.

## Akzeptanzkriterien

- Import wird durch die Indexierung nicht spürbar blockiert.
- Indexaufbau überlebt Neustarts und kann fortgesetzt werden.
- Speicherbedarf des Inhaltsindex ist vor Aktivierung sichtbar.
- Ein Buch kann aus der Inhaltssuche direkt geöffnet werden.
- Sicherheitslimits für Archive und XML bleiben erhalten.

## Architekturentscheidung

Vor der Umsetzung entscheiden, ob der Inhalt in SQLite oder in einem separaten
Index gespeichert wird. Bei durchschnittlich 200 KiB Text pro Buch kann eine
Bibliothek mit 100.000 Büchern viele Gigabyte zusätzlichen Speicher benötigen.
