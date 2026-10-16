"""Standalone source diagnostic: no real DNS, sockets, providers or credentials."""

import asyncio
import io
import json
import socket
import ssl
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from deepdive_ai.http_transport import (
    HTTPResponse, PublicHTTPTransport,
    TransportStage, _PinnedConnection,
)
from deepdive_ai.source_connectivity import SOURCE_URL, main, run_source_diagnostic


PRIVATE = "private-offline-diagnostic-content"
CONNECT = _PinnedConnection.connect


class SourceConnectivityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("No real DNS")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No real sockets")),
                      patch("deepdive_ai.mesh.MeshAPIClient.complete", side_effect=AssertionError("No Mesh")),
                      patch("deepdive_ai.search.BraveSearchDiscovery.discover", side_effect=AssertionError("No Brave"))):
            guard.start()
            self.addCleanup(guard.stop)

    def assert_safe(self, result):
        serialized = json.dumps(result)
        for forbidden in (PRIVATE, SOURCE_URL, "Authorization", "api_key", "#", "?"):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("headers", result)
        self.assertNotIn("body", result)

    async def test_preflight_constructs_no_transport_and_needs_no_environment(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch("deepdive_ai.source_connectivity.PublicHTTPTransport") as factory:
            result = await run_source_diagnostic()
        factory.assert_not_called()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["attempts"], 0)
        self.assertFalse(any(result["stages_reached"].values()))
        self.assertEqual(result["source_hostname"], "developer.mozilla.org")
        self.assert_safe(result)

    async def test_execution_requires_explicit_boolean(self):
        for value in (1, "yes", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await run_source_diagnostic(execute=value)

    async def test_dns_failure_has_no_tcp_or_tls_activity(self):
        resolver = Mock(side_effect=socket.gaierror(PRIVATE))
        result = await run_source_diagnostic(execute=True, transport=PublicHTTPTransport(resolver))
        resolver.assert_called_once_with("developer.mozilla.org", 443)
        self.assertEqual(result["failure_category"], "dns_failure")
        self.assertEqual(result["failure_stage"], "dns")
        self.assertTrue(result["stages_reached"]["dns"])
        self.assertFalse(result["stages_reached"]["tcp_connect"])
        self.assertEqual(result["attempts"], 1)
        self.assert_safe(result)

    async def test_tcp_and_tls_failures_use_verified_transport_with_mocked_sockets(self):
        for stage in ("tcp_connect", "tls_handshake"):
            raw, context = Mock(), Mock()
            if stage == "tcp_connect":
                raw.connect.side_effect = TimeoutError(PRIVATE)
            else:
                context.wrap_socket.side_effect = ssl.SSLCertVerificationError(PRIVATE)
            proxy = SimpleNamespace(**vars(socket))
            proxy.socket = Mock(return_value=raw)
            with patch("deepdive_ai.http_transport.socket", proxy), \
                    patch.object(_PinnedConnection, "connect", CONNECT), \
                    patch("ssl.create_default_context", return_value=context) as tls_context:
                result = await run_source_diagnostic(execute=True,
                    transport=PublicHTTPTransport(lambda host, port: ("8.8.8.8",)))
            self.assertEqual(result["failure_stage"], stage)
            self.assertEqual(result["failure_category"], "timeout" if stage == "tcp_connect" else "tls_failure")
            self.assertEqual(result["attempts"], 1)
            self.assertTrue(result["stages_reached"][stage])
            self.assertFalse(result["stages_reached"]["send_request"])
            raw.connect.assert_called_once_with(("8.8.8.8", 443))
            raw.close.assert_called()
            if stage == "tls_handshake":
                self.assertIn("cafile", tls_context.call_args.kwargs)
                self.assertEqual(context.wrap_socket.call_args.kwargs["server_hostname"], "developer.mozilla.org")
            self.assert_safe(result)

    async def test_http_stages_success_and_body_failure_use_real_response_handling(self):
        for fail in (False, True):
            response = Mock(status=200)
            body = PRIVATE.encode()
            response.getheaders.return_value = (("Content-Length", str(len(body))),)
            response.read1.side_effect = TimeoutError(PRIVATE) if fail else (body, b"")
            connection = Mock()
            connection.getresponse.return_value = response
            with patch("deepdive_ai.http_transport._PinnedConnection", return_value=connection):
                result = await run_source_diagnostic(execute=True,
                    transport=PublicHTTPTransport(lambda host, port: ("8.8.8.8",)))
            self.assertEqual(result["status"], "failed" if fail else "passed")
            self.assertEqual(result["http_status"], 200)
            self.assertTrue(result["stages_reached"]["response_body"])
            self.assertTrue(result["stages_reached"]["response_headers"])
            self.assertTrue(result["stages_reached"]["send_request"])
            # Mocked connect does not report a TLS handshake: never invent it.
            self.assertFalse(result["stages_reached"]["tls_handshake"])
            if fail:
                self.assertEqual(result["failure_stage"], "response_body")
            self.assert_safe(result)
            connection.close.assert_called_once()

    async def test_redirects_and_error_statuses_never_trigger_second_request(self):
        for status, category in ((302, "redirect_rejected"), (403, "authentication_http_status"),
                                 (404, "unexpected_http_status"), (503, "provider_http_status")):
            class Transport:
                calls = []

                async def send(self, request):
                    self.calls.append(request)
                    request.progress.update(TransportStage.HEADERS, status)
                    return HTTPResponse(status, (("Location", "http://127.0.0.1/?api_key=" + PRIVATE),), PRIVATE.encode())

            transport = Transport()
            result = await run_source_diagnostic(execute=True, transport=transport)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(result["failure_category"], category)
            self.assertEqual(result["http_status"], status)
            self.assertEqual(transport.calls[0].timeout_seconds, 10)
            self.assertEqual(transport.calls[0].max_response_bytes, 256_000)
            self.assertEqual(transport.calls[0].url, SOURCE_URL)
            self.assertFalse(any(key.lower() in {"authorization", "x-subscription-token"}
                                 for key, _ in transport.calls[0].headers))
            self.assert_safe(result)

    async def test_private_dns_answers_are_rejected_without_connecting_or_publishing_host(self):
        result = await run_source_diagnostic(execute=True,
            transport=PublicHTTPTransport(lambda host, port: ("10.0.0.1",)))
        self.assertEqual(result["failure_category"], "unsafe_destination")
        self.assertNotIn("source_hostname", result)
        self.assertFalse(result["stages_reached"]["tcp_connect"])
        self.assert_safe(result)

    async def test_enforced_deadline_retains_progress_and_never_retries(self):
        class Hanging:
            async def send(self, request):
                request.progress.update(TransportStage.TCP)
                await asyncio.Event().wait()

        with patch("deepdive_ai.source_connectivity.TIMEOUT_SECONDS", 0.01):
            result = await run_source_diagnostic(execute=True, transport=Hanging())
        self.assertEqual(result["failure_category"], "timeout")
        self.assertEqual(result["failure_stage"], "tcp_connect")
        self.assertEqual(result["attempts"], 1)
        self.assertTrue(result["stages_reached"]["tcp_connect"])
        self.assert_safe(result)

    async def test_oversized_and_unexpected_failures_are_safe(self):
        class Oversized:
            async def send(self, request):
                return HTTPResponse(200, (), b"x" * 256_001)

        class Failed:
            async def send(self, request):
                raise RuntimeError(PRIVATE)

        for transport, category in ((Oversized(), "response_too_large"), (Failed(), "internal_error")):
            result = await run_source_diagnostic(execute=True, transport=transport)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failure_category"], category)
            self.assertEqual(result["attempts"], 1)
            self.assert_safe(result)

    async def test_malformed_status_values_cannot_be_exposed(self):
        class Invalid:
            async def send(self, request):
                return HTTPResponse(PRIVATE)

        result = await run_source_diagnostic(execute=True, transport=Invalid())
        self.assertEqual(result["failure_category"], "http_protocol_failure")
        self.assertIsNone(result["http_status"])
        self.assert_safe(result)


class SourceDiagnosticCLITests(unittest.TestCase):
    def test_cli_preflight_never_constructs_network_transport(self):
        with patch("sys.argv", ["source_connectivity"]), patch.dict("os.environ", {}, clear=True), \
                patch("deepdive_ai.source_connectivity.PublicHTTPTransport") as factory, \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        factory.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["attempts"], 0)

    def test_custom_url_is_not_accepted(self):
        with patch("sys.argv", ["source_connectivity", "--url", "http://127.0.0.1/?secret=" + PRIVATE]), \
                patch("deepdive_ai.source_connectivity.PublicHTTPTransport") as factory, \
                redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()) as errors, \
                self.assertRaises(SystemExit) as error:
            main()
        factory.assert_not_called()
        self.assertEqual(error.exception.code, 2)
        self.assertNotIn(PRIVATE, errors.getvalue())
