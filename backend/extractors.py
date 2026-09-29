from __future__ import annotations

import re
import posixpath
import struct
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree

from ebooklib import ITEM_COVER, ITEM_IMAGE, epub
from pypdf import PdfReader

from backend.metadata import BookMetadata, FieldValue, normalize_isbn, year_from
from backend.epub_safety import UnsafeEpubError, validate_epub_zip


class InvalidBookError(ValueError):
    pass


class _CoverReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        local = tag.rsplit(":", 1)[-1].lower()
        values = {key.rsplit(":", 1)[-1].lower(): value for key, value in attrs}
        reference = (
            values.get("src") if local == "img"
            else values.get("href") if local == "image"
            else values.get("data") if local == "object"
            else None
        )
        if reference:
            self.references.append(reference)


@dataclass
class Extracted:
    metadata: BookMetadata
    cover: bytes | None = None


def _zip_member(archive: zipfile.ZipFile, name: str) -> bytes | None:
    normalized = posixpath.normpath(name.replace("\\", "/")).lstrip("/")
    if normalized == ".." or normalized.startswith("../"):
        return None
    try:
        return archive.read(normalized)
    except KeyError:
        return None


def _relative_zip_member(base_member: str, href: str) -> str | None:
    decoded = unquote(href.split("#", 1)[0].split("?", 1)[0]).strip()
    if not decoded or decoded.startswith(("/", "\\")) or "://" in decoded:
        return None
    member = posixpath.normpath(posixpath.join(posixpath.dirname(base_member), decoded))
    if member == ".." or member.startswith("../"):
        return None
    return member


def _cover_from_document(
    archive: zipfile.ZipFile,
    document_member: str,
) -> bytes | None:
    document_data = _zip_member(archive, document_member)
    if not document_data:
        return None
    references: list[str] = []
    try:
        document = ElementTree.fromstring(document_data)
    except ElementTree.ParseError:
        parser = _CoverReferenceParser()
        parser.feed(document_data.decode("utf-8", "replace"))
        references = parser.references
    else:
        for node in document.iter():
            local = node.tag.rsplit("}", 1)[-1].lower()
            if local == "img" and node.get("src"):
                references.append(node.get("src", ""))
            elif local == "image":
                reference = node.get("href") or next(
                    (value for key, value in node.attrib.items() if key.endswith("}href")), None,
                )
                if reference:
                    references.append(reference)
            elif local == "object" and node.get("data"):
                references.append(node.get("data", ""))
    for reference in references:
        image_member = _relative_zip_member(document_member, reference)
        if image_member and Path(image_member).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            image = _zip_member(archive, image_member)
            if image:
                return image
    return None


def _epub_cover(path: Path) -> bytes | None:
    """Read EPUB2 meta/guide and EPUB3 cover-image conventions from the OPF."""
    try:
        with zipfile.ZipFile(path) as archive:
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next(
                (node.get("full-path") for node in container.iter() if node.tag.endswith("rootfile")),
                None,
            )
            if not rootfile:
                return None
            opf_data = _zip_member(archive, rootfile)
            if not opf_data:
                return None
            package = ElementTree.fromstring(opf_data)
            manifest: dict[str, tuple[str, str, str]] = {}
            cover_id: str | None = None
            guide_href: str | None = None
            for node in package.iter():
                local = node.tag.rsplit("}", 1)[-1]
                if local == "item" and node.get("id") and node.get("href"):
                    manifest[node.get("id", "")] = (
                        node.get("href", ""), node.get("media-type", ""), node.get("properties", ""),
                    )
                elif local == "meta" and node.get("name", "").lower() == "cover":
                    cover_id = node.get("content")
                elif local == "reference" and "cover" in node.get("type", "").lower():
                    guide_href = node.get("href")
            candidate: str | None = None
            if cover_id in manifest:
                candidate = manifest[cover_id][0]
            if not candidate:
                candidate = next(
                    (href for href, media, properties in manifest.values()
                     if "cover-image" in properties.split() and media.startswith("image/")),
                    None,
                )
            if not candidate and guide_href:
                guide_base = unquote(guide_href.split("#", 1)[0])
                if Path(guide_base).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                    candidate = guide_base
                elif Path(guide_base).suffix.lower() in {".html", ".htm", ".xhtml"}:
                    guide_member = _relative_zip_member(rootfile, guide_base)
                    if guide_member:
                        cover = _cover_from_document(archive, guide_member)
                        if cover:
                            return cover
            if not candidate:
                candidate = next(
                    (href for href, media, _properties in manifest.values()
                     if media.startswith("image/") and "cover" in href.lower()),
                    None,
                )
            if not candidate:
                return None
            member = _relative_zip_member(rootfile, candidate)
            return _zip_member(archive, member) if member else None
    except (KeyError, OSError, zipfile.BadZipFile, ElementTree.ParseError):
        return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _epub_values(book, namespace: str, key: str) -> list[str]:
    return [value for value, _attributes in book.get_metadata(namespace, key) if _clean(value)]


def extract_epub(path: Path) -> Extracted:
    try:
        if not zipfile.is_zipfile(path):
            raise InvalidBookError("Datei ist kein gültiges EPUB-Archiv")
        with zipfile.ZipFile(path) as archive:
            validate_epub_zip(archive)
        book = epub.read_epub(str(path), options={"ignore_ncx": True})
    except UnsafeEpubError as exc:
        raise InvalidBookError(str(exc)) from exc
    except InvalidBookError:
        raise
    except Exception as exc:
        raise InvalidBookError(f"EPUB konnte nicht gelesen werden: {exc}") from exc
    titles = _epub_values(book, "DC", "title")
    authors = _epub_values(book, "DC", "creator")
    identifiers = _epub_values(book, "DC", "identifier")
    isbn = next((normalize_isbn(value) for value in identifiers if normalize_isbn(value)), None)
    metadata = BookMetadata(
        title=FieldValue(_clean(titles[0]) if titles else None, "embedded" if titles else None),
        authors=FieldValue([_clean(v) for v in authors if _clean(v)], "embedded" if authors else None),
        publication_year=FieldValue(year_from((_epub_values(book, "DC", "date") or [None])[0]), "embedded"),
        language=FieldValue(_clean((_epub_values(book, "DC", "language") or [None])[0]), "embedded"),
        publisher=FieldValue(_clean((_epub_values(book, "DC", "publisher") or [None])[0]), "embedded"),
        isbn=FieldValue(isbn, "embedded" if isbn else None),
        genres=FieldValue([_clean(v) for v in _epub_values(book, "DC", "subject") if _clean(v)], "embedded"),
        description=FieldValue(_clean((_epub_values(book, "DC", "description") or [None])[0]), "embedded"),
    )
    cover = _epub_cover(path)
    if not cover:
        for item in book.get_items():
            if item.get_type() == ITEM_COVER or (item.get_type() == ITEM_IMAGE and "cover" in item.get_name().lower()):
                cover = item.get_content()
                break
    return Extracted(metadata, cover)


def extract_pdf(path: Path) -> Extracted:
    try:
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise InvalidBookError("PDF-Signatur fehlt")
        reader = PdfReader(str(path))
        info = reader.metadata or {}
        # Accessing pages forces validation of the cross-reference/page tree.
        _ = len(reader.pages)
    except InvalidBookError:
        raise
    except Exception as exc:
        raise InvalidBookError(f"PDF konnte nicht gelesen werden: {exc}") from exc
    raw_author = _clean(info.get("/Author"))
    metadata = BookMetadata(
        title=FieldValue(_clean(info.get("/Title")), "embedded"),
        authors=FieldValue([raw_author] if raw_author else [], "embedded" if raw_author else None),
        publication_year=FieldValue(year_from(info.get("/CreationDate")), "embedded"),
        publisher=FieldValue(_clean(info.get("/Producer")), "embedded"),
        description=FieldValue(_clean(info.get("/Subject")), "embedded"),
    )
    cover = None
    try:
        # PDF page thumbnails are explicit embedded previews; the page itself is never rendered as a cover.
        thumbnail = reader.pages[0].get("/Thumb") if reader.pages else None
        if thumbnail:
            thumbnail = thumbnail.get_object()
            cover = thumbnail.get_data()
    except Exception:
        pass
    return Extracted(metadata, cover)


def extract_mobi(path: Path) -> Extracted:
    data = path.read_bytes()
    if len(data) < 80 or data[60:68] != b"BOOKMOBI":
        raise InvalidBookError("MOBI/AZW3-Signatur fehlt")
    metadata = BookMetadata()
    cover = None
    try:
        record_count = struct.unpack(">H", data[76:78])[0]
        offsets = [struct.unpack(">I", data[78 + index * 8:82 + index * 8])[0] for index in range(record_count)]
        first_offset = offsets[0] if offsets else 0
        mobi_start = first_offset + 16
        if data[mobi_start:mobi_start + 4] != b"MOBI":
            return Extracted(metadata)
        full_name_offset = struct.unpack(">I", data[mobi_start + 84:mobi_start + 88])[0]
        full_name_length = struct.unpack(">I", data[mobi_start + 88:mobi_start + 92])[0]
        title = data[first_offset + full_name_offset:first_offset + full_name_offset + full_name_length].decode("utf-8", "replace")
        metadata.title = FieldValue(_clean(title), "embedded")
        exth_flags = struct.unpack(">I", data[mobi_start + 128:mobi_start + 132])[0]
        if exth_flags & 0x40:
            header_length = struct.unpack(">I", data[mobi_start + 20:mobi_start + 24])[0]
            pos = mobi_start + header_length
            if data[pos:pos + 4] == b"EXTH":
                count = struct.unpack(">I", data[pos + 8:pos + 12])[0]
                cursor = pos + 12
                values: dict[int, list[str]] = {}
                raw_values: dict[int, list[bytes]] = {}
                for _ in range(count):
                    kind, length = struct.unpack(">II", data[cursor:cursor + 8])
                    raw = data[cursor + 8:cursor + length]
                    raw_values.setdefault(kind, []).append(raw)
                    values.setdefault(kind, []).append(raw.decode("utf-8", "replace"))
                    cursor += length
                metadata.authors = FieldValue(values.get(100, []), "embedded" if values.get(100) else None)
                metadata.publisher = FieldValue(_clean((values.get(101) or [None])[0]), "embedded")
                metadata.description = FieldValue(_clean((values.get(103) or [None])[0]), "embedded")
                metadata.isbn = FieldValue(normalize_isbn((values.get(104) or [None])[0]), "embedded")
                metadata.publication_year = FieldValue(year_from((values.get(106) or [None])[0]), "embedded")
                if raw_values.get(201):
                    cover_offset = int.from_bytes(raw_values[201][0], "big")
                    first_image_index = struct.unpack(">I", data[mobi_start + 108:mobi_start + 112])[0]
                    image_index = first_image_index + cover_offset
                    if image_index < len(offsets):
                        end = offsets[image_index + 1] if image_index + 1 < len(offsets) else len(data)
                        cover = data[offsets[image_index]:end]
    except (struct.error, IndexError, ValueError):
        pass
    return Extracted(metadata, cover)


def detect_and_extract(path: Path, original_filename: str) -> tuple[str, Extracted]:
    extension = Path(original_filename).suffix.lower().lstrip(".")
    if extension == "epub":
        return extension, extract_epub(path)
    if extension == "pdf":
        return extension, extract_pdf(path)
    if extension in {"mobi", "azw3"}:
        return extension, extract_mobi(path)
    raise InvalidBookError(f"Nicht unterstütztes Dateiformat: {extension or 'ohne Endung'}")
