"""Real adapter/parser code with mocked HTTP and authored local document fixtures."""

import asyncio
import hashlib
import io
import json
import os
import subprocess
import traceback
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, TextStringObject

from deepdive_ai.agents.common import AgentError
from deepdive_ai.agents.researcher import RetrievalResearcherAgent
from deepdive_ai.discovery import DiscoveredSource
from deepdive_ai.extraction import BoundedContentExtractor, ExtractionError, ExtractionSettings
from deepdive_ai.http_transport import HTTPResponse, RequestPolicy, UnsafeURL, _PinnedConnection
from deepdive_ai.models import (
    Claim, ResearchBundle, ResearchQuestion, ResearchTask, Source, SourceProvenance,
    VerificationResult, VerificationStatus,
)
from deepdive_ai.reporting import render_verified_summary
from deepdive_ai.retrieval import HTTPWebRetriever, RetrievalSettings, RetrievedDocument, SearchRequest
from deepdive_ai.search import BraveSearchDiscovery, BraveSearchSettings, DiscoveryError


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
URL = "https://fixture.invalid/article"
KEY = "offline-search-placeholder"
POLICY = RequestPolicy(backoff_seconds=0, max_retry_delay_seconds=0, max_retries=1)


def document(body: bytes, media_type="text/html", charset=None):
    source = Source("fixture-source", URL, "Offline content fixture", SourceProvenance(
        NOW, hashlib.sha256(body).hexdigest(), "offline fixture only"))
    return RetrievedDocument(source, body, URL, (URL,), media_type, charset)


def pdf_variant(*, pages=1, encrypted=False, active=False, blank=False):
    writer = PdfWriter()
    if blank:
        writer.add_blank_page(width=612, height=792)
    else:
        reader = PdfReader(FIXTURES / "article.pdf", strict=True)
        for _ in range(pages):
            writer.add_page(reader.pages[0])
    if encrypted:
        writer.encrypt("offline-fixture-password")
    if active:
        writer._root_object[NameObject("/OpenAction")] = DictionaryObject({
            NameObject("/S"): NameObject("/JavaScript"), NameObject("/JS"): TextStringObject("offline fixture only")})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class Responses:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def send(self, request):
        self.calls.append(request)
        if not self.responses:
            raise AssertionError("Unexpected HTTP exchange")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def search_response(rows=None, **changes):
    data = {"type": "search", "web": {"results": rows if rows is not None else [{"url": URL}]}}
    data.update(changes)
    return HTTPResponse(200, (("Content-Type", "application/json"),), json.dumps(data).encode())


class OfflineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dns = patch("socket.getaddrinfo", side_effect=AssertionError("Live DNS is forbidden"))
        self.connect = patch.object(_PinnedConnection, "connect", side_effect=AssertionError("Live connection is forbidden"))
        self.dns.start()
        self.connect.start()
        self.addCleanup(self.dns.stop)
        self.addCleanup(self.connect.stop)


class DiscoveryTests(OfflineTests):
    def provider(self, transport, **policy):
        return BraveSearchDiscovery(BraveSearchSettings(KEY, policy=replace(POLICY, **policy)), transport)

    async def test_brave_request_auth_query_and_success(self):
        transport = Responses(search_response())
        result = await self.provider(transport).discover(SearchRequest("offline fixture evidence", 3))
        self.assertEqual(result, (DiscoveredSource(URL),))
        request = transport.calls[0]
        self.assertTrue(request.url.startswith("https://api.search.brave.com/res/v1/web/search?"))
        self.assertIn("q=offline+fixture+evidence", request.url)
        self.assertIn("count=3", request.url)
        self.assertEqual(dict(request.headers)["X-Subscription-Token"], KEY)
        self.assertNotIn(KEY, repr(request))

    async def test_normalized_duplicates_and_empty_results(self):
        transport = Responses(search_response([{"url": "https://FIXTURE.invalid/article#one"}, {"url": URL + "#two"}]))
        self.assertEqual(await self.provider(transport).discover(SearchRequest("fixture", 2)), (DiscoveredSource(URL),))
        for response in (search_response([]), search_response(web=None)):
            if json.loads(response.body)["web"] is None:
                with self.assertRaises(DiscoveryError):
                    await self.provider(Responses(response)).discover(SearchRequest("fixture"))
            else:
                self.assertEqual(await self.provider(Responses(response)).discover(SearchRequest("fixture")), ())
        response = HTTPResponse(200, (("content-type", "application/json"),), b'{"type":"search"}')
        self.assertEqual(await self.provider(Responses(response)).discover(SearchRequest("fixture")), ())

    async def test_malformed_search_results_and_unsafe_urls_rejected(self):
        responses = [search_response(web=[]), search_response([{}]), search_response([{"url": 7}]),
                     search_response([{"url": "https://127.0.0.1/private"}]), search_response([{"url": "file:///private"}]),
                     search_response(type="error"), search_response(error={"message": KEY}),
                     HTTPResponse(200, (("content-type", "application/json"),), b'{"type":"search","type":"error"}'),
                     HTTPResponse(200, (("content-type", "application/json"),), b"\xff"),
                     HTTPResponse(200, (("content-type", "text/html"),), b"private provider response")]
        for response in responses:
            with self.subTest(response=response), self.assertRaises(DiscoveryError) as caught:
                await self.provider(Responses(response)).discover(SearchRequest("fixture"))
            self.assertNotIn(KEY, "".join(traceback.format_exception(caught.exception)))

    async def test_provider_returning_excess_results_is_rejected(self):
        with self.assertRaises(DiscoveryError):
            await self.provider(Responses(search_response([{"url": URL}, {"url": URL + "2"}]))).discover(SearchRequest("fixture", 1))

    async def test_errors_redirects_and_retry_budget(self):
        for status in (301, 302, 307, 400, 401, 403):
            transport = Responses(HTTPResponse(status, (("Location", "https://other.invalid/"),), KEY.encode()))
            with self.subTest(status=status), self.assertRaises(DiscoveryError):
                await self.provider(transport).discover(SearchRequest("fixture"))
            self.assertEqual(len(transport.calls), 1)
        transport = Responses(HTTPResponse(429), search_response())
        await self.provider(transport).discover(SearchRequest("fixture"))
        self.assertEqual(len(transport.calls), 2)
        transport = Responses(HTTPResponse(503), HTTPResponse(503))
        with self.assertRaises(DiscoveryError):
            await self.provider(transport).discover(SearchRequest("fixture"))
        self.assertEqual(len(transport.calls), 2)

    async def test_query_response_and_lifetime_limits(self):
        for query in ("x" * 401, "word " * 51, "bad\x00query", "bad\ud800query"):
            transport = Responses()
            with self.subTest(query_length=len(query)), self.assertRaises(DiscoveryError):
                await self.provider(transport).discover(SearchRequest(query))
            self.assertEqual(transport.calls, [])
        with self.assertRaises(DiscoveryError):
            await self.provider(Responses(search_response()), max_response_bytes=10).discover(SearchRequest("fixture"))
        provider = self.provider(Responses(search_response()), max_requests=1)
        await provider.discover(SearchRequest("fixture"))
        with self.assertRaises(DiscoveryError):
            await provider.discover(SearchRequest("fixture"))

    async def test_settings_env_redaction_and_no_startup_calls(self):
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": KEY}):
            settings = BraveSearchSettings.from_environment()
        self.assertEqual(settings.api_key, KEY)
        self.assertNotIn(KEY, repr(settings))
        transport = Responses()
        BraveSearchDiscovery(settings, transport)
        self.assertEqual(transport.calls, [])
        for options in ({"api_key": ""}, {"api_key": "x\r\nInjected: value"},
                        {"api_key": KEY, "endpoint": "https://other.invalid/search"}):
            with self.subTest(options=list(options)), self.assertRaises(ValueError):
                BraveSearchSettings(**options)


class HTMLTests(OfflineTests):
    async def test_readable_html_removes_boilerplate_and_keeps_meaningful_text(self):
        body = (FIXTURES / "article.html").read_bytes()
        artifacts = await BoundedContentExtractor().extract(document(body))
        result = artifacts[0]
        self.assertIn("Fixture A records 12 tokens & keeps its original source.", result.text)
        self.assertIn("Café", result.text)
        for removed in ("Account", "cookies", "Advertisement", "fetch(", "fabricated", "Newsletter", "Site branding"):
            self.assertNotIn(removed, result.text)
        self.assertEqual(result.original_content_sha256, hashlib.sha256(body).hexdigest())
        self.assertIn("normalize-block-whitespace", result.transformations)

    async def test_encodings_bom_entities_and_declaration_conflicts(self):
        body = '<html><meta charset="windows-1252"><body><main><p>Café &amp; tea</p></main></body></html>'.encode("cp1252")
        artifacts = await BoundedContentExtractor().extract(document(body))
        self.assertEqual(artifacts[0].text, "Café & tea")
        self.assertIn("decode:cp1252:strict", artifacts[0].transformations)
        utf16 = '<html><body><main><p>Unicode text</p></main></body></html>'.encode("utf-16")
        self.assertEqual((await BoundedContentExtractor().extract(document(utf16)))[0].text, "Unicode text")
        for body, charset in ((b'<meta charset="cp1252"><p>fixture</p>', "utf-8"),
                              (b'<p>bad\xff</p>', None), (b'<p>ok</p>', "utf-7"),
                              (b'<p>bad\x00</p>', None)):
            with self.subTest(charset=charset), self.assertRaises(ExtractionError):
                await BoundedContentExtractor().extract(document(body, charset=charset))

    async def test_malformed_active_declarations_and_empty_html_rejected(self):
        for body in (b"<main><div>unclosed", b"<nav>nothing else", b"<div></span>",
                     b'<!DOCTYPE html SYSTEM "file:///private"><p>text</p>',
                     b'<!ENTITY external SYSTEM "file:///private"><p>text</p>',
                     b"<script>only executable content</script>", b"<html"):
            with self.subTest(body=body), self.assertRaises(ExtractionError):
                await BoundedContentExtractor().extract(document(body))

    async def test_html_input_output_depth_and_node_limits(self):
        for settings, body in (
            (ExtractionSettings(max_input_bytes=5), b"<p>fixture</p>"),
            (ExtractionSettings(max_output_chars=3), b"<p>fixture</p>"),
            (ExtractionSettings(max_depth=2), b"<div><div><p>fixture</p></div></div>"),
            (ExtractionSettings(max_nodes=2), b"<p>fixture</p>"),
        ):
            with self.subTest(settings=settings), self.assertRaises(ExtractionError):
                await BoundedContentExtractor(settings).extract(document(body))

    async def test_transformed_spans_have_explicit_provenance_and_no_byte_claim(self):
        original = document(b"<main><p>Caf\xc3\xa9 &amp; tea</p></main>")
        artifact = (await BoundedContentExtractor().extract(original))[0]
        item = artifact.evidence("e", original.source.id, "t", 0, len(artifact.text))
        self.assertEqual(item.excerpt, "Café & tea")
        self.assertNotIn("bytes", item.locator)
        self.assertIn("extracted text chars", item.locator)
        self.assertEqual(item.extraction.text_end, len(item.excerpt))
        self.assertEqual(item.extraction.original_content_sha256, original.source.provenance.content_sha256)
        self.assertEqual(item.extraction.extracted_text_sha256, hashlib.sha256(artifact.text.encode()).hexdigest())
        ResearchBundle((original.source,), (item,))
        with self.assertRaises(ValueError):
            replace(item, excerpt="X" * len(item.excerpt))
        with self.assertRaises(ValueError):
            replace(item, locator="original bytes 0:10")
        wrong_source = replace(original.source, provenance=replace(original.source.provenance, content_sha256="0" * 64))
        with self.assertRaises(ValueError):
            ResearchBundle((wrong_source,), (item,))


class PDFTests(OfflineTests):
    async def test_local_pdf_under_real_process_limits_has_page_text_and_provenance(self):
        original = document((FIXTURES / "article.pdf").read_bytes(), "application/pdf")
        artifact = (await BoundedContentExtractor().extract(original))[0]
        self.assertIn("Offline PDF fixture records 12 tokens.", artifact.text)
        self.assertEqual(artifact.page_number, 1)
        self.assertEqual(artifact.method, "pypdf-6.20.1/plain-v1")
        item = artifact.evidence("e", original.source.id, "t", 0, len(artifact.text))
        self.assertIn("PDF page 1", item.locator)
        self.assertNotIn("bytes", item.locator)
        self.assertEqual(item.extraction.original_content_sha256, hashlib.sha256(original.content).hexdigest())

    async def test_encrypted_active_malformed_and_no_text_pdf_rejected(self):
        for content in (pdf_variant(encrypted=True), pdf_variant(active=True), pdf_variant(blank=True),
                        b"%PDF-1.3\nmalformed\n%%EOF", b"not a PDF"):
            with self.subTest(size=len(content)), self.assertRaises(ExtractionError):
                await BoundedContentExtractor().extract(document(content, "application/pdf"))

    async def test_pdf_page_stream_text_and_size_limits(self):
        for settings, content in (
            (ExtractionSettings(max_pages=1), pdf_variant(pages=2)),
            (ExtractionSettings(max_stream_bytes=10), pdf_variant()),
            (ExtractionSettings(max_page_chars=5), pdf_variant()),
            (ExtractionSettings(max_output_chars=5), pdf_variant()),
            (ExtractionSettings(max_input_bytes=20), pdf_variant()),
            (ExtractionSettings(max_nodes=2), pdf_variant()),
        ):
            with self.subTest(settings=settings), self.assertRaises(ExtractionError):
                await BoundedContentExtractor(settings).extract(document(content, "application/pdf"))

    async def test_pdf_multiple_pages_preserve_page_numbers(self):
        artifacts = await BoundedContentExtractor().extract(document(pdf_variant(pages=2), "application/pdf"))
        self.assertEqual([artifact.page_number for artifact in artifacts], [1, 2])

    async def test_compressed_pdf_stream_expansion_is_bounded(self):
        writer = PdfWriter()
        reader = PdfReader(FIXTURES / "article.pdf")
        page = writer.add_page(reader.pages[0])
        stream = DecodedStreamObject()
        stream.set_data(b" " * 5000 + b"BT /F1 12 Tf (offline fixture) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
        buffer = io.BytesIO()
        writer.write(buffer)
        with self.assertRaises(ExtractionError):
            await BoundedContentExtractor(ExtractionSettings(max_stream_bytes=64)).extract(
                document(buffer.getvalue(), "application/pdf"))

    async def test_pdf_timeout_cleanup_and_worker_env_excludes_credentials(self):
        process = Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired("pdf worker", 1)
        process.poll.return_value = None
        process.wait.return_value = 0
        process._handle = 123
        with patch.dict(os.environ, {"MESH_API_KEY": "offline-mesh-placeholder", "BRAVE_SEARCH_API_KEY": KEY}), \
             patch("deepdive_ai.extraction.subprocess.Popen", return_value=process) as popen, \
             patch("deepdive_ai.extraction.WindowsJob") as job:
            with self.assertRaises(ExtractionError):
                await BoundedContentExtractor().extract(document(pdf_variant(), "application/pdf"))
        self.assertNotIn("MESH_API_KEY", popen.call_args.kwargs["env"])
        self.assertNotIn("BRAVE_SEARCH_API_KEY", popen.call_args.kwargs["env"])
        self.assertIn("-I", popen.call_args.args[0])
        process.kill.assert_called_once()
        if os.name == "nt":
            job.return_value.close.assert_called_once()

    async def test_pdf_resource_limit_failure_is_closed(self):
        if os.name != "nt":
            self.skipTest("Windows job assignment path")
        process = Mock()
        process.poll.return_value = None
        process._handle = 123
        with patch("deepdive_ai.extraction.subprocess.Popen", return_value=process), \
             patch("deepdive_ai.extraction.WindowsJob", side_effect=RuntimeError("private failure")):
            with self.assertRaises(ExtractionError) as caught:
                await BoundedContentExtractor().extract(document(pdf_variant(), "application/pdf"))
        process.communicate.assert_not_called()
        process.kill.assert_called_once()
        self.assertNotIn("private failure", str(caught.exception))


class PipelineTests(OfflineTests):
    async def test_ambiguous_http_charset_is_rejected_before_extraction(self):
        fetch = Responses(HTTPResponse(200, (("content-type", "text/html; charset=utf-8; charset=cp1252"),), b"<p>fixture</p>"))
        retriever = HTTPWebRetriever(RetrievalSettings(policy=POLICY), fetch)
        with self.assertRaisesRegex(RuntimeError, "charset declaration is ambiguous"):
            await retriever.fetch(URL)

    async def test_discovery_html_research_and_citation_metadata(self):
        search_transport = Responses(search_response())
        provider = BraveSearchDiscovery(BraveSearchSettings(KEY, policy=POLICY), search_transport)
        body = (FIXTURES / "article.html").read_bytes()
        fetch = Responses(HTTPResponse(200, (("content-type", "text/html; charset=utf-8"),), body))
        retriever = HTTPWebRetriever(RetrievalSettings(policy=POLICY), fetch, lambda: NOW)
        researcher = RetrievalResearcherAgent(provider, retriever)
        question = ResearchQuestion("q", "Read the offline fixture")
        task = ResearchTask("t", "q", "Find fixture token counts")
        bundle = await researcher.research(question, (task,), ResearchBundle(), ())
        self.assertEqual(len(bundle.sources), 1)
        self.assertEqual(len(bundle.evidence), 1)
        evidence = bundle.evidence[0]
        self.assertIn("Fixture A records 12 tokens", evidence.excerpt)
        self.assertNotIn("fetch(", evidence.excerpt)
        self.assertIsNotNone(evidence.extraction)
        self.assertEqual(bundle.sources[0].provenance.content_sha256, hashlib.sha256(body).hexdigest())
        claim = Claim("c", "Offline fixture token count", (evidence.id,))
        result = VerificationResult("c", VerificationStatus.SUPPORTED, "Controlled fixture assessment", (evidence.id,))
        summary = json.loads(render_verified_summary(bundle, (claim,), (result,)))
        citation = summary["accepted_claims"][0]["citations"][0]
        self.assertEqual(citation["extraction"]["original_content_sha256"], bundle.sources[0].provenance.content_sha256)
        self.assertNotIn("bytes", citation["locator"])

    async def test_discovery_pdf_research_uses_pdf_page_provenance(self):
        provider = BraveSearchDiscovery(BraveSearchSettings(KEY, policy=POLICY), Responses(search_response()))
        fetch = Responses(HTTPResponse(200, (("content-type", "application/pdf"),), pdf_variant()))
        researcher = RetrievalResearcherAgent(provider, HTTPWebRetriever(RetrievalSettings(policy=POLICY), fetch, lambda: NOW))
        bundle = await researcher.research(ResearchQuestion("q", "Read fixture"), (ResearchTask("t", "q", "Read fixture"),), ResearchBundle(), ())
        self.assertEqual(bundle.evidence[0].extraction.page_number, 1)

    async def test_unsafe_source_redirect_rejected_without_followup(self):
        provider = BraveSearchDiscovery(BraveSearchSettings(KEY, policy=POLICY), Responses(search_response()))
        fetch = Responses(HTTPResponse(302, (("location", "http://169.254.169.254/private"),)))
        researcher = RetrievalResearcherAgent(provider, HTTPWebRetriever(RetrievalSettings(policy=POLICY), fetch))
        with self.assertRaises(AgentError):
            await researcher.research(ResearchQuestion("q", "Read fixture"), (ResearchTask("t", "q", "Read fixture"),), ResearchBundle(), ())
        self.assertEqual(len(fetch.calls), 1)

    async def test_duplicate_sources_via_redirect_alias_keep_single_provenance_record(self):
        provider = BraveSearchDiscovery(BraveSearchSettings(KEY, policy=POLICY), Responses(search_response([
            {"url": URL + "/alias1"}, {"url": URL + "/alias2"}])))
        body = (FIXTURES / "article.html").read_bytes()
        fetch = Responses(HTTPResponse(302, (("location", URL),)), HTTPResponse(200, (("content-type", "text/html"),), body),
                          HTTPResponse(302, (("location", URL),)), HTTPResponse(200, (("content-type", "text/html"),), body))
        researcher = RetrievalResearcherAgent(provider, HTTPWebRetriever(RetrievalSettings(policy=POLICY), fetch, lambda: NOW))
        bundle = await researcher.research(ResearchQuestion("q", "Read fixture"), (ResearchTask("t", "q", "Read fixture"),), ResearchBundle(), ())
        self.assertEqual(len(bundle.sources), 1)
        self.assertEqual(len(bundle.evidence), 1)


class ConfigurationTests(unittest.TestCase):
    def test_invalid_extraction_settings(self):
        for settings in ({"max_pages": 0}, {"max_pages": 101}, {"max_input_bytes": 8_000_001},
                         {"memory_mb": 1}, {"cpu_seconds": 31}, {"timeout_seconds": float("inf")},
                         {"max_depth": 257}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                ExtractionSettings(**settings)


if __name__ == "__main__":
    unittest.main()
