# Importbenchmark – 2026-10-03T06-30-34Z-d36b384

Status: **blocked** (vor Messbeginn)

## Ergebnis

Der Benchmark `import-10k-v1` konnte nicht starten. Die Testdaten unter `/home/steffen/Schreibtisch/Entwicklungen/TestDaten` enthalten **427 unterstützte E-Books** (2,133,530,565 Bytes). Das einzige gefundene Goblin-Archiv `/home/steffen/Schreibtisch/Entwicklungen/GoblinArchive/goblin-data/goblin.db` enthält jedoch **38 statt 10.000 archivierten Büchern**. Ein versiegeltes 10k-Paket ist nicht vorhanden. Es gab **keine Importläufe**; Importzeit, Speicherpeak und fachlicher Prüfstatus sind nicht gemessen.

## Vorprüfung und Rohdaten

- Revision: `d36b384c018d7f7214f2f40cc670034df914415e`; Arbeitsbaum vor Erstellung dieses Berichts sauber.
- Formate: .epub: 395, .fb2: 7, .mobi: 2, .pdf: 23.
- Quelldatenbank: `PRAGMA quick_check = ok`, 0 Fremdschlüsselverletzungen; Staging leer. Dies ersetzt keinen 10k-Snapshot.
- [Eingabemanifest](input-manifest.json), SHA-256 `f0c950c98266b9d954ca152b52b43bcfb899392a328b923471659577674986b3`; Pfade, Bytegrößen und SHA-256 aller unterstützten Dateien.
- [Rohdaten der Vorprüfung](preflight.json) und [maschinenlesbare Zusammenfassung](summary.json).
- Runner-CLI geprüft; `verify --bundle benchmarks/.local/import-10k-v1` endet mit `FileNotFoundError` für `dataset.json`.

## Vergleich

Eine feste Referenz nach `import-10k-v1` und eine kompatible Vorgängermessung fehlen. Die bisherigen 43-Dateien-Messungen nutzen einen anderen Ausgangsbestand; wegen der gewechselten Hardware und des neuen Eingabeordners sind ihre Zeiten auch nicht direkt vergleichbar.

## Nächster Schritt

Einen konsistenten Snapshot mit mindestens 10.000 archivierten Büchern samt Dateien und Analyseindizes bereitstellen. Dann den neuen Eingabebestand mit eigener Datensatzkennung einfrieren, zwei Piloten, `verify` und zehn Referenzläufe auf dieser Hardware ausführen. Ein synthetischer 10k-Bestand wäre nur als gesondert gekennzeichneter Benchmark mit gespeichertem Generator zulässig.
