from typing import Protocol

from ..models import Claim, Report, ResearchBundle, ResearchQuestion, VerificationResult


class SynthesizerAgent(Protocol):
    async def synthesize(
        self, question: ResearchQuestion, bundle: ResearchBundle,
        claims: tuple[Claim, ...], verification: tuple[VerificationResult, ...],
    ) -> Report:
        """Produce a report preserving supplied claims, citations and assessments.

        Summary prose must reflect these records; semantic checking is deferred.
        """
        ...
