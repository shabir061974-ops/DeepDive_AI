"""Fixed routing rejection codes; acceptance, persistence and redaction offline."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deepdive_ai.audit import AuditEvent, AuditKind, RoutingRejectionCode
from deepdive_ai.http_transport import HTTPResponse, _PinnedConnection
from deepdive_ai.live import run_live
from deepdive_ai.mesh import mesh_identity_checks, mesh_routing_rejection_codes
from test_agents import URL, assessment, draft, plan
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport, SourceTransport


PRIVATE = "private-routing-value-that-must-not-be-recorded"
BASE = (("Content-Type", "application/json"), ("X-Mesh-Version", "2026-08"))
SAFE = BASE + (("X-Mesh-Routing-Fallback", "false"),)
CASES = (
    (BASE, ("fallback_missing",)),
    (BASE + (("X-Mesh-Routing-Fallback", "true"),), ("fallback_true",)),
    (BASE + (("X-Mesh-Routing-Fallback", PRIVATE),), ("fallback_invalid",)),
    (SAFE + (("X-Mesh-Routing-Model", PRIVATE),), ("routing_model_present",)),
    (SAFE + (("x-mesh-routing-fallback", "false"),), ("fallback_invalid", "duplicate_metadata")),
    ((("X-Mesh-Version", "2026-13"), ("X-Mesh-Routing-Fallback", "false")), ("invalid_version",)),
    (SAFE + (("X-Mesh-Routing-Attempts", "0"),), ("invalid_attempts",)),
    (SAFE + (("X-Mesh-Routing-Model", PRIVATE + "\t"),), ("routing_model_present", "invalid_metadata_value")),
    (BASE + (("X-Cache", "HIT"),), ("fallback_missing", "cached_response_unverified")),
    ((("Content-Type", "text/event-stream"), ("X-Mesh-Routing-Fallback", "false")),
     ("unexpected_streaming",)),
    (SAFE + (("X-Cache", PRIVATE),), ("invalid_cache_metadata",)),
    (SAFE + (("X-Cache", "HIT"),), ("cached_response_unverified", "contradictory_metadata")),
)


class RoutingDiagnosticTests(unittest.TestCase):
    def test_each_category_and_existing_rejection_decision(self):
        covered = set()
        for headers, expected in CASES:
            with self.subTest(expected=expected):
                response = HTTPResponse(200, headers)
                before = mesh_identity_checks(response, "google/gemini-2.5-flash", "google/gemini-2.5-flash")
                codes = mesh_routing_rejection_codes(response)
                self.assertEqual(codes, expected)
                self.assertTrue(before["model_matches"])
                self.assertFalse(before["routing_metadata_valid"])
                self.assertEqual(mesh_identity_checks(response, "google/gemini-2.5-flash", "google/gemini-2.5-flash"), before)
                self.assertNotIn(PRIVATE, json.dumps(codes))
                covered.update(codes)
        self.assertEqual(covered, set(RoutingRejectionCode))

    def test_accepted_metadata_remains_accepted_without_rejection_codes(self):
        for headers in (SAFE, (("x-mesh-routing-fallback", "false"),),
                        SAFE + (("X-Mesh-Routing-Attempts", "2"),),
                        (("X-Mesh-Version", "2025-01"), ("X-Mesh-Routing-Fallback", "false"))):
            response = HTTPResponse(200, headers)
            self.assertEqual(mesh_routing_rejection_codes(response), ())
            self.assertTrue(all(mesh_identity_checks(response, "offline-model", "offline-model").values()))
        response = HTTPResponse(200, SAFE)
        self.assertTrue(all(mesh_identity_checks(response, "google/gemini-2.5-flash", "gemini-2.5-flash").values()))
        self.assertFalse(mesh_identity_checks(response, "google/gemini-2.5-flash", PRIVATE)["model_matches"])

    def test_overlapping_malformed_metadata_is_bounded_and_never_echoed(self):
        malformed = (BASE + (("X-Mesh-Routing-Fallback", PRIVATE + "\r\n"),),
                     SAFE + (("X-Mesh-Version", "2026-08"),),
                     SAFE + (("X-Mesh-Routing-Attempts", "1"), ("x-mesh-routing-attempts", "2")),
                     (("X-Mesh-Routing-Fallback", "false"), ("X-Mesh-Version", None)),
                     SAFE + (("X-Mesh-Routing-Model", {PRIVATE: PRIVATE}),),
                     BASE + (("X-Mesh-Routing-Fallback", "true"), ("x-mesh-routing-fallback", "false")))
        for headers in malformed:
            codes = mesh_routing_rejection_codes(HTTPResponse(200, headers))
            self.assertTrue(codes)
            self.assertLessEqual(len(codes), 8)
            self.assertEqual(len(codes), len(set(codes)))
            self.assertTrue(set(codes) <= set(RoutingRejectionCode))
            self.assertFalse(mesh_identity_checks(HTTPResponse(200, headers), "offline-model", "offline-model")["routing_metadata_valid"])
            self.assertNotIn(PRIVATE, json.dumps(codes))

    def test_audit_model_rejects_arbitrary_or_misplaced_diagnostics(self):
        event = AuditEvent("run", "q", AuditKind.FAILED, 0, datetime.now(timezone.utc))
        accepted = replace(event, routing_rejection_codes=(RoutingRejectionCode.FALLBACK_MISSING,))
        self.assertEqual(accepted.routing_rejection_codes, ("fallback_missing",))
        for codes in ((PRIVATE,), [RoutingRejectionCode.FALLBACK_TRUE],
                      (RoutingRejectionCode.FALLBACK_TRUE,) * 2):
            with self.assertRaises(ValueError):
                replace(event, routing_rejection_codes=codes)
        with self.assertRaises(ValueError):
            replace(accepted, kind=AuditKind.COMPLETED)


class PersistedRoutingDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_categories_reach_outcome_and_existing_failure_audit(self):
        Path("artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir="artifacts") as temporary, \
                patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")), \
                patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No live HTTP")):
            for headers, expected in CASES:
                class Transport(MeshTransport):
                    async def send(self, request):
                        response = await super().send(request)
                        return HTTPResponse(200, headers, response.body)

                result = await run_live(OPTIONS, execute=True, environment=ENV, output_root=Path(temporary),
                    mesh_transport=Transport(plan(), draft, assessment),
                    brave_transport=SearchTransport((URL,)), source_transport=SourceTransport())
                folder = Path(temporary) / result["run_id"]
                persisted = json.loads((folder / "outcome.json").read_text())
                audit = [json.loads(row) for row in (folder / "audit.jsonl").read_text().splitlines()]
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["attempts"], {"mesh": 1, "brave": 0, "source": 0})
                self.assertEqual(result["diagnostics"]["mesh"]["routing_rejection_codes"], list(expected))
                self.assertEqual(persisted, result)
                self.assertEqual([row["kind"] for row in audit], ["started", "failed"])
                self.assertEqual(audit[-1]["routing_rejection_codes"], list(expected))
                self.assertFalse((folder / "report.json").exists())
                serialized = json.dumps(result) + json.dumps(audit)
                for secret in (PRIVATE, *ENV.values(), "private-response-id"):
                    self.assertNotIn(secret, serialized)
