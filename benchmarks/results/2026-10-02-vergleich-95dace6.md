# Vergleich: Importoptimierung `95dace6` gegen `78f780b`

Stand: 2. Oktober 2026. Datenpaket: 43 E-Books aus `/home/steffen/Work/Testdaten/Unsortiert`
(18 PDF, 16 EPUB, 7 FB2, 2 MOBI; 705.636.721 Bytes), leeres Ausgangsarchiv,
ein Importarbeiter, externe Dienste deaktiviert.

## Ergebnis

| Kennzahl | `78f780b` (alt) | `95dace6` (neu) | Änderung |
| --- | ---: | ---: | ---: |
| Importzeit Median | 108,41 s | **53,43 s** | **−50,7 %** |
| Einzelläufe | 108,41 / 111,45 / 105,24 | 50,33 / 56,17 / 53,43 | |
| Variationskoeffizient | 2,86 % | 5,48 % | |
| Durchsatz | 23,8 Dateien/min | **48,3 Dateien/min** | +103 % |
| Backend-Speicherpeak (max) | 573,2 MiB | 559,8 MiB | −2,3 % |
| Fachliches Ergebnis | 41 archiviert, 1 Duplikat, 1 Prüffall | identisch | unverändert |

Berichte: [alt](2026-10-02T13-32-53Z-78f780b-recheck/result.md) ·
[neu](2026-10-02T13-41-53Z-95dace6/result.md)

## Analyseabdeckung verbessert sich gleichzeitig

Die Beschleunigung geht **nicht** auf weniger Analyse zurück — im Gegenteil:

| Fingerprintstatus | alt | neu |
| --- | ---: | ---: |
| `complete` | 21 | **27** |
| `partial` | 8 | 13 |
| `unavailable` (keine Textanalyse) | **13** | **2** |

Ursache: 11 der 18 PDFs tragen ein leeres Owner-Passwort. Der alte Pfad lehnte
sie über `reader.is_encrypted` ab und erzeugte keinen Fingerprint; PDFium liest
sie. Übrig bleiben die 2 MOBI-Dateien, für die keine Textextraktion existiert.
Die neue Revision leistet also **mehr** fachliche Arbeit in **halber** Zeit.

## Umgesetzte Änderungen

1. `signature_for` mit NumPy vektorisiert (`backend/duplicates.py`). Ergebnis
   bitgenau identisch zur vorherigen Schleife, geprüft gegen eine
   Referenzimplementierung inklusive Chunkgrenzen und uint64-Extremwerten.
   Blockweise Verarbeitung hält den Speicherbedarf unabhängig von der Shinglezahl.
2. PDF-Textextraktion über PDFium statt pypdf (`backend/duplicates.py`).
   `TEXT_VERSION` auf 3 erhöht, damit vorhandene Fingerprints neu berechnet werden.
3. `get_or_create_author/genre/tag` nebenläufigkeitssicher über
   `ON CONFLICT DO NOTHING` plus erneutes Lesen (`backend/repository.py`).
   Behebt den Abbruch `UNIQUE constraint failed: authors.name`, bei dem zuvor
   die Importdatei verloren ging.

## Einschränkungen

- **Kein protokollkonformer A/B-Nachweis.** Das Protokoll `import-10k-v1` verlangt
  zehn alternierende Paare mit Bootstrap-Intervall. Hier liegen zwei getrennte
  Reihen mit je drei Läufen vor, erhoben neun Minuten auseinander auf derselben
  Maschine. Die 50,7 % sind eine Differenz der Mediane, **kein** Konfidenzintervall.
- **Verschiedene Vergleichsgruppen.** Beide Reihen verwenden unterschiedliche
  Pakete, da die Ergebnisreferenz der neuen Revision wegen geänderter Texthashes
  neu eingefroren werden musste. Ein gepaarter Lauf im selben Paket ist deshalb
  nicht möglich.
- **Messumgebung unruhig.** Die neue Reihe erreicht 5,48 % Variationskoeffizient
  und ist damit nach Protokoll `invalid` (Grenze 5 %). Ursache ist Fremdlast:
  Der Desktop-Indexer `localsearch-3.service` lief während früherer Versuche mit
  rund 80 % CPU und wurde für die Messung gestoppt; der Agentenprozess selbst
  belegt dauerhaft etwa 60 % einer CPU. Frühere Reihen derselben Revision ergaben
  79,05 s, 65,63 s und 53,52 s — die absoluten Zahlen hängen spürbar vom
  Maschinenzustand ab. Deshalb wurde die alte Revision unmittelbar vor der neuen
  erneut gemessen; **nur dieser zeitnahe Vergleich ist aussagekräftig**, nicht der
  Rückgriff auf die ursprünglichen 165,43 s vom Vormittag.
- Gilt für ein leeres Archiv. Bei 10.000 Bestandsbüchern verschiebt sich das
  Gewicht voraussichtlich zur Duplikatsuche; die dort vermuteten N+1-Abfragen
  und `lazy="selectin"`-Ladevorgänge sind unverändert.

## Nächster Schritt

Für eine belastbare Zahl: ruhige Maschine ohne Agentenlast, dann zehn Läufe je
Revision. Für den eigentlichen Auftrag fehlt weiterhin ein Ausgangsbestand mit
10.000 Büchern.
