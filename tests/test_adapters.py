"""Deterministic adapter and wire tests. No test opens a network connection."""

import asyncio
import hashlib
import io
import json
import socket
import time
import traceback
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from deepdive_ai.http_transport import (
    BoundedHTTPClient, HTTPError, HTTPRequest, HTTPResponse, PublicHTTPTransport,
    RequestLimitExceeded, RequestPolicy, ResponseTooLarge, TransportError, UnsafeURL,
    _PinnedConnection, public_ip, validate_url,
)
from deepdive_ai.mesh import MeshAPIClient, MeshError, MeshRequest, MeshSettings
from deepdive_ai.retrieval import HTTPWebRetriever, RetrievalError, RetrievalSettings, SearchRequest


KEY = "offline-test-credential"
PROMPT = "private offline prompt"
URL = "https://source.example/document"
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
POLICY = RequestPolicy(backoff_seconds=0, max_retry_delay_seconds=0)
JSON_HEADERS = (("Content-Type", "application/json"),)
TEXT_HEADERS = (("Content-Type", "text/plain; charset=utf-8"),)


class ScriptedTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def send(self, request):
        self.calls.append(request)
        if not self.outcomes:
            raise AssertionError("unexpected extra HTTP request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def completion(**changes):
    data = {"id": "fixture-completion", "choices": [{
        "message": {"role": "assistant", "content": "offline response"}, "finish_reason": "stop"}]}
    data.update(changes)
    return HTTPResponse(200, JSON_HEADERS, json.dumps(data).encode())


class NoNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dns_guard = patch("socket.getaddrinfo", side_effect=AssertionError("unexpected real DNS"))
        self.connect_guard = patch.object(_PinnedConnection, "connect", side_effect=AssertionError("unexpected real connection"))
        self.dns_guard.start()
        self.connect_guard.start()
        self.addCleanup(self.dns_guard.stop)
        self.addCleanup(self.connect_guard.stop)


class MeshTests(NoNetworkTests):
    def client(self, transport, **policy):
        return MeshAPIClient(MeshSettings(KEY, policy=replace(POLICY, **policy)), transport)

    async def test_success_uses_mesh_only_and_correct_payload(self):
        transport = ScriptedTransport(completion())
        result = await self.client(transport).complete(MeshRequest("configured-model", PROMPT))
        self.assertEqual(result.text, "offline response")
        self.assertEqual(result.request_id, "fixture-completion")
        request = transport.calls[0]
        self.assertEqual(request.url, "https://api.meshapi.ai/v1/chat/completions")
        self.assertEqual(request.method, "POST")
        self.assertEqual(dict(request.headers)["Authorization"], "Bearer " + KEY)
        self.assertEqual(json.loads(request.body), {"model": "configured-model", "messages": [
            {"role": "user", "content": PROMPT}], "max_tokens": 1024, "stream": False})
        self.assertNotIn(KEY, repr(request))
        self.assertNotIn(PROMPT, repr(request))

    async def test_transient_status_retries_then_succeeds(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                transport = ScriptedTransport(HTTPResponse(status), completion())
                await self.client(transport).complete(MeshRequest("model", PROMPT))
                self.assertEqual(len(transport.calls), 2)

    async def test_retry_exhaustion_is_bounded_and_redacted(self):
        transport = ScriptedTransport(*[HTTPResponse(503, body=KEY.encode()) for _ in range(3)])
        with self.assertRaises(MeshError) as caught:
            await self.client(transport).complete(MeshRequest("model", PROMPT))
        self.assertEqual(len(transport.calls), 3)
        self.assertNotIn(KEY, str(caught.exception))

    async def test_nonretryable_errors_and_redirects_are_not_followed(self):
        for status in (400, 401, 403, 404, 301, 302, 307):
            with self.subTest(status=status):
                transport = ScriptedTransport(HTTPResponse(status, (("Location", "https://other.example"),), KEY.encode()))
                with self.assertRaises(MeshError):
                    await self.client(transport).complete(MeshRequest("model", PROMPT))
                self.assertEqual(len(transport.calls), 1)

    async def test_ambiguous_post_transport_error_is_not_retried(self):
        transport = ScriptedTransport(TransportError(KEY))
        with self.assertRaises(TransportError) as caught:
            await self.client(transport).complete(MeshRequest("model", PROMPT))
        self.assertEqual(len(transport.calls), 1)
        diagnostic = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn(KEY, diagnostic)

    async def test_malformed_json_and_schema_rejected(self):
        bodies = [b"not JSON", b"\xff", b"[]", b"null", b'{"id":"a","id":"b"}',
                  b'{"id":NaN}', json.dumps({"id": 7, "choices": []}).encode()]
        responses = [HTTPResponse(200, JSON_HEADERS, body) for body in bodies]
        responses += [completion(choices=choice) for choice in ([], [None], [{"message": {}}],
            [{"finish_reason": "length", "message": {"role": "assistant", "content": "partial"}}],
            [{"finish_reason": "stop", "message": {"role": "assistant", "content": None}}],
            [{"finish_reason": "stop", "message": {"role": "assistant", "content": ""}}],
            [{"finish_reason": "stop", "message": {"role": "assistant", "content": "text", "tool_calls": [1]}}])]
        for response in responses:
            with self.subTest(response=response), self.assertRaises(MeshError):
                await self.client(ScriptedTransport(response)).complete(MeshRequest("model", PROMPT))

    async def test_response_type_size_and_request_limits(self):
        with self.assertRaises(MeshError):
            await self.client(ScriptedTransport(HTTPResponse(200, TEXT_HEADERS, b"text"))).complete(MeshRequest("m", PROMPT))
        with self.assertRaises(ResponseTooLarge):
            await self.client(ScriptedTransport(completion()), max_response_bytes=10).complete(MeshRequest("m", PROMPT))
        transport = ScriptedTransport()
        with self.assertRaises(RequestLimitExceeded):
            await self.client(transport, max_request_bytes=10).complete(MeshRequest("m", PROMPT))
        with self.assertRaises(MeshError):
            await self.client(transport).complete(MeshRequest("m", PROMPT, 5000))
        self.assertEqual(transport.calls, [])

    async def test_lifetime_request_budget_includes_retries(self):
        transport = ScriptedTransport(HTTPResponse(429), completion())
        with self.assertRaises(RequestLimitExceeded):
            await self.client(transport, max_requests=1).complete(MeshRequest("model", PROMPT))
        self.assertEqual(len(transport.calls), 1)

    async def test_no_requests_at_construction_or_env_loading(self):
        transport = ScriptedTransport()
        self.client(transport)
        self.assertEqual(transport.calls, [])
        with patch.dict("os.environ", {"MESH_API_KEY": KEY}):
            self.assertEqual(MeshSettings.from_environment().api_key, KEY)

    async def test_invalid_unicode_request_is_sanitized_before_transport(self):
        transport = ScriptedTransport()
        with self.assertRaises(MeshError) as caught:
            await self.client(transport).complete(MeshRequest("model", PROMPT + "\ud800"))
        self.assertNotIn(PROMPT, str(caught.exception))
        self.assertEqual(transport.calls, [])


class RetrievalTests(NoNetworkTests):
    def retriever(self, transport, **settings):
        return HTTPWebRetriever(RetrievalSettings(policy=POLICY, **settings), transport, lambda: NOW)

    async def test_fetch_preserves_bytes_hash_time_and_url(self):
        body = "offline fixture: café".encode()
        document = await self.retriever(ScriptedTransport(HTTPResponse(200, TEXT_HEADERS, body))).fetch(URL)
        self.assertEqual(document.content, body)
        self.assertEqual(document.source.url, URL)
        self.assertEqual(document.requested_url, URL)
        self.assertEqual(document.source.provenance.content_sha256, hashlib.sha256(body).hexdigest())
        self.assertEqual(document.source.provenance.retrieved_at, NOW)
        evidence = document.evidence_from_bytes("e1", "t1", 0, len(body))
        self.assertEqual(evidence.excerpt, body.decode())
        self.assertEqual(evidence.source_id, document.source.id)
        self.assertEqual(evidence.locator, f"bytes 0:{len(body)} (UTF-8)")
        for start, end in ((-1, 2), (0, 999), (0, 0), (True, 2), (len(body)-1, len(body))):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                document.evidence_from_bytes("e1", "t1", start, end)

    async def test_redirect_chain_and_final_source_url(self):
        transport = ScriptedTransport(HTTPResponse(302, (("location", "/final"),)), HTTPResponse(200, TEXT_HEADERS, b"fixture"))
        document = await self.retriever(transport).fetch(URL)
        self.assertEqual(document.source.url, "https://source.example/final")
        self.assertEqual(document.redirect_chain, (URL, "https://source.example/final"))
        self.assertEqual(document.requested_url, URL)
        self.assertNotIn("Authorization", dict(transport.calls[1].headers))

    async def test_unsafe_redirects_rejected_before_second_request(self):
        for destination in ("http://source.example/final", "https://127.0.0.1/x", "https://[::1]/", "file:///etc/passwd",
                            "https://user:password@source.example", "https://source.example/\r\nx", "https://source.example\\x"):
            transport = ScriptedTransport(HTTPResponse(302, (("Location", destination),)))
            with self.subTest(destination=destination), self.assertRaises(UnsafeURL):
                await self.retriever(transport).fetch(URL)
            self.assertEqual(len(transport.calls), 1)

    async def test_redirect_limit_loop_and_missing_destination(self):
        for responses, settings in (
            ([HTTPResponse(302, (("Location", "/next"),))], {"max_redirects": 0}),
            ([HTTPResponse(302, (("Location", URL),))], {}),
            ([HTTPResponse(302)], {}),
            ([HTTPResponse(302, (("Location", "/a"), ("location", "/b")))], {}),
        ):
            with self.subTest(settings=settings), self.assertRaises(HTTPError):
                await self.retriever(ScriptedTransport(*responses), **settings).fetch(URL)

    async def test_source_errors_and_empty_or_unsupported_content(self):
        for response in (HTTPResponse(404), HTTPResponse(200, TEXT_HEADERS),
                         HTTPResponse(200, (), b"fixture"),
                         HTTPResponse(200, (("Content-Type", "application/octet-stream"),), b"binary")):
            with self.subTest(response=response), self.assertRaises(RetrievalError):
                await self.retriever(ScriptedTransport(response)).fetch(URL)

    async def test_get_transport_retries_and_exhaustion(self):
        transport = ScriptedTransport(TransportError("redacted"), HTTPResponse(200, TEXT_HEADERS, b"fixture"))
        await self.retriever(transport).fetch(URL)
        self.assertEqual(len(transport.calls), 2)
        transport = ScriptedTransport(*[TransportError("redacted") for _ in range(3)])
        with self.assertRaises(TransportError):
            await self.retriever(transport).fetch(URL)
        self.assertEqual(len(transport.calls), 3)

    async def test_source_ids_change_with_content_or_final_url(self):
        documents = []
        for url, body in ((URL, b"one"), (URL, b"two"), (URL + "2", b"one"), (URL, b"one")):
            documents.append(await self.retriever(ScriptedTransport(HTTPResponse(200, TEXT_HEADERS, body))).fetch(url))
        ids = [doc.source.id for doc in documents]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(ids[0], ids[3])

    async def test_search_never_fabricates_sources(self):
        transport = ScriptedTransport()
        with self.assertRaisesRegex(RetrievalError, "No search provider"):
            await self.retriever(transport).search(SearchRequest("offline"))
        self.assertEqual(transport.calls, [])

    async def test_oversize_response_rejected_even_with_injected_transport(self):
        retriever = HTTPWebRetriever(RetrievalSettings(policy=replace(POLICY, max_response_bytes=2)),
                                     ScriptedTransport(HTTPResponse(200, TEXT_HEADERS, b"fixture")))
        with self.assertRaises(ResponseTooLarge):
            await retriever.fetch(URL)


class PolicyTests(unittest.TestCase):
    def test_unsafe_urls(self):
        urls = ["file:///x", "ftp://source.example/x", "https://localhost", "https://localhost.",
                "https://foo.localhost", "https://metadata.google.internal", "https://foo.local",
                "https://127.0.0.1", "https://10.0.0.1", "https://169.254.169.254", "https://192.168.1.1",
                "https://172.16.0.1", "https://0.0.0.0", "https://[::1]", "https://[fe80::1]",
                "https://[::ffff:127.0.0.1]", "https://[2002:0808:0808::1]", "https://[fc00::1]",
                "https://[64:ff9b::a00:1]",
                "https://[fe80::1%25eth0]", "https://source.example:8080", "https://user@source.example",
                "https://source.example\n/x", "https://source.example\\@other.example", "https://%31%32%37.0.0.1"]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(UnsafeURL):
                validate_url(url)

    def test_normalize_public_urls(self):
        self.assertEqual(validate_url("https://Source.Example.:443/path#fragment"), "https://source.example/path")
        self.assertEqual(validate_url("http://8.8.8.8/"), "http://8.8.8.8/")
        self.assertTrue(public_ip("2606:4700:4700::1111"))
        self.assertFalse(public_ip("224.0.0.1"))
        self.assertFalse(public_ip("100.64.0.1"))

    def test_invalid_settings(self):
        for build in (
            lambda: RequestPolicy(timeout_seconds=0), lambda: RequestPolicy(timeout_seconds=float("nan")),
            lambda: RequestPolicy(backoff_seconds=-1), lambda: RequestPolicy(max_retries=6),
            lambda: RequestPolicy(max_requests=True), lambda: RequestPolicy(max_response_bytes=0),
            lambda: RequestPolicy(max_concurrency=0), lambda: RetrievalSettings(max_redirects=11),
            lambda: MeshSettings(KEY + "\r\nHeader: injected"),
            lambda: MeshSettings(KEY, base_url="https://other.example/v1"),
            lambda: MeshSettings(KEY, base_url="http://api.meshapi.ai/v1"),
        ):
            with self.subTest(build=build), self.assertRaises(ValueError):
                build()


class WireTests(NoNetworkTests):
    async def test_dns_rejects_private_mixed_empty_and_obfuscated_addresses(self):
        for addresses in (("127.0.0.1",), ("8.8.8.8", "10.0.0.1"), (), ("::ffff:10.0.0.1",)):
            with self.subTest(addresses=addresses), self.assertRaises(UnsafeURL):
                await PublicHTTPTransport(lambda host, port: addresses).send(HTTPRequest("GET", "http://2130706433/"))

    async def test_dns_failures_are_sanitized(self):
        def resolver(host, port):
            raise OSError(KEY)

        with self.assertRaises(TransportError) as caught:
            await PublicHTTPTransport(resolver).send(HTTPRequest("GET", URL))
        self.assertNotIn(KEY, "".join(traceback.format_exception(caught.exception)))

    async def test_wire_success_and_size_framing_encoding_checks(self):
        cases = [
            ((("Content-Length", "7"),), [b"fixture", b""], None),
            ((("Content-Length", "99"),), [], ResponseTooLarge),
            ((), [b"12345678901"], ResponseTooLarge),
            ((("Content-Length", "7"),), [b"short", b""], TransportError),
            ((("Content-Length", "bad"),), [], HTTPError),
            ((("Content-Length", "2"), ("content-length", "3")), [], HTTPError),
            ((("Content-Encoding", "gzip"),), [], HTTPError),
            ((("X-Large", "x" * 65536),), [], ResponseTooLarge),
            ((("Transfer-Encoding", "chunked"), ("Content-Length", "7")), [], HTTPError),
        ]
        for headers, chunks, error in cases:
            with self.subTest(headers=headers):
                response = Mock(status=200)
                response.getheaders.return_value = headers
                response.read1.side_effect = chunks
                connection = Mock()
                connection.getresponse.return_value = response
                resolver = Mock(return_value=("8.8.8.8",))
                with patch("deepdive_ai.http_transport._PinnedConnection", return_value=connection) as factory:
                    transport = PublicHTTPTransport(resolver)
                    request = HTTPRequest("GET", URL, max_response_bytes=10)
                    if error:
                        with self.assertRaises(error):
                            await transport.send(request)
                    else:
                        result = await transport.send(request)
                        self.assertEqual(result.body, b"fixture")
                    self.assertEqual(factory.call_args.args[:3], ("source.example", 443, "8.8.8.8"))
                    resolver.assert_called_once_with("source.example", 443)
                    connection.close.assert_called_once()
                    response.close.assert_called_once()

    async def test_dns_is_revalidated_on_each_redirect(self):
        response = Mock(status=302)
        response.getheaders.return_value = (("Location", "https://other.example/"),)
        response.read1.side_effect = [b""]
        connection = Mock()
        connection.getresponse.return_value = response
        resolver = Mock(side_effect=[("8.8.8.8",), ("192.168.1.1",)])
        with patch("deepdive_ai.http_transport._PinnedConnection", return_value=connection) as factory:
            retriever = HTTPWebRetriever(RetrievalSettings(policy=POLICY), PublicHTTPTransport(resolver))
            with self.assertRaises(UnsafeURL):
                await retriever.fetch(URL)
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(factory.call_count, 1)

    async def test_late_dns_result_never_opens_connection(self):
        resolver = Mock(return_value=("8.8.8.8",))
        transport = PublicHTTPTransport(resolver)
        with self.assertRaises(TransportError):
            transport._send(HTTPRequest("GET", URL), time.monotonic() - 1)

    async def test_retry_after_capped_and_exponential_delay(self):
        delays = []

        async def sleep(seconds):
            delays.append(seconds)

        transport = ScriptedTransport(HTTPResponse(429, (("Retry-After", "999999"),)), HTTPResponse(503), HTTPResponse(200))
        client = BoundedHTTPClient(transport, RequestPolicy(backoff_seconds=1, max_retry_delay_seconds=3), sleep)
        await client.exchange("GET", URL)
        self.assertEqual(delays, [3, 2])

    async def test_async_timeout_and_cancellation(self):
        class HangingTransport:
            async def send(self, request):
                await asyncio.Event().wait()

        client = BoundedHTTPClient(HangingTransport(), replace(POLICY, timeout_seconds=0.01, max_retries=0))
        with self.assertRaises(TransportError):
            await client.exchange("GET", URL)
        task = asyncio.create_task(client.exchange("GET", URL))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_concurrency_is_bounded(self):
        class ConcurrentTransport:
            active = 0
            peak = 0

            async def send(self, request):
                self.active += 1
                self.peak = max(self.active, self.peak)
                await asyncio.sleep(0)
                self.active -= 1
                return HTTPResponse(200)

        transport = ConcurrentTransport()
        client = BoundedHTTPClient(transport, replace(POLICY, max_concurrency=1))
        await asyncio.gather(*(client.exchange("GET", URL) for _ in range(4)))
        self.assertEqual(transport.peak, 1)


class ConnectionTests(unittest.TestCase):
    def test_deadline_eof_does_not_become_successful_partial_document(self):
        raw = Mock()
        raw.makefile.return_value = io.BytesIO(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n")
        with patch("socket.socket", return_value=raw), patch(
                "deepdive_ai.http_transport.time.monotonic", side_effect=[0, 0, 0, 0, 2]):
            with self.assertRaisesRegex(TransportError, "deadline exceeded"):
                PublicHTTPTransport(lambda host, port: ("8.8.8.8",))._send(
                    HTTPRequest("GET", "http://source.example/"), 1)

    def test_real_http_parser_and_connection_close_without_network(self):
        for wire, expected_error in (
            (b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\nConnection: close\r\n\r\nfixture", None),
            (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n7\r\nfixture\r\n0\r\n\r\n", None),
            (b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\n\r\nshort", TransportError),
            (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nb\r\n12345678901\r\n0\r\n\r\n", ResponseTooLarge),
        ):
            with self.subTest(wire=wire):
                raw = Mock()
                raw.makefile.return_value = io.BytesIO(wire)
                with patch("socket.socket", return_value=raw), patch("socket.getaddrinfo", side_effect=AssertionError("second DNS resolution")):
                    transport = PublicHTTPTransport(lambda host, port: ("8.8.8.8",))
                    request = HTTPRequest("GET", "http://source.example/path?q=1", max_response_bytes=10)
                    if expected_error:
                        with self.assertRaises(expected_error):
                            transport._send(request, time.monotonic() + 5)
                    else:
                        self.assertEqual(transport._send(request, time.monotonic() + 5).body, b"fixture")
                    sent = b"".join(call.args[0] for call in raw.sendall.call_args_list)
                    self.assertIn(b"GET /path?q=1 HTTP/1.1", sent)
                    self.assertIn(b"Host: source.example", sent)
                    raw.connect.assert_called_once_with(("8.8.8.8", 80))

    def test_ip_connection_retains_original_tls_hostname(self):
        raw = Mock()
        secure = Mock()
        context = Mock()
        context.wrap_socket.return_value = secure
        with patch("socket.socket", return_value=raw) as factory, patch("ssl.create_default_context", return_value=context):
            connection = _PinnedConnection("source.example", 443, "8.8.8.8", 2, True)
            connection.connect()
        factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
        raw.connect.assert_called_once_with(("8.8.8.8", 443))
        context.wrap_socket.assert_called_once_with(raw, server_hostname="source.example")
        self.assertIs(connection.sock, secure)

    def test_connection_failure_closes_socket(self):
        raw = Mock()
        raw.connect.side_effect = OSError("offline failure")
        with patch("socket.socket", return_value=raw):
            with self.assertRaises(OSError):
                _PinnedConnection("source.example", 80, "8.8.8.8", 2, False).connect()
        raw.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
