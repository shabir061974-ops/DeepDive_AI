from typing import Protocol

from ..models import Claim, ResearchBundle, VerificationResult


class VerifierAgent(Protocol):
    async def verify(
        self, claims: tuple[Claim, ...], bundle: ResearchBundle,
    ) -> tuple[VerificationResult, ...]:
        """Assess entailment using actual excerpts; absence of support fails closed."""
        ...
