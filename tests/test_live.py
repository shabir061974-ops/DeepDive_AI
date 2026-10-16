"""Real live composition with explicitly injected HTTP fixtures; no external access."""

import asyncio
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from deepdive_ai.audit import AuditEvent, AuditKind
from deepdive_ai.connectivity import TestOptions
from deepdive_ai.http_transport import FailureCategory, HTTPResponse, TransportError, TransportStage, _PinnedConnection
from deepdive_ai.live import LiveOptions, main, run_live
from deepdive_ai.persistence import JsonlAuditSink, write_record
from test_agents import BODY, COUNTER, STATEMENT, URL, OTHER_URL, assessment, draft, plan


ENV = {"MESH_API_KEY": "offline-mesh-fixture", "BRAVE_SEARCH_API_KEY": "offline-brave-fixture"}
APPROVALS = TestOptions(gateway_risk_accepted=True, spending_confirmed=True,
                       mesh_input_rate=Decimal("1"), mesh_output_rate=Decimal("2"), brave_request_rate=Decimal("0.01"))
OPTIONS = LiveOptions("What count does the offline fixture record?", "offline-model", APPROVALS)
HEADERS = (("Content-Type", "application/json"),)


class MeshTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def send(self, request):
        self.calls.append(request)
        payload = json.loads(json.loads(request.body)["messages"][0]["content"])
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        if callable(result):
            result = result(payload)
        text = result if isinstance(result, str) else json.dumps(result)
        return HTTPResponse(200, HEADERS, json.dumps({"id": "private-response-id", "model": OPTIONS.model,
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}).encode())


class SearchTransport:
    def __init__(self, *batches):
        self.batches = list(batches)
        self.calls = []

    async def send(self, request):
        self.calls.append(request)
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return HTTPResponse(200, HEADERS, json.dumps({"type": "search", "web": {
            "results": [{"url": url} for url in batch]}}).encode())


class SourceTransport:
    def __init__(self):
        self.calls = []

    async def send(self, request):
        self.calls.append(request)
        return HTTPResponse(200, (("Content-Type", "text/plain; charset=utf-8"),),
                            BODY if request.url == URL else COUNTER)


class LiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No live sockets"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def run_case(self, mesh=None, search=None, **kwargs):
        mesh = MeshTransport(plan(), draft, assessment) if mesh is None else mesh
        search = SearchTransport((URL,)) if search is None else search
        source = SourceTransport()
        report = await run_live(OPTIONS, execute=True, environment=ENV, output_root=self.root,
                                mesh_transport=mesh, brave_transport=search, source_transport=source, **kwargs)
        text = json.dumps(report)
        for secret in (*ENV.values(), "private-response-id", STATEMENT):
            self.assertNotIn(secret, text)
        return report, mesh, search, source

    def files(self, result):
        folder = self.root / result["run_id"]
        audit = [json.loads(line) for line in (folder / "audit.jsonl").read_text().splitlines()]
        return folder, audit

    async def test_real_adapters_complete_and_persist_provenance_without_demo_import(self):
        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result, mesh, search, source = await self.run_case()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["report_status"], "verified")
        self.assertEqual(result["attempts"], {"mesh": 3, "brave": 1, "source": 1})
        folder, audit = self.files(result)
        record = json.loads((folder / "report.json").read_text())["report"]
        self.assertEqual(record["claims"][0]["text"], STATEMENT)
        self.assertEqual(record["bundle"]["sources"][0]["url"], URL)
        self.assertTrue(record["bundle"]["sources"][0]["provenance"]["content_sha256"])
        self.assertEqual(audit[-1]["kind"], "completed")
        self.assertEqual(json.loads((folder / "outcome.json").read_text()), result)
        for request in mesh.calls:
            self.assertEqual(request.timeout_seconds, 15)
            self.assertEqual(json.loads(request.body)["max_tokens"], 1024)
            self.assertEqual(request.url, "https://api.meshapi.ai/v1/chat/completions")
        self.assertEqual(search.calls[0].timeout_seconds, 10)
        self.assertEqual(source.calls[0].max_response_bytes, 256_000)
        self.assertNotIn(STATEMENT, (folder / "audit.jsonl").read_text())

    async def test_bounded_research_again_with_conflict_preserves_both_sources(self):
        insufficient = lambda payload: assessment(payload, ["insufficient"], "insufficient_evidence")
        conflict = lambda payload: assessment(payload, ["supports", "contradicts"])
        result, _, _, _ = await self.run_case(MeshTransport(plan(), draft, insufficient, draft, conflict),
                                             SearchTransport((URL,), (OTHER_URL,)))
        folder, audit = self.files(result)
        self.assertEqual(sum(event["kind"] == "retry" for event in audit), 1)
        self.assertEqual(result["report_status"], "needs_review")
        record = json.loads((folder / "report.json").read_text())["report"]
        self.assertEqual(len(record["bundle"]["sources"]), 2)
        self.assertEqual(json.loads(record["summary"])["accepted_claims"], [])
        self.assertEqual(record["verification"][0]["status"], "insufficient_evidence")

    async def test_empty_discovery_exhausts_retry_without_fake_evidence(self):
        result, mesh, search, source = await self.run_case(MeshTransport(plan()), SearchTransport((), ()))
        self.assertEqual(len(mesh.calls), 1)
        self.assertEqual(len(search.calls), 2)
        self.assertEqual(source.calls, [])
        self.assertEqual(result["report_status"], "needs_review")

    async def test_two_claims_exhaust_exact_seven_mesh_call_budget_in_two_rounds(self):
        def two_claims(payload):
            value = draft(payload)
            value["claims"].append({"text": "The fixture contains a token count.",
                                    "evidence_ids": value["claims"][0]["evidence_ids"]})
            return value

        def uncertain(payload):
            return assessment(payload, ["insufficient"] * len(payload["data"]["evidence"]), "insufficient_evidence")

        result, mesh, search, source = await self.run_case(
            MeshTransport(plan(), two_claims, uncertain, uncertain, two_claims, uncertain, uncertain),
            SearchTransport((URL,), (OTHER_URL,)))
        self.assertEqual((len(mesh.calls), len(search.calls), len(source.calls)), (7, 2, 2))
        self.assertEqual(result["report_status"], "needs_review")
        _, audit = self.files(result)
        self.assertEqual(sum(event["kind"] == "retry" for event in audit), 1)

    async def test_overall_deadline_cancels_without_fallback_and_persists_outcome(self):
        class HangingMesh(MeshTransport):
            async def send(self, request):
                self.calls.append(request)
                await asyncio.sleep(60)
                raise AssertionError("Must be cancelled")

        original = asyncio.timeout
        with patch("deepdive_ai.live.asyncio.timeout", side_effect=lambda seconds: original(0.01)):
            result, mesh, search, source = await self.run_case(HangingMesh())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_category"], "timeout")
        self.assertEqual(len(mesh.calls), 1)
        self.assertEqual(search.calls + source.calls, [])
        folder, _ = self.files(result)
        self.assertEqual(json.loads((folder / "outcome.json").read_text())["status"], "failed")

    async def test_malformed_agent_json_stops_before_brave_and_keeps_failure_audit(self):
        result, _, search, _ = await self.run_case(MeshTransport("not-json"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_category"], "agent_contract_failure")
        folder, audit = self.files(result)
        self.assertEqual(audit[-1]["kind"], "failed")
        self.assertFalse((folder / "report.json").exists())
        self.assertEqual(search.calls, [])

    async def test_brave_timeout_stage_is_preserved_without_retry_or_substitution(self):
        error = TransportError("private response", category=FailureCategory.TIMEOUT, stage=TransportStage.HEADERS)
        result, mesh, search, source = await self.run_case(search=SearchTransport(error))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["diagnostics"]["brave"]["stage"], "response_headers")
        self.assertEqual((len(mesh.calls), len(search.calls), len(source.calls)), (1, 1, 0))
        self.assertNotIn("private response", json.dumps(result))

    async def test_missing_credentials_or_approvals_block_before_artifacts_or_network(self):
        for options, env in ((OPTIONS, {}), (replace(OPTIONS, approvals=TestOptions()), ENV)):
            with patch("deepdive_ai.live.PublicHTTPTransport") as transport:
                result = await run_live(options, execute=True, environment=env, output_root=self.root)
            self.assertFalse(result["ready"])
            transport.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_preflight_has_no_transport_or_files(self):
        with patch("deepdive_ai.live.PublicHTTPTransport") as transport:
            result = await run_live(OPTIONS, environment=ENV, output_root=self.root)
        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], "not_run")
        transport.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_storage_failure_prevents_all_external_work(self):
        mesh, search, source = MeshTransport(plan()), SearchTransport((URL,)), SourceTransport()
        with patch("deepdive_ai.persistence.os.fsync", side_effect=OSError("private storage error")), self.assertRaises(OSError):
            await run_live(OPTIONS, execute=True, environment=ENV, output_root=self.root,
                           mesh_transport=mesh, brave_transport=search, source_transport=source)
        self.assertEqual(mesh.calls + search.calls + source.calls, [])

    async def test_mesh_model_usage_and_output_violations_stop_before_discovery(self):
        class Incompatible(MeshTransport):
            def __init__(self, changes):
                super().__init__(plan())
                self.changes = changes

            async def send(self, request):
                response = await super().send(request)
                body = json.loads(response.body)
                body.update(self.changes)
                return HTTPResponse(200, HEADERS, json.dumps(body).encode())

        for changes in ({"model": "wrong-model"}, {"usage": None},
                        {"usage": {"prompt_tokens": 10, "completion_tokens": 1025, "total_tokens": 1035}}):
            result, mesh, search, _ = await self.run_case(Incompatible(changes))
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["diagnostics"]["mesh"]["category"], "response_validation")
            self.assertEqual(len(mesh.calls), 1)
            self.assertEqual(search.calls, [])

    async def test_agent_contracts_reject_unknown_citations_and_ungrounded_quotes(self):
        invalid_draft = {"claims": [{"text": STATEMENT, "evidence_ids": ["invented"]}]}

        def invalid_verdict(payload):
            value = assessment(payload)
            value["assessments"][0]["quote"] = "invented quote"
            return value

        for mesh in (MeshTransport(plan(), invalid_draft), MeshTransport(plan(), draft, invalid_verdict)):
            result, *_ = await self.run_case(mesh)
            self.assertEqual(result["failure_category"], "agent_contract_failure")
            folder, audit = self.files(result)
            self.assertFalse((folder / "report.json").exists())
            self.assertEqual(audit[-1]["kind"], "failed")

    async def test_audit_failure_after_planning_prevents_discovery(self):
        original = JsonlAuditSink.emit

        def emit(sink, event):
            if event.kind == AuditKind.PLANNED:
                raise OSError("private storage error")
            return original(sink, event)

        with patch.object(JsonlAuditSink, "emit", emit):
            result, mesh, search, source = await self.run_case()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(mesh.calls), 1)
        self.assertEqual(search.calls + source.calls, [])

    async def test_non_boolean_execution_is_rejected_without_credential_access(self):
        with self.assertRaises(ValueError), patch("deepdive_ai.live.MeshSettings.from_environment") as settings:
            await run_live(OPTIONS, execute="yes")
        settings.assert_not_called()

    async def test_run_directories_do_not_overwrite_and_invalid_config_rejected(self):
        first, *_ = await self.run_case()
        second, *_ = await self.run_case()
        self.assertNotEqual(first["run_id"], second["run_id"])
        for changes in ({"question": ""}, {"question": "x" * 161}, {"model": "bad\nmodel"},
                        {"approvals": replace(APPROVALS, mesh_only=True)}):
            with self.assertRaises(ValueError):
                replace(OPTIONS, **changes)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        Path("artifacts").mkdir(exist_ok=True)

    def test_audit_is_exclusive_flushes_and_rejects_overlarge_events(self):
        with tempfile.TemporaryDirectory(dir="artifacts") as directory:
            path = Path(directory) / "audit.jsonl"
            sink = JsonlAuditSink(path)
            try:
                event = AuditEvent("run", "question", AuditKind.STARTED, 0, datetime.now(timezone.utc))
                sink.emit(event)
                self.assertEqual(json.loads(path.read_text())["kind"], "started")
                with self.assertRaises(FileExistsError):
                    JsonlAuditSink(path)
                with self.assertRaises(ValueError):
                    sink.emit(replace(event, run_id="x" * 9000))
                with self.assertRaises(FileExistsError):
                    write_record(path, {})
            finally:
                sink.close()

    def test_cli_preflight_default_never_constructs_transport(self):
        args = ["live", "--question", OPTIONS.question, "--model", OPTIONS.model,
                "--accept-gateway-uncertainty", "--confirm-spend-controls",
                "--mesh-input-usd-per-million", "1", "--mesh-output-usd-per-million", "2",
                "--brave-usd-per-request", "0.01"]
        with patch.dict("os.environ", ENV, clear=True), patch("sys.argv", args), \
                patch("deepdive_ai.live.PublicHTTPTransport") as transport, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        self.assertEqual(json.loads(output.getvalue())["mode"], "preflight")
        transport.assert_not_called()
