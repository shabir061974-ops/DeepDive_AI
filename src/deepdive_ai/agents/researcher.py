from typing import Protocol

from ..models import ResearchBundle, ResearchQuestion, ResearchTask, VerificationResult


class ResearcherAgent(Protocol):
    async def research(
        self, question: ResearchQuestion, tasks: tuple[ResearchTask, ...],
        previous: ResearchBundle, feedback: tuple[VerificationResult, ...],
    ) -> ResearchBundle:
        """Return a complete updated bundle with provenance; use feedback on retries.

        Preserve previous records and identifiers. Never manufacture findings.
        """
        ...
