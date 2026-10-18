"""Virtual delayed Mesh headers through the real secure transport, no network."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deepdive_ai.agents.common import AgentLimits
from deepdive_ai.http_transport import PublicHTTPTransport, _PinnedConnection
from deepdive_ai.live import PROFILE, policy, run_live
from test_agents import URL, assessment, draft, plan
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport, SourceTransport


class DelayedHeaders(MeshTransport):
    """Advance a transport-only virtual clock; never change asyncio's clock."""

    def __init__(self, delay: float, *, fallback: str = "false"):
        super().__init__(plan(), draft, assessment)
        self.delay = delay
        self.fallback = fallback

    async def send(self, request):
        fixture = await super().send(request)
        clock = [0.0]
        connection = Mock()
        response = Mock(status=200)
        headers = [(k, v) for k, v in fixture.headers if k != "X-Mesh-Routing-Fallback"]
        response.getheaders.return_value = tuple(headers + [("X-Mesh-Routing-Fallback", self.fallback),
                                                            ("Content-Length", str(len(fixture.body)))])
        response.read1.side_effect = [fixture.body, b""]

        def getresponse():
            remaining = connection.sock.settimeout.call_args.args[0]
            if self.delay >= remaining:
                clock[0] += remaining
                raise TimeoutError("private-offline-header-error")
            clock[0] += self.delay
            return response

        connection.getresponse.side_effect = getresponse
        with patch("deepdive_ai.http_transport._PinnedConnection", return_value=connection), \
                patch("deepdive_ai.http_transport.time", SimpleNamespace(monotonic=lambda: clock[0])), \
                patch("deepdive_ai.http_transport.threading.Timer"):
            return await PublicHTTPTransport(lambda host, port: ("8.8.8.8",)).send(request)


class LiveMeshTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("No real DNS")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No real sockets"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def run_case(self, mesh):
        search, source = SearchTransport((URL,)), SourceTransport()
        result = await run_live(OPTIONS, execute=True, environment=ENV, output_root=self.root,
                                mesh_transport=mesh, brave_transport=search, source_transport=source)
        folder = self.root / result["run_id"]
        self.assertEqual(json.loads((folder / "outcome.json").read_text()), result)
        self.assertNotIn("private-offline-header-error", json.dumps(result))
        return result, folder

    async def test_header_delay_between_old_and_new_deadlines_completes_real_workflow(self):
        mesh = DelayedHeaders(20)
        result, folder = await self.run_case(mesh)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["report_status"], "verified")
        self.assertEqual(result["attempts"], {"mesh": 3, "brave": 1, "source": 1})
        self.assertTrue((folder / "report.json").exists())
        self.assertTrue(all(request.timeout_seconds == 25 for request in mesh.calls))
        self.assertLess(PROFILE["mesh_timeout_seconds"], AgentLimits().operation_timeout_seconds)
        self.assertEqual(PROFILE["overall_timeout_seconds"], 120)
        self.assertEqual((PROFILE["brave_timeout_seconds"], PROFILE["source_timeout_seconds"]), (10, 10))

    async def test_old_budget_reproduces_headers_timeout_without_retries(self):
        with patch("deepdive_ai.live.PROFILE", {**PROFILE, "mesh_timeout_seconds": 15}):
            result, folder = await self.run_case(DelayedHeaders(20))
        self.assertEqual(result["failure_category"], "timeout")
        self.assertEqual(result["diagnostics"]["mesh"]["stage"], "response_headers")
        self.assertEqual(result["attempts"], {"mesh": 1, "brave": 0, "source": 0})
        self.assertFalse((folder / "report.json").exists())

    async def test_new_budget_still_enforces_deadline_and_persists_sanitized_failure(self):
        result, folder = await self.run_case(DelayedHeaders(26))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_category"], "timeout")
        self.assertEqual(result["diagnostics"]["mesh"], {"category": "timeout", "stage": "response_headers", "http_status": None})
        self.assertEqual(result["attempts"], {"mesh": 1, "brave": 0, "source": 0})
        audit = [json.loads(row)["kind"] for row in (folder / "audit.jsonl").read_text().splitlines()]
        self.assertEqual(audit, ["started", "failed"])
        self.assertEqual(policy(25, 7, 32768).max_retries, 0)

    async def test_delayed_success_cannot_bypass_routing_validation(self):
        result, folder = await self.run_case(DelayedHeaders(20, fallback="true"))
        self.assertEqual(result["failure_category"], "response_validation")
        self.assertFalse(result["diagnostics"]["mesh"]["mesh_validation_checks"]["routing_metadata_valid"])
        self.assertEqual(result["attempts"], {"mesh": 1, "brave": 0, "source": 0})
        self.assertFalse((folder / "report.json").exists())
