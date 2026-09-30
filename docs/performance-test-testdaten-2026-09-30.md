# Performance-Test mit `/home/steffen/Work/Testdaten`

Datum: 30. September 2026. Ein einzelner lokaler Durchlauf auf diesem Rechner.

## Umfang und Methode

- Im Ordner liegen 52 Dateien: 43 unterstützte Bücher (18 PDF, 16 EPUB, 7 FB2, 2 MOBI) und 9 Begleitdateien. Die 43 Bücher umfassen 705.636.721 Byte (672,95 MiB).
- Alle 43 Bücher wurden über `POST /api/import` in sieben Anfragen hochgeladen. Jede Anfrage blieb unter dem Standardlimit von 20 Dateien und 200 MiB. Der Import lief mit der Standardkonfiguration von drei parallelen Importarbeitern.
- Der Test nutzte FastAPIs `TestClient` und ein isoliertes Archiv unter `/tmp/goblin-perf-books-data`. Die Originaldateien und das bestehende `goblin-data` wurden nicht verändert.
- Externe Metadaten- und Coverdienste sowie die nachgelagerte ISBN-, Autoren-, Sprach- und Tag-Bearbeitung waren für eine reproduzierbare Messung der lokalen Import-Pipeline deaktiviert. Lokale Metadatenextraktion, Duplikatprüfung, Coverextraktion, Archivierung und Datenbankindexierung liefen regulär.
- Die Zeiten sind Wandzeit einschließlich Multipart-Übertragung innerhalb des Testprozesses, Staging, Import und Statusabfrage. Der Speicherwert ist der maximale RSS des gesamten Testprozesses, einschließlich `TestClient` und Upload-Verarbeitung. Es handelt sich nicht um eine isolierte Messung des Backend-Prozesses.

## Ergebnis

| Kennzahl | Wert |
| --- | ---: |
| Gesamtdauer | 113,81 s |
| Zeit bis zum Abschluss aller Uploads | 92,11 s |
| Restliche Verarbeitung | 21,70 s |
| Gesamtdurchsatz | 5,91 MiB/s |
| Maximaler RSS des Testprozesses | 1.249,6 MiB |
| Erfolgreich archiviert | 41 |
| Identisches Duplikat | 1 |
| Zur Duplikatprüfung angehalten | 1 |
| Fehlgeschlagene Importe | 0 |

Das identische Duplikat war `ABAP-Entwicklung in Eclipse.pdf`; die zweite Datei liegt als Kopie mit anderem Namen im Testordner. `Der Kastanienmann - Søren Sveistrup.epub` wurde wegen möglicher Inhaltsgleichheit zur Prüfung vorgelegt. Es ist noch nicht archiviert; die zugehörige Vorschau liegt im isolierten Testarchiv. Der Import hat die beiden Dateien erwartungsgemäß nicht ungefragt zusammengeführt.

Die Datenbank enthält 41 Bücher (17 PDF, 15 EPUB, 7 FB2, 2 MOBI) und 41 FTS-Einträge. `PRAGMA integrity_check` meldete `ok`. Das Archiv enthält 41 `metadata.json`-Dateien; im Staging liegen nur die Buchdatei und das eingebettete Cover der offenen Duplikatvorschau.

Ein anschließender Einzelabruf über die API lieferte `GET /api/books?limit=50` mit 41 Einträgen in 27,67 ms, `GET /api/search?q=ABAP` mit fünf Treffern in 9,20 ms und `GET /api/import/previews` mit einer offenen Vorschau in 24,44 ms. Diese Zeiten sind einzelne lokale Messungen ohne Netzwerklatenz und keine Lasttest-Perzentile.

Während der PDF-Verarbeitung meldete `pypdf`, dass das optionale Paket `fontTools` für manche CFF-Type1-Schriften fehlt. Der Import wurde dadurch nicht abgebrochen. Die großen PDFs waren der sichtbar zeit- und speicherintensive Teil des Laufs; der Peak-RSS ist Anlass für eine getrennte Profilierung von PDF-Extraktion und Fingerprinting, falls der Speicherverbrauch auf dem Zielsystem problematisch ist.

## Nachanalyse und Verbesserungsmöglichkeiten

Drei Dateien wurden zusätzlich direkt und seriell mit getrennten Zeitmessungen für die lokalen Funktionen verarbeitet. Das sind Einzelmessungen mit bereits zuvor gelesenen Dateien, kein Vergleich mit dem parallelen Gesamtlauf. Wiederholte Schriftwarnungen von `pypdf` waren bei dieser Nachmessung unterdrückt.

| Datei | Metadaten und Cover | Text normalisieren einschließlich Extraktion | Shingles bilden | Signatur berechnen |
| --- | ---: | ---: | ---: | ---: |
| ABAP Workbench – 100 Tipps & Tricks.pdf | 0,240 s | 6,050 s | 0,115 s | 0,655 s |
| Discover Logistik mit SAP.pdf | 0,334 s | 11,888 s | 0,249 s | 1,497 s |
| American Psycho.epub | 0,017 s | 0,271 s | 0,257 s | 1,574 s |

Für die Textextraktion der beiden PDFs betrug die CPU-Zeit 6,009 bzw. 11,747 Sekunden und lag damit nahezu auf Höhe der Wandzeit. Die verwendete Python-Umgebung hat den GIL aktiviert. CPU-Arbeit in zusätzlichen Threads verspricht deshalb hier keine entsprechende Beschleunigung.

Prioritäten aus Messung und Codeprüfung:

1. **PDF-Speicherbedarf reduzieren.** Die installierte `PdfReader`-Implementierung liest bei Übergabe eines Pfads die gesamte Datei in einen `BytesIO`-Puffer. Binäre Dateihandles mit begrenzter Lebensdauer vermeiden diese vollständige Dateikopie. Metadatenextraktion und Fingerprinting öffnen dieselbe Datei derzeit getrennt. Ein gemeinsamer Analyseablauf könnte Reader und Ergebnisse wiederverwenden; PDF-Objektbäume und Text benötigen weiterhin Speicher.
2. **CPU-Arbeit passend parallelisieren.** PDF-Textextraktion und Fingerprinting in einem kleinen, begrenzten Prozesspool untersuchen; zunächst ein und zwei Prozesse vergleichen. Prozesspfade statt großer Textobjekte übergeben. Gesamt-RSS aller Prozesse und API-Reaktionszeiten während des Imports messen, bevor die Parallelität erhöht wird.
3. **Ähnlichkeitssignaturen beschleunigen und Analysen wiederverwenden.** `signature_for()` durchläuft jeden Shingle 32-mal in Python. Hier lohnt ein Vergleich mit einer optimierten Berechnung, die exakt dieselben Signaturen erzeugt. Beim Wechsel vom Direktimport zur Duplikatvorschau werden Hash, Metadaten und Fingerprint erneut berechnet. Auch der Detailvergleich liest beide Buchtexte erneut. Bereits vorhandene Ergebnisse sollten innerhalb eines Auftrags wiederverwendet werden, ohne einen unbegrenzten Textcache aufzubauen.
4. **Upload-Verwaltung vereinfachen.** `_store_uploads()` zählt pro 1-MiB-Block synchron sämtliche Staging-Dateien; `_write_upload_chunk()` öffnet und schließt die Zieldatei pro Block. Eine konsistente Platzreservierung mit Zähler und ein offenes Handle pro Datei vermeiden diese wiederholte Arbeit. Bereinigung, Vorschaucovers und parallele Uploads müssen den Zähler korrekt aktualisieren. Der Anteil dieses Aufwands an den bisherigen 113,81 Sekunden wurde nicht isoliert gemessen.
5. **Messung erweitern.** Backend und Upload-Client als getrennte Prozesse ausführen, Phasenzeiten pro Datei, gesamten Prozessspeicher und Antwortzeit-Perzentile während des Imports erfassen. Danach auch die reguläre Nachbearbeitung und externe Dienste einschließen. Die 92,11 Sekunden bis zum letzten Upload enthalten gleichzeitig laufende Analyse und belegen keinen reinen Übertragungsengpass.

Zusätzlicher Qualitätsbefund: Von den 17 archivierten PDFs haben sechs einen vollständigen Textfingerprint; elf werden wegen PDF-Verschlüsselung ohne Textfingerprint geführt. Alle 17 PDFs besitzen ein Cover. Drei EPUBs und vier FB2-Dateien überschreiten das Wortlimit der Ähnlichkeitsanalyse; bei beiden MOBI-Dateien fehlt die Textextraktion für den Fingerprint. Erfolgreiche Archivierung bedeutet daher nicht, dass bei allen Büchern die Inhaltsprüfung vollständig ausgeführt wurde.
