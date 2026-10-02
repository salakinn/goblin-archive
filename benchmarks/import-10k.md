# Arbeitsauftrag für eine KI: Importzeiten bei rund 10.000 E-Books optimieren

Diese Anleitung dient als Arbeitsauftrag für eine KI mit Zugriff auf das
Repository. Einstieg und Ablagekonventionen: [README.md](README.md). Sie enthält keine neuen Messergebnisse. Stand: 2. Oktober 2026.

## Auftrag und Ziel

Analysiere die Importpipeline mit dem Ziel, die Importzeiten bei Bibliotheken
mit rund **10.000 vorhandenen E-Books** zu senken. Der Hauptfall ist das
Hinzufügen neuer Bücher zu diesem Bestand. Untersuche auch identische Dateien
und mögliche Inhaltsduplikate. Ermittle, welche Schritte die Gesamtdauer
bestimmen und welche zusätzlichen Kosten der vorhandene Bestand verursacht.

Liefere einen ausführbaren Benchmark, Rohdaten und einen deutschen Bericht mit
priorisierten Optimierungen. Führe die Messungen tatsächlich aus, soweit die
Umgebung dies ermöglicht. Messskripte, Instrumentierung und isolierte
Optimierungsexperimente gehören zum Auftrag. Die dauerhafte Übernahme von
Produktänderungen erfolgt in einem anschließenden Umsetzungsauftrag.

Die Hauptkennzahlen sind Gesamtimportzeit und Durchsatz. Speicherverbrauch,
API-Bedienbarkeit und fachlich unveränderte Ergebnisse sind Nebenbedingungen.

## 1. Aktuellen Code und bisherige Erkenntnisse prüfen

Lies geltende `AGENTS.md`, prüfe den Arbeitsbaum und bewahre vorhandene Änderungen.
Lies anschließend:

- [Performanceaudit](../docs/performance-audit.md)
- [Importmessung vom 30. September 2026](../docs/performance-test-testdaten-2026-09-30.md)
- [Feature 13: Import-Performance](../docs/features/feature-13-import-performance.md)
- [Offene Nacharbeiten](../docs/TODO.md)
- `backend/main.py`, `backend/imports.py`, `backend/duplicates.py`,
  `backend/previews.py`, `backend/repository.py`, `backend/database.py`,
  `backend/models.py`, `backend/config.py` sowie die aufgerufenen Extraktoren
- Relevante Tests und die Upload-/Statussteuerung in `frontend/src/api.ts`
  und `frontend/src/App.tsx`

Prüfe historische Befunde am aktuellen Code. Die damaligen 113,81 Sekunden für
43 Bücher mit 672,95 MiB wurden mit leerem Archiv gemessen. Der Spitzen-RSS von
1.249,6 MiB umfasste Backend und Testclient gemeinsam. Diese Werte belegen
keine aktuelle Importleistung bei 10.000 Bestandsbüchern.

## 2. Reproduzierbare Messumgebung aufbauen

1. Erfasse Commit, lokale Änderungen, CPU, RAM, Betriebssystem, Python-, SQLite-
   und Paketversionen, Speichermedium, Dateisystem und relevante Einstellungen.
   Dokumentiere konkurrierende Last. Schreibe keine Zugangsdaten in Messartefakte.
2. Lege ein eigenes Testverzeichnis an. Setze `GOBLIN_DATA_DIR` auf dessen
   absoluten Pfad, bevor Backend-Module importiert oder Prozesse gestartet werden:
   `backend/database.py` erzeugt die Engine bereits beim Import. Prüfe die
   effektiven Archiv-, Staging- und Datenbankpfade. Das vorhandene `goblin-data`
   ist kein Benchmarkziel.
3. Starte Backend und HTTP-Client getrennt, mit einem Backend-Worker, freiem
   lokalen Port und ohne Reload. Prüfe die Bereitschaft. Verwende die reguläre
   Anmeldung mit Testzugangsdaten und erforderlichen Cookies/CSRF-Headern.
4. Erstelle einen unveränderten Ausgangsbestand mit 10.000 Büchern. Jede
   Wiederholung beginnt mit einer eigenen konsistenten Kopie einschließlich
   Buchdateien und Analyseindizes. Kopiere SQLite bei beendetem Backend oder
   verwende eine konsistente Backup-Methode. Eine allein kopierte DB-Datei bei
   aktivem WAL reicht nicht. Bestandsaufbau und Kopieren zählen nicht zur Importzeit.
5. Prüfe Eingabedateien anhand eines Manifests mit Größe und SHA-256. Verwende
   dieselbe Eingabereihenfolge und Batchaufteilung für vergleichbare Läufe.
6. Deaktiviere externe Dienste und KI-Nachbearbeitung im lokalen Messaufbau
   nachvollziehbar. Ein leerer API-Schlüssel allein belegt keine vollständige
   Abschaltung. Lokale Extraktion, Cover, Fingerprinting, Duplikatprüfung,
   Archivierung und Indexierung bleiben aktiv.
7. Dokumentiere Prozessneustart, Anwendungscache und Dateisystemcache getrennt.
   Ein neuer Prozess bedeutet keinen kalten Betriebssystemcache. Setze passende
   Laufzeit-, Speicher- und Speicherplatzgrenzen und protokolliere Abbrüche.
8. Beende eigene Prozesse nach dem Lauf. Entferne ausschließlich eindeutig
   zugeordnete temporäre Testdateien.

Führe mindestens drei unabhängige Wiederholungen je Hauptszenario durch.
Berichte Median und Streuung der Gesamtdauer. Drei Läufe ergeben keinen
belastbaren p95 der Importdauer. Trenne Aufwärmphasen von den Messungen.

## 3. Aussagekräftigen Bestand mit 10.000 Büchern vorbereiten

Bevorzuge eine isolierte Kopie eines verfügbaren realistischen Bestands.
Andernfalls erzeuge einen reproduzierbaren Bestand mit festem Seed. Dokumentiere
Formate, Dateigrößen, Textlängen, Autoren, Titel, Reihen, Sprachen und Duplikatanteile.
10.000 Kopien weniger Bücher erzeugen unrealistische Hash-Treffer und Kandidatenmengen.

Der Bestand muss die importrelevanten Daten enthalten: Buchdateien, SHA-256,
normalisierte Vergleichsdaten, Textfingerprints, Ähnlichkeits-Buckets,
Autorenbeziehungen/-Aliase und FTS-Einträge. Prüfe Analyseversionen, Abdeckung
und referenzierte Dateien. Reine Metadatenzeilen ohne Fingerprints und Texte
belegen nur SQL-Teilaspekte, keine realistische Duplikatprüfung bei 10.000 Büchern.
Dokumentiere fehlende Daten und begrenze die daraus gezogenen Aussagen.

Miss denselben zusätzlichen Import gegen ein leeres Archiv und den 10.000er-Bestand.
Verwende hierfür ausschließlich Bücher, die in beiden Fällen neu sind, damit
unterschiedliche Duplikatpfade den Bestandsvergleich nicht verfälschen. Ergänze
5.000 oder 20.000 Bestandsbücher nur zur Prüfung einer konkreten Skalierungshypothese.

## 4. Importlasten messen

Prüfe den vorhandenen Ordner `/home/steffen/Work/Testdaten`. Historisch enthielt
er 43 unterstützte Bücher: 18 PDF, 16 EPUB, 7 FB2 und 2 MOBI. Erfasse den Bestand
neu. Fehlt er, dokumentiere einen Ersatzbestand und die Vergleichsgrenzen.

| Lastfall auf 10.000 Bestandsbüchern | Zweck |
| --- | --- |
| Einzelnes neues E-Book | Wartezeit eines normalen Einzelimports |
| Gemischter Batch, zunächst der vorhandene Testbestand | Hauptreferenz für Gesamtzeit und Durchsatz |
| 100–500 neue Bücher, sofern geeignete Dateien verfügbar sind | Anhaltender Durchsatz und Speicherentwicklung |
| Byte-identische Dateien aus dem Bestand | Früher Hash-Abgleich |
| Ähnliche Inhalte mit unterschiedlichen Dateihashes | Kandidatensuche, Detailvergleich und Vorschauanalyse |
| Große PDFs und lange EPUB-/FB2-Texte | Extraktion und Signaturbildung |

Werte neue Bücher, identische Duplikate und Prüffälle getrennt aus. Ein höherer
Anteil früh erkannter Duplikate darf keinen scheinbaren Durchsatzgewinn erzeugen.
Halte Datei-, Upload- und Warteschlangenlimits ein. Liefere größere Batches in
mehreren zulässigen Anfragen nach und dokumentiere die Nachlieferungsstrategie.

Vergleiche `GOBLIN_IMPORT_CONCURRENCY` mit 1, 2, 3 und 4 Arbeitern bei sonst
identischem Aufbau. Diese Einstellung ist nicht die Anzahl der Backend- oder
Analyseprozesse. Verwende für diese Varianten zunächst den repräsentativen Batch;
vertiefe anschließend die Lastfälle, die einen konkreten Engpass erkennen lassen.

Erfasse:

- Gesamtzeit vom ersten Uploadbeginn bis zum definierten automatischen
  Endzustand aller Dateien; Uploadabschluss und Restverarbeitung zusätzlich.
- Dateien pro Minute und MiB pro Sekunde mit genauer Definition des Nenners.
- Wartezeit je Datei und Phasenzeiten für Staging, Hash, Metadaten/Cover,
  Text/Normalisierung, Shingles/Signatur, Duplikatvergleich und Archivierung/DB/FTS.
- Zahl der SQL-Abfragen, geladenen Kandidaten, tatsächlichen Textvergleiche
  und erneut gelesenen Bestandsdateien pro Importdatei.
- CPU-Zeit, CPU-Auslastung, Speicherverlauf, I/O, Staging-Scans, Dateiöffnungen,
  Kopien, Commitzeiten und beobachtete SQLite-Lock-Wartezeiten.
- Wiederholte Analyse beim Wechsel zur Vorschau oder Vergleich mehrerer Kandidaten.

Eine wartende manuelle Duplikatentscheidung ist ein automatischer Endzustand,
aber kein archiviertes Buch. Warte auch auf die automatisch gestartete
Vorschauanalyse; `needs_review` allein darf die Messung nicht beenden, während
im Hintergrund dieselbe Datei noch analysiert wird. Summiere überlappende
Phasen nicht zur Gesamtzeit. Uploadzeit bei gleichzeitig laufender Analyse ist
keine reine Netzwerkzeit.

Miss Backend und Kindprozesse in festen Intervallen, beispielsweise 100 ms.
Dokumentiere die Methode: Zeitgleich summierter RSS kann gemeinsame Seiten
mehrfach zählen; ergänze unter Linux nach Möglichkeit PSS. Addiere keine
unabhängigen Einzelprozessmaxima zu einem Gesamtpeak. Erfasse den Client separat
und den verbleibenden Speicher nach einer definierten Ruhephase.

Rufe während des Imports Buchliste und Suche mit moderater fester Last ab.
Erfasse geplante und tatsächliche Requeststarts, p50/p95, Fehler und Timeouts
und vergleiche mit dem Leerlauf bei gleichem Bestand. Nenne Stichprobengrößen;
sammle für Perzentile nach Möglichkeit mindestens 200 Anfragen je Szenario.
Diese Messung prüft die Bedienbarkeit unter Importlast.

## 5. Engpässe profilieren und Optimierungen erproben

Profiliere nach der Referenzmessung die teuersten Phasen und Dateien. Nutze
CPU-/Wandzeit und I/O zur Unterscheidung von Rechenarbeit und Wartezeiten.
Führe Profiling und ausführliche SQL-Protokollierung in gesonderten Läufen aus.

| Ansatzpunkt | Benötigter Nachweis |
| --- | --- |
| PDF-Reader/Textextraktion | Weniger Reader-Aufbauten, Dateikopien oder Extraktionen bei gleichem Ergebnis |
| Signaturberechnung | Weniger CPU-Zeit bei exakt gleichen Signaturen |
| Wiederverwendung pro Auftrag | Hash, Metadaten, Cover und Textanalyse werden beim Vorschauwechsel und Kandidatenvergleich seltener berechnet |
| Duplikatsuche im 10.000er-Bestand | Querypläne, Queryzahl, ORM-Ladeaufwand und Kandidatenmenge erklären die Laufzeit |
| Begrenzte Prozessparallelität | Geringere Gesamtzeit bei eingehaltenem Speicherbudget, kompakter Datenübergabe und korrekter Fehlerbehandlung |
| Staging/Archivierung | Weniger Scans, Dateiöffnungen oder Kopien senken die gemessene Dauer |
| Datenbank-Schreibpfad | Weniger Schreib- oder Warteaufwand bei unveränderter Transaktionssicherheit |

Prüfe für langsame Abfragen `EXPLAIN QUERY PLAN`, insbesondere Hash, ISBN, Titel,
Fingerprints, Buckets und Autorenaliase. Behaupte keinen Full Scan oder fehlenden
Index ohne Prüfung. Ein Prozesspool ist eine Hypothese, kein vorgegebenes Ergebnis.

Teste jeweils eine Änderung in einer isolierten Variante und dokumentiere
Patch/Commit und Einstellungen. Vergleiche Referenz und Variante abwechselnd
über mindestens drei Läufe auf derselben Hardware und demselben Ausgangsbestand.
Prüfe schnellere Einzelfunktionen anschließend im vollständigen Importlauf.
Kombiniere erfolgreiche Änderungen erst nach deren Einzelbewertung.

Reduziere für Geschwindigkeitsgewinne weder Analyseumfang noch Kandidatenlimits,
Duplikatqualität, Integritätsprüfungen oder Haltbarkeit der Daten. Prüfe passende
Regressionstests für Signaturen, Duplikatbefunde, Parallelität und Fehlerpfade.

Untersuche Frontend-Batching, SSE/Polling und Filterabrufe gezielt, wenn sie die
Importzeit oder Hintergrundlast beeinflussen. Ein allgemeines Frontend- oder
100.000-Bücher-Audit gehört nicht zum Schwerpunkt.

## 6. Fachliche Ergebnisse und Zielsystem prüfen

Prüfe Buchzahlen, Duplikate, Vorschauen, Fehler, Dateihashes, Metadaten, Cover,
Fingerprint-Abdeckung, SQLite-Integrität und fehlende/verwaiste FTS-Einträge.

Die historischen 41 archivierten Bücher, ein identisches Duplikat und eine offene
Prüfung gelten für den leeren Kontrollbestand bei gleichen Dateien und gleicher
Konfiguration. Im 10.000er-Bestand können zusätzliche Treffer korrekt sein.
Erstelle vor Optimierung eine geprüfte Ergebnisreferenz je Lastfall und vergleiche
jede Variante damit. Fehlende Textanalyse darf nicht als Beschleunigung durchgehen.

Wiederhole die wichtigsten Messungen auf dem vorgesehenen Speichermedium,
beispielsweise einem verfügbaren und als Testziel freigegebenen TrueNAS-Pool.
Ein tmpfs-Lauf belegt keine NAS-Performance. Ein ergänzender Lauf mit regulärer
Nachbearbeitung und externen Diensten weist deren Zeiten, Retries und Rate-Limits
separat aus. Verwende kostenpflichtige Dienste im bereits autorisierten Rahmen.
Fehlende Zugänge blockieren die lokale Analyse nicht.

## 7. Bewerten und dokumentieren

Priorisiere nach eingesparter Gesamtimportzeit bei 10.000 Bestandsbüchern,
Häufigkeit des Lastfalls, Speicherbedarf und Aufwand. Trenne Messbefunde,
Codebefunde und Hypothesen. Zeige eingesparte Sekunden und Prozent:
`(Referenzzeit - Variantenzeit) / Referenzzeit × 100`.

Nutze die Ziele aus Feature 13 als vorläufige Kriterien für den neuen Messaufbau:

- 20–40 % kürzere Gesamtzeit für den repräsentativen gemischten Importbatch.
  Dies ist ein Planungsziel; die Erreichbarkeit bei 10.000 Büchern ist zu prüfen.
- p95 von Buchliste und Suche während des Imports unter 500 ms, ohne Lesefehler.
- Kein höherer Backend-Spitzenspeicher für eine empfohlene Standardeinstellung.
  Schnellere Varianten mit höherem Bedarf separat ausweisen.
- Unveränderte fachliche Ergebnisse.

Lege Skripte unter `benchmarks/tools/` ab. Prüfe den Messaufbau zunächst an einem kleinen Bestand. Dokumentiere
Abhängigkeiten, genaue Befehle, Parameter, Abbruch und Bereinigung.

Speichere jede Messreihe unter
`benchmarks/results/<YYYY-MM-DDTHH-MM-SSZ>-<kurzer-commit>/`. Dort liegen
`result.md`, `summary.json` und die Rohdaten unter `runs/`. Pflege den Index
`benchmarks/results/HISTORY.md` nach jeder Messreihe. Die verbindlichen
Ablageregeln stehen in [README.md](README.md). Große Archive, Profile und lokale
Konfiguration liegen unter `benchmarks/.local/` oder einem dokumentierten
externen Datenpfad. Speichere keine Buchinhalte oder Zugangsdaten im Bericht.

Der Bericht enthält Messumgebung, Bestandszusammensetzung, Importlasten,
Wiederholungen, Streuung, Phasenprofile, Ergebnisprüfungen sowie priorisierte
Optimierungen mit Codebezug, Aufwand und nachgewiesener oder erwarteter Wirkung.
Empfiehl eine Parallelität für das gemessene Zielsystem und benenne Grenzen.

| Lastfall bei 10.000 Bestandsbüchern | Variante/Arbeiter | Läufe | Gesamtzeit Median/Streuung | Dateien/min | Zeitersparnis | Speicherpeak |
| --- | --- | --- | --- | --- | --- | --- |
| Neue Bücher, gemischter Batch | Referenz / … | … | … | … | — | … |
| Derselbe Batch | Änderung A / … | … | … | … | … | … |
| Identische Duplikate | … | … | … | … | … | … |
| Ähnliche Inhalte inkl. fertiger Vorschau | … | … | … | … | … | … |

Ersetze Platzhalter ausschließlich durch erhobene Werte. Ergänze Phasenzeiten,
Ergebnis-/Fehlerzahlen und API-Latenzen in eigenen Tabellen. Verlinke Skripte und
Rohdaten und liefere exakte Reproduktionsbefehle. Schließe mit Berichtspfad,
wichtigsten Importbefunden, empfohlenem nächsten Schritt und offenen Messungen ab.

## 8. Verbindliches Benchmark-Protokoll: `import-10k-v1`

Dieser Abschnitt legt den Referenzbenchmark fest. Seine konkreten Vorgaben haben
für diesen Benchmark Vorrang vor den allgemeineren Beispielen und Mindestzahlen
oben. Der Runner liegt unter `benchmarks/tools/`. Prüfe ihn gegen diese Vorgaben,
bereite einen geeigneten 10.000-Bücher-Bestand vor und erhebe anschließend die
Referenz. Ohne ausgeführte gültige Referenzreihe gibt es noch keinen historischen
Messwert nach diesem Protokoll.

### 8.1 Verbindliche Aufgabe und Hauptmesswert

Beantworte mit diesem Benchmark genau diese Frage:
**Wie lange dauert die vollständige lokale automatische Verarbeitung eines
festgelegten Importpakets bei einem Ausgangsbestand von exakt 10.000 Büchern?**

Der Hauptmesswert ist `import_total_seconds`. Er umfasst HTTP-Upload, Staging,
Warteschlange, lokale Buchanalyse, Duplikatprüfung, Archivierung und automatisch
angestoßene Vorschauanalyse. Manuelle Entscheidungen und externe Nachbearbeitung
gehören nicht zur gemessenen Verarbeitung. Erfasse separat, wie viele Dateien
archiviert, als identisch erkannt oder zur Prüfung bereitgestellt wurden.

Die Hauptmessung läuft ohne Browser und ohne zusätzliche Buchlisten-/Suchlast.
Statusbeobachtung und Ressourcenmessung sind in allen Varianten identisch.
Die Prüfung der API-Bedienbarkeit erfolgt in einer gesonderten Messreihe.

### 8.2 Einmalig ein unveränderliches Benchmark-Paket erstellen

Führe die folgenden Schritte vor jeglicher Optimierung in dieser Reihenfolge aus:

1. Lege `benchmarks/tools/` für Werkzeuge und ein vom Produktivarchiv getrenntes
   Verzeichnis für große Benchmark-Dateien an. Speichere dessen absoluten Pfad in
   einer lokalen Konfiguration. Prüfe programmgesteuert, dass Snapshot, Laufarchiv
   und Produktivarchiv verschiedene Verzeichnisse sind und sich nicht überlappen.
2. Erstelle einen konsistenten Snapshot mit **exakt 10.000 archivierten Büchern**.
   Verwende einen verfügbaren realen Bestand mit den dazugehörigen Dateien.
   Bei einem größeren Bestand wähle die Bücher deterministisch nach SHA-256,
   bei Gleichstand nach Buch-ID, und übernehme sämtliche zugehörigen Beziehungen
   und Analyseindizes in das Testarchiv. Prüfe danach referenzielle Integrität.
3. Fehlen 10.000 geeignete Bücher, benenne den fehlenden Bestand ausdrücklich.
   Ein synthetischer Ersatz erhält eine eigene Kennung, beispielsweise
   `import-10k-synthetic-v1`, und einen gespeicherten Generator mit festem Seed.
   Er darf nicht als Ergebnis für den realen Referenzbestand ausgegeben werden.
4. Bereite den Snapshot einmal mit der unveränderten Referenzversion vor:
   Initialisierung/Migrationen abschließen, Analyseindizes auf den regulären Stand
   bringen, alle Hintergrundaufträge beenden, leeres Staging herstellen, Backend
   stoppen und WAL konsistent checkpointen. Speichere anschließend den Snapshot.
   Wiederhole diese Vorbereitung nicht innerhalb der Zeitmessung.
5. Prüfe `COUNT(books) = 10000`, `PRAGMA integrity_check = ok`, keine Verletzung
   von `PRAGMA foreign_key_check`, vorhandene Buchdateien und vollständige
   Zuordnung aller erwarteten FTS-Einträge. Dokumentiere Anzahl und Version von
   Vergleichsdatensätzen, Fingerprints und Buckets sowie Gründe fehlender Analysen.
6. Verwende die 43 unterstützten Dateien aus `/home/steffen/Work/Testdaten` als
   initiales Importpaket. Vergleiche Anzahl, Formate und Byteumfang mit dem alten
   Bericht. Bei Abweichungen friere den tatsächlich geprüften Bestand unter einer
   neuen Paketkennung ein; verwende keine alten erwarteten Ergebniszahlen.
7. Speichere für Snapshot und Importpaket je ein Manifest. Jeder Dateieintrag
   enthält relativen Pfad, Dateigröße und SHA-256. Importdateien erhalten zusätzlich
   eine stabile `input_id` entsprechend ihrer Position. Gleiche Inhalte unter
   verschiedenen Namen bleiben getrennte Einträge. Prüfe auch die Datenbankdatei
   und alle Dateien des Snapshots, die während eines Imports verwendet werden.
8. Sortiere Importpfade nach ihren unveränderten relativen UTF-8-Pfadbytes.
   Teile diese Reihenfolge deterministisch in Batches auf: höchstens 20 Dateien
   und höchstens `200 * 1024 * 1024` Nutzdatenbytes pro Batch. Füge Dateien der
   Reihe nach hinzu; beginne bei Überschreiten eines Limits einen neuen Batch.
   Eine einzelne zu große Datei führt zum Preflight-Fehler. Speichere die fertige
   Batchliste, damit spätere Läufe sie nicht neu interpretieren.
9. Erzeuge `dataset.json` mit Snapshot-/Importmanifest-Hashes, Batchliste,
   Gesamtbytes, Dateizahl, Formatverteilung, Analyseabdeckung und Snapshot-Herkunft.
   Berechne `dataset_id` als SHA-256 der kanonischen JSON-Darstellung dieser
   Beschreibung ohne das Feld `dataset_id` selbst. Verwende UTF-8, sortierte
   Schlüssel und feste kompakte JSON-Trennzeichen; dokumentiere den Algorithmus.
10. Führe zwei ungemessene Pilotimporte aus frischen Snapshot-Kopien durch.
    Vergleiche ihre fachlichen Ergebnisse pro `input_id`. Prüfe insbesondere
    zeitabhängige Unterschiede bei Duplikaten innerhalb des Importpakets.
    Friere die geprüften Erwartungen in `expected-results.json` ein. Bei
    schwankenden Ergebnissen kläre zuerst die Ursache; eine instabile
    Ergebnisreferenz ist nicht bereit für Performancevergleiche.

Die Ergebnisreferenz enthält pro Eingabedatei Ergebnisart, Dateihash,
normalisierte Metadaten, Coverhash oder dokumentiertes Fehlen, Fingerprintstatus,
Text-/Signaturwerte soweit vorhanden und erwartete Duplikatbeziehungen. Vergleiche
Beziehungen über stabile Bestandsschlüssel beziehungsweise Eingabedateien.
Ignoriere ausschließlich explizit aufgeführte laufabhängige Felder wie neu
vergebene IDs, Zeitstempel und absolute Laufpfade. Sichere alle anderen Ergebnisse.

### 8.3 Konfiguration einfrieren

Erstelle `protocol.json` und verwende diese Werte für die Hauptreferenz:

| Parameter | Verbindlicher Wert |
| --- | --- |
| Ausgangsbestand | 10.000 Bücher aus dem gespeicherten Snapshot |
| Importpaket | Exakte Dateien und Batchliste aus `dataset.json` |
| Backend | Ein Uvicorn-Worker, kein Reload |
| `GOBLIN_IMPORT_CONCURRENCY` | 3 |
| `GOBLIN_PROVIDER_CONCURRENCY` | 2; externe Provider bleiben deaktiviert |
| Uploadlimits | 20 Dateien, 209.715.200 Nutzdatenbytes |
| `GOBLIN_MAX_QUEUED_IMPORT_FILES` | 100 |
| `GOBLIN_MAX_STAGING_BYTES` | 2.147.483.648 |
| Upload-Client | Ein Prozess, eine persistente HTTP-Verbindung, sequenzielle POSTs |
| Chunkgröße beim Lesen der Uploadquellen | 1.048.576 Byte; Multipart-Implementierung und Version festhalten |
| Netzwerk | Loopback, keine künstliche Bandbreitenbegrenzung |
| Wiederholungen | 10 gültige gemessene Läufe pro Variante |
| Aufwärmen | Ein vollständiger ungewerteter Import pro Variante vor der Messreihe |
| Ressourcenmessung | Alle 100 ms, Backend samt Kindprozessen und Client getrennt |
| Bereitschafts-Timeout | 120 Sekunden |
| HTTP-Upload-Timeout | 300 Sekunden je Anfrage |
| Gesamtlauf-Timeout | 1.800 Sekunden ab Messbeginn |
| Ruhezeit nach Backend-Bereitschaft | 5 Sekunden |
| Nachbeobachtung des Speichers | 10 Sekunden nach gemessenem Abschluss |

Speichere zusätzlich Python- und Paketversionen, SQLite-Version/-Pragmas,
Umgebungsvariablen mit Einfluss auf das Ergebnis, CPU-/RAM-Daten, Betriebssystem,
Speichermedium, Mountoptionen und CPU-Energieprofil in `environment.json`.
Erfasse die Referenzrevision einschließlich Hash eines eventuell vorhandenen
Patches. Fixiere die tatsächlich verwendeten Abhängigkeiten in einer
reproduzierbaren Versionsliste. Ändere sie nicht zwischen A und B, außer genau
diese Abhängigkeitsänderung ist die zu prüfende Optimierung.

Erstelle einen Benchmark-Startadapter, der die reguläre App mit Anmeldung startet
und externe Metadaten-/Coveranbieter sowie `postprocess_step` zuverlässig
abschaltet. Prüfe auch die Vorschaupfade. Behalte eingebettete Cover und alle
lokalen Analysen bei. Protokolliere die aktiven Dienste beim Start. Ein unerwarteter
externer Aufruf lässt den Lauf fehlschlagen; darf nicht still als Fallback enden.

### 8.4 Jeden Lauf exakt gleich vorbereiten

1. Stelle sicher, dass kein Backend eines vorherigen Laufs mehr aktiv ist.
2. Erzeuge eine frische Laufkopie des eingefrorenen Snapshots. Verwende immer
   dieselbe Kopiermethode, keine Hardlinks auf veränderliche Dateien. Prüfe vor
   dem Start sämtliche Manifest-Hashes und die 10.000 Ausgangsbücher.
3. Führe eine definierte Cachevorbereitung aus: Lies alle im Snapshot-Manifest
   und Importmanifest aufgeführten Dateien einmal vollständig, sequenziell nach
   Manifestreihenfolge, in 1-MiB-Blöcken. Protokolliere Start/Ende und gelesene Bytes.
   Diese Vorbereitung und die Hashprüfung liegen außerhalb der Zeitmessung.
4. Bezeichne diesen Zustand als „nach definierter Dateivorbereitung“. Behaupte
   keinen vollständig warmen Cache, wenn der Bestand nicht in den RAM passt.
   Setze den globalen Betriebssystemcache nicht zwischen einzelnen Läufen zurück.
5. Starte das Backend mit dem Laufarchiv. Warte auf Bereitschaft, melde den
   Testclient an und prüfe Uploadlimits sowie ausgeschaltete externe Dienste.
   Prüfe, dass keine Import-, Vorschau-, Scan-, Backup- oder Übersetzungsarbeit läuft.
6. Starte Ressourcenmonitor und Abschlussbeobachtung. Warte fünf Sekunden.
   Beginne danach den Upload; verwende für jeden Lauf dieselbe Reihenfolge.

Halte Hardware, CPU-Profil, Datenträger und sonstige Last konstant. Protokolliere
Speicherdruck, Swap und fremde CPU-/I/O-Last. Kaltstart- oder NAS-Versuche bekommen
eine eigene Messreihenkennung; ihre Zeiten werden nicht mit dieser Reihe vermischt.

### 8.5 Beginn, Ende und Uploadablauf implementieren

Implementiere die Zeitmessung im Upload-Client mit `time.perf_counter_ns()`.
Verwende für Zeitdifferenzen ausschließlich Zeitstempel desselben Clientprozesses.

1. Öffne die Uploadquellen zum Streamen; lies nicht den gesamten Multipart-Body
   vorab in den RAM. Setze `t0` unmittelbar vor dem Aufruf, der den ersten
   `POST /api/import` sendet. Multipart-Erzeugung gehört ab diesem Punkt zur Zeit.
2. Sende die gespeicherten Batches nacheinander. Sende den nächsten unmittelbar
   nach der vollständig gelesenen `202`-Antwort des vorherigen. Warte dabei nicht
   auf Importabschluss. Speichere Requestbeginn/-ende, Bytes, Status und Job-ID.
   Verwende keine automatischen Upload-Retries.
3. Ordne die zurückgegebenen Item-IDs über Batchpositionen den `input_id` zu.
   Doppelte Dateinamen dürfen diese Zuordnung nicht mehrdeutig machen.
4. Speichere `t_upload_end`, sobald die letzte `202`-Antwort vollständig gelesen ist.
5. Warte auf die vollständig abgeschlossene automatische Verarbeitung sämtlicher
   Eingabedateien und auf alle dazu gestarteten Vorschauaufträge. Setze `t_end`
   bei Empfang des letzten erforderlichen Abschlussnachweises, frühestens nach
   `t_upload_end`. Backend-Hintergrundarbeit darf nicht übrig bleiben.
6. Berechne `import_total_seconds = (t_end - t0) / 1e9`,
   `upload_seconds = (t_upload_end - t0) / 1e9` und
   `tail_seconds = (t_end - t_upload_end) / 1e9`.

**Abschlussnachweis konkret implementieren:** Ergänze im Benchmark-Adapter einen
passiven Beobachter. Er erfasst zunächst die Registrierung aller Items und
abgeleiteten Vorschau-Tasks, damit offene Folgeaufträge sicher bekannt sind.
Er schreibt nach Abschluss jedes Importitems und jedes
zugehörigen Vorschau-Tasks einen kleinen JSONL-Datensatz in eine laufbezogene
Datei: `run_id`, Job-/Item-/Preview-ID, Endzustand und gegebenenfalls Fehler.
Schreibe Abschlüsse erst nach Rückkehr der jeweiligen Verarbeitung einschließlich
Datenbankcommit und `finally`-Bereinigung. Flush jeden Abschlussdatensatz.
Erfasse auch Task-Ausnahmen.
Der Client liest nur neu angehängte Daten alle 10 ms und hält die IDs offener
Aufträge fest. Erzeuge keine Hashes, Textanalysen oder Datenbankabfragen im Beobachter.

Die erlaubten erfolgreichen Item-Endzustände sind im aktuellen Code `finished`,
`duplicate` und `needs_review`. Letzterer zählt erst mit zugehöriger Vorschau im
Zustand `ready` und abgeschlossenem Analyse-Task. Prüfe diese Zustände bei der
Implementierung am aktuellen Code und dokumentiere nötige Anpassungen.
`failed`, unerwartetes `discarded`, Task-Ausnahmen oder fehlende Nachweise machen
den Lauf fachlich ungültig. Verwende nicht allein `job.completed`: Dort ist
`needs_review` derzeit nicht als abgeschlossen gezählt.

Rufe während der Hauptmessung **keine Vorschau-Detail-/Listenendpunkte** auf.
`PreviewManager._with_matches()` kann über `_matches()` erneut Textvergleiche
anstoßen. Die Abschlussbeobachtung muss diesen zusätzlichen Aufwand vermeiden.

Die Hauptzeit enthält die Zustell-/Beobachtungsverzögerung. Protokolliere die
tatsächlichen Leseintervalle, statt einen garantierten 10-ms-Fehler zu behaupten.
Prüfe vor Freigabe des Benchmarks mit drei alternierenden Pilotpaaren den
Zusatzaufwand von Beobachter und Ressourcenmonitor gegenüber grober
Abschlusskontrolle. Ein Hinweis
auf mehr als 1 % Median-Mehrzeit erfordert Überarbeitung und erneute Prüfung.
Speichere Methode, Unsicherheit und Ergebnisse dieser Messaufbauprüfung.

### 8.6 Was der Monitor und die Phasenmessung speichern müssen

Speichere in `resources.jsonl` alle 100 ms monotone Zeit, PID samt Prozessstartzeit,
RSS, CPU-Zeit und gelesene/geschriebene Bytes aus den Betriebssystemzählern.
Erfasse neue Kindprozesse und ihren Austritt. Speichere jede Sekunde zusätzlich
PSS, soweit verfügbar. Dokumentiere Zählersemantik und fehlende Samples; physische
I/O-Bytes sind nicht dasselbe wie vom Programm gelesene Bytes aus dem Cache.

Berechne den Speicherpeak aus der **zeitgleichen** Summe der Backendprozesse,
niemals aus der Summe ihrer individuellen Maxima. Gib Samplingauflösung und
möglicherweise verpasste kurzlebige Prozesse an. CPU-Zeit kurzlebiger Kinder
muss über deren Abschlusswerte oder geeignete Prozessgruppen-Zähler erfasst
werden; andernfalls kennzeichne die Gesamtsumme als unvollständig.

Die offizielle Gesamtzeit wird ohne Profiler und ohne detaillierte SQL-Logs
erhoben. Führe danach drei separate Diagnoseläufe mit derselben Konfiguration
durch. Speichere in `phases.jsonl` pro Phase `input_id`, `span_id`, `parent_span_id`,
Prozess-/Thread-ID, Anfang, Ende und Erfolg/Fehler. Instrumentiere mindestens:

- Staging je Datei und Zeit zwischen Einreihen und Beginn der Verarbeitung.
- SHA-256, lokale Metadaten/Cover, Textgewinnung/Normalisierung,
  Shingle-Bildung und Signaturberechnung.
- Kandidatensuche und jeden Detailvergleich einschließlich Kandidatenschlüssel.
- Dateikopie/Archivierung, Datenbank/FTS sowie Transaktionsabschluss.
- Erneute Vorschauanalyse einschließlich wiederholter Schritte.

Speichere pro Importdatei zusätzlich Queryzahl, Kandidatenzahl, Zahl der
Textvergleiche und Extraktionen. Verwende Eltern-/Kindspannen zur Erkennung
überlappender Zeiten; addiere keine inklusiven Phasen doppelt. Verwende keine
prozessweite CPU-Zeit als vermeintliche CPU-Zeit einer einzelnen parallelen Phase.

### 8.7 Nach jedem Lauf fachlich validieren

Beende die Hauptzeitmessung vor dieser Prüfung. Prüfe dann jedes Ergebnis gegen
`expected-results.json`, einschließlich Duplikatbeziehungen und Analyseabdeckung.
Prüfe erwarteten Buchzuwachs, Dateihashes, Cover, Metadaten, FTS-Zuordnung,
`integrity_check` und `foreign_key_check`. Kontrolliere, dass im Staging nur
Dateien erwarteter offener Vorschauen liegen und keine Arbeit mehr läuft.

Speichere `validation.json` mit einzelnen Prüfresultaten und dem Gesamtstatus.
Ein schneller Lauf mit abweichenden Ergebnissen ist kein Performancegewinn.
Bewahre auch Rohdaten fehlerhafter oder abgebrochener Läufe auf. Entferne keine
langsamen Ausreißer allein aufgrund ihrer Dauer. Dokumentierte Infrastrukturfehler
dürfen einen Lauf ungültig machen; der Grund muss unabhängig vom Zeitwert sein.
Wiederhole bei einem ungültigen Vergleichslauf das vollständige zugehörige Paar.

### 8.8 Referenz einfrieren und spätere Änderungen vergleichen

Erhebe zunächst zehn gültige Referenzläufe nach einem ungewerteten Aufwärmlauf.
Speichere alle Einzelzeiten, Median, Minimum, Maximum, arithmetischen Mittelwert,
Stichproben-Standardabweichung und Variationskoeffizient `s / Mittelwert`.
Bei mehr als 5 % Variationskoeffizient markiere die Umgebung als instabil,
untersuche die Ursache und erhebe nach Behebung eine vollständig neue Messreihe.
Behalt die ursprüngliche Reihe samt Begründung; sammle nicht selektiv bis zum
gewünschten Ergebnis weiter.

Für jede spätere Optimierung vergleiche die unveränderte Referenz A und die
Variante B erneut auf demselben Rechner. Verwende zehn Paare, Reihenfolge fest:
`AB, BA, AB, BA, AB, BA, AB, BA, AB, BA`. Vorher je ein ungewerteter Aufwärmlauf
von A und B; für jeden einzelnen Lauf erneut Snapshot wiederherstellen und den
Ablauf aus 8.4 ausführen. Die alten Referenzzeiten bleiben dokumentiert, die
aktuellen A-Läufe kontrollieren Veränderungen der Messumgebung.

Berechne für jedes Paar `d_i = 1 - T_B_i / T_A_i`. Berichte den Median dieser
relativen Zeitersparnisse sowie die Mediane der absoluten Zeiten von A und B.
Berechne ein 95-%-Bootstrap-Intervall des Medians von `d_i`: 10.000 Stichproben
mit Zurücklegen aus den zehn vollständigen Paaren, NumPy-Generator
`Generator(PCG64(10000))`, Grenzen bei 2,5 und 97,5 Prozent. Fixiere die
NumPy-Version und Quantilmethode; verwende
für Rohzeit- und Bootstrap-Quantile lineare Interpolation.

Eine Variante gilt in diesem Protokoll als **belastbar schneller**, wenn:

1. Alle zehn Paare fachlich gültig sind und beide Varianten den Stabilitätscheck
   mit höchstens 5 % Variationskoeffizient erfüllen.
2. Die mediane gepaarte Zeitersparnis mindestens 5 % beträgt und die untere
   Grenze des 95-%-Intervalls über 0 liegt.
3. Keine zusätzlichen Importfehler, fehlenden Analysen oder geänderten
   Duplikatentscheidungen vorliegen.

Andernfalls lautet das Ergebnis „Verbesserung nicht belastbar nachgewiesen“
oder „Regression“, mit Zahlen und Unsicherheit. Das Intervall beschreibt nur
Wiederholungen dieser Hardware und dieses Datenpakets. Werden viele Varianten
explorativ probiert, bestätige die ausgewählte finale Variante mit einer neuen,
unabhängigen Zehn-Paar-Reihe; verwende die Auswahlmessungen nicht als Bestätigung.

Eine Empfehlung als neue Standardeinstellung verlangt zusätzlich den
Speichervergleich und die Bedienbarkeitsprüfung. Berichte je Variante alle
Speicherpeaks, deren Median und Maximum. Akzeptiere Mehrverbrauch nicht still
als Messrauschen; kläre Abweichungen und weise ressourcenintensivere Einstellungen
separat aus. Die 20–40 % aus Feature 13 bleiben ein Planungsziel.

### 8.9 Bedienbarkeit separat und mit fester Last prüfen

Führe für A und B jeweils drei weitere Importläufe durch. Ein zweiter Client
sendet ab `t0` jede Sekunde `GET /api/books?limit=50&offset=0` und um 500 ms
versetzt `GET /api/search?q=ABAP`. Verwende dieselbe authentifizierte Sitzung
beziehungsweise dieselbe Anmeldemethode in beiden Varianten. Setze zehn Sekunden
Request-Timeout und höchstens 20 ausstehende Anfragen. Bei voller Kapazität
protokolliere die geplante Anfrage als nicht gesendet wegen Überlast; verschiebe
sie nicht still. Prüfe den Suchbegriff beim Einfrieren des Pakets und halte ihn fest.

Stoppe neue Leseanfragen mit dem Importabschluss. Speichere geplante Startzeit,
tatsächlichen Start, vollständiges Antwortende, Status und Fehler. Berichte
p50/p95 der Dauer ab tatsächlichem Start und zusätzlich ab geplantem Start,
Stichprobenzahl, Timeouts und ausgelassene Requests je Endpunkt. Bei weniger als
200 Beobachtungen je Endpunkt kennzeichne p95 als schwach belegt. Vermische diese
Importzeiten nicht mit der Hauptreferenz ohne Leselast.

### 8.10 Verbindliche Artefakte und ausführbare Befehle liefern

Verwende die CLI unter `benchmarks/tools/benchmark_import.py` mit den
Unterbefehlen `prepare`, `verify`, `baseline`, `compare` und `report`. Prüfe
`--help` und die [getesteten Befehle](tools/README.md). Die folgenden Befehle
zeigen die Schnittstelle; ersetze die Pfade und Revisionen durch reale Werte:

```bash
.venv/bin/python benchmarks/tools/benchmark_import.py prepare --config benchmarks/.local/benchmark-config.json
.venv/bin/python benchmarks/tools/benchmark_import.py verify --bundle benchmarks/.local/import-10k-v1
.venv/bin/python benchmarks/tools/benchmark_import.py baseline --bundle benchmarks/.local/import-10k-v1 --revision REF_COMMIT --output benchmarks/results/REFERENZ_KENNUNG
.venv/bin/python benchmarks/tools/benchmark_import.py compare --bundle benchmarks/.local/import-10k-v1 --baseline-revision REF_COMMIT --candidate-revision TEST_COMMIT --output benchmarks/results/VERGLEICH_KENNUNG
.venv/bin/python benchmarks/tools/benchmark_import.py report --results benchmarks/results/VERGLEICH_KENNUNG
```

`prepare` erstellt das Paket und die Pilotreferenz; `verify` prüft Hashes,
Schema, Vollständigkeit und Konfiguration; `baseline` und `compare` führen das
hier festgelegte Protokoll aus. `report` berechnet alles ausschließlich aus den
gespeicherten Rohdaten. Verwende isolierte Checkouts für A/B, ohne den Arbeitsbaum
des Nutzers umzuschalten. Friere auch den Benchmark-Runner selbst auf eine Version
ein; benutze denselben Runner und Beobachter für beide Produktrevisionen.

Speichere mindestens:

- Paket: `dataset.json`, Snapshot-/Importmanifest, `expected-results.json`,
  `protocol.json`, `bundle-seal.json`, Versionsliste sowie Hashes dieser Dateien.
- Messreihe: Runner-Version, Revision/Patch von A/B, `environment.json`,
  vollständige geplante und tatsächlich ausgeführte Laufreihenfolge sowie
  `calibration.json` mit der Prüfung des Messaufwands.
- Je Lauf: `run.json` mit Zeiten/Status, `uploads.jsonl`, `completions.jsonl`,
  `resources.jsonl`, `validation.json` und Backend-Log; bei Diagnose zusätzlich
  `phases.jsonl`, bei Leselast `requests.jsonl`.
- Auswertung: `summary.json`, Tabelle aller Einzelwerte und deutscher Bericht
  mit Datensatzkennung, Bedingungen, Konfidenzintervall und Freigabeentscheidung.

Ein Lauf darf nur starten, wenn alle Pflichtfelder ausgefüllt sind und Paket,
Runner und Konfiguration die erwarteten Hashes besitzen. Änderungen an Dateien,
Batchreihenfolge, Abschlussdefinition, Cachevorbereitung oder Beobachtung erzeugen
eine neue Protokoll-/Datensatzkennung und erfordern eine neue Referenzmessung.

**Fertig ist der Benchmark erst**, wenn ein zweiter vollständiger Aufruf aus
frischen Laufverzeichnissen dieselben fachlichen Ergebnisse liefert, die Rohdaten
ohne Backend erneut ausgewertet werden können und der Referenzbericht sämtliche
zehn gültigen Laufwerte samt Streuung enthält. Ein Skript ohne ausgeführte und
validierte Referenzreihe ist nur ein vorbereiteter Messaufbau.
