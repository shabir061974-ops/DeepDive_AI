"""Planner task-count instruction and strict validation. Offline only."""

import json
import unittest
from unittest.mock import patch

from deepdive_ai.agents.common import AgentFailureCode, AgentLimits, AgentOutputError, MeshJSON
from deepdive_ai.agents.planner import MeshPlannerAgent
from test_agents import QUESTION, FakeMesh


def tasks(count):
    return {"tasks": [{"instruction": f"Research query {index}"} for index in range(1, count + 1)]}


class PlannerTaskScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        guard = patch("socket.getaddrinfo", side_effect=AssertionError("No network"))
        guard.start()
        self.addCleanup(guard.stop)

    async def plan(self, output, max_tasks):
        mesh = FakeMesh(output)
        planned = await MeshPlannerAgent(MeshJSON(mesh, "offline-model", AgentLimits(max_tasks=max_tasks))).plan(QUESTION)
        return planned, mesh.calls[0]["instruction"]

    async def test_single_task_limit_asks_for_exactly_one_covering_task(self):
        planned, instruction = await self.plan(tasks(1), 1)
        self.assertEqual(len(planned), 1)
        self.assertIn("exactly ONE task", instruction)
        self.assertIn("entire question", instruction)
        self.assertIn("counter-evidence", instruction)
        self.assertIn("Never return more than one task", instruction)
        for contradictory in ("Return 1 to 1", "Include tasks", "Decompose", "independent"):
            self.assertNotIn(contradictory, instruction)

    async def test_single_task_limit_still_rejects_extra_tasks_without_truncation(self):
        for count in (2, 3):
            mesh = FakeMesh(tasks(count))
            planner = MeshPlannerAgent(MeshJSON(mesh, "offline-model", AgentLimits(max_tasks=1)))
            with self.subTest(count=count):
                with self.assertRaises(AgentOutputError) as caught:
                    await planner.plan(QUESTION)
                self.assertEqual(caught.exception.code, AgentFailureCode.LIST_LIMIT)
                self.assertEqual(len(mesh.calls), 1)

    async def test_single_task_limit_keeps_empty_and_fenced_handling(self):
        with self.assertRaises(AgentOutputError) as caught:
            await self.plan(tasks(0), 1)
        self.assertEqual(caught.exception.code, AgentFailureCode.EMPTY_TASKS)
        planned, _ = await self.plan("```json\n" + json.dumps(tasks(1)) + "\n```", 1)
        self.assertEqual([task.instruction for task in planned], ["Research query 1"])

    async def test_multi_task_wording_is_unchanged(self):
        for max_tasks in (2, 8):
            with self.subTest(max_tasks=max_tasks):
                planned, instruction = await self.plan(tasks(2), max_tasks)
                self.assertEqual(len(planned), 2)
                self.assertTrue(instruction.startswith(
                    "Decompose this question into independent, specific research queries. "
                    f"Return 1 to {max_tasks} tasks. Include tasks checking counter-evidence. "))
                self.assertNotIn("exactly ONE", instruction)
        with self.assertRaises(AgentOutputError) as caught:
            await self.plan(tasks(3), 2)
        self.assertEqual(caught.exception.code, AgentFailureCode.LIST_LIMIT)


if __name__ == "__main__":
    unittest.main()
