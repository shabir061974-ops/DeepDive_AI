"""Offline DNS-over-HTTPS resolver tests. No test performs real DNS or opens a socket."""

import io
import itertools
import json
import socket
import ssl
import unittest
from unittest.mock import patch

from deepdive_ai.http_transport import (
    DNS_RESOLVER_ENV, DOH_ATTEMPT_SECONDS, DOH_MAX_ANSWERS, DOH_MAX_RESPONSE_BYTES, DoHResolver,
    FailureCategory, HTTPRequest, PublicHTTPTransport, TransportError, TransportStage, UnsafeURL,
    _PinnedConnection, _doh_connection, resolver_from_environment, system_resolver,
)


HOST = "api.example.com"


def answer(*addresses, name=HOST, status=0, extra=None):
    data = {"Status": status, "TC": False, "Question": [{"name": f"{name}.", "type": 1}],
            "Answer": [{"name": f"{HOST}.", "type": 5, "data": "edge.example.net."}]
            + [{"name": "edge.example.net.", "type": 1, "data": a} for a in addresses]}
    data.update(extra or {})
    return json.dumps(data).encode()


class FakeResponse:
    def __init__(self, body=b"", status=200, headers=None, chunk=65536):
        self.status = status
        self.headers = {"content-type": "application/dns-json"} if headers is None else headers
        self.stream = io.BytesIO(body)
        self.chunk = chunk
        self.closed = False

    def getheader(self, name):
        return self.headers.get(name.lower())

    def read1(self, amount):
        return self.stream.read(min(amount, self.chunk))

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, outcome):
        self.outcome = outcome
        self.sock = None
        self.requests = []
        self.closed = False

    def request(self, method, url, headers=None):
        self.requests.append((method, url, dict(headers or {})))
        if isinstance(self.outcome, BaseException):
            raise self.outcome

    def getresponse(self):
        return self.outcome

    def close(self):
        self.closed = True


class Connector:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.connections = []

    def __call__(self, address, timeout):
        if not self.outcomes:
            raise AssertionError("unexpected extra DNS-over-HTTPS attempt")
        self.calls.append((address, timeout))
        connection = FakeConnection(self.outcomes.pop(0))
        self.connections.append(connection)
        return connection


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        for target in ("socket.getaddrinfo", "socket.create_connection", "socket.socket.connect",
                       "socket.socket.connect_ex"):
            guard = patch(target, side_effect=AssertionError("real network access forbidden"))
            guard.start()
            self.addCleanup(guard.stop)


class ConfigurationTests(OfflineTestCase):
    def test_default_and_system_select_os_resolver(self):
        self.assertIs(resolver_from_environment({}), system_resolver)
        self.assertIs(resolver_from_environment({DNS_RESOLVER_ENV: "system"}), system_resolver)

    def test_doh_selects_doh_resolver(self):
        self.assertIsInstance(resolver_from_environment({DNS_RESOLVER_ENV: "doh"}), DoHResolver)

    def test_invalid_values_are_rejected_without_echo(self):
        for value in ("", "DOH", " doh", "doh ", "dot", "system,doh", "https://1.1.1.1/dns-query"):
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                resolver_from_environment({DNS_RESOLVER_ENV: value})
            if value:
                self.assertNotIn(value, str(caught.exception))

    def test_transport_reads_environment_only_without_explicit_resolver(self):
        with patch.dict("os.environ", {DNS_RESOLVER_ENV: "doh"}):
            self.assertIsInstance(PublicHTTPTransport()._resolver, DoHResolver)
        with patch.dict("os.environ", {DNS_RESOLVER_ENV: "invalid"}):
            with self.assertRaises(ValueError):
                PublicHTTPTransport()
            explicit = lambda host, port: ("93.184.216.34",)
            self.assertIs(PublicHTTPTransport(explicit)._resolver, explicit)
        with patch.dict("os.environ", {}, clear=True):
            self.assertIs(PublicHTTPTransport()._resolver, system_resolver)

    def test_default_connection_verifies_certificate_against_fixed_ip(self):
        connection = _doh_connection("1.1.1.1", DOH_ATTEMPT_SECONDS)
        self.assertEqual((connection.host, connection.port, connection.timeout), ("1.1.1.1", 443, DOH_ATTEMPT_SECONDS))
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(connection._context.check_hostname)
        self.assertIsNone(connection.sock)


class ResolverTests(OfflineTestCase):
    def test_success_returns_unique_a_records_in_order(self):
        connector = Connector(FakeResponse(answer("93.184.216.34", "93.184.216.35", "93.184.216.34")))
        self.assertEqual(DoHResolver(connector)(HOST, 443), ("93.184.216.34", "93.184.216.35"))
        self.assertEqual(connector.calls, [("1.1.1.1", DOH_ATTEMPT_SECONDS)])
        method, url, headers = connector.connections[0].requests[0]
        self.assertEqual((method, url), ("GET", f"/dns-query?name={HOST}&type=A"))
        self.assertEqual(headers, {"Accept": "application/dns-json", "Accept-Encoding": "identity"})
        self.assertTrue(connector.connections[0].closed)

    def test_question_name_matches_case_insensitively_with_trailing_dot(self):
        connector = Connector(FakeResponse(answer("93.184.216.34", name=HOST.upper())))
        self.assertEqual(DoHResolver(connector)(HOST, 443), ("93.184.216.34",))

    def test_ip_literals_are_returned_without_lookup(self):
        connector = Connector()
        self.assertEqual(DoHResolver(connector)("93.184.216.34", 443), ("93.184.216.34",))
        self.assertEqual(DoHResolver(connector)("2606:4700::1111", 443), ("2606:4700::1111",))
        self.assertEqual(connector.calls, [])

    def test_invalid_names_never_reach_the_provider(self):
        connector = Connector()
        for host in ("", "a b", "a/b", "a&type=AAAA", "Upper.example", "a..b", "x" * 254, ("a" * 64) + ".com"):
            with self.subTest(host=host), self.assertRaises(socket.gaierror):
                DoHResolver(connector)(host, 443)
        self.assertEqual(connector.calls, [])

    def test_failure_falls_back_once_to_second_endpoint(self):
        for first in (TimeoutError(), ConnectionResetError(), ssl.SSLError(), FakeResponse(b"{}"),
                      FakeResponse(answer("93.184.216.34"), status=503)):
            with self.subTest(first=type(first).__name__):
                connector = Connector(first, FakeResponse(answer("93.184.216.34")))
                self.assertEqual(DoHResolver(connector)(HOST, 443), ("93.184.216.34",))
                self.assertEqual([a for a, _ in connector.calls], ["1.1.1.1", "8.8.8.8"])
                self.assertEqual(connector.connections[1].requests[0][1], f"/resolve?name={HOST}&type=A")

    def test_both_endpoints_failing_raises_dns_failure_after_two_attempts(self):
        connector = Connector(ConnectionRefusedError(), FakeResponse(b"not json"))
        with self.assertRaises(socket.gaierror) as caught:
            DoHResolver(connector)(HOST, 443)
        self.assertEqual(len(connector.calls), 2)
        self.assertNotIn("json", str(caught.exception).lower())

    def test_both_endpoints_timing_out_raises_timeout(self):
        connector = Connector(TimeoutError(), socket.timeout())
        with self.assertRaises(TimeoutError):
            DoHResolver(connector)(HOST, 443)
        self.assertEqual(len(connector.calls), 2)

    def test_malformed_or_unsafe_responses_are_rejected(self):
        oversized = json.dumps({"Status": 0, "pad": "x" * DOH_MAX_RESPONSE_BYTES}).encode()
        cases = {
            "redirect": FakeResponse(answer("93.184.216.34"), status=301),
            "server_error": FakeResponse(answer("93.184.216.34"), status=500),
            "nxdomain": FakeResponse(answer("93.184.216.34", status=3)),
            "status_bool": FakeResponse(answer("93.184.216.34", extra={"Status": False})),
            "status_missing": FakeResponse(answer("93.184.216.34", extra={"Status": None})),
            "truncated": FakeResponse(answer("93.184.216.34", extra={"TC": True})),
            "tc_missing": FakeResponse(answer("93.184.216.34", extra={"TC": None})),
            "not_json": FakeResponse(b"<html>"),
            "not_utf8": FakeResponse(b"\xff\xfe{}"),
            "nan_constant": FakeResponse(b'{"Status": NaN}'),
            "deep_nesting": FakeResponse(b"[" * 30000 + b"]" * 30000),
            "array_root": FakeResponse(b"[]"),
            "question_mismatch": FakeResponse(answer("93.184.216.34", name="other.example")),
            "question_missing": FakeResponse(answer("93.184.216.34", extra={"Question": None})),
            "question_type_bool": FakeResponse(answer("93.184.216.34", extra={"Question": [{"name": HOST, "type": True}]})),
            "answer_missing": FakeResponse(answer(extra={"Answer": None})),
            "answer_empty": FakeResponse(answer(extra={"Answer": []})),
            "cname_only": FakeResponse(answer()),
            "too_many_answers": FakeResponse(answer(*["93.184.216.34"] * (DOH_MAX_ANSWERS + 1))),
            "answer_not_object": FakeResponse(answer(extra={"Answer": ["93.184.216.34"]})),
            "type_bool": FakeResponse(answer(extra={"Answer": [{"type": True, "data": "93.184.216.34"}]})),
            "data_not_string": FakeResponse(answer(extra={"Answer": [{"type": 1, "data": 1572395042}]})),
            "data_ipv6_in_a": FakeResponse(answer("2606:4700::1111")),
            "data_out_of_range": FakeResponse(answer("93.184.216.300")),
            "data_padded": FakeResponse(answer(" 93.184.216.34")),
            "data_octal": FakeResponse(answer("093.184.216.034")),
            "declared_too_large": FakeResponse(answer("93.184.216.34"), headers={"content-length": str(DOH_MAX_RESPONSE_BYTES + 1)}),
            "declared_invalid": FakeResponse(answer("93.184.216.34"), headers={"content-length": "+12"}),
            "declared_mismatch": FakeResponse(answer("93.184.216.34"), headers={"content-length": "9999"}),
            "streamed_too_large": FakeResponse(oversized, chunk=4096),
            "compressed": FakeResponse(answer("93.184.216.34"), headers={"content-encoding": "gzip"}),
        }
        for name, response in cases.items():
            with self.subTest(case=name):
                second = FakeResponse(response.stream.getvalue(), response.status, response.headers, response.chunk)
                connector = Connector(response, second)
                with self.assertRaises(socket.gaierror):
                    DoHResolver(connector)(HOST, 443)
                self.assertEqual(len(connector.calls), 2)

    def test_slow_body_exceeding_attempt_deadline_times_out(self):
        ticks = itertools.count(0.0, DOH_ATTEMPT_SECONDS + 0.5)  # every clock read passes the deadline
        clock = lambda: next(ticks)
        body = answer("93.184.216.34")
        connector = Connector(FakeResponse(body, chunk=8), FakeResponse(body, chunk=8))
        with self.assertRaises(TimeoutError):
            DoHResolver(connector, clock)(HOST, 443)
        self.assertEqual(len(connector.calls), 2)


class TransportIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        guard = patch("socket.getaddrinfo", side_effect=AssertionError("system DNS must not be used"))
        guard.start()
        self.addCleanup(guard.stop)

    async def send(self, resolver, url=f"https://{HOST}/path"):
        return await PublicHTTPTransport(resolver).send(HTTPRequest("GET", url, timeout_seconds=5))

    async def test_private_or_mixed_answers_are_rejected_before_connecting(self):
        for addresses in (("10.0.0.1",), ("127.0.0.1",), ("93.184.216.34", "192.168.1.1"), ("169.254.169.254",)):
            with self.subTest(addresses=addresses):
                resolver = DoHResolver(Connector(FakeResponse(answer(*addresses))))
                with patch.object(_PinnedConnection, "connect", side_effect=AssertionError("must not connect")):
                    with self.assertRaises(UnsafeURL):
                        await self.send(resolver)

    async def test_public_answer_is_pinned_with_original_tls_hostname(self):
        seen = []

        def record(connection):
            seen.append((connection.address, connection.host, connection.tls))
            raise ConnectionRefusedError

        resolver = DoHResolver(Connector(FakeResponse(answer("93.184.216.34", "93.184.216.35"))))
        with patch.object(_PinnedConnection, "connect", autospec=True, side_effect=record):
            with self.assertRaises(TransportError) as caught:
                await self.send(resolver)
        self.assertEqual(seen, [("93.184.216.34", HOST, True)])
        self.assertEqual(caught.exception.category, FailureCategory.NETWORK)
        self.assertEqual(caught.exception.stage, TransportStage.TCP)

    async def test_resolver_failures_map_to_sanitized_categories(self):
        cases = ((Connector(ConnectionRefusedError(), FakeResponse(b"{}")), FailureCategory.DNS),
                 (Connector(TimeoutError(), TimeoutError()), FailureCategory.TIMEOUT))
        for connector, category in cases:
            with self.subTest(category=category):
                with self.assertRaises(TransportError) as caught:
                    await self.send(DoHResolver(connector))
                self.assertEqual(caught.exception.category, category)
                self.assertEqual(caught.exception.stage, TransportStage.DNS)
                self.assertNotIn("1.1.1.1", str(caught.exception))

    async def test_url_validation_still_precedes_resolution(self):
        connector = Connector()
        for url in ("https://localhost/", "https://10.0.0.1/", "https://foo.internal/", "file:///etc/passwd"):
            with self.subTest(url=url), self.assertRaises(UnsafeURL):
                await self.send(DoHResolver(connector), url)
        self.assertEqual(connector.calls, [])


if __name__ == "__main__":
    unittest.main()
