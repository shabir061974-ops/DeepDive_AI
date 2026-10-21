"""Drafter failure location and value-shape diagnostics. No model text is recorded; offline only."""

import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deepdive_ai.agents.common import (
    AgentFailureCode, AgentFailureLocation, AgentLimits, AgentOutputError, AgentValueShape, MeshJSON, value_shape,
)
from deepdive_ai.agents.drafter import MeshDrafterAgent
from deepdive_ai.http_transport import _PinnedConnection
from deepdive_ai.live import run_live
from test_agents import BUNDLE, QUESTION, URL, FakeMesh, plan
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport, SourceTransport

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("summarize_live_run", ROOT / "scripts" / "summarize_live_run.py")
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)

C, L, S = AgentFailureCode, AgentFailureLocation, AgentValueShape
PRIVATE = "PRIVATE-MODEL-TEXT-must-not-be-recorded"
GOOD = {"text": "Offline fixture A records 12 tokens.", "evidence_ids": ["e"]}


async def draft(reply, **limits):
    text = reply if isinstance(reply, str) else json.dumps(reply)
    return await MeshDrafterAgent(MeshJSON(FakeMesh(text), "offline-model", AgentLimits(**limits))).draft(QUESTION, BUNDLE)


class OfflineCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("real DNS forbidden")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("real HTTP forbidden"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def failure(self, reply, **limits):
        with self.assertRaises(AgentOutputError) as caught:
            await draft(reply, **limits)
        error = caught.exception
        for value in vars(error).values():
            self.assertNotIn(PRIVATE, repr(value))
        return error


class WholeReplyTests(OfflineCase):
    async def test_root_shape_is_recorded(self):
        cases = (([GOOD], C.OBJECT_REQUIRED, S.ARRAY), (None, C.OBJECT_REQUIRED, S.NULL),
                 (PRIVATE, C.OBJECT_REQUIRED, S.STRING), (7, C.OBJECT_REQUIRED, S.NUMBER),
                 (2.5, C.OBJECT_REQUIRED, S.NUMBER), (True, C.OBJECT_REQUIRED, S.BOOLEAN),
                 ({}, C.FIELD_MISMATCH, S.OBJECT), ({"claims": [], "reason": PRIVATE}, C.FIELD_MISMATCH, S.OBJECT),
                 ({"claims": None}, C.LIST_REQUIRED, S.OBJECT), ({"claims": PRIVATE}, C.LIST_REQUIRED, S.OBJECT),
                 ({"claims": [GOOD, GOOD]}, C.LIST_LIMIT, S.OBJECT),
                 ({"claims": [], "status": PRIVATE}, C.DRAFTER_INVALID_STATUS, S.OBJECT),
                 ({"claims": [GOOD], "status": "insufficient_evidence"}, C.DRAFTER_INVALID_STATUS, S.OBJECT))
        for reply, code, shape in cases:
            with self.subTest(reply=str(reply)[:40]):
                error = await self.failure(json.dumps(reply), max_claims=1)
                self.assertEqual((error.code, error.location, error.row_index, error.value_shape),
                                 (code, L.WHOLE_REPLY, None, shape))

    async def test_fenced_array_reports_array_shape(self):
        error = await self.failure("```json\n" + json.dumps([GOOD]) + "\n```")
        self.assertEqual((error.code, error.location, error.value_shape), (C.OBJECT_REQUIRED, L.WHOLE_REPLY, S.ARRAY))

    async def test_unparsed_reply_has_location_but_no_value_shape(self):
        error = await self.failure(f"{PRIVATE}: no claims")
        self.assertEqual((error.code, error.location, error.row_index, error.value_shape),
                         (C.MALFORMED_JSON, L.WHOLE_REPLY, None, None))
        self.assertIsNotNone(error.json_shape)

    async def test_duplicate_claims_are_a_whole_reply_failure(self):
        error = await self.failure({"claims": [GOOD, GOOD]})
        self.assertEqual((error.code, error.location, error.row_index, error.value_shape),
                         (C.INVALID_OUTPUT, L.WHOLE_REPLY, None, S.OBJECT))


class ClaimRowTests(OfflineCase):
    async def test_row_index_and_row_shape_are_recorded(self):
        cases = (([PRIVATE], 1, C.OBJECT_REQUIRED, S.STRING), ([GOOD, None], 2, C.OBJECT_REQUIRED, S.NULL),
                 ([GOOD, [GOOD]], 2, C.OBJECT_REQUIRED, S.ARRAY), ([GOOD, 3], 2, C.OBJECT_REQUIRED, S.NUMBER),
                 ([{"text": PRIVATE}], 1, C.FIELD_MISMATCH, S.OBJECT),
                 ([{"text": "x" * 3000, "evidence_ids": ["e"]}], 1, C.TEXT_LIMIT, S.OBJECT),
                 ([{"text": "   ", "evidence_ids": ["e"]}], 1, C.INVALID_TEXT, S.OBJECT),
                 ([{"text": f"see https://{PRIVATE}.example", "evidence_ids": ["e"]}], 1, C.INVALID_OUTPUT, S.OBJECT),
                 ([GOOD, {"text": "Second claim.", "evidence_ids": "e"}], 2, C.LIST_REQUIRED, S.OBJECT),
                 ([{"text": "Unknown evidence.", "evidence_ids": [PRIVATE]}], 1, C.INVALID_OUTPUT, S.OBJECT),
                 ([{"text": "No evidence.", "evidence_ids": []}], 1, C.INVALID_OUTPUT, S.OBJECT))
        for rows, index, code, shape in cases:
            with self.subTest(rows=str(rows)[:40]):
                error = await self.failure({"claims": rows})
                self.assertEqual((error.code, error.location, error.row_index, error.value_shape),
                                 (code, L.CLAIM_ROW, index, shape))

    async def test_valid_replies_are_unchanged(self):
        self.assertEqual(len(await draft({"claims": [GOOD]})), 1)
        self.assertEqual(await draft({"claims": [], "status": "insufficient_evidence"}), ())
        self.assertEqual(await draft({"claims": []}), ())


class DiagnosticHelperTests(unittest.TestCase):
    def test_value_shape_and_locate_accept_only_fixed_values(self):
        self.assertEqual([value_shape(v) for v in ({}, [], "", 0, 0.5, False, None)],
                         [S.OBJECT, S.ARRAY, S.STRING, S.NUMBER, S.NUMBER, S.BOOLEAN, S.NULL])
        self.assertIsNone(value_shape(object()))
        error = AgentOutputError("x").locate("claim_row", [], 1)
        self.assertEqual((error.location, error.row_index, error.value_shape), (None, None, None))
        for bad_row in (0, -1, 1001, True, "2", 1.0):
            with self.subTest(row=bad_row):
                self.assertIsNone(AgentOutputError("x").locate(L.CLAIM_ROW, {}, bad_row).row_index)
        first = AgentOutputError("x").locate(L.CLAIM_ROW, [], 3)
        first.locate(L.WHOLE_REPLY, {})  # the innermost location is kept
        self.assertEqual((first.location, first.row_index, first.value_shape), (L.CLAIM_ROW, 3, S.ARRAY))


class LiveAndSummaryTests(OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def run_with(self, reply):
        mesh = MeshTransport(plan(), lambda payload: reply(payload["data"]["evidence"][0]["id"]))
        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result = await run_live(replace(OPTIONS), execute=True, environment=ENV, output_root=self.root,
                                    mesh_transport=mesh, brave_transport=SearchTransport((URL,)),
                                    source_transport=SourceTransport())
        folder = self.root / result["run_id"]
        self.assertEqual(json.loads((folder / "outcome.json").read_text()), result)
        rendered = summary.build(None, result, self.root)
        persisted = (folder / "outcome.json").read_text() + (folder / "audit.jsonl").read_text()
        for private in (PRIVATE, *ENV.values(), "Agent output requires", "Traceback"):
            self.assertNotIn(private, persisted)
            self.assertNotIn(private, rendered)
        return result, rendered

    async def test_whole_reply_failure_reaches_outcome_and_summary(self):
        result, rendered = await self.run_with(lambda eid: json.dumps([{"text": PRIVATE, "evidence_ids": [eid]}]))
        self.assertEqual((result["agent_failure_code"], result["agent_failure_location"], result["agent_value_shape"]),
                         ("object_required", "whole_reply", "array"))
        self.assertNotIn("agent_failure_row", result)
        self.assertIn("| Agent failure location | whole_reply |", rendered)
        self.assertIn("| Agent value shape | array |", rendered)
        self.assertNotIn("Agent failure row", rendered)

    async def test_claim_row_failure_reaches_outcome_and_summary(self):
        result, rendered = await self.run_with(
            lambda eid: json.dumps({"claims": [{"text": "Offline fixture A records 12 tokens.", "evidence_ids": [eid]},
                                               PRIVATE]}))
        self.assertEqual((result["agent_failure_code"], result["agent_failure_location"], result["agent_failure_row"],
                          result["agent_value_shape"]), ("object_required", "claim_row", 2, "string"))
        self.assertIn("| Agent failure location | claim_row |", rendered)
        self.assertIn("| Agent failure row | 2 |", rendered)
        self.assertIn("| Agent value shape | string |", rendered)

    def test_summary_allowlists_match_enums_and_reject_other_values(self):
        self.assertEqual(summary.FAILURE_LOCATIONS, {location.value for location in L})
        self.assertEqual(summary.VALUE_SHAPES, {shape.value for shape in S})
        result = {"mode": "live", "status": "failed", "run_id": None, "agent_failure_location": PRIVATE,
                  "agent_failure_row": "1\n| injected |", "agent_value_shape": "<b>array</b>"}
        rendered = summary.build(None, result, Path("missing-reports"))
        self.assertIn("| Agent failure location | invalid |", rendered)
        self.assertIn("| Agent failure row | invalid |", rendered)
        self.assertIn("| Agent value shape | invalid |", rendered)
        self.assertNotIn("injected", rendered)
        self.assertNotIn(PRIVATE, rendered)
        for row in (0, 1001, True, 2.0):
            with self.subTest(row=row):
                text = summary.build(None, {**result, "agent_failure_row": row}, Path("missing-reports"))
                self.assertIn("| Agent failure row | invalid |", text)


if __name__ == "__main__":
    unittest.main()
