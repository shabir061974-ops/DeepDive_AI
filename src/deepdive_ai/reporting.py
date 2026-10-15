"""Canonical final narrative: exact accepted claim text, with resolved citations."""

import json

from .models import Claim, ResearchBundle, VerificationResult, VerificationStatus, validate_verification


def render_verified_summary(bundle: ResearchBundle, claims: tuple[Claim, ...],
                            verification: tuple[VerificationResult, ...]) -> str:
    validate_verification(claims, bundle, verification)
    by_claim = {result.claim_id: result for result in verification}
    by_evidence = {item.id: item for item in bundle.evidence}
    by_source = {item.id: item for item in bundle.sources}
    accepted = []
    for claim in claims:
        result = by_claim[claim.id]
        if result.status != VerificationStatus.SUPPORTED:
            continue
        citations = []
        for evidence_id in result.evidence_ids:
            item = by_evidence[evidence_id]
            source = by_source[item.source_id]
            citations.append({"evidence_id": item.id, "source_id": source.id, "url": source.url,
                "locator": item.locator, "content_sha256": source.provenance.content_sha256,
                "retrieved_at": source.provenance.retrieved_at.isoformat()})
        accepted.append({"id": claim.id, "text": claim.text, "citations": citations})
    return json.dumps({"accepted_claims": accepted}, ensure_ascii=False, indent=2)
