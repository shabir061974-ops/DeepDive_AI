"""Offline provider-contract regressions and proposed smoke-test request budgets."""

import json
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from deepdive_ai.http_transport import HTTPResponse, RequestLimitExceeded, RequestPolicy, _PinnedConnection
from deepdive_ai.mesh import MeshAPIClient, MeshError, MeshRequest, MeshSettings
from deepdive_ai.retrieval import SearchRequest
from deepdive_ai.search import BraveSearchDiscovery, BraveSearchSettings, DiscoveryError


KEY = "offline-readiness-placeholder"
HEADERS = (("Content-Type", "application/json"),)


class Responses:
    def __init__(self, *responses: HTTPResponse):
        self.responses = list(responses)
        self.calls = []

    async def send(self, request):
        self.calls.append(request)
        if not self.responses:
            raise AssertionError("Unexpected extra HTTP attempt")
        return self.responses.pop(0)


def search_response() -> HTTPResponse:
    return HTTPResponse(200, HEADERS, b'{"type":"search","web":{"results":[]}}')


def mesh_response() -> HTTPResponse:
    return HTTPResponse(200, HEADERS + (("X-Mesh-Routing-Fallback", "false"),), json.dumps({
        "id": "offline-completion", "model": "offline-model", "choices": [{"finish_reason": "stop",
        "message": {"role": "assistant", "content": "OK"}}],
    }).encode())


def smoke_policy(timeout: int) -> RequestPolicy:
    return RequestPolicy(timeout_seconds=timeout, max_retries=0, max_requests=1,
                         max_concurrency=1, max_request_bytes=2048, max_response_bytes=16_384)


class ProviderReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (
            patch("socket.getaddrinfo", side_effect=AssertionError("Live DNS forbidden")),
            patch.object(_PinnedConnection, "connect", side_effect=AssertionError("Live connection forbidden")),
        ):
            guard.start()
            self.addCleanup(guard.stop)

    def test_documented_country_default_in_direct_and_environment_settings(self):
        for settings in (BraveSearchSettings(KEY),
                         BraveSearchSettings.from_environment({"BRAVE_SEARCH_API_KEY": KEY})):
            self.assertEqual(settings.country, "US")
            self.assertEqual(settings.search_lang, "en")
            self.assertNotIn(KEY, repr(settings))

    def test_country_requires_two_uppercase_ascii_letters(self):
        for country in ("ALL", "us", "U", "USA", "12", "ÅB", None):
            with self.subTest(country=country), self.assertRaises(ValueError):
                BraveSearchSettings(KEY, country=country)

    async def test_search_requests_web_only_with_explicit_locale(self):
        transport = Responses(search_response())
        settings = BraveSearchSettings.from_environment(
            {"BRAVE_SEARCH_API_KEY": KEY}, country="GB", policy=smoke_policy(10))
        result = await BraveSearchDiscovery(settings, transport).discover(SearchRequest("offline fixture", 1))
        self.assertEqual(result, ())
        request = transport.calls[0]
        self.assertEqual(parse_qs(urlsplit(request.url).query), {
            "q": ["offline fixture"], "count": ["1"], "search_lang": ["en"],
            "country": ["GB"], "spellcheck": ["false"], "result_filter": ["web"],
        })
        self.assertEqual(request.timeout_seconds, 10)
        self.assertEqual(dict(request.headers)["X-Subscription-Token"], KEY)
        self.assertNotIn(KEY, repr(request))

    async def test_brave_smoke_errors_never_retry_or_follow_redirects(self):
        for status in (301, 307, 401, 403, 429, 500, 503):
            transport = Responses(HTTPResponse(status, body=KEY.encode()))
            provider = BraveSearchDiscovery(BraveSearchSettings(KEY, policy=smoke_policy(10)), transport)
            with self.subTest(status=status), self.assertRaises(DiscoveryError) as caught:
                await provider.discover(SearchRequest("offline fixture", 1))
            self.assertEqual(len(transport.calls), 1)
            self.assertNotIn(KEY, str(caught.exception))

    async def test_mesh_smoke_errors_never_retry_or_follow_redirects(self):
        for status in (301, 307, 401, 402, 403, 429, 500, 503):
            transport = Responses(HTTPResponse(status, body=KEY.encode()))
            client = MeshAPIClient(MeshSettings(KEY, policy=smoke_policy(15)), transport)
            with self.subTest(status=status), self.assertRaises(MeshError) as caught:
                await client.complete(MeshRequest("offline-model", "Reply exactly OK.", 64))
            self.assertEqual(len(transport.calls), 1)
            self.assertNotIn(KEY, str(caught.exception))

    async def test_smoke_success_exhausts_each_clients_lifetime_budget(self):
        mesh_transport = Responses(mesh_response())
        mesh = MeshAPIClient(MeshSettings(KEY, max_output_tokens=64, policy=smoke_policy(15)), mesh_transport)
        search_transport = Responses(search_response())
        search = BraveSearchDiscovery(BraveSearchSettings(KEY, policy=smoke_policy(10)), search_transport)
        request = MeshRequest("offline-model", "Reply exactly OK.", 64)
        self.assertEqual((await mesh.complete(request)).text, "OK")
        self.assertEqual(await search.discover(SearchRequest("offline fixture", 1)), ())
        # A second call is rejected before reaching either injected transport.
        with self.assertRaises(RequestLimitExceeded):
            await mesh.complete(request)
        with self.assertRaises(DiscoveryError):
            await search.discover(SearchRequest("offline fixture", 1))
        self.assertEqual(len(mesh_transport.calls), 1)
        self.assertEqual(len(search_transport.calls), 1)
        sent = mesh_transport.calls[0]
        self.assertEqual(sent.timeout_seconds, 15)
        self.assertEqual(json.loads(sent.body)["max_tokens"], 64)

    async def test_mesh_payment_required_is_not_retried_even_with_default_policy(self):
        transport = Responses(HTTPResponse(402, body=KEY.encode()))
        client = MeshAPIClient(MeshSettings(KEY), transport)
        with self.assertRaises(MeshError):
            await client.complete(MeshRequest("offline-model", "offline prompt"))
        self.assertEqual(len(transport.calls), 1)
