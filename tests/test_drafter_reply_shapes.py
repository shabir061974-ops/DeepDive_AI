"""Drafter reply shapes through the real Mesh client, MeshJSON and Drafter. Offline only."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deepdive_ai.agents.common import AgentError, AgentFailureCode, AgentJSONShape, AgentLimits, AgentOutputError, MeshJSON
from deepdive_ai.agents.drafter import INSUFFICIENT_EVIDENCE, MeshDrafterAgent
from deepdive_ai.http_transport import HTTPResponse, RequestPolicy, _PinnedConnection
from deepdive_ai.live import run_live
from deepdive_ai.mesh import MeshAPIClient, MeshCompletionCheck, MeshSettings
from test_agents import BUNDLE, OTHER_URL, QUESTION, URL, assessment, plan
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport, SourceTransport

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "drafter_replies.json").read_text(encoding="utf-8"))
MODEL = "offline-model"
C = AgentFailureCode

# Expected result per fixture: number of accepted claims, or the exact rejection.
EXPECTED = {
    "plain_object": 1, "fenced_object": 1, "fenced_object_no_tag_crlf": 1,
    "empty_claims": 0, "insufficient_evidence": 0, "fenced_insufficient_evidence": 0,
    "top_level_array": (C.OBJECT_REQUIRED, None), "fenced_array": (C.OBJECT_REQUIRED, None),
    "null": (C.OBJECT_REQUIRED, None), "fenced_null": (C.OBJECT_REQUIRED, None),
    "number": (C.OBJECT_REQUIRED, None), "json_string": (C.OBJECT_REQUIRED, None),
    "claim_row_string": (C.OBJECT_REQUIRED, None), "claims_null": (C.LIST_REQUIRED, None),
    "empty_object": (C.FIELD_MISMATCH, None), "free_text_reason": (C.FIELD_MISMATCH, None),
    "status_with_claims": (C.DRAFTER_INVALID_STATUS, None), "unknown_status": (C.DRAFTER_INVALID_STATUS, None),
    "prose": (C.MALFORMED_JSON, AgentJSONShape.NON_JSON_PREFIX),
    "prose_then_fence": (C.MALFORMED_JSON, AgentJSONShape.EMBEDDED_FENCE),
    "empty_content": "completion", "whitespace_content": "completion",
}


def content(name, evidence_id="e"):
    return FIXTURES[name].replace("EVIDENCE_ID", evidence_id)


class Envelope:
    """Valid Mesh completion around a fixture's assistant content."""

    def __init__(self, text):
        self.text = text
        self.calls = 0

    async def send(self, request):
        self.calls += 1
        return HTTPResponse(200, (("Content-Type", "application/json"), ("X-Mesh-Routing-Fallback", "false")),
                            json.dumps({"id": "offline-id", "model": MODEL, "choices": [{
                                "finish_reason": "stop", "message": {"role": "assistant", "content": self.text}}]}).encode())


async def draft_with(name):
    envelope = Envelope(content(name))
    client = MeshAPIClient(MeshSettings("offline-drafter-key", policy=RequestPolicy(max_retries=0)), envelope)
    claims = await MeshDrafterAgent(MeshJSON(client, MODEL, AgentLimits())).draft(QUESTION, BUNDLE)
    return claims, envelope


class OfflineCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("real DNS forbidden")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("real HTTP forbidden"))):
            guard.start()
            self.addCleanup(guard.stop)


class ReplyShapeTests(OfflineCase):
    def test_every_fixture_has_an_expectation(self):
        self.assertEqual(set(EXPECTED), set(FIXTURES) - {"_note"})

    async def test_each_reply_shape_through_the_real_client_and_drafter(self):
        for name, expected in EXPECTED.items():
            with self.subTest(fixture=name):
                if isinstance(expected, int):
                    claims, envelope = await draft_with(name)
                    self.assertEqual(len(claims), expected)
                    self.assertEqual(envelope.calls, 1)  # no retry of the model call
                    if expected:
                        self.assertEqual(claims[0].evidence_ids, ("e",))
                    continue
                if expected == "completion":
                    # Empty content never reaches the JSON parser; the Mesh client rejects it.
                    with self.assertRaises(AgentError) as caught:
                        await draft_with(name)
                    self.assertNotIsInstance(caught.exception, AgentOutputError)
                    self.assertIs(caught.exception.__context__.completion_check, MeshCompletionCheck.CONTENT_EMPTY)
                    continue
                code, shape = expected
                with self.assertRaises(AgentOutputError) as caught:
                    await draft_with(name)
                self.assertIs(caught.exception.code, code)
                self.assertIs(caught.exception.json_shape, shape)

    async def test_fence_stripping_does_not_turn_arrays_or_prose_into_valid_output(self):
        for name in ("fenced_array", "fenced_null", "prose_then_fence"):
            with self.subTest(fixture=name), self.assertRaises(AgentOutputError):
                await draft_with(name)

    async def test_prompt_defines_the_insufficient_evidence_reply(self):
        envelope = Envelope(content("insufficient_evidence"))
        client = MeshAPIClient(MeshSettings("offline-drafter-key", policy=RequestPolicy(max_retries=0)), envelope)

        class Recording:
            def __init__(self):
                self.prompts = []

            async def complete(self, request):
                self.prompts.append(json.loads(request.prompt))
                return await client.complete(request)

        mesh = Recording()
        await MeshDrafterAgent(MeshJSON(mesh, MODEL, AgentLimits())).draft(QUESTION, BUNDLE)
        self.assertIn(f'return {{"claims": [], "status": "{INSUFFICIENT_EVIDENCE}"}}', mesh.prompts[0]["instruction"])

    async def test_no_structured_output_parameter_is_sent(self):
        captured = []

        class Capture(Envelope):
            async def send(self, request):
                captured.append(json.loads(request.body))
                return await super().send(request)

        client = MeshAPIClient(MeshSettings("offline-drafter-key", policy=RequestPolicy(max_retries=0)),
                               Capture(content("plain_object")))
        await MeshDrafterAgent(MeshJSON(client, MODEL, AgentLimits())).draft(QUESTION, BUNDLE)
        self.assertEqual(set(captured[0]), {"model", "messages", "max_tokens", "stream"})


class LiveReplyShapeTests(OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def run_case(self, responses, search, source=None):
        mesh = MeshTransport(*responses)
        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result = await run_live(replace(OPTIONS), execute=True, environment=ENV, output_root=self.root,
                                    mesh_transport=mesh, brave_transport=SearchTransport(*search),
                                    source_transport=source or SourceTransport())
        return result

    @staticmethod
    def fixture(name):
        return lambda payload: content(name, payload["data"]["evidence"][0]["id"])

    async def test_fenced_drafter_reply_completes_verified(self):
        result = await self.run_case((plan(), self.fixture("fenced_object"), assessment), ((URL,),))
        self.assertEqual((result["status"], result["report_status"]), ("completed", "verified"))

    async def test_array_reply_fails_closed_with_object_required(self):
        result = await self.run_case((plan(), self.fixture("top_level_array")), ((URL,),))
        self.assertEqual(result["failure_category"], "agent_contract_failure")
        self.assertEqual(result["agent_failure_code"], "object_required")
        self.assertFalse((self.root / result["run_id"] / "report.json").exists())

    async def test_insufficient_evidence_completes_as_needs_review_within_budgets(self):
        result = await self.run_case((plan(), self.fixture("insufficient_evidence"), self.fixture("insufficient_evidence")),
                                ((URL,), (OTHER_URL,)))
        self.assertEqual((result["status"], result["report_status"]), ("completed", "needs_review"))
        self.assertEqual(result["attempts"], {"mesh": 3, "brave": 2, "source": 2})
        report = json.loads((self.root / result["run_id"] / "report.json").read_text())["report"]
        self.assertEqual((report["claims"], report["verification"]), ([], []))

    async def test_insufficient_evidence_after_a_source_fallback_hits_the_fetch_budget(self):
        # Existing limit, unchanged: a 403 fallback uses both fetches, so research-again cannot fetch.
        class Blocking(SourceTransport):
            async def send(self, request):
                if request.url == URL:
                    self.calls.append(request)
                    return HTTPResponse(403, (("Content-Type", "text/html"),), b"")
                return await super().send(request)

        result = await self.run_case((plan(), self.fixture("insufficient_evidence")), ((URL, OTHER_URL), (URL,)), Blocking())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["attempts"]["source"], 2)
        self.assertFalse((self.root / result["run_id"] / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
