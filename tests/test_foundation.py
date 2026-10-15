"""Offline fixtures describe test tokens, never real research findings."""

import hashlib
import importlib.util
import os
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from unittest.mock import patch

from deepdive_ai.audit import AuditEvent, AuditKind
from deepdive_ai.mesh import DisabledMeshClient, MeshRequest, MeshResponse, MeshSettings
from deepdive_ai.models import (
    Claim, Evidence, Report, ReportStatus, ResearchBundle, ResearchQuestion,
    ResearchTask, Source, SourceProvenance, VerificationResult, VerificationStatus,
    report_status, validate_verification,
)
from deepdive_ai.retrieval import DisabledWebRetriever, RetrievedDocument, SearchRequest
from deepdive_ai.workflow import WorkflowCoordinator, WorkflowPolicy


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
CONTENT = b"offline fixture token: alpha"
QUESTION = ResearchQuestion("q1", "Which token is in the offline fixture?")
TASK = ResearchTask("t1", "q1", "Read the supplied offline fixture")
SOURCE = Source(
    "s1", "https://fixture.invalid/document", "Offline fixture only",
    SourceProvenance(NOW, hashlib.sha256(CONTENT).hexdigest(), "unit-test fixture"),
)
EVIDENCE = Evidence("e1", "s1", "t1", CONTENT.decode(), "line 1")
BUNDLE = ResearchBundle((SOURCE,), (EVIDENCE,))
CLAIM = Claim("c1", "The offline fixture token is alpha", ("e1",))
SUPPORTED = VerificationResult("c1", VerificationStatus.SUPPORTED, "Matches fixture", ("e1",))
INSUFFICIENT = VerificationResult("c1", VerificationStatus.INSUFFICIENT, "More evidence required")


class ModelTests(unittest.TestCase):
    def test_blank_inputs_rejected(self):
        for build in (
            lambda: ResearchQuestion("q", " "),
            lambda: ResearchTask("", "q", "instruction"),
            lambda: Claim("c", " "),
            lambda: Evidence("e", "s", "t", "", "line 1"),
            lambda: SearchRequest(" "),
            lambda: MeshRequest("model", " "),
            lambda: MeshResponse("", "id"),
        ):
            with self.subTest(build=build), self.assertRaises(ValueError):
                build()

    def test_provenance_validation(self):
        for build in (
            lambda: replace(SOURCE.provenance, retrieved_at=datetime(2026, 1, 1)),
            lambda: replace(SOURCE.provenance, content_sha256="invalid"),
            lambda: replace(SOURCE.provenance, retrieval_method=""),
            lambda: replace(SOURCE, provenance=None),
        ):
            with self.subTest(build=build), self.assertRaises(ValueError):
                build()

    def test_source_urls_reject_unsafe_schemes_and_credentials(self):
        for url in ("file:///test", "https:///no-host", "https://user:password@example.invalid", "https://[", "https://host:bad"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                replace(SOURCE, url=url)

    def test_retrieved_bytes_must_match_digest(self):
        self.assertEqual(RetrievedDocument(SOURCE, CONTENT).content, CONTENT)
        with self.assertRaises(ValueError):
            RetrievedDocument(SOURCE, b"changed")

    def test_records_are_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            QUESTION.text = "changed"
        with self.assertRaises(ValueError):
            Claim("c", "text", ["e1"])

    def test_duplicate_and_dangling_evidence_rejected(self):
        for build in (
            lambda: ResearchBundle((SOURCE, SOURCE)),
            lambda: ResearchBundle((), (EVIDENCE,)),
            lambda: ResearchBundle((SOURCE,), (EVIDENCE, EVIDENCE)),
            lambda: Claim("c", "text", ("e", "e")),
        ):
            with self.subTest(build=build), self.assertRaises(ValueError):
                build()

    def test_support_requires_evidence(self):
        with self.assertRaises(ValueError):
            VerificationResult("c1", VerificationStatus.SUPPORTED, "No evidence")
        with self.assertRaises(ValueError):
            validate_verification((CLAIM,), ResearchBundle(), (SUPPORTED,))

    def test_verification_must_cover_each_claim_once(self):
        for results in ((), (SUPPORTED, SUPPORTED), (replace(SUPPORTED, claim_id="other"),)):
            with self.subTest(results=results), self.assertRaises(ValueError):
                validate_verification((CLAIM,), BUNDLE, results)

    def test_verifier_cannot_cite_evidence_outside_claim(self):
        with self.assertRaises(ValueError):
            validate_verification((replace(CLAIM, evidence_ids=()),), BUNDLE, (SUPPORTED,))

    def test_report_fails_closed_for_absent_claims_or_support(self):
        self.assertEqual(report_status((), ()), ReportStatus.NEEDS_REVIEW)
        self.assertEqual(report_status((CLAIM,), (INSUFFICIENT,)), ReportStatus.NEEDS_REVIEW)
        with self.assertRaises(ValueError):
            Report(QUESTION, "fixture summary", BUNDLE, (CLAIM,), (INSUFFICIENT,), ReportStatus.VERIFIED)
        with self.assertRaises(ValueError):
            Report(QUESTION, "empty", ResearchBundle(), (), (), ReportStatus.VERIFIED)

    def test_string_statuses_rejected(self):
        with self.assertRaises(ValueError):
            replace(SUPPORTED, status="supported")

    def test_limits_and_audit_validation(self):
        for build in (
            lambda: WorkflowPolicy(max_research_retries=-1),
            lambda: WorkflowPolicy(max_research_retries=6),
            lambda: WorkflowPolicy(max_research_retries=True),
            lambda: WorkflowPolicy(max_tasks=0),
            lambda: WorkflowPolicy(max_claims=0),
            lambda: SearchRequest("query", 21),
            lambda: MeshRequest("model", "prompt", 0),
            lambda: AuditEvent("r", "q", AuditKind.STARTED, -1, NOW),
            lambda: AuditEvent("r", "q", "started", 0, NOW),
        ):
            with self.subTest(build=build), self.assertRaises(ValueError):
                build()

    def test_mesh_reads_environment_and_redacts_repr(self):
        with patch.dict(os.environ, {"MESH_API_KEY": "unit-test-placeholder"}):
            settings = MeshSettings.from_environment()
        self.assertEqual(settings.api_key, "unit-test-placeholder")
        self.assertNotIn(settings.api_key, repr(settings))
        self.assertNotIn("private prompt", repr(MeshRequest("model", "private prompt")))
        with self.assertRaisesRegex(ValueError, "MESH_API_KEY must be configured"):
            MeshSettings.from_environment({})


class FixtureAgents:
    """Explicit deterministic test doubles; never used by production entrypoints."""

    def __init__(self):
        self.calls = []
        self.events = []
        self.feedback = []
        self.tasks = (TASK,)
        self.bundle = BUNDLE
        self.claims = (CLAIM,)
        self.results = [(SUPPORTED,)]
        self.verify_count = 0
        self.change_report = False

    def emit(self, event):
        self.events.append(event)

    async def plan(self, question):
        self.calls.append("plan")
        return self.tasks

    async def research(self, question, tasks, previous, feedback):
        self.calls.append("research")
        self.feedback.append(feedback)
        return self.bundle

    async def draft(self, question, bundle):
        self.calls.append("draft")
        return self.claims

    async def verify(self, claims, bundle):
        self.calls.append("verify")
        result = self.results[min(self.verify_count, len(self.results) - 1)]
        self.verify_count += 1
        return result

    async def synthesize(self, question, bundle, claims, verification):
        self.calls.append("synthesize")
        if self.change_report:
            claims = (replace(CLAIM, text="unauthorized change"),)
        return Report(question, "Offline fixture summary", bundle, claims, verification,
                      report_status(claims, verification))

    def coordinator(self, **policy):
        return WorkflowCoordinator(self, self, self, self, self, self,
                                   WorkflowPolicy(**policy), lambda: NOW)


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_sequence_and_audit(self):
        agents = FixtureAgents()
        report = await agents.coordinator().run(QUESTION, run_id="run1")
        self.assertEqual(report.status, ReportStatus.VERIFIED)
        self.assertEqual(agents.calls, ["plan", "research", "draft", "verify", "synthesize"])
        self.assertEqual([e.kind for e in agents.events], [
            AuditKind.STARTED, AuditKind.PLANNED, AuditKind.RESEARCHED,
            AuditKind.DRAFTED, AuditKind.VERIFIED, AuditKind.COMPLETED,
        ])
        self.assertTrue(all(e.occurred_at == NOW and e.run_id == "run1" for e in agents.events))

    async def test_insufficient_evidence_retries_with_feedback(self):
        agents = FixtureAgents()
        agents.results = [(INSUFFICIENT,), (SUPPORTED,)]
        report = await agents.coordinator().run(QUESTION, run_id="run1")
        self.assertEqual(report.status, ReportStatus.VERIFIED)
        self.assertEqual(agents.feedback, [(), (INSUFFICIENT,)])
        self.assertEqual(agents.calls.count("research"), 2)
        self.assertEqual(agents.calls.count("plan"), 1)
        self.assertEqual([e.attempt for e in agents.events if e.kind == AuditKind.RETRY], [0])

    async def test_retry_exhaustion_returns_needs_review(self):
        agents = FixtureAgents()
        agents.results = [(INSUFFICIENT,)]
        report = await agents.coordinator(max_research_retries=2).run(QUESTION, run_id="run1")
        self.assertEqual(agents.calls.count("research"), 3)
        self.assertEqual(report.status, ReportStatus.NEEDS_REVIEW)
        self.assertEqual(report.verification, (INSUFFICIENT,))

    async def test_zero_retries(self):
        agents = FixtureAgents()
        agents.results = [(INSUFFICIENT,)]
        await agents.coordinator(max_research_retries=0).run(QUESTION, run_id="run1")
        self.assertEqual(agents.calls.count("research"), 1)

    async def test_empty_research_and_draft_never_verified(self):
        agents = FixtureAgents()
        agents.bundle, agents.claims, agents.results = ResearchBundle(), (), [()]
        report = await agents.coordinator().run(QUESTION, run_id="run1")
        self.assertEqual(report.status, ReportStatus.NEEDS_REVIEW)
        self.assertEqual(agents.calls.count("research"), 2)

    async def test_unsupported_claim_does_not_trigger_evidence_retry(self):
        agents = FixtureAgents()
        agents.results = [(replace(INSUFFICIENT, status=VerificationStatus.UNSUPPORTED),)]
        report = await agents.coordinator().run(QUESTION, run_id="run1")
        self.assertEqual(report.status, ReportStatus.NEEDS_REVIEW)
        self.assertEqual(agents.calls.count("research"), 1)

    async def test_missing_support_rejected_before_synthesis(self):
        agents = FixtureAgents()
        agents.bundle = ResearchBundle()
        with self.assertRaises(ValueError):
            await agents.coordinator().run(QUESTION, run_id="run1")
        self.assertNotIn("synthesize", agents.calls)
        self.assertEqual(agents.events[-1].kind, AuditKind.FAILED)

    async def test_missing_verifier_output_rejected(self):
        agents = FixtureAgents()
        agents.results = [()]
        with self.assertRaises(ValueError):
            await agents.coordinator().run(QUESTION, run_id="run1")

    async def test_uncited_claim_remains_unverified_without_sources(self):
        agents = FixtureAgents()
        agents.bundle = ResearchBundle()
        agents.claims = (replace(CLAIM, evidence_ids=()),)
        agents.results = [(INSUFFICIENT,)]
        report = await agents.coordinator().run(QUESTION, run_id="run1")
        self.assertEqual(report.status, ReportStatus.NEEDS_REVIEW)
        self.assertEqual(report.bundle.sources, ())
        self.assertEqual(agents.calls.count("research"), 2)

    async def test_invalid_task_plans_rejected(self):
        for tasks in ((), (TASK, TASK), (replace(TASK, question_id="other"),)):
            agents = FixtureAgents()
            agents.tasks = tasks
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                await agents.coordinator().run(QUESTION, run_id="run1")
            self.assertNotIn("research", agents.calls)

    async def test_task_and_claim_limits(self):
        for field in ("tasks", "claims"):
            agents = FixtureAgents()
            if field == "tasks":
                agents.tasks = (TASK, replace(TASK, id="t2"))
            else:
                agents.claims = (CLAIM, replace(CLAIM, id="c2"))
            with self.subTest(field=field), self.assertRaises(ValueError):
                await agents.coordinator(max_tasks=1, max_claims=1).run(QUESTION, run_id="run1")

    async def test_evidence_must_belong_to_planned_task(self):
        agents = FixtureAgents()
        agents.bundle = ResearchBundle((SOURCE,), (replace(EVIDENCE, task_id="other"),))
        with self.assertRaises(ValueError):
            await agents.coordinator().run(QUESTION, run_id="run1")

    async def test_retry_cannot_rewrite_or_drop_provenance(self):
        for changed in (ResearchBundle(), ResearchBundle((replace(SOURCE, title="changed"),), (EVIDENCE,))):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                WorkflowCoordinator._validate_update(BUNDLE, changed, (TASK,))

    async def test_synthesizer_cannot_rewrite_claims(self):
        agents = FixtureAgents()
        agents.change_report = True
        with self.assertRaisesRegex(ValueError, "synthesizer must preserve"):
            await agents.coordinator().run(QUESTION, run_id="run1")
        self.assertEqual(agents.events[-1].kind, AuditKind.FAILED)

    async def test_retry_accepts_additional_provenance_and_evidence(self):
        additional_source = replace(SOURCE, id="s2", url="https://fixture.invalid/second")
        additional_evidence = replace(EVIDENCE, id="e2", source_id="s2")
        updated = ResearchBundle((SOURCE, additional_source), (EVIDENCE, additional_evidence))
        WorkflowCoordinator._validate_update(BUNDLE, updated, (TASK,))

    async def test_failure_audit_has_no_exception_details(self):
        agents = FixtureAgents()

        async def fail(question):
            raise RuntimeError("private diagnostic fixture")

        agents.plan = fail
        with self.assertRaises(RuntimeError):
            await agents.coordinator().run(QUESTION, run_id="run1")
        self.assertNotIn("private diagnostic fixture", repr(agents.events))
        self.assertEqual(agents.events[-1].kind, AuditKind.FAILED)

    async def test_audit_sink_failure_stops_work(self):
        agents = FixtureAgents()

        def fail(event):
            raise RuntimeError("audit unavailable")

        agents.emit = fail
        with self.assertRaises(RuntimeError):
            await agents.coordinator().run(QUESTION, run_id="run1")
        self.assertEqual(agents.calls, [])

    async def test_external_adapters_are_disabled(self):
        for call in (
            lambda: DisabledMeshClient().complete(MeshRequest("unconfigured", "fixture")),
            lambda: DisabledWebRetriever().search(SearchRequest("fixture")),
            lambda: DisabledWebRetriever().fetch("https://fixture.invalid"),
        ):
            with self.subTest(call=call), self.assertRaisesRegex(RuntimeError, "disabled in Phase 1"):
                await call()


class BackendTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("fastapi"), "optional FastAPI backend dependency is not installed")
    def test_health_endpoint(self):
        from deepdive_ai.api import create_app

        app = create_app()
        route = next(route for route in app.routes if route.path == "/health")
        self.assertEqual(route.endpoint(), {"status": "ok", "phase": "foundation"})


if __name__ == "__main__":
    unittest.main()
