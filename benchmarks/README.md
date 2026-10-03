# Benchmarks

Dies ist die zentrale Ablage für Benchmark-Anleitung, Messergebnisse und
Vergleichshistorie. Der Auftrag **„Mache einen Benchmark“** bedeutet standardmäßig:
den vollständigen unterstützten Inhalt des lokalen Testdatenordners in ein leeres,
isoliertes Archiv importieren, Ergebnis hier speichern und kurz zusammenfassen.

Die aktuelle Messvorschrift steht in [import-folder.md](import-folder.md).
Der 10.000-Bücher-Benchmark in [import-10k.md](import-10k.md) ist ein späteres,
getrenntes Ausbauziel. Der Einstieg für künftige KI-Sitzungen ist in der obersten
`AGENTS.md` des Repositorys verankert.

## Ablage

```text
benchmarks/
  README.md                 Ablauf für den Auftrag „Mache einen Benchmark“
  import-folder.md           Standardmessung mit dem vollständigen Testdatenordner
  import-10k.md              Vollständige Messvorschrift, Protokoll import-10k-v1
  tools/                    Wiederverwendbare Messwerkzeuge
  templates/                Vorlagen für Ergebnisbericht und Zusammenfassung
  results/
    HISTORY.md              Index aller Messreihen mit Referenzverweisen
    <UTC-Zeit>-<Commit>/
      result.md             Lesbarer Ergebnisbericht der Messreihe
      summary.json          Maschinenlesbare Kennzahlen und Vergleichsidentität
      environment.json      Hardware, Software und Messumgebung
      protocol.json         Tatsächlich verwendete Messkonfiguration
      dataset.json          Identität des eingefrorenen Testbestands
      runs/                 Einzelmessungen einschließlich ungültiger Läufe
      diagnostics/          Optionale Diagnosedaten
  .local/                   Große Testdaten und Arbeitsdateien; von Git ignoriert
```

Beispiel für eine Messreihenkennung: `2026-10-02T14-30-00Z-a1b2c3d`.
Verwende die tatsächliche UTC-Startzeit und den kurzen Commit-Hash. Bei Kollision
hänge `-02`, `-03` usw. an; überschreibe keinen vorhandenen Ordner. Die Kennung
bezeichnet eine vollständige Messreihe mit mehreren Läufen, nicht einen Einzellauf.
Bei lokalen Produktänderungen ergänze `-dirty` und speichere einen Patch sowie
dessen Hash; ein Commit allein beschreibt dann nicht den getesteten Code.

Kompakte Ergebnisse und Rohdaten werden normal von Git erfasst. Füge keine
Archive, E-Book-Inhalte, Auth-Dateien oder Zugangsdaten hinzu. Bewahre große
Artefakte in `.local/` oder einem dokumentierten externen Verzeichnis auf und
speichere deren Hashes und Pfade. Erstelle ohne entsprechenden Auftrag keinen
Commit und veröffentliche nichts.

## Arbeitsanweisung für „Mache einen Benchmark“

1. Lies die Messvorschrift und [HISTORY.md](results/HISTORY.md). Prüfe vorhandene
   Werkzeuge, Datenpakete und frühere Messungen. Verwende den festgelegten
   Benchmark; frage nicht erneut nach schon dokumentierten Einstellungen.
2. Wähle eine neue Ergebniskennung und übergib den noch nicht vorhandenen
   Zielpfad an `baseline --output` oder `compare --output`. Der Runner erstellt
   den Ordner, Rohdaten, [result.md](templates/result.md) und
   [summary.json](templates/summary.json). Falls die Messung schon vor dem
   Runnerstart blockiert ist, lege den Ordner selbst mit den Vorlagen an und
   dokumentiere `status: blocked`; noch nicht gemessene Zahlen bleiben `null`.
3. Prüfe Paket, Hardware, Runner und Konfiguration. Verwende das unveränderte
   eingefrorene Paket und die Referenzrevision. Wähle die älteste gültige
   Referenz derselben Vergleichsgruppe als feste Referenz und die jüngste
   kompatible erfolgreiche Messreihe als zusätzlichen historischen Vergleich.
4. Prüfe den [vorhandenen Runner](tools/README.md) mit `verify`. Ein Datenpaket
   und eine Referenzmessung müssen separat erstellt werden. Ersetze die Messung
   nicht durch eine Schätzung. Fehlen zwingende Voraussetzungen, dokumentiere sie.
5. Ohne kompatible Referenz: Führe die Referenzläufe gemäß Protokoll aus.
   Mit kompatibler Referenz und geändertem Produktcode: Führe die A/B-Paare
   gegen die eingefrorene Referenzrevision aus. Bei identischem Produktstand
   wiederhole die Referenzläufe; bewerte Reproduzierbarkeit statt Codebeschleunigung.
   Verwende für beide Revisionen denselben eingefrorenen Runner.
6. Speichere Rohdaten während der Ausführung, validiere jedes Ergebnis und
   erstelle danach `result.md` und `summary.json`. Berichte auch ungültige und
   abgebrochene Läufe. Statistische Aussagen folgen ausschließlich dem Protokoll.
7. Ergänze [HISTORY.md](results/HISTORY.md) um einen verlinkten Eintrag. Kennzeichne
   neue Referenzen ausdrücklich; ersetze die alte Referenz nicht automatisch
   durch eine schnellere Variante. Geänderte Vergleichsbedingungen eröffnen eine
   neue Gruppe mit neuer Referenz.
8. Gib dem Nutzer eine kurze Zusammenfassung: Importzeit, gültige Laufzahl,
   Vergleich zur Referenz und letzter kompatibler Messung, Speicherpeak,
   fachlicher Prüfstatus und Link zu `result.md`. Bei fehlender Vergleichbarkeit
   oder fehlgeschlagener Messung nenne dies ausdrücklich.

## Vergleichbarkeit und Historie

Zwei Messreihen gehören nur dann in dieselbe Vergleichsgruppe, wenn Protokoll,
Datensatz, Runner einschließlich Instrumentierung, Cachevorbereitung, Lastprofil,
Hardware und Softwareumgebung übereinstimmen. Verwende für die Fingerprints
kanonische JSON-Darstellungen mit SHA-256. Schließe Zeitstempel, Laufpfade und
die zu vergleichende Produktrevision aus der Gruppenidentität aus; deren Werte
werden separat gespeichert. Geänderte Abhängigkeiten oder Importparallelität
sind nur in ausdrücklich dokumentierten A/B-Experimenten zulässige Unterschiede.
Jedes Experiment nennt alle Unterschiede und erhält eine eigene Kennung.

Eine Prozentdifferenz zu einem alten historischen Median ist beschreibend.
Eine belastbare Behauptung zur Wirkung einer Codeänderung benötigt die neuen,
abwechselnden A/B-Läufe samt Konfidenzintervall. Zeige beide Vergleiche getrennt.
Wenn eine alte Revision nicht mehr ausführbar ist, protokolliere den Grund und
behaupte keine durch A/B bestätigte Verbesserung.

Abgeschlossene Ergebnisordner bleiben unverändert. Speichere Korrekturen in
einem neuen Ergebnisordner mit Verweis auf den ersetzten Bericht und ergänze
einen Korrekturverweis in der Historie. Ein noch laufender Ergebnisordner darf
bis zum Abschluss vervollständigt werden.

Erlaubte Abschlusszustände in `summary.json`:

- `completed`: Protokoll vollständig durchgeführt und fachlich gültig; dies
  bedeutet noch nicht, dass eine Verbesserung statistisch nachgewiesen ist.
- `invalid`: Ausgeführt, aber fachliche Prüfung oder Protokollanforderungen verletzt.
- `blocked`: Voraussetzungen fehlen, daher keine vollständige Messung möglich.
- `aborted`: Begonnene Messung wurde abgebrochen oder lief in ein Timeout.

Nutze `null` für nicht gemessene Werte, niemals erfundene Zahlen oder Nullzeiten.
Bewahre auch Berichte mit `blocked`, `invalid` oder `aborted` in der Historie auf.

Die JSON-Vorlage verwendet bei Referenz/Wiederholung `metrics_a` für die aktuelle
Messreihe; `metrics_b` bleibt dann `null`. Bei A/B enthält `metrics_a` die erneut
gemessene Referenz und `metrics_b` dieselben Kennzahlen für die Variante. Schreibe
beide Produktrevisionen beziehungsweise bei einer Einzelrevision nur
`revision_a` hinein. Der Variationskoeffizient ist ein Verhältnis (0,05 = 5 %),
Zeitersparnisse stehen in Prozentpunkten (5 = 5 %), Speicherwerte in Bytes.
`reason` benennt bei einem anderen Abschlussstatus als `completed` die Ursache.
Nur vorhandene Artefakte dürfen im fertigen Bericht verlinkt sein; ein blockierter
Lauf kann beispielsweise noch keine `environment.json` oder `runs/` besitzen.

## Bisherige Dokumente

Der [Importtest vom 30. September 2026](../docs/performance-test-testdaten-2026-09-30.md)
und das [Performanceaudit](../docs/performance-audit.md) bleiben als historische
Quellen erhalten. Sie erfüllen das neue 10k-Protokoll nicht und sind keine
freigegebene Referenz für dessen Vergleiche.
