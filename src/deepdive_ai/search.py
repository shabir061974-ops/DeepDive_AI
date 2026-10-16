"""Brave Web Search integration. Construction never activates external services."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping
from urllib.parse import urlencode

from .discovery import DiscoveredSource
from .configuration import provider_key
from .http_transport import (
    BoundedHTTPClient, FailureCategory, HTTPError, HTTPTransport, PublicHTTPTransport,
    RequestPolicy, status_category, validate_url,
)
from .retrieval import SearchRequest


class DiscoveryError(HTTPError):
    """Sanitized provider failure; no credential, query, URL or response body."""

    default_category = FailureCategory.INTERNAL


@dataclass(frozen=True)
class BraveSearchSettings:
    api_key: str = field(repr=False)
    endpoint: str = "https://api.search.brave.com/res/v1/web/search"
    search_lang: str = "en"
    country: str = "US"
    policy: RequestPolicy = field(default_factory=lambda: RequestPolicy(
        timeout_seconds=10, max_retries=1, max_response_bytes=256_000, max_requests=32, max_concurrency=2))

    def __post_init__(self) -> None:
        if (not isinstance(self.api_key, str) or not 1 <= len(self.api_key) <= 4096
                or any(ord(c) <= 32 or ord(c) >= 127 for c in self.api_key)):
            raise ValueError("BRAVE_SEARCH_API_KEY must contain a valid nonempty header value")
        if self.endpoint != "https://api.search.brave.com/res/v1/web/search":
            raise ValueError("Brave search endpoint must use the approved HTTPS origin and path")
        if (not isinstance(self.search_lang, str) or not 2 <= len(self.search_lang) <= 12
                or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-" for c in self.search_lang)):
            raise ValueError("Invalid Brave search language")
        if (not isinstance(self.country, str) or len(self.country) != 2
                or not self.country.isascii() or not self.country.isupper() or not self.country.isalpha()):
            raise ValueError("Invalid Brave search country")
        if not isinstance(self.policy, RequestPolicy):
            raise ValueError("Search policy must be a RequestPolicy")

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None, *, search_lang: str = "en",
        country: str = "US", policy: RequestPolicy | None = None,
    ) -> "BraveSearchSettings":
        key = provider_key("BRAVE_SEARCH_API_KEY", environment)
        if policy is None:
            return cls(api_key=key, search_lang=search_lang, country=country)
        return cls(api_key=key, search_lang=search_lang, country=country, policy=policy)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError


class BraveSearchDiscovery:
    def __init__(self, settings: BraveSearchSettings, transport: HTTPTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._settings = settings
        self._http = BoundedHTTPClient(transport if transport is not None else PublicHTTPTransport(), settings.policy, sleep)

    async def discover(self, request: SearchRequest) -> tuple[DiscoveredSource, ...]:
        if not isinstance(request, SearchRequest):
            raise ValueError("Expected a SearchRequest")
        if (len(request.query) > 400 or len(request.query.split()) > 50
                or any(ord(c) < 32 and c not in "\n\t" for c in request.query)):
            raise DiscoveryError("Search query exceeds provider limits or contains invalid controls")
        try:
            query = urlencode({"q": request.query, "count": request.limit, "search_lang": self._settings.search_lang,
                               "country": self._settings.country, "spellcheck": "false",
                               "result_filter": "web"}, encoding="utf-8", errors="strict")
        except UnicodeError:
            raise DiscoveryError("Search query contains invalid Unicode") from None
        try:
            response = await self._http.exchange("GET", self._settings.endpoint + "?" + query, (
                ("X-Subscription-Token", self._settings.api_key), ("Accept", "application/json"),
                ("Accept-Encoding", "identity")))
        except HTTPError as error:
            raise DiscoveryError("Search provider request failed", category=error.category,
                                 http_status=error.http_status, stage=error.stage) from None
        except Exception:
            raise DiscoveryError("Search provider request failed") from None
        # Never follow a redirect while holding a provider credential.
        if response.status != 200:
            raise DiscoveryError(f"Search provider failed with HTTP status {response.status}",
                                 category=status_category(response.status), http_status=response.status)
        try:
            if (response.header("content-type") or "").split(";", 1)[0].strip().lower() != "application/json":
                raise ValueError
            data = json.loads(response.body.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_invalid_constant)
            if not isinstance(data, dict) or data.get("type") != "search" or "error" in data:
                raise ValueError
            if "web" not in data:
                return ()
            web = data["web"]
            if not isinstance(web, dict) or not isinstance(web.get("results"), list):
                raise ValueError
            rows = web["results"]
            if len(rows) > request.limit:
                raise ValueError
            candidates = []
            seen = set()
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("url"), str):
                    raise ValueError
                url = validate_url(row["url"])
                if url not in seen:
                    candidates.append(DiscoveredSource(url))
                    seen.add(url)
            return tuple(candidates)
        except HTTPError as error:
            raise DiscoveryError("Search provider returned malformed or unsafe results", category=error.category,
                                 http_status=response.status) from None
        except (ValueError, TypeError, RecursionError):
            raise DiscoveryError("Search provider returned malformed or unsafe results", category=FailureCategory.PARSING,
                                 http_status=response.status) from None
