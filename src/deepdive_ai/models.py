"""Immutable domain records. Citations always resolve to supplied evidence."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
from urllib.parse import urlsplit


def nonempty(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")


def identifiers(values: tuple[str, ...], field: str) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{field} must be an immutable tuple")
    for value in values:
        nonempty(value, field)
    if len(set(values)) != len(values):
        raise ValueError(f"{field} must be unique")


def aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")


@dataclass(frozen=True)
class ResearchQuestion:
    id: str
    text: str

    def __post_init__(self) -> None:
        nonempty(self.id, "question id")
        nonempty(self.text, "question text")


@dataclass(frozen=True)
class ResearchTask:
    id: str
    question_id: str
    instruction: str

    def __post_init__(self) -> None:
        for name in ("id", "question_id", "instruction"):
            nonempty(getattr(self, name), name)


@dataclass(frozen=True)
class SourceProvenance:
    retrieved_at: datetime
    content_sha256: str
    retrieval_method: str

    def __post_init__(self) -> None:
        aware(self.retrieved_at)
        if (not isinstance(self.content_sha256, str)
                or len(self.content_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.content_sha256)):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        nonempty(self.retrieval_method, "retrieval_method")


@dataclass(frozen=True)
class Source:
    id: str
    url: str
    title: str
    provenance: SourceProvenance

    def __post_init__(self) -> None:
        nonempty(self.id, "source id")
        nonempty(self.title, "source title")
        nonempty(self.url, "source URL")
        try:
            url = urlsplit(self.url)
            valid = (url.scheme in {"http", "https"} and url.hostname
                     and not url.username and not url.password)
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("source URL must be HTTP(S) without credentials")
        if not isinstance(self.provenance, SourceProvenance):
            raise ValueError("source provenance is required")


@dataclass(frozen=True)
class ExtractionProvenance:
    """Offsets refer to a transformed text artifact, never original HTTP bytes."""

    original_content_sha256: str
    extracted_text_sha256: str
    excerpt_sha256: str
    method: str
    transformations: tuple[str, ...]
    text_start: int
    text_end: int
    page_number: int | None = None

    def __post_init__(self) -> None:
        for digest in (self.original_content_sha256, self.extracted_text_sha256, self.excerpt_sha256):
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Extraction digest must be lowercase SHA-256")
        nonempty(self.method, "extraction method")
        identifiers(self.transformations, "extraction transformations")
        if not self.transformations:
            raise ValueError("Extraction must record its transformations")
        if type(self.text_start) is not int or type(self.text_end) is not int or not 0 <= self.text_start < self.text_end:
            raise ValueError("Invalid extracted-text span")
        if self.page_number is not None and (type(self.page_number) is not int or self.page_number < 1):
            raise ValueError("Invalid PDF page number")


@dataclass(frozen=True)
class Evidence:
    id: str
    source_id: str
    task_id: str
    excerpt: str
    locator: str
    extraction: ExtractionProvenance | None = None

    def __post_init__(self) -> None:
        for name in ("id", "source_id", "task_id", "excerpt", "locator"):
            nonempty(getattr(self, name), name)
        if self.extraction is not None:
            if not isinstance(self.extraction, ExtractionProvenance):
                raise ValueError("Invalid extraction provenance")
            if len(self.excerpt) != self.extraction.text_end - self.extraction.text_start:
                raise ValueError("Excerpt length does not match extracted-text span")
            try:
                excerpt_digest = hashlib.sha256(self.excerpt.encode("utf-8")).hexdigest()
            except UnicodeError:
                raise ValueError("Transformed evidence must contain valid Unicode") from None
            if excerpt_digest != self.extraction.excerpt_sha256:
                raise ValueError("Excerpt digest does not match extraction provenance")
            if "bytes" in self.locator.lower():
                raise ValueError("Transformed evidence cannot claim original byte offsets")


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        nonempty(self.id, "claim id")
        nonempty(self.text, "claim text")
        identifiers(self.evidence_ids, "claim evidence ids")


class VerificationStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    INSUFFICIENT = "insufficient_evidence"


@dataclass(frozen=True)
class VerificationResult:
    claim_id: str
    status: VerificationStatus
    rationale: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        nonempty(self.claim_id, "claim id")
        nonempty(self.rationale, "verification rationale")
        if not isinstance(self.status, VerificationStatus):
            raise ValueError("invalid verification status")
        identifiers(self.evidence_ids, "verification evidence ids")
        if self.status == VerificationStatus.SUPPORTED and not self.evidence_ids:
            raise ValueError("supported verification requires evidence")


class ReportStatus(StrEnum):
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class ResearchBundle:
    sources: tuple[Source, ...] = ()
    evidence: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        records(self.sources, Source)
        records(self.evidence, Evidence)
        known = {source.id for source in self.sources}
        if any(item.source_id not in known for item in self.evidence):
            raise ValueError("evidence references an unknown source")
        by_id = {source.id: source for source in self.sources}
        for item in self.evidence:
            if item.extraction is not None and item.extraction.original_content_sha256 != by_id[item.source_id].provenance.content_sha256:
                raise ValueError("Extraction provenance does not match original source digest")


def records(values: tuple, kind: type) -> None:
    if not isinstance(values, tuple) or any(not isinstance(v, kind) for v in values):
        raise ValueError(f"expected an immutable tuple of {kind.__name__}")
    ids = tuple(getattr(v, "id", getattr(v, "claim_id", "")) for v in values)
    identifiers(ids, kind.__name__)


@dataclass(frozen=True)
class Report:
    question: ResearchQuestion
    summary: str
    bundle: ResearchBundle
    claims: tuple[Claim, ...]
    verification: tuple[VerificationResult, ...]
    status: ReportStatus

    @property
    def accepted_claims(self) -> tuple[Claim, ...]:
        """Final research claims; the full claims/verification fields remain the audit ledger."""
        supported = {result.claim_id for result in self.verification
                     if result.status == VerificationStatus.SUPPORTED}
        return tuple(claim for claim in self.claims if claim.id in supported)

    def __post_init__(self) -> None:
        if not isinstance(self.question, ResearchQuestion) or not isinstance(self.bundle, ResearchBundle):
            raise ValueError("report requires a question and research bundle")
        nonempty(self.summary, "report summary")
        records(self.claims, Claim)
        records(self.verification, VerificationResult)
        if not isinstance(self.status, ReportStatus):
            raise ValueError("invalid report status")
        validate_verification(self.claims, self.bundle, self.verification)
        expected = report_status(self.claims, self.verification)
        if self.status != expected:
            raise ValueError("report status does not match verification")


def validate_verification(
    claims: tuple[Claim, ...], bundle: ResearchBundle,
    results: tuple[VerificationResult, ...],
) -> None:
    records(claims, Claim)
    records(results, VerificationResult)
    by_id = {claim.id: claim for claim in claims}
    if set(by_id) != {result.claim_id for result in results}:
        raise ValueError("each claim requires exactly one verification result")
    available = {item.id for item in bundle.evidence}
    for claim in claims:
        if not set(claim.evidence_ids) <= available:
            raise ValueError("claim references unknown evidence")
    for result in results:
        claim = by_id[result.claim_id]
        if not set(result.evidence_ids) <= set(claim.evidence_ids):
            raise ValueError("verification must reference the claim's evidence")


def report_status(claims: tuple[Claim, ...], results: tuple[VerificationResult, ...]) -> ReportStatus:
    if claims and len(claims) == len(results) and all(
        result.status == VerificationStatus.SUPPORTED for result in results
    ):
        return ReportStatus.VERIFIED
    return ReportStatus.NEEDS_REVIEW
