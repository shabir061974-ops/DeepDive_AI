from typing import Protocol

from ..models import Claim, ResearchBundle, ResearchQuestion
from .common import AgentOutputError, MeshJSON, bounded_list, object_fields, text


class DrafterAgent(Protocol):
    async def draft(self, question: ResearchQuestion, bundle: ResearchBundle) -> tuple[Claim, ...]:
        """Draft claims whose evidence identifiers resolve within the bundle."""
        ...


def evidence_payload(bundle: ResearchBundle) -> list[dict[str, str]]:
    return [{"id": item.id, "source_id": item.source_id, "excerpt": item.excerpt,
             "locator": item.locator} for item in bundle.evidence]


def check_bundle(bundle: ResearchBundle, llm: MeshJSON) -> None:
    limits = llm.limits
    if (len(bundle.sources) > limits.max_sources or len(bundle.evidence) > limits.max_evidence
            or sum(len(item.excerpt) for item in bundle.evidence) > limits.max_evidence_chars):
        raise AgentOutputError("Research bundle exceeds agent context limits")


class MeshDrafterAgent:
    def __init__(self, llm: MeshJSON):
        self.llm = llm

    async def draft(self, question: ResearchQuestion, bundle: ResearchBundle) -> tuple[Claim, ...]:
        limits = self.llm.limits
        text(question.text, limits.max_question_chars)
        check_bundle(bundle, self.llm)
        if not bundle.evidence:
            return ()
        result = object_fields(await self.llm.ask(
            "drafter", f"Draft at most {limits.max_claims} atomic factual claims relevant to the question. "
            "Use only supplied excerpts. Each claim must cite existing evidence IDs. "
            "Do not embed URLs or citation markup in claim text. Preserve qualifiers, dates, "
            "scope and uncertainty; never hide conflicting evidence. Return an empty list if no claim is justified. "
            f"Each claim text must be at most {limits.max_text_chars} characters; write a shorter claim rather "
            "than exceed this limit.",
            {"claims": [{"text": "one factual claim", "evidence_ids": ["supplied id"]}]},
            {"question": question.text, "evidence": evidence_payload(bundle)},
            allow_markdown_fence=True), {"claims"})
        rows = bounded_list(result["claims"], limits.max_claims)
        known = {item.id for item in bundle.evidence}
        claims = []
        for index, row in enumerate(rows, 1):
            row = object_fields(row, {"text", "evidence_ids"})
            statement = text(row["text"], limits.max_text_chars)
            if any(token in statement.lower() for token in ("http:", "https:", "](")):
                raise AgentOutputError("Claim text must not contain embedded source links")
            ids = bounded_list(row["evidence_ids"], limits.max_evidence)
            if not ids or any(not isinstance(item, str) or item not in known for item in ids) or len(set(ids)) != len(ids):
                raise AgentOutputError("Drafter returned missing, duplicate or unknown evidence IDs")
            claims.append(Claim(f"{question.id}:claim:{index}", statement, tuple(ids)))
        if len({claim.text for claim in claims}) != len(claims):
            raise AgentOutputError("Drafter returned duplicate claims")
        return tuple(claims)
