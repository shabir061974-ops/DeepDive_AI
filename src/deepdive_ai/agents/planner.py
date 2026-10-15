from typing import Protocol

from ..models import ResearchQuestion, ResearchTask


class PlannerAgent(Protocol):
    async def plan(self, question: ResearchQuestion) -> tuple[ResearchTask, ...]:
        """Decompose the question into bounded research tasks."""
        ...
