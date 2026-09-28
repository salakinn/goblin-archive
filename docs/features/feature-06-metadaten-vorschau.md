# Feature 06: Metadaten-Vorschau vor dem Import

## Ziel

Metadaten und Cover sollen vor dem endgültigen Archivieren geprüft und
korrigiert werden können.

## Ablauf

```text
Upload → Analyse → Vorschau → Korrektur → Bestätigen → Archivieren
                             └────────── Überspringen / Verwerfen
```

## Umfang

- Persistenter Preview-Job mit Ablaufzeit und Status je Datei.
- Eingebettete Metadaten und optionale Provider-Anreicherung anzeigen.
- Titel, Autoren, Jahr, Sprache, Verlag, ISBN, Reihe, Beschreibung, Tags und
  Cover mit jeweiliger Quelle darstellen.
- Werte vor Bestätigung bearbeiten; manuelle Werte erhalten `source: manual`.
- Cover auswählen, vorhandenes Cover behalten oder Suche überspringen.
- Einzelne Dateien bestätigen, überspringen oder neu analysieren.
- Vorschauen nach 24 Stunden automatisch bereinigen.
- Vorschau darf keine kostenpflichtige KI-Analyse automatisch auslösen.

## Akzeptanzkriterien

- Vor Bestätigung existiert kein Bibliothekseintrag.
- Datenbank, `metadata.json` und FTS sind nach Bestätigung konsistent.
- Verwerfen und Ablauf entfernen Staging-Dateien sicher.
- Duplikate werden schon in der Vorschau markiert.
- Vorschauen überleben einen Neustart des Dienstes.
- Der direkte Import bleibt für große Sammelimporte verfügbar.

## Offene Entscheidungen

- Provider-Anreicherung automatisch oder erst nach Klick?
- Sammelbestätigung für unveränderte Dateien?
- Soll eine Vorschau auch ohne externe Provider funktionieren?
