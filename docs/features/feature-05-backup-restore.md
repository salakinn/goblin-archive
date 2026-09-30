# Feature 05: Vollständiges Backup und Wiederherstellung
Umsetzungsaufwand: 9/10

Status: Weboberfläche, serverseitige Aufträge, ZIP-Prüfung und Wiederherstellung
implementiert. Die in diesem Dokument beschriebenen Last- und Prozessabbruchtests
mit großen produktiven Archiven stehen noch aus.

## Ziel

Das gesamte Archiv lässt sich einschließlich aller Buchdateien und Metadaten
sichern und später vollständig wieder einspielen. Die Sicherung ist auf
derselben oder einer frischen kompatiblen Installation nutzbar. Nutzer müssen
Bücher nicht erneut importieren, katalogisieren oder durch KI analysieren lassen.

Ein Backup ist in diesem Feature immer vollständig. Ein reiner Metadatenexport
ist kein Ersatz dafür und nicht Teil der ersten Version.

## Ausgangslage

`backend/backup.py` bietet bereits Offline-Befehle für Erstellen, Prüfen und
Wiederherstellen eines Backup-Verzeichnisses. Die Implementierung verwendet
die SQLite-Backup-API, sichert `library/`, Staging und Wiederherstellungsjournale,
prüft Prüfsummen und Datenbanken und bewahrt beim Ersetzen das alte Archiv auf.
KI-Schlüssel werden aus der gesicherten Konfiguration entfernt. Die gemeinsame
Archiv-Sperre verhindert derzeit eine Ausführung neben dem laufenden Dienst.

Diese Grundlage wird weiterverwendet und um einen bedienbaren, serverseitigen
Backup- und Restore-Auftrag ergänzt. Die Offline-Befehle bleiben als
Wiederherstellungsweg verfügbar, auch wenn die Weboberfläche nicht startet.

## Inhalt einer Sicherung

| Bestandteil | Inhalt |
| --- | --- |
| Buchdateien | Alle archivierten EPUB-, PDF-, MOBI-, AZW3-, FB2- und sonstigen bereits unterstützten Buchdateien, einschließlich archivierter Übersetzungen |
| Metadaten | Bibliotheksdatenbank und `metadata.json`: Titel, Autoren, ISBN, Sprache, Tags, Reihen, Beschreibungen, Herkunft und manuelle Korrekturen |
| Cover | Alle im Archiv gespeicherten Coverdateien |
| Weitere Bibliotheksdaten | Gespeicherte Duplikatsentscheidungen, Glossare, Übersetzungsstände, KI-Analyseergebnisse und Nutzungsdaten, soweit sie zur Bibliotheksdatenbank gehören |
| Offene Vorgänge | Persistierte Vorschauen und zugehörige Staging-Dateien sowie erforderliche Wiederherstellungsjournale; keine bloß im Prozessspeicher vorhandenen Tasks |
| Einstellungen | Lokale Anwendungseinstellungen einschließlich KI-Konfiguration ohne API-Schlüssel |
| Anmeldung | `auth.db` einschließlich Passwort-Hash; aktive Sitzungen werden nach Wiederherstellung ungültig gemacht |
| Manifest | Backup-Formatversion, App- und Schemainformationen, Zeitpunkt, Buchanzahl sowie Dateipfade, Größen und SHA-256-Prüfsummen |

API-Schlüssel, Update-Geheimnisse, Umgebungsdateien und Anfrageprotokolle mit
Buchtexten werden nicht exportiert. Nach einem Restore müssen ausgeschlossene
Zugangsdaten bei Bedarf erneut eingerichtet werden. Das Passwort der gesicherten
Installation gilt nach Wiederherstellung; die Vorschau weist darauf hin.
Der bestehende Offline-Weg zum Zurücksetzen des Passworts bleibt verfügbar.

## Backup erstellen

Im Einstellungsbereich gibt es die Aktion „Vollständiges Backup erstellen“.
Das Backend prüft freien Speicher und laufende Arbeit. Für einen konsistenten
Stand werden neue schreibende Vorgänge gesperrt und bereits laufende Schreib-
und Hintergrundaufgaben kontrolliert beendet beziehungsweise abgewartet.
Importnachbearbeitung, Übersetzungen und manuelle Änderungen sind eingeschlossen.
Es werden keine Aufträge stillschweigend verworfen.

Erst wenn ein konsistenter Ruhezustand erreicht ist, werden Datenbanken über
die SQLite-Backup-API gesichert und die dazugehörigen Dateien kopiert. Eine
Datenbankkopie allein genügt nicht, wenn Buchdateien oder Metadaten gleichzeitig
verändert werden könnten. Kann laufende Arbeit nicht rechtzeitig abgeschlossen
werden, wird der Grund angezeigt und das Backup ohne Teilresultat abgebrochen.

Die erste Version darf während der Sicherung Schreibzugriffe vorübergehend
sperren. Lesender Zugriff bleibt möglich, soweit er mit der Sicherung vereinbar
ist. Ein manueller Dienststopp ist für die Webfunktion nicht erforderlich.

Die Ausgabe ist eine herunterladbare Datei, vorgeschlagen als ZIP mit
ZIP64-Unterstützung für große Archive. Bereits komprimierte Bücher werden nicht
unnötig stark erneut komprimiert. Der Export erfolgt dateiweise mit begrenztem
Speicherverbrauch. Das fertige Paket wird vor Freigabe vollständig geprüft.

Die Oberfläche zeigt Phase, verarbeitete Datenmenge und Ergebnis. Erst nach
erfolgreicher Prüfung erscheint „Backup herunterladen“. Das Schließen des Tabs
bricht die serverseitige Erstellung nicht ab. Fertige Sicherungen sind über
eine authentifizierte Auftragsübersicht erneut erreichbar und können dort
gelöscht werden. Temporäre Dateien fehlgeschlagener Aufträge werden bereinigt.

## Backup wieder einspielen

1. Über „Backup wiederherstellen“ eine Sicherungsdatei hochladen. Große Backups
   erhalten einen eigenen Uploadpfad und passende Größen- und Speicherprüfungen;
   die Grenzen des normalen Buchuploads werden nicht ungeprüft übernommen.
2. Das Backup zunächst in einem separaten Arbeitsbereich vollständig prüfen.
   Die aktive Bibliothek bleibt während dieser Vorprüfung unverändert.
3. Eine Zusammenfassung mit Sicherungsdatum, Version, Buchanzahl, Gesamtgröße
   und aktueller Bibliotheksgröße anzeigen. Die Oberfläche erklärt: Die
   Wiederherstellung ersetzt das Archiv; sie führt zwei Bibliotheken nicht zusammen.
4. Erst nach ausdrücklicher Bestätigung den Austausch beginnen. Laufende
   Aufgaben werden kontrolliert zum Stillstand gebracht und neue Schreibzugriffe
   gesperrt. Der verfügbare Speicher wird nochmals geprüft.
5. Den geprüften Stand vollständig vorbereiten. Datenbankverbindungen und
   Dateizugriffe vor dem Austausch schließen; anschließend den Datenbestand
   kontrolliert umschalten. Die bisherige Installation als Rückfallkopie behalten.
6. Datenbanken, Dateipfade und Anwendung prüfen und benötigte Caches neu aufbauen.
   Bei erfolgreicher Wiederherstellung abmelden und eine erneute Anmeldung mit
   dem Passwort aus der Sicherung verlangen.
7. Einen Abschlussbericht anzeigen: wiederhergestellte Bücher, Metadaten und
   Cover sowie Hinweise auf neu einzurichtende Zugangsdaten und offene Vorgänge.

Der Vorgang funktioniert auch bei einer leeren, frisch eingerichteten Installation.
Bei geändertem Datenverzeichnis werden interne Referenzen auf das neue Ziel
angepasst. Externe absolute Pfade aus der ursprünglichen Maschine werden nicht
als Schreibziele verwendet.

Ein Restore startet keine kostenpflichtigen KI-Aufträge oder Übersetzungen
automatisch neu. Unterbrochene Vorgänge werden nachvollziehbar markiert.
Wiederaufnahme persistierter Importaufträge richtet sich nach
[Feature 01](feature-01-importjobs.md); das Backup macht flüchtige Tasks nicht
nachträglich wiederaufnehmbar.

## Validierung und Ausfallsicherheit

- Manifest, Format- und Schemaversion werden vor dem Austausch geprüft.
  Backups neuerer, nicht unterstützter Versionen werden verständlich abgelehnt.
  Unterstützte Migrationen älterer Stände laufen nur auf der vorbereiteten Kopie.
- Dateiliste, Größen und Prüfsummen müssen vollständig passen. Für Buchdateien
  wird zusätzlich der gespeicherte `books.sha256` geprüft. Metadaten in
  Datenbank und Dateien müssen unter Berücksichtigung vorhandener
  Wiederherstellungsjournale konsistent sein.
- SQLite-Integrität und Fremdschlüssel sowie referenzierte Bücher, Cover und
  Vorschauressourcen werden geprüft. Fehlende oder beschädigte Dateien werden
  mit ihrem Namen gemeldet; unvollständige Backups werden nicht übernommen.
- Beim Entpacken werden Pfadtraversierung, absolute Pfade, Links, doppelte
  Archiveinträge und ungewöhnliche Dateitypen abgelehnt. Grenzen für entpackte
  Größe und Dateianzahl verhindern unkontrollierten Speicherverbrauch.
- Der Speicherbedarf umfasst Upload, entpackten Stand, Arbeitskopien und
  Rückfallkopie. Platzmangel darf nicht erst nach dem Austausch erkannt werden.
- Der Umschaltmechanismus muss auch bei eingebundenen Datenverzeichnissen im
  Container funktionieren. Ein Umbenennen des Mountpoints wird nicht vorausgesetzt.
- Ein dauerhaft gespeicherter Wiederherstellungsstatus macht einen Abbruch
  während des Austauschs beim nächsten Start erkennbar. Das Backend öffnet
  keinen gemischten Zustand aus alter Datenbank und neuen Buchdateien.
- Bei Fehlern während Austausch oder Abschlussprüfung wird der vorherige Stand
  wieder aktiviert. Die Rückfallkopie wird nach Erfolg nicht automatisch gelöscht;
  die Oberfläche zeigt ihren Speicherbedarf und bietet eine separate Löschaktion.
- Backup-Download, Restore und Löschung sind nur authentifiziert zugänglich.
  Das Paket enthält private Buchdaten und Passwort-Hashes und wird nicht über
  öffentliche statische URLs bereitgestellt.

## Akzeptanzkriterien

- Eine Bibliothek mit verschiedenen Formaten, Covers, Tags, manuellen Metadaten
  und archivierten Übersetzungen lässt sich vollständig sichern und auf einer
  frischen kompatiblen Installation wiederherstellen.
- Buchanzahl, Buchdatei-Hashes, Metadaten, Zuordnungen und Cover stimmen nach
  dem Restore mit dem Sicherungsstand überein. Suche und Downloads funktionieren.
- Ein Backup enthält standardmäßig alle Bücher; es benötigt keine zusätzliche
  Option, um aus einem Metadatenexport eine vollständige Sicherung zu machen.
- Ein beschädigtes, unvollständiges oder inkompatibles Paket wird vor Austausch
  abgelehnt. Die aktive Bibliothek bleibt dabei nutzbar und unverändert.
- Der Benutzer muss das Ersetzen nach der Vorprüfung ausdrücklich bestätigen.
  Es findet keine automatische Zusammenführung mit vorhandenen Büchern statt.
- Gleichzeitige Importe, Nachbearbeitung und Übersetzungen erzeugen keinen
  inkonsistenten Sicherungsstand. Das Backend meldet Warte- oder Sperrgründe.
- Tab-Schließen unterbricht angenommene Backend-Aufträge nicht. Fortschritt und
  Ergebnisse lassen sich anschließend wieder aufrufen.
- Tests mit Platzmangel, Dateifehlern und Prozessabbruch beim Restore belegen,
  dass ein vollständiger alter oder neuer Stand wiederherstellbar bleibt.
- Restore auf einen anderen Datenpfad funktioniert auch im Container. Alte
  Sitzungen werden ungültig; ausgeschlossene Geheimnisse werden nicht exportiert.
- Große Sicherungen werden ohne vollständiges Laden des Pakets in den RAM
  verarbeitet. Die Offline-Wiederherstellung bleibt unabhängig von der UI nutzbar.

## Abgrenzung

Die erste Version bietet manuelles vollständiges Backup und vollständigen
Restore. Automatische Zeitpläne, inkrementelle Sicherungen, Cloud-Ziele,
Zusammenführung zweier Archive und selektiver Restore einzelner Bücher sind
nicht enthalten. Paketverschlüsselung ist eine spätere Erweiterung; die
Oberfläche kennzeichnet die Sicherungsdatei als unverschlüsselt.
