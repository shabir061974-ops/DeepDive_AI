"""Offline tests for the opt-in Mesh SDK diagnostic; no network and no real credentials."""

import importlib.util
import io
import json
import socket
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from deepdive_ai import mesh_sdk
from deepdive_ai.mesh_sdk import MESH_SDK_BASE_URL, MeshSDKError, build_client, run_diagnostic

SYNTHETIC_KEY = "synthetic-sdk-test-key-0000"
ENV = {"MESH_API_KEY": SYNTHETIC_KEY}
SAFE_HEADERS = [("content-type", "application/json"), ("x-mesh-version", "2026-08"),
                ("x-mesh-routing-fallback", "false")]


class FakeHeaders(list):
    def multi_items(self):
        return list(self)


class FakeRaw:
    def __init__(self, headers, model="google/gemini-2.5-flash", status=200):
        self.headers, self.status_code, self._model = FakeHeaders(headers), status, model

    def parse(self):
        return SimpleNamespace(model=self._model, choices=[SimpleNamespace(finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=12, completion_tokens=2))


class FakeClient:
    def __init__(self, raw=None, error=None, **kwargs):
        self.kwargs, self.calls = kwargs, []
        outer = self

        class Create:
            def create(self, **request):
                outer.calls.append(request)
                if error:
                    raise error
                return raw

        self.chat = SimpleNamespace(completions=SimpleNamespace(with_raw_response=Create()))


def factory_for(client_box, **options):
    def factory(**kwargs):
        client_box.append(FakeClient(**options, **kwargs))
        return client_box[-1]
    return factory


class NoNetwork(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket.socket, "connect", side_effect=AssertionError("network used"))
        guard.start()
        self.addCleanup(guard.stop)


class ConfigurationTests(NoNetwork):
    def test_endpoint_and_client_options_are_fixed(self):
        box = []
        build_client(ENV, client_factory=factory_for(box, raw=None))
        kwargs = box[0].kwargs
        self.assertEqual(MESH_SDK_BASE_URL, "https://api.meshapi.ai/v1")
        self.assertEqual(kwargs["base_url"], MESH_SDK_BASE_URL)
        self.assertEqual((kwargs["max_retries"], kwargs["organization"], kwargs["project"]), (0, "", ""))
        self.assertEqual(kwargs["api_key"], SYNTHETIC_KEY)

    def test_missing_or_invalid_key_never_builds_a_client(self):
        for env in ({}, {"MESH_API_KEY": ""}, {"MESH_API_KEY": "bad key"}, {"MESH_API_KEY": "x" * 4097}):
            box = []
            with self.assertRaises(MeshSDKError):
                build_client(env, client_factory=factory_for(box, raw=None))
            self.assertEqual(box, [])
            result = run_diagnostic(execute=True, environment=env, client_factory=factory_for(box, raw=None))
            self.assertFalse(result["ready"])
            self.assertEqual(result["blockers"], ["mesh_api_key_missing_or_invalid"])
            self.assertEqual(box, [])

    def test_key_comes_from_environment_only(self):
        with patch.dict("os.environ", {"MESH_API_KEY": SYNTHETIC_KEY}, clear=True):
            self.assertEqual(mesh_sdk.mesh_api_key(), SYNTHETIC_KEY)
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(MeshSDKError):
                mesh_sdk.mesh_api_key()

    def test_invalid_model_is_rejected(self):
        for model in ("", "x" * 121, "bad model", "café", "a;b"):
            with self.assertRaises(ValueError):
                run_diagnostic(model, environment=ENV)

    @unittest.skipUnless(importlib.util.find_spec("openai"), "optional openai package not installed")
    def test_real_client_ignores_openai_environment_and_sends_nothing(self):
        hostile = {"OPENAI_BASE_URL": "https://example.invalid/v1", "OPENAI_ORG_ID": "org-env",
                   "OPENAI_PROJECT_ID": "proj-env", "OPENAI_API_KEY": "other-env-key"}
        with patch.dict("os.environ", hostile):
            client = build_client(ENV)
        self.assertEqual(str(client.base_url).rstrip("/"), MESH_SDK_BASE_URL)
        self.assertEqual((client.max_retries, client.organization, client.project), (0, "", ""))
        self.assertEqual(client.api_key, SYNTHETIC_KEY)


class DiagnosticTests(NoNetwork):
    def run_with(self, raw=None, error=None, execute=True):
        box = []
        result = run_diagnostic(execute=execute, environment=ENV,
                                client_factory=factory_for(box, raw=raw, error=error))
        return result, box

    def test_default_is_a_preflight_without_any_client(self):
        result, box = self.run_with(execute=False)
        self.assertEqual((result["mode"], result["ready"], result["status"]), ("preflight", True, "not_run"))
        self.assertEqual(box, [])

    def test_accepted_response_uses_shared_validation(self):
        result, box = self.run_with(FakeRaw(SAFE_HEADERS))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["classification"], "normal_non_streaming")
        self.assertEqual(result["checks"], {"model_matches": True, "routing_metadata_valid": True})
        self.assertEqual((result["prompt_tokens"], result["completion_tokens"], result["finish_reason"]),
                         (12, 2, "stop"))
        self.assertEqual(len(box[0].calls), 1)
        request = box[0].calls[0]
        self.assertEqual((request["max_tokens"], request["stream"]), (16, False))

    def test_cache_hit_missing_fallback_and_model_mismatch_are_rejected(self):
        hit = [("content-type", "application/json"), ("x-cache", "HIT")]
        result, _ = self.run_with(FakeRaw(hit))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["classification"], "cached_response")
        self.assertIn("cached_response_unverified", result["routing_rejection_codes"])
        result, _ = self.run_with(FakeRaw([("content-type", "application/json")]))
        self.assertEqual(result["status"], "rejected")
        self.assertIn("fallback_missing", result["routing_rejection_codes"])
        result, _ = self.run_with(FakeRaw(SAFE_HEADERS, model="other/model"))
        self.assertEqual((result["status"], result["checks"]["model_matches"]), ("rejected", False))

    def test_sdk_errors_are_sanitized(self):
        result, _ = self.run_with(error=RuntimeError("secret-" + SYNTHETIC_KEY))
        self.assertEqual((result["status"], result["failure"]), ("failed", "sdk_request_failed"))
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(result))

    def test_output_never_contains_key_prompt_or_completion(self):
        result, _ = self.run_with(FakeRaw(SAFE_HEADERS))
        text = json.dumps(result)
        self.assertNotIn(SYNTHETIC_KEY, text)
        self.assertNotIn(mesh_sdk.PROMPT, text)

    def test_cli_defaults_to_preflight_and_exit_codes(self):
        for env, argv, code, mode in (
                (ENV, [], 0, "preflight"), ({}, [], 2, "preflight")):
            with patch.dict("os.environ", env, clear=True), patch("sys.argv", ["mesh_sdk"] + argv):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(mesh_sdk.main(), code)
            self.assertEqual(json.loads(out.getvalue())["mode"], mode)
            self.assertNotIn(SYNTHETIC_KEY, out.getvalue())


class IsolationTests(unittest.TestCase):
    def test_pipeline_modules_do_not_import_the_sdk_module(self):
        import pathlib
        root = pathlib.Path(mesh_sdk.__file__).parent
        for path in root.rglob("*.py"):
            if path.name == "mesh_sdk.py":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("mesh_sdk", text, path.name)
            self.assertNotIn("import openai", text, path.name)
            self.assertNotIn("from openai", text, path.name)


if __name__ == "__main__":
    unittest.main()
