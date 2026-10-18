"""Support-confirmed non-streaming identity/routing rules; mock HTTP only."""

import json
from deepdive_ai.connectivity import run_connectivity
from deepdive_ai.http_transport import HTTPResponse, FailureCategory
from deepdive_ai.mesh import MeshAPIClient, MeshIdentityError, MeshRequest, MeshSettings
from test_adapters import NoNetworkTests, ScriptedTransport
from test_mesh_only import OPTIONS, completion


PREFIXED = "google/gemini-2.5-flash"
SHORT = "gemini-2.5-flash"
SAFE = (("Content-Type", "application/json"), ("X-Mesh-Version", "2026-08"),
        ("X-Mesh-Routing-Fallback", "false"))
PRIVATE = "private-routing-fixture"


class MeshIdentityTests(NoNetworkTests):
    def response(self, returned=SHORT, headers=SAFE):
        return HTTPResponse(200, headers, completion(model=returned).body)

    async def check(self, response, *, requested=PREFIXED, accepted=False):
        transport = ScriptedTransport(response)
        client = MeshAPIClient(MeshSettings("offline-identity-fixture"), transport)
        if accepted:
            result = await client.complete(MeshRequest(requested, "Offline prompt"))
            self.assertEqual(result.text, "OK")
        else:
            with self.assertRaises(MeshIdentityError) as caught:
                await client.complete(MeshRequest(requested, "Offline prompt"))
            self.assertEqual(caught.exception.category, FailureCategory.VALIDATION)
            self.assertNotIn(PRIVATE, str(caught.exception))
        self.assertEqual(len(transport.calls), 1)
        self.assertFalse(json.loads(transport.calls[0].body)["stream"])
        self.assertEqual(json.loads(transport.calls[0].body)["model"], requested)

    async def test_exact_pair_both_directions_on_supported_versions(self):
        for requested in (PREFIXED, SHORT):
            for returned in (PREFIXED, SHORT):
                for version in ("2026-08", "2026-09", "2027-01"):
                    headers = tuple((k, version if k == "X-Mesh-Version" else v) for k, v in SAFE)
                    await self.check(self.response(returned, headers), requested=requested, accepted=True)

    async def test_normalization_requires_confirmed_version(self):
        for version in (None, "2026-07", "2025-12", "2026-13", "2026-00", "2026-8", "latest", PRIVATE):
            headers = tuple((k, v) for k, v in SAFE if k != "X-Mesh-Version")
            if version is not None:
                headers += (("X-Mesh-Version", version),)
            await self.check(self.response(headers=headers))

    async def test_unknown_and_nonstring_models_are_not_aliases(self):
        for returned in (None, False, 12, [], {}, "", "Google/gemini-2.5-flash", "vertex/gemini-2.5-flash",
                         "gemini-2.5-pro", "gemini-2.5-flash-preview", SHORT + " ", PRIVATE):
            await self.check(self.response(returned))

    async def test_missing_and_unexpected_fallback_are_rejected(self):
        for value in (None, "true", "TRUE", "False", "0", "", "false,true", " false", PRIVATE):
            headers = tuple((k, v) for k, v in SAFE if k != "X-Mesh-Routing-Fallback")
            if value is not None:
                headers += (("X-Mesh-Routing-Fallback", value),)
            await self.check(self.response(headers=headers))

    async def test_routing_model_header_always_signals_unexpected_model(self):
        for value in (PREFIXED, SHORT, PRIVATE, ""):
            await self.check(self.response(headers=SAFE + (("X-Mesh-Routing-Model", value),)))

    async def test_duplicate_conflicting_and_malformed_metadata_fail_closed(self):
        for header in (("x-mesh-version", "2026-08"), ("x-mesh-version", "2026-09"),
                       ("x-mesh-routing-fallback", "false"), ("x-mesh-routing-fallback", "true"),
                       ("X-Mesh-Routing-Attempts", "0"), ("X-Mesh-Routing-Attempts", "-1"),
                       ("X-Mesh-Routing-Attempts", "9999999"), ("X-Mesh-Routing-Attempts", "١"),
                       ("X-Mesh-Routing-Attempts", "1,2"), ("X-Mesh-Routing-Attempts", PRIVATE + "\r\n")):
            await self.check(self.response(headers=SAFE + (header,)))
        await self.check(self.response(headers=SAFE + (("X-Mesh-Routing-Attempts", "1"),
                                                      ("x-mesh-routing-attempts", "1"))))

    async def test_case_insensitive_names_and_valid_attempt_counts(self):
        headers = tuple((k.swapcase(), v) for k, v in SAFE) + (("X-Mesh-Routing-Attempts", "2"),)
        await self.check(self.response(headers=headers), accepted=True)

    async def test_isolated_connectivity_uses_same_policy_and_sanitizes_duplicates(self):
        for headers, expected in ((SAFE, "passed"), (SAFE + (("x-mesh-routing-fallback", PRIVATE),), "failed"),
                                  (SAFE + (("X-Mesh-Routing-Model", PRIVATE),), "failed")):
            transport = ScriptedTransport(self.response(headers=headers))
            result = await run_connectivity(OPTIONS, execute=True, environment={"MESH_API_KEY": "offline-key"},
                                            mesh_transport=transport)
            self.assertEqual(result["mesh"]["status"], expected)
            self.assertEqual(result["mesh"]["attempts"], 1)
            self.assertEqual(result["brave"]["attempts"], 0)
            self.assertNotIn(PRIVATE, json.dumps(result))
            if expected == "failed":
                self.assertFalse(result["mesh"]["diagnostics"]["validation_checks"]["routing_metadata_valid"])
