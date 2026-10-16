"""Source-only failure metadata, real adapter composition and mocked sockets."""

import asyncio
import json
import socket
import ssl
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from deepdive_ai.http_transport import (
    FailureCategory, HTTPRequest, HTTPResponse, PublicHTTPTransport,
    TransportError, TransportStage, UnsafeURL,
)
from deepdive_ai.live import ObservedTransport, policy, run_live
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport, plan


SECRET = "private-offline-marker"
URL = "https://SOURCE.example/private-offline-marker?api_key=private-offline-marker#private-offline-marker"


class SourceDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        guard = patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS"))
        guard.start()
        self.addCleanup(guard.stop)

    async def test_dns_tcp_and_tls_failures_record_only_hostname(self):
        for stage, category in (("dns", "dns_failure"), ("tcp_connect", "timeout"),
                                ("tls_handshake", "tls_failure")):
            with self.subTest(stage=stage):
                resolver = Mock(return_value=("8.8.8.8",))
                raw, context = Mock(), Mock()
                if stage == "dns":
                    resolver.side_effect = socket.gaierror(SECRET)
                elif stage == "tcp_connect":
                    raw.connect.side_effect = TimeoutError(SECRET)
                else:
                    context.wrap_socket.side_effect = ssl.SSLCertVerificationError(SECRET)
                proxy = SimpleNamespace(**vars(socket))
                proxy.socket = Mock(return_value=raw)
                observer = ObservedTransport(PublicHTTPTransport(resolver), source_diagnostics=True)
                with patch("deepdive_ai.http_transport.socket", proxy), \
                        patch("ssl.create_default_context", return_value=context), self.assertRaises(TransportError):
                    await observer.send(HTTPRequest("GET", URL, headers=(("Authorization", SECRET),)))
                self.assertEqual(observer.attempts, 1)
                self.assertEqual(observer.failure, {"category": category, "stage": stage,
                                 "http_status": None, "source_hostname": "source.example"})
                self.assertNotIn(SECRET, json.dumps(observer.failure))
                resolver.assert_called_once_with("source.example", 443)
                if stage == "dns":
                    proxy.socket.assert_not_called()
                else:
                    raw.connect.assert_called_once_with(("8.8.8.8", 443))
                    raw.close.assert_called()
                if stage != "tls_handshake":
                    context.wrap_socket.assert_not_called()

    async def test_invalid_and_dns_private_destinations_do_not_publish_hostnames(self):
        for url in ("file:///private-offline-marker", "http://localhost/", "http://127.0.0.1/",
                    "https://user:private-offline-marker@source.example/", "https://source.example/\nsecret"):
            resolver = Mock(side_effect=AssertionError("Unsafe URL must not resolve"))
            observer = ObservedTransport(PublicHTTPTransport(resolver), source_diagnostics=True)
            with self.subTest(url=url), self.assertRaises(UnsafeURL):
                await observer.send(HTTPRequest("GET", url))
            self.assertNotIn("source_hostname", observer.failure or {})
            resolver.assert_not_called()
        observer = ObservedTransport(PublicHTTPTransport(lambda host, port: ("10.0.0.1",)),
                                     source_diagnostics=True)
        with self.assertRaises(UnsafeURL):
            await observer.send(HTTPRequest("GET", URL))
        self.assertEqual(observer.failure["category"], "unsafe_destination")
        self.assertNotIn("source_hostname", observer.failure)

    async def test_provider_observers_do_not_record_destination_metadata(self):
        class Failed:
            async def send(self, request):
                raise TransportError(SECRET, category=FailureCategory.TIMEOUT, stage=TransportStage.TCP)

        observer = ObservedTransport(Failed())
        with self.assertRaises(TransportError):
            await observer.send(HTTPRequest("GET", URL))
        self.assertNotIn("source_hostname", observer.failure)

    async def test_success_does_not_retain_url_or_hostname(self):
        class Successful:
            async def send(self, request):
                return HTTPResponse(200)

        observer = ObservedTransport(Successful(), source_diagnostics=True)
        await observer.send(HTTPRequest("GET", URL))
        self.assertIsNone(observer.failure)
        self.assertNotIn(SECRET, repr(vars(observer)))

    async def test_redirect_destination_failure_is_persisted_without_secrets(self):
        class RedirectThenFailure:
            def __init__(self):
                self.calls = 0

            async def send(self, request):
                self.calls += 1
                if self.calls == 1:
                    return HTTPResponse(302, (("Location", "https://redirect.example/private-offline-marker?token="
                                              + SECRET + "#" + SECRET),))
                raise TransportError(SECRET, category=FailureCategory.TIMEOUT, stage=TransportStage.TCP)

        source = RedirectThenFailure()
        result, folder, audit = await self.run_failure(source)
        self.assertEqual(result["attempts"], {"mesh": 1, "brave": 1, "source": 2})
        self.assertEqual(result["diagnostics"]["source"]["source_hostname"], "redirect.example")
        self.assertEqual(source.calls, 2)

    async def test_enforced_timeout_preserves_hostname_when_cancellation_is_reclassified(self):
        class Hanging:
            async def send(self, request):
                request.progress.update(TransportStage.TCP)
                await asyncio.Event().wait()

        def short_policy(timeout, requests, response_bytes):
            configured = policy(timeout, requests, response_bytes)
            return replace(configured, timeout_seconds=0.01) if requests == 4 else configured

        with patch("deepdive_ai.live.policy", side_effect=short_policy):
            result, _, _ = await self.run_failure(Hanging())
        self.assertEqual(result["attempts"], {"mesh": 1, "brave": 1, "source": 1})
        self.assertEqual(result["diagnostics"]["source"], {"category": "timeout", "stage": "tcp_connect",
                         "http_status": None, "source_hostname": "source.example"})

    async def run_failure(self, source):
        Path("artifacts").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result = await run_live(OPTIONS, execute=True, environment=ENV, output_root=root,
                                    mesh_transport=MeshTransport(plan()), brave_transport=SearchTransport((URL,)),
                                    source_transport=source)
        folder = root / result["run_id"]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_category"], "timeout")
        self.assertEqual(json.loads((folder / "outcome.json").read_text()), result)
        audit = [json.loads(line) for line in (folder / "audit.jsonl").read_text().splitlines()]
        self.assertEqual([event["kind"] for event in audit], ["started", "planned", "failed"])
        self.assertFalse((folder / "report.json").exists())
        for secret in (SECRET, *ENV.values(), URL, "Authorization"):
            self.assertNotIn(secret, json.dumps(result) + (folder / "audit.jsonl").read_text())
        return result, folder, audit
