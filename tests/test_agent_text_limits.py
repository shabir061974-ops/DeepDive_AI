"""Drafter/Verifier text-length instructions and unchanged strict limits. Offline only."""

import unittest
from unittest.mock import patch

from deepdive_ai.agents.common import AgentFailureCode, AgentLimits, AgentOutputError, MeshJSON
from deepdive_ai.agents.drafter import MeshDrafterAgent
from deepdive_ai.agents.verifier import MeshVerifierAgent
from deepdive_ai.models import Claim, Evidence, ResearchBundle
from test_agents import EVIDENCE, QUESTION, SOURCE, TASK, FakeMesh, assessment


LONG_EXCERPT = "Offline fixture text " * 12
LONG_EVIDENCE = Evidence("e", "s", TASK.id, LONG_EXCERPT, f"bytes 0:{len(LONG_EXCERPT)} (UTF-8)")
LONG_BUNDLE = ResearchBundle((SOURCE,), (LONG_EVIDENCE,))


class AgentTextLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        guard = patch("socket.getaddrinfo", side_effect=AssertionError("No network"))
        guard.start()
        self.addCleanup(guard.stop)

    def llm(self, mesh, max_text_chars):
        return MeshJSON(mesh, "offline-model", AgentLimits(max_text_chars=max_text_chars))

    async def drafter_instruction(self, max_text_chars):
        mesh = FakeMesh({"claims": []})
        await MeshDrafterAgent(self.llm(mesh, max_text_chars)).draft(QUESTION, LONG_BUNDLE)
        return mesh.calls[0]["instruction"]

    async def verifier_instruction(self, max_text_chars):
        mesh = FakeMesh(assessment)
        claim = Claim("q:claim:1", "Offline fixture text", ("e",))
        try:
            await MeshVerifierAgent(self.llm(mesh, max_text_chars)).verify((claim,), LONG_BUNDLE)
        except AgentOutputError:
            pass  # the fixture's full-excerpt quote may exceed small limits; only the prompt matters here
        return mesh.calls[0]["instruction"]

    async def test_instructions_state_the_configured_limit(self):
        for limit in (160, 321):
            with self.subTest(limit=limit):
                self.assertIn(f"Each claim text must be at most {limit} characters",
                              await self.drafter_instruction(limit))
                self.assertIn("The overall rationale, every assessment rationale and every quote must each be at most "
                              f"{limit} characters", await self.verifier_instruction(limit))
        self.assertNotIn("160", await self.drafter_instruction(321))
        self.assertNotIn("160", await self.verifier_instruction(321))

    async def test_overlong_claim_is_still_rejected_without_truncation(self):
        limit = 40
        mesh = FakeMesh({"claims": [{"text": "x" * (limit + 1), "evidence_ids": ["e"]}]})
        with self.assertRaises(AgentOutputError) as caught:
            await MeshDrafterAgent(self.llm(mesh, limit)).draft(QUESTION, ResearchBundle((SOURCE,), (EVIDENCE,)))
        self.assertEqual(caught.exception.code, AgentFailureCode.TEXT_LIMIT)
        mesh = FakeMesh({"claims": [{"text": "x" * limit, "evidence_ids": ["e"]}]})
        claims = await MeshDrafterAgent(self.llm(mesh, limit)).draft(QUESTION, ResearchBundle((SOURCE,), (EVIDENCE,)))
        self.assertEqual(claims[0].text, "x" * limit)

    async def test_overlong_verifier_rationale_and_quote_are_still_rejected(self):
        limit = 2_000
        claim = Claim("q:claim:1", "Offline fixture text", ("e",))

        def long_overall(payload):
            return {**assessment(payload), "rationale": "r" * (limit + 1)}

        def long_row_rationale(payload):
            result = assessment(payload)
            result["assessments"][0]["rationale"] = "r" * (limit + 1)
            return result

        for builder in (long_overall, long_row_rationale):
            with self.subTest(case=builder.__name__):
                with self.assertRaises(AgentOutputError) as caught:
                    await MeshVerifierAgent(self.llm(FakeMesh(builder), limit)).verify((claim,), LONG_BUNDLE)
                self.assertEqual(caught.exception.code, AgentFailureCode.TEXT_LIMIT)

        short = 100  # shorter than the excerpt the fixture quotes in full
        claim = Claim("q:claim:1", "Offline fixture", ("e",))
        with self.assertRaises(AgentOutputError):
            await MeshVerifierAgent(self.llm(FakeMesh(assessment), short)).verify((claim,), LONG_BUNDLE)


if __name__ == "__main__":
    unittest.main()
