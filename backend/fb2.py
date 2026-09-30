"""Bounded, entity-safe helpers for plain FictionBook 2 files."""
from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

from defusedxml import ElementTree as SafeElementTree

MAX_FB2_BYTES = 100_000_000


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def children(node: ElementTree.Element | None, name: str) -> list[ElementTree.Element]:
    return [item for item in node if local_name(item.tag) == name] if node is not None else []


def child(node: ElementTree.Element | None, name: str) -> ElementTree.Element | None:
    return next(iter(children(node, name)), None)


def text_content(node: ElementTree.Element | None, limit: int = 100_000) -> str | None:
    if node is None:
        return None
    parts: list[str] = []
    remaining = limit
    for part in node.itertext():
        if remaining <= 0:
            break
        parts.append(part[:remaining])
        remaining -= len(parts[-1])
    return re.sub(r"\s+", " ", " ".join(parts)).strip() or None


def parse_fb2(path: Path) -> ElementTree.Element:
    if path.stat().st_size > MAX_FB2_BYTES:
        raise ValueError("FB2-Datei überschreitet das Leselimit von 100 MB.")
    try:
        root = SafeElementTree.parse(path, forbid_dtd=True).getroot()
    except (ElementTree.ParseError, ValueError) as exc:
        raise ValueError("FB2-Datei enthält ungültiges oder unsicheres XML.") from exc
    if local_name(root.tag) != "FictionBook":
        raise ValueError("Datei enthält kein FictionBook-Dokument.")
    return root


def body_units(root: ElementTree.Element, limit: int = 2_000_000) -> list[tuple[str, str]]:
    bodies = [body for body in children(root, "body")
              if body.get("name", "").lower() not in {"notes", "footnotes", "comments"}]
    units: list[tuple[str, str]] = []
    for body in bodies:
        sections = children(body, "section") or [body]
        for section in sections:
            text = text_content(section, limit)
            if text:
                units.append((f"Abschnitt {len(units) + 1}", text))
    return units
