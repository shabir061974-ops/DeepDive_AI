"""Verifier contract failure codes. Validation order and acceptance are unchanged; offline only."""

import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deepdive_ai.agents.common import AgentFailureCode, AgentLimits, AgentOutputError, MeshJSON
from deepdive_ai.agents.verifier import MeshVerifierAgent
from deepdive_ai.http_transport import _PinnedConnection
from deepdive_ai.live import run_live
from deepdive_ai.models import Claim, Evidence, ResearchBundle, VerificationStatus
from test_agents import BUNDLE, CLAIM, EVIDENCE, SOURCE, STATEMENT, TASK, URL, FakeMesh, assessment, draft, plan
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport, SourceTransport

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("summarize_live_run", ROOT / "scripts" / "summarize_live_run.py")
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)

C = AgentFailureCode
PRIVATE = "PRIVATE-QUOTE-OR-MODEL-TEXT"
VERIFIER_CODES = {code for code in AgentFailureCode if code.value.startswith("verifier_")}
SECOND = Evidence("e2", "s", TASK.id, "Second offline excerpt.", "bytes 0:23 (UTF-8)")
TWO_EVIDENCE = ResearchBundle((SOURCE,), (EVIDENCE, SECOND))


def row(evidence_id="e", relation="supports", quote=STATEMENT, rationale="Controlled offline semantic assessment"):
    return {"evidence_id": evidence_id, "relation": relation, "quote": quote, "rationale": rationale}


def reply(*rows, claim_id=CLAIM.id, verdict="supported", rationale="Controlled offline verdict"):
    return {"claim_id": claim_id, "verdict": verdict, "rationale": rationale, "assessments": list(rows)}


async def verify(output, bundle=BUNDLE, claim=CLAIM, **limits):
    llm = MeshJSON(FakeMesh(output), "offline-model", AgentLimits(**limits))
    return await MeshVerifierAgent(llm).verify((claim,), bundle)


class OfflineCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("real DNS forbidden")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("real HTTP forbidden"))):
            guard.start()
            self.addCleanup(guard.stop)


class VerifierCodeTests(OfflineCase):
    async def assert_code(self, output, code, message, bundle=BUNDLE, **limits):
        with self.assertRaises(AgentOutputError) as caught:
            await verify(output, bundle, **limits)
        self.assertIs(caught.exception.code, code)
        self.assertEqual(str(caught.exception), message)  # messages are unchanged
        self.assertNotIn(PRIVATE, str(caught.exception))

    async def test_every_verifier_code_and_unchanged_message(self):
        invalid_claim = "Verifier returned an invalid claim or verdict"
        evidence = "Verifier returned unknown or duplicate evidence assessments"
        quote = "Verifier quote is invalid or too long"
        cases = [
            (reply(row(), claim_id=PRIVATE), C.VERIFIER_CLAIM_ID_MISMATCH, invalid_claim),
            (reply(row(), claim_id=None), C.VERIFIER_CLAIM_ID_MISMATCH, invalid_claim),
            (reply(row(), verdict="supports"), C.VERIFIER_INVALID_VERDICT, invalid_claim),
            (reply(row(), verdict=None), C.VERIFIER_INVALID_VERDICT, invalid_claim),
            (reply(row(), verdict=PRIVATE), C.VERIFIER_INVALID_VERDICT, invalid_claim),
            (reply(row(evidence_id=5)), C.VERIFIER_EVIDENCE_ID_INVALID, evidence),
            (reply(row(evidence_id=["e"])), C.VERIFIER_EVIDENCE_ID_INVALID, evidence),
            (reply(row(evidence_id=PRIVATE)), C.VERIFIER_EVIDENCE_ID_UNKNOWN, evidence),
            (reply(row(), row()), C.VERIFIER_EVIDENCE_ID_DUPLICATE, evidence),
            (reply(row(relation="support")), C.VERIFIER_INVALID_RELATION, "Verifier returned an invalid evidence relation"),
            (reply(row(relation=None)), C.VERIFIER_INVALID_RELATION, "Verifier returned an invalid evidence relation"),
            (reply(row(quote=None)), C.VERIFIER_QUOTE_INVALID, quote),
            (reply(row(quote=[STATEMENT])), C.VERIFIER_QUOTE_INVALID, quote),
            (reply(row(quote="x" * 41)), C.VERIFIER_QUOTE_TOO_LONG, quote),
            (reply(row(quote="")), C.VERIFIER_QUOTE_MISSING, "Semantic support/contradiction requires an exact quote"),
            (reply(row(relation="contradicts", quote="  ")), C.VERIFIER_QUOTE_MISSING,
             "Semantic support/contradiction requires an exact quote"),
            (reply(row(quote=PRIVATE[:20])), C.VERIFIER_QUOTE_NOT_IN_EVIDENCE, "Verifier quoted text outside retrieved evidence"),
            (reply(row(relation="irrelevant", quote="not in it")), C.VERIFIER_QUOTE_NOT_IN_EVIDENCE,
             "Verifier quoted text outside retrieved evidence"),
            (reply(), C.VERIFIER_INCOMPLETE_ASSESSMENT, "Verifier must assess all supplied evidence"),
        ]
        self.assertEqual({code for _, code, _ in cases}, VERIFIER_CODES)
        for output, code, message in cases:
            with self.subTest(code=code, output=str(output)[:60]):
                await self.assert_code(output, code, message, max_text_chars=40)

    async def test_incomplete_assessment_with_multiple_evidence(self):
        await self.assert_code(reply(row()), C.VERIFIER_INCOMPLETE_ASSESSMENT,
                               "Verifier must assess all supplied evidence", TWO_EVIDENCE)

    async def test_validation_order_is_unchanged(self):
        # When several checks fail, the first check in the original order still rejects.
        cases = [
            (reply(row(), claim_id="other", verdict="bad"), C.VERIFIER_CLAIM_ID_MISMATCH),
            (reply(row(relation="bad"), verdict="bad"), C.VERIFIER_INVALID_VERDICT),
            (reply(row(evidence_id=PRIVATE, relation="bad", quote=None)), C.VERIFIER_EVIDENCE_ID_UNKNOWN),
            (reply(row(), row(relation="bad")), C.VERIFIER_EVIDENCE_ID_DUPLICATE),
            (reply(row(relation="bad", quote=None)), C.VERIFIER_INVALID_RELATION),
            (reply(row(quote="x" * 41)), C.VERIFIER_QUOTE_TOO_LONG),
            (reply(row(quote="not in the evidence", rationale="")), C.VERIFIER_QUOTE_NOT_IN_EVIDENCE),
        ]
        for output, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(AgentOutputError) as caught:
                    await verify(output, max_text_chars=40)
                self.assertIs(caught.exception.code, code)
        # Earlier shared checks keep their existing specific codes.
        for output, code in ((reply(row(), rationale="r" * 41), C.TEXT_LIMIT),
                             ({**reply(row()), "assessments": "x"}, C.LIST_REQUIRED),
                             (reply({"evidence_id": "e", "relation": "supports", "quote": STATEMENT}), C.FIELD_MISMATCH),
                             (reply(row(rationale="")), C.INVALID_TEXT)):
            with self.subTest(code=code):
                with self.assertRaises(AgentOutputError) as caught:
                    await verify(output, max_text_chars=40)
                self.assertIs(caught.exception.code, code)

    async def test_accepted_outputs_and_downgrades_are_unchanged(self):
        result = await verify(assessment)
        self.assertEqual(result[0].status, VerificationStatus.SUPPORTED)
        self.assertEqual(result[0].evidence_ids, ("e",))
        for relations, verdict, expected in (
                (["insufficient"], "supported", VerificationStatus.INSUFFICIENT),
                (["irrelevant"], "supported", VerificationStatus.INSUFFICIENT),
                (["supports"], "unsupported", VerificationStatus.INSUFFICIENT),
                (["contradicts"], "supported", VerificationStatus.UNSUPPORTED),
                (["supports"], "insufficient_evidence", VerificationStatus.INSUFFICIENT)):
            with self.subTest(relations=relations, verdict=verdict):
                result = await verify(lambda p: assessment(p, relations, verdict))
                self.assertEqual(result[0].status, expected)
        # Empty quotes remain allowed for irrelevant/insufficient relations.
        result = await verify(reply(row(relation="irrelevant", quote="")))
        self.assertEqual(result[0].status, VerificationStatus.INSUFFICIENT)
        # Claims without evidence still skip the model call.
        mesh = FakeMesh()
        llm = MeshJSON(mesh, "offline-model", AgentLimits())
        result = await MeshVerifierAgent(llm).verify((Claim("q:claim:1", STATEMENT, ()),), BUNDLE)
        self.assertEqual((result[0].status, mesh.calls), (VerificationStatus.INSUFFICIENT, []))


class LiveVerifierCodeTests(OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def run_with_verifier(self, build):
        mesh = MeshTransport(plan(), draft, build)
        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result = await run_live(replace(OPTIONS), execute=True, environment=ENV, output_root=self.root,
                                    mesh_transport=mesh, brave_transport=SearchTransport((URL,)),
                                    source_transport=SourceTransport())
        folder = self.root / result["run_id"]
        self.assertEqual(json.loads((folder / "outcome.json").read_text()), result)
        return result, folder

    async def test_codes_reach_outcome_and_public_summary_without_private_text(self):
        def wrong_quote(payload):
            result = assessment(payload)
            result["assessments"][0]["quote"] = PRIVATE
            return result

        def wrong_claim(payload):
            return {**assessment(payload), "claim_id": PRIVATE}

        def incomplete(payload):
            return {**assessment(payload), "assessments": []}

        def unknown_evidence(payload):
            result = assessment(payload)
            result["assessments"][0]["evidence_id"] = PRIVATE
            return result

        for build, code in ((wrong_quote, "verifier_quote_not_in_evidence"), (wrong_claim, "verifier_claim_id_mismatch"),
                            (incomplete, "verifier_incomplete_assessment"),
                            (unknown_evidence, "verifier_evidence_id_unknown")):
            with self.subTest(code=code):
                result, folder = await self.run_with_verifier(build)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["failure_category"], "agent_contract_failure")
                self.assertEqual(result["agent_failure_code"], code)
                self.assertEqual(result["attempts"]["mesh"], 3)
                self.assertFalse((folder / "report.json").exists())
                rendered = summary.build(None, result, self.root)
                self.assertIn(f"| Agent failure code | {code} |", rendered)
                self.assertIn("| Verifier | &#10060; failed or stopped here |", rendered)
                persisted = (folder / "outcome.json").read_text() + (folder / "audit.jsonl").read_text()
                evidence_ids = [line for line in (folder / "audit.jsonl").read_text().splitlines() if '"e-' in line]
                self.assertEqual(evidence_ids, [])
                for private in (PRIVATE, *ENV.values(), "private-response-id", "Verifier quoted",
                                "Verifier returned", "Verifier must", "Traceback", "Offline fixture A records"):
                    self.assertNotIn(private, persisted)
                    self.assertNotIn(private, rendered)
                self.assertNotRegex(persisted + rendered, r"e-[0-9a-f]{64}")


class SummaryCodeTests(unittest.TestCase):
    def test_summary_shows_each_verifier_code_and_rejects_others(self):
        for code in VERIFIER_CODES:
            with self.subTest(code=code):
                self.assertEqual(summary.code(code.value), code.value)
                self.assertLessEqual(len(code.value), 64)
        for bad in ("Verifier quoted text outside retrieved evidence", "verifier quote", "<b>x</b>", PRIVATE):
            self.assertEqual(summary.code(bad), "invalid")


if __name__ == "__main__":
    unittest.main()
