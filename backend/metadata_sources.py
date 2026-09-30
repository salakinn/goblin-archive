"""Audit trail of source values used to select displayed book metadata."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.models import MetadataSourceValue


def record_sources(session, book_id: str, snapshots: list[dict]) -> None:
    previous: dict[str, tuple[str, str | None]] = {}
    for snapshot in snapshots:
        for field, entry in snapshot.items():
            if not isinstance(entry, dict) or "value" not in entry:
                continue
            value_json = json.dumps(entry["value"], ensure_ascii=False, sort_keys=True)
            source = entry.get("source")
            value = (value_json, source)
            if previous.get(field) == value:
                continue
            session.add(MetadataSourceValue(book_id=book_id, field=field, value_json=value_json,
                                            source=source, recorded_at=datetime.now(timezone.utc)))
            previous[field] = value
