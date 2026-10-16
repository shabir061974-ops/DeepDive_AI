"""Brave-only mode is isolated from Mesh; all requests are fixture exchanges."""

import io
import json
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from deepdive_ai.connectivity import TestOptions, main, run_connectivity
from deepdive_ai.http_transport import FailureCategory, HTTPResponse, ResponseTooLarge, TransportError, _PinnedConnection


KEY = "offline-brave-fixture"
OPTIONS = TestOptions(brave_request_rate=Decimal("0.005"), spending_confirmed=True, brave_only=True)
OK = HTTPResponse(200, (("Content-Type", "application/json"),), b'{"type":"search","web":{"results":[]}}')


class BraveEnvironment(dict):
    def get(self, name, default=None):
        if name == "MESH_API_KEY":
            raise AssertionError("Mesh credential must not be accessed")
        return super().get(name, default)


class Transport:
    def __init__(self, result=OK):
        self.result = result
        self.calls = []

    async def send(self, request):
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class BraveOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No live connections"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def run_fixture(self, result=OK, *, options=OPTIONS, environment=None, execute=True):
        brave = Transport(result)
        mesh = Transport(RuntimeError("Mesh must remain uncalled"))
        environment = BraveEnvironment(BRAVE_SEARCH_API_KEY=KEY) if environment is None else environment
        with patch("deepdive_ai.connectivity.MeshSettings.from_environment") as settings, \
                patch("deepdive_ai.connectivity.MeshAPIClient") as mesh_client:
            report = await run_connectivity(options, execute=execute, environment=environment,
                                            brave_transport=brave, mesh_transport=mesh)
        settings.assert_not_called()
        mesh_client.assert_not_called()
        self.assertEqual(mesh.calls, [])
        self.assertEqual(report["mesh"]["attempts"], 0)
        self.assertEqual(report["mesh"]["status"], "not_run")
        self.assertNotIn(KEY, json.dumps(report))
        return report, brave

    async def test_success_requires_only_brave_configuration_and_never_mesh(self):
        report, brave = await self.run_fixture()
        self.assertTrue(report["ready"])
        self.assertEqual(report["scope"], "brave_only")
        self.assertEqual(report["brave"]["status"], "passed")
        self.assertIsNone(report["brave"]["failure_category"])
        self.assertEqual(report["brave"]["attempts"], 1)
        self.assertEqual(len(brave.calls), 1)
        request = brave.calls[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(urlsplit(request.url).netloc, "api.search.brave.com")
        self.assertEqual(parse_qs(urlsplit(request.url).query)["count"], ["1"])
        self.assertEqual(request.timeout_seconds, 10)
        self.assertEqual(request.max_response_bytes, 16_384)

    async def test_preflight_remains_offline_without_mesh_credentials_or_prices(self):
        report, brave = await self.run_fixture(execute=False)
        self.assertTrue(report["ready"])
        self.assertEqual(report["mode"], "preflight")
        self.assertEqual(brave.calls, [])

    async def test_brave_credentials_price_and_spending_confirmation_still_required(self):
        for options, environment in ((OPTIONS, {}), (replace(OPTIONS, brave_request_rate=None), None),
                                     (replace(OPTIONS, spending_confirmed=False), None)):
            report, brave = await self.run_fixture(options=options, environment=environment)
            self.assertFalse(report["ready"])
            self.assertTrue(report["blockers"])
            self.assertEqual(brave.calls, [])

    async def test_failures_never_retry_or_call_mesh_and_keep_safe_categories(self):
        for result, category in ((HTTPResponse(401, body=KEY.encode()), "authentication_http_status"),
                                 (HTTPResponse(429), "rate_limited"), (HTTPResponse(503), "provider_http_status"),
                                 (HTTPResponse(302), "redirect_rejected"),
                                 (HTTPResponse(200, (("Content-Type", "application/json"),), b"bad-json"), "response_parsing"),
                                 (TransportError(KEY, category=FailureCategory.TIMEOUT), "timeout"),
                                 (ResponseTooLarge(KEY, http_status=200), "response_too_large")):
            report, brave = await self.run_fixture(result)
            self.assertEqual(report["brave"]["status"], "failed")
            self.assertEqual(report["brave"]["failure_category"], category)
            self.assertEqual(len(brave.calls), 1)

    async def test_only_one_default_transport_is_constructed(self):
        transport = Transport()
        with patch("deepdive_ai.connectivity.PublicHTTPTransport", return_value=transport) as factory:
            report = await run_connectivity(OPTIONS, execute=True,
                                            environment=BraveEnvironment(BRAVE_SEARCH_API_KEY=KEY))
        factory.assert_called_once_with()
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(report["mesh"]["attempts"], 0)


class BraveOnlyCommandTests(unittest.TestCase):
    def test_cli_success_does_not_require_a_mesh_pass(self):
        transport = Transport()
        arguments = ["connectivity", "--brave-only", "--confirm-spend-controls",
                     "--brave-usd-per-request", "0.005", "--execute"]
        with patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": KEY}, clear=True), \
                patch("sys.argv", arguments), patch("deepdive_ai.connectivity.PublicHTTPTransport", return_value=transport), \
                patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["mesh"]["status"], "not_run")
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn(KEY, output.getvalue())

    def test_cli_brave_only_default_stays_offline(self):
        arguments = ["connectivity", "--brave-only", "--confirm-spend-controls", "--brave-usd-per-request", "0.005"]
        with patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": KEY}, clear=True), \
                patch("sys.argv", arguments), patch("deepdive_ai.connectivity.PublicHTTPTransport") as factory, \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        factory.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["mode"], "preflight")

    def test_provider_selection_requires_a_boolean(self):
        with self.assertRaises(ValueError):
            replace(OPTIONS, brave_only="yes")
