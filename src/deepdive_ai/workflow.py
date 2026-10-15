"""Dependency-injected orchestration with bounded, fail-closed verification."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .agents import DrafterAgent, PlannerAgent, ResearcherAgent, SynthesizerAgent, VerifierAgent
from .audit import AuditEvent, AuditKind, AuditSink
from .models import (
    Claim, Report, ResearchBundle, ResearchQuestion, ResearchTask,
    VerificationResult, VerificationStatus, nonempty, records, validate_verification,
)


@dataclass(frozen=True)
class WorkflowPolicy:
    max_research_retries: int = 1
    max_tasks: int = 20
    max_claims: int = 100

    def __post_init__(self) -> None:
        if type(self.max_research_retries) is not int or not 0 <= self.max_research_retries <= 5:
            raise ValueError("max_research_retries must be between 0 and 5")
        for value in (self.max_tasks, self.max_claims):
            if type(value) is not int or value < 1:
                raise ValueError("task and claim limits must be positive integers")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowCoordinator:
    def __init__(
        self, planner: PlannerAgent, researcher: ResearcherAgent,
        drafter: DrafterAgent, verifier: VerifierAgent,
        synthesizer: SynthesizerAgent, audit: AuditSink,
        policy: WorkflowPolicy = WorkflowPolicy(),
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.planner = planner
        self.researcher = researcher
        self.drafter = drafter
        self.verifier = verifier
        self.synthesizer = synthesizer
        self.audit = audit
        self.policy = policy
        self.clock = clock

    async def run(self, question: ResearchQuestion, *, run_id: str) -> Report:
        nonempty(run_id, "run id")
        if not isinstance(question, ResearchQuestion):
            raise ValueError("question must be a ResearchQuestion")
        attempt = 0

        def emit(kind: AuditKind) -> None:
            self.audit.emit(AuditEvent(run_id, question.id, kind, attempt, self.clock()))

        emit(AuditKind.STARTED)
        try:
            tasks = await self.planner.plan(question)
            records(tasks, ResearchTask)
            if not tasks or len(tasks) > self.policy.max_tasks:
                raise ValueError("planner must return tasks within the configured limit")
            if any(task.question_id != question.id for task in tasks):
                raise ValueError("task belongs to a different question")
            emit(AuditKind.PLANNED)
            bundle = ResearchBundle()
            feedback: tuple[VerificationResult, ...] = ()
            for attempt in range(self.policy.max_research_retries + 1):
                updated = await self.researcher.research(question, tasks, bundle, feedback)
                if not isinstance(updated, ResearchBundle):
                    raise ValueError("researcher must return a ResearchBundle")
                self._validate_update(bundle, updated, tasks)
                bundle = updated
                emit(AuditKind.RESEARCHED)
                claims = await self.drafter.draft(question, bundle)
                records(claims, Claim)
                if len(claims) > self.policy.max_claims:
                    raise ValueError("draft exceeds configured claim limit")
                emit(AuditKind.DRAFTED)
                results = await self.verifier.verify(claims, bundle)
                validate_verification(claims, bundle, results)
                emit(AuditKind.VERIFIED)
                feedback = tuple(r for r in results if r.status == VerificationStatus.INSUFFICIENT)
                if (claims and not feedback) or attempt == self.policy.max_research_retries:
                    break
                emit(AuditKind.RETRY)
            report = await self.synthesizer.synthesize(question, bundle, claims, results)
            if not isinstance(report, Report) or (
                report.question != question or report.bundle != bundle
                or report.claims != claims or report.verification != results
            ):
                raise ValueError("synthesizer must preserve supplied research and verification")
            emit(AuditKind.COMPLETED)
            return report
        except Exception:
            emit(AuditKind.FAILED)
            raise

    @staticmethod
    def _validate_update(
        previous: ResearchBundle, updated: ResearchBundle, tasks: tuple[ResearchTask, ...],
    ) -> None:
        for old, new in ((previous.sources, updated.sources), (previous.evidence, updated.evidence)):
            by_id = {item.id: item for item in new}
            if any(by_id.get(item.id) != item for item in old):
                raise ValueError("research retries must preserve previous provenance and evidence")
        task_ids = {task.id for task in tasks}
        if any(item.task_id not in task_ids for item in updated.evidence):
            raise ValueError("evidence references an unknown research task")
