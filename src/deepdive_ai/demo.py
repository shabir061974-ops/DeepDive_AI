"""Explicit offline fixtures through the real five-agent workflow. No provider clients."""

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.resources import files
from typing import Any, Callable
from uuid import uuid4

from .agents.common import AgentLimits
from .audit import AuditEvent
from .discovery import DiscoveredSource
from .mesh import MeshRequest, MeshResponse
from .models import Report, ResearchQuestion, Source, SourceProvenance
from .retrieval import RetrievedDocument, SearchRequest
from .runtime import build_research_workflow
from .workflow import WorkflowPolicy


NOTICE = "OFFLINE FIXTURE DEMO — NO LIVE RESEARCH"
FIXTURE = json.loads(files("deepdive_ai").joinpath("fixtures/research.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    description: str
    retries: int = 0


SCENARIOS = (
    Scenario("supported", "Supported evidence", "A claim supported by the authored source fixture."),
    Scenario("unsupported", "Direct contradiction", "Counter-evidence rejects the draft claim."),
    Scenario("conflict", "Conflicting sources", "Follow-up finds a contradiction; verification fails closed.", 1),
    Scenario("recovery", "Evidence gap resolved", "A bounded research-again loop adds supporting evidence.", 1),
    Scenario("empty", "No evidence available", "Empty discovery exhausts one retry without inventing an answer.", 1),
    Scenario("failure", "Malformed assessment", "Invalid scripted model output aborts safely with a failure event."),
)


def scenario_list() -> list[dict]:
    return [{**asdict(s), "question": FIXTURE["question"]} for s in SCENARIOS]


class FixtureMesh:
    """Scripted MeshClient implementation, not a network client or semantic model."""

    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.calls = 0

    async def complete(self, request: MeshRequest) -> MeshResponse:
        self.calls += 1
        if self.calls > 8:
            raise ValueError("Fixture call budget exceeded")
        payload = json.loads(request.prompt)
        role, data = payload["role"], payload["data"]
        if role == "planner":
            result = {"tasks": [{"instruction": FIXTURE["instruction"]}]}
        elif role == "drafter":
            result = {"claims": [{"text": FIXTURE["source_a_text"],
                                   "evidence_ids": [data["evidence"][0]["id"]]}]}
        elif role == "verifier":
            if self.scenario.id == "failure":
                return MeshResponse("authored malformed fixture response", "offline-script")
            gap = self.scenario.id in {"conflict", "recovery"} and len(data["evidence"]) == 1
            rows = []
            for item in data["evidence"]:
                if item["excerpt"] not in (FIXTURE["source_a_text"], FIXTURE["counter_text"]):
                    raise ValueError("Unknown fixture evidence")
                relation = ("insufficient" if gap else "contradicts" if item["excerpt"] == FIXTURE["counter_text"]
                            else "supports")
                rows.append({"evidence_id": item["id"], "relation": relation,
                             "quote": "" if gap else item["excerpt"],
                             "rationale": "Scripted offline fixture assessment; not a live model evaluation."})
            result = {"claim_id": data["claim"]["id"],
                      "verdict": "insufficient_evidence" if gap else "unsupported" if self.scenario.id == "unsupported" else "supported",
                      "rationale": "Authored scenario verdict. The real verifier applies its validation and contradiction vetoes.",
                      "assessments": rows}
        else:
            raise ValueError("Unsupported fixture model role")
        await asyncio.sleep(0)  # Yield to stream actual events, without artificial delays.
        return MeshResponse(json.dumps(result), "offline-script")


class FixtureDiscovery:
    def __init__(self, scenario: Scenario):
        self.scenario, self.calls = scenario, 0

    async def discover(self, request: SearchRequest) -> tuple[DiscoveredSource, ...]:
        self.calls += 1
        if self.calls > 2:
            raise ValueError("Fixture discovery budget exceeded")
        if self.scenario.id == "empty":
            return ()
        url = FIXTURE["source_b_url"] if self.scenario.id == "unsupported" or self.calls > 1 else FIXTURE["source_a_url"]
        return (DiscoveredSource(url),)


class FixtureRetriever:
    """Known local byte fixtures only; preserve hashes and explicit fixture provenance."""

    def __init__(self, scenario: Scenario):
        self.scenario, self.calls = scenario, 0

    async def search(self, request: SearchRequest) -> tuple[Source, ...]:
        raise ValueError("Use the separate offline fixture discovery interface")

    async def fetch(self, url: str) -> RetrievedDocument:
        if url not in (FIXTURE["source_a_url"], FIXTURE["source_b_url"]) or self.calls >= 2:
            raise ValueError("Fixture fetch outside the allowlist or budget")
        self.calls += 1
        counter = url == FIXTURE["source_b_url"] and self.scenario.id != "recovery"
        body = FIXTURE["counter_text" if counter else "source_a_text"].encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        source_id = hashlib.sha256((url + "\n" + digest).encode()).hexdigest()
        source = Source(source_id, url, "Authored fixture B" if url == FIXTURE["source_b_url"] else "Authored fixture A",
                        SourceProvenance(datetime.fromisoformat(FIXTURE["fixture_timestamp"]), digest,
                                         "offline authored fixture; local bytes; synthetic fixture timestamp"))
        return RetrievedDocument(source, body, url, (url,), "text/plain", "utf-8")


class Recorder:
    def __init__(self, notify: Callable[[dict], None] | None):
        self.events: list[dict] = []
        self.audit: list[dict] = []
        self.notify = notify

    def record(self, event_type: str, **data: Any) -> None:
        event = {"sequence": len(self.events) + 1, "type": event_type,
                 "occurred_at": datetime.now(timezone.utc).isoformat(), **data}
        self.events.append(event)
        if self.notify is not None:
            self.notify(event)

    def emit(self, event: AuditEvent) -> None:
        row = {**asdict(event), "kind": event.kind.value, "occurred_at": event.occurred_at.isoformat()}
        self.audit.append(row)
        self.record("audit", **row)


class ObservedAgent:
    """Delegates unchanged calls; records actual starts/completions, never simulated progress."""

    def __init__(self, agent: Any, name: str, recorder: Recorder):
        self.agent, self.name, self.recorder = agent, name, recorder

    async def _call(self, method: str, *args: Any) -> Any:
        self.recorder.record("agent_started", agent=self.name)
        try:
            result = await getattr(self.agent, method)(*args)
        except Exception:
            self.recorder.record("agent_failed", agent=self.name)
            raise
        details = {}
        if method == "plan":
            details["tasks"] = [asdict(task) for task in result]
        elif method == "verify":
            details["verification"] = [asdict(item) for item in result]
        self.recorder.record("agent_completed", agent=self.name, **details)
        return result

    async def plan(self, *args: Any) -> Any:
        return await self._call("plan", *args)

    async def research(self, *args: Any) -> Any:
        return await self._call("research", *args)

    async def draft(self, *args: Any) -> Any:
        return await self._call("draft", *args)

    async def verify(self, *args: Any) -> Any:
        return await self._call("verify", *args)

    async def synthesize(self, *args: Any) -> Any:
        return await self._call("synthesize", *args)


def export_markdown(report: Report, scenario: Scenario, audit: list[dict]) -> str:
    def literal(text: str) -> str:
        for symbol in ("\\", "`", "*", "_", "[", "]", "<", ">", "#"):
            text = text.replace(symbol, "\\" + symbol)
        return text

    lines = ["# DeepDive AI — offline demonstration", "", f"> {NOTICE}",
             "> Authored evidence and scripted model assessments. Not a live research report.", "",
             f"## {scenario.title}", "", report.question.text, "", f"Report status: {report.status.value}", "",
             "## Accepted fixture claims", ""]
    accepted = json.loads(report.summary)["accepted_claims"]
    for claim in accepted:
        refs = " ".join(f"[source {index + 1}](#source-{citation['source_id']})" for index, citation in enumerate(claim["citations"]))
        lines.append(f"- {literal(claim['text'])} {refs}")
    if not accepted:
        lines.append("No claims accepted. No answer has been invented.")
    lines += ["", "## Verification ledger (scripted assessments)", ""]
    by_claim = {c.id: c for c in report.claims}
    for verification in report.verification:
        lines += [f"### {verification.status.value}", "", literal(by_claim[verification.claim_id].text), "",
                  "```json", json.dumps(json.loads(verification.rationale), indent=2), "```", ""]
    lines += ["## Fixture sources and evidence", ""]
    for source in report.bundle.sources:
        lines += [f"<a id=\"source-{source.id}\"></a>", f"### {source.title}", "",
                  f"Fixture identifier (not a live citation): `{source.url}`", "",
                  f"SHA-256: `{source.provenance.content_sha256}`", "",
                  f"Method: {source.provenance.retrieval_method}", "",
                  f"Synthetic fixture timestamp: {source.provenance.retrieved_at.isoformat()}", ""]
        for evidence in report.bundle.evidence:
            if evidence.source_id == source.id:
                lines += [literal(evidence.excerpt), "", f"Locator: {evidence.locator}", ""]
    lines += ["## Actual run audit events", ""]
    lines += [f"- {event['occurred_at']} — {event['kind']} — attempt {event['attempt']}" for event in audit]
    return "\n".join(lines) + "\n"


async def run_demo(scenario_id: str, notify: Callable[[dict], None] | None = None) -> dict:
    scenario = next((s for s in SCENARIOS if s.id == scenario_id), None)
    if scenario is None:
        raise ValueError("Unknown offline scenario")
    recorder = Recorder(notify)
    mesh, discovery, retriever = FixtureMesh(scenario), FixtureDiscovery(scenario), FixtureRetriever(scenario)
    limits = AgentLimits(max_tasks=1, max_claims=1, max_sources=2, max_evidence=2,
                         max_model_calls=8, max_discovery_calls=2, max_fetch_calls=2)
    workflow = build_research_workflow(mesh=mesh, model="offline-script-not-a-live-model", discovery=discovery,
        retriever=retriever, audit=recorder, limits=limits,
        policy=WorkflowPolicy(max_research_retries=scenario.retries, max_tasks=1, max_claims=1))
    for name in ("planner", "researcher", "drafter", "verifier", "synthesizer"):
        setattr(workflow, name, ObservedAgent(getattr(workflow, name), name, recorder))
    try:
        async with asyncio.timeout(10):
            report = await workflow.run(ResearchQuestion("fixture-question", FIXTURE["question"]),
                                        run_id=f"offline-{scenario.id}-{uuid4().hex}")
    except Exception:
        return {"notice": NOTICE, "scenario_id": scenario.id, "status": "failed", "report": None,
                "error": "Offline workflow stopped safely. No report was generated.",
                "events": recorder.events, "audit": recorder.audit}
    checks = {"supported": 0, "unsupported": 0, "insufficient_evidence": 0}
    for result in report.verification:
        checks[result.status.value] += 1
    report_view = json.loads(json.dumps(asdict(report), default=lambda value: value.isoformat()))
    return {"notice": NOTICE, "scenario_id": scenario.id, "status": "completed", "report": report_view,
            "summary": json.loads(report.summary), "events": recorder.events, "audit": recorder.audit,
            "metrics": {**checks, "sources": len(report.bundle.sources), "evidence": len(report.bundle.evidence),
                        "research_retries": sum(e["kind"] == "retry" for e in recorder.audit),
                        "scripted_model_calls": mesh.calls, "fixture_discoveries": discovery.calls,
                        "fixture_fetches": retriever.calls},
            "evaluation_note": "Computed run counts only. No model accuracy score or benchmark evaluation is available.",
            "markdown": export_markdown(report, scenario, recorder.audit)}
