from __future__ import annotations

import logging
import struct
import tempfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.metadata import normalize_isbn

logger = logging.getLogger(__name__)

MAX_COVER_BYTES = 15 * 1024 * 1024
MAX_COVER_PIXELS = 50_000_000


@dataclass(frozen=True)
class CoverAsset:
    content: bytes
    extension: str
    mime_type: str
    width: int
    height: int
    source: str
    provider: str | None = None
    source_id: str | None = None
    source_url: str | None = None
    fetched_at: str | None = None

    @property
    def filename(self) -> str:
        return f"cover{self.extension}"

    def metadata(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "filename": self.filename,
            "source": self.source,
            "provider": self.provider,
            "source_id": self.source_id,
            "width": self.width,
            "height": self.height,
            "mime_type": self.mime_type,
        }
        if self.fetched_at:
            result["fetched_at"] = self.fetched_at
        if self.source_url:
            result["source_url"] = self.source_url
        return result


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    position = 2
    while position + 9 <= len(data):
        if data[position] != 0xFF:
            position += 1
            continue
        marker = data[position + 1]
        position += 2
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if position + 2 > len(data):
            break
        length = struct.unpack(">H", data[position:position + 2])[0]
        if length < 2 or position + length > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height, width = struct.unpack(">HH", data[position + 3:position + 7])
            return width, height
        position += length
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    kind = data[12:16]
    if kind == b"VP8X":
        return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
    if kind == b"VP8L" and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def inspect_image(data: bytes) -> tuple[str, str, int, int] | None:
    """Validate a complete image and return canonical suffix, MIME type and dimensions."""
    if not data or len(data) > MAX_COVER_BYTES:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        position, complete = 8, False
        while position + 12 <= len(data):
            length = struct.unpack(">I", data[position:position + 4])[0]
            end = position + 12 + length
            if end > len(data):
                break
            chunk_type = data[position + 4:position + 8]
            expected_crc = struct.unpack(">I", data[end - 4:end])[0]
            if zlib.crc32(data[position + 4:end - 4]) & 0xFFFFFFFF != expected_crc:
                break
            position = end
            if chunk_type == b"IEND":
                complete = position == len(data)
                break
        if not complete:
            return None
        result = (".png", "image/png", width, height)
    elif dimensions := _jpeg_size(data):
        if not data.rstrip().endswith(b"\xff\xd9"):
            return None
        result = (".jpg", "image/jpeg", *dimensions)
    elif dimensions := _webp_size(data):
        if int.from_bytes(data[4:8], "little") + 8 > len(data):
            return None
        result = (".webp", "image/webp", *dimensions)
    else:
        return None
    width, height = result[2], result[3]
    if width <= 0 or height <= 0 or width * height > MAX_COVER_PIXELS:
        return None
    return result


def embedded_cover(data: bytes | None) -> CoverAsset | None:
    details = inspect_image(data or b"")
    if not details:
        return None
    extension, mime_type, width, height = details
    return CoverAsset(data or b"", extension, mime_type, width, height, "embedded")


class CoverProvider:
    name: str

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def fetch(self, isbn: str) -> CoverAsset | None:
        raise NotImplementedError

    async def _download(self, url: str, isbn: str) -> CoverAsset | None:
        async with self.client.stream("GET", url) as response:
            if response.status_code == 404:
                return None
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > MAX_COVER_BYTES:
                return None
            # Provider data first lands in an isolated temporary file. Only a
            # completely downloaded and validated image is returned for atomic
            # placement inside the archive.
            with tempfile.TemporaryFile() as temporary:
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_COVER_BYTES:
                        return None
                    temporary.write(chunk)
                temporary.seek(0)
                content = temporary.read()
            source_url = str(response.url)
        details = inspect_image(content)
        if not details:
            logger.warning("Cover provider %s returned an invalid image for ISBN %s", self.name, isbn)
            return None
        extension, mime_type, width, height = details
        return CoverAsset(
            content, extension, mime_type, width, height, "external",
            provider=self.name, source_id=isbn, source_url=source_url,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )


class OpenLibraryCoverProvider(CoverProvider):
    name = "openlibrary"

    async def fetch(self, isbn: str) -> CoverAsset | None:
        return await self._download(
            f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false", isbn,
        )


class GoogleBooksCoverProvider(CoverProvider):
    name = "googlebooks"
    image_sizes = ("extraLarge", "large", "medium", "small", "thumbnail")

    async def fetch(self, isbn: str) -> CoverAsset | None:
        response = await self.client.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": f"isbn:{isbn}", "maxResults": 5},
        )
        response.raise_for_status()
        for wrapper in response.json().get("items", []):
            info = wrapper.get("volumeInfo", {})
            identifiers = {
                normalize_isbn(entry.get("identifier"))
                for entry in info.get("industryIdentifiers", [])
            }
            if isbn not in identifiers:
                continue
            links = info.get("imageLinks", {})
            url = next((links.get(size) for size in self.image_sizes if links.get(size)), None)
            if not url:
                continue
            return await self._download(str(url).replace("http://", "https://", 1), isbn)
        return None


class CoverService:
    def __init__(self, providers: list[CoverProvider]):
        self.providers = providers

    async def close(self) -> None:
        clients = {id(provider.client): provider.client for provider in self.providers}
        for client in clients.values():
            await client.aclose()

    async def resolve(self, raw_embedded: bytes | None, isbn: str | None) -> CoverAsset | None:
        asset = embedded_cover(raw_embedded)
        if asset:
            return asset
        normalized = normalize_isbn(isbn)
        if not normalized:
            return None
        for provider in self.providers:
            try:
                asset = await provider.fetch(normalized)
            except Exception as exc:
                logger.warning("Cover provider %s failed for ISBN %s: %s", provider.name, normalized, exc)
                continue
            if asset:
                return asset
        return None


def make_cover_service(timeout: float) -> CoverService:
    client = httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "GoblinArchivar/0.1"},
    )
    return CoverService([OpenLibraryCoverProvider(client), GoogleBooksCoverProvider(client)])
