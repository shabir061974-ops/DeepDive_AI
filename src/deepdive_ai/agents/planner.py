from typing import Protocol

from ..models import ResearchQuestion, ResearchTask
from .common import AgentOutputError, MeshJSON, bounded_list, object_fields, text


class PlannerAgent(Protocol):
    async def plan(self, question: ResearchQuestion) -> tuple[ResearchTask, ...]:
        """Decompose the question into bounded research tasks."""
        ...


class MeshPlannerAgent:
    def __init__(self, llm: MeshJSON):
        self.llm = llm

    async def plan(self, question: ResearchQuestion) -> tuple[ResearchTask, ...]:
        limits = self.llm.limits
        text(question.id, limits.max_text_chars)
        text(question.text, limits.max_question_chars)
        result = object_fields(await self.llm.ask(
            "planner", "Decompose this question into independent, specific research queries. "
            f"Return 1 to {limits.max_tasks} tasks. Include tasks checking counter-evidence. "
            "Do not answer the question or generate URLs.",
            {"tasks": [{"instruction": "nonempty research query"}]},
            {"question": question.text}), {"tasks"})
        rows = bounded_list(result["tasks"], limits.max_tasks)
        if not rows:
            raise AgentOutputError("Planner must return at least one task")
        instructions = [text(object_fields(row, {"instruction"})["instruction"], limits.max_text_chars)
                        for row in rows]
        if len(set(instructions)) != len(instructions):
            raise AgentOutputError("Planner returned duplicate tasks")
        return tuple(ResearchTask(f"{question.id}:task:{i}", question.id, instruction)
                     for i, instruction in enumerate(instructions, 1))
