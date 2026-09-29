# Feature 05: Backup und Restore

Stand: Der lokale, offline nutzbare Backup- und Restore-Ablauf ist in
`backend/backup.py` implementiert. Er prüft Datenbanken und Dateien vor dem
Restore, bewahrt bei `--replace` das alte Archiv auf und lässt KI-Keys aus.
Ein Backup ohne Dienststopp, UI-Fortschritt und Zeitpläne bleiben offen.

## Ziel

Bibliothek, Metadaten und Konfiguration sollen gegen Datenverlust gesichert und
auf einer frischen Installation wiederhergestellt werden können.

## Umfang

- Konsistenter Datenbankexport über `VACUUM INTO`.
- Optional vollständiges Backup inklusive `library/`.
- Restore mit Prüfung von Pfaden, Prüfsummen und fehlenden Dateien.
- Fortschritt und Ergebnisbericht anzeigen.
- Backup und Restore im laufenden Betrieb sicher koordinieren.
- `auth.db` und Update-Geheimnisse ausdrücklich berücksichtigen.
- KI-Key standardmäßig nicht exportieren; explizites Opt-in anbieten.

## Akzeptanzkriterien

- Backup lässt sich ohne Dienststopp erzeugen.
- Restore auf eine frische Installation ergibt eine nutzbare Bibliothek.
- Fehlende Dateien werden namentlich gemeldet und nicht still verschluckt.
- `books.sha256` wird zur Verifikation verwendet.
- Ein beschädigtes oder unvollständiges Backup wird vor dem Überschreiben
  der aktiven Daten abgelehnt.

## Offene Entscheidungen

- Nur Metadaten oder standardmäßig vollständige Archivdateien sichern?
- Verschlüsselung und Passwortschutz des Backup-Archivs?
- Automatische Zeitpläne oder zunächst nur manueller Export?
