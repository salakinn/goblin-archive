"""Remove promotional and identity terms from catalogue subject suggestions."""
from __future__ import annotations

from backend.metadata import normalize_text


NOISY_SUBJECT_MARKERS = (
    "geschenk", "bestseller", "adventskalender", "weihnachtsgeschenk",
    "new york times", "spiegel bestseller", "carlsen", "bloomsbury",
    "vorlesebuch", "vorlesen", "zum vorlesen", "zaubern", "phantastische tierwesen",
    "fur fans", "fur leser", "top seller", "must read",
)


def clean_subjects(values, *, known_names=(), limit: int = 20) -> list[str]:
    known = {normalize_text(value) for value in known_names if isinstance(value, str) and value.strip()}
    result: list[str] = []
    seen: set[str] = set()
    broad_groups = (
        {"fantasy", "fantasy bucher", "fantasy roman", "zeitgenossische fantasyromane"},
        {"magie", "zauber"},
    )
    for raw in (values if isinstance(values, (list, tuple)) else []):
        if not isinstance(raw, str):
            continue
        for part in raw.split("|"):
            value = " ".join(part.split()).strip(" -|,;:")
            key = normalize_text(value)
            if not value or len(value) < 3 or key in known or key in seen:
                continue
            if any(marker in key for marker in NOISY_SUBJECT_MARKERS):
                continue
            # These are broad catalogue shelf paths rather than useful book tags.
            generic_key = " ".join(key.split())
            if generic_key in {"abenteuer entdecken", "abenteuer und entdecken", "moderne klassiker",
                       "england", "schottland", "vereinigtes konigreich", "united kingdom"}:
                continue
            if any(generic_key in group and seen.intersection(group) for group in broad_groups):
                continue
            seen.add(generic_key)
            result.append(value[:160])
            if len(result) >= limit:
                return result
    return result
