from typing import Protocol

from ..models import Claim, Report, ResearchBundle, ResearchQuestion, VerificationResult
from ..models import report_status
from ..reporting import render_verified_summary
from .common import AgentLimitError, AgentLimits


class SynthesizerAgent(Protocol):
    async def synthesize(
        self, question: ResearchQuestion, bundle: ResearchBundle,
        claims: tuple[Claim, ...], verification: tuple[VerificationResult, ...],
    ) -> Report:
        """Produce a report preserving supplied claims, citations and assessments.

        The concrete implementation copies accepted claims exactly into a canonical
        summary; all draft records remain available for audit and review.
        """
        ...


class VerifiedClaimsSynthesizerAgent:
    """Deterministic synthesis prevents paraphrase drift or invented final claims."""

    def __init__(self, limits: AgentLimits = AgentLimits()):
        self.limits = limits

    async def synthesize(
        self, question: ResearchQuestion, bundle: ResearchBundle,
        claims: tuple[Claim, ...], verification: tuple[VerificationResult, ...],
    ) -> Report:
        summary = render_verified_summary(bundle, claims, verification)
        if len(summary) > self.limits.max_report_chars:
            raise AgentLimitError("Final summary exceeds configured report limit")
        return Report(question, summary, bundle, claims, verification, report_status(claims, verification))
