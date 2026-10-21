from typing import Protocol

from ..models import Claim, ResearchBundle, ResearchQuestion
from .common import (
    AgentFailureCode, AgentFailureLocation, AgentOutputError, MeshJSON, bounded_list, object_fields, text,
)


# The only status a Drafter may return: a valid "no justified claims" answer.
INSUFFICIENT_EVIDENCE = "insufficient_evidence"


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
        try:
            output = await self.llm.ask(
                "drafter", f"Draft at most {limits.max_claims} atomic factual claims relevant to the question. "
                "Use only supplied excerpts. Each claim must cite existing evidence IDs. "
                "Do not embed URLs or citation markup in claim text. Preserve qualifiers, dates, "
                "scope and uncertainty; never hide conflicting evidence. "
                f'If the excerpts justify no claim, return {{"claims": [], "status": "{INSUFFICIENT_EVIDENCE}"}}. '
                f"Each claim text must be at most {limits.max_text_chars} characters; write a shorter claim rather "
                "than exceed this limit.",
                {"claims": [{"text": "one factual claim", "evidence_ids": ["supplied id"]}]},
                {"question": question.text, "evidence": evidence_payload(bundle)},
                allow_markdown_fence=True)
        except AgentOutputError as error:
            # Unparsed reply: location only (malformed JSON keeps its separate shape code).
            error.locate(AgentFailureLocation.WHOLE_REPLY)
            raise
        # Diagnostics record only where a check failed and the JSON type there, never text.
        try:
            # The root must be an object with exactly `claims`, or `claims` plus the single
            # allowlisted status, which is only valid with an empty claim list.
            has_status = isinstance(output, dict) and "status" in output
            result = object_fields(output, {"claims", "status"} if has_status else {"claims"})
            rows = bounded_list(result["claims"], limits.max_claims)
            if has_status and (result["status"] != INSUFFICIENT_EVIDENCE or rows):
                raise AgentOutputError("Drafter returned an unsupported status",
                                       code=AgentFailureCode.DRAFTER_INVALID_STATUS)
        except AgentOutputError as error:
            error.locate(AgentFailureLocation.WHOLE_REPLY, output)
            raise
        known = {item.id for item in bundle.evidence}
        claims = []
        for index, row in enumerate(rows, 1):
            try:
                fields = object_fields(row, {"text", "evidence_ids"})
                statement = text(fields["text"], limits.max_text_chars)
                if any(token in statement.lower() for token in ("http:", "https:", "](")):
                    raise AgentOutputError("Claim text must not contain embedded source links")
                ids = bounded_list(fields["evidence_ids"], limits.max_evidence)
                if not ids or any(not isinstance(item, str) or item not in known for item in ids) or len(set(ids)) != len(ids):
                    raise AgentOutputError("Drafter returned missing, duplicate or unknown evidence IDs")
            except AgentOutputError as error:
                error.locate(AgentFailureLocation.CLAIM_ROW, row, index)
                raise
            claims.append(Claim(f"{question.id}:claim:{index}", statement, tuple(ids)))
        if len({claim.text for claim in claims}) != len(claims):
            raise AgentOutputError("Drafter returned duplicate claims").locate(AgentFailureLocation.WHOLE_REPLY, output)
        return tuple(claims)
