"""Discovery is separate from fetching. No implicit or LLM-generated URL search."""

from dataclasses import dataclass, field
from typing import Protocol

from .http_transport import validate_url
from .retrieval import SearchRequest


@dataclass(frozen=True)
class DiscoveredSource:
    """Candidate from an injected search/index provider, not yet source evidence."""

    url: str = field(repr=False)

    def __post_init__(self) -> None:
        validate_url(self.url)


class SourceDiscovery(Protocol):
    async def discover(self, request: SearchRequest) -> tuple[DiscoveredSource, ...]:
        """Search a real provider/index, honoring limit and its own HTTP budgets.

        Return only actual discovery results, never guessed or LLM-invented URLs.
        Metadata/snippets are not evidence. Fetching and provenance come later.
        Provider implementations must apply the Phase 2 network security policy.
        """
        ...
