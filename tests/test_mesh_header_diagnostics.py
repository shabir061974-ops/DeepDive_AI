"""Cache/routing header diagnostics for failed live runs. Names plus allowlisted values only; offline."""

import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deepdive_ai import live
from deepdive_ai.http_transport import HTTPResponse, _PinnedConnection
from deepdive_ai.live import _header_diagnostics, run_live
from test_agents import URL, assessment, draft, plan
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport, SourceTransport

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("summarize_live_run", ROOT / "scripts" / "summarize_live_run.py")
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)

PRIVATE = "PRIVATE-req-7f3a9c-or-credential"
CACHED_HEADERS = (
    ("Content-Type", "application/json"), ("X-Cache", "HIT"), ("Age", "37"), ("Cache-Control", "no-store"),
    ("X-Mesh-Version", "2026-08"), ("X-Mesh-Request-Id", PRIVATE), ("X-Mesh-Cache-Key", PRIVATE),
    ("X-Mesh-Cache", "BYPASS"), ("CF-Cache-Status", "HIT"), ("Set-Cookie", PRIVATE),
    ("Authorization", "Bearer " + PRIVATE), ("X-Request-Id", PRIVATE), ("Server", PRIVATE))
EXPECTED = [
    {"name": "x-cache", "value": "hit"}, {"name": "age", "value": "37"},
    {"name": "cache-control", "value": None}, {"name": "x-mesh-version", "value": None},
    {"name": "x-mesh-request-id", "value": None}, {"name": "x-mesh-cache-key", "value": None},
    {"name": "x-mesh-cache", "value": "bypass"}, {"name": "cf-cache-status", "value": "hit"}]


class HeaderRuleTests(unittest.TestCase):
    def test_only_cache_and_routing_names_with_allowlisted_values(self):
        self.assertEqual(_header_diagnostics(CACHED_HEADERS), EXPECTED)

    def test_values(self):
        cases = {"true": "true", " False ": "false", "HIT": "hit", "Miss": "miss", "bypass": "bypass", "0": "0",
                 "1234567890": "1234567890", "12345678901": None, "-1": None, "1.5": None, "hit, miss": None,
                 "max-age=60": None, "": None, "2026-08": None, "١٢": None, PRIVATE: None}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(_header_diagnostics((("X-Mesh-Routing-Attempts", value),)),
                                 [{"name": "x-mesh-routing-attempts", "value": expected}])
        self.assertEqual(_header_diagnostics((("X-Mesh-Routing-Fallback", 5),)),
                         [{"name": "x-mesh-routing-fallback", "value": None}])

    def test_identifier_like_names_never_record_values(self):
        for name in ("X-Mesh-Request-Id", "X-Mesh-Trace", "X-Cache-Key", "X-Mesh-Session", "X-Mesh-User",
                     "X-Mesh-Auth", "X-Mesh-Token", "X-Mesh-Org", "X-Mesh-Client-Ip", "X-Mesh-Signature"):
            with self.subTest(name=name):
                self.assertEqual(_header_diagnostics(((name, "true"),))[0]["value"], None)

    def test_unrelated_malformed_and_excess_headers(self):
        self.assertEqual(_header_diagnostics((("Server", "x"), ("Via", "1.1 cdn"), ("X-Request-Id", "1"),
                                              ("Content-Type", "application/json"))), [])
        self.assertEqual(_header_diagnostics((("X-Mesh Bad", "true"), ("x-mesh-‮", "true"), (5, "true"),
                                              ("x-mesh-" + "a" * 64, "true"), ("X-MESH-OK", "true"))),
                         [{"name": "x-mesh-ok", "value": "true"}])
        self.assertEqual(_header_diagnostics(None), [])
        self.assertEqual(_header_diagnostics([("x-cache", "HIT", "extra"), "x-cache"]), [])
        many = tuple((f"X-Mesh-H{i}", str(i)) for i in range(40))
        self.assertEqual(len(_header_diagnostics(many)), live.MAX_RECORDED_HEADERS)


class LiveHeaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("real DNS forbidden")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("real HTTP forbidden"))):
            guard.start()
            self.addCleanup(guard.stop)
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def run_with_planner_headers(self, headers):
        class Mesh(MeshTransport):
            async def send(self, request):
                response = await super().send(request)
                return HTTPResponse(200, headers, response.body) if len(self.calls) == 1 else response

        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result = await run_live(replace(OPTIONS), execute=True, environment=ENV, output_root=self.root,
                                    mesh_transport=Mesh(plan(), draft, assessment),
                                    brave_transport=SearchTransport((URL,)), source_transport=SourceTransport())
        folder = self.root / result["run_id"]
        self.assertEqual(json.loads((folder / "outcome.json").read_text()), result)
        rendered = summary.build(None, result, self.root)
        persisted = (folder / "outcome.json").read_text() + (folder / "audit.jsonl").read_text()
        for private in (PRIVATE, "7f3a9c", "Bearer", *ENV.values(), "private-response-id", "no-store", "2026-08"):
            self.assertNotIn(private, persisted)
            self.assertNotIn(private, rendered)
        return result, rendered

    async def test_cached_planner_reply_records_and_summarizes_headers(self):
        result, rendered = await self.run_with_planner_headers(CACHED_HEADERS)
        self.assertEqual(result["failure_category"], "response_validation")
        mesh = result["diagnostics"]["mesh"]
        self.assertEqual(mesh["mesh_validation_failure"], "routing_metadata_valid")
        self.assertEqual(mesh["mesh_response_classification"], "cached_response")
        self.assertIn("cached_response_unverified", mesh["routing_rejection_codes"])
        self.assertIn("fallback_missing", mesh["routing_rejection_codes"])
        self.assertEqual(result["mesh_last_headers"], EXPECTED)
        self.assertIn("### Mesh response headers", rendered)
        for row in ("| x-cache | hit |", "| age | 37 |", "| cache-control | withheld |", "| x-mesh-version | withheld |",
                    "| x-mesh-request-id | withheld |", "| x-mesh-cache-key | withheld |", "| x-mesh-cache | bypass |",
                    "| cf-cache-status | hit |"):
            self.assertIn(row, rendered)
        self.assertNotIn("set-cookie", rendered)
        self.assertNotIn("authorization", rendered)

    async def test_reply_without_cache_or_routing_headers(self):
        result, rendered = await self.run_with_planner_headers((("Content-Type", "application/json"),))
        self.assertEqual(result["mesh_last_headers"], [])
        self.assertIn("No cache or routing headers were present.", rendered)

    async def test_successful_run_records_no_headers(self):
        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result = await run_live(replace(OPTIONS), execute=True, environment=ENV, output_root=self.root,
                                    mesh_transport=MeshTransport(plan(), draft, assessment),
                                    brave_transport=SearchTransport((URL,)), source_transport=SourceTransport())
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("mesh_last_headers", result)


class SummaryHeaderTests(unittest.TestCase):
    def test_summary_rules_match_runner_rules(self):
        self.assertEqual(summary.HEADER_NAME.pattern, live.HEADER_NAME.pattern)
        self.assertEqual(summary.HEADER_VALUE_WORDS, live.HEADER_VALUE_WORDS)
        self.assertEqual(summary.HEADER_ID_TOKENS, live.HEADER_ID_TOKENS)
        self.assertEqual(summary.MAX_RECORDED_HEADERS, live.MAX_RECORDED_HEADERS)

    def test_tampered_outcome_values_are_withheld(self):
        headers = [{"name": "<b>x</b>", "value": "hit"}, {"name": "x-mesh-routing-fallback", "value": PRIVATE},
                   {"name": "x-mesh-request-id", "value": "hit"}, {"name": "server", "value": "hit"},
                   {"name": "x-cache", "value": "HIT\n| injected |"}, "x-cache", {"name": "age", "value": "12"}]
        rendered = summary.build(None, {"mode": "live", "status": "failed", "run_id": None,
                                        "mesh_last_headers": headers}, Path("missing-reports"))
        self.assertEqual(rendered.count("| invalid | withheld |"), 3)
        self.assertIn("| x-mesh-routing-fallback | withheld |", rendered)
        self.assertIn("| x-mesh-request-id | withheld |", rendered)
        self.assertIn("| x-cache | withheld |", rendered)
        self.assertIn("| age | 12 |", rendered)
        for private in (PRIVATE, "<b>", "injected", "| server |"):
            self.assertNotIn(private, rendered)


if __name__ == "__main__":
    unittest.main()
