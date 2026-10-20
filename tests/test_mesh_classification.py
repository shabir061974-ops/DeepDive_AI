"""Authored cache/streaming fixtures; no real provider responses or network."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deepdive_ai.audit import AuditEvent, AuditKind, MeshResponseClassification as Kind
from deepdive_ai.connectivity import run_connectivity
from deepdive_ai.http_transport import HTTPResponse, _PinnedConnection
from deepdive_ai.live import run_live
from deepdive_ai.mesh import (MeshAPIClient, MeshError, MeshRequest, MeshSettings,
                             classify_mesh_response, mesh_identity_checks, mesh_routing_rejection_codes)
from test_agents import URL, assessment, draft, plan
from test_live import ENV, OPTIONS as LIVE_OPTIONS, MeshTransport, SearchTransport, SourceTransport
from test_mesh_only import KEY, OPTIONS, Transport, completion


PRIVATE = "private-cache-header-value-do-not-retain"
BASE = (("Content-Type", "application/json"), ("X-Mesh-Version", "2026-08"))
SAFE = BASE + (("X-Mesh-Routing-Fallback", "false"),)
CASES = (
    (SAFE, Kind.NORMAL, ()),
    (SAFE + (("X-Cache", "MISS"),), Kind.NORMAL, ()),
    (BASE, Kind.UNKNOWN, ("fallback_missing",)),
    (BASE + (("X-Cache", "MISS"),), Kind.UNKNOWN, ("fallback_missing",)),
    (BASE + (("x-cache", "HIT"),), Kind.CACHED,
     ("fallback_missing", "cached_response_unverified")),
    (SAFE + (("X-Cache", "HIT"),), Kind.INVALID,
     ("cached_response_unverified", "contradictory_metadata")),
    ((("Content-Type", "text/event-stream; charset=utf-8"),), Kind.STREAMING,
     ("fallback_missing", "unexpected_streaming")),
    (SAFE + (("X-Cache", PRIVATE),), Kind.INVALID, ("invalid_cache_metadata",)),
)


class ClassificationTests(unittest.TestCase):
    def test_fixed_classifications_and_rejections(self):
        for headers, kind, codes in CASES:
            response = HTTPResponse(200, headers)
            with self.subTest(kind=kind, codes=codes):
                self.assertEqual(classify_mesh_response(response).classification, kind)
                self.assertEqual(mesh_routing_rejection_codes(response), codes)
                checks = mesh_identity_checks(response, OPTIONS_MODEL, OPTIONS_MODEL)
                self.assertEqual(checks["routing_metadata_valid"], not codes)
                self.assertTrue(checks["model_matches"])

    def test_duplicate_conflicting_and_malformed_cache_values_reject(self):
        for extra in (
            (("X-Cache", "HIT"), ("x-cache", "HIT")),
            (("X-Cache", "HIT"), ("x-cache", "MISS")),
            *((('X-Cache', value),) for value in (
                None, True, 12, [], {}, "", "hit", " HIT", "HIT ", "HIT,MISS", PRIVATE + "\r\n")),
        ):
            response = HTTPResponse(200, SAFE + extra)
            self.assertEqual(classify_mesh_response(response).classification, Kind.INVALID)
            self.assertIn("invalid_cache_metadata", mesh_routing_rejection_codes(response))
            self.assertFalse(mesh_identity_checks(response, OPTIONS_MODEL, OPTIONS_MODEL)["routing_metadata_valid"])

    def test_cache_cannot_hide_fallback_or_model_substitution(self):
        for routing in ((("X-Mesh-Routing-Fallback", "true"),),
                        (("X-Mesh-Routing-Model", OPTIONS_MODEL),),
                        (("X-Mesh-Routing-Attempts", "1"),)):
            response = HTTPResponse(200, BASE + (("X-Cache", "HIT"),) + routing)
            self.assertEqual(classify_mesh_response(response).classification, Kind.INVALID)
            self.assertIn("contradictory_metadata", mesh_routing_rejection_codes(response))
            self.assertFalse(mesh_identity_checks(response, OPTIONS_MODEL, PRIVATE)["model_matches"])

    def test_streaming_and_invalid_content_metadata_cannot_be_hidden(self):
        for content in ((("Content-Type", "text/event-stream"),),
                        (("Content-Type", "application/json"), ("content-type", "text/event-stream")),
                        (("Content-Type", "application/json;" + PRIVATE + "\r\n"),),
                        (("Content-Type", None),)):
            response = HTTPResponse(200, content + (("X-Cache", "HIT"),))
            self.assertTrue(classify_mesh_response(response).rejection_codes)
            self.assertFalse(mesh_identity_checks(response, OPTIONS_MODEL, OPTIONS_MODEL)["routing_metadata_valid"])

    def test_cache_does_not_authorize_missing_or_old_alias_version(self):
        for version in (None, "2026-07", "invalid"):
            headers = (("X-Cache", "HIT"),)
            if version is not None:
                headers += (("X-Mesh-Version", version),)
            checks = mesh_identity_checks(HTTPResponse(200, headers), OPTIONS_MODEL, "gemini-2.5-flash")
            self.assertFalse(checks["model_matches"])
            self.assertFalse(checks["routing_metadata_valid"])

    def test_audit_only_accepts_fixed_failure_classifications(self):
        event = AuditEvent("run", "question", AuditKind.FAILED, 0, datetime.now(timezone.utc))
        for kind in Kind:
            self.assertEqual(replace(event, mesh_response_classification=kind).mesh_response_classification, kind)
        for kind in (PRIVATE, "cached_response", {}, True):
            with self.assertRaises(ValueError):
                replace(event, mesh_response_classification=kind)
        with self.assertRaises(ValueError):
            replace(event, kind=AuditKind.COMPLETED, mesh_response_classification=Kind.CACHED)


OPTIONS_MODEL = "google/gemini-2.5-flash"


class ClassificationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No live HTTP"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def test_client_and_connectivity_share_decisions_and_safe_diagnostics(self):
        for headers, kind, codes in CASES:
            response = HTTPResponse(200, headers, completion().body)
            transport = Transport(response)
            client = MeshAPIClient(MeshSettings(KEY), transport)
            if codes:
                with self.assertRaises(MeshError):
                    await client.complete(MeshRequest(OPTIONS_MODEL, "Offline fixture"))
            else:
                self.assertEqual((await client.complete(MeshRequest(OPTIONS_MODEL, "Offline fixture"))).text, "OK")
            report = await run_connectivity(OPTIONS, execute=True, environment={"MESH_API_KEY": KEY},
                                            mesh_transport=Transport(response))
            self.assertEqual(report["mesh"]["status"], "failed" if codes else "passed")
            self.assertEqual(report["mesh"]["diagnostics"]["mesh_response_classification"], kind)
            self.assertEqual(report["mesh"]["diagnostics"]["routing_rejection_codes"], list(codes))
            self.assertEqual(report["mesh"]["attempts"], 1)
            self.assertEqual(report["brave"]["attempts"], 0)
            for secret in (KEY, PRIVATE, "private-fixture-id"):
                self.assertNotIn(secret, json.dumps(report))

    async def test_real_stream_body_rejects_without_json_or_text_disclosure(self):
        response = HTTPResponse(200, (("Content-Type", "text/event-stream"),),
                                ("data: " + PRIVATE + "\n\n").encode())
        report = await run_connectivity(OPTIONS, execute=True, environment={"MESH_API_KEY": KEY},
                                        mesh_transport=Transport(response))
        self.assertEqual(report["mesh"]["failure_category"], "response_validation")
        self.assertEqual(report["mesh"]["diagnostics"]["mesh_response_classification"], Kind.STREAMING)
        self.assertNotIn(PRIVATE, json.dumps(report))

    async def test_failed_live_outcome_and_audit_persist_only_fixed_metadata(self):
        Path("artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir="artifacts") as temporary:
            for headers, kind, codes in CASES:
                if not codes:
                    continue
                class FixtureTransport(MeshTransport):
                    async def send(self, request):
                        response = await super().send(request)
                        body = b"data: private-stream-body\n\n" if kind == Kind.STREAMING else response.body
                        return HTTPResponse(200, headers, body)

                result = await run_live(LIVE_OPTIONS, execute=True, environment=ENV, output_root=Path(temporary),
                    mesh_transport=FixtureTransport(plan(), draft, assessment),
                    brave_transport=SearchTransport((URL,)), source_transport=SourceTransport())
                folder = Path(temporary) / result["run_id"]
                outcome = json.loads((folder / "outcome.json").read_text())
                audit = [json.loads(line) for line in (folder / "audit.jsonl").read_text().splitlines()]
                self.assertEqual(outcome, result)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["attempts"], {"mesh": 1, "brave": 0, "source": 0})
                self.assertEqual(result["diagnostics"]["mesh"]["mesh_response_classification"], kind)
                self.assertEqual(result["diagnostics"]["mesh"]["routing_rejection_codes"], list(codes))
                self.assertEqual([row["kind"] for row in audit], ["started", "failed"])
                self.assertEqual(audit[-1]["mesh_response_classification"], kind)
                self.assertEqual(audit[-1]["routing_rejection_codes"], list(codes))
                self.assertFalse((folder / "report.json").exists())
                serialized = json.dumps(outcome) + json.dumps(audit)
                for secret in (PRIVATE, *ENV.values(), "private-response-id", "private-stream-body"):
                    self.assertNotIn(secret, serialized)
