"""Explicit bounded real-adapter research. Preflight is offline; no fixture path."""

import argparse
import asyncio
import json
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from .agents.common import AgentLimits, AgentOutputError
from .audit import AuditEvent, AuditKind
from .connectivity import TestOptions, _rate
from .extraction import BoundedContentExtractor, ExtractionSettings
from .http_transport import (
    FailureCategory, HTTPError, HTTPRequest, HTTPResponse, HTTPTransport,
    PublicHTTPTransport, RequestPolicy, status_category, validate_url,
)
from .mesh import (MeshAPIClient, MeshCompletionCheck, MeshError, MeshSettings, classify_mesh_response,
                   mesh_identity_checks, mesh_routing_rejection_codes)
from .models import ResearchQuestion, nonempty
from .persistence import JsonlAuditSink, write_record
from .retrieval import HTTPWebRetriever, RetrievalSettings
from .runtime import build_research_workflow
from .search import BraveSearchDiscovery, BraveSearchSettings
from .workflow import WorkflowPolicy


PROFILE = {"mesh_requests": 7, "brave_requests": 2, "source_requests": 4,
           "http_retries": 0, "research_retries": 1, "tasks": 1, "claims": 2,
           "output_tokens_per_mesh_request": 1024, "mesh_timeout_seconds": 25,
           "brave_timeout_seconds": 10, "source_timeout_seconds": 10,
           "overall_timeout_seconds": 120}


# Display policy only: never consult this set when accepting a response model.
_PUBLIC_MODEL_DISPLAY_IDS = frozenset({"google/gemini-2.5-flash", "gemini-2.5-flash"})


def _model_identity_diagnostics(data: dict) -> dict:
    present = "model" in data
    value = data.get("model")
    label = {type(None): "null", bool: "boolean", int: "number", float: "number",
             str: "string", list: "array", dict: "object"}.get(type(value), "unavailable")
    return {"model_field_present": present, "model_field_type": label if present else "missing",
            "public_model_identifier": value if type(value) is str and value in _PUBLIC_MODEL_DISPLAY_IDS else None}


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


# Runner checks on every Mesh 200 response, in evaluation order; the first that fails is
# recorded as `mesh_validation_failure`.
MESH_VALIDATION_CHECKS = ("response_json_valid", "root_object", "model_matches", "routing_metadata_valid",
                          "usage_object", "token_fields_valid", "token_total_matches", "output_limit_satisfied")
MESH_FINISH_REASONS = frozenset({"stop", "length", "content_filter", "tool_calls", "function_call"})
MAX_TOKEN_COUNT = 1_000_000_000


def _token_count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= MAX_TOKEN_COUNT else None


def _completion_metadata(data: dict) -> dict:
    """Fixed finish-reason label and bounded token counts only; never message content."""
    choices = data.get("choices")
    finish = (choices[0].get("finish_reason")
              if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details")
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else usage.get("reasoning_tokens")
    return {"finish_reason": "missing" if finish is None else finish if isinstance(finish, str)
            and finish in MESH_FINISH_REASONS else "other",
            "prompt_tokens": _token_count(usage.get("prompt_tokens")),
            "completion_tokens": _token_count(usage.get("completion_tokens")),
            "total_tokens": _token_count(usage.get("total_tokens")),
            "reasoning_tokens": _token_count(reasoning)}


# Cache/routing header diagnostics. Names are recorded only for x-mesh*, *cache* and age;
# values only when they are one of a few fixed words or a short number, and never for
# identifier-like names. Nothing else from a response is retained.
HEADER_NAME = re.compile(r"[a-z0-9-]{1,64}")
HEADER_VALUE_WORDS = frozenset({"true", "false", "hit", "miss", "bypass"})
HEADER_ID_TOKENS = frozenset({"id", "key", "token", "auth", "authorization", "cookie", "secret", "signature",
                              "session", "trace", "request", "user", "account", "org", "ip", "client"})
MAX_RECORDED_HEADERS = 24


def _recorded_header_name(name: object) -> str | None:
    if not isinstance(name, str):
        return None
    name = name.strip().lower()
    if not HEADER_NAME.fullmatch(name):
        return None
    return name if name.startswith("x-mesh") or "cache" in name or name == "age" else None


def _recorded_header_value(name: str, value: object) -> str | None:
    if set(name.split("-")) & HEADER_ID_TOKENS or not isinstance(value, str):
        return None
    value = value.strip().lower()
    if value in HEADER_VALUE_WORDS or (value.isascii() and value.isdecimal() and len(value) <= 10):
        return value
    return None


def _header_diagnostics(headers: object) -> list[dict]:
    recorded = []
    for item in headers if isinstance(headers, (list, tuple)) else ():
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            continue
        name = _recorded_header_name(item[0])
        if name is not None:
            recorded.append({"name": name, "value": _recorded_header_value(name, item[1])})
            if len(recorded) == MAX_RECORDED_HEADERS:
                break
    return recorded


class ObservedTransport:
    """Safe counters/diagnostics only. Does not retain requests, headers or bodies."""

    def __init__(self, transport: HTTPTransport, *, model: str | None = None,
                 source_diagnostics: bool = False):
        self.transport = transport
        self.model = model
        self.source_diagnostics = source_diagnostics
        self.attempts = 0
        self.failure: dict | None = None
        # Finish reason and token counts of the most recent Mesh 200 response, if parseable.
        self.last_completion: dict | None = None
        # Allowlisted cache/routing header names and values of the most recent Mesh response.
        self.last_headers: list[dict] | None = None

    def _record_failure(self, details: dict, hostname: str | None) -> None:
        self.failure = details
        if hostname is not None and details["category"] != FailureCategory.UNSAFE_DESTINATION.value:
            self.failure["source_hostname"] = hostname

    async def send(self, request: HTTPRequest) -> HTTPResponse:
        self.attempts += 1
        validation_checks = None
        model_diagnostics = None
        routing_codes = ()
        classification = None
        hostname = None
        if self.source_diagnostics:
            try:
                # Paths and even apparently ordinary query values may contain
                # secrets. Retain only the normalized hostname, never a URL.
                candidate = urlsplit(validate_url(request.url)).hostname
                if candidate and len(candidate) <= 253 and (
                        ":" in candidate or all(label and len(label) <= 63
                        and label[0].isalnum() and label[-1].isalnum() for label in candidate.split("."))):
                    hostname = candidate
            except (HTTPError, ValueError, UnicodeError):
                pass
        try:
            response = await self.transport.send(request)
            if self.model is not None:
                self.last_headers = _header_diagnostics(response.headers)
            if response.status >= 400:
                self._record_failure({"category": status_category(response.status).value,
                                      "stage": None, "http_status": response.status}, hostname)
            if self.model is not None and response.status == 200:
                self.last_completion = None
                classification = classify_mesh_response(response).classification
                routing_codes = mesh_routing_rejection_codes(response)
                # The Mesh client independently validates strict JSON/completion shape.
                # This runner additionally requires the selected model and bounded usage.
                validation_checks = dict.fromkeys(MESH_VALIDATION_CHECKS, False)
                model_diagnostics = {"model_field_present": False, "model_field_type": "unavailable",
                                     "public_model_identifier": None}
                try:
                    data = json.loads(response.body.decode("utf-8"))
                    validation_checks["response_json_valid"] = True
                    validation_checks["root_object"] = isinstance(data, dict)
                    if not isinstance(data, dict):
                        raise ValueError
                    self.last_completion = _completion_metadata(data)
                    model_diagnostics = _model_identity_diagnostics(data)
                    validation_checks.update(mesh_identity_checks(response, self.model, data.get("model")))
                    usage = data.get("usage")
                    validation_checks["usage_object"] = isinstance(usage, dict)
                    if not isinstance(usage, dict):
                        raise ValueError
                    names = ("prompt_tokens", "completion_tokens", "total_tokens")
                    valid_tokens = all(type(usage.get(n)) is int and 0 <= usage[n] <= 1_000_000_000 for n in names)
                    validation_checks["token_fields_valid"] = valid_tokens
                    if valid_tokens:
                        validation_checks["token_total_matches"] = (
                            usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"])
                        validation_checks["output_limit_satisfied"] = usage["completion_tokens"] <= 1024
                    if not all(validation_checks.values()):
                        raise ValueError
                except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                    raise HTTPError("Mesh model or usage incompatible", category=FailureCategory.VALIDATION,
                                    http_status=200) from None
            return response
        except HTTPError as error:
            self._record_failure({"category": error.category.value,
                                  "stage": error.stage.value if error.stage else None,
                                  "http_status": error.http_status}, hostname)
            if error.category == FailureCategory.VALIDATION and validation_checks is not None:
                self.failure["mesh_response_classification"] = classification
                self.failure["mesh_validation_checks"] = validation_checks
                self.failure["mesh_validation_failure"] = next(
                    (name for name in MESH_VALIDATION_CHECKS if not validation_checks[name]), None)
                self.failure.update(model_diagnostics)
                if routing_codes:
                    self.failure["routing_rejection_codes"] = list(routing_codes)
            raise
        except asyncio.CancelledError:
            # wait_for cancels the inner transport before raising TimeoutError
            # outside this observer. Preserve only its fixed progress snapshot.
            stage, status = request.progress.snapshot() if request.progress is not None else (None, None)
            self._record_failure({"category": "request_cancelled", "stage": stage.value if stage else None,
                                  "http_status": status}, hostname)
            raise


class _RoutingAuditSink:
    """Enrich the existing failure event; no extra events or raw metadata."""

    def __init__(self, sink: JsonlAuditSink, observers: Mapping[str, ObservedTransport]):
        self.sink = sink
        self.observers = observers

    def emit(self, event: AuditEvent) -> None:
        mesh = self.observers.get("mesh")
        if event.kind == AuditKind.FAILED and mesh is not None and mesh.failure:
            codes = tuple(mesh.failure.get("routing_rejection_codes", ()))
            classification = mesh.failure.get("mesh_response_classification")
            if codes or classification is not None:
                event = replace(event, routing_rejection_codes=codes,
                                mesh_response_classification=classification)
        self.sink.emit(event)


def http_failure(error: BaseException) -> dict | None:
    """Read typed diagnostics through sanitizing agent boundaries, never messages."""
    seen: set[int] = set()
    for _ in range(8):
        if id(error) in seen:
            break
        seen.add(id(error))
        if isinstance(error, HTTPError):
            details = {"category": error.category.value, "stage": error.stage.value if error.stage else None,
                       "http_status": error.http_status}
            if isinstance(error, MeshError) and isinstance(error.completion_check, MeshCompletionCheck):
                details["mesh_completion_check"] = error.completion_check.value
            return details
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
        mesh_settings = MeshSettings.from_environment(environment, policy=policy(PROFILE["mesh_timeout_seconds"], 7, 32_768), max_output_tokens=1024)
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
                                               model=options.model if name == "mesh" else None,
                                               source_diagnostics=name == "source")
        limits = AgentLimits(max_tasks=1, max_claims=2, max_question_chars=160, max_text_chars=160,
                             discovery_results_per_task=2, sources_per_task=1, max_model_calls=7, max_discovery_calls=2,
                             max_fetch_calls=2, max_document_bytes=256_000, chunks_per_document=1,
                             max_prompt_chars=20_000, max_response_chars=16_000, output_tokens=1024,
                             max_quote_chars=400)
        workflow = build_research_workflow(
            mesh=MeshAPIClient(mesh_settings, observers["mesh"]), model=options.model,
            discovery=BraveSearchDiscovery(brave_settings, observers["brave"]),
            retriever=HTTPWebRetriever(RetrievalSettings(policy=policy(10, 4, 256_000), max_redirects=1), observers["source"]),
            audit=_RoutingAuditSink(sink, observers), limits=limits, policy=WorkflowPolicy(max_research_retries=1, max_tasks=1, max_claims=2),
            extractor=BoundedContentExtractor(ExtractionSettings(max_input_bytes=256_000, max_pages=3)))
        async with asyncio.timeout(120):
            report = await workflow.run(ResearchQuestion(run_id, options.question), run_id=run_id)
        write_record(directory / "report.json", {"mode": "live", "report": asdict(report)})
        result.update(status="completed", report_status=report.status.value)
    except Exception as error:
        # Agent boundaries sanitize errors; never print even injected exception text.
        result["status"] = "failed"
        if isinstance(error, AgentOutputError):
            result["agent_failure_code"] = error.code.value
            if error.json_shape is not None:
                result["agent_json_shape"] = error.json_shape.value
            if error.location is not None:
                result["agent_failure_location"] = error.location.value
            if error.row_index is not None:
                result["agent_failure_row"] = error.row_index
            if error.value_shape is not None:
                result["agent_value_shape"] = error.value_shape.value
        details = http_failure(error)
        result["failure_category"] = ("agent_contract_failure" if isinstance(error, AgentOutputError) else
                                      "timeout" if isinstance(error, TimeoutError) else
                                      details["category"] if details else "workflow_or_storage_failure")
        mesh_observer = observers.get("mesh")
        if mesh_observer is not None and mesh_observer.last_completion is not None:
            result["mesh_last_completion"] = dict(mesh_observer.last_completion)
        if mesh_observer is not None and mesh_observer.last_headers is not None:
            result["mesh_last_headers"] = [dict(item) for item in mesh_observer.last_headers]
        if details is not None:
            result["failure_details"] = details
            for observer in observers.values():
                if observer.failure is not None and observer.failure["category"] == "request_cancelled":
                    observer.failure.update({key: value if value is not None else observer.failure.get(key)
                                             for key, value in details.items()})
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
