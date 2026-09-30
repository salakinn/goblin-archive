# Feature 13: Schnellere Buchanalyse und geringerer Speicherbedarf beim Import
Umsetzungsaufwand: 8/10

Status: Angefragt. Optimierungen und Beschleunigungsziele sind noch nicht umgesetzt oder nachgewiesen.

## Ziel

Große Ordnerimporte sollen schneller abgeschlossen werden und die API während
der Verarbeitung bedienbar bleiben. PDF-Analyse, Ähnlichkeitssignaturen und
Upload-Verwaltung werden anhand reproduzierbarer Messungen optimiert.
Speicherbedarf und Parallelität werden gemeinsam bewertet.

Als Planungsziel gelten **20–40 % kürzere Laufzeiten** für den lokalen Import
des vorhandenen Testbestands. Bezogen auf den bisherigen Lauf mit 113,81 Sekunden
entspräche das ungefähr 68–91 Sekunden. Diese Spanne ist eine Schätzung, keine
zugesicherte Beschleunigung. Eine Halbierung der Laufzeit ist kein Abnahmekriterium.
Für den vollständigen Ablauf mit externen Diensten und KI-Nachbearbeitung wird
keine gleich hohe prozentuale Verbesserung vorausgesetzt.

## Ausgangslage und Messgrundlage

Der [Performancebericht vom 30. September 2026](../performance-test-testdaten-2026-09-30.md)
dokumentiert den Import von 43 Buchdateien mit insgesamt 672,95 MiB aus
`/home/steffen/Work/Testdaten`: 18 PDF, 16 EPUB, 7 FB2 und 2 MOBI.

| Kennzahl | Bisherige Messung |
| --- | ---: |
| Gesamtdauer | 113,81 s |
| Gesamtdurchsatz | 5,91 MiB/s |
| Maximaler RSS einschließlich Testclient | 1.249,6 MiB |
| Archiviert / identisches Duplikat / manuelle Prüfung | 41 / 1 / 1 |
| Fehlgeschlagene Importe | 0 |

Der Test nutzte drei Importarbeiter und deaktivierte externe Dienste sowie
die nachgelagerte ISBN-, Autoren-, Sprach- und Tag-Bearbeitung. Die Zeit bis
zum Abschluss aller Uploads enthält bereits parallel laufende Buchanalyse.
Sie misst daher keine reine Übertragungszeit. Der RSS enthält Client und
Backend im selben Prozess und eignet sich nicht als isolierter Backendwert.

Zusätzliche Einzelmessungen zeigen die Ansatzpunkte:

| Datei | Metadaten und Cover | Textextraktion und Normalisierung | Signaturberechnung |
| --- | ---: | ---: | ---: |
| ABAP Workbench | 0,24 s | 6,05 s | 0,66 s |
| Discover Logistik | 0,33 s | 11,89 s | 1,50 s |
| American Psycho | 0,02 s | 0,27 s | 1,57 s |

Die Shingle-Bildung kommt mit 0,12–0,26 Sekunden pro Beispiel hinzu.
Die PDF-Textextraktion ist in diesen Messungen CPU-lastig; der verwendete
Python-Interpreter hat den GIL aktiviert.

## Geplante Verbesserungen

### 1. PDF-Dateien speichersparend analysieren

- PDF-Reader erhalten binäre Dateihandles mit klar begrenzter Lebensdauer.
  Bei Übergabe eines Pfads lädt die aktuell installierte Implementierung die
  vollständige Datei zusätzlich in einen Speicherpuffer.
- Metadatenextraktion und Fingerprinting sollen innerhalb einer Analyse
  Reader und bereits ermittelte Ergebnisse wiederverwenden können.
- Handles und große Analyseobjekte werden nach Abschluss oder Fehlern
  freigegeben. Objektbäume, Seiteninhalte und Text bleiben im Speicherbudget
  berücksichtigt; die Umstellung beseitigt nicht sämtliche PDF-Speicherkosten.

### 2. CPU-Arbeit mit begrenzter Prozessparallelität ausführen

- Einen kleinen Prozesspool für lokale Textextraktion und Fingerprinting
  untersuchen. Ein und zwei Analyseprozesse werden auf demselben Rechner
  verglichen; höhere Parallelität wird nur bei nachgewiesenem Nutzen gewählt.
- Arbeitsaufträge übergeben Dateipfade und kleine Parameter. Ergebnisse werden
  kompakt zurückgegeben; Datenbanksitzungen und geöffnete Handles werden nicht
  zwischen Prozessen geteilt.
- Analyseprozesse besitzen eine eigene begrenzte Kapazität. Zusammen mit der
  bestehenden Importwarteschlange entstehen keine unbegrenzten Auftragsmengen.
- Fehler eines Analyseprozesses werden dem betroffenen Auftrag zugeordnet.
  Shutdown, Abbruch und Ressourcenfreigabe funktionieren auch bei laufender Analyse.
- Die Auswahl der Standardeinstellung berücksichtigt Gesamtspeicher aller
  Prozesse und API-Reaktionszeiten, nicht nur den Durchsatz.

### 3. Signaturen optimieren und Ergebnisse wiederverwenden

- Die 32 Durchläufe über alle Shingles in `signature_for()` werden optimiert.
  Die bevorzugte Lösung erzeugt exakt dieselben Signaturen wie bisher.
- Hash, Metadaten, Cover und Fingerprint werden beim Übergang eines Direktimports
  zur Duplikatvorschau wiederverwendet, solange Datei und Analyseversion gleich sind.
- Detailvergleiche mit mehreren Kandidaten sollen den Text der neuen Datei
  nicht für jeden Kandidaten erneut extrahieren. Wiederverwendung bleibt
  mengenmäßig begrenzt und endet spätestens mit dem Analyseauftrag.
- Vollständige Texthashes werden weiterhin nur aus vollständiger Analyse
  gebildet. Stichproben dürfen keinen vollständigen Fingerprint vortäuschen.
- Falls sich die Bedeutung einer Signatur ändern muss, erfordert dies eine
  neue Analyseversion und einen dokumentierten Neuaufbau betroffener Indizes.
  Alte und neue Signaturen dürfen nicht ungeprüft verglichen werden.

### 4. Upload und Staging effizienter verwalten

- Den synchronen Verzeichnisscan pro 1-MiB-Block durch eine konsistente
  Platzreservierung mit Belegungszähler ersetzen. Beim Start wird der tatsächliche
  Bestand erfasst; definierte Abgleichpunkte erkennen Abweichungen.
- Parallele Uploads, Vorschaucovers, Importabschluss, Abbruch und Bereinigung
  aktualisieren Reservierung und Belegung korrekt. Das globale Staging-Limit
  darf auch bei konkurrierenden Anfragen nicht überschritten werden.
- Eine Zieldatei wird pro Upload einmal geöffnet und nach Abschluss oder
  Fehler geschlossen. Schreibzugriffe blockieren nicht den Event-Loop.
- Bestehende Datei-, Anfrage- und Warteschlangenlimits bleiben wirksam.

## Messplan und Akzeptanzkriterien

1. Ein wiederverwendbarer Benchmark führt Client und Backend in getrennten
   Prozessen aus. Jede Messung beginnt mit einem frischen, isolierten Archiv
   und verwendet dieselben unveränderten Quelldateien.
2. Vor Änderungen wird eine neue Referenz unter diesem Messaufbau erhoben.
   Referenz und optimierte Variante laufen jeweils mindestens dreimal auf
   derselben Hardware mit gleichen Einstellungen. Warm- und Kaltstartbedingungen
   werden kenntlich gemacht. Verglichen werden Median und Streuung;
   der alte Testclient-Lauf bleibt eine historische Orientierung.
3. Erfasst werden Gesamtzeit, Durchsatz, CPU-Zeit, Backend-RSS einschließlich
   Analyseprozessen sowie Zeiten je Datei und Phase: Annahme/Staging, Hash,
   Metadaten/Cover, Text, Signatur, Duplikatvergleich und Archivierung.
   Die verwendete Methode zur Ermittlung des Prozessspeichers wird dokumentiert.
4. Während des Imports werden Bibliotheksliste und Suche wiederholt abgerufen.
   Median, p95 und Fehlerquote werden mit Leerlauf und Referenz verglichen.
   Das vorläufige Ziel für diesen Testbestand auf derselben Hardware ist
   p95 unter 500 ms ohne fehlgeschlagene Leseanfragen.
5. Das Ziel von 20–40 % weniger Importzeit wird gegen die neue Referenz geprüft.
   Eine Abweichung wird mit den verbleibenden Engpässen dokumentiert.
   Ein Performancegewinn gilt erst nach Messung als erreicht.
6. Die gewählte Standardeinstellung erhöht den gemessenen Backend-Spitzenspeicher
   gegenüber der neuen Referenz nicht. Schnellere Varianten mit höherem Verbrauch
   werden als gesonderte Konfiguration mit gemessenem Ressourcenbedarf ausgewiesen.
7. Der Testbestand ergibt weiterhin 41 archivierte Bücher, ein identisches
   Duplikat und eine offene Duplikatprüfung ohne Importfehler. Buchdateihashes,
   Metadaten, Cover, Suchindex und Datenbankintegrität werden geprüft.
8. Gezielte Regressionstests sichern unveränderte Signaturen und Duplikatbefunde,
   Wiederverwendung von Analyseergebnissen sowie korrekte Staging-Belegung bei
   parallelen Uploads, Abbruch und Fehlern ab.
9. Ein separater Lauf mit regulärer Nachbearbeitung und externen Diensten
   dokumentiert deren Anteil. Dessen Gesamtdauer wird nicht unmittelbar mit
   dem lokalen Benchmark verglichen.

## Bezug und Abgrenzung

Das Feature ergänzt den parallelen Import (`backend/imports.py`) und die
Duplikatsprüfung (`backend/duplicates.py`). Weitere Nacharbeiten dazu stehen in
[`docs/TODO.md`](../TODO.md).

Die fachliche Qualität der Duplikatprüfung wird für die Beschleunigung nicht
reduziert. Die bestehenden Einschränkungen bei verschlüsselten PDFs, langen
Texten und MOBI-Fingerprints werden dokumentiert und bei der Ergebnisprüfung
berücksichtigt. Eine Erweiterung dieser Formatunterstützung gehört in ein
separates Feature. Die Archivstruktur und die fachlichen Regeln externer
Metadaten- und KI-Dienste werden durch diesen Request nicht umgestellt.
