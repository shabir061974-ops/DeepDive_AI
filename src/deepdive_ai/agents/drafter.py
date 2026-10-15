from typing import Protocol

from ..models import Claim, ResearchBundle, ResearchQuestion


class DrafterAgent(Protocol):
    async def draft(self, question: ResearchQuestion, bundle: ResearchBundle) -> tuple[Claim, ...]:
        """Draft claims whose evidence identifiers resolve within the bundle."""
        ...
