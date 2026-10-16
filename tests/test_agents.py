"""Controlled offline research fixtures; no findings here describe the real world."""

import asyncio
import hashlib
import json
import traceback
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from deepdive_ai.agents.common import AgentBudget, AgentError, AgentLimitError, AgentLimits, AgentOutputError, MeshJSON
from deepdive_ai.agents.drafter import MeshDrafterAgent
from deepdive_ai.agents.planner import MeshPlannerAgent
from deepdive_ai.agents.researcher import RetrievalResearcherAgent
from deepdive_ai.agents.synthesizer import VerifiedClaimsSynthesizerAgent
from deepdive_ai.agents.verifier import MeshVerifierAgent
from deepdive_ai.audit import AuditKind
from deepdive_ai.discovery import DiscoveredSource
from deepdive_ai.http_transport import HTTPResponse, RequestPolicy, _PinnedConnection
from deepdive_ai.mesh import MeshResponse
from deepdive_ai.models import (
    Claim, Evidence, ReportStatus, ResearchBundle, ResearchQuestion, ResearchTask,
    Source, SourceProvenance, VerificationResult, VerificationStatus,
)
from deepdive_ai.retrieval import HTTPWebRetriever, RetrievalSettings
from deepdive_ai.runtime import build_research_workflow
from deepdive_ai.workflow import WorkflowPolicy


NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
URL = "https://fixture.invalid/a"
OTHER_URL = "https://fixture.invalid/b"
BODY = b"Offline fixture A records 12 tokens."
COUNTER = b"Offline fixture A records 7 tokens, not 12."
STATEMENT = BODY.decode()
QUESTION = ResearchQuestion("q", "How many tokens does offline fixture A record?")
TASK = ResearchTask("q:task:1", "q", "Find the token count in the offline fixture")
SOURCE = Source("s", URL, "Offline fixture", SourceProvenance(NOW, hashlib.sha256(BODY).hexdigest(), "test fixture"))
EVIDENCE = Evidence("e", "s", TASK.id, STATEMENT, f"bytes 0:{len(BODY)} (UTF-8)")
BUNDLE = ResearchBundle((SOURCE,), (EVIDENCE,))
CLAIM = Claim("q:claim:1", STATEMENT, ("e",))


class FakeMesh:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def complete(self, request):
        payload = json.loads(request.prompt)
        self.calls.append(payload)
        if not self.outcomes:
            raise AssertionError("unexpected Mesh invocation")
        result = self.outcomes.pop(0)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            result = result(payload)
        return MeshResponse(result if isinstance(result, str) else json.dumps(result), "offline-request")


def plan():
    return {"tasks": [{"instruction": TASK.instruction}]}


def draft(payload):
    return {"claims": [{"text": STATEMENT, "evidence_ids": [payload["data"]["evidence"][0]["id"]]}]}


def assessment(payload, relations=None, verdict="supported"):
    rows = []
    for index, item in enumerate(payload["data"]["evidence"]):
        relation = relations[index] if relations else "supports"
        rows.append({"evidence_id": item["id"], "relation": relation,
                     "quote": item["excerpt"] if relation in {"supports", "contradicts"} else "",
                     "rationale": "Controlled offline semantic assessment"})
    return {"claim_id": payload["data"]["claim"]["id"], "verdict": verdict,
            "rationale": "Controlled offline verdict", "assessments": rows}


class FakeDiscovery:
    def __init__(self, *batches):
        self.batches = list(batches)
        self.calls = []

    async def discover(self, request):
        self.calls.append(request)
        batch = self.batches.pop(0) if self.batches else ()
        if isinstance(batch, BaseException):
            raise batch
        return tuple(DiscoveredSource(url) for url in batch)


class FixtureTransport:
    def __init__(self, bodies=None, status=200, media_type="text/plain"):
        self.bodies = bodies or {URL: BODY, OTHER_URL: COUNTER}
        self.status = status
        self.media_type = media_type
        self.calls = []

    async def send(self, request):
        self.calls.append(request)
        body = self.bodies[request.url]
        return HTTPResponse(self.status, (("content-type", self.media_type),), body)


class Audit:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class OfflineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dns = patch("socket.getaddrinfo", side_effect=AssertionError("real DNS forbidden"))
        self.connect = patch.object(_PinnedConnection, "connect", side_effect=AssertionError("real HTTP forbidden"))
        self.dns.start()
        self.connect.start()
        self.addCleanup(self.dns.stop)
        self.addCleanup(self.connect.stop)

    def llm(self, mesh, **limits):
        return MeshJSON(mesh, "offline-configured-model", AgentLimits(**limits))

    def retriever(self, transport=None):
        return HTTPWebRetriever(RetrievalSettings(policy=RequestPolicy(max_retries=0)),
                                transport or FixtureTransport(), lambda: NOW)

    def workflow(self, mesh, discovery=None, transport=None, limits=AgentLimits(), retries=0):
        audit = Audit()
        workflow = build_research_workflow(mesh=mesh, model="offline-configured-model",
            discovery=discovery or FakeDiscovery((URL,)), retriever=self.retriever(transport),
            audit=audit, limits=limits, policy=WorkflowPolicy(max_research_retries=retries), clock=lambda: NOW)
        return workflow, audit


class PlannerDrafterTests(OfflineTests):
    async def test_planner_assigns_ids_and_binds_question(self):
        mesh = FakeMesh(plan())
        result = await MeshPlannerAgent(self.llm(mesh)).plan(QUESTION)
        self.assertEqual(result, (TASK,))
        self.assertEqual(mesh.calls[0]["role"], "planner")

    async def test_planner_rejects_empty_excess_duplicate_and_fabricated_fields(self):
        outputs = [{"tasks": []}, {"tasks": [{"instruction": "x"}, {"instruction": "y"}]},
                   {"tasks": [{"instruction": "x"}, {"instruction": "x"}]},
                   {"tasks": [{"instruction": "x", "url": URL}]}, {"tasks": "not a list"}]
        for output in outputs:
            with self.subTest(output=output), self.assertRaises(AgentOutputError):
                await MeshPlannerAgent(self.llm(FakeMesh(output), max_tasks=1)).plan(QUESTION)

    async def test_strict_json_rejects_invalid_fenced_schema_duplicates_nonfinite_and_wrong_root(self):
        for output in ('```json\n{}\n```', '{"tasks":[],"tasks":[]}', '{"tasks":NaN}', 'null', '[]', 'not JSON'):
            with self.subTest(output=output), self.assertRaises(AgentOutputError):
                await MeshPlannerAgent(self.llm(FakeMesh(output))).plan(QUESTION)

    async def test_drafter_uses_only_retrieved_evidence_ids(self):
        mesh = FakeMesh(draft)
        claims = await MeshDrafterAgent(self.llm(mesh)).draft(QUESTION, BUNDLE)
        self.assertEqual(claims, (CLAIM,))
        self.assertEqual(mesh.calls[0]["data"]["evidence"][0]["excerpt"], EVIDENCE.excerpt)

    async def test_drafter_rejects_unknown_missing_duplicate_and_embedded_citations(self):
        rows = [{"text": STATEMENT, "evidence_ids": ids} for ids in ([], ["invented"], ["e", "e"], [None])]
        rows += [{"text": "Claim with https://invented.invalid", "evidence_ids": ["e"]},
                 {"text": " ", "evidence_ids": ["e"]}, {"text": STATEMENT}]
        for row in rows:
            with self.subTest(row=row), self.assertRaises(AgentOutputError):
                await MeshDrafterAgent(self.llm(FakeMesh({"claims": [row]}))).draft(QUESTION, BUNDLE)

    async def test_empty_bundle_never_generates_claims_or_calls_mesh(self):
        mesh = FakeMesh()
        self.assertEqual(await MeshDrafterAgent(self.llm(mesh)).draft(QUESTION, ResearchBundle()), ())
        self.assertEqual(mesh.calls, [])


class ResearcherTests(OfflineTests):
    async def test_discovery_precedes_fetch_and_preserves_exact_provenance(self):
        discovery = FakeDiscovery((URL,))
        transport = FixtureTransport()
        researcher = RetrievalResearcherAgent(discovery, self.retriever(transport))
        result = await researcher.research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assertEqual(len(discovery.calls), 1)
        self.assertIn(QUESTION.text, discovery.calls[0].query)
        self.assertEqual(len(transport.calls), 1)
        item = result.evidence[0]
        self.assertEqual(item.excerpt.encode(), BODY)
        self.assertEqual(item.task_id, TASK.id)
        self.assertEqual(item.source_id, result.sources[0].id)
        self.assertEqual(result.sources[0].provenance.content_sha256, hashlib.sha256(BODY).hexdigest())
        self.assertEqual(result.sources[0].provenance.retrieved_at, NOW)

    async def test_retry_preserves_old_records_and_adds_discovered_counter_evidence(self):
        discovery = FakeDiscovery((URL,), (OTHER_URL,))
        researcher = RetrievalResearcherAgent(discovery, self.retriever())
        previous = await researcher.research(QUESTION, (TASK,), ResearchBundle(), ())
        feedback = (VerificationResult("c", VerificationStatus.INSUFFICIENT, "Evidence gap"),)
        result = await researcher.research(QUESTION, (TASK,), previous, feedback)
        self.assertEqual(result.sources[:1], previous.sources)
        self.assertEqual(result.evidence[:1], previous.evidence)
        self.assertEqual(len(result.sources), 2)
        self.assertIn("counter-evidence", discovery.calls[-1].query)

    async def test_repeat_content_retains_original_records_despite_new_timestamp(self):
        discovery = FakeDiscovery((URL,), (URL,))
        retriever = self.retriever()
        researcher = RetrievalResearcherAgent(discovery, retriever)
        first = await researcher.research(QUESTION, (TASK,), ResearchBundle(), ())
        retriever._clock = lambda: datetime(2026, 10, 16, tzinfo=timezone.utc)
        second = await researcher.research(QUESTION, (TASK,), first, ())
        self.assertEqual(second, first)

    async def test_discovery_is_required_and_empty_search_does_not_fetch(self):
        transport = FixtureTransport()
        researcher = RetrievalResearcherAgent(FakeDiscovery(()), self.retriever(transport))
        self.assertEqual(await researcher.research(QUESTION, (TASK,), ResearchBundle(), ()), ResearchBundle())
        self.assertEqual(transport.calls, [])

    async def test_duplicate_candidates_are_not_fetched_twice_per_task(self):
        transport = FixtureTransport()
        researcher = RetrievalResearcherAgent(FakeDiscovery((URL, URL)), self.retriever(transport))
        result = await researcher.research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(result.evidence), 1)

    async def test_discovery_failure_and_excess_candidates_fail_safely(self):
        for discovery in (FakeDiscovery(RuntimeError("private provider detail")), FakeDiscovery((URL, OTHER_URL))):
            researcher = RetrievalResearcherAgent(discovery, self.retriever(), AgentLimits(discovery_results_per_task=1))
            with self.assertRaises(AgentError) as caught:
                await researcher.research(QUESTION, (TASK,), ResearchBundle(), ())
            self.assertNotIn("private provider detail", "".join(traceback.format_exception(caught.exception)))

    async def test_invalid_source_content_and_http_failure_abort(self):
        transports = [FixtureTransport(status=403), FixtureTransport(media_type="application/pdf"),
                      FixtureTransport({URL: b"\xff"}), FixtureTransport({URL: b"x" * 10})]
        for index, transport in enumerate(transports):
            with self.subTest(transport=transport), self.assertRaises(AgentError):
                researcher = RetrievalResearcherAgent(FakeDiscovery((URL,)), self.retriever(transport),
                                                      AgentLimits(max_document_bytes=5 if index == 3 else 2_000_000))
                await researcher.research(QUESTION, (TASK,), ResearchBundle(), ())

    async def test_utf8_chunks_preserve_byte_boundaries(self):
        body = "ééééé".encode()
        researcher = RetrievalResearcherAgent(FakeDiscovery((URL,)), self.retriever(FixtureTransport({URL: body})),
                                              AgentLimits(chunk_bytes=5, chunks_per_document=2))
        result = await researcher.research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assertEqual([e.excerpt for e in result.evidence], ["éé", "éé"])
        self.assertEqual([e.locator for e in result.evidence], ["bytes 0:4 (UTF-8)", "bytes 4:8 (UTF-8)"])

    async def test_call_and_bundle_limits_are_enforced(self):
        for limits in (AgentLimits(max_fetch_calls=1), AgentLimits(max_discovery_calls=1), AgentLimits(max_sources=1)):
            researcher = RetrievalResearcherAgent(FakeDiscovery((URL,), (OTHER_URL,)), self.retriever(), limits)
            previous = await researcher.research(QUESTION, (TASK,), ResearchBundle(), ())
            with self.subTest(limits=limits), self.assertRaises(AgentLimitError):
                await researcher.research(QUESTION, (TASK,), previous, ())

    async def test_private_discovery_url_rejected_before_fetch(self):
        transport = FixtureTransport()
        researcher = RetrievalResearcherAgent(FakeDiscovery(("https://127.0.0.1/",)), self.retriever(transport))
        with self.assertRaises(AgentError):
            await researcher.research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assertEqual(transport.calls, [])


class VerifierTests(OfflineTests):
    async def test_semantic_support_requires_grounded_assessment(self):
        mesh = FakeMesh(assessment)
        results = await MeshVerifierAgent(self.llm(mesh)).verify((CLAIM,), BUNDLE)
        self.assertEqual(results[0].status, VerificationStatus.SUPPORTED)
        self.assertEqual(results[0].evidence_ids, ("e",))
        self.assertIn("assessments", json.loads(results[0].rationale))
        self.assertIn("lexical overlap", mesh.calls[0]["instruction"])

    async def test_citation_presence_or_irrelevance_is_not_verification(self):
        for relation in ("irrelevant", "insufficient"):
            mesh = FakeMesh(lambda p: assessment(p, [relation]))
            result = await MeshVerifierAgent(self.llm(mesh)).verify((CLAIM,), BUNDLE)
            self.assertEqual(result[0].status, VerificationStatus.INSUFFICIENT)

    async def test_direct_contradiction_is_unsupported(self):
        mesh = FakeMesh(lambda p: assessment(p, ["contradicts"], "unsupported"))
        result = await MeshVerifierAgent(self.llm(mesh)).verify((CLAIM,), BUNDLE)
        self.assertEqual(result[0].status, VerificationStatus.UNSUPPORTED)

    async def test_uncited_counter_evidence_vetoes_supported_verdict(self):
        other_source = replace(SOURCE, id="s2", url=OTHER_URL,
                               provenance=replace(SOURCE.provenance, content_sha256=hashlib.sha256(COUNTER).hexdigest()))
        other_evidence = replace(EVIDENCE, id="e2", source_id="s2", excerpt=COUNTER.decode())
        bundle = ResearchBundle((SOURCE, other_source), (EVIDENCE, other_evidence))
        mesh = FakeMesh(lambda p: assessment(p, ["supports", "contradicts"], "supported"))
        result = await MeshVerifierAgent(self.llm(mesh)).verify((CLAIM,), bundle)
        self.assertEqual(result[0].status, VerificationStatus.INSUFFICIENT)
        self.assertIn("e2", result[0].rationale)

    async def test_missing_evidence_never_calls_model_or_becomes_supported(self):
        mesh = FakeMesh()
        result = await MeshVerifierAgent(self.llm(mesh)).verify((replace(CLAIM, evidence_ids=()),), ResearchBundle())
        self.assertEqual(result[0].status, VerificationStatus.INSUFFICIENT)
        self.assertEqual(mesh.calls, [])
        with self.assertRaises(AgentOutputError):
            await MeshVerifierAgent(self.llm(mesh)).verify((CLAIM,), ResearchBundle())

    async def test_unknown_missing_duplicate_assessments_and_fabricated_quotes_fail(self):
        def malformed(kind):
            def generate(payload):
                result = assessment(payload)
                if kind == "unknown":
                    result["assessments"][0]["evidence_id"] = "invented"
                elif kind == "missing":
                    result["assessments"] = []
                elif kind == "duplicate":
                    result["assessments"] *= 2
                elif kind == "quote":
                    result["assessments"][0]["quote"] = "invented supporting quotation"
                elif kind == "empty quote":
                    result["assessments"][0]["quote"] = ""
                elif kind == "wrong claim":
                    result["claim_id"] = "other"
                else:
                    result["verdict"] = []
                return result
            return generate
        for kind in ("unknown", "missing", "duplicate", "quote", "empty quote", "wrong claim", "bad verdict"):
            with self.subTest(kind=kind), self.assertRaises(AgentOutputError):
                await MeshVerifierAgent(self.llm(FakeMesh(malformed(kind)))).verify((CLAIM,), BUNDLE)

    async def test_unsupported_without_counter_evidence_is_uncertain(self):
        result = await MeshVerifierAgent(self.llm(FakeMesh(
            lambda p: assessment(p, ["insufficient"], "unsupported")))).verify((CLAIM,), BUNDLE)
        self.assertEqual(result[0].status, VerificationStatus.INSUFFICIENT)


class WorkflowTests(OfflineTests):
    async def test_five_concrete_agents_end_to_end(self):
        mesh = FakeMesh(plan(), draft, assessment)
        workflow, audit = self.workflow(mesh)
        report = await workflow.run(QUESTION, run_id="offline-run")
        self.assertEqual(report.status, ReportStatus.VERIFIED)
        self.assertEqual([call["role"] for call in mesh.calls], ["planner", "drafter", "verifier"])
        accepted = json.loads(report.summary)["accepted_claims"]
        self.assertEqual(accepted[0]["text"], STATEMENT)
        self.assertEqual(accepted[0]["citations"][0]["url"], URL)
        self.assertEqual(report.accepted_claims, report.claims)
        self.assertEqual(audit.events[-1].kind, AuditKind.COMPLETED)
        self.assertTrue(workflow.policy.require_verified_summary)

    async def test_unsupported_claims_are_retained_only_in_audit_ledger(self):
        mesh = FakeMesh(plan(), draft, lambda p: assessment(p, ["contradicts"], "unsupported"))
        workflow, _ = self.workflow(mesh)
        report = await workflow.run(QUESTION, run_id="offline-run")
        self.assertEqual(report.status, ReportStatus.NEEDS_REVIEW)
        self.assertEqual(report.accepted_claims, ())
        self.assertEqual(json.loads(report.summary), {"accepted_claims": []})
        self.assertEqual(len(report.claims), 1)
        self.assertEqual(report.verification[0].status, VerificationStatus.UNSUPPORTED)

    async def test_contradiction_retry_exhaustion_never_emits_accepted_claim(self):
        responses = [plan(), draft, lambda p: assessment(p, ["insufficient"], "insufficient_evidence"),
                     draft, lambda p: assessment(p, ["supports", "contradicts"], "supported")]
        workflow, audit = self.workflow(FakeMesh(*responses), FakeDiscovery((URL,), (OTHER_URL,)), retries=1)
        report = await workflow.run(QUESTION, run_id="offline-run")
        self.assertEqual(report.status, ReportStatus.NEEDS_REVIEW)
        self.assertEqual(report.accepted_claims, ())
        self.assertEqual(len(report.bundle.evidence), 2)
        self.assertEqual(sum(event.kind == AuditKind.RETRY for event in audit.events), 1)

    async def test_retry_can_resolve_evidence_gap_without_losing_provenance(self):
        mesh = FakeMesh(plan(), draft,
            lambda p: assessment(p, ["insufficient"], "insufficient_evidence"),
            draft, lambda p: assessment(p, ["supports", "supports"], "supported"))
        transport = FixtureTransport({URL: BODY, OTHER_URL: BODY})
        workflow, audit = self.workflow(mesh, FakeDiscovery((URL,), (OTHER_URL,)), transport, retries=1)
        report = await workflow.run(QUESTION, run_id="offline-run")
        self.assertEqual(report.status, ReportStatus.VERIFIED)
        self.assertEqual(len(report.bundle.sources), 2)
        self.assertEqual(report.bundle.sources[0].provenance.retrieved_at, NOW)
        self.assertEqual(len(report.accepted_claims), 1)
        self.assertEqual(sum(event.kind == AuditKind.RETRY for event in audit.events), 1)

    async def test_empty_discovery_retries_boundedly_and_has_no_research_answer(self):
        mesh = FakeMesh(plan())
        discovery = FakeDiscovery((), ())
        workflow, audit = self.workflow(mesh, discovery, retries=1)
        report = await workflow.run(QUESTION, run_id="offline-run")
        self.assertEqual(report.status, ReportStatus.NEEDS_REVIEW)
        self.assertEqual(report.accepted_claims, ())
        self.assertEqual(len(discovery.calls), 2)
        self.assertEqual(len(mesh.calls), 1)

    async def test_model_failure_and_malformed_output_emit_failed_audit_without_body(self):
        for bad in (RuntimeError("private API detail"), "sensitive malformed response"):
            workflow, audit = self.workflow(FakeMesh(plan(), draft, bad))
            with self.subTest(bad=type(bad)), self.assertRaises(AgentError) as caught:
                await workflow.run(QUESTION, run_id="offline-run")
            diagnostic = "".join(traceback.format_exception(caught.exception))
            self.assertNotIn("private API detail", diagnostic)
            self.assertNotIn("sensitive malformed response", diagnostic)
            self.assertEqual(audit.events[-1].kind, AuditKind.FAILED)
            self.assertNotIn(STATEMENT, repr(audit.events))

    async def test_summary_tampering_is_rejected_even_when_claim_records_match(self):
        workflow, audit = self.workflow(FakeMesh(plan(), draft, assessment))
        original = workflow.synthesizer

        class TamperingSynthesizer:
            async def synthesize(self, *args):
                report = await original.synthesize(*args)
                return replace(report, summary="The fixture records 999 tokens.")

        workflow.synthesizer = TamperingSynthesizer()
        with self.assertRaisesRegex(ValueError, "exact verified claim text"):
            await workflow.run(QUESTION, run_id="offline-run")
        self.assertEqual(audit.events[-1].kind, AuditKind.FAILED)

    async def test_shared_model_budget_covers_all_agents(self):
        workflow, audit = self.workflow(FakeMesh(plan(), draft, assessment), limits=AgentLimits(max_model_calls=2))
        with self.assertRaises(AgentLimitError):
            await workflow.run(QUESTION, run_id="offline-run")
        self.assertEqual(audit.events[-1].kind, AuditKind.FAILED)

    async def test_mixed_report_contains_only_exact_supported_claim_in_final_summary(self):
        accepted = VerificationResult(CLAIM.id, VerificationStatus.SUPPORTED, "Grounded fixture assessment", ("e",))
        rejected = replace(CLAIM, id="rejected", text="Unverified fixture assertion")
        result = VerificationResult(rejected.id, VerificationStatus.INSUFFICIENT, "No entailment", ("e",))
        report = await VerifiedClaimsSynthesizerAgent().synthesize(QUESTION, BUNDLE, (CLAIM, rejected), (accepted, result))
        self.assertEqual(report.accepted_claims, (CLAIM,))
        self.assertEqual(report.claims, (CLAIM, rejected))
        self.assertEqual(report.status, ReportStatus.NEEDS_REVIEW)
        self.assertNotIn(rejected.text, report.summary)
        self.assertEqual(json.loads(report.summary)["accepted_claims"][0]["text"], CLAIM.text)


class LimitTests(OfflineTests):
    async def test_question_prompt_response_and_report_lengths_are_bounded(self):
        for limit in ("max_question_chars", "max_prompt_chars", "max_response_chars"):
            with self.subTest(limit=limit), self.assertRaises(AgentError):
                await MeshPlannerAgent(self.llm(FakeMesh(plan()), **{limit: 1})).plan(QUESTION)
        supported = VerificationResult(CLAIM.id, VerificationStatus.SUPPORTED, "fixture", ("e",))
        with self.assertRaises(AgentLimitError):
            await VerifiedClaimsSynthesizerAgent(AgentLimits(max_report_chars=10)).synthesize(
                QUESTION, BUNDLE, (CLAIM,), (supported,))

    async def test_agent_timeout_is_safe(self):
        class HangingMesh:
            async def complete(self, request):
                await asyncio.Event().wait()

        llm = MeshJSON(HangingMesh(), "model", AgentLimits(operation_timeout_seconds=0.01))
        with self.assertRaisesRegex(AgentError, "Mesh completion failed"):
            await MeshPlannerAgent(llm).plan(QUESTION)

    async def test_budget_is_shared_and_not_reset_by_retry(self):
        budget = AgentBudget(AgentLimits(max_model_calls=1))
        llm = MeshJSON(FakeMesh(plan(), plan()), "model", budget.limits, budget)
        await MeshPlannerAgent(llm).plan(QUESTION)
        with self.assertRaises(AgentLimitError):
            await MeshPlannerAgent(llm).plan(QUESTION)


class ConfigurationTests(unittest.TestCase):
    def test_invalid_agent_limits(self):
        for options in ({"max_tasks": 0}, {"max_claims": True}, {"chunk_bytes": 3},
                        {"discovery_results_per_task": 21}, {"operation_timeout_seconds": float("nan")},
                        {"operation_timeout_seconds": 301}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                AgentLimits(**options)


if __name__ == "__main__":
    unittest.main()
