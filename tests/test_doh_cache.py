"""Bounded DoH answer cache. Offline only: every connection is a fake."""

import json
import threading
import unittest
from unittest.mock import patch

from deepdive_ai import http_transport
from deepdive_ai.http_transport import (
    DNS_RESOLVER_ENV, DOH_CACHE_MAX_ENTRIES, DOH_CACHE_MAX_TTL_SECONDS, DoHCache, DoHResolver, HTTPRequest,
    PublicHTTPTransport, UnsafeURL, _PinnedConnection, resolver_from_environment,
)
from test_dns_over_https import HOST, Connector, FakeResponse, OfflineTestCase


def answer(*addresses, name=HOST, ttl=60, cname_ttl=None):
    records = [{"name": f"{name}.", "type": 5, "TTL": ttl if cname_ttl is None else cname_ttl, "data": "edge.example.net."}]
    records += [{"name": "edge.example.net.", "type": 1, "TTL": ttl, "data": a} for a in addresses]
    return FakeResponse(json.dumps({"Status": 0, "TC": False, "Question": [{"name": f"{name}.", "type": 1}],
                                    "Answer": records}).encode())


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ResolverCacheTests(OfflineTestCase):
    def resolver(self, *outcomes, cache=None):
        connector = Connector(*outcomes)
        clock = Clock()
        return DoHResolver(connector, clock, cache if cache is not None else DoHCache()), connector, clock

    def test_hit_skips_network_until_ttl_expires(self):
        resolver, connector, clock = self.resolver(answer("93.184.216.34", ttl=30), answer("93.184.216.35", ttl=30))
        self.assertEqual(resolver(HOST, 443), ("93.184.216.34",))
        clock.now += 29.9
        self.assertEqual(resolver(HOST, 443), ("93.184.216.34",))
        self.assertEqual(len(connector.calls), 1)
        clock.now += 0.1
        self.assertEqual(resolver(HOST, 443), ("93.184.216.35",))
        self.assertEqual(len(connector.calls), 2)

    def test_minimum_record_ttl_and_cap_apply(self):
        resolver, connector, clock = self.resolver(answer("93.184.216.34", ttl=600, cname_ttl=20), answer("93.184.216.34"))
        resolver(HOST, 443)
        clock.now += 20
        resolver(HOST, 443)
        self.assertEqual(len(connector.calls), 2)
        resolver, connector, clock = self.resolver(answer("93.184.216.34", ttl=86_400), answer("93.184.216.34"))
        resolver(HOST, 443)
        clock.now += DOH_CACHE_MAX_TTL_SECONDS - 1
        resolver(HOST, 443)
        self.assertEqual(len(connector.calls), 1)
        clock.now += 1
        resolver(HOST, 443)
        self.assertEqual(len(connector.calls), 2)

    def test_zero_missing_or_invalid_ttl_is_not_cached_but_still_resolves(self):
        for ttl in (0, -5, None, True, "60", 1.5):
            with self.subTest(ttl=ttl):
                resolver, connector, _ = self.resolver(answer("93.184.216.34", ttl=ttl), answer("93.184.216.34"))
                self.assertEqual(resolver(HOST, 443), ("93.184.216.34",))
                resolver(HOST, 443)
                self.assertEqual(len(connector.calls), 2)

    def test_failures_are_not_cached_and_fallback_still_runs_on_miss(self):
        resolver, connector, _ = self.resolver(TimeoutError(), TimeoutError(), TimeoutError(), answer("93.184.216.34"))
        with self.assertRaises(TimeoutError):
            resolver(HOST, 443)
        self.assertEqual(resolver(HOST, 443), ("93.184.216.34",))
        self.assertEqual([address for address, _ in connector.calls], ["1.1.1.1", "8.8.8.8", "1.1.1.1", "8.8.8.8"])
        resolver(HOST, 443)
        self.assertEqual(len(connector.calls), 4)

    def test_unsafe_answers_are_never_cached(self):
        for addresses in (("10.0.0.1",), ("93.184.216.34", "127.0.0.1"), ("169.254.169.254",)):
            with self.subTest(addresses=addresses):
                cache = DoHCache()
                resolver, connector, _ = self.resolver(answer(*addresses), answer(*addresses), cache=cache)
                self.assertEqual(resolver(HOST, 443), addresses)
                self.assertEqual(len(cache), 0)
                resolver(HOST, 443)
                self.assertEqual(len(connector.calls), 2)

    def test_tampered_cache_entry_is_revalidated_and_dropped(self):
        cache = DoHCache()
        resolver, connector, clock = self.resolver(answer("93.184.216.36"), cache=cache)
        cache._entries[HOST] = (clock.now + 60, ("192.168.1.1",))
        self.assertIsNone(cache.get(HOST, clock.now))
        self.assertEqual(len(cache), 0)
        self.assertEqual(resolver(HOST, 443), ("93.184.216.36",))
        self.assertEqual(len(connector.calls), 1)

    def test_cache_is_bounded_lru(self):
        cache = DoHCache(max_entries=2)
        cache.put("a.example", ("93.184.216.1",), 60, 0)
        cache.put("b.example", ("93.184.216.2",), 60, 0)
        self.assertEqual(cache.get("a.example", 1), ("93.184.216.1",))  # a becomes most recent
        cache.put("c.example", ("93.184.216.3",), 60, 1)
        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.get("b.example", 1))
        self.assertIsNotNone(cache.get("a.example", 1))
        self.assertIsNotNone(cache.get("c.example", 1))

    def test_expired_entries_are_purged_on_insert(self):
        cache = DoHCache(max_entries=8)
        for index in range(5):
            cache.put(f"h{index}.example", ("93.184.216.1",), 10, 0)
        cache.put("fresh.example", ("93.184.216.2",), 10, 10)
        self.assertEqual(len(cache), 1)

    def test_default_cache_limit_holds_under_many_hosts(self):
        cache = DoHCache()
        for index in range(DOH_CACHE_MAX_ENTRIES * 3):
            cache.put(f"h{index}.example", ("93.184.216.1",), 60, 0)
        self.assertEqual(len(cache), DOH_CACHE_MAX_ENTRIES)

    def test_invalid_cache_configuration_is_rejected(self):
        for kwargs in ({"max_entries": 0}, {"max_entries": True}, {"max_ttl_seconds": 0},
                       {"max_ttl_seconds": float("inf")}, {"max_ttl_seconds": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                DoHCache(**kwargs)

    def test_ip_literals_and_rejected_names_bypass_the_cache(self):
        cache = DoHCache()
        resolver, connector, _ = self.resolver(cache=cache)
        self.assertEqual(resolver("93.184.216.34", 443), ("93.184.216.34",))
        with self.assertRaises(OSError):
            resolver("bad name", 443)
        self.assertEqual((len(cache), connector.calls), (0, []))

    def test_concurrent_access_is_safe(self):
        cache = DoHCache(max_entries=16)
        errors = []

        def worker(offset):
            try:
                for index in range(200):
                    host = f"h{(offset + index) % 40}.example"
                    cache.put(host, ("93.184.216.1",), 60, 0)
                    cache.get(host, 1)
            except Exception as error:  # pragma: no cover - reported below
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertLessEqual(len(cache), 16)

    def test_environment_resolvers_share_one_process_cache(self):
        first = resolver_from_environment({DNS_RESOLVER_ENV: "doh"})
        second = resolver_from_environment({DNS_RESOLVER_ENV: "doh"})
        self.assertIs(first._cache, second._cache)
        self.assertIs(first._cache, http_transport._PROCESS_DOH_CACHE)
        self.assertIsNot(DoHResolver()._cache, DoHResolver()._cache)


class CachedTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        guard = patch("socket.getaddrinfo", side_effect=AssertionError("system DNS must not be used"))
        guard.start()
        self.addCleanup(guard.stop)

    async def test_cached_answer_is_still_pinned_and_public_checked(self):
        seen = []

        def record(connection):
            seen.append((connection.address, connection.host, connection.tls))
            raise ConnectionRefusedError

        resolver = DoHResolver(Connector(answer("93.184.216.34")), Clock())
        transport = PublicHTTPTransport(resolver)
        with patch.object(_PinnedConnection, "connect", autospec=True, side_effect=record):
            for _ in range(2):
                with self.assertRaises(Exception):
                    await transport.send(HTTPRequest("GET", f"https://{HOST}/", timeout_seconds=5))
        self.assertEqual(seen, [("93.184.216.34", HOST, True)] * 2)

    async def test_unsafe_answer_is_rejected_on_every_request(self):
        resolver = DoHResolver(Connector(answer("10.0.0.1"), answer("10.0.0.1")), Clock())
        transport = PublicHTTPTransport(resolver)
        with patch.object(_PinnedConnection, "connect", side_effect=AssertionError("must not connect")):
            for _ in range(2):
                with self.assertRaises(UnsafeURL):
                    await transport.send(HTTPRequest("GET", f"https://{HOST}/", timeout_seconds=5))


if __name__ == "__main__":
    unittest.main()
