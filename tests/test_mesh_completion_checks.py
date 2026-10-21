"""Fixed Mesh completion-check diagnostics. Acceptance is unchanged; no network or provider access."""

import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deepdive_ai.http_transport import FailureCategory, HTTPResponse, RequestPolicy, _PinnedConnection
from deepdive_ai.live import run_live
from deepdive_ai.mesh import (
    MeshAPIClient, MeshCompletionCheck, MeshError, MeshIdentityError, MeshRequest, MeshSettings,
)
from test_agents import URL, assessment, draft, plan
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport, SourceTransport

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("summarize_live_run", ROOT / "scripts" / "summarize_live_run.py")
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)

KEY = "offline-mesh-completion-key"
MODEL = "configured-model"
PRIVATE = "PRIVATE-MODEL-OUTPUT-or-provider-value"
HEADERS = (("Content-Type", "application/json"), ("X-Mesh-Routing-Fallback", "false"))


def body(choice=None, **data):
    choice = {"finish_reason": "stop", "message": {"role": "assistant", "content": "offline text"}} \
        if choice is None else choice
    document = {"id": "offline-id", "model": MODEL, "choices": [choice]}
    document.update(data)
    return json.dumps(document).encode()


def message(**changes):
    result = {"role": "assistant", "content": "offline text"}
    result.update(changes)
    return result


class Transport:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def send(self, request):
        self.calls += 1
        return self.response


async def complete(response, retries=0):
    transport = Transport(response)
    client = MeshAPIClient(MeshSettings(KEY, policy=RequestPolicy(max_retries=retries, backoff_seconds=0)), transport)
    return await client.complete(MeshRequest(MODEL, "offline prompt", 64)), transport


class OfflineCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("real DNS forbidden")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("real HTTP forbidden"))):
            guard.start()
            self.addCleanup(guard.stop)


class CompletionCheckTests(OfflineCase):
    async def assert_check(self, response, check):
        with self.assertRaises(MeshError) as caught:
            await complete(response)
        error = caught.exception
        self.assertNotIsInstance(error, MeshIdentityError)
        self.assertIs(error.completion_check, check)
        self.assertEqual(error.category, FailureCategory.PROTOCOL)
        self.assertEqual(error.http_status, 200)
        self.assertNotIn(PRIVATE, str(error))
        self.assertIsNone(error.__cause__)

    async def test_every_completion_check_has_a_fixed_code(self):
        C = MeshCompletionCheck
        cases = [
            (HTTPResponse(200, (("Content-Type", "text/plain"),), body()), C.UNSUPPORTED_CONTENT_TYPE),
            (HTTPResponse(200, HEADERS, b"not json " + PRIVATE.encode()), C.ENVELOPE_INVALID),
            (HTTPResponse(200, HEADERS, b"[]"), C.ENVELOPE_INVALID),
            (HTTPResponse(200, HEADERS, b'{"id": "a", "id": "b"}'), C.ENVELOPE_INVALID),
            (HTTPResponse(200, HEADERS, b'{"id": NaN}'), C.ENVELOPE_INVALID),
            (HTTPResponse(200, HEADERS, b"\xff\xfe"), C.ENVELOPE_INVALID),
            (HTTPResponse(200, HEADERS, json.dumps({"model": MODEL, "choices": []}).encode()), C.ENVELOPE_INVALID),
            (HTTPResponse(200, HEADERS, body(id="   ")), C.ENVELOPE_INVALID),
            (HTTPResponse(200, HEADERS, body(id=7)), C.ENVELOPE_INVALID),
            (HTTPResponse(200, HEADERS, body(choices=[])), C.CHOICES_INVALID),
            (HTTPResponse(200, HEADERS, body(choices=None)), C.CHOICES_INVALID),
            (HTTPResponse(200, HEADERS, body(choices=[PRIVATE])), C.CHOICES_INVALID),
            (HTTPResponse(200, HEADERS, body(choices=[{"finish_reason": "stop", "message": message()}] * 2)),
             C.CHOICES_INVALID),
            (HTTPResponse(200, HEADERS, body({"finish_reason": "length", "message": message()})), C.FINISH_REASON_LENGTH),
            (HTTPResponse(200, HEADERS, body({"finish_reason": "content_filter", "message": message()})),
             C.FINISH_REASON_CONTENT_FILTER),
            (HTTPResponse(200, HEADERS, body({"finish_reason": "tool_calls", "message": message()})),
             C.FINISH_REASON_TOOL_CALLS),
            (HTTPResponse(200, HEADERS, body({"finish_reason": "function_call", "message": message()})),
             C.FINISH_REASON_TOOL_CALLS),
            (HTTPResponse(200, HEADERS, body({"finish_reason": PRIVATE, "message": message()})), C.FINISH_REASON_OTHER),
            (HTTPResponse(200, HEADERS, body({"finish_reason": 5, "message": message()})), C.FINISH_REASON_OTHER),
            (HTTPResponse(200, HEADERS, body({"finish_reason": "STOP", "message": message()})), C.FINISH_REASON_OTHER),
            (HTTPResponse(200, HEADERS, body({"message": message()})), C.FINISH_REASON_MISSING),
            (HTTPResponse(200, HEADERS, body({"finish_reason": None, "message": message()})), C.FINISH_REASON_MISSING),
            (HTTPResponse(200, HEADERS, body({"finish_reason": "stop"})), C.MESSAGE_INVALID),
            (HTTPResponse(200, HEADERS, body({"finish_reason": "stop", "message": PRIVATE})), C.MESSAGE_INVALID),
            (HTTPResponse(200, HEADERS, body({"finish_reason": "stop", "message": message(role="user")})),
             C.MESSAGE_INVALID),
            (HTTPResponse(200, HEADERS, body({"finish_reason": "stop",
                                              "message": message(tool_calls=[{"id": PRIVATE}])})), C.TOOL_CALLS_PRESENT),
            (HTTPResponse(200, HEADERS, body({"finish_reason": "stop", "message": message(content=None)})),
             C.CONTENT_NOT_TEXT),
            (HTTPResponse(200, HEADERS, body({"finish_reason": "stop", "message": message(content=[PRIVATE])})),
             C.CONTENT_NOT_TEXT),
            (HTTPResponse(200, HEADERS, body({"finish_reason": "stop", "message": message(content="")})),
             C.CONTENT_EMPTY),
            (HTTPResponse(200, HEADERS, body({"finish_reason": "stop", "message": message(content=" \n ")})),
             C.CONTENT_EMPTY),
        ]
        self.assertEqual({check for _, check in cases}, set(MeshCompletionCheck))
        for response, check in cases:
            with self.subTest(check=check, body=response.body[:40]):
                await self.assert_check(response, check)

    async def test_non_200_status_is_preserved_without_a_completion_check(self):
        for status in (400, 401, 402, 404, 302, 204):
            with self.subTest(status=status):
                with self.assertRaises(MeshError) as caught:
                    await complete(HTTPResponse(status, HEADERS, PRIVATE.encode()))
                self.assertEqual(caught.exception.http_status, status)
                self.assertIsNone(caught.exception.completion_check)
                self.assertEqual(caught.exception.category, FailureCategory.PROTOCOL)
                self.assertNotIn(PRIVATE, str(caught.exception))

    async def test_acceptance_and_other_rejections_are_unchanged(self):
        response, transport = await complete(HTTPResponse(200, HEADERS, body()))
        self.assertEqual((response.text, response.request_id, transport.calls), ("offline text", "offline-id", 1))
        # tool_calls that are present but empty were, and remain, acceptable.
        response, _ = await complete(HTTPResponse(200, HEADERS, body({"finish_reason": "stop",
                                                                      "message": message(tool_calls=[])})))
        self.assertEqual(response.text, "offline text")
        with self.assertRaises(MeshIdentityError) as caught:
            await complete(HTTPResponse(200, HEADERS, body(model="other-model")))
        self.assertIsNone(caught.exception.completion_check)
        self.assertEqual(caught.exception.category, FailureCategory.VALIDATION)
        with self.assertRaises(MeshError) as caught:
            await complete(HTTPResponse(200, (("Content-Type", "application/json"), ("X-Cache", "HIT")), body()))
        self.assertEqual(caught.exception.category, FailureCategory.VALIDATION)
        self.assertIsNone(caught.exception.completion_check)

    def test_invalid_check_values_are_dropped(self):
        self.assertIsNone(MeshError("x", completion_check="finish_reason_length").completion_check)
        self.assertIs(MeshError("x", completion_check=MeshCompletionCheck.CONTENT_EMPTY).completion_check,
                      MeshCompletionCheck.CONTENT_EMPTY)


class LiveCompletionCheckTests(OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def run_drafter(self, mutate):
        class Mesh(MeshTransport):
            async def send(self, request):
                response = await super().send(request)
                if len(self.calls) != 2:
                    return response
                return mutate(response)

        mesh = Mesh(plan(), draft, assessment)
        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result = await run_live(replace(OPTIONS), execute=True, environment=ENV, output_root=self.root,
                                    mesh_transport=mesh, brave_transport=SearchTransport((URL,)),
                                    source_transport=SourceTransport())
        folder = self.root / result["run_id"]
        self.assertEqual(json.loads((folder / "outcome.json").read_text()), result)
        persisted = (folder / "outcome.json").read_text() + (folder / "audit.jsonl").read_text()
        rendered = summary.build(None, result, self.root)
        for private in (PRIVATE, *ENV.values(), "private-response-id", "Bearer", "Authorization",
                        "Mesh returned", "Traceback", "offline text"):
            self.assertNotIn(private, persisted)
            self.assertNotIn(private, rendered)
        return result, rendered

    async def test_truncated_drafter_completion_is_recorded_and_summarized(self):
        def truncated(response):
            data = json.loads(response.body)
            data["choices"][0]["finish_reason"] = "length"
            data["choices"][0]["message"]["content"] = PRIVATE
            data["usage"] = {"prompt_tokens": 10, "completion_tokens": 1024, "total_tokens": 1034}
            return HTTPResponse(200, response.headers, json.dumps(data).encode())

        result, rendered = await self.run_drafter(truncated)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_category"], "http_protocol_failure")
        self.assertEqual(result["failure_details"], {"category": "http_protocol_failure", "stage": None,
                                                     "http_status": 200,
                                                     "mesh_completion_check": "finish_reason_length"})
        self.assertNotIn("mesh", result["diagnostics"])
        self.assertEqual(result["attempts"]["mesh"], 2)
        self.assertIn("| Mesh completion check | finish_reason_length |", rendered)
        self.assertIn("| HTTP status | 200 |", rendered)

    async def test_provider_error_status_is_recorded(self):
        result, rendered = await self.run_drafter(lambda response: HTTPResponse(400, response.headers, PRIVATE.encode()))
        self.assertEqual(result["failure_details"], {"category": "http_protocol_failure", "stage": None,
                                                     "http_status": 400})
        self.assertEqual(result["diagnostics"]["mesh"]["http_status"], 400)
        self.assertIn("| HTTP status | 400 |", rendered)
        self.assertNotIn("Mesh completion check", rendered)

    async def test_every_check_reaches_the_outcome(self):
        def with_finish(reason):
            def mutate(response):
                data = json.loads(response.body)
                data["choices"][0]["finish_reason"] = reason
                return HTTPResponse(200, response.headers, json.dumps(data).encode())
            return mutate

        for reason, check in (("content_filter", "finish_reason_content_filter"), (PRIVATE, "finish_reason_other")):
            with self.subTest(check=check):
                result, rendered = await self.run_drafter(with_finish(reason))
                self.assertEqual(result["failure_details"]["mesh_completion_check"], check)
                self.assertIn(f"| Mesh completion check | {check} |", rendered)


class SummaryCompletionCheckTests(unittest.TestCase):
    def render(self, details):
        result = {"mode": "live", "status": "failed", "run_id": None, "failure_details": details,
                  "attempts": {"mesh": 2}}
        return summary.build(None, result, Path("missing-reports"))

    def test_allowlist_matches_application_enum(self):
        self.assertEqual(summary.MESH_COMPLETION_CHECKS, {check.value for check in MeshCompletionCheck})

    def test_values_are_shown_only_after_allowlist_validation(self):
        base = {"category": "http_protocol_failure", "stage": None, "http_status": 200}
        for check in MeshCompletionCheck:
            with self.subTest(check=check):
                self.assertIn(f"| Mesh completion check | {check.value} |",
                              self.render({**base, "mesh_completion_check": check.value}))
        for hostile in (PRIVATE, "finish_reason_length\n| injected |", "<b>x</b>", "FINISH_REASON_LENGTH",
                        ["finish_reason_length"], {"x": 1}, 5, True, "x" * 500):
            with self.subTest(value=str(hostile)[:30]):
                text = self.render({**base, "mesh_completion_check": hostile})
                self.assertIn("| Mesh completion check | invalid |", text)
                self.assertNotIn("injected", text)
                self.assertNotIn(PRIVATE, text)
        self.assertIn("| Mesh completion check | none |", self.render({**base, "mesh_completion_check": None}))
        self.assertNotIn("Mesh completion check", self.render(base))


if __name__ == "__main__":
    unittest.main()
