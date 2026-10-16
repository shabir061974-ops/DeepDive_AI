"""Offline dashboard logic and ASGI integration; no HTTP client dependency or network."""

import asyncio
import hashlib
import json
import unittest
from unittest.mock import patch

from deepdive_ai.api import create_app
from deepdive_ai.demo import FIXTURE, NOTICE, SCENARIOS, FixtureRetriever, run_demo


async def request(app, path, *, method="GET", body=b"", headers=(), client="127.0.0.1", host="127.0.0.1"):
    messages = []
    consumed = False

    async def receive():
        nonlocal consumed
        if not consumed:
            consumed = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "headers": [(b"host", host.encode()), *headers],
             "client": (client, 1), "server": ("127.0.0.1", 80)}
    await asyncio.wait_for(app(scope, receive, send), 5)
    start = next(message for message in messages if message["type"] == "http.response.start")
    content = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), content


POST_HEADERS = ((b"content-type", b"application/json"), (b"x-deepdive-demo", b"offline"))


class OfflineDemoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")),
                      patch("deepdive_ai.http_transport.PublicHTTPTransport.send", side_effect=AssertionError("No live HTTP")),
                      patch("deepdive_ai.mesh.MeshSettings.from_environment", side_effect=AssertionError("No credential access")),
                      patch("deepdive_ai.search.BraveSearchSettings.from_environment", side_effect=AssertionError("No credential access"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def test_supported_runs_real_five_agent_sequence_and_resolves_citations(self):
        result = await run_demo("supported")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["report"]["status"], "verified")
        calls = [event["agent"] for event in result["events"] if event["type"] == "agent_started"]
        self.assertEqual(calls, ["planner", "researcher", "drafter", "verifier", "synthesizer"])
        citation = result["summary"]["accepted_claims"][0]["citations"][0]
        evidence = result["report"]["bundle"]["evidence"][0]
        source = result["report"]["bundle"]["sources"][0]
        self.assertEqual(citation["evidence_id"], evidence["id"])
        self.assertEqual(evidence["source_id"], source["id"])
        self.assertEqual(source["provenance"]["content_sha256"], hashlib.sha256(FIXTURE["source_a_text"].encode()).hexdigest())
        self.assertIn("offline authored fixture", source["provenance"]["retrieval_method"])
        self.assertEqual(result["metrics"]["scripted_model_calls"], 3)
        self.assertIn(NOTICE, result["markdown"])
        self.assertIn(f"#source-{source['id']}", result["markdown"])

    async def test_conflicting_sources_veto_positive_scripted_verdict_and_bound_retry(self):
        result = await run_demo("conflict")
        self.assertEqual(result["summary"]["accepted_claims"], [])
        self.assertEqual(result["metrics"]["research_retries"], 1)
        self.assertEqual(len(result["report"]["bundle"]["sources"]), 2)
        verification = result["report"]["verification"][0]
        rationale = json.loads(verification["rationale"])
        self.assertEqual(rationale["model_verdict"], "supported")
        self.assertEqual(verification["status"], "insufficient_evidence")
        self.assertIn("contradicts", [row["relation"] for row in rationale["assessments"]])

    async def test_unsupported_claim_excluded_from_final_summary(self):
        result = await run_demo("unsupported")
        self.assertEqual(result["summary"]["accepted_claims"], [])
        self.assertEqual(result["report"]["verification"][0]["status"], "unsupported")
        self.assertEqual(result["metrics"]["research_retries"], 0)

    async def test_recovery_preserves_first_source_and_real_retry_events(self):
        result = await run_demo("recovery")
        self.assertEqual(result["report"]["status"], "verified")
        self.assertEqual(result["metrics"]["research_retries"], 1)
        self.assertEqual([e["kind"] for e in result["audit"]].count("retry"), 1)
        self.assertEqual(len(result["report"]["bundle"]["evidence"]), 2)

    async def test_empty_evidence_cannot_produce_an_answer(self):
        result = await run_demo("empty")
        self.assertEqual(result["report"]["status"], "needs_review")
        self.assertEqual(result["report"]["claims"], [])
        self.assertEqual(result["metrics"]["scripted_model_calls"], 1)
        self.assertEqual(result["metrics"]["fixture_fetches"], 0)
        self.assertEqual(result["metrics"]["research_retries"], 1)

    async def test_malformed_assessment_fails_safely_with_audit(self):
        result = await run_demo("failure")
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["report"])
        self.assertEqual(result["audit"][-1]["kind"], "failed")
        self.assertNotIn("markdown", result)
        self.assertNotIn("authored malformed fixture response", json.dumps(result))
        self.assertFalse(any(e.get("agent") == "synthesizer" for e in result["events"]))

    async def test_fixture_scenarios_are_isolated_between_runs(self):
        a, b = await asyncio.gather(run_demo("supported"), run_demo("empty"))
        self.assertEqual(a["metrics"]["fixture_fetches"], 1)
        self.assertEqual(b["metrics"]["fixture_fetches"], 0)
        for result in (a, b):
            self.assertEqual([e["sequence"] for e in result["events"]], list(range(1, len(result["events"]) + 1)))

    async def test_unknown_scenario_and_nonfixture_url_rejected(self):
        with self.assertRaises(ValueError):
            await run_demo("arbitrary question")
        with self.assertRaises(ValueError):
            await FixtureRetriever(SCENARIOS[0]).fetch("https://example.com/")

    async def test_static_page_and_original_health_contract(self):
        app = create_app()
        status, headers, body = await request(app, "/")
        self.assertEqual(status, 200)
        self.assertIn(NOTICE.encode(), body)
        self.assertIn(b"default-src 'self'", headers[b"content-security-policy"])
        self.assertEqual(headers[b"x-content-type-options"], b"nosniff")
        status, _, body = await request(app, "/health")
        self.assertEqual(json.loads(body), {"status": "ok", "phase": "foundation"})
        for asset in ("dashboard.js", "dashboard.css", "mark.svg"):
            status, _, body = await request(app, "/static/" + asset)
            self.assertEqual(status, 200)
            self.assertTrue(body)

    async def test_api_stream_includes_real_progress_and_terminal_report(self):
        app = create_app()
        status, _, content = await request(app, "/api/demo/runs", method="POST",
                                           headers=POST_HEADERS, body=b'{"scenario_id":"recovery"}')
        self.assertEqual(status, 200)
        events = [json.loads(line) for line in content.splitlines()]
        self.assertEqual(events[0]["kind"], "started")
        self.assertTrue(any(event["type"] == "agent_started" for event in events))
        self.assertEqual(events[-1]["type"], "result")
        self.assertEqual(events[-1]["data"]["metrics"]["research_retries"], 1)

    async def test_api_accepts_only_fixed_scenario_selection(self):
        app = create_app()
        for body in (b'{"question":"Research something live"}', b'{"scenario_id":"supported","question":"extra"}',
                     b'{"scenario_id":"not-real"}', b'{"scenario_id":[]}', b'{"scenario_id":"supported","scenario_id":"empty"}',
                     b"not json", b"\xff"):
            status, _, content = await request(app, "/api/demo/runs", method="POST", headers=POST_HEADERS, body=body)
            self.assertEqual(status, 400)
        status, _, _ = await request(app, "/api/demo/runs", method="POST", headers=POST_HEADERS, body=b"x" * 1025)
        self.assertEqual(status, 413)

    async def test_api_rejects_cross_origin_and_remote_access(self):
        app = create_app()
        status, _, _ = await request(app, "/api/demo/runs", method="POST", body=b'{"scenario_id":"supported"}')
        self.assertEqual(status, 403)
        status, _, _ = await request(app, "/api/demo/runs", method="POST", body=b'{"scenario_id":"supported"}',
                                     headers=POST_HEADERS + ((b"origin", b"https://untrusted.invalid"),))
        self.assertEqual(status, 403)
        status, _, _ = await request(app, "/api/demo/scenarios", client="203.0.113.2")
        self.assertEqual(status, 403)

    async def test_scenario_catalog_truthfully_disables_live_research(self):
        status, _, content = await request(create_app(), "/api/demo/scenarios")
        data = json.loads(content)
        self.assertEqual(status, 200)
        self.assertFalse(data["live_research_enabled"])
        self.assertEqual(len(data["scenarios"]), 6)
        self.assertTrue(all(s["question"] == FIXTURE["question"] for s in data["scenarios"]))

    async def test_stream_concurrency_limit_and_slot_release(self):
        released, started = asyncio.Event(), asyncio.Event()
        count = 0

        async def controlled_run(scenario_id, notify):
            nonlocal count
            count += 1
            if count == 2:
                started.set()
            await released.wait()
            return {"status": "test-completed"}

        with patch("deepdive_ai.demo.run_demo", side_effect=controlled_run):
            app = create_app()
        running = [asyncio.create_task(request(app, "/api/demo/runs", method="POST", headers=POST_HEADERS,
                                              body=b'{"scenario_id":"supported"}')) for _ in range(2)]
        try:
            await asyncio.wait_for(started.wait(), 2)
            status, _, _ = await request(app, "/api/demo/runs", method="POST", headers=POST_HEADERS,
                                         body=b'{"scenario_id":"supported"}')
            self.assertEqual(status, 429)
        finally:
            released.set()
            await asyncio.gather(*running)
        status, _, _ = await request(app, "/api/demo/runs", method="POST", headers=POST_HEADERS,
                                     body=b'{"scenario_id":"supported"}')
        self.assertEqual(status, 200)

    async def test_host_validation_rejects_rebinding_names(self):
        app = create_app()
        status, _, _ = await request(app, "/api/demo/scenarios", host="untrusted.invalid")
        self.assertEqual(status, 400)

    async def test_cancelled_client_request_cancels_worker_and_releases_capacity(self):
        release, two_started, third_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        count = 0

        async def controlled_run(scenario_id, notify):
            nonlocal count
            count += 1
            if count == 2:
                two_started.set()
            if count == 3:
                third_started.set()
            await release.wait()
            return {"status": "test-completed"}

        with patch("deepdive_ai.demo.run_demo", side_effect=controlled_run):
            app = create_app()
        async def start_request():
            return await request(app, "/api/demo/runs", method="POST", headers=POST_HEADERS,
                                 body=b'{"scenario_id":"supported"}')

        running = [asyncio.create_task(start_request()) for _ in range(2)]
        try:
            await asyncio.wait_for(two_started.wait(), 2)
            running[0].cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running[0]
            running.append(asyncio.create_task(start_request()))
            await asyncio.wait_for(third_started.wait(), 2)
        finally:
            release.set()
            await asyncio.gather(*running, return_exceptions=True)
