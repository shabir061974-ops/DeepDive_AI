"""Reproduce first-live-failure signatures with mock sockets/responses only."""

import asyncio
import json
import socket
import ssl
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock, patch

from deepdive_ai.connectivity import TestOptions, _policy, run_connectivity
from deepdive_ai.http_transport import HTTPResponse, PublicHTTPTransport, _PinnedConnection


SECRET = "sensitive-offline-fixture-only"
ENV = {"MESH_API_KEY": SECRET, "BRAVE_SEARCH_API_KEY": SECRET}
OPTIONS = TestOptions(gateway_risk_accepted=True, mesh_input_rate=Decimal("1"),
                      mesh_output_rate=Decimal("1"), brave_request_rate=Decimal("0.005"), spending_confirmed=True)


class Responses:
    def __init__(self, result=None):
        self.result = result
        self.calls = 0

    async def send(self, request):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        if self.result is None:
            raise AssertionError("No Mesh request permitted after Brave failure")
        return self.result


class FailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No live connections"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def check_failure(self, brave, category, status=None):
        mesh = Responses()
        report = await run_connectivity(OPTIONS, execute=True, environment=ENV,
                                        brave_transport=brave, mesh_transport=mesh)
        self.assertEqual(report["brave"]["failure_category"], category)
        self.assertEqual(report["brave"]["http_status"], status)
        self.assertEqual(report["brave"]["status"], "failed")
        self.assertFalse(report["brave"]["response_compatible"])
        self.assertEqual(report["brave"]["attempts"], 1)
        self.assertEqual(report["brave"]["cost_status"], "unknown_failure_may_be_billable")
        self.assertEqual(report["mesh"]["attempts"], 0)
        self.assertEqual(mesh.calls, 0)
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertNotIn("Authorization", json.dumps(report))

    async def test_dns_error_survives_all_sanitizing_layers(self):
        resolver = Mock(side_effect=socket.gaierror(SECRET))
        await self.check_failure(PublicHTTPTransport(resolver), "dns_failure")
        resolver.assert_called_once_with("api.search.brave.com", 443)

    async def test_tls_and_connection_errors_are_distinguished(self):
        for error, category in ((ssl.SSLCertVerificationError(SECRET), "tls_failure"),
                                (ConnectionRefusedError(SECRET), "network_failure"),
                                (TimeoutError(SECRET), "timeout")):
            connection = Mock()
            connection.connect.side_effect = error
            with patch("deepdive_ai.http_transport._PinnedConnection", return_value=connection):
                await self.check_failure(PublicHTTPTransport(lambda host, port: ("8.8.8.8",)), category)
            connection.connect.assert_called_once()
            connection.close.assert_called_once()

    async def test_response_rejected_after_headers_retains_status(self):
        cases = (
            ((("Content-Length", "20000"),), [], "response_too_large"),
            ((("Content-Encoding", "gzip"),), [], "unsupported_encoding"),
            ((("Content-Length", "bad"),), [], "http_protocol_failure"),
            ((("Content-Length", "7"),), [b"short", b""], "incomplete_response"),
        )
        for headers, chunks, category in cases:
            response = Mock(status=200)
            response.getheaders.return_value = headers
            response.read1.side_effect = chunks
            connection = Mock()
            connection.getresponse.return_value = response
            with self.subTest(category=category), patch(
                "deepdive_ai.http_transport._PinnedConnection", return_value=connection
            ):
                await self.check_failure(PublicHTTPTransport(lambda host, port: ("8.8.8.8",)), category, 200)
            connection.connect.assert_called_once()
            response.close.assert_called_once()

    async def test_body_read_socket_timeout_retains_received_status(self):
        response = Mock(status=200)
        response.getheaders.return_value = ()
        response.read1.side_effect = TimeoutError(SECRET)
        connection = Mock()
        connection.getresponse.return_value = response
        with patch("deepdive_ai.http_transport._PinnedConnection", return_value=connection):
            await self.check_failure(PublicHTTPTransport(lambda host, port: ("8.8.8.8",)), "timeout", 200)

    async def test_authentication_and_other_http_errors_have_known_status(self):
        for status, category in ((401, "authentication_http_status"), (403, "authentication_http_status"),
                                 (402, "billing_http_status"), (429, "rate_limited"),
                                 (302, "redirect_rejected"), (503, "provider_http_status"),
                                 (400, "unexpected_http_status")):
            transport = Responses(HTTPResponse(status, body=SECRET.encode()))
            await self.check_failure(transport, category, status)
            self.assertEqual(transport.calls, 1)

    async def test_json_and_unsafe_source_failures_happen_after_full_response(self):
        for body, category in ((b"not-json", "response_parsing"),
                               (b'{"type":"search","web":null}', "response_parsing"),
                               (b'{"type":"search","web":{"results":[{"url":"http://127.0.0.1/"}]}}',
                                "unsafe_destination")):
            transport = Responses(HTTPResponse(200, (("Content-Type", "application/json"),), body))
            await self.check_failure(transport, category, 200)
            self.assertEqual(transport.calls, 1)

    async def test_async_attempt_deadline_has_a_timeout_category(self):
        class HangingTransport:
            async def send(self, request):
                await asyncio.Event().wait()

        with patch("deepdive_ai.connectivity._policy", side_effect=lambda timeout: replace(_policy(timeout), timeout_seconds=0.01)):
            await self.check_failure(HangingTransport(), "timeout")

    async def test_unclassified_exception_is_not_guessed_or_echoed(self):
        transport = Responses(RuntimeError(SECRET))
        await self.check_failure(transport, "internal_error")
        self.assertEqual(transport.calls, 1)

    async def test_private_dns_is_still_rejected(self):
        await self.check_failure(PublicHTTPTransport(lambda host, port: ("127.0.0.1",)), "unsafe_destination")
