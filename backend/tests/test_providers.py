import httpx
import pytest

from backend.metadata import BookMetadata, FieldValue
from backend.providers import MetadataProvider, ProviderChain
from backend.providers import _first, _list


class FailingProvider(MetadataProvider):
    name = "failing"

    def __init__(self):
        pass

    async def search(self, isbn, title, author):
        raise httpx.ConnectError("offline")


class WorkingProvider(MetadataProvider):
    name = "working"

    def __init__(self):
        pass

    async def search(self, isbn, title, author):
        return BookMetadata(
            title=FieldValue("Der Hobbit", self.name),
            authors=FieldValue(["J.R.R. Tolkien"], self.name),
            publisher=FieldValue("Klett-Cotta", self.name),
        )


@pytest.mark.asyncio
async def test_provider_fallback_and_merge():
    embedded = BookMetadata(title=FieldValue("Der Hobbit", "embedded"))
    result, used = await ProviderChain([FailingProvider(), WorkingProvider()]).enrich(embedded)
    assert used == ["working"]
    assert result.title.source == "embedded"
    assert result.publisher.value == "Klett-Cotta"
    assert result.publisher.source == "working"


def test_structured_lobid_values_are_reduced_to_labels():
    assert _first([{"id": "lang:de", "label": "Deutsch"}]) == "Deutsch"
    assert _list([{"label": "Historischer Roman"}, {"id": "topic:fiction"}]) == [
        "Historischer Roman", "topic:fiction",
    ]
