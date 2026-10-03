# Feature 13: Ordnerimport auf dieser Hardware halbieren

Umsetzungsaufwand: 9/10
Status: Angefragt. Die neue Referenz ist gemessen; die Beschleunigung ist noch
nicht umgesetzt oder nachgewiesen.

## Wunsch und Ziel

Als Nutzer möchte ich einen großen lokalen Buchordner deutlich schneller
importieren können, während Duplikatprüfung, Metadaten, Fingerprints und
Archivintegrität zuverlässig bleiben. Der vorhandene Rechner hat freie CPU-
und RAM-Kapazität, die der Import gezielt nutzen soll.

**Ziel:** Der vollständige lokale Import des eingefrorenen Testdatenordners im
Protokoll `import-folder-v1` dauert auf derselben Hardware im Median höchstens
**35,113 s statt 70,226 s** (mindestens 50 % kürzer). Externe Provider und
KI-Nachbearbeitung sind im Protokoll deaktiviert. Die Messung mit 10.000
vorhandenen Büchern ist ein separates Skalierungsziel.

## Messbasis vom 3. Oktober 2026

[Referenz und Rohdaten](../../benchmarks/results/2026-10-03T06-51-31Z-d36b384/result.md) ·
[Messvorschrift](../../benchmarks/import-folder.md)

- 427 unterstützte Dateien, 2.133.530.565 Bytes, leeres Ausgangsarchiv,
  ein Importarbeiter und ein Backend-Worker.
- Drei gültige Laufzeiten: 70,227 s, 70,150 s und 70,226 s. Höchster
  zeitgleicher Backend-RSS-Peak: 1.419 MiB.
- Je Lauf 421 archivierte Bücher, zwei identische Duplikate, zwei offene
  Prüffälle und zwei dokumentiert defekte EPUBs. Die defekten Dateien bleiben
  Teil des Pakets; zusätzliche Fehler sind unzulässig.

Ein gesonderter, fachlich gültiger Diagnoselauf mit Phaseninstrumentierung
dauerte 70,886 s. Die Phasensummen sind **nicht addierbar**: `fingerprint`
enthält Textgewinnung, Shingles und Signatur.

| Phase | Aufrufe | Summe im Diagnoselauf |
| --- | ---: | ---: |
| Fingerprint einschließlich Unterphasen | 423 | 54,53 s |
| Textgewinnung und Normalisierung | 425 | 31,41 s |
| Shingle-Bildung | 387 | 19,55 s |
| Signatur aus Shingles | 387 | 2,11 s |
| Metadaten und Cover | 425 | 8,38 s |
| Kandidatensuche und Vergleich | 423 | 2,61 s |
| Archivierung einschließlich Datenbank/FTS | 421 | 2,96 s |
| SHA-256 | 427 | 1,03 s |

Von der Textgewinnung entfielen 19,62 s auf EPUB und 11,43 s auf PDF; von der
Shingle-Bildung 18,41 s auf EPUB. Die 26 HTTP-Uploads dauerten zusammen rund
7 s. Die etwa 61 s zwischen Uploads waren überwiegend Verarbeitungszeit in
den fünf definierten Uploadwellen. Der Backend-Prozess verbrauchte während
der Hauptmessung im Mittel ungefähr einen CPU-Kern. Der Rechner hat acht
physische Kerne, 16 logische CPUs und rund 29 GiB RAM. Die ältere
43-Dateien-Messung auf anderer Hardware ist keine kompatible Referenz.

## Delta zur bisherigen Fassung von Feature 13

Die bisherige Fassung von Feature 13 stützte sich auf den
[Performancebericht vom 30. September](../performance-test-testdaten-2026-09-30.md)
mit 43 Dateien und 672,95 MiB auf anderer Hardware.
Der neue Benchmark verwendet 427 Dateien und 2.133,53 MB, ein eigenes
Protokoll und einen Importarbeiter. Laufzeiten, Durchsatz und RSS beider
Messungen dürfen daher nicht als Vorher-nachher-Effekt interpretiert werden.

| Bisheriger Feature-13-Punkt | Neue Erkenntnis und Konsequenz |
| --- | --- |
| 20–40 % kürzere Zeit gegenüber einer damals noch zu erhebenden Referenz; Halbierung ausdrücklich kein Abnahmekriterium | Das Nutzerziel ist jetzt mindestens 50 % gegenüber der neuen 70,226-s-Referenz, also höchstens 35,113 s. Die alte 113,81-s-Zahl liefert dafür keine Vergleichsbasis. |
| PDF-Analyse zuerst optimieren, gestützt auf einzelne langsame PDFs | PDF-Text kostet im gesamten neuen Paket 11,43 s. EPUB-Text kostet 19,62 s und EPUB-Shingles 18,41 s. Die Priorität verschiebt sich auf die gesamte Fingerprint-Pipeline und parallele Analyse; PDF-Optimierung bleibt Teil davon. |
| PDF-Reader speichersparend öffnen und zwischen Metadaten, Cover und Text wiederverwenden | Die neue Messung belegt noch keinen separaten Speichergewinn dieser Änderung. PDF-Metadaten/Cover kosten etwa 2,79 s und PDF-Text 11,43 s. Reader-Lebensdauer wird vor allem bei mehreren Analyseprozessen wichtig; sie braucht eine eigene Speicher- und Laufzeitmessung. |
| Die 32 Durchläufe in `signature_for()` optimieren | Der aktuelle Code verwendet bereits NumPy für die Signatur. Sie kostet nur noch 2,11 s. Die vorangehende Shingle-Bildung kostet 19,55 s und ist jetzt der gezieltere Ansatzpunkt. |
| Analyse für Vorschau und Kandidaten wiederverwenden | Fachlich weiterhin sinnvoll. Im neuen Paket gab es nur zwei Prüffälle; ihre erneute Fingerprint-Analyse kostete zusammen etwa 0,06 s. Sie kann die Halbierung dieses Pakets allein nicht erklären. |
| Platzreservierung statt Staging-Verzeichnisscan pro Uploadblock | Der Verzeichnisscan existiert noch. Alle HTTP-Uploads zusammen beanspruchten etwa 7 s und überlappten mit der Verarbeitung. Der Eingriff bleibt nützlich, hat für dieses 50-%-Ziel aber geringere Priorität als Fingerprints. |
| Ein bis zwei Analyseprozesse prüfen; die Standardeinstellung darf keinen höheren Backend-Peak haben | Die Messung nutzte im Mittel ungefähr einen CPU-Kern bei 1.419 MiB maximalem Backend-RSS. Deshalb werden auch drei und vier Prozesse und ein ausdrücklich begrenzter höherer Speicherbedarf erprobt. Einfache Importparallelität änderte in Piloten fachliche Ergebnisse und braucht zuerst eine stabile Entscheidungsreihenfolge. |
| 41 archiviert, ein Duplikat, ein Prüffall, keine Importfehler als fachliche Erwartung | Diese Erwartung gehört zum alten 43-Dateien-Paket. Für den vollständigen Ordner gelten 421 archiviert, zwei Duplikate, zwei Prüffälle und zwei bekannte defekte EPUBs, jeweils mit Prüfung pro Eingabedatei. |

Die neue Referenz verwendet bewusst nur einen Importarbeiter, weil bei der
Paketvorbereitung mit drei Arbeitern fachlich verschiedene Pilotergebnisse
auftraten. Eine spätere schnellere Variante wird deshalb gegen eine stabile
serielle Referenz bewertet; sie beweist keine Beschleunigung gegenüber der
alten Drei-Arbeiter-Messung. Mehr Parallelität ist nur mit gleichen
Dateiergebnissen ein gültiger Gewinn.

Unverändert wichtig sind frische isolierte Archive, deaktivierte externe
Dienste für die lokale Hauptmessung, bitgleiche Signaturen, vollständige
Duplikatprüfung, begrenzte Warteschlangen, saubere Fehlerbehandlung und die
separate API-Leselastprüfung mit p95-Ziel unter 500 ms. Die neue Referenz
enthielt keine Leselast; eine Aussage zur API-Bedienbarkeit steht deshalb noch
aus. Ein Lauf mit regulärer externer Nachbearbeitung bleibt eine eigene
Messreihe. Die Beschränkungen bei verschlüsselten PDFs, langen Texten und
MOBI-Fingerprints werden durch dieses Feature nicht aufgehoben.

## Umsetzung

### 1. Analyse parallel vorbereiten, Entscheidungen geordnet treffen

- Hash, lokale Metadaten, Text und Fingerprint als begrenzte Analyseaufträge
  vorbereiten. Für CPU-lastige Arbeit einen Pool aus getrennten Prozessen mit
  zunächst zwei, drei und vier Analyseprozessen vergleichen. Dateipfade und
  kleine Ergebnisse übergeben; keine Datenbanksitzungen oder offenen Reader
  zwischen Prozessen teilen.
- Bytegleiche Dateien innerhalb des Pakets müssen genau eine deterministische
  Archiv-/Duplikatentscheidung erhalten. Analyseergebnisse können parallel
  entstehen; Reservierung, Kandidatenentscheidung und Commit brauchen eine
  fachlich feste Reihenfolge oder gleichwertige Sperren.
- Prozessabsturz, Abbruch, Timeout und Neustart dürfen weder verwaiste
  Stagingdateien noch doppelte Bücher hinterlassen. Die Zahl offener Aufträge
  bleibt begrenzt. Eine bloße Erhöhung von `GOBLIN_IMPORT_CONCURRENCY` genügt
  nicht: Bei der Benchmark-Vorbereitung unterschieden sich Piloten mit drei
  Importarbeitern in Duplikat- und PDF-Fingerprintresultaten.
- Mehr Speicher ist auf diesem Zielsystem zulässig. Als vorläufiges Budget
  gelten höchstens 8 GiB zeitgleicher Backend-RSS-Peak über alle Prozesse,
  ohne Swap oder Speicherdruck. Der Client wird getrennt erfasst.

### 2. Shingle-Bildung ergebnisgleich beschleunigen

- `backend/duplicates.py::shingles()` erzeugt für jedes Fünfwortfenster neue
  Teilstücke und Zeichenketten. Eine kompaktere Implementierung oder native
  Ausführung soll diese Arbeit senken.
- Für identische normalisierte Texte müssen Shingles, Texthash, die bereits
  vektorisierte 32-Werte-Signatur und Buckets bitgenau gleich bleiben. Eine
  Änderung ihrer Bedeutung würde eine neue Analyseversion und Indexmigration
  verlangen und gehört nicht zu diesem Performance-Request.
- Mikrobenchmark am eingefrorenen Korpus und anschließende Bestätigung im
  vollständigen HTTP-Import. Selbst das vollständige Einsparen dieser Phase
  (19,55 s) reicht allein nicht für das 50-%-Ziel.

### 3. Textgewinnung und Analyseergebnisse wiederverwenden

- EPUB- und PDFium-Textpfade getrennt profilieren. Bereits extrahierten Text
  innerhalb eines Auftrags für Fingerprint, Kandidatenvergleich und Vorschau
  wiederverwenden, sofern Datei und Analyseversion übereinstimmen. Die
  Wiederverwendung bleibt speicherbegrenzt.
- PDF-Metadaten, Cover und Text nach Möglichkeit mit weniger Reader-Aufbauten
  gewinnen. Prüfung beschädigter und geschützter Dateien bleibt erhalten.
  Textstichproben dürfen keinen vollständigen Fingerprint vortäuschen.

### 4. Kleinere Pfade nach erneuter Messung priorisieren

Kandidatensuche, SHA-256 und Archivierung kosteten jeweils nur ungefähr ein
bis drei Sekunden. Upload und Staging beanspruchten rund sieben Sekunden und
überlappten mit Verarbeitung. Eine dynamische Nachlieferung statt vollständiger
80-Dateien-Wellen kann die Pipeline glätten, muss jedoch Queue- und
Staginglimits einhalten. Diese Arbeit folgt den CPU-Schwerpunkten, sofern
neue Phasenmessungen sie nicht höher priorisieren.

## Abnahmekriterien

1. Die Optimierung wird gegen die unveränderte Revision der
   [Ordnerreferenz](../../benchmarks/results/2026-10-03T06-51-31Z-d36b384/result.md)
   auf demselben Rechner und Paket verglichen. Zehn abwechselnde A/B-Paare
   mit frischen Archivkopien und identischem Runner liefern Rohdaten,
   Mediane, Streuung und ein gepaartes 95-%-Intervall. Zielwerte sind höchstens
   35,113 s Median und mindestens 50 % mediane gepaarte Zeitersparnis. Liegt
   die untere Intervallgrenze höchstens bei 0, ist ein Gewinn nicht belastbar
   nachgewiesen; das Erreichen der 50-%-Marke wird separat ausgewiesen.
2. Jeder Lauf liefert dieselben erwarteten Ergebnisse pro Eingabedatei:
   bekannte Fehler, Buchdateihashes, Metadaten, Cover, Fingerprints,
   Duplikatbeziehungen, FTS und Datenbankintegrität. Schwankende
   PDF-Fingerprintstatus oder Duplikatentscheidungen verhindern die Freigabe.
3. Alle Backend- und Kindprozesse bleiben zusammen unter dem vorläufigen
   8-GiB-Budget. Es gibt keinen Swap, Prozessabsturz, Warteschlangenüberlauf
   oder zusätzlichen Importfehler. Speicherpeak und Prozesszahl werden je
   Lauf dokumentiert.
4. Eine getrennte Leselastprüfung vergleicht Buchliste und Suche während des
   Imports mit der Referenz. Ziel: keine Requestfehler und p95 unter 500 ms
   je Endpunkt, bei mindestens 200 erfolgreichen Antworten je Endpunkt.
5. Regressionstests decken identische Dateien im selben und in verschiedenen
   Batches, ähnliche Inhalte, Prozessabbruch, Timeout, Dateifehler,
   Neuversuch, Stagingbereinigung und bitgleiche Signaturen ab.
   Optimierungen werden einzeln gemessen, bevor ihre Kombination bewertet wird.

## Geltungsbereich

35,113 s beziehen sich auf diesen lokalen Import mit leerem Ausgangsarchiv.
Ein Bestand mit 10.000 Büchern, reguläre externe Anbieter und KI-Nachbearbeitung
brauchen eigene Messreihen. Parallelität muss für andere Rechner begrenzbar
bleiben; höherer Speicherbedarf wird nicht still zur allgemeinen Voreinstellung.
