"""Evaluation scoring, isolation and failure retention; no external execution."""

import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from offline_evaluation import (
    MANIFEST, ROOT, evaluate, fraction, load_manifest, markdown, measure,
    offline_guards, Script, write_artifacts,
)


class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.result = await evaluate()

    def score(self, index: int, report: dict) -> dict:
        manifest = load_manifest()
        case = self.result["cases"][index]
        # Actual scripted assessment observations, not expected outcome labels.
        scripted = [{"assessments": json.loads(v["rationale"])["assessments"]}
                    for turn in case["rounds"] for v in turn["verification"]]
        return measure(manifest["cases"][index], report, case["rounds"], case["audit"],
                       case["observed"]["attempts"], scripted)

    async def test_six_cases_and_measured_aggregate_results(self):
        self.assertTrue(self.result["passed"])
        self.assertEqual(len(self.result["cases"]), 6)
        scores = self.result["metrics"]
        for name, expected in (("case_correctness", (6, 6)), ("unsafe_acceptances", (0, 3)),
                               ("citation_traceability", (3, 3)), ("contradiction_safeguard", (2, 2)),
                               ("retry_correctness", (6, 6))):
            self.assertEqual(scores[name], fraction(*expected))
        self.assertEqual([scores["classification_correctness"][s]["display"] for s in
                          ("supported", "unsupported", "insufficient_evidence")], ["3/3", "1/1", "2/2"])
        self.assertEqual(self.result["manifest_sha256"], hashlib.sha256(MANIFEST.read_bytes()).hexdigest())
        self.assertIn("not live semantic accuracy", markdown(self.result))

    async def test_bad_effective_verdict_is_not_replaced_by_expected_verdict(self):
        record = copy.deepcopy(self.result["cases"][1]["report"])
        record["verification"][0]["status"] = "supported"
        record["summary"] = json.dumps({"accepted_claims": [{"id": record["claims"][0]["id"],
            "text": record["claims"][0]["text"], "citations": []}]})
        score = self.score(1, record)
        self.assertFalse(score["passed"])
        self.assertEqual(score["observed"]["counts"]["supported"], 1)
        self.assertEqual(score["metrics"]["unsafe_acceptances"], fraction(1, 1))
        self.assertEqual(score["metrics"]["classification_correctness"]["unsupported"], fraction(0, 1))
        self.assertEqual(score["metrics"]["contradiction_safeguard"], fraction(0, 1))

    async def test_citation_provenance_tampering_is_detected(self):
        for field in ("url", "content_sha256", "locator", "retrieved_at"):
            with self.subTest(field=field):
                record = copy.deepcopy(self.result["cases"][0]["report"])
                summary = json.loads(record["summary"])
                summary["accepted_claims"][0]["citations"][0][field] = "altered"
                record["summary"] = json.dumps(summary)
                score = self.score(0, record)
                self.assertFalse(score["passed"])
                self.assertEqual(score["metrics"]["citation_traceability"], fraction(1, 2))

    async def test_guard_blocks_network_and_private_configuration(self):
        from deepdive_ai.mesh import MeshSettings
        # Resolve functions inside the guard so patched names, not saved aliases, are used.
        with offline_guards():
            import deepdive_ai.configuration as config
            for operation in (lambda: socket.getaddrinfo("example.invalid", 443),
                              config._read_private_values, MeshSettings.from_environment):
                with self.assertRaises(AssertionError):
                    operation()

    async def test_failure_observations_and_artifacts_are_retained(self):
        with patch.object(Script, "assess", return_value={"invalid": "AUTHORED failure"}):
            failed = await evaluate()
        self.assertFalse(failed["passed"])
        case = failed["cases"][0]
        self.assertIsNone(case["report"])
        self.assertEqual(case["audit"][-1]["kind"], "failed")
        self.assertEqual(case["failure_category"], "offline_case_execution_failed")
        (ROOT / "artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temp:
            first = write_artifacts(failed, artifact_root=Path(temp))
            second = write_artifacts(self.result, artifact_root=Path(temp))
            self.assertNotEqual(first, second)
            retained = json.loads((first / "results.json").read_text(encoding="utf-8"))
            self.assertFalse(retained["passed"])
            self.assertEqual(retained["cases"][0]["audit"][-1]["kind"], "failed")
            self.assertIn("False", (first / "results.md").read_text(encoding="utf-8"))
            for secret in ("offline-evaluation-mesh", "offline-evaluation-search", "private-response-id"):
                self.assertNotIn(secret, (first / "results.json").read_text(encoding="utf-8"))

    async def test_zero_denominators_and_repeatability(self):
        self.assertEqual(fraction(0, 0)["display"], "N/A")
        empty = self.result["cases"][4]
        self.assertEqual(empty["metrics"]["citation_traceability"], fraction(0, 0))
        self.assertEqual(await evaluate(), self.result)

    async def test_expected_labels_do_not_drive_scripted_responses(self):
        case = copy.deepcopy(load_manifest()["cases"][1])
        case["expected"]["claims"] = {"invented gold label": "supported"}
        from offline_evaluation import run_case
        with offline_guards():
            measured = await run_case(case, datetime.fromisoformat(load_manifest()["timestamp"]))
        self.assertFalse(measured["passed"])
        self.assertEqual(measured["observed"]["counts"]["unsupported"], 1)


if __name__ == "__main__":
    unittest.main()
