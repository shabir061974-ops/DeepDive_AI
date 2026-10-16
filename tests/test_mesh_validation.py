"""Structural diagnostics use authored fixtures, not reconstructed live responses."""

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from deepdive_ai.connectivity import run_connectivity
from deepdive_ai.http_transport import HTTPResponse, _PinnedConnection
from test_mesh_only import KEY, OPTIONS, MeshEnvironment, Transport, completion


class MeshValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No live connections"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def run_response(self, response, *, options=OPTIONS):
        mesh = Transport(response)
        brave = Transport(RuntimeError("Brave forbidden"))
        report = await run_connectivity(options, execute=True, environment=MeshEnvironment(MESH_API_KEY=KEY),
                                        mesh_transport=mesh, brave_transport=brave)
        self.assertEqual(len(mesh.calls), 1)
        self.assertEqual(brave.calls, [])
        self.assertEqual(report["brave"]["attempts"], 0)
        self.assertEqual(report["mesh"]["http_status"], 200)
        serialized = json.dumps(report)
        for private in (KEY, "private-fixture-id", "private-model", "private-version", "private-answer"):
            self.assertNotIn(private, serialized)
        return report["mesh"]

    def reply(self, text, *, finish="stop", **extra):
        return [{"finish_reason": finish, "message": {"role": "assistant", "content": text, **extra}}]

    async def test_reported_usage_does_not_identify_failed_condition(self):
        # Three different authored responses reproduce the reported count/category.
        # None is asserted to be the operator's unknown live response.
        usage = {"prompt_tokens": 4, "completion_tokens": 27, "total_tokens": 31}
        cases = ((completion(usage=usage, choices=self.reply("private-answer")), OPTIONS, "reply_matches"),
                 (completion(usage=usage, model="private-model"), OPTIONS, "model_matches"),
                 (completion(usage=usage), replace(OPTIONS, mesh_version="2026-08"), "expected_version_matches"))
        for response, options, failed in cases:
            with self.subTest(failed=failed):
                result = await self.run_response(response, options=options)
                self.assertEqual(result["failure_category"], "response_validation")
                self.assertEqual(result["token_usage"], usage)
                self.assertEqual(result["diagnostics"]["failed_checks"], [failed])
                checks = result["diagnostics"]["validation_checks"]
                self.assertTrue(checks["response_schema_compatible"])
                self.assertTrue(checks["output_limit_satisfied"])

    async def test_all_failures_are_reported_without_short_circuit_or_raw_values(self):
        headers = (("Content-Type", "application/json"), ("X-Mesh-Version", "private-version"))
        fixture = completion(model="private-model", choices=self.reply("private-answer"),
                             usage={"prompt_tokens": 4, "completion_tokens": 65, "total_tokens": 69})
        result = await self.run_response(HTTPResponse(200, headers, fixture.body),
                                         options=replace(OPTIONS, mesh_version="2026-08"))
        self.assertEqual(result["diagnostics"]["failed_checks"], [
            "model_matches", "expected_version_matches", "reply_matches", "output_limit_satisfied"])
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["estimated_cost_usd"])

    async def test_success_reports_each_checked_condition_and_unchecked_version(self):
        result = await self.run_response(completion())
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["diagnostics"]["failed_checks"], [])
        self.assertEqual(result["diagnostics"]["validation_checks"], {
            "response_schema_compatible": True, "model_matches": True, "expected_version_matches": None,
            "reply_matches": True, "output_limit_satisfied": True})

    async def test_exact_reply_rule_preserves_whitespace_tolerance_and_rejects_near_matches(self):
        for text in ("OK", " \r\nOK\t", "ok", "OK.", '"OK"', "OK private-answer"):
            result = await self.run_response(completion(choices=self.reply(text)))
            matches = text.strip() == "OK"
            self.assertEqual(result["response_compatible"], matches)
            self.assertEqual(result["diagnostics"]["validation_checks"]["reply_matches"], matches)

    async def test_missing_invalid_usage_does_not_claim_output_cap_was_verified(self):
        for usage in (None, {}, {"prompt_tokens": 4, "completion_tokens": 27, "total_tokens": 99},
                      {"prompt_tokens": True, "completion_tokens": 27, "total_tokens": 28}):
            result = await self.run_response(completion(usage=usage))
            self.assertEqual(result["status"], "passed")  # Existing policy, not a billing pass.
            self.assertIsNone(result["diagnostics"]["validation_checks"]["output_limit_satisfied"])
            self.assertIsNone(result["token_usage"])
            self.assertIsNone(result["estimated_cost_usd"])
            self.assertEqual(result["cost_status"], "usage_unavailable")

    async def test_optional_metadata_and_usage_details_remain_ignored(self):
        result = await self.run_response(completion(
            object="chat.completion", created=123, system_fingerprint="private-answer",
            usage={"prompt_tokens": 4, "completion_tokens": 27, "total_tokens": 31,
                   "completion_tokens_details": {"reasoning_tokens": 25}, "extra": "private-answer"}))
        self.assertEqual(result["status"], "passed")
        self.assertEqual(set(result["token_usage"]), {"prompt_tokens", "completion_tokens", "total_tokens"})

    async def test_unsupported_or_incomplete_schema_does_not_claim_schema_success(self):
        cases = (completion(choices=self.reply(None)), completion(choices=self.reply("")),
                 completion(choices=self.reply(" \t\n")),
                 completion(choices=self.reply([{"type": "text", "text": "OK"}])),
                 completion(choices=self.reply("OK", finish="length")),
                 completion(choices=self.reply("OK", finish="content_filter")),
                 completion(choices=self.reply("OK", finish=None)),
                 completion(choices=self.reply("OK", tool_calls=[{"private": "private-answer"}])),
                 completion(choices=[]), completion(id=""),
                 HTTPResponse(200, (("Content-Type", "application/json"),), b'{"id":"a","id":"b"}'))
        for response in cases:
            result = await self.run_response(response)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failure_category"], "http_protocol_failure")
            self.assertNotIn("validation_checks", result["diagnostics"])
            self.assertIsNone(result["token_usage"])
            self.assertIsNone(result["estimated_cost_usd"])

    async def test_token_cap_boundary_and_expected_version_success(self):
        fixture = completion(usage={"prompt_tokens": 4, "completion_tokens": 64, "total_tokens": 68})
        response = HTTPResponse(200, fixture.headers + (("X-Mesh-Version", "2026-08"),), fixture.body)
        result = await self.run_response(response, options=replace(OPTIONS, mesh_version="2026-08"))
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["diagnostics"]["validation_checks"]["expected_version_matches"])
        self.assertTrue(result["diagnostics"]["validation_checks"]["output_limit_satisfied"])
