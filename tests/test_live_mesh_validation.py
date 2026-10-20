"""Second-call Mesh metadata failures through the real offline workflow."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deepdive_ai.http_transport import HTTPResponse, _PinnedConnection
from deepdive_ai.live import run_live
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport, SourceTransport
from test_agents import URL, assessment, draft, plan


PRIVATE = "private-offline-mesh-value"
CHECK_NAMES = {"response_json_valid", "root_object", "model_matches", "routing_metadata_valid", "usage_object",
               "token_fields_valid", "token_total_matches", "output_limit_satisfied"}


class LiveMeshValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No live sockets"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def run_second_response(self, mutate, *, requested_model=OPTIONS.model, second_headers=None):
        class Transport(MeshTransport):
            async def send(self, request):
                response = await super().send(request)
                data = json.loads(response.body)
                data["model"] = requested_model
                if len(self.calls) == 2:
                    data = mutate(data)
                body = data if isinstance(data, bytes) else json.dumps(data).encode()
                headers = second_headers if len(self.calls) == 2 and second_headers is not None else response.headers
                return HTTPResponse(200, headers, body)

        mesh = Transport(plan(), draft, assessment)
        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result = await run_live(replace(OPTIONS, model=requested_model), execute=True, environment=ENV, output_root=self.root,
                mesh_transport=mesh, brave_transport=SearchTransport((URL,)), source_transport=SourceTransport())
        roles = [json.loads(json.loads(request.body)["messages"][0]["content"])["role"] for request in mesh.calls]
        self.assertEqual(roles[:2], ["planner", "drafter"])
        self.assertTrue(all(json.loads(request.body)["model"] == requested_model for request in mesh.calls))
        folder = self.root / result["run_id"]
        self.assertEqual(json.loads((folder / "outcome.json").read_text()), result)
        audit = [json.loads(line)["kind"] for line in (folder / "audit.jsonl").read_text().splitlines()]
        safe_output = json.dumps(result) + (folder / "audit.jsonl").read_text()
        for secret in (PRIVATE, *ENV.values(), "private-response-id"):
            self.assertNotIn(secret, safe_output)
        return result, audit, folder

    async def assert_metadata_rejected(self, mutate, failed_check):
        result, audit, folder = await self.run_second_response(mutate)
        self.assertEqual(result["attempts"], {"mesh": 2, "brave": 1, "source": 1})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_category"], "response_validation")
        self.assertNotIn("agent_failure_code", result)
        details = result["diagnostics"]["mesh"]
        self.assertEqual(details["http_status"], 200)
        checks = details["mesh_validation_checks"]
        self.assertEqual(set(checks), CHECK_NAMES)
        self.assertTrue(all(type(value) is bool for value in checks.values()))
        self.assertFalse(checks[failed_check])
        self.assertEqual(audit, ["started", "planned", "researched", "failed"])
        self.assertFalse((folder / "report.json").exists())
        return checks

    async def test_model_usage_shape_total_and_output_failures_remain_distinct(self):
        for changes, failed in (
                ({"model": PRIVATE}, "model_matches"),
                ({"usage": None}, "usage_object"),
                ({"usage": {"prompt_tokens": 10, "completion_tokens": 20}}, "token_fields_valid"),
                ({"usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 31}}, "token_total_matches"),
                ({"usage": {"prompt_tokens": 10, "completion_tokens": 1025, "total_tokens": 1035}}, "output_limit_satisfied")):
            with self.subTest(failed=failed):
                await self.assert_metadata_rejected(lambda data: {**data, **changes}, failed)

    async def test_invalid_numeric_types_and_ranges_still_fail_closed(self):
        for invalid in (True, 20.0, "20", -1, 1_000_000_001, None):
            with self.subTest(invalid=invalid):
                await self.assert_metadata_rejected(lambda data: {**data, "usage": {
                    "prompt_tokens": 10, "completion_tokens": invalid, "total_tokens": 30}}, "token_fields_valid")

    async def test_malformed_json_and_wrong_root_do_not_expose_response_data(self):
        for mutate, failed in ((lambda data: b"not-json-" + PRIVATE.encode(), "response_json_valid"),
                               (lambda data: b"\xff", "response_json_valid"),
                               (lambda data: [PRIVATE], "root_object")):
            with self.subTest(failed=failed):
                await self.assert_metadata_rejected(mutate, failed)

    async def test_output_cap_boundary_and_optional_metadata_keep_existing_acceptance(self):
        result, audit, folder = await self.run_second_response(lambda data: {**data, "extra": PRIVATE,
            "usage": {"prompt_tokens": 10, "completion_tokens": 1024, "total_tokens": 1034}})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["report_status"], "verified")
        self.assertEqual(result["attempts"]["mesh"], 3)
        self.assertNotIn("mesh", result["diagnostics"])
        self.assertEqual(audit[-1], "completed")
        self.assertTrue((folder / "report.json").exists())

    async def test_valid_metadata_does_not_bypass_drafter_json_validation(self):
        def fenced(data):
            data["choices"][0]["message"]["content"] = 'Here is the JSON:\n```json\n{"claims":[]}\n```'
            return data

        result, audit, folder = await self.run_second_response(fenced)
        self.assertEqual(result["failure_category"], "agent_contract_failure")
        self.assertEqual(result["agent_failure_code"], "malformed_agent_json")
        self.assertEqual(result["agent_json_shape"], "embedded_fence")
        self.assertNotIn("mesh", result["diagnostics"])
        self.assertEqual(audit[-1], "failed")
        self.assertFalse((folder / "report.json").exists())

    async def test_public_identifiers_are_displayed_without_becoming_accepted_aliases(self):
        for identifier in ("google/gemini-2.5-flash", "gemini-2.5-flash"):
            with self.subTest(identifier=identifier):
                result, _, folder = await self.run_second_response(lambda data: {**data, "model": identifier})
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["failure_category"], "response_validation")
                self.assertEqual(result["attempts"], {"mesh": 2, "brave": 1, "source": 1})
                metadata = result["diagnostics"]["mesh"]
                self.assertTrue(metadata["model_field_present"])
                self.assertEqual(metadata["model_field_type"], "string")
                self.assertEqual(metadata["public_model_identifier"], identifier)
                self.assertFalse(metadata["mesh_validation_checks"]["model_matches"])
                self.assertFalse((folder / "report.json").exists())

    async def test_unrecognized_model_strings_are_redacted_without_normalization(self):
        for value in (PRIVATE, "Google/gemini-2.5-flash", "gemini-2.5-flash ",
                      "google/gemini-2.5-flash-001", "x" * 20_000, "private\ncredential"):
            with self.subTest(length=len(value)):
                result, _, _ = await self.run_second_response(lambda data: {**data, "model": value})
                metadata = result["diagnostics"]["mesh"]
                self.assertTrue(metadata["model_field_present"])
                self.assertEqual(metadata["model_field_type"], "string")
                self.assertIsNone(metadata["public_model_identifier"])
                self.assertNotIn(value, json.dumps(result))

    async def test_missing_and_invalid_model_types_record_only_fixed_labels(self):
        for value, label in ((None, "null"), (True, "boolean"), (1, "number"), (1.5, "number"),
                             ([PRIVATE], "array"), ({PRIVATE: PRIVATE}, "object")):
            with self.subTest(label=label):
                result, _, _ = await self.run_second_response(lambda data: {**data, "model": value})
                metadata = result["diagnostics"]["mesh"]
                self.assertTrue(metadata["model_field_present"])
                self.assertEqual(metadata["model_field_type"], label)
                self.assertIsNone(metadata["public_model_identifier"])
                self.assertEqual(result["status"], "failed")
        def missing(data):
            data.pop("model")
            return data
        result, _, _ = await self.run_second_response(missing)
        metadata = result["diagnostics"]["mesh"]
        self.assertFalse(metadata["model_field_present"])
        self.assertEqual(metadata["model_field_type"], "missing")
        self.assertIsNone(metadata["public_model_identifier"])

    async def test_undecodable_document_does_not_claim_model_field_absence(self):
        result, _, _ = await self.run_second_response(lambda data: b"invalid-" + PRIVATE.encode())
        metadata = result["diagnostics"]["mesh"]
        self.assertFalse(metadata["model_field_present"])
        self.assertEqual(metadata["model_field_type"], "unavailable")
        self.assertIsNone(metadata["public_model_identifier"])

    async def test_support_confirmed_pair_is_accepted_without_using_display_allowlist(self):
        requested = "google/gemini-2.5-flash"
        for returned in (requested, "gemini-2.5-flash"):
            result, _, _ = await self.run_second_response(lambda data: {**data, "model": returned},
                                                         requested_model=requested)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["report_status"], "verified")
            self.assertNotIn("mesh", result["diagnostics"])

    async def test_routing_failures_are_persisted_without_raw_metadata(self):
        base = (("Content-Type", "application/json"), ("X-Mesh-Version", "2026-08"))
        for extra in ((("X-Mesh-Routing-Fallback", "true"),),
                      (("X-Mesh-Routing-Fallback", "false"), ("X-Mesh-Routing-Model", PRIVATE)),
                      (("X-Mesh-Routing-Fallback", "false"), ("x-mesh-routing-fallback", PRIVATE))):
            result, audit, folder = await self.run_second_response(lambda data: data, second_headers=base + extra)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failure_category"], "response_validation")
            self.assertFalse(result["diagnostics"]["mesh"]["mesh_validation_checks"]["routing_metadata_valid"])
            self.assertEqual(result["attempts"], {"mesh": 2, "brave": 1, "source": 1})
            self.assertEqual(audit[-1], "failed")
            self.assertFalse((folder / "report.json").exists())
