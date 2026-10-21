"""Mesh validation sub-codes, finish reason and token counts in outcome and summary. Offline only."""

import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deepdive_ai.audit import MeshResponseClassification, RoutingRejectionCode
from deepdive_ai.http_transport import HTTPResponse, _PinnedConnection
from deepdive_ai.live import MESH_FINISH_REASONS, MESH_VALIDATION_CHECKS, run_live
from test_agents import URL, assessment, draft, plan
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport, SourceTransport

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("summarize_live_run", ROOT / "scripts" / "summarize_live_run.py")
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)

PRIVATE = "PRIVATE-PLANNER-TEXT-must-not-be-recorded"


class PlannerReply(MeshTransport):
    """Rewrites only the first (Planner) completion; later calls stay valid."""

    def __init__(self, mutate):
        super().__init__(plan(), draft, assessment)
        self.mutate = mutate

    async def send(self, request):
        response = await super().send(request)
        if len(self.calls) != 1:
            return response
        data = json.loads(response.body)
        headers = list(response.headers)
        changed = self.mutate(data, headers)
        return HTTPResponse(200, tuple(headers), json.dumps(data if changed is None else changed).encode())


class DetailsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("real DNS forbidden")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("real HTTP forbidden"))):
            guard.start()
            self.addCleanup(guard.stop)
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def run_planner(self, mutate):
        mesh = PlannerReply(mutate)
        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result = await run_live(replace(OPTIONS), execute=True, environment=ENV, output_root=self.root,
                                    mesh_transport=mesh, brave_transport=SearchTransport((URL,)),
                                    source_transport=SourceTransport())
        folder = self.root / result["run_id"]
        self.assertEqual(json.loads((folder / "outcome.json").read_text()), result)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["attempts"], {"mesh": 1, "brave": 0, "source": 0})
        rendered = summary.build(None, result, self.root)
        persisted = (folder / "outcome.json").read_text() + (folder / "audit.jsonl").read_text()
        for private in (PRIVATE, *ENV.values(), "private-response-id", "Traceback", "Mesh returned"):
            self.assertNotIn(private, persisted)
            self.assertNotIn(private, rendered)
        return result, rendered

    def assert_last(self, result, rendered, finish, prompt, completion, total, reasoning):
        self.assertEqual(result["mesh_last_completion"],
                         {"finish_reason": finish, "prompt_tokens": prompt, "completion_tokens": completion,
                          "total_tokens": total, "reasoning_tokens": reasoning})
        self.assertIn(f"| Last finish reason | {finish} |", rendered)
        for name, value in (("prompt", prompt), ("completion", completion), ("total", total), ("reasoning", reasoning)):
            self.assertIn(f"| Last {name} tokens | {'none' if value is None else value} |", rendered)

    async def test_empty_content_with_length_finish_reason(self):
        def mutate(data, headers):
            data["choices"][0].update(finish_reason="length", message={"role": "assistant", "content": ""})
            data["usage"] = {"prompt_tokens": 412, "completion_tokens": 1024, "total_tokens": 1436,
                             "completion_tokens_details": {"reasoning_tokens": 1024}}

        result, rendered = await self.run_planner(mutate)
        self.assertEqual(result["failure_category"], "http_protocol_failure")
        self.assertEqual(result["failure_details"]["mesh_completion_check"], "finish_reason_length")
        self.assertNotIn("mesh", result["diagnostics"])  # the runner's usage gate passed
        self.assertIn("| Mesh completion check | finish_reason_length |", rendered)
        self.assert_last(result, rendered, "length", 412, 1024, 1436, 1024)

    async def test_missing_usage(self):
        def mutate(data, headers):
            data["choices"][0]["message"]["content"] = PRIVATE
            del data["usage"]

        result, rendered = await self.run_planner(mutate)
        self.assertEqual(result["failure_category"], "response_validation")
        self.assertEqual(result["failure_details"], {"category": "response_validation", "stage": None, "http_status": 200})
        self.assertEqual(result["diagnostics"]["mesh"]["mesh_validation_failure"], "usage_object")
        self.assertIn("| Validation failure | usage_object |", rendered)
        self.assertIn("| Response classification | normal_non_streaming |", rendered)
        self.assert_last(result, rendered, "stop", None, None, None, None)

    async def test_missing_choices(self):
        def mutate(data, headers):
            del data["choices"]

        result, rendered = await self.run_planner(mutate)
        self.assertEqual(result["failure_category"], "http_protocol_failure")
        self.assertEqual(result["failure_details"]["mesh_completion_check"], "choices_invalid")
        self.assertIn("| Mesh completion check | choices_invalid |", rendered)
        self.assert_last(result, rendered, "missing", 10, 20, 30, None)

    async def test_each_runner_check_reports_its_own_sub_code(self):
        def usage(**values):
            def mutate(data, headers):
                data["usage"] = values
            return mutate

        def model(data, headers):
            data["model"] = "another-model"

        def no_fallback(data, headers):
            headers[:] = [h for h in headers if h[0] != "X-Mesh-Routing-Fallback"]

        def top_level_reasoning_filtered(data, headers):
            # Passes the runner gate; the client then rejects the content_filter finish reason.
            data["usage"] = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "reasoning_tokens": 7}
            data["choices"][0]["finish_reason"] = "content_filter"

        cases = (
            (usage(prompt_tokens=10, completion_tokens=20, total_tokens=530,
                   completion_tokens_details={"reasoning_tokens": 500}), "token_total_matches", (10, 20, 530, 500)),
            (usage(prompt_tokens=10, completion_tokens=1500, total_tokens=1510), "output_limit_satisfied",
             (10, 1500, 1510, None)),
            (usage(prompt_tokens=10, completion_tokens="20", total_tokens=30), "token_fields_valid", (10, None, 30, None)),
            (top_level_reasoning_filtered, None, (10, 20, 30, 7)),
            (model, "model_matches", (10, 20, 30, None)),
            (no_fallback, "routing_metadata_valid", (10, 20, 30, None)),
        )
        for mutate, check, tokens in cases:
            with self.subTest(check=check):
                result, rendered = await self.run_planner(mutate)
                if check:
                    self.assertEqual(result["failure_category"], "response_validation")
                    self.assertEqual(result["diagnostics"]["mesh"]["mesh_validation_failure"], check)
                    self.assertIn(f"| Validation failure | {check} |", rendered)
                    finish = "stop"
                else:
                    self.assertEqual(result["failure_details"]["mesh_completion_check"], "finish_reason_content_filter")
                    finish = "content_filter"
                self.assert_last(result, rendered, finish, *tokens)
        result, rendered = await self.run_planner(no_fallback)
        self.assertIn("| Routing rejection codes | fallback_missing |", rendered)

    async def test_unparseable_reply_records_no_completion_metadata(self):
        result, rendered = await self.run_planner(lambda data, headers: PRIVATE)
        self.assertEqual(result["diagnostics"]["mesh"]["mesh_validation_failure"], "root_object")
        self.assertNotIn("mesh_last_completion", result)
        self.assertNotIn("Last finish reason", rendered)


class SummaryAllowlistTests(unittest.TestCase):
    def test_allowlists_match_application_values(self):
        self.assertEqual(summary.MESH_VALIDATION_CHECKS, set(MESH_VALIDATION_CHECKS))
        self.assertEqual(summary.ROUTING_REJECTION_CODES, {code.value for code in RoutingRejectionCode})
        self.assertEqual(summary.MESH_CLASSIFICATIONS, {value.value for value in MeshResponseClassification})
        self.assertEqual(summary.FINISH_REASON_LABELS, set(MESH_FINISH_REASONS) | {"other", "missing"})

    def test_unlisted_values_and_bad_counts_are_rejected(self):
        result = {"mode": "live", "status": "failed", "run_id": None,
                  "diagnostics": {"mesh": {"category": "response_validation", "mesh_validation_failure": PRIVATE,
                                           "routing_rejection_codes": ["fallback_missing", PRIVATE, ["x"]],
                                           "mesh_response_classification": "<b>x</b>"}},
                  "mesh_last_completion": {"finish_reason": PRIVATE, "prompt_tokens": -1, "completion_tokens": True,
                                           "total_tokens": 10 ** 12, "reasoning_tokens": "12"}}
        rendered = summary.build(None, result, Path("missing-reports"))
        self.assertIn("| Validation failure | invalid |", rendered)
        self.assertIn("| Routing rejection codes | fallback_missing, invalid, invalid |", rendered)
        self.assertIn("| Response classification | invalid |", rendered)
        self.assertIn("| Last finish reason | invalid |", rendered)
        for name in ("prompt", "completion", "total", "reasoning"):
            self.assertIn(f"| Last {name} tokens | invalid |", rendered)
        self.assertNotIn(PRIVATE, rendered)
        self.assertNotIn("<b>", rendered)


if __name__ == "__main__":
    unittest.main()
