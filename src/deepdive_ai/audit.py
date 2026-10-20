"""Metadata-only events: no prompts, excerpts, credentials or exception text."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from .models import aware, nonempty


class AuditKind(StrEnum):
    STARTED = "started"
    PLANNED = "planned"
    RESEARCHED = "researched"
    DRAFTED = "drafted"
    VERIFIED = "verified"
    RETRY = "retry"
    COMPLETED = "completed"
    FAILED = "failed"


class RoutingRejectionCode(StrEnum):
    FALLBACK_MISSING = "fallback_missing"
    FALLBACK_TRUE = "fallback_true"
    FALLBACK_INVALID = "fallback_invalid"
    ROUTING_MODEL_PRESENT = "routing_model_present"
    DUPLICATE_METADATA = "duplicate_metadata"
    INVALID_VERSION = "invalid_version"
    INVALID_ATTEMPTS = "invalid_attempts"
    INVALID_METADATA_VALUE = "invalid_metadata_value"
    CACHED_RESPONSE_UNVERIFIED = "cached_response_unverified"
    UNEXPECTED_STREAMING = "unexpected_streaming"
    INVALID_CACHE_METADATA = "invalid_cache_metadata"
    CONTRADICTORY_METADATA = "contradictory_metadata"


class MeshResponseClassification(StrEnum):
    NORMAL = "normal_non_streaming"
    CACHED = "cached_response"
    STREAMING = "unexpected_streaming"
    UNKNOWN = "unknown_response"
    INVALID = "invalid_metadata"


@dataclass(frozen=True)
class AuditEvent:
    run_id: str
    question_id: str
    kind: AuditKind
    attempt: int
    occurred_at: datetime
    routing_rejection_codes: tuple[RoutingRejectionCode, ...] = ()
    mesh_response_classification: MeshResponseClassification | None = None

    def __post_init__(self) -> None:
        nonempty(self.run_id, "run id")
        nonempty(self.question_id, "question id")
        if not isinstance(self.kind, AuditKind):
            raise ValueError("invalid audit kind")
        if type(self.attempt) is not int or self.attempt < 0:
            raise ValueError("attempt must be a nonnegative integer")
        aware(self.occurred_at)
        codes = self.routing_rejection_codes
        if (not isinstance(codes, tuple) or len(codes) > len(RoutingRejectionCode)
                or any(not isinstance(code, RoutingRejectionCode) for code in codes)
                or len(set(codes)) != len(codes)
                or (codes and self.kind != AuditKind.FAILED)):
            raise ValueError("Invalid routing audit codes")
        if self.mesh_response_classification is not None and (
                not isinstance(self.mesh_response_classification, MeshResponseClassification)
                or self.kind != AuditKind.FAILED):
            raise ValueError("Invalid Mesh response classification")


class AuditSink(Protocol):
    def emit(self, event: AuditEvent) -> None:
        """Persist an event; failures propagate rather than silently lose audit data."""
        ...
