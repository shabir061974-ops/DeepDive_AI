"""Bounded source fallback: a failed candidate is skipped, never retried or cited. Offline only."""

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deepdive_ai.agents.common import AgentError, AgentLimitError, AgentLimits
from deepdive_ai.agents.researcher import RetrievalResearcherAgent
from deepdive_ai.discovery import DiscoveredSource
from deepdive_ai.http_transport import (
    FailureCategory, HTTPError, HTTPResponse, RequestPolicy, TransportError, _PinnedConnection,
)
from deepdive_ai.live import PROFILE, run_live
from deepdive_ai.models import ResearchBundle, Source, SourceProvenance
from deepdive_ai.retrieval import HTTPWebRetriever, RetrievalSettings, RetrievedDocument
from test_agents import BODY, COUNTER, NOW, OTHER_URL, QUESTION, TASK, URL, FakeDiscovery, assessment, draft, plan
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport

TEXT = (("content-type", "text/plain; charset=utf-8"),)


class ScriptedSource:
    """Per-URL outcomes; records every request so unsafe or repeated fetches are visible."""

    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    async def send(self, request):
        self.calls.append(request.url)
        outcome = self.outcomes.get(request.url, HTTPResponse(200, TEXT, BODY if request.url == URL else COUNTER))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def ok(body=COUNTER):
    return HTTPResponse(200, TEXT, body)


def researcher(transport, max_response_bytes=2_000_000, max_requests=100, **limits):
    retriever = HTTPWebRetriever(RetrievalSettings(policy=RequestPolicy(
        max_retries=0, max_response_bytes=max_response_bytes, max_requests=max_requests), max_redirects=1),
        transport, lambda: NOW)
    limits.setdefault("sources_per_task", 1)
    return RetrievalResearcherAgent(FakeDiscovery((URL, OTHER_URL)), retriever, AgentLimits(**limits))


class OfflineCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("real DNS forbidden")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("real HTTP forbidden"))):
            guard.start()
            self.addCleanup(guard.stop)


class ResearcherFallbackTests(OfflineCase):
    def assert_only_second_source(self, bundle):
        self.assertEqual([source.url for source in bundle.sources], [OTHER_URL])
        self.assertTrue(bundle.evidence)
        self.assertTrue(all(item.source_id == bundle.sources[0].id for item in bundle.evidence))
        self.assertEqual({item.excerpt for item in bundle.evidence}, {COUNTER.decode()})

    async def test_first_candidate_failure_falls_back_to_second(self):
        failures = {
            "forbidden": HTTPResponse(403, TEXT, b"denied"),
            "not_found": HTTPResponse(404, TEXT, b""),
            "server_error": HTTPResponse(500, TEXT, b""),
            "unsupported_type": HTTPResponse(200, (("content-type", "image/png"),), b"png"),
            "empty": HTTPResponse(200, TEXT, b""),
            "network": TransportError("reset", category=FailureCategory.NETWORK),
            "timeout": TransportError("slow", category=FailureCategory.TIMEOUT),
            "tls": TransportError("tls", category=FailureCategory.TLS),
            "dns": TransportError("dns", category=FailureCategory.DNS),
        }
        for name, outcome in failures.items():
            with self.subTest(failure=name):
                transport = ScriptedSource({URL: outcome})
                agent = researcher(transport)
                bundle = await agent.research(QUESTION, (TASK,), ResearchBundle(), ())
                self.assert_only_second_source(bundle)
                self.assertEqual(transport.calls, [URL, OTHER_URL])  # the failed URL is never retried
                self.assertEqual(agent.budget.used["fetch"], 2)

    async def test_unsafe_redirect_is_skipped_without_requesting_the_destination(self):
        transport = ScriptedSource({URL: HTTPResponse(302, (("Location", "https://127.0.0.1/admin"),), b"")})
        bundle = await researcher(transport).research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assert_only_second_source(bundle)
        self.assertEqual(transport.calls, [URL, OTHER_URL])

    async def test_oversized_response_is_skipped(self):
        transport = ScriptedSource({URL: ok(b"x" * 100), OTHER_URL: ok(b"small text")})
        bundle = await researcher(transport, max_response_bytes=50).research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assertEqual([source.url for source in bundle.sources], [OTHER_URL])

    async def test_successful_first_candidate_does_not_fetch_the_fallback(self):
        transport = ScriptedSource({})
        agent = researcher(transport)
        bundle = await agent.research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assertEqual(transport.calls, [URL])
        self.assertEqual([source.url for source in bundle.sources], [URL])
        self.assertEqual(agent.budget.used["fetch"], 1)

    async def test_all_candidates_failing_stops_without_evidence(self):
        transport = ScriptedSource({URL: HTTPResponse(403, TEXT, b""), OTHER_URL: TransportError("x")})
        with self.assertRaises(AgentError) as caught:
            await researcher(transport).research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assertEqual(str(caught.exception), "Source retrieval failed")
        self.assertIsInstance(caught.exception.__context__, TransportError)  # last candidate's typed failure
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(transport.calls, [URL, OTHER_URL])

    async def test_default_limits_keep_every_successful_candidate(self):
        transport = ScriptedSource({})
        bundle = await researcher(transport, sources_per_task=20).research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assertEqual([source.url for source in bundle.sources], [URL, OTHER_URL])
        self.assertEqual(AgentLimits().sources_per_task, 20)
        transport = ScriptedSource({URL: HTTPResponse(403, TEXT, b"")})
        bundle = await researcher(transport, sources_per_task=20).research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assertEqual([source.url for source in bundle.sources], [OTHER_URL])

    async def test_budget_violations_remain_fatal(self):
        transport = ScriptedSource({URL: HTTPResponse(403, TEXT, b"")})
        with self.assertRaises(AgentLimitError):
            await researcher(transport, max_fetch_calls=1).research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assertEqual(transport.calls, [URL])
        # The HTTP client's lifetime request budget is not a candidate failure.
        transport = ScriptedSource({URL: HTTPResponse(403, TEXT, b"")})
        with self.assertRaises(AgentError) as caught:
            await researcher(transport, max_requests=1).research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assertNotIsInstance(caught.exception, AgentLimitError)
        self.assertEqual(transport.calls, [URL])

    async def test_post_retrieval_failures_remain_fatal_and_stop_fallback(self):
        transport = ScriptedSource({URL: ok(b"\xff\xfe not utf-8")})
        with self.assertRaises(AgentError):
            await researcher(transport).research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assertEqual(transport.calls, [URL])

        class Retriever:
            def __init__(self, document=None, error=None):
                self.document, self.error, self.calls = document, error, []

            async def fetch(self, url):
                self.calls.append(url)
                if self.error is not None:
                    raise self.error
                return self.document

        tampered = RetrievedDocument(Source("s", URL, URL, SourceProvenance(NOW, hashlib.sha256(BODY).hexdigest(),
                                                                             "fixture")), BODY, URL, (URL,), "text/plain")
        object.__setattr__(tampered, "content", b"bytes changed after provenance was recorded")
        wrong_url = RetrievedDocument(Source("s", URL, URL, SourceProvenance(NOW, hashlib.sha256(BODY).hexdigest(),
                                                                              "fixture")), BODY, OTHER_URL, (OTHER_URL,),
                                      "text/plain")
        for retriever in (Retriever(tampered), Retriever(wrong_url), Retriever("not a document"),
                          Retriever(error=RuntimeError("private detail")), Retriever(error=ValueError("x"))):
            with self.subTest(retriever=retriever.error or type(retriever.document).__name__):
                agent = RetrievalResearcherAgent(FakeDiscovery((URL, OTHER_URL)), retriever,
                                                 AgentLimits(sources_per_task=1))
                with self.assertRaises(AgentError) as caught:
                    await agent.research(QUESTION, (TASK,), ResearchBundle(), ())
                self.assertNotIn("private detail", str(caught.exception))
                self.assertEqual(retriever.calls, [URL])

    async def test_unsafe_candidate_is_rejected_without_being_fetched(self):
        unsafe = object.__new__(DiscoveredSource)
        object.__setattr__(unsafe, "url", "https://127.0.0.1/")
        for field_name in ("title", "snippet"):
            if field_name in getattr(DiscoveredSource, "__dataclass_fields__", {}):
                object.__setattr__(unsafe, field_name, None)

        class Discovery:
            async def discover(self, request):
                return (unsafe,)

        transport = ScriptedSource({})
        agent = RetrievalResearcherAgent(Discovery(), HTTPWebRetriever(RetrievalSettings(), transport, lambda: NOW),
                                         AgentLimits(sources_per_task=1))
        with self.assertRaises(HTTPError):
            await agent.research(QUESTION, (TASK,), ResearchBundle(), ())
        self.assertEqual(transport.calls, [])
        self.assertEqual(agent.budget.used["fetch"], 0)

    def test_sources_per_task_must_be_positive(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AgentLimits(sources_per_task=value)


class LiveFallbackTests(OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def run_with(self, source_outcomes):
        source = ScriptedSource(source_outcomes)
        mesh = MeshTransport(plan(), draft, assessment)
        brave = SearchTransport((URL, OTHER_URL))
        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result = await run_live(replace(OPTIONS), execute=True, environment=ENV, output_root=self.root,
                                    mesh_transport=mesh, brave_transport=brave, source_transport=source)
        return result, source, brave

    async def test_live_run_completes_from_the_fallback_source(self):
        result, source, brave = await self.run_with({URL: HTTPResponse(403, TEXT, b"denied")})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["report_status"], "verified")
        self.assertEqual(result["attempts"], {"brave": 1, "mesh": 3, "source": 2})
        self.assertEqual(source.calls, [URL, OTHER_URL])
        self.assertIn("count=2", brave.calls[0].url)
        self.assertEqual(result["diagnostics"]["source"],
                         {"category": "authentication_http_status", "stage": None, "http_status": 403,
                          "source_hostname": "fixture.invalid"})
        report = json.loads((self.root / result["run_id"] / "report.json").read_text())["report"]
        cited = {source_record["url"] for source_record in report["bundle"]["sources"]}
        self.assertEqual(cited, {OTHER_URL})
        summary = json.loads(report["summary"])
        urls = {c["url"] for claim in summary["accepted_claims"] for c in claim["citations"]}
        self.assertEqual(urls, {OTHER_URL})
        self.assertNotIn("denied", json.dumps(result) + (self.root / result["run_id"] / "report.json").read_text())

    async def test_live_run_does_not_fetch_fallback_after_success(self):
        result, source, _ = await self.run_with({})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["attempts"]["source"], 1)
        self.assertEqual(source.calls, [URL])

    async def test_live_run_fails_closed_when_every_candidate_fails(self):
        result, source, _ = await self.run_with({URL: HTTPResponse(403, TEXT, b""), OTHER_URL: HTTPResponse(403, TEXT, b"")})
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("report_status", result)
        # Same typed failure the single-candidate path reports for a 403; never a generic category.
        self.assertEqual(result["failure_category"], "http_protocol_failure")
        self.assertEqual(result["diagnostics"]["source"]["http_status"], 403)
        self.assertEqual(result["attempts"]["source"], 2)
        self.assertEqual(source.calls, [URL, OTHER_URL])
        self.assertFalse((self.root / result["run_id"] / "report.json").exists())

    def test_live_request_and_spending_limits_are_unchanged(self):
        self.assertEqual({key: PROFILE[key] for key in ("mesh_requests", "brave_requests", "source_requests",
                                                        "overall_timeout_seconds", "http_retries", "research_retries")},
                         {"mesh_requests": 7, "brave_requests": 2, "source_requests": 4, "overall_timeout_seconds": 120,
                          "http_retries": 0, "research_retries": 1})
        live_source = Path(__file__).resolve().parents[1] / "src" / "deepdive_ai" / "live.py"
        text = live_source.read_text(encoding="utf-8")
        self.assertIn("discovery_results_per_task=2, sources_per_task=1", text)
        self.assertIn("max_discovery_calls=2", text)
        self.assertIn("max_fetch_calls=2", text)
        self.assertIn("max_model_calls=7", text)


if __name__ == "__main__":
    unittest.main()
