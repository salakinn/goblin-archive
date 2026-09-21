#!/usr/bin/env python3
from __future__ import annotations

import argparse

from backend.config import get_settings
from backend.database import SessionLocal, engine, init_db
from backend.maintenance import clear_archive


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Löscht alle importierten Bücher und temporären Dateien aus Goblin Archivar."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Bestätigt die unwiderrufliche Löschung ohne Rückfrage.",
    )
    args = parser.parse_args()

    if not args.yes:
        confirmation = input("Wirklich das gesamte Archiv leeren? Tippe LÖSCHEN: ")
        if confirmation != "LÖSCHEN":
            print("Abgebrochen.")
            return

    settings = get_settings()
    init_db()
    try:
        result = clear_archive(settings, SessionLocal)
    finally:
        engine.dispose()

    print(f"Archiv geleert: {result['deleted_books']} Buch/Bücher gelöscht.")


if __name__ == "__main__":
    main()
