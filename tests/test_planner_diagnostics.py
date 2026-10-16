"""Planner rejection codes and the exact documented probe, using offline HTTP only."""

import io
import json
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from deepdive_ai.agents.common import AgentFailureCode, AgentLimits, AgentOutputError, MeshJSON
from deepdive_ai.agents.planner import MeshPlannerAgent
from deepdive_ai.http_transport import HTTPResponse, _PinnedConnection
from deepdive_ai.models import ResearchQuestion
from test_agents import FakeMesh


class PlannerDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_fence_support_is_opt_in_and_does_not_change_other_agent_parsing(self):
        for role in ("planner", "drafter", "verifier"):
            llm = MeshJSON(FakeMesh('```json\n{"tasks":[]}\n```'), "offline-model", AgentLimits())
            with self.subTest(role=role), self.assertRaises(AgentOutputError) as error:
                await llm.ask(role, "instruction", {}, {})
            self.assertEqual(error.exception.code, AgentFailureCode.MALFORMED_JSON)

    async def test_raw_response_limit_applies_before_fence_removal(self):
        document = '{"tasks":[{"instruction":"safe"}]}'
        llm = MeshJSON(FakeMesh("```json\n" + document + "\n```"), "offline-model",
                       AgentLimits(max_response_chars=len(document)))
        with self.assertRaises(AgentOutputError) as error:
            await MeshPlannerAgent(llm).plan(ResearchQuestion("q", "question"))
        self.assertEqual(error.exception.code, AgentFailureCode.TEXT_LIMIT)

    async def test_fence_unwrapping_preserves_instruction_text_exactly(self):
        instruction = 'Find the literal ``` marker and Unicode \u03b1 in documentation'
        document = json.dumps({"tasks": [{"instruction": instruction}]})
        planner = MeshPlannerAgent(MeshJSON(FakeMesh("```json\n" + document + "\n```"),
                                            "offline-model", AgentLimits()))
        tasks = await planner.plan(ResearchQuestion("q", "question"))
        self.assertEqual(tasks[0].instruction, instruction)

    async def test_instruction_character_bound_is_in_prompt_and_still_enforced(self):
        for size in (160, 161):
            mesh = FakeMesh({"tasks": [{"instruction": "x" * size}]})
            planner = MeshPlannerAgent(MeshJSON(mesh, "offline-model", AgentLimits(max_tasks=1, max_text_chars=160)))
            if size == 160:
                self.assertEqual(len(await planner.plan(ResearchQuestion("q", "question"))), 1)
            else:
                with self.assertRaises(AgentOutputError) as error:
                    await planner.plan(ResearchQuestion("q", "question"))
                self.assertEqual(error.exception.code, AgentFailureCode.TEXT_LIMIT)
            self.assertIn("at most 160 characters", mesh.calls[0]["instruction"])

    async def test_duplicate_tasks_remain_rejected_with_allowlisted_code(self):
        planner = MeshPlannerAgent(MeshJSON(FakeMesh({"tasks": [{"instruction": "same"}] * 2}),
                                            "offline-model", AgentLimits(max_tasks=2)))
        with self.assertRaises(AgentOutputError) as error:
            await planner.plan(ResearchQuestion("q", "question"))
        self.assertEqual(error.exception.code, AgentFailureCode.DUPLICATE_TASKS)


class DocumentedPlannerProbeTests(unittest.TestCase):
    def setUp(self):
        source = Path("docs/phase10_live_validation.md").read_text(encoding="utf-8")
        script = re.search(r"\$meshJsonScript = @'\n(.*?)\n'@", source, re.S).group(1)
        self.namespace = {"__name__": "offline_document_probe"}
        exec(compile(script, "documented_mesh_json_probe", "exec"), self.namespace)
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("No real DNS")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No real connections"))):
            guard.start()
            self.addCleanup(guard.stop)

    def probe(self, content, *, finish="stop", execute=True):
        calls = []
        model = self.namespace["MODEL"]

        class Transport:
            async def send(self, request):
                calls.append(request)
                body = {"id": "private-id", "model": model,
                        "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
                return HTTPResponse(200, (("Content-Type", "application/json"),), json.dumps(body).encode())

        def factory():
            if not execute:
                raise AssertionError("Preflight must not construct a transport")
            return Transport()

        args = ["probe", "--confirm-spend-controls", "--accept-gateway-uncertainty",
                "--mesh-input-usd-per-million", "1", "--mesh-output-usd-per-million", "2"]
        with patch.dict("os.environ", {"MESH_API_KEY": "offline-key-fixture"}, clear=True), \
                patch.dict(self.namespace, {"PublicHTTPTransport": factory}), \
                patch("sys.argv", args + (["--execute"] if execute else [])), redirect_stdout(io.StringIO()) as output:
            status = self.namespace["main"]()
        for secret in ("offline-key-fixture", "private-id", "private-field", "sensitive-instruction"):
            self.assertNotIn(secret, output.getvalue())
        self.assertEqual(len(calls), 1 if execute else 0)
        return status, json.loads(output.getvalue())

    def test_http_200_rejection_conditions_are_distinguished_without_exposing_values(self):
        cases = (
            ("OK", "malformed_agent_json"),
            ('```json\n{"tasks": []}\n```', "empty_planner_tasks"),
            ('{"tasks":[],"tasks":[]}', "duplicate_json_keys"),
            ('{"tasks":NaN}', "nonfinite_json_value"),
            ("[]", "object_required"),
            ('{"private-field":[]}', "required_fields_mismatch"),
            ('{"tasks":"sensitive-instruction"}', "list_required"),
            ('{"tasks":[{},{}]}', "list_limit_exceeded"),
            ('{"tasks":[]}', "empty_planner_tasks"),
            ('{"tasks":[{"private-field":"sensitive-instruction"}]}', "required_fields_mismatch"),
            ('{"tasks":[{"instruction":""}]}', "invalid_text"),
            (json.dumps({"tasks": [{"instruction": "x" * 161}]}), "text_limit_exceeded"),
            ('{"tasks":[{"instruction":"\\ud800"}]}', "invalid_unicode"),
        )
        for content, code in cases:
            with self.subTest(code=code):
                status, result = self.probe(content)
                self.assertEqual(status, 1)
                self.assertEqual(result["http_status"], 200)
                self.assertFalse(result["agent_json_compatible"])
                self.assertEqual(result["failure_category"], "agent_contract_failure")
                self.assertEqual(result["agent_failure_code"], code)

    def test_valid_contract_and_offline_preflight_remain_successful(self):
        content = '{"tasks":[{"instruction":"Find the definition in authoritative documentation"}]}'
        status, result = self.probe(content)
        self.assertEqual(status, 0)
        self.assertTrue(result["agent_json_compatible"])
        self.assertEqual(result["task_count"], 1)
        status, result = self.probe(content, execute=False)
        self.assertEqual(status, 0)
        self.assertEqual(result["mode"], "preflight")

    def test_complete_standalone_json_fences_pass_real_probe_and_schema(self):
        document = '{"tasks":[{"instruction":"Find authoritative documentation"}]}'
        for content in (document, " \r\n" + document + "\t",
                        "```json\n" + document + "\n```",
                        "```\n" + document + "\n```",
                        " \r\n```json\r\n" + document + "\r\n```\t\r\n"):
            with self.subTest(content=content):
                status, result = self.probe(content)
                self.assertEqual(status, 0)
                self.assertTrue(result["agent_json_compatible"])
                self.assertEqual(result["attempts"], 1)
                self.assertEqual(result["task_count"], 1)

    def test_ambiguous_partial_and_unsupported_wrappers_are_not_repaired(self):
        document = '{"tasks":[{"instruction":"Find authoritative documentation"}]}'
        fenced = "```json\n" + document + "\n```"
        for content in ("Here is JSON:\n" + fenced, fenced + "\nExplanation",
                        fenced + "\n" + fenced, document + document,
                        "```json\n" + document, document[:-1],
                        "```json\n" + document[:-1] + "\n```",
                        "```javascript\n" + document + "\n```",
                        "```json " + document + "```", "~~~json\n" + document + "\n~~~"):
            with self.subTest(content=content):
                status, result = self.probe(content)
                self.assertEqual(status, 1)
                self.assertEqual(result["agent_failure_code"], "malformed_agent_json")

    def test_fenced_documents_keep_strict_json_and_planner_validation(self):
        for document, code in (
                ('{"tasks":[],"tasks":[]}', "duplicate_json_keys"),
                ('{"tasks":NaN}', "nonfinite_json_value"),
                ('{"tasks":[{}]}', "required_fields_mismatch"),
                ('{"tasks":[],"extra":0}', "required_fields_mismatch"),
                ('{"tasks":[{"instruction":""}]}', "invalid_text"),
                ('{"tasks":[{"instruction":"\\ud800"}]}', "invalid_unicode"),
                ('{"tasks":[{},{}]}', "list_limit_exceeded")):
            with self.subTest(code=code):
                status, result = self.probe("```json\n" + document + "\n```")
                self.assertEqual(status, 1)
                self.assertEqual(result["agent_failure_code"], code)

    def test_truncated_completion_fails_before_planner_json_acceptance(self):
        status, result = self.probe('{"tasks":[{"instruction":"safe"}]}', finish="length")
        self.assertEqual(status, 1)
        self.assertEqual(result["failure_category"], "http_protocol_failure")
        self.assertNotIn("agent_failure_code", result)

    def test_untrusted_failure_code_cannot_be_exposed(self):
        error = AgentOutputError("private exception", code="private-field")
        self.assertEqual(error.code, AgentFailureCode.INVALID_OUTPUT)
