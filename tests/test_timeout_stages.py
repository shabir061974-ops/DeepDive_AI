"""Deadline stage diagnostics with mocked DNS, sockets and TLS; no live calls."""

import asyncio
import json
import socket
import unittest
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from deepdive_ai.connectivity import TestOptions, _policy, run_connectivity
from deepdive_ai.http_transport import HTTPRequest, PublicHTTPTransport, TransportProgress, TransportStage


SECRET = "sensitive-offline-only"
OPTIONS = TestOptions(brave_request_rate=Decimal("0.005"), spending_confirmed=True, brave_only=True)
ENV = {"BRAVE_SEARCH_API_KEY": SECRET}


class TimeoutStageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        guard = patch("socket.getaddrinfo", side_effect=AssertionError("Live DNS forbidden"))
        guard.start()
        self.addCleanup(guard.stop)

    async def check_timeout(self, transport, stage, status=None):
        with patch("deepdive_ai.connectivity.MeshAPIClient") as mesh:
            report = await run_connectivity(OPTIONS, execute=True, environment=ENV, brave_transport=transport)
        mesh.assert_not_called()
        self.assertEqual(report["brave"]["failure_category"], "timeout")
        self.assertEqual(report["brave"]["failure_stage"], stage)
        self.assertEqual(report["brave"]["http_status"], status)
        self.assertEqual(report["brave"]["attempts"], 1)
        self.assertEqual(report["mesh"]["attempts"], 0)
        self.assertNotIn(SECRET, json.dumps(report))

    async def test_dns_timeout_is_distinct_from_connection_stages(self):
        resolver = Mock(side_effect=TimeoutError(SECRET))
        await self.check_timeout(PublicHTTPTransport(resolver), "dns")
        resolver.assert_called_once()

    async def test_tcp_and_tls_timeouts_use_mocked_sockets(self):
        for stage in ("tcp_connect", "tls_handshake"):
            raw = Mock()
            context = Mock()
            if stage == "tcp_connect":
                raw.connect.side_effect = TimeoutError(SECRET)
            else:
                context.wrap_socket.side_effect = TimeoutError(SECRET)
            # Replace only the transport's module binding: Windows asyncio uses
            # isinstance(..., socket.socket) while its event loop is running.
            socket_proxy = SimpleNamespace(**vars(socket))
            socket_proxy.socket = Mock(return_value=raw)
            with patch("deepdive_ai.http_transport.socket", socket_proxy), patch("ssl.create_default_context", return_value=context):
                await self.check_timeout(PublicHTTPTransport(lambda host, port: ("8.8.8.8",)), stage)
            raw.close.assert_called()

    async def test_send_headers_and_body_timeouts_are_distinguished(self):
        for stage in ("send_request", "response_headers", "response_body"):
            response = Mock(status=200)
            response.getheaders.return_value = (("Content-Length", "7"),)
            connection = Mock()
            connection.getresponse.return_value = response
            if stage == "send_request":
                connection.request.side_effect = TimeoutError(SECRET)
            elif stage == "response_headers":
                connection.getresponse.side_effect = TimeoutError(SECRET)
            else:
                response.read1.side_effect = TimeoutError(SECRET)
            with patch("deepdive_ai.http_transport._PinnedConnection", return_value=connection):
                await self.check_timeout(PublicHTTPTransport(lambda host, port: ("8.8.8.8",)), stage,
                                         200 if stage == "response_body" else None)
            connection.close.assert_called_once()

    async def test_async_deadline_preserves_last_stage_and_status_after_cancellation(self):
        class HangingTransport:
            async def send(self, request):
                request.progress.update(TransportStage.BODY, 200)
                await asyncio.Event().wait()

        with patch("deepdive_ai.connectivity._policy", side_effect=lambda timeout: replace(_policy(timeout), timeout_seconds=0.01)):
            await self.check_timeout(HangingTransport(), "response_body", 200)

    async def test_uninstrumented_transport_does_not_invent_a_stage(self):
        class HangingTransport:
            async def send(self, request):
                await asyncio.Event().wait()

        with patch("deepdive_ai.connectivity._policy", side_effect=lambda timeout: replace(_policy(timeout), timeout_seconds=0.01)):
            await self.check_timeout(HangingTransport(), None)

    async def test_worker_queue_deadline_is_reported_before_dns(self):
        async def queued_worker(*args):
            await asyncio.Event().wait()

        with patch("deepdive_ai.http_transport.asyncio.to_thread", side_effect=queued_worker), \
                patch("deepdive_ai.connectivity._policy", side_effect=lambda timeout: replace(_policy(timeout), timeout_seconds=0.01)):
            await self.check_timeout(PublicHTTPTransport(), "worker_queue")


class ProgressTests(unittest.TestCase):
    def test_remaining_socket_budget_is_not_reset_after_dns(self):
        response = Mock(status=200)
        response.getheaders.return_value = (("Content-Length", "0"),)
        response.read1.return_value = b""
        connection = Mock()
        connection.getresponse.return_value = response
        progress = TransportProgress()
        with patch("deepdive_ai.http_transport.time.monotonic", return_value=4), \
                patch("deepdive_ai.http_transport.threading.Timer"), \
                patch("deepdive_ai.http_transport._PinnedConnection", return_value=connection) as factory:
            PublicHTTPTransport(lambda host, port: ("8.8.8.8",))._send(
                HTTPRequest("GET", "https://fixture.invalid/", timeout_seconds=10, progress=progress), 10)
        self.assertEqual(factory.call_args.args[3], 6)
        self.assertTrue(connection.sock.settimeout.call_args_list)
        self.assertTrue(all(call.args == (6,) for call in connection.sock.settimeout.call_args_list))
        self.assertEqual(progress.snapshot(), (TransportStage.BODY, 200))

    def test_progress_accepts_only_fixed_stages_and_validated_status(self):
        progress = TransportProgress()
        with self.assertRaises(ValueError):
            progress.update(SECRET)
        progress.update(TransportStage.HEADERS, SECRET)
        self.assertEqual(progress.snapshot(), (TransportStage.HEADERS, None))
