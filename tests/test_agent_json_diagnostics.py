"""Malformed agent JSON shape diagnostics. Acceptance is unchanged; no network is used."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deepdive_ai.agents.common import (
    AgentFailureCode, AgentJSONShape, AgentLimits, AgentOutputError, MeshJSON, object_fields,
)
from deepdive_ai.http_transport import HTTPResponse, _PinnedConnection
from deepdive_ai.live import run_live
from deepdive_ai.mesh import MeshResponse
from test_agents import URL, assessment, draft, plan
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport, SourceTransport


PRIVATE = "private-model-text-do-not-retain"


class FixedMesh:
    def __init__(self, text):
        self.text = text

    async def complete(self, request):
        return MeshResponse(self.text, "offline-request")


async def ask(text, *, fence=False):
    return await MeshJSON(FixedMesh(text), "model", AgentLimits()).ask(
        "drafter", "instruction", {"claims": []}, {}, allow_markdown_fence=fence)


class ShapeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        guard = patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS"))
        guard.start()
        self.addCleanup(guard.stop)

    async def test_malformed_documents_are_rejected_with_a_fixed_shape(self):
        cases = {
            f'```json\n{{"claims":[],"note":"{PRIVATE}"}}\n```': AgentJSONShape.FENCED_VALID_JSON,
            '```\n{"claims":[]}\r\n```': AgentJSONShape.FENCED_VALID_JSON,
            f'```json\n{{"claims":[{{"text":"{PRIVATE}\n```': AgentJSONShape.FENCED_INVALID_JSON,
            f'{PRIVATE}\n```json\n{{"claims":[]}}\n```': AgentJSONShape.EMBEDDED_FENCE,
            f'```json\n{{"claims":[]}}\n```\n{PRIVATE}': AgentJSONShape.EMBEDDED_FENCE,
            f'{PRIVATE}: {{"claims": []}}': AgentJSONShape.NON_JSON_PREFIX,
            'claims: []': AgentJSONShape.NON_JSON_PREFIX,
            f'{{"claims": []}} {PRIVATE}': AgentJSONShape.TRAILING_CONTENT,
            '{"claims": []}{"claims": []}': AgentJSONShape.TRAILING_CONTENT,
            f'{{"claims": [{{"text": "{PRIVATE}': AgentJSONShape.INCOMPLETE_JSON,
            '{"claims": [': AgentJSONShape.INCOMPLETE_JSON,
            '{"claims": [{"text": "a", "evidence_ids": ["e"]},': AgentJSONShape.INCOMPLETE_JSON,
            '{"claims": [}': AgentJSONShape.INVALID_SYNTAX,
            "{'claims': []}": AgentJSONShape.INVALID_SYNTAX,
            '{"claims": [],}': AgentJSONShape.INVALID_SYNTAX,
            "[" * 15000 + "]" * 15000: AgentJSONShape.INVALID_SYNTAX,
        }
        for document, shape in cases.items():
            with self.subTest(shape=shape, document=document[:40]):
                with self.assertRaises(AgentOutputError) as caught:
                    await ask(document)
                self.assertEqual(caught.exception.code, AgentFailureCode.MALFORMED_JSON)
                self.assertIs(caught.exception.json_shape, shape)
                self.assertNotIn(PRIVATE, str(caught.exception))

    async def test_strict_acceptance_is_unchanged(self):
        self.assertEqual(await ask('{"claims": []}'), {"claims": []})
        self.assertEqual(await ask(' \n{"claims": []}\n '), {"claims": []})
        # The whole-document fence is opt-in per agent call; the MeshJSON default stays strict.
        self.assertEqual(await ask('```json\n{"claims": []}\n```', fence=True), {"claims": []})
        with self.assertRaises(AgentOutputError) as caught:
            await ask('```json\n{"claims": []}\n```')
        self.assertIs(caught.exception.json_shape, AgentJSONShape.FENCED_VALID_JSON)

    async def test_opted_in_fence_with_invalid_content_reports_fenced_invalid(self):
        with self.assertRaises(AgentOutputError) as caught:
            await ask('```json\n{"claims": [\n```', fence=True)
        self.assertEqual(caught.exception.code, AgentFailureCode.MALFORMED_JSON)
        self.assertIs(caught.exception.json_shape, AgentJSONShape.FENCED_INVALID_JSON)

    async def test_other_failure_codes_carry_no_shape(self):
        for document, code in (('{"a": 1, "a": 2}', AgentFailureCode.DUPLICATE_KEYS),
                               ('{"a": NaN}', AgentFailureCode.NONFINITE),
                               ("x" * 40_000, AgentFailureCode.TEXT_LIMIT)):
            with self.subTest(code=code), self.assertRaises(AgentOutputError) as caught:
                await ask(document)
            self.assertEqual(caught.exception.code, code)
            self.assertIsNone(caught.exception.json_shape)
        with self.assertRaises(AgentOutputError) as caught:
            object_fields([], {"claims"})
        self.assertIsNone(caught.exception.json_shape)

    def test_shape_values_are_fixed_and_invalid_values_are_dropped(self):
        self.assertEqual({shape.value for shape in AgentJSONShape}, {
            "fenced_valid_json", "fenced_invalid_json", "embedded_fence", "non_json_prefix",
            "trailing_content", "incomplete_json", "invalid_syntax"})
        self.assertIsNone(AgentOutputError("x", json_shape="fenced_valid_json").json_shape)


class LiveShapeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No live sockets"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def run_with_drafter_text(self, content):
        class Transport(MeshTransport):
            async def send(self, request):
                response = await super().send(request)
                if len(self.calls) != 2:
                    return response
                data = json.loads(response.body)
                data["choices"][0]["message"]["content"] = content
                return HTTPResponse(200, response.headers, json.dumps(data).encode())

        mesh = Transport(plan(), draft, assessment)
        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result = await run_live(replace(OPTIONS), execute=True, environment=ENV, output_root=self.root,
                                    mesh_transport=mesh, brave_transport=SearchTransport((URL,)),
                                    source_transport=SourceTransport())
        return result, self.root / result["run_id"]

    async def test_drafter_shape_is_persisted_without_content(self):
        for content, shape in ((f'```json\n{{"claims":[{{"text":"{PRIVATE}\n```', "fenced_invalid_json"),
                               (f'{PRIVATE}\n```json\n{{"claims":[]}}\n```', "embedded_fence"),
                               (f'{{"claims": [{{"text": "{PRIVATE}', "incomplete_json"),
                               (f'{PRIVATE} {{"claims": []}}', "non_json_prefix")):
            with self.subTest(shape=shape):
                result, folder = await self.run_with_drafter_text(content)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["failure_category"], "agent_contract_failure")
                self.assertEqual(result["agent_failure_code"], "malformed_agent_json")
                self.assertEqual(result["agent_json_shape"], shape)
                self.assertEqual(result["attempts"]["mesh"], 2)
                self.assertNotIn("mesh", result["diagnostics"])
                self.assertFalse((folder / "report.json").exists())
                self.assertEqual(json.loads((folder / "outcome.json").read_text()), result)
                persisted = (folder / "outcome.json").read_text() + (folder / "audit.jsonl").read_text()
                for secret in (PRIVATE, *ENV.values()):
                    self.assertNotIn(secret, persisted)

    async def test_successful_and_non_json_failures_have_no_shape(self):
        result, _ = await self.run_with_drafter_text(json.dumps({"claims": [], "extra": 1}))
        self.assertEqual(result["agent_failure_code"], "required_fields_mismatch")
        self.assertNotIn("agent_json_shape", result)


if __name__ == "__main__":
    unittest.main()
