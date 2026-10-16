"""Explicit bounded real-adapter research. Preflight is offline; no fixture path."""

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from .agents.common import AgentLimits, AgentOutputError
from .connectivity import TestOptions, _rate
from .extraction import BoundedContentExtractor, ExtractionSettings
from .http_transport import (
    FailureCategory, HTTPError, HTTPRequest, HTTPResponse, HTTPTransport,
    PublicHTTPTransport, RequestPolicy, status_category,
)
from .mesh import MeshAPIClient, MeshSettings
from .models import ResearchQuestion, nonempty
from .persistence import JsonlAuditSink, write_record
from .retrieval import HTTPWebRetriever, RetrievalSettings
from .runtime import build_research_workflow
from .search import BraveSearchDiscovery, BraveSearchSettings
from .workflow import WorkflowPolicy


PROFILE = {"mesh_requests": 7, "brave_requests": 2, "source_requests": 4,
           "http_retries": 0, "research_retries": 1, "tasks": 1, "claims": 2,
           "output_tokens_per_mesh_request": 1024, "mesh_timeout_seconds": 15,
           "brave_timeout_seconds": 10, "source_timeout_seconds": 10,
           "overall_timeout_seconds": 120}


@dataclass(frozen=True)
class LiveOptions:
    question: str = field(repr=False)
    model: str
    approvals: TestOptions = field(default_factory=TestOptions)

    def __post_init__(self) -> None:
        nonempty(self.question, "question")
        nonempty(self.model, "model")
        self.question.encode("utf-8")
        if (len(self.question) > 160 or len(self.question.split()) > 20
                or any(ord(c) < 32 for c in self.question)
                or len(self.model) > 120 or not self.model.isascii()
                or any(not (c.isalnum() or c in "/-._") for c in self.model)):
            raise ValueError("Question or model exceeds controlled run constraints")
        if not isinstance(self.approvals, TestOptions) or self.approvals.brave_only or self.approvals.mesh_only:
            raise ValueError("Live research requires both providers")
        if self.approvals.mesh_version is not None:
            raise ValueError("Version pinning is not supported by this runner")


def policy(timeout: int, requests: int, response_bytes: int) -> RequestPolicy:
    return RequestPolicy(timeout_seconds=timeout, max_retries=0, max_requests=requests,
                         max_concurrency=1, max_request_bytes=24_000, max_response_bytes=response_bytes)


class ObservedTransport:
    """Safe counters/diagnostics only. Does not retain requests, headers or bodies."""

    def __init__(self, transport: HTTPTransport, *, model: str | None = None):
        self.transport = transport
        self.model = model
        self.attempts = 0
        self.failure: dict | None = None

    async def send(self, request: HTTPRequest) -> HTTPResponse:
        self.attempts += 1
        try:
            response = await self.transport.send(request)
            if response.status >= 400:
                self.failure = {"category": status_category(response.status).value,
                                "stage": None, "http_status": response.status}
            if self.model is not None and response.status == 200:
                # The Mesh client independently validates strict JSON/completion shape.
                # This runner additionally requires the selected model and bounded usage.
                try:
                    data = json.loads(response.body.decode("utf-8"))
                    usage = data["usage"]
                    names = ("prompt_tokens", "completion_tokens", "total_tokens")
                    valid = (data.get("model") == self.model and isinstance(usage, dict)
                             and all(type(usage.get(n)) is int and 0 <= usage[n] <= 1_000_000_000 for n in names)
                             and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
                             and usage["completion_tokens"] <= 1024)
                    if not valid:
                        raise ValueError
                except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                    raise HTTPError("Mesh model or usage incompatible", category=FailureCategory.VALIDATION,
                                    http_status=200) from None
            return response
        except HTTPError as error:
            self.failure = {"category": error.category.value,
                            "stage": error.stage.value if error.stage else None, "http_status": error.http_status}
            raise
        except asyncio.CancelledError:
            # wait_for cancels the inner transport before raising TimeoutError
            # outside this observer. Preserve only its fixed progress snapshot.
            stage, status = request.progress.snapshot() if request.progress is not None else (None, None)
            self.failure = {"category": "request_cancelled", "stage": stage.value if stage else None,
                            "http_status": status}
            raise


def http_failure(error: BaseException) -> dict | None:
    """Read typed diagnostics through sanitizing agent boundaries, never messages."""
    seen: set[int] = set()
    for _ in range(8):
        if id(error) in seen:
            break
        seen.add(id(error))
        if isinstance(error, HTTPError):
            return {"category": error.category.value, "stage": error.stage.value if error.stage else None,
                    "http_status": error.http_status}
        nested = error.__cause__ if error.__cause__ is not None else error.__context__
        if nested is None:
            break
        error = nested
    return None


async def run_live(
    options: LiveOptions, *, execute: bool = False, environment: Mapping[str, str] | None = None,
    mesh_transport: HTTPTransport | None = None, brave_transport: HTTPTransport | None = None,
    source_transport: HTTPTransport | None = None, output_root: Path | None = None,
) -> dict:
    """Use real adapters exclusively; test callers may explicitly inject HTTP doubles."""
    if type(execute) is not bool:
        raise ValueError("Execution requires an explicit boolean")
    result = {"mode": "live" if execute else "preflight", "ready": False, "blockers": [],
              "status": "not_run", "profile": dict(PROFILE), "attempts": {}, "diagnostics": {},
              "cost_status": "not_run"}
    approvals = options.approvals
    if not approvals.gateway_risk_accepted:
        result["blockers"].append("gateway_uncertainty_not_accepted")
    if not approvals.spending_confirmed:
        result["blockers"].append("approved_spend_controls_not_confirmed")
    if any(rate is None for rate in (approvals.mesh_input_rate, approvals.mesh_output_rate, approvals.brave_request_rate)):
        result["blockers"].append("approved_account_prices_required")
    # Do not access credential configuration before safety gates pass.
    if result["blockers"]:
        return result
    try:
        mesh_settings = MeshSettings.from_environment(environment, policy=policy(15, 7, 32_768), max_output_tokens=1024)
        brave_settings = BraveSearchSettings.from_environment(environment, policy=policy(10, 2, 16_384))
    except Exception:
        result["blockers"].append("provider_credentials_missing_or_invalid")
        return result
    result["ready"] = True
    if not execute:
        return result

    # Persist audit before constructing providers or starting any request.
    run_id = uuid4().hex
    directory = (output_root if output_root is not None else Path.cwd() / "reports" / "live") / run_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    sink = JsonlAuditSink(directory / "audit.jsonl")
    observers: dict[str, ObservedTransport] = {}
    try:
        write_record(directory / "manifest.json", {"run_id": run_id, "mode": "live", "model": options.model,
                     "profile": PROFILE, "rates_are_operator_attested_not_verified": True,
                     "approved_rates": {"mesh_input_per_million": str(approvals.mesh_input_rate),
                                        "mesh_output_per_million": str(approvals.mesh_output_rate),
                                        "brave_per_request": str(approvals.brave_request_rate)}})
        for name, supplied in (("mesh", mesh_transport), ("brave", brave_transport), ("source", source_transport)):
            observers[name] = ObservedTransport(supplied if supplied is not None else PublicHTTPTransport(),
                                               model=options.model if name == "mesh" else None)
        limits = AgentLimits(max_tasks=1, max_claims=2, max_question_chars=160, max_text_chars=160,
                             discovery_results_per_task=1, max_model_calls=7, max_discovery_calls=2,
                             max_fetch_calls=2, max_document_bytes=256_000, chunks_per_document=1,
                             max_prompt_chars=20_000, max_response_chars=16_000, output_tokens=1024)
        workflow = build_research_workflow(
            mesh=MeshAPIClient(mesh_settings, observers["mesh"]), model=options.model,
            discovery=BraveSearchDiscovery(brave_settings, observers["brave"]),
            retriever=HTTPWebRetriever(RetrievalSettings(policy=policy(10, 4, 256_000), max_redirects=1), observers["source"]),
            audit=sink, limits=limits, policy=WorkflowPolicy(max_research_retries=1, max_tasks=1, max_claims=2),
            extractor=BoundedContentExtractor(ExtractionSettings(max_input_bytes=256_000, max_pages=3)))
        async with asyncio.timeout(120):
            report = await workflow.run(ResearchQuestion(run_id, options.question), run_id=run_id)
        write_record(directory / "report.json", {"mode": "live", "report": asdict(report)})
        result.update(status="completed", report_status=report.status.value)
    except Exception as error:
        # Agent boundaries sanitize errors; never print even injected exception text.
        result["status"] = "failed"
        details = http_failure(error)
        result["failure_category"] = ("agent_contract_failure" if isinstance(error, AgentOutputError) else
                                      "timeout" if isinstance(error, TimeoutError) else
                                      details["category"] if details else "workflow_or_storage_failure")
        if details is not None:
            result["failure_details"] = details
            for observer in observers.values():
                if observer.failure is not None and observer.failure["category"] == "request_cancelled":
                    observer.failure = {key: value if value is not None else observer.failure.get(key)
                                        for key, value in details.items()}
    except asyncio.CancelledError:
        result["status"] = "interrupted"
        raise
    finally:
        result.update(run_id=run_id, attempts={key: value.attempts for key, value in observers.items()},
                      diagnostics={key: value.failure for key, value in observers.items() if value.failure is not None})
        result["cost_status"] = "unknown_may_be_billable" if any(result["attempts"].values()) else "not_run"
        try:
            write_record(directory / "outcome.json", result)
        finally:
            sink.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--execute", action="store_true", help="Explicitly execute paid real-provider research")
    parser.add_argument("--accept-gateway-uncertainty", action="store_true")
    parser.add_argument("--confirm-spend-controls", action="store_true")
    parser.add_argument("--mesh-input-usd-per-million", type=_rate)
    parser.add_argument("--mesh-output-usd-per-million", type=_rate)
    parser.add_argument("--brave-usd-per-request", type=_rate)
    args = parser.parse_args()
    try:
        approvals = TestOptions(gateway_risk_accepted=args.accept_gateway_uncertainty,
            spending_confirmed=args.confirm_spend_controls, mesh_input_rate=args.mesh_input_usd_per_million,
            mesh_output_rate=args.mesh_output_usd_per_million, brave_request_rate=args.brave_usd_per_request)
        result = asyncio.run(run_live(LiveOptions(args.question, args.model, approvals), execute=args.execute))
    except KeyboardInterrupt:
        print('{"status":"interrupted","cost_status":"unknown_may_be_billable"}')
        return 130
    except Exception:
        print('{"status":"configuration_or_storage_failure","cost_status":"unknown_may_be_billable"}')
        return 1
    print(json.dumps(result, sort_keys=True))
    if not result["ready"]:
        return 2
    if not args.execute:
        return 0
    if result["status"] != "completed":
        return 1
    return 0 if result["report_status"] == "verified" else 3


if __name__ == "__main__":
    raise SystemExit(main())
