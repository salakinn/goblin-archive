# Feature 07: KI-Kostenübersicht und Limits

Status: Weitgehend umgesetzt. Die Installation speichert Nutzungsdaten und
Preis-Snapshots für Tagging, Spracherkennung und Übersetzung. Reservierungen
sichern Tages- und Monatsgrenzen bei parallelen Anfragen. Unbekannte Kosten
werden ausgewiesen; Warnschwellen und ein filterbarer Verlauf sind vorhanden.
CSV-Export und frei wählbare Zeiträume bleiben Erweiterungen.

## Ziel

KI-Verbrauch und Kosten sollen nachvollziehbar sein und durch globale Limits
kontrolliert werden können.

## Umfang

- Zentrale Nutzungsaufzeichnung für Tagging, Spracherkennung, Übersetzung,
  Qualitätssicherung und Lektorat.
- Übersicht für heute, Monat und frei wählbare Zeiträume.
- Filter nach Buch, Funktion, Modell und Status.
- Schätzung vor kostenpflichtigen Aktionen sowie laufende Ist-Kosten.
- Tages-, Monats- und Übersetzungslimits.
- Bei erreichtem Limit keine neue KI-Anfrage; Auftrag pausieren.
- Warnung bei etwa 80 Prozent Verbrauch.
- CSV- und JSON-Export.

## Datenmodell

Eine eigene Tabelle `ai_usage` sollte mindestens enthalten:

```text
id, created_at, feature, book_id, job_id, model,
input_tokens, output_tokens, input_rate, output_rate,
estimated_cost_usd, actual_cost_usd, status, error_code
```

Die zum Zeitpunkt des Aufrufs verwendeten Preise werden mitgespeichert. So
bleiben historische Kosten auch nach einer Preisänderung nachvollziehbar.

## Akzeptanzkriterien

- Jeder kostenpflichtige Aufruf erzeugt genau einen Datensatz, auch bei Fehlern.
- Übersichtssumme und Einzelaufzeichnungen stimmen überein.
- Parallele Anfragen können globale Limits nicht umgehen.
- Übersetzungsbudget und globale Limits wirken gemeinsam; das strengere Limit
  gewinnt.
- API-Keys sowie Prompt- und Antwortinhalte werden nicht gespeichert.
- Token ohne hinterlegte Preise erscheinen als „Preis unbekannt“, nicht als
  fälschlich kostenlose Anfrage.
- Verbrauchsdaten überleben Neustarts.

## Offene Entscheidungen

- Welche Server-Zeitzone gilt für Tages- und Monatsgrenzen?
- Soll die Kostenübersicht nur Admins oder später auch einzelne Benutzer sehen?
- Welche Modelle und Preislisten werden initial unterstützt?
