"""Separate Verifier quote limit (live 400) with exact-substring grounding unchanged. Offline only."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deepdive_ai.agents.common import AgentFailureCode, AgentLimits, AgentOutputError, MeshJSON
from deepdive_ai.agents.verifier import MeshVerifierAgent
from deepdive_ai.http_transport import HTTPResponse, _PinnedConnection
from deepdive_ai.live import run_live
from deepdive_ai.models import Claim, Evidence, ResearchBundle, VerificationStatus
from test_agents import SOURCE, TASK, URL, FakeMesh, draft, plan
from test_live import ENV, OPTIONS, MeshTransport, SearchTransport

C = AgentFailureCode
WORDS = ("A 405 response means the server knows the request method but the target resource does not support it. "
         "A 501 response means the server does not support the functionality required to fulfil the request. ")
EXCERPT = (WORDS * 4).strip()  # about 800 characters of real, ordered text
assert len(EXCERPT) > 500
COUNTER = "The target resource does not support it is false: the method is permitted for this resource."
E1 = Evidence("e1", "s", TASK.id, EXCERPT, f"bytes 0:{len(EXCERPT)} (UTF-8)")
E2 = Evidence("e2", "s", TASK.id, COUNTER, f"bytes 0:{len(COUNTER)} (UTF-8)")
ONE = ResearchBundle((SOURCE,), (E1,))
TWO = ResearchBundle((SOURCE,), (E1, E2))
CLAIM = Claim("q:claim:1", "A 405 response means the method is known but not supported by the target resource.", ("e1",))
LIVE = AgentLimits(max_text_chars=160, max_quote_chars=400)
DEFAULT = AgentLimits(max_text_chars=160)


def row(evidence_id="e1", relation="supports", quote=EXCERPT[:100], rationale="Exact grounding."):
    return {"evidence_id": evidence_id, "relation": relation, "quote": quote, "rationale": rationale}


def reply(*rows, verdict="supported", rationale="Offline verdict."):
    return {"claim_id": CLAIM.id, "verdict": verdict, "rationale": rationale, "assessments": list(rows)}


async def verify(output, limits=LIVE, bundle=ONE, claim=CLAIM):
    mesh = FakeMesh(output)
    result = await MeshVerifierAgent(MeshJSON(mesh, "offline-model", limits)).verify((claim,), bundle)
    return result, mesh


class OfflineCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for guard in (patch("socket.getaddrinfo", side_effect=AssertionError("real DNS forbidden")),
                      patch.object(_PinnedConnection, "connect", side_effect=AssertionError("real HTTP forbidden"))):
            guard.start()
            self.addCleanup(guard.stop)

    async def assert_code(self, output, code, limits=LIVE, bundle=ONE, claim=CLAIM):
        with self.assertRaises(AgentOutputError) as caught:
            await verify(output, limits, bundle, claim)
        self.assertIs(caught.exception.code, code)


class QuoteLimitTests(OfflineCase):
    def test_setting_defaults_to_text_limit_and_is_validated(self):
        self.assertIsNone(AgentLimits().max_quote_chars)
        self.assertEqual(AgentLimits().quote_chars, AgentLimits().max_text_chars)
        self.assertEqual(DEFAULT.quote_chars, 160)
        self.assertEqual(LIVE.quote_chars, 400)
        for bad in (0, -1, True, 1.5, "400"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                AgentLimits(max_quote_chars=bad)

    async def test_live_boundary_160_to_400(self):
        for length in (160, 161, 399, 400):
            with self.subTest(length=length):
                quote = EXCERPT[:length]
                self.assertEqual(len(quote), length)
                result, _ = await verify(reply(row(quote=quote)))
                self.assertEqual(result[0].status, VerificationStatus.SUPPORTED)
                self.assertIn(json.dumps(quote, ensure_ascii=False)[1:-1], result[0].rationale)
        await self.assert_code(reply(row(quote=EXCERPT[:401])), C.VERIFIER_QUOTE_TOO_LONG)
        await self.assert_code(reply(row(quote=EXCERPT[:600])), C.VERIFIER_QUOTE_TOO_LONG)

    async def test_default_profile_still_rejects_quotes_over_the_text_limit(self):
        result, _ = await verify(reply(row(quote=EXCERPT[:160])), DEFAULT)
        self.assertEqual(result[0].status, VerificationStatus.SUPPORTED)
        await self.assert_code(reply(row(quote=EXCERPT[:161])), C.VERIFIER_QUOTE_TOO_LONG, DEFAULT)

    async def test_prompt_states_each_limit(self):
        _, mesh = await verify(reply(row()), DEFAULT)
        self.assertIn("The overall rationale, every assessment rationale and every quote must each be at most "
                      "160 characters; choose a shorter exact quote rather than exceed this limit.",
                      mesh.calls[0]["instruction"])
        _, mesh = await verify(reply(row()), LIVE)
        instruction = mesh.calls[0]["instruction"]
        self.assertIn("The overall rationale and every assessment rationale must each be at most 160 characters. "
                      "Every quote must be at most 400 characters; choose a shorter exact quote rather than "
                      "exceed this limit.", instruction)
        self.assertNotIn("every quote must each be at most", instruction)

    async def test_exact_substring_matching_is_unchanged(self):
        long_quote = EXCERPT[:300]
        variants = {
            "one_character_changed": long_quote[:150] + ("X" if long_quote[150] != "X" else "Y") + long_quote[151:],
            "whitespace_normalized": long_quote.replace(" ", "  ", 1),
            "case_changed": long_quote.upper(),
            "curly_quotes": long_quote.replace("A ", "“A ", 1),
            "joined_out_of_order": EXCERPT[200:300] + EXCERPT[:100],
            "trailing_invented_text": long_quote[:250] + " and it is always cached.",
        }
        for name, quote in variants.items():
            with self.subTest(variant=name):
                self.assertNotIn(quote, EXCERPT)
                await self.assert_code(reply(row(quote=quote)), C.VERIFIER_QUOTE_NOT_IN_EVIDENCE)

    async def test_malformed_quotes_are_rejected(self):
        for quote, code in ((None, C.VERIFIER_QUOTE_INVALID), (["x"], C.VERIFIER_QUOTE_INVALID),
                            (5, C.VERIFIER_QUOTE_INVALID), ("", C.VERIFIER_QUOTE_MISSING),
                            (" " * 300, C.VERIFIER_QUOTE_MISSING), ("\x00" + EXCERPT[:50], C.VERIFIER_QUOTE_NOT_IN_EVIDENCE),
                            (" " * 401, C.VERIFIER_QUOTE_TOO_LONG)):
            with self.subTest(quote=repr(quote)[:20]):
                await self.assert_code(reply(row(quote=quote)), code)
        await self.assert_code(reply(row(relation="contradicts", quote="")), C.VERIFIER_QUOTE_MISSING)

    async def test_contradictions_keep_their_veto_with_long_quotes(self):
        support = row(quote=EXCERPT[:350])
        contradiction = row("e2", "contradicts", COUNTER)
        result, _ = await verify(reply(support, contradiction), bundle=TWO)
        self.assertEqual(result[0].status, VerificationStatus.INSUFFICIENT)
        only_contradiction = reply(row(relation="contradicts", quote=EXCERPT[:350]), verdict="supported")
        result, _ = await verify(only_contradiction)
        self.assertEqual(result[0].status, VerificationStatus.UNSUPPORTED)
        # An over-limit contradiction quote fails closed; the veto is never silently dropped.
        await self.assert_code(reply(support, row("e2", "contradicts", COUNTER + " " + EXCERPT[:400])),
                               C.VERIFIER_QUOTE_TOO_LONG, bundle=TWO)

    async def test_misleading_partial_quotes_do_not_establish_support(self):
        partial = "the target resource does not support it"  # exact substring of the first excerpt only
        self.assertIn(partial, EXCERPT)
        self.assertNotIn(partial, COUNTER)  # case differs, so it cannot ground the counter-evidence
        for relations, expected in (((("e1", "irrelevant"),), VerificationStatus.INSUFFICIENT),
                                    ((("e1", "insufficient"),), VerificationStatus.INSUFFICIENT)):
            with self.subTest(relations=relations):
                rows = [row(e, r, partial) for e, r in relations]
                result, _ = await verify(reply(*rows))
                self.assertEqual(result[0].status, expected)
        # An exact partial quote marked as support cannot override cited contradicting evidence.
        result, _ = await verify(reply(row("e1", "supports", partial),
                                       row("e2", "contradicts", "the method is permitted for this resource")), bundle=TWO)
        self.assertEqual(result[0].status, VerificationStatus.INSUFFICIENT)
        # Uncited supporting evidence never becomes a citation.
        claim = Claim("q:claim:1", CLAIM.text, ("e2",))
        result, _ = await verify(reply(row("e1", "supports", EXCERPT[:300]), row("e2", "irrelevant", "")),
                                 bundle=TWO, claim=claim)
        self.assertEqual(result[0].status, VerificationStatus.INSUFFICIENT)

    async def test_claim_and_rationale_limits_stay_at_160(self):
        await self.assert_code(reply(row(), rationale="r" * 161), C.TEXT_LIMIT)
        await self.assert_code(reply(row(rationale="r" * 161)), C.TEXT_LIMIT)
        with self.assertRaises(AgentOutputError) as caught:
            await verify(reply(row()), claim=Claim("q:claim:1", "c" * 161, ("e1",)))
        self.assertIs(caught.exception.code, C.TEXT_LIMIT)
        result, _ = await verify(reply(row(rationale="r" * 160), rationale="r" * 160))
        self.assertEqual(result[0].status, VerificationStatus.SUPPORTED)


class LongSource:
    def __init__(self):
        self.calls = []

    async def send(self, request):
        self.calls.append(request.url)
        return HTTPResponse(200, (("Content-Type", "text/plain; charset=utf-8"),), EXCERPT.encode())


class LiveQuoteLimitTests(OfflineCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        Path("artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def run_with_quote_length(self, length):
        def verifier(payload):
            excerpt = payload["data"]["evidence"][0]["excerpt"]
            return {"claim_id": payload["data"]["claim"]["id"], "verdict": "supported",
                    "rationale": "Offline verdict.",
                    "assessments": [{"evidence_id": payload["data"]["evidence"][0]["id"], "relation": "supports",
                                     "quote": excerpt[:length], "rationale": "Exact grounding."}]}

        def short_draft(payload):
            result = draft(payload)
            result["claims"][0]["text"] = CLAIM.text
            return result

        mesh = MeshTransport(plan(), short_draft, verifier)
        with patch("deepdive_ai.demo.run_demo", side_effect=AssertionError("No fixture fallback")):
            result = await run_live(replace(OPTIONS), execute=True, environment=ENV, output_root=self.root,
                                    mesh_transport=mesh, brave_transport=SearchTransport((URL,)),
                                    source_transport=LongSource())
        instruction = json.loads(json.loads(mesh.calls[2].body)["messages"][0]["content"])["instruction"]
        return result, instruction

    async def test_live_profile_accepts_400_and_rejects_401(self):
        result, instruction = await self.run_with_quote_length(400)
        self.assertEqual((result["status"], result.get("report_status")), ("completed", "verified"))
        self.assertEqual(result["attempts"], {"mesh": 3, "brave": 1, "source": 1})
        self.assertIn("Every quote must be at most 400 characters", instruction)
        self.assertIn("every assessment rationale must each be at most 160 characters", instruction)
        result, _ = await self.run_with_quote_length(401)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["agent_failure_code"], "verifier_quote_too_long")
        self.assertFalse((self.root / result["run_id"] / "report.json").exists())

    def test_live_profile_values(self):
        text = (Path(__file__).resolve().parents[1] / "src" / "deepdive_ai" / "live.py").read_text(encoding="utf-8")
        self.assertIn("max_text_chars=160", text)
        self.assertIn("max_quote_chars=400", text)
        for unchanged in ("max_model_calls=7", "max_discovery_calls=2", "max_fetch_calls=2", "output_tokens=1024",
                          "max_response_chars=16_000", "max_prompt_chars=20_000"):
            self.assertIn(unchanged, text)


if __name__ == "__main__":
    unittest.main()
