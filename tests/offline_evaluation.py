"""Versioned authored evaluation, never live research. Run from the repository root.

Reuses existing HTTP doubles; production agents and adapters are unchanged.
"""

import asyncio
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
from unittest.mock import patch
from uuid import uuid4

from deepdive_ai.agents.common import AgentLimits
from deepdive_ai.http_transport import PublicHTTPTransport, RequestPolicy, _PinnedConnection
from deepdive_ai.mesh import MeshAPIClient, MeshSettings
from deepdive_ai.models import ResearchQuestion
from deepdive_ai.retrieval import HTTPWebRetriever, RetrievalSettings
from deepdive_ai.runtime import build_research_workflow
from deepdive_ai.search import BraveSearchDiscovery, BraveSearchSettings
from deepdive_ai.workflow import WorkflowPolicy
from test_agents import Audit, FixtureTransport
from test_live import MeshTransport, SearchTransport


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).parent / "fixtures" / "evaluation_v1.json"
MODEL = "offline-model"


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@contextmanager
def offline_guards():
    """Fail before any DNS, socket, real HTTP or private configuration access."""
    with ExitStack() as stack:
        for target in ("socket.getaddrinfo", "socket.create_connection", "socket.socket.connect",
                       "socket.socket.connect_ex", "deepdive_ai.configuration._read_private_values",
                       "deepdive_ai.mesh.MeshSettings.from_environment",
                       "deepdive_ai.search.BraveSearchSettings.from_environment"):
            stack.enter_context(patch(target, side_effect=AssertionError("Offline evaluation boundary")))
        stack.enter_context(patch.object(_PinnedConnection, "connect", side_effect=AssertionError("No real HTTP")))
        stack.enter_context(patch.object(PublicHTTPTransport, "send", side_effect=AssertionError("No real HTTP")))
        yield


def fraction(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator,
            "display": f"{numerator}/{denominator}" if denominator else "N/A"}


def url_for(case: dict, document: str) -> str:
    return f"https://{case['id'].replace('_', '-')}-{document}.evaluation.invalid/document"


class Script:
    """Scripted responses independent of expected verdicts; never an accuracy model."""

    def __init__(self, case: dict):
        self.case = case
        self.round = -1
        self.scripted_assessments: list[dict] = []

    def doc_for(self, excerpt: str) -> str:
        return next(key for key, value in self.case["documents"].items() if value == excerpt)

    def draft(self, payload: dict) -> dict:
        self.round += 1
        evidence = {self.doc_for(e["excerpt"]): e["id"] for e in payload["data"]["evidence"]}
        return {"claims": [{"text": c["text"], "evidence_ids": [evidence[d] for d in c["cite"]]}
                           for c in self.case["rounds"][self.round]["claims"]]}

    def assess(self, payload: dict) -> dict:
        data = payload["data"]
        claim = next(c for c in self.case["rounds"][self.round]["claims"] if c["text"] == data["claim"]["text"])
        rows = []
        for item in data["evidence"]:
            relation = claim["relations"][self.doc_for(item["excerpt"])]
            rows.append({"evidence_id": item["id"], "relation": relation,
                         "quote": item["excerpt"] if relation in {"supports", "contradicts"} else "",
                         "rationale": "Authored scripted relation; not live semantic accuracy."})
        self.scripted_assessments.append({"round": self.round, "claim": claim["text"], "assessments": rows})
        # Deliberately optimistic: real verifier must veto contradictions/absent support.
        return {"claim_id": data["claim"]["id"], "verdict": "supported",
                "rationale": "Authored optimistic verdict, subject to real validation.", "assessments": rows}


class RecordingVerifier:
    def __init__(self, delegate):
        self.delegate = delegate
        self.rounds: list[dict] = []

    async def verify(self, claims, bundle):
        results = await self.delegate.verify(claims, bundle)
        self.rounds.append({"claims": [asdict(c) for c in claims],
                            "verification": [asdict(r) for r in results],
                            "bundle": asdict(bundle)})
        return results


def measure(case: dict, report: dict | None, rounds: list[dict], audit: list[dict],
            attempts: dict, scripted: list[dict]) -> dict:
    """Observe actual reports first; gold expectations never generate observations."""
    expected = case["expected"]
    counts = dict.fromkeys(("supported", "unsupported", "insufficient_evidence"), 0)
    actual = {}
    summary = {"accepted_claims": []}
    if report is not None:
        claim_text = {c["id"]: c["text"] for c in report["claims"]}
        for row in report["verification"]:
            counts[row["status"]] += 1
            actual[claim_text[row["claim_id"]]] = row["status"]
        summary = json.loads(report["summary"])
    accepted = summary["accepted_claims"]
    accepted_text = [c["text"] for c in accepted]
    expected_safe = {text for text, status in expected["claims"].items() if status == "supported"}
    unsafe = sum(text not in expected_safe for text in accepted_text)
    unsafe_eligible = sum(s != "supported" for s in expected["claims"].values()) + len(set(actual) - set(expected["claims"]))
    retries = sum(e["kind"] == "retry" for e in audit)
    citation_valid = citation_total = 0
    if report is not None:
        evidence = {e["id"]: e for e in report["bundle"]["evidence"]}
        sources = {s["id"]: s for s in report["bundle"]["sources"]}
        for claim in accepted:
            for citation in claim["citations"]:
                citation_total += 1
                item = evidence.get(citation["evidence_id"])
                source = sources.get(item["source_id"]) if item else None
                if source:
                    document = next((d for d in case["documents"] if url_for(case, d) == source["url"]), None)
                    body = case["documents"][document].encode() if document else b""
                    provenance = source["provenance"]
                    valid = (citation["source_id"] == source["id"] and citation["url"] == source["url"]
                             and citation["locator"] == item["locator"] == f"bytes 0:{len(body)} (UTF-8)"
                             and citation["content_sha256"] == provenance["content_sha256"] == hashlib.sha256(body).hexdigest()
                             and citation["retrieved_at"] == provenance["retrieved_at"].isoformat()
                             == datetime.fromisoformat(load_manifest()["timestamp"]).isoformat()
                             and item["excerpt"].encode() == body
                             and citation["evidence_id"] in next(c["evidence_ids"] for c in report["claims"] if c["id"] == claim["id"]))
                    citation_valid += int(valid)
    contradictions = sum(any(a["relation"] == "contradicts" for a in row["assessments"]) for row in scripted)
    contradiction_ok = bool(report is not None and contradictions and not accepted_text
                            and actual == expected["claims"])
    correctness = {status: fraction(sum(actual.get(t) == status for t, gold in expected["claims"].items() if gold == status),
                                     sum(gold == status for gold in expected["claims"].values())) for status in counts}
    preserved = all(all(item in current["bundle"][key] for item in previous["bundle"][key])
                    for previous, current in zip(rounds, rounds[1:]) for key in ("sources", "evidence"))
    independent = (case["id"] != "supported_comparison" or
                   (len(accepted) == 2 and len({c["citations"][0]["source_id"] for c in accepted if c["citations"]}) == 2))
    checks = {"completed": report is not None,
              "report_status": report is not None and report["status"] == expected["report_status"],
              "claim_classifications": actual == expected["claims"],
              "accepted_claims": sorted(accepted_text) == sorted(expected_safe),
              "no_unsafe_acceptances": unsafe == 0,
              "citation_traceability": citation_valid == citation_total and all(c["citations"] for c in accepted),
              "separate_comparison_sources": independent,
              "contradiction_guard": contradiction_ok if expected["contradiction"] else True,
              "retry_count": retries == expected["retries"] and retries <= 1,
              "provenance_preserved": preserved,
              "audit_completed": bool(audit and audit[-1]["kind"] == "completed"),
              "request_bounds": attempts["mesh"] <= 7 and attempts["search"] <= 2 and attempts["source"] <= 4}
    if case["id"] == "missing_evidence":
        checks["no_invented_evidence"] = bool(report is not None and not report["bundle"]["sources"]
                                               and not report["bundle"]["evidence"] and attempts["mesh"] == 1 and attempts["source"] == 0)
    if case["id"] == "evidence_gap_recovery":
        checks["gap_then_support"] = (len(rounds) == 2 and [r["status"] for r in rounds[0]["verification"]] == ["insufficient_evidence"]
                                      and [r["status"] for r in rounds[1]["verification"]] == ["supported"])
    return {"id": case["id"], "question": case["question"], "passed": all(checks.values()), "checks": checks,
            "observed": {"report_status": report["status"] if report else None, "claims": actual,
                         "counts": counts, "accepted_claims": len(accepted), "retries": retries, "attempts": attempts,
                         "contradictory_assessments": contradictions},
            "metrics": {"classification_correctness": correctness, "unsafe_acceptances": fraction(unsafe, unsafe_eligible),
                        "citation_traceability": fraction(citation_valid, citation_total),
                        "contradiction_safeguard": fraction(int(contradiction_ok), int(expected["contradiction"])),
                        "retry_correctness": fraction(int(checks["retry_count"]), 1)},
            "expected": expected, "rounds": rounds, "audit": audit, "report": report}


async def run_case(case: dict, timestamp: datetime) -> dict:
    script = Script(case)
    outcomes = [{"tasks": [{"instruction": "Check the authored records for this fictional question."}]}]
    for turn in case["rounds"]:
        if turn["claims"]:
            outcomes.extend([script.draft, *([script.assess] * len(turn["claims"]))])
    mesh = MeshTransport(*outcomes)
    search = SearchTransport(*(tuple(url_for(case, d) for d in turn["discover"]) for turn in case["rounds"]))
    source = FixtureTransport({url_for(case, d): text.encode() for d, text in case["documents"].items()})
    audit = Audit()
    policy = RequestPolicy(max_retries=0, max_requests=7)
    workflow = build_research_workflow(
        mesh=MeshAPIClient(MeshSettings("offline-evaluation-mesh", policy=policy), mesh), model=MODEL,
        discovery=BraveSearchDiscovery(BraveSearchSettings("offline-evaluation-search", policy=policy), search),
        retriever=HTTPWebRetriever(RetrievalSettings(policy=policy), source, lambda: timestamp),
        audit=audit, limits=AgentLimits(max_tasks=1, max_claims=2, max_sources=2, max_evidence=2,
                                      max_model_calls=7, max_discovery_calls=2, max_fetch_calls=4,
                                      discovery_results_per_task=2, chunks_per_document=1),
        policy=WorkflowPolicy(max_research_retries=1, max_tasks=1, max_claims=2), clock=lambda: timestamp)
    recorder = RecordingVerifier(workflow.verifier)
    workflow.verifier = recorder
    report = None
    failure_type = None
    try:
        value = await workflow.run(ResearchQuestion(case["id"], case["question"]), run_id="evaluation-" + case["id"])
        report = asdict(value)
    except Exception:
        # Fixed category only: never include exception text or arbitrary response data.
        failure_type = "offline_case_execution_failed"
    result = measure(case, report, recorder.rounds, [asdict(e) for e in audit.events],
                     {"mesh": len(mesh.calls), "search": len(search.calls), "source": len(source.calls)}, script.scripted_assessments)
    result["failure_category"] = failure_type
    return result


async def evaluate() -> dict:
    manifest = load_manifest()
    with offline_guards():
        results = [await run_case(case, datetime.fromisoformat(manifest["timestamp"])) for case in manifest["cases"]]
    checkpoint = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    metrics = {}
    for name in ("unsafe_acceptances", "citation_traceability", "contradiction_safeguard", "retry_correctness"):
        metrics[name] = fraction(sum(r["metrics"][name]["numerator"] for r in results),
                                 sum(r["metrics"][name]["denominator"] for r in results))
    metrics["classification_correctness"] = {
        status: fraction(sum(r["metrics"]["classification_correctness"][status]["numerator"] for r in results),
                         sum(r["metrics"]["classification_correctness"][status]["denominator"] for r in results))
        for status in ("supported", "unsupported", "insufficient_evidence")}
    metrics["case_correctness"] = fraction(sum(r["passed"] for r in results), len(results))
    return {"version": manifest["version"], "notice": manifest["notice"], "git_checkpoint": checkpoint,
            "working_tree_dirty": dirty, "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
            "fixture_hashes": {c["id"]: {d: hashlib.sha256(t.encode()).hexdigest() for d, t in c["documents"].items()}
                               for c in manifest["cases"]},
            "evaluation_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "cases": results, "metrics": metrics, "passed": all(r["passed"] for r in results)}


def markdown(result: dict) -> str:
    lines = ["# Offline self-evaluation " + result["version"], "", result["notice"], "",
             "Deterministic scripted correctness only; not live semantic accuracy or a benchmark.", "",
             f"Git checkpoint: `{result['git_checkpoint']}`; dirty tree: `{result['working_tree_dirty']}`.",
             f"Manifest SHA-256: `{result['manifest_sha256']}`. Full hashes and round observations are in JSON.", "",
             "| Case | Pass | S/U/I | Accepted | Citation traceability | Unsafe acceptances | Contradiction safeguard | Retry correctness | Retries observed/expected |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for case in result["cases"]:
        obs, metrics = case["observed"], case["metrics"]
        counts = "/".join(str(obs["counts"][k]) for k in ("supported", "unsupported", "insufficient_evidence"))
        lines.append(f"| {case['id']} | {case['passed']} | {counts} | {obs['accepted_claims']} | "
                     f"{metrics['citation_traceability']['display']} | {metrics['unsafe_acceptances']['display']} | "
                     f"{metrics['contradiction_safeguard']['display']} | {metrics['retry_correctness']['display']} | "
                     f"{obs['retries']}/{case['expected']['retries']} |")
    lines += ["", "S/U/I are final supported/unsupported/insufficient-evidence counts; rounds are not double-counted.",
              "N/A means a zero denominator, not a passing score. Failed observations remain in JSON."]
    lines += ["", "## Aggregate metrics", "", "| Metric | Numerator/denominator |", "| --- | --- |"]
    for name, metric in result["metrics"].items():
        if name == "classification_correctness":
            lines.extend(f"| {status} classification correctness | {score['display']} |" for status, score in metric.items())
        else:
            lines.append(f"| {name} | {metric['display']} |")
    return "\n".join(lines) + "\n"


def write_artifacts(result: dict, *, artifact_root: Path | None = None) -> Path:
    parent = artifact_root if artifact_root is not None else ROOT / "artifacts"
    directory = parent / ("offline-evaluation-v" + result["version"]) / ("run-" + uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "results.json").write_text(json.dumps(result, indent=2, default=lambda v: v.isoformat(), ensure_ascii=True) + "\n", encoding="utf-8")
    (directory / "results.md").write_text(markdown(result), encoding="utf-8")
    return directory


if __name__ == "__main__":
    result = asyncio.run(evaluate())
    directory = write_artifacts(result)
    print(markdown(result))
    print("Artifacts: " + str(directory.relative_to(ROOT)))
    raise SystemExit(0 if result["passed"] else 1)
