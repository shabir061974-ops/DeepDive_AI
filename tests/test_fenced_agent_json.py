"""Drafter/Verifier whole-document JSON fence compatibility. Offline only; strict checks unchanged."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deepdive_ai.agents.common import AgentFailureCode, AgentJSONShape, AgentLimits, AgentOutputError, MeshJSON
from deepdive_ai.agents.drafter import MeshDrafterAgent
from deepdive_ai.agents.verifier import MeshVerifierAgent
from deepdive_ai.http_transport import HTTPResponse, _PinnedConnection
from deepdive_ai.live import run_live
from deepdive_ai.models import VerificationStatus
from test_agents import BUNDLE, CLAIM, QUESTION, STATEMENT, URL, FakeMesh, assessment, draft, plan
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport, SourceTransport


def fence(document, tag="json", newline="\n"):
    return f"```{tag}{newline}{document}{newline}```"


def fenced(builder, **kwargs):
    return lambda payload: fence(json.dumps(builder(payload)), **kwargs)


class FencedAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for target in ("socket.getaddrinfo", "socket.create_connection"):
            guard = patch(target, side_effect=AssertionError("No network"))
            guard.start()
            self.addCleanup(guard.stop)

    def llm(self, *outcomes):
        mesh = FakeMesh(*outcomes)
        return mesh, MeshJSON(mesh, "offline-configured-model", AgentLimits())

    async def draft_with(self, output):
        return await MeshDrafterAgent(self.llm(output)[1]).draft(QUESTION, BUNDLE)

    async def verify_with(self, output):
        return await MeshVerifierAgent(self.llm(output)[1]).verify((CLAIM,), BUNDLE)

    async def test_drafter_accepts_complete_fence_with_identical_result(self):
        plain = await self.draft_with(draft)
        for kwargs in ({}, {"tag": ""}, {"newline": "\r\n"}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(await self.draft_with(fenced(draft, **kwargs)), plain)
        self.assertEqual(await self.draft_with(lambda p: " \n" + fence(json.dumps(draft(p))) + "\n\t"), plain)

    async def test_verifier_accepts_complete_fence_with_identical_result(self):
        plain = await self.verify_with(assessment)
        result = await self.verify_with(fenced(assessment))
        self.assertEqual(result, plain)
        self.assertEqual(result[0].status, VerificationStatus.SUPPORTED)

    async def test_prose_multiple_or_partial_fences_still_reject(self):
        for wrap, shape in (
            (lambda d: "Here is the JSON:\n" + fence(d), AgentJSONShape.EMBEDDED_FENCE),
            (lambda d: fence(d) + "\nDone.", AgentJSONShape.EMBEDDED_FENCE),
            # Two fences match as one wrapper whose content is not a single JSON document.
            (lambda d: fence(d) + "\n" + fence(d), AgentJSONShape.FENCED_INVALID_JSON),
            (lambda d: "```JSON\n" + d + "\n```", AgentJSONShape.EMBEDDED_FENCE),
            (lambda d: "```json " + d + "```", AgentJSONShape.EMBEDDED_FENCE),
            (lambda d: "```json\n" + d, AgentJSONShape.EMBEDDED_FENCE),
            (lambda d: fence(d[:-2]), AgentJSONShape.FENCED_INVALID_JSON),
            (lambda d: fence(d + " trailing"), AgentJSONShape.FENCED_INVALID_JSON),
        ):
            for name, builder, run in (("drafter", draft, self.draft_with), ("verifier", assessment, self.verify_with)):
                with self.subTest(agent=name, shape=shape):
                    with self.assertRaises(AgentOutputError) as caught:
                        await run(lambda payload: wrap(json.dumps(builder(payload))))
                    self.assertEqual(caught.exception.code, AgentFailureCode.MALFORMED_JSON)
                    self.assertIs(caught.exception.json_shape, shape)

    async def test_fenced_duplicates_nonfinite_and_schema_errors_keep_their_codes(self):
        cases = (
            (self.draft_with, '{"claims": [], "claims": []}', AgentFailureCode.DUPLICATE_KEYS),
            (self.draft_with, '{"claims": NaN}', AgentFailureCode.NONFINITE),
            (self.draft_with, '{"claims": [], "extra": 1}', AgentFailureCode.FIELD_MISMATCH),
            (self.draft_with, '[]', AgentFailureCode.OBJECT_REQUIRED),
            (self.verify_with, '{"claim_id": "x", "claim_id": "y"}', AgentFailureCode.DUPLICATE_KEYS),
            (self.verify_with, '{"claim_id": "q:claim:1"}', AgentFailureCode.FIELD_MISMATCH),
        )
        for run, document, code in cases:
            with self.subTest(document=document):
                with self.assertRaises(AgentOutputError) as caught:
                    await run(fence(document))
                self.assertEqual(caught.exception.code, code)
                self.assertIsNone(caught.exception.json_shape)

    async def test_fenced_unsupported_evidence_is_still_rejected(self):
        bad_drafts = (
            {"claims": [{"text": STATEMENT, "evidence_ids": ["unknown"]}]},
            {"claims": [{"text": STATEMENT, "evidence_ids": []}]},
            {"claims": [{"text": STATEMENT, "evidence_ids": ["e", "e"]}]},
            {"claims": [{"text": f"{STATEMENT} https://fixture.invalid", "evidence_ids": ["e"]}]},
            {"claims": [{"text": STATEMENT, "evidence_ids": ["e"]}, {"text": STATEMENT, "evidence_ids": ["e"]}]},
        )
        for document in bad_drafts:
            with self.subTest(draft=document), self.assertRaises(AgentOutputError):
                await self.draft_with(fence(json.dumps(document)))

        def outside_quote(payload):
            result = assessment(payload)
            result["assessments"][0]["quote"] = "text not present in the retrieved excerpt"
            return result

        def unknown_evidence(payload):
            result = assessment(payload)
            result["assessments"][0]["evidence_id"] = "unknown"
            return result

        def wrong_claim(payload):
            return {**assessment(payload), "claim_id": "other"}

        for builder in (outside_quote, unknown_evidence, wrong_claim):
            with self.subTest(verifier=builder.__name__), self.assertRaises(AgentOutputError):
                await self.verify_with(fenced(builder))

    async def test_fenced_unsupported_verdict_is_still_downgraded(self):
        result = await self.verify_with(fenced(lambda p: assessment(p, relations=["insufficient"])))
        self.assertEqual(result[0].status, VerificationStatus.INSUFFICIENT)

    async def test_raw_response_limit_applies_before_fence_removal(self):
        mesh = FakeMesh(lambda p: fence(json.dumps(draft(p))))
        llm = MeshJSON(mesh, "offline-configured-model", AgentLimits(max_response_chars=20))
        with self.assertRaises(AgentOutputError) as caught:
            await MeshDrafterAgent(llm).draft(QUESTION, BUNDLE)
        self.assertEqual(caught.exception.code, AgentFailureCode.TEXT_LIMIT)


class LiveFencedWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No live sockets"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def run_live_with(self, wrap):
        class Transport(MeshTransport):
            async def send(self, request):
                response = await super().send(request)
                data = json.loads(response.body)
                data["choices"][0]["message"]["content"] = wrap(data["choices"][0]["message"]["content"])
                return HTTPResponse(200, response.headers, json.dumps(data).encode())

        mesh = Transport(plan(), draft, assessment)
        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result = await run_live(replace(OPTIONS), execute=True, environment=ENV, output_root=self.root,
                                    mesh_transport=mesh, brave_transport=SearchTransport((URL,)),
                                    source_transport=SourceTransport())
        return result, mesh

    async def test_fenced_planner_drafter_and_verifier_complete_with_unchanged_budgets(self):
        plain, plain_mesh = await self.run_live_with(lambda content: content)
        result, mesh = await self.run_live_with(fence)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["report_status"], plain["report_status"])
        self.assertEqual(result["attempts"], plain["attempts"])
        self.assertEqual(result["attempts"]["mesh"], 3)
        self.assertNotIn("agent_json_shape", result)
        roles = [json.loads(json.loads(r.body)["messages"][0]["content"])["role"] for r in mesh.calls]
        self.assertEqual(roles, ["planner", "drafter", "verifier"])
        report = json.loads((self.root / result["run_id"] / "report.json").read_text())
        plain_report = json.loads((self.root / plain["run_id"] / "report.json").read_text())
        self.assertEqual(report["report"]["status"], plain_report["report"]["status"])

    async def test_fenced_verifier_with_prose_fails_closed(self):
        calls = []

        def wrap(content):
            calls.append(content)
            return "Verdict below.\n" + fence(content) if len(calls) == 3 else content

        result, _ = await self.run_live_with(wrap)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["agent_failure_code"], "malformed_agent_json")
        self.assertEqual(result["agent_json_shape"], "embedded_fence")
        self.assertFalse((self.root / result["run_id"] / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
