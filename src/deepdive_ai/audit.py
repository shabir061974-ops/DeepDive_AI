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


@dataclass(frozen=True)
class AuditEvent:
    run_id: str
    question_id: str
    kind: AuditKind
    attempt: int
    occurred_at: datetime

    def __post_init__(self) -> None:
        nonempty(self.run_id, "run id")
        nonempty(self.question_id, "question id")
        if not isinstance(self.kind, AuditKind):
            raise ValueError("invalid audit kind")
        if type(self.attempt) is not int or self.attempt < 0:
            raise ValueError("attempt must be a nonnegative integer")
        aware(self.occurred_at)


class AuditSink(Protocol):
    def emit(self, event: AuditEvent) -> None:
        """Persist an event; failures propagate rather than silently lose audit data."""
        ...
