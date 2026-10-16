"""Connectivity harness tests use only controlled transports, never provider APIs."""

import asyncio
import io
import json
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from deepdive_ai.connectivity import MODEL, TestOptions, main, run_connectivity
from deepdive_ai.http_transport import HTTPResponse, _PinnedConnection


ENV = {"MESH_API_KEY": "offline-mesh-secret", "BRAVE_SEARCH_API_KEY": "offline-brave-secret"}
OPTIONS = TestOptions(True, "2026-08", Decimal("0.3"), Decimal("2.5"), Decimal("0.005"), True)
JSON_HEADERS = (("Content-Type", "application/json"),)
MESH_HEADERS = JSON_HEADERS + (("X-Mesh-Routing-Attempts", "1"),
                              ("X-Mesh-Routing-Fallback", "false"), ("X-Mesh-Version", "2026-08"))


class Transport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def send(self, request):
        self.calls.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def mesh_response(*, headers=MESH_HEADERS, **changes):
    body = {"id": "sensitive-fixture-id", "model": MODEL,
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
    body.update(changes)
    return HTTPResponse(200, headers, json.dumps(body).encode())


def brave_response():
    return HTTPResponse(200, JSON_HEADERS,
                        b'{"type":"search","web":{"results":[{"url":"https://fixture.invalid/sensitive"}]}}')


class ConnectivityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("Live DNS forbidden")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("Live connections forbidden"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def run_fixture(self, *, brave=None, mesh=None, options=OPTIONS, execute=True, environment=ENV):
        brave = Transport(brave_response()) if brave is None else brave
        mesh = Transport(mesh_response()) if mesh is None else mesh
        report = await run_connectivity(options, execute=execute, environment=environment,
                                        brave_transport=brave, mesh_transport=mesh)
        return report, brave, mesh

    async def test_default_preflight_makes_zero_requests(self):
        report, brave, mesh = await self.run_fixture(execute=False)
        self.assertTrue(report["ready"])
        self.assertEqual(report["mode"], "preflight")
        self.assertEqual(brave.calls + mesh.calls, [])

    async def test_missing_credentials_or_gates_prevent_all_requests(self):
        for options, environment in ((OPTIONS, {}), (TestOptions(), ENV),
                                     (replace(OPTIONS, gateway_risk_accepted=False), ENV),
                                     (replace(OPTIONS, spending_confirmed=False), ENV),
                                     (replace(OPTIONS, mesh_input_rate=None), ENV),
                                     (OPTIONS, dict(ENV, MESH_API_KEY="bad\r\nvalue"))):
            report, brave, mesh = await self.run_fixture(options=options, environment=environment)
            self.assertFalse(report["ready"])
            self.assertTrue(report["blockers"])
            self.assertEqual(brave.calls + mesh.calls, [])

    async def test_success_is_two_requests_with_safe_usage_and_cost(self):
        report, brave, mesh = await self.run_fixture()
        self.assertEqual(report["brave"]["status"], "passed")
        self.assertEqual(report["mesh"]["status"], "passed")
        self.assertEqual(report["mesh"]["token_usage"]["total_tokens"], 12)
        self.assertEqual(Decimal(report["mesh"]["estimated_cost_usd"]), Decimal("0.000008"))
        self.assertEqual(report["brave"]["estimated_cost_usd"], "0.005")
        self.assertEqual((len(brave.calls), len(mesh.calls)), (1, 1))
        text = json.dumps(report)
        for sensitive in (*ENV.values(), "Authorization", "sensitive-fixture-id", "fixture.invalid", '"content"'):
            self.assertNotIn(sensitive, text)
        self.assertEqual(mesh.calls[0].timeout_seconds, 15)
        self.assertEqual(brave.calls[0].timeout_seconds, 10)
        for request in brave.calls + mesh.calls:
            self.assertEqual(request.max_response_bytes, 16_384)
            self.assertLessEqual(len(request.body), 2048)
        self.assertEqual(json.loads(mesh.calls[0].body)["max_tokens"], 64)
        self.assertEqual(json.loads(mesh.calls[0].body)["model"], MODEL)

    async def test_brave_errors_stop_before_mesh_without_retry(self):
        for status in (301, 307, 401, 403, 429, 500, 503):
            report, brave, mesh = await self.run_fixture(brave=Transport(HTTPResponse(status)))
            self.assertEqual(report["brave"]["status"], "failed")
            self.assertEqual(report["brave"]["http_status"], status)
            self.assertEqual(report["mesh"]["status"], "not_run")
            self.assertEqual((len(brave.calls), len(mesh.calls)), (1, 0))

    async def test_mesh_errors_never_retry_or_follow_redirects(self):
        for status in (301, 307, 401, 402, 403, 429, 503):
            report, brave, mesh = await self.run_fixture(mesh=Transport(HTTPResponse(status)))
            self.assertEqual(report["mesh"]["status"], "failed")
            self.assertEqual(report["mesh"]["http_status"], status)
            self.assertEqual((len(brave.calls), len(mesh.calls)), (1, 1))

    async def test_unsafe_results_and_malformed_body_stop_without_mesh(self):
        for body in (b'not-json', b'{"type":"search","web":{"results":[{"url":"http://127.0.0.1/"}]}}'):
            report, brave, mesh = await self.run_fixture(brave=Transport(HTTPResponse(200, JSON_HEADERS, body)))
            self.assertEqual(report["brave"]["status"], "failed")
            self.assertEqual(len(mesh.calls), 0)

    async def test_model_and_explicit_version_checks_remain_strict(self):
        for response in (mesh_response(model="another-model"), mesh_response(headers=JSON_HEADERS),
                         mesh_response(headers=JSON_HEADERS + (("X-Mesh-Routing-Attempts", "2"),)),
                         mesh_response(headers=MESH_HEADERS[:-1] + (("X-Mesh-Version", "2026-09"),))):
            report, _, mesh = await self.run_fixture(mesh=Transport(response))
            self.assertEqual(report["mesh"]["status"], "failed")
            self.assertFalse(report["mesh"]["response_compatible"])
            self.assertIsNone(report["mesh"]["estimated_cost_usd"])
            self.assertEqual(len(mesh.calls), 1)

    async def test_no_version_or_routing_headers_needed_for_connectivity(self):
        report, brave, mesh = await self.run_fixture(
            options=replace(OPTIONS, mesh_version=None), mesh=Transport(mesh_response(headers=JSON_HEADERS)))
        self.assertTrue(report["ready"])
        self.assertEqual(report["mesh"]["status"], "passed")
        self.assertEqual(report["mesh"]["diagnostics"], {
            "gateway_attempts_reported": None, "gateway_fallback_reported": None, "version_matches": None,
            "warnings": ["gateway_attempts_unverified", "gateway_fallback_unverified", "mesh_version_not_checked"],
        })
        self.assertNotIn("X-Mesh-Version", dict(mesh.calls[0].headers))
        self.assertEqual((len(brave.calls), len(mesh.calls)), (1, 1))

    async def test_gateway_activity_is_reported_without_claiming_local_control(self):
        headers = JSON_HEADERS + (("X-Mesh-Routing-Attempts", "3"), ("X-Mesh-Routing-Fallback", "true"))
        report, _, mesh = await self.run_fixture(options=replace(OPTIONS, mesh_version=None),
                                                 mesh=Transport(mesh_response(headers=headers)))
        self.assertEqual(report["mesh"]["status"], "passed")
        self.assertEqual(report["mesh"]["attempts"], 1)
        self.assertEqual(report["mesh"]["diagnostics"]["gateway_attempts_reported"], 3)
        self.assertIn("gateway_fallback_reported", report["mesh"]["diagnostics"]["warnings"])
        self.assertEqual(report["mesh"]["cost_status"], "response_usage_estimate_not_total_bill")
        self.assertEqual(len(mesh.calls), 1)

    async def test_invalid_gateway_metadata_never_echoes_raw_values(self):
        for attempts in ("-1", "0", "9999999", "sensitive-header", "١"):
            headers = JSON_HEADERS + (("X-Mesh-Routing-Attempts", attempts),
                                      ("X-Mesh-Routing-Fallback", "sensitive-header"))
            report, _, _ = await self.run_fixture(options=replace(OPTIONS, mesh_version=None),
                                                  mesh=Transport(mesh_response(headers=headers)))
            self.assertEqual(report["mesh"]["status"], "passed")
            self.assertIsNone(report["mesh"]["diagnostics"]["gateway_attempts_reported"])
            self.assertNotIn("sensitive-header", json.dumps(report))

    async def test_new_acknowledgements_cannot_replace_required_prices(self):
        options = TestOptions(gateway_risk_accepted=True, spending_confirmed=True)
        report, brave, mesh = await self.run_fixture(options=options)
        self.assertIn("approved_account_prices_required", report["blockers"])
        self.assertEqual(brave.calls + mesh.calls, [])

    async def test_missing_or_invalid_usage_never_becomes_a_cost_estimate(self):
        for usage in (None, {}, {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3},
                      {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 99},
                      {"prompt_tokens": "sensitive-body", "completion_tokens": 2, "total_tokens": 12}):
            report, _, _ = await self.run_fixture(mesh=Transport(mesh_response(usage=usage)))
            self.assertEqual(report["mesh"]["status"], "passed")
            self.assertIsNone(report["mesh"]["token_usage"])
            self.assertIsNone(report["mesh"]["estimated_cost_usd"])
            self.assertEqual(report["mesh"]["cost_status"], "usage_unavailable")

    async def test_unexpected_text_or_output_cap_violation_fails(self):
        for changes in ({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "other"}}]},
                        {"usage": {"prompt_tokens": 10, "completion_tokens": 65, "total_tokens": 75}}):
            report, _, _ = await self.run_fixture(mesh=Transport(mesh_response(**changes)))
            self.assertEqual(report["mesh"]["status"], "failed")

    async def test_transport_exception_and_timeout_are_redacted_without_retry(self):
        for error in (RuntimeError(ENV["MESH_API_KEY"]), asyncio.TimeoutError(ENV["MESH_API_KEY"])):
            report, _, mesh = await self.run_fixture(mesh=Transport(error))
            self.assertEqual(report["mesh"]["status"], "failed")
            self.assertEqual(len(mesh.calls), 1)
            self.assertNotIn(ENV["MESH_API_KEY"], json.dumps(report))

    async def test_real_awaited_timeout_cancels_and_stops_before_mesh(self):
        from deepdive_ai.connectivity import _policy

        class HangingTransport(Transport):
            async def send(self, request):
                self.calls.append(request)
                await asyncio.sleep(60)
                raise AssertionError("Timeout must cancel this exchange")

        brave = HangingTransport(None)
        with patch("deepdive_ai.connectivity._policy", side_effect=lambda seconds: replace(_policy(seconds), timeout_seconds=0.01)):
            report, _, mesh = await self.run_fixture(brave=brave)
        self.assertEqual(report["brave"]["status"], "failed")
        self.assertEqual((len(brave.calls), len(mesh.calls)), (1, 0))


class CommandTests(unittest.TestCase):
    def test_revised_cli_needs_no_version_and_remains_offline_by_default(self):
        arguments = ["connectivity", "--accept-gateway-uncertainty", "--confirm-spend-controls",
                     "--mesh-input-usd-per-million", "0.3", "--mesh-output-usd-per-million", "2.5",
                     "--brave-usd-per-request", "0.005"]
        with patch.dict("os.environ", ENV, clear=True), patch("sys.argv", arguments), \
                patch("deepdive_ai.connectivity.PublicHTTPTransport", side_effect=AssertionError("No transport allowed")), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["ready"])
        self.assertEqual(report["mode"], "preflight")
        self.assertEqual(report["mesh"]["attempts"] + report["brave"]["attempts"], 0)

    def test_default_cli_is_offline_and_missing_configuration_is_safe(self):
        with patch.dict("os.environ", {}, clear=True), patch("sys.argv", ["connectivity"]), \
                patch("socket.getaddrinfo", side_effect=AssertionError("Live DNS forbidden")), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 2)
        self.assertEqual(json.loads(output.getvalue())["mode"], "preflight")

    def test_invalid_versions_and_rates_are_rejected(self):
        for changes in ({"mesh_version": "2026-13"}, {"mesh_input_rate": Decimal("NaN")},
                        {"mesh_output_rate": Decimal("-1")}, {"brave_request_rate": Decimal("Infinity")},
                        {"mesh_input_rate": Decimal("1e-10000")}, {"brave_request_rate": Decimal("0e+999999")},
                        {"gateway_risk_accepted": 1}, {"spending_confirmed": "yes"}):
            with self.assertRaises(ValueError):
                replace(OPTIONS, **changes)
