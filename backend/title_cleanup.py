"""Conservative, versioned formatting of display titles for new imports."""
from __future__ import annotations

import unicodedata
from datetime import datetime, timezone

from backend.metadata import FieldValue

VERSION = "1"
WHITESPACE = frozenset("\u0009\u000a\u000b\u000c\u000d\u0020\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000")


def clean_title(value: str) -> tuple[str, list[str]]:
    rules: list[str] = []
    title = unicodedata.normalize("NFC", value)
    if title != value:
        rules.append("nfc")
    without_bom = title.lstrip("\ufeff")
    if without_bom != title:
        rules.append("leading_bom")
    title = without_bom
    spaced = "".join(" " if char in WHITESPACE else char for char in title)
    if spaced != title:
        rules.append("whitespace")
    title = " ".join(part for part in spaced.split(" ") if part)
    if title != spaced and "whitespace" not in rules:
        rules.append("whitespace")
    for char in title:
        if unicodedata.category(char) == "Cc":
            raise ValueError("Titel enthält ein unzulässiges Steuerzeichen")
    if not title:
        raise ValueError("Titel darf nicht leer sein")
    if len(title) > 500:
        raise ValueError("Titel darf höchstens 500 Zeichen lang sein")
    return title, rules


def clean_field(field: FieldValue, history: list[dict]) -> None:
    if field.source in {"manual", "user"}:
        return
    original = field.value
    cleaned, rules = clean_title(str(original))
    if cleaned != original:
        history.append({"old_title": original, "new_title": cleaned,
                        "source": field.source, "version": VERSION, "rules": rules,
                        "created_at": datetime.now(timezone.utc).isoformat()})
        field.value = cleaned
