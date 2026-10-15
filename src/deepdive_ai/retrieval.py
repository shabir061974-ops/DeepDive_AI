"""Public-source HTTP fetching with provenance. Search-provider integration is deferred."""

import hashlib
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Protocol
from urllib.parse import urljoin, urlsplit

from .models import Evidence, Source, SourceProvenance, nonempty
from .http_transport import (
    BoundedHTTPClient, HTTPError, HTTPTransport, PublicHTTPTransport,
    RequestPolicy, UnsafeURL, validate_url,
)


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
    requested_url: str | None = field(default=None, repr=False)
    redirect_chain: tuple[str, ...] = field(default=(), repr=False)
    media_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, Source) or not isinstance(self.content, bytes) or not self.content:
            raise ValueError("retrieval requires a source and nonempty original bytes")
        if hashlib.sha256(self.content).hexdigest() != self.source.provenance.content_sha256:
            raise ValueError("retrieved content does not match provenance digest")

    def evidence_from_bytes(self, evidence_id: str, task_id: str, start: int, end: int) -> Evidence:
        """Create evidence only from a caller-selected, exact UTF-8 byte range.

        This does not extract PDF text, render HTML, or assess semantic support.
        """
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(self.content):
            raise ValueError("evidence byte range is invalid")
        try:
            excerpt = self.content[start:end].decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("evidence byte range must be valid UTF-8") from None
        return Evidence(evidence_id, self.source.id, task_id, excerpt, f"bytes {start}:{end} (UTF-8)")


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


class RetrievalError(HTTPError):
    pass


@dataclass(frozen=True)
class RetrievalSettings:
    policy: RequestPolicy = field(default_factory=RequestPolicy)
    max_redirects: int = 5

    def __post_init__(self) -> None:
        if not isinstance(self.policy, RequestPolicy):
            raise ValueError("retrieval policy must be a RequestPolicy")
        if type(self.max_redirects) is not int or not 0 <= self.max_redirects <= 10:
            raise ValueError("max_redirects must be between 0 and 10")


class HTTPWebRetriever:
    ALLOWED_TYPES = {"text/plain", "text/html", "text/csv", "application/xhtml+xml",
                     "application/json", "application/pdf"}
    REDIRECTS = {301, 302, 303, 307, 308}

    def __init__(self, settings: RetrievalSettings = RetrievalSettings(),
                 transport: HTTPTransport | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._settings = settings
        self._http = BoundedHTTPClient(transport if transport is not None else PublicHTTPTransport(),
                                       settings.policy, sleep)
        self._clock = clock

    async def search(self, request: SearchRequest) -> tuple[Source, ...]:
        raise RetrievalError("No search provider is configured; supply a public source URL")

    async def fetch(self, url: str) -> RetrievedDocument:
        current = validate_url(url)
        chain: list[str] = []
        for hop in range(self._settings.max_redirects + 1):
            if current in chain:
                raise RetrievalError("Redirect loop detected")
            chain.append(current)
            response = await self._http.exchange("GET", current, (
                ("Accept-Encoding", "identity"), ("User-Agent", "DeepDive-AI/0.1 (research retrieval)"),
                ("Accept", ", ".join(sorted(self.ALLOWED_TYPES)))))
            if response.status in self.REDIRECTS:
                if hop == self._settings.max_redirects:
                    raise RetrievalError("Redirect limit exceeded")
                location = response.header("location")
                if not location:
                    raise RetrievalError("Redirect is missing a destination")
                # Check raw input before urljoin can strip whitespace/control characters.
                if any(ord(c) <= 32 or ord(c) == 127 for c in location) or "\\" in location:
                    raise UnsafeURL("Redirect destination is malformed")
                destination = validate_url(urljoin(current, location))
                if urlsplit(current).scheme == "https" and urlsplit(destination).scheme != "https":
                    raise UnsafeURL("HTTPS downgrade redirects are not permitted")
                current = destination
                continue
            if response.status != 200:
                raise RetrievalError(f"Retrieval failed with HTTP status {response.status}")
            media_type = (response.header("content-type") or "").split(";", 1)[0].strip().lower()
            if media_type not in self.ALLOWED_TYPES:
                raise RetrievalError("Source response type is not supported")
            if not response.body:
                raise RetrievalError("Source response is empty")
            digest = hashlib.sha256(response.body).hexdigest()
            source_id = hashlib.sha256((current + "\n" + digest).encode("utf-8")).hexdigest()
            source = Source(source_id, current, current, SourceProvenance(
                self._clock(), digest, "http-get; identity encoding; original response bytes"))
            return RetrievedDocument(source, response.body, url, tuple(chain), media_type)
        raise AssertionError("unreachable")
