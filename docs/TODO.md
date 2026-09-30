# Offene Nacharbeiten zu abgeschlossenen Features

- **KI-Kostenübersicht (ehemals Feature 07):** Nutzungsverlauf im Einstellungsdialog
  mit Filtern nach Buch, Funktion, Modell und Status anzeigen; frei wählbare
  Zeiträume und einen CSV-/JSON-Export ergänzen. Schätzungen vor kostenpflichtigen
  Aktionen und das Zusammenspiel von Übersetzungsbudget und globalen Limits
  nachvollziehbar in der Oberfläche darstellen.
- **Duplikatsprüfung (ehemals Feature 08):** Versionen und Revisionen der Treffer
  und Entscheidungen absichern, unvollständige Kandidatensuchen sichtbar machen
  und Extraktion/Normalisierung bei großen Archiven ressourcenbegrenzt prüfen.
  Schwellenwerte und Fehlalarme anhand einer repräsentativen Sammlung kalibrieren;
  OCR und zusätzliche Textextraktoren sind spätere optionale Ausbauten.
- **Paralleler Import (ehemals Feature 09):** Provider-429/`Retry-After` und
  Wartezeiten so steuern, dass ein wartender Aufruf keinen Buch-Worker bindet;
  faire Bedienung mehrerer Aufträge sowie begrenzte KI-Warteplätze prüfen.
  Auf demselben Bestand Parallelität 1–4 mit Antwortzeiten, Speicher und
  Fehlerraten vergleichen. Wiederaufnahme nach Neustart gehört zum offenen
  [Feature 01](features/feature-01-importjobs.md).
- **Lokale Spracherkennung (ehemals Feature 10):** Lingua an einem beschrifteten,
  repräsentativen Bestand mit Deutsch, Englisch, Niederländisch, gemischten und
  textarmen Büchern auswerten. Genauigkeit der automatischen Übernahmen,
  Fallbackquote, vermiedene KI-Aufrufe sowie Laufzeit (Median/p95) und Speicherbedarf
  messen; Schwellenwerte gegebenenfalls anhand der Fehlklassifikationen anpassen.

Die größeren offenen Vorhaben bleiben unter `docs/features/` dokumentiert.
