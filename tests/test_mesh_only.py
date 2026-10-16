"""Isolated Mesh readiness uses fixture exchanges, never live credentials or APIs."""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from deepdive_ai.connectivity import MODEL, TestOptions, main, run_connectivity
from deepdive_ai.http_transport import (
    FailureCategory, HTTPResponse, ResponseTooLarge, TransportError, TransportStage,
    _PinnedConnection,
)


KEY = "offline-mesh-fixture"
OPTIONS = TestOptions(gateway_risk_accepted=True, spending_confirmed=True,
                      mesh_input_rate=Decimal("1"), mesh_output_rate=Decimal("2"), mesh_only=True)


def completion(**changes):
    body = {"id": "private-fixture-id", "model": MODEL,
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
    body.update(changes)
    return HTTPResponse(200, (("Content-Type", "application/json"),), json.dumps(body).encode())


class MeshEnvironment(dict):
    def get(self, name, default=None):
        if name == "BRAVE_SEARCH_API_KEY":
            raise AssertionError("Brave credential must not be accessed")
        return super().get(name, default)


class Transport:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def send(self, request):
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class MeshOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No live connections"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def run_fixture(self, result=None, *, options=OPTIONS, environment=None, execute=True):
        mesh = Transport(completion() if result is None else result)
        brave = Transport(RuntimeError("Brave must remain uncalled"))
        environment = MeshEnvironment(MESH_API_KEY=KEY) if environment is None else environment
        with patch("deepdive_ai.connectivity.BraveSearchSettings.from_environment") as settings, \
                patch("deepdive_ai.connectivity.BraveSearchDiscovery") as client:
            report = await run_connectivity(options, execute=execute, environment=environment,
                                            mesh_transport=mesh, brave_transport=brave)
        settings.assert_not_called()
        client.assert_not_called()
        self.assertEqual(brave.calls, [])
        self.assertEqual(report["brave"]["attempts"], 0)
        self.assertEqual(report["brave"]["status"], "not_run")
        self.assertNotIn(KEY, json.dumps(report))
        self.assertNotIn("private-fixture-id", json.dumps(report))
        return report, mesh

    async def test_mesh_only_succeeds_without_brave_configuration(self):
        report, mesh = await self.run_fixture()
        self.assertTrue(report["ready"])
        self.assertEqual(report["scope"], "mesh_only")
        self.assertEqual(report["mesh"]["status"], "passed")
        self.assertEqual(report["mesh"]["attempts"], 1)
        self.assertEqual(len(mesh.calls), 1)
        request = mesh.calls[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, "https://api.meshapi.ai/v1/chat/completions")
        self.assertEqual(json.loads(request.body), {
            "model": MODEL, "messages": [{"role": "user", "content": "Reply exactly OK."}],
            "max_tokens": 64, "stream": False,
        })
        self.assertEqual(request.timeout_seconds, 15)
        self.assertEqual(request.max_response_bytes, 16_384)
        self.assertEqual(report["mesh"]["token_usage"]["total_tokens"], 12)
        self.assertEqual(Decimal(report["mesh"]["estimated_cost_usd"]), Decimal("0.000014"))

    async def test_preflight_never_constructs_transport_or_calls_providers(self):
        with patch("deepdive_ai.connectivity.PublicHTTPTransport") as factory:
            report = await run_connectivity(OPTIONS, environment=MeshEnvironment(MESH_API_KEY=KEY))
        factory.assert_not_called()
        self.assertTrue(report["ready"])
        self.assertEqual(report["mode"], "preflight")
        self.assertEqual(report["mesh"]["attempts"], 0)
        self.assertEqual(report["brave"]["attempts"], 0)

    async def test_mesh_credentials_prices_and_safety_gates_remain_required(self):
        cases = ((OPTIONS, MeshEnvironment()),
                 (OPTIONS, MeshEnvironment(MESH_API_KEY="invalid\r\nkey")),
                 (replace(OPTIONS, mesh_input_rate=None), None),
                 (replace(OPTIONS, mesh_output_rate=None), None),
                 (replace(OPTIONS, spending_confirmed=False), None),
                 (replace(OPTIONS, gateway_risk_accepted=False), None))
        for options, environment in cases:
            with self.subTest(options=options):
                report, mesh = await self.run_fixture(options=options, environment=environment)
                self.assertFalse(report["ready"])
                self.assertTrue(report["blockers"])
                self.assertEqual(mesh.calls, [])

    async def test_failures_do_not_retry_or_invoke_brave_or_leak_sensitive_data(self):
        cases = ((HTTPResponse(401, body=KEY.encode()), "authentication_http_status"),
                 (HTTPResponse(402), "billing_http_status"),
                 (HTTPResponse(429), "rate_limited"),
                 (HTTPResponse(503), "provider_http_status"),
                 (HTTPResponse(302, (("Location", "http://127.0.0.1/"),)), "redirect_rejected"),
                 (TransportError(KEY, category=FailureCategory.TLS), "tls_failure"),
                 (TransportError(KEY, category=FailureCategory.TIMEOUT, stage=TransportStage.HEADERS), "timeout"),
                 (ResponseTooLarge(KEY, http_status=200), "response_too_large"),
                 (RuntimeError(KEY), "internal_error"))
        for result, category in cases:
            with self.subTest(category=category):
                report, mesh = await self.run_fixture(result)
                self.assertEqual(report["mesh"]["status"], "failed")
                self.assertFalse(report["mesh"]["response_compatible"])
                self.assertEqual(report["mesh"]["failure_category"], category)
                self.assertEqual(report["mesh"]["cost_status"], "unknown_failure_may_be_billable")
                self.assertEqual(len(mesh.calls), 1)
                if category == "timeout":
                    self.assertEqual(report["mesh"]["failure_stage"], "response_headers")

    async def test_incomplete_or_incompatible_responses_fail_closed(self):
        for response in (HTTPResponse(200, (("Content-Type", "application/json"),), b"bad-json"),
                         completion(choices=[{"finish_reason": "length", "message": {"role": "assistant", "content": "OK"}}]),
                         completion(model="another-model"),
                         completion(choices=[{"finish_reason": "stop", "message": {"role": "assistant", "content": " "}}]),
                         completion(usage={"prompt_tokens": 10, "completion_tokens": 65, "total_tokens": 75})):
            report, mesh = await self.run_fixture(response)
            self.assertEqual(report["mesh"]["status"], "failed")
            self.assertFalse(report["mesh"]["response_compatible"])
            self.assertIsNone(report["mesh"]["estimated_cost_usd"])
            self.assertEqual(len(mesh.calls), 1)

    async def test_missing_usage_and_gateway_headers_are_explicit_uncertainties(self):
        report, _ = await self.run_fixture(completion(usage=None))
        result = report["mesh"]
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["token_usage"])
        self.assertIsNone(result["estimated_cost_usd"])
        self.assertEqual(result["cost_status"], "unknown_failure_may_be_billable")
        self.assertEqual(result["diagnostics"]["failed_checks"], ["output_limit_satisfied"])
        self.assertIn("gateway_attempts_unverified", result["diagnostics"]["warnings"])
        self.assertIn("gateway_fallback_unverified", result["diagnostics"]["warnings"])

    async def test_only_one_default_transport_is_constructed(self):
        transport = Transport(completion())
        with patch("deepdive_ai.connectivity.PublicHTTPTransport", return_value=transport) as factory:
            report = await run_connectivity(OPTIONS, execute=True, environment=MeshEnvironment(MESH_API_KEY=KEY))
        factory.assert_called_once_with()
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(report["mesh"]["status"], "passed")


class MeshOnlyCommandTests(unittest.TestCase):
    def arguments(self):
        return ["connectivity", "--mesh-only", "--accept-gateway-uncertainty", "--confirm-spend-controls",
                "--mesh-input-usd-per-million", "1", "--mesh-output-usd-per-million", "2"]

    def test_cli_success_requires_only_mesh_pass(self):
        transport = Transport(completion(choices=[{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "private non-OK fixture reply"}}]))
        with patch.dict("os.environ", {"MESH_API_KEY": KEY}, clear=True), \
                patch("sys.argv", self.arguments() + ["--execute"]), \
                patch("deepdive_ai.connectivity.PublicHTTPTransport", return_value=transport), \
                patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(json.loads(output.getvalue())["brave"]["status"], "not_run")
        self.assertFalse(json.loads(output.getvalue())["mesh"]["diagnostics"]["reply_matches"])
        self.assertNotIn(KEY, output.getvalue())
        self.assertNotIn("private non-OK fixture reply", output.getvalue())

    def test_cli_default_is_offline_preflight(self):
        with patch.dict("os.environ", {"MESH_API_KEY": KEY}, clear=True), \
                patch("sys.argv", self.arguments()), \
                patch("deepdive_ai.connectivity.PublicHTTPTransport") as factory, \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        factory.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["mode"], "preflight")

    def test_cli_failure_returns_nonzero_without_brave(self):
        transport = Transport(HTTPResponse(401, body=KEY.encode()))
        with patch.dict("os.environ", {"MESH_API_KEY": KEY}, clear=True), \
                patch("sys.argv", self.arguments() + ["--execute"]), \
                patch("deepdive_ai.connectivity.PublicHTTPTransport", return_value=transport), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn(KEY, output.getvalue())

    def test_provider_modes_are_boolean_and_mutually_exclusive(self):
        for changes in ({"mesh_only": "yes"}, {"brave_only": True}):
            with self.assertRaises(ValueError):
                replace(OPTIONS, **changes)
        with patch("sys.argv", ["connectivity", "--mesh-only", "--brave-only"]), \
                patch("deepdive_ai.connectivity.run_connectivity") as run, \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(error.exception.code, 2)
        run.assert_not_called()
