from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import quote

import httpx

from backend.metadata import BookMetadata, FieldValue, normalize_isbn, plausible_match, year_from

logger = logging.getLogger(__name__)


def _first(value: Any) -> Any:
    result = value[0] if isinstance(value, list) and value else value
    if isinstance(result, dict):
        return result.get("label") or result.get("name") or result.get("id")
    return result


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        if isinstance(item, dict):
            item = item.get("label") or item.get("name") or item.get("id")
        if item is not None and str(item).strip():
            result.append(str(item).strip())
    return result


class MetadataProvider(ABC):
    name: str

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    @abstractmethod
    async def search(self, isbn: str | None, title: str, author: str | None) -> BookMetadata | None:
        raise NotImplementedError


class LobidProvider(MetadataProvider):
    name = "lobid"

    async def search(self, isbn: str | None, title: str, author: str | None) -> BookMetadata | None:
        query = f'isbn:{isbn}' if isbn else f'title:"{title}"' + (f' AND contribution.agent.label:"{author}"' if author else "")
        response = await self.client.get("https://lobid.org/resources/search", params={"q": query, "format": "json", "size": 5})
        response.raise_for_status()
        members = response.json().get("member", [])
        for item in members:
            contribution = item.get("contribution", [])
            authors = [c.get("agent", {}).get("label") for c in contribution if c.get("agent", {}).get("label")]
            result = BookMetadata(
                title=FieldValue(_first(item.get("title")), self.name),
                authors=FieldValue(authors, self.name),
                publication_year=FieldValue(year_from(_first(item.get("dateOfPublication"))), self.name),
                language=FieldValue(_first(item.get("language", [])), self.name),
                publisher=FieldValue(_first(item.get("publishedBy", [])), self.name),
                isbn=FieldValue(normalize_isbn(_first(item.get("isbn", []))), self.name),
                genres=FieldValue(_list(item.get("subject", [])), self.name),
            )
            if isbn or plausible_match(result, title, author):
                return result
        return None


class OpenLibraryProvider(MetadataProvider):
    name = "openlibrary"

    async def search(self, isbn: str | None, title: str, author: str | None) -> BookMetadata | None:
        params = {"isbn": isbn} if isbn else {"title": title, **({"author": author} if author else {})}
        params["limit"] = 5
        response = await self.client.get("https://openlibrary.org/search.json", params=params)
        response.raise_for_status()
        for item in response.json().get("docs", []):
            result = BookMetadata(
                title=FieldValue(item.get("title"), self.name),
                authors=FieldValue(_list(item.get("author_name")), self.name),
                publication_year=FieldValue(year_from(item.get("first_publish_year")), self.name),
                language=FieldValue(_first(item.get("language", [])), self.name),
                publisher=FieldValue(_first(item.get("publisher", [])), self.name),
                isbn=FieldValue(normalize_isbn(_first(item.get("isbn", []))), self.name),
                genres=FieldValue(_list(item.get("subject", []))[:20], self.name),
            )
            if isbn or plausible_match(result, title, author):
                return result
        return None


class GoogleBooksProvider(MetadataProvider):
    name = "googlebooks"

    async def search(self, isbn: str | None, title: str, author: str | None) -> BookMetadata | None:
        query = f"isbn:{isbn}" if isbn else f'intitle:"{title}"' + (f'+inauthor:"{author}"' if author else "")
        response = await self.client.get("https://www.googleapis.com/books/v1/volumes", params={"q": query, "maxResults": 5})
        response.raise_for_status()
        for wrapper in response.json().get("items", []):
            item = wrapper.get("volumeInfo", {})
            identifiers = item.get("industryIdentifiers", [])
            found_isbn = next((entry.get("identifier") for entry in identifiers if "ISBN" in entry.get("type", "")), None)
            result = BookMetadata(
                title=FieldValue(item.get("title"), self.name),
                authors=FieldValue(_list(item.get("authors")), self.name),
                publication_year=FieldValue(year_from(item.get("publishedDate")), self.name),
                language=FieldValue(item.get("language"), self.name),
                publisher=FieldValue(item.get("publisher"), self.name),
                isbn=FieldValue(normalize_isbn(found_isbn), self.name),
                genres=FieldValue(_list(item.get("categories")), self.name),
                description=FieldValue(item.get("description"), self.name),
            )
            if isbn or plausible_match(result, title, author):
                return result
        return None


PROVIDER_CLASSES = {
    "lobid": LobidProvider,
    "openlibrary": OpenLibraryProvider,
    "googlebooks": GoogleBooksProvider,
}


class ProviderChain:
    def __init__(self, providers: list[MetadataProvider]):
        self.providers = providers

    async def close(self) -> None:
        clients = {id(provider.client): provider.client for provider in self.providers}
        for client in clients.values():
            await client.aclose()

    async def enrich(self, embedded: BookMetadata) -> tuple[BookMetadata, list[str]]:
        title = embedded.title.value or ""
        author = _first(embedded.authors.value)
        used: list[str] = []
        for provider in self.providers:
            try:
                candidate = await provider.search(embedded.isbn.value, title, author)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                logger.warning("Metadata provider %s failed: %s", provider.name, exc)
                continue
            if not candidate:
                continue
            used.append(provider.name)
            self._merge(embedded, candidate)
            return embedded, used
        return embedded, used

    @staticmethod
    def _merge(target: BookMetadata, source: BookMetadata) -> None:
        # Embedded title and identifier are the most trustworthy for this concrete file.
        for name in target.__dataclass_fields__:
            current = getattr(target, name)
            incoming = getattr(source, name)
            # ISBN inference is handled separately by IsbnResolver so ambiguous editions
            # are never silently selected as a side effect of metadata enrichment.
            if name == "isbn":
                continue
            if name == "title" and current.value:
                continue
            if incoming.value not in (None, "", []):
                setattr(target, name, incoming)


def make_provider_chain(order: list[str], timeout: float) -> ProviderChain:
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": "GoblinArchivar/0.1"})
    return ProviderChain([PROVIDER_CLASSES[name](client) for name in order if name in PROVIDER_CLASSES])
