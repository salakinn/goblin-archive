# KI-Buchübersetzung

Status: Konzept, noch nicht implementiert. Festgehalten am 27.09.2026.
Die Umsetzung ist für den nächsten Entwicklungsschritt vorgesehen.

## Ziel und erste Ausbaustufe

Goblin übersetzt ein vollständiges EPUB mit OpenAI und archiviert das Ergebnis
als eigenständige, mit dem Original verknüpfte Ausgabe. Nutzer wählen Zielsprache
und Qualitätsprofil, sehen eine Kostenschätzung, prüfen das erste Kapitel und
können das Glossar bearbeiten, bevor das restliche Buch übersetzt wird.

Die erste Ausbaustufe umfasst EPUB → EPUB. PDF, OCR, MOBI-/AZW3-Konvertierung und
die Übersetzung von Text innerhalb von Bildern benötigen eigene spätere Lösungen.
Das Original wird nicht verändert. Bilder, CSS, Kapitelstruktur, Fußnoten und
interne Verweise bleiben erhalten. Sprachabhängige Texte und Ausgabenmetadaten
werden gezielt angepasst.

## Nutzerablauf

1. In der Buchansicht „Buch übersetzen“ wählen.
2. Zielsprache, Qualitätsprofil und Budgetgrenze festlegen; optional ein vorhandenes
   Glossar übernehmen. Goblin zeigt Umfang, Schätzkosten und erkannte Einschränkungen.
3. Vorbereitung und erstes Kapitel starten. Goblin erzeugt einen ersten Buchkontext
   und Glossarvorschläge und übersetzt das Kapitel als Vorschau.
4. Original und Vorschau vergleichen; Glossar und Stilvorgaben bearbeiten.
5. Restübersetzung starten. Fortschritt, Verbrauch und Probleme bleiben sichtbar.
6. Auftrag bei Bedarf pausieren, fortsetzen oder abbrechen. Ein Backend-Neustart
   verliert weder den Auftrag noch bereits gespeicherte Ergebnisse.
7. Nach abgeschlossener Prüfung erscheint die Übersetzung als verknüpfte Ausgabe
   in der Bibliothek und kann heruntergeladen werden.

Die Vorschau ist ein bewusstes Zwischenstadium. Vor dem Weiterübersetzen wartet
der Auftrag auf die Glossarbearbeitung beziehungsweise den Start durch den Nutzer.

## Verarbeitungspipeline

### 1. EPUB analysieren

OPF, Manifest, Spine, Kapitel-XHTML, EPUB3-Navigation, EPUB2-NCX, Bilder, CSS,
Fußnoten und Metadaten erfassen. Eine vollständige Liste aller zu übersetzenden
Texte und ihrer Einfügepositionen erstellen. Neben Fließtext gehören Überschriften,
Navigationseinträge und relevante `alt`-/`title`-Attribute dazu. Ausnahmen wie Code
oder absichtlich unveränderte Inhalte müssen ausdrücklich erfasst werden.

ZIP-Pfade, XML und Ressourcen mit Größenlimits prüfen. Buchtexte und KI-Ergebnisse
werden als nicht vertrauenswürdige Daten behandelt; die KI erhält keine Werkzeuge
oder Berechtigung, Dateien und Links zu erzeugen.

### 2. Text segmentieren und Formatierung schützen

Nach Kapitel → Absatz → Satzgruppen segmentieren, ohne Sätze willkürlich zu
zerteilen. Stabile IDs wie `chapter_04:p_037` mit Quell-Hash und Zuordnung zur
ursprünglichen XML-Position speichern.

Absätze als sprachliche Einheiten übersetzen. Einzelne Textknoten eignen sich als
Einfügepositionen, aber nicht als unabhängige Übersetzungseinheiten: Ein Satz kann
über normale, kursive und verlinkte Textknoten verteilt sein.

Inline-Elemente werden durch geschützte Marker repräsentiert. Goblin prüft deren
Identität und gültige Verschachtelung und rekonstruiert die ursprünglichen Elemente.
Eine einfache Zuordnung `segment_id → translated_text` allein reicht bei
Inline-Formatierung nicht aus. Die konkrete Marker-/Antwortrepräsentation ist vor
der Implementierung anhand verschachtelter Formatierung und Fußnoten zu testen.

### 3. Buchkontext und erstes Glossar erzeugen

Ein erster kompakter Kontext enthält Figuren, Orte, Eigennamen, besondere Begriffe,
Erzählperspektive und Stil. Er entsteht aus definierten Textgrundlagen und darf
keine vermeintlichen Buchinhalte aus Modellwissen ergänzen. Eine Analyse von
Stichproben gilt nicht als vollständige Kenntnis des Buches.

Nach jedem Kapitel können Kontext und Glossar um neue Vorschläge ergänzt werden.
Kurze Kapitelzusammenfassungen dienen dem Anschlusskontext. Bestätigte Regeln
bleiben verbindlich; neue Vorschläge dürfen sie nicht automatisch überschreiben.

### 4. Übersetzen

Kapitel sind logische Arbeitseinheiten, API-Aufrufe können kleinere Teile eines
Kapitels enthalten. Keine feste Größe von 5.000–10.000 Wörtern voraussetzen:
Abschnittsgrößen richten sich nach dem Eingabe- und Ausgabetokenbudget des Modells,
mit Reserve für Übersetzung, Kontext, Marker und strukturierte Antworten.

Jeder Aufruf erhält Zielsprache, Stilvorgaben, relevante Glossareinträge und
begrenzten vorherigen Kapitel-/Abschnittskontext. Zurück kommen strukturierte
Übersetzungen mit Segment-IDs und geschützten Markern, kein frei erzeugtes HTML.
Goblin kontrolliert die exakte Menge der erwarteten IDs und verwaltet die Struktur.

Ergebnisse, Quell-Hashes, Modell, Prompt-Version und verwendete Glossarversion
werden pro Abschnitt dauerhaft gespeichert. Fertige, weiterhin gültige Abschnitte
werden beim Fortsetzen wiederverwendet.

### 5. Qualität und Konsistenz prüfen

Verbindliche technische Prüfungen in allen Qualitätsprofilen:

- Alle erwarteten Segmente sind vorhanden; keine fremden oder doppelten IDs.
- Keine leeren Übersetzungen für nicht leere übersetzbare Inhalte.
- Marker, Verschachtelungen und geschützte Verweise sind gültig.
- Antworten sind vollständig und nicht wegen Ausgabelimits abgebrochen.

Weitere Prüfungen je nach Profil:

- Zielsprache und auffällige unübersetzte Passagen.
- Glossarverstöße und inkonsistente Eigennamen.
- Auffällige Änderungen von Zahlen oder Angaben.
- Hinweise auf Bedeutungsverlust, Zusammenfassung oder ausgelassene Textteile.

Zahlen, Namen, Datumsformate und sprachliche Varianten dürfen nicht blind auf
Zeichenidentität geprüft werden. Beispielsweise sind „twenty“ → „zwanzig“ und
„Mr. Tagomi“ → „Herr Tagomi“ zulässige Änderungen.

Semantische QA liefert Hinweise, keine Garantie für Fehlerfreiheit. Auch vollständige
Segment-IDs garantieren nicht, dass jede Aussage korrekt übersetzt wurde.

Fehlerhafte Segmente gezielt mit erforderlichem Nachbarkontext erneut übersetzen.
Wiederholungen und Kosten begrenzen. Bleibt ein Problem ungelöst, erhält der Auftrag
einen sichtbaren Prüfstatus; es darf keine stillschweigend unvollständige fertige
Übersetzung entstehen.

### 6. EPUB rekonstruieren

Übersetzte Texte und geschützte Elemente in die ursprünglichen XHTML-Strukturen
einsetzen. Links, Fußnoten, Überschriften, Bilder und Kapitelreihenfolge erhalten.
Navigationsbeschriftungen, relevante Textattribute, Sprachkennzeichnungen und
sprachabhängige Metadaten konsistent aktualisieren.

Keine KI-Antwort direkt als ausführbares Markup einsetzen. Text korrekt escapen;
Strukturelemente stammen aus den kontrollierten Originalstrukturen.

### 7. EPUB validieren

ZIP-/EPUB-Struktur, XML, Manifest, Spine, Navigation, Ressourcen und interne
Verweise prüfen. Optional EPUBCheck ergänzen und dessen Ergebnis dokumentieren.
Die Originaldatei bleibt auch bei Fehlern unangetastet.

### 8. Als abgeleitete Ausgabe archivieren

Erst ein vollständig verarbeitetes und technisch gültiges Ergebnis als fertige
Ausgabe veröffentlichen. Es erhält eigene Buch-ID, Datei, Hash und EPUB-Kennung,
Zielsprache und den sichtbaren Hinweis „KI-Übersetzung“.

Die Quell-ISBN wird nicht zur Ausgaben-ISBN der Übersetzung. Archivieren ohne
automatische Kataloganreicherung, die Metadaten des Originals erneut übernehmen
oder die Zielsprache überschreiben könnte.

Dauerhaft dokumentieren:

- Quellbuch-ID, Quellausgabe und Hash der tatsächlichen Quelldatei.
- Verbindung zum Ursprungswerk, sofern verlässlich vorhanden.
- Quell- und Zielsprache, Auftrag und Erstellungszeitpunkt.
- Qualitätsprofil, Modellkonfiguration und Prompt-Versionen.
- Glossarversionen, Revisionen und Prüfergebnisse.
- Verbrauch und Kostenberechnungsgrundlage.

Ein bestehender Werkmatch allein ist noch keine stabile, eigene Werkentität.
Die genaue Abbildung von Werk- und Ausgabenbeziehungen ist beim Datenmodell zu
klären; die eindeutige Verbindung zum Quellbuch ist in jedem Fall erforderlich.

## Glossar

Glossare sind eigenständige, editierbare und versionierte Ressourcen. Sie können
für weitere Bücher einer Reihe wiederverwendet werden. Jeder Eintrag enthält
mindestens Originalbegriff, Übersetzung oder Regel, Sprachpaar, Geltungsbereich,
Status und gegebenenfalls Kontextbedingungen beziehungsweise erlaubte Varianten.

Beispielhafte Einträge, keine automatisch vorausgesetzten Übersetzungen:

| Original | Deutsch / Regel |
| --- | --- |
| Pacific States of America | Pazifikstaaten von Amerika |
| Trade Mission | Handelsmission |
| I Ching | I Ging |
| Childan | unverändert lassen |
| Mr. Tagomi | Herr Tagomi |
| Nippon | Kontextbedingung ausdrücklich festlegen |

Status unterscheidet KI-Vorschläge von Nutzerfestlegungen. Buchspezifische Regeln
haben Vorrang vor allgemeinen Serienregeln. Grammatisch nötige Flexionen müssen
zugelassen werden können.

Glossaränderungen erzeugen eine neue Version. Bereits fertige, betroffene Segmente
werden als überprüfungsbedürftig markiert; Nutzer können ihre Neuübersetzung
auslösen. Keine automatische Ersetzung über fertigen Text per Suchen/Ersetzen.

## Qualitätsprofile

| Profil | Verarbeitung |
| --- | --- |
| Schnell | Ein Übersetzungsdurchlauf, günstiges konfiguriertes Modell, grundlegende Glossarregeln, alle technischen Mindestprüfungen. |
| Buch (Default) | Passendes Qualitätsmodell, vollständiges Glossar, Kapitelkontext und zusätzliche inhaltliche QA mit gezielten Wiederholungen. |
| Literarisch | Verarbeitung wie „Buch“ plus vergleichendes Lektorat mit Original und Übersetzung. |

Profile beschreiben den Ablauf, nicht dauerhaft festgeschriebene Modellnamen.
Modelle für Kontext, Übersetzung, QA und Lektorat sind getrennt konfigurierbar.

Beim Lektorat gelten dieselben Glossar-, Struktur- und Vollständigkeitsregeln.
Die erste Übersetzung bleibt als Revision erhalten. Vor Einsatz des Profils an
Testkapiteln prüfen, ob das Lektorat Qualität verbessert und keine Aussagen
auslässt oder zusätzliche Inhalte erfindet.

## Dauerhafte Aufträge, Budget und Betrieb

Die aktuellen Importjobs leben im Speicher und reichen für diese Funktion nicht
aus. Übersetzungsaufträge, Abschnittsstatus, Vorschauphase, Ergebnisse und
Verbrauch müssen in SQLite beziehungsweise lokalen Arbeitsdateien liegen.

Beispielhafte Zustände: `preparing`, `preview`, `awaiting_glossary`, `translating`,
`paused`, `checking`, `needs_review`, `assembling`, `completed`, `failed`, `cancelled`.
Nach Neustarts werden unterbrochene Arbeitsschritte erkannt und sicher fortgesetzt.
Auftragsübernahme und Archivierung müssen gegen parallele doppelte Verarbeitung
geschützt sein. Archivwartung und Löschung müssen aktive Aufträge berücksichtigen.

Vor dem Start eine Kostenspanne mit Berechnungsgrundlage anzeigen. Berücksichtigen:
Kontextanalyse, Übersetzung, wiederholt gesendeten Kontext, QA, Lektorat und eine
begrenzte Reserve für Reparaturversuche. Schätzung und tatsächlicher Verbrauch sind
getrennt darzustellen. Keine feste Laufzeit wie „wenige Stunden“ versprechen.

Eine Budgetgrenze pausiert vor weiteren Aufrufen, sobald das verbleibende Budget
für den nächsten begrenzten Aufruf nicht ausreicht. Kosten eines bereits laufenden
Aufrufs lassen sich durch Abbrechen nicht zuverlässig zurücknehmen. Nach einem
Timeout kann ein Aufruf abgerechnet worden sein, auch wenn Goblin keine Antwort
gespeichert hat; solche Fälle dürfen nicht als garantiert kostenlose Wiederholung
behandelt werden.

Die bestehende OpenAI-Provider-Schnittstelle wird weiterverwendet und um
funktionsabhängige Ausgabegrenzen und Zeitlimits erweitert. Ihr aktuelles festes
Limit von 1.200 Ausgabetokens ist für die Buchübersetzung nicht ausreichend.

## Abnahmekriterien und offene Detailentscheidungen

- Original und fertige Übersetzung sind getrennt und eindeutig verknüpft.
- Abbruch und Neustart verlieren keine erfolgreich gespeicherten Abschnitte.
- Fehlende Segmente, kaputte Marker und ungültige EPUB-Struktur verhindern die
  Veröffentlichung als fertige Ausgabe.
- Tests decken verschachtelte Inline-Formatierung, Fußnoten, EPUB2-/EPUB3-Navigation,
  Unicode, Metadaten, API-Fehler, Budgetpausen und doppelte Auftragsausführung ab.
- Wiederverwendetes Glossar und Buchüberschreibungen wirken nachvollziehbar.
- Vorschau und Glossarbearbeitung funktionieren vor der Restübersetzung.
- Literarische Qualität wird anhand repräsentativer Kapitel mit menschlicher
  Bewertung beurteilt; strukturierte Antworten und KI-QA allein sind kein Nachweis.

Vor Implementierung konkret entscheiden: Markerformat, kontextuelle Segmentgrößen,
Modellauswahl je Profil, Kostenberechnung, QA-Schwellen, Wiederholungsobergrenzen,
Werkbeziehungsmodell und Umfang der Metadatenübersetzung. EPUBCheck bleibt eine
optionale zusätzliche Prüfung; eine eigene strukturelle Validierung ist Pflicht.
