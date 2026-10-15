import json
from typing import Protocol

from ..models import Claim, ResearchBundle, VerificationResult, VerificationStatus, records
from .common import AgentOutputError, MeshJSON, bounded_list, object_fields, text
from .drafter import check_bundle, evidence_payload


class VerifierAgent(Protocol):
    async def verify(
        self, claims: tuple[Claim, ...], bundle: ResearchBundle,
    ) -> tuple[VerificationResult, ...]:
        """Assess entailment using actual excerpts; absence of support fails closed."""
        ...


class MeshVerifierAgent:
    """Semantic assessment with quote grounding; citation presence never suffices."""

    def __init__(self, llm: MeshJSON):
        self.llm = llm

    async def verify(self, claims: tuple[Claim, ...], bundle: ResearchBundle) -> tuple[VerificationResult, ...]:
        limits = self.llm.limits
        records(claims, Claim)
        check_bundle(bundle, self.llm)
        if len(claims) > limits.max_claims:
            raise AgentOutputError("Claim count exceeds verifier limit")
        known = {item.id: item for item in bundle.evidence}
        results = []
        for claim in claims:
            text(claim.text, limits.max_text_chars)
            if not set(claim.evidence_ids) <= set(known):
                raise AgentOutputError("Claim references unknown evidence")
            if not claim.evidence_ids:
                results.append(VerificationResult(claim.id, VerificationStatus.INSUFFICIENT,
                    "No cited evidence is available; semantic support cannot be established."))
                continue
            result = object_fields(await self.llm.ask(
                "verifier", "Independently evaluate the ENTIRE claim, including numbers, scope, dates and qualifiers. "
                "Evaluate every supplied excerpt, including uncited excerpts, for counter-evidence. "
                "Citation presence, lexical overlap, authority and absence of contradiction are NOT semantic support. "
                "supports means the excerpt entails the entire claim; contradicts means explicit incompatible facts. "
                "irrelevant means unrelated; insufficient means relevant but not enough. "
                "Keep rationales concise. For supports/contradicts return a short, nonempty "
                "EXACT substring quote from that excerpt. "
                "For other relations quote may be empty. Never infer facts beyond supplied excerpts. "
                "Verdict supported requires cited entailment and no contradictions; contradictory sources require "
                "insufficient_evidence, unless no cited support exists and the claim is explicitly contradicted, "
                "in which case use unsupported. Assess each evidence ID exactly once.",
                {"claim_id": "supplied claim id", "verdict": "supported|unsupported|insufficient_evidence",
                 "rationale": "semantic explanation",
                 "assessments": [{"evidence_id": "supplied id", "relation": "supports|contradicts|irrelevant|insufficient",
                                  "quote": "exact substring or empty", "rationale": "semantic explanation"}]},
                {"claim": {"id": claim.id, "text": claim.text, "evidence_ids": claim.evidence_ids},
                 "evidence": evidence_payload(bundle)}), {"claim_id", "verdict", "rationale", "assessments"})
            if (result["claim_id"] != claim.id or not isinstance(result["verdict"], str)
                    or result["verdict"] not in {status.value for status in VerificationStatus}):
                raise AgentOutputError("Verifier returned an invalid claim or verdict")
            text(result["rationale"], limits.max_text_chars)
            rows = bounded_list(result["assessments"], limits.max_evidence)
            assessed: dict[str, str] = {}
            for row in rows:
                row = object_fields(row, {"evidence_id", "relation", "quote", "rationale"})
                evidence_id = row["evidence_id"]
                if not isinstance(evidence_id, str) or evidence_id not in known or evidence_id in assessed:
                    raise AgentOutputError("Verifier returned unknown or duplicate evidence assessments")
                relation = row["relation"]
                if not isinstance(relation, str) or relation not in {"supports", "contradicts", "irrelevant", "insufficient"}:
                    raise AgentOutputError("Verifier returned an invalid evidence relation")
                quote = row["quote"]
                if not isinstance(quote, str) or len(quote) > limits.max_text_chars:
                    raise AgentOutputError("Verifier quote is invalid or too long")
                if relation in {"supports", "contradicts"} and not quote.strip():
                    raise AgentOutputError("Semantic support/contradiction requires an exact quote")
                if quote and quote not in known[evidence_id].excerpt:
                    raise AgentOutputError("Verifier quoted text outside retrieved evidence")
                text(row["rationale"], limits.max_text_chars)
                assessed[evidence_id] = relation
            if set(assessed) != set(known):
                raise AgentOutputError("Verifier must assess all supplied evidence")
            supporting = tuple(eid for eid in claim.evidence_ids if assessed[eid] == "supports")
            contradictions = tuple(eid for eid, relation in assessed.items() if relation == "contradicts")
            verdict = VerificationStatus(result["verdict"])
            # Deterministic vetoes can only downgrade the model's positive verdict.
            if contradictions:
                verdict = VerificationStatus.INSUFFICIENT if supporting else VerificationStatus.UNSUPPORTED
            elif verdict == VerificationStatus.SUPPORTED and not supporting:
                verdict = VerificationStatus.INSUFFICIENT
            elif verdict == VerificationStatus.UNSUPPORTED:
                # Absence of support is uncertainty; unsupported requires counter-evidence.
                verdict = VerificationStatus.INSUFFICIENT
            rationale = json.dumps({"model_rationale": result["rationale"], "model_verdict": result["verdict"],
                "effective_verdict": verdict.value, "assessments": rows}, ensure_ascii=False)
            result_ids = supporting if verdict == VerificationStatus.SUPPORTED else claim.evidence_ids
            results.append(VerificationResult(claim.id, verdict, rationale, result_ids))
        return tuple(results)
