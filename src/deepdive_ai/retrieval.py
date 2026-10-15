"""Future real retrieval boundary. No search or HTTP implementation is enabled."""

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from .models import Source, nonempty


@dataclass(frozen=True)
class SearchRequest:
    query: str = field(repr=False)
    limit: int = 5

    def __post_init__(self) -> None:
        nonempty(self.query, "query")
        if type(self.limit) is not int or not 1 <= self.limit <= 20:
            raise ValueError("search limit must be between 1 and 20")


@dataclass(frozen=True)
class RetrievedDocument:
    source: Source
    content: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source, Source) or not isinstance(self.content, bytes) or not self.content:
            raise ValueError("retrieval requires a source and nonempty original bytes")
        if hashlib.sha256(self.content).hexdigest() != self.source.provenance.content_sha256:
            raise ValueError("retrieved content does not match provenance digest")


class WebRetriever(Protocol):
    async def search(self, request: SearchRequest) -> tuple[Source, ...]:
        """Search real sources; never generate substitute findings."""
        ...

    async def fetch(self, url: str) -> RetrievedDocument:
        """Fetch original bytes and record final URL, time, method and digest.

        Future transports must restrict destinations and redirects, set timeouts
        and size limits, and treat retrieved content as untrusted input.
        """
        ...


class DisabledWebRetriever:
    async def search(self, request: SearchRequest) -> tuple[Source, ...]:
        raise RuntimeError("Web retrieval is disabled in Phase 1")

    async def fetch(self, url: str) -> RetrievedDocument:
        raise RuntimeError("Web retrieval is disabled in Phase 1")
