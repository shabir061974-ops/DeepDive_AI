"""GitHub live-demo summary and workflow guards. Offline only; no provider or GitHub access."""

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("summarize_live_run", ROOT / "scripts" / "summarize_live_run.py")
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)

RUN_ID = "56943dceac174beebd53e9b10573344c"
URL = "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/501"
EXCERPT = "PRIVATE-EXCERPT-TEXT 501 is the appropriate response when the server does not recognize the method."
RATIONALE = "PRIVATE-RATIONALE-TEXT"
PROMPT = "PRIVATE-PROMPT-TEXT"
TICK, CROSS = "&#9989;", "&#10060;"


def outcome(**changes):
    data = {"mode": "live", "ready": True, "blockers": [], "status": "completed", "report_status": "verified",
            "attempts": {"mesh": 4, "brave": 1, "source": 1}, "diagnostics": {}, "cost_status": "unknown_may_be_billable",
            "run_id": RUN_ID, "profile": {"mesh_requests": 7}}
    data.update(changes)
    return data


def report(claims=None, status="supported", url=URL):
    claims = claims if claims is not None else [
        "A 501 Not Implemented response is appropriate when the server does not recognize the request method",
        "If a server recognizes a method but intentionally does not allow it the response is 405"]
    return {"mode": "live", "report": {
        "question": {"id": RUN_ID, "text": PROMPT},
        "summary": json.dumps({"accepted_claims": [], "note": PROMPT}),
        "bundle": {"sources": [{"id": "s1", "url": url, "title": url,
                                "provenance": {"retrieved_at": "2026-10-20T20:52:24+00:00", "content_sha256": "ab"}}],
                   "evidence": [{"id": "e1", "source_id": "s1", "excerpt": EXCERPT, "locator": "chars 0:2000"}]},
        "claims": [{"id": f"c{i}", "text": text, "evidence_ids": ["e1"]} for i, text in enumerate(claims, 1)],
        "verification": [{"claim_id": f"c{i}", "status": status, "rationale": RATIONALE, "evidence_ids": ["e1"]}
                         for i in range(1, len(claims) + 1)],
        "status": "verified"}}


class Fixture:
    def __init__(self, test):
        self.temp = tempfile.TemporaryDirectory()
        test.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.reports = self.dir / "reports"

    def write(self, name, value):
        path = self.dir / name
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return path

    def run_files(self, kinds=None, report_value=None, run_id=RUN_ID):
        folder = self.reports / run_id
        folder.mkdir(parents=True)
        if kinds is not None:
            (folder / "audit.jsonl").write_text("".join(json.dumps({"kind": k, "note": PROMPT}) + "\n" for k in kinds),
                                                encoding="utf-8")
        if report_value is not None:
            (folder / "report.json").write_text(report_value if isinstance(report_value, str) else json.dumps(report_value),
                                                encoding="utf-8")

    def build(self, preflight=None, outcome_value=None):
        pre = self.write("preflight.json", preflight) if preflight is not None else None
        out = self.write("outcome.json", outcome_value) if outcome_value is not None else None
        return summary.build(summary.load_json(pre), summary.load_json(out), self.reports)


COMPLETE = ["started", "planned", "researched", "drafted", "verified", "completed"]


class SummaryTests(unittest.TestCase):
    def assert_private_text_absent(self, text):
        for private in (EXCERPT, RATIONALE, PROMPT, "PRIVATE"):
            self.assertNotIn(private, text)

    def test_successful_run_shows_five_stages_counts_claims_and_plain_urls(self):
        fixture = Fixture(self)
        fixture.run_files(COMPLETE, report())
        text = fixture.build(outcome(ready=True), outcome())
        for agent in ("Planner", "Researcher", "Drafter", "Verifier", "Synthesizer"):
            self.assertIn(f"| {agent} | {TICK} completed |", text)
        self.assertIn("| Status | completed |", text)
        self.assertIn("| Report status | verified |", text)
        self.assertIn("| Mesh | 4 | 7 |", text)
        self.assertIn("| Brave | 1 | 2 |", text)
        self.assertIn("| Source | 1 | 4 |", text)
        self.assertIn("**supported**: ` A 501 Not Implemented response is appropriate", text)
        self.assertEqual(text.count("Citation: "), 2)
        self.assertIn(f"Citation: ` {URL} ` (retrieved ` 2026-10-20T20:52:24+00:00 `)", text)
        self.assertNotIn("](", text)
        self.assert_private_text_absent(text)

    def test_failed_run_marks_stage_and_omits_report(self):
        fixture = Fixture(self)
        fixture.run_files(["started", "planned", "researched", "failed"])
        text = fixture.build(None, outcome(status="failed", report_status=None, failure_category="agent_contract_failure",
                                           agent_failure_code="malformed_agent_json", agent_json_shape="embedded_fence",
                                           attempts={"mesh": 2, "brave": 1, "source": 1}))
        self.assertIn(f"| Planner | {TICK} completed |", text)
        self.assertIn(f"| Researcher | {TICK} completed |", text)
        self.assertIn(f"| Drafter | {CROSS} failed or stopped here |", text)
        self.assertIn("| Verifier | not reached |", text)
        self.assertIn("| Synthesizer | not reached |", text)
        self.assertIn("| Agent failure code | malformed_agent_json |", text)
        self.assertIn("| Agent json shape | embedded_fence |", text)
        self.assertIn("No final report was produced", text)

    def test_partial_and_retried_runs(self):
        fixture = Fixture(self)
        kinds = ["started", "planned", "researched", "drafted", "verified", "retry", "researched", "drafted",
                 "verified", "completed"]
        fixture.run_files(kinds, report(status="insufficient_evidence"))
        text = fixture.build(None, outcome(report_status="needs_review"))
        self.assertIn(f"| Researcher | {TICK} completed (2 rounds) |", text)
        self.assertIn("Research-again rounds: 1", text)
        self.assertIn("| Report status | needs_review |", text)
        self.assertIn("**insufficient_evidence**", text)

    def test_preflight_blocked_and_missing_outputs(self):
        fixture = Fixture(self)
        text = fixture.build(outcome(mode="preflight", ready=False, status="not_run", run_id=None, attempts={},
                                     blockers=["provider_credentials_missing_or_invalid"]))
        self.assertIn("provider_credentials_missing_or_invalid", text)
        self.assertIn("No live execution was recorded", text)
        self.assertIn("| Mesh | 0 | 7 |", text)
        self.assertIn("No run output was recorded", Fixture(self).build())

    def test_missing_or_malformed_run_files_never_raise(self):
        fixture = Fixture(self)
        folder = fixture.reports / RUN_ID
        folder.mkdir(parents=True)
        (folder / "audit.jsonl").write_text('not json\n{"kind": ["x"]}\n[]\n{"kind": "planned"}\n', encoding="utf-8")
        bad_report = {"report": {"claims": [{"id": ["x"], "text": 5}, "x"], "verification": [{"claim_id": {}}],
                                 "bundle": {"sources": [{"id": []}], "evidence": "x"}}}
        (folder / "report.json").write_text(json.dumps(bad_report), encoding="utf-8")
        text = fixture.build(None, outcome(status="failed"))
        self.assertIn(f"| Planner | {TICK} completed |", text)
        self.assertIn("**invalid**: —", text)
        self.assertIn("Citation: none recorded", text)
        for broken in ("{", "null", "[]", '{"status": NaN}', "﻿" + json.dumps(outcome())):
            with self.subTest(outcome=broken[:12]):
                self.assertIsInstance(Fixture(self).build(None, broken), str)
        missing = Fixture(self)
        text = missing.build(None, outcome())
        self.assertIn("audit trail not available", text)
        self.assertIn("No final report was produced", text)

    def test_untrusted_text_is_inert(self):
        hostile = ("[click](https://evil.example) ![img](https://evil.example/x.png) <script>alert(1)</script> "
                   "@octocat | col | **bold** `code` www.evil.example ‮evil​\nsecond line # heading")
        fixture = Fixture(self)
        fixture.run_files(COMPLETE, report(claims=[hostile]))
        text = fixture.build(None, outcome())
        line = next(l for l in text.splitlines() if l.startswith("1. "))
        body = line.split("**: ", 1)[1]
        # The whole untrusted value is one code span whose delimiter outruns any inner backticks.
        match = re.fullmatch(r"(`+) (.*) \1", body)
        self.assertIsNotNone(match)
        self.assertNotIn(match.group(1), match.group(2))
        for removed in ("‮", "​"):
            self.assertNotIn(removed, text)
        self.assertIn("second line # heading", match.group(2))
        self.assertEqual(sum(1 for l in text.splitlines() if "second line" in l), 1)

    def test_backtick_runs_cannot_close_the_span(self):
        for value in ("`", "``x``", "a ``` b", "````"):
            with self.subTest(value=value):
                rendered = summary.text(value)
                fence = re.match(r"`+", rendered).group(0)
                inner = rendered[len(fence) + 1:-len(fence) - 1]
                self.assertTrue(rendered.endswith(" " + fence))
                self.assertNotIn(fence, inner)
        self.assertEqual(summary.text("‮​ \n"), "—")

    def test_text_is_bounded(self):
        rendered = summary.text("a" * 5000)
        self.assertLessEqual(len(rendered), summary.MAX_TEXT_CHARS + 4)
        self.assertTrue(rendered.endswith("… `"))
        fixture = Fixture(self)
        fixture.run_files(COMPLETE, report(claims=[f"claim {i}" for i in range(25)]))
        text = fixture.build(None, outcome())
        self.assertIn("5 further claims not shown", text)
        self.assertLessEqual(len(text), summary.MAX_SUMMARY_CHARS + 100)

    def test_unsafe_urls_are_replaced(self):
        for bad in ("javascript:alert(1)", "data:text/html,x", "https://user:pass@example.com/", "ftp://example.com/",
                    "https://" + "a" * 400 + ".example", "", 5, None):
            with self.subTest(url=str(bad)[:20]):
                self.assertEqual(summary.url(bad), "(invalid URL)")

    def test_codes_and_run_ids_are_allowlisted(self):
        self.assertEqual(summary.code("verified"), "verified")
        for bad in ("Verified", "a b", "<x>", "x" * 65, 5, None):
            self.assertEqual(summary.code(bad), "invalid")
        fixture = Fixture(self)
        fixture.run_files(COMPLETE, report())
        text = fixture.build(None, outcome(run_id="../" + RUN_ID, status="<b>x</b>"))
        self.assertIn("| Run ID | none |", text)
        self.assertIn("| Status | invalid |", text)
        self.assertNotIn("Claims and citations", text)

    def test_main_appends_and_reports_write_errors(self):
        fixture = Fixture(self)
        fixture.run_files(COMPLETE, report())
        target = fixture.dir / "summary.md"
        target.write_text("existing\n", encoding="utf-8")
        out = fixture.write("outcome.json", outcome())
        self.assertEqual(summary.main(["--summary-file", str(target), "--outcome", str(out),
                                       "--preflight", str(fixture.dir / "absent.json"),
                                       "--reports-root", str(fixture.reports)]), 0)
        written = target.read_text(encoding="utf-8")
        self.assertTrue(written.startswith("existing\n## DeepDive AI live research run"))
        self.assertEqual(summary.main(["--summary-file", str(fixture.dir / "missing-dir" / "s.md")]), 1)


class WorkflowGuardTests(unittest.TestCase):
    workflow = (ROOT / ".github" / "workflows" / "live-demo.yml").read_text(encoding="utf-8")

    def test_manual_trigger_only_and_minimal_permissions(self):
        on_block = self.workflow.split("\non:", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("workflow_dispatch:", on_block)
        for trigger in ("push", "pull_request", "schedule", "workflow_run", "repository_dispatch"):
            self.assertNotRegex(on_block, rf"\n  {trigger}\b")
        self.assertIn("permissions:\n  contents: read\n", self.workflow)
        self.assertNotIn("id-token", self.workflow)
        self.assertNotIn("write", self.workflow.split("permissions:", 1)[1].split("concurrency:", 1)[0])
        self.assertIn("environment: live-demo", self.workflow)
        self.assertIn("if: github.ref == 'refs/heads/master'", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)
        self.assertRegex(self.workflow, r"timeout-minutes: \d+")

    def test_actions_are_sha_pinned_and_nothing_is_uploaded(self):
        uses = re.findall(r"uses: (\S+)", self.workflow)
        self.assertEqual(len(uses), 2)
        for reference in uses:
            self.assertRegex(reference, r"^actions/(checkout|setup-python)@[0-9a-f]{40}$")
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertNotIn("upload-artifact", self.workflow)
        self.assertNotIn("cache", self.workflow.split("steps:", 1)[1].lower().replace("# ", ""))

    def test_inputs_and_secrets_never_reach_scripts_directly(self):
        steps = self.workflow.split("steps:", 1)[1]
        self.assertNotIn("${{ inputs.", steps)
        self.assertNotIn("${{ github.event", self.workflow)
        self.assertEqual(steps.count("${{ secrets.MESH_API_KEY }}"), 2)
        self.assertEqual(steps.count("${{ secrets.BRAVE_SEARCH_API_KEY }}"), 2)
        self.assertEqual(len(re.findall(r"\$\{\{ secrets\.", self.workflow)), 4)
        summary_step = steps.split("Write sanitized run summary", 1)[1]
        self.assertNotIn("secrets.", summary_step)

    def test_single_execution_after_preflight_without_retries(self):
        self.assertEqual(self.workflow.count("'--execute'"), 1)
        preflight = self.workflow.index("Live preflight")
        execute = self.workflow.index("Execute one live research run")
        self.assertLess(preflight, execute)
        self.assertNotRegex(self.workflow.lower(), r"\bretry\b|continue-on-error|for \(|while \(")
        self.assertIn("-cne 'yes'", self.workflow)
        self.assertIn("'google/gemini-2.5-flash'", self.workflow)
        self.assertIn("if: always()", self.workflow)


if __name__ == "__main__":
    unittest.main()
