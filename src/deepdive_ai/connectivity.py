"""Explicit, bounded adapter smoke test. Default invocation never uses the network."""

import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from .http_transport import (
    FailureCategory, HTTPError, HTTPRequest, HTTPResponse, HTTPTransport,
    PublicHTTPTransport, RequestPolicy, status_category,
)
from .mesh import MeshAPIClient, MeshRequest, MeshSettings
from .retrieval import SearchRequest
from .search import BraveSearchDiscovery, BraveSearchSettings


MODEL = "google/gemini-2.5-flash"


@dataclass(frozen=True)
class TestOptions:
    gateway_risk_accepted: bool = False
    mesh_version: str | None = None
    mesh_input_rate: Decimal | None = None
    mesh_output_rate: Decimal | None = None
    brave_request_rate: Decimal | None = None
    spending_confirmed: bool = False
    brave_only: bool = False
    mesh_only: bool = False

    def __post_init__(self) -> None:
        if (type(self.gateway_risk_accepted) is not bool or type(self.spending_confirmed) is not bool
                or type(self.brave_only) is not bool or type(self.mesh_only) is not bool):
            raise ValueError("Invalid safety acknowledgement")
        if self.brave_only and self.mesh_only:
            raise ValueError("Select at most one isolated provider")
        if self.mesh_version is not None and (
            not isinstance(self.mesh_version, str)
            or re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", self.mesh_version) is None
        ):
            raise ValueError("Expected an approved YYYY-MM Mesh version")
        for rate in (self.mesh_input_rate, self.mesh_output_rate, self.brave_request_rate):
            if rate is not None and (not isinstance(rate, Decimal) or not rate.is_finite()
                                     or not 0 <= rate <= 1_000_000
                                     or not -12 <= rate.as_tuple().exponent <= 6):
                raise ValueError("Invalid account price")


def _policy(timeout: int) -> RequestPolicy:
    return RequestPolicy(timeout_seconds=timeout, max_retries=0, max_requests=1,
                         max_concurrency=1, max_request_bytes=2048, max_response_bytes=16_384)


class _Observation:
    """One exchange; raw records remain private and are never returned or logged."""

    def __init__(self, transport: HTTPTransport):
        self._transport = transport
        self._response: HTTPResponse | None = None
        self._failure_status: int | None = None
        self.attempts = 0

    async def send(self, request: HTTPRequest) -> HTTPResponse:
        if self.attempts:
            raise RuntimeError("Single-attempt budget exhausted")
        self.attempts += 1
        try:
            response = await self._transport.send(request)
        except HTTPError as error:
            self._failure_status = error.http_status
            raise
        self._response = response
        return response

    @property
    def status(self) -> int | None:
        return self._response.status if self._response is not None else self._failure_status

    def mesh_metadata(self, version: str | None) -> tuple[bool, dict[str, int] | None, dict]:
        response = self._response
        if response is None:
            return False, None, {}
        # Called only after MeshAPIClient has validated the JSON and completion.
        data = json.loads(response.body.decode("utf-8"))
        compatible = data.get("model") == MODEL
        version_matches = None if version is None else response.header("X-Mesh-Version") == version
        if version_matches is False:
            compatible = False
        raw_attempts = response.header("X-Mesh-Routing-Attempts")
        attempts = (int(raw_attempts) if raw_attempts is not None and len(raw_attempts) <= 6
                    and raw_attempts.isascii() and raw_attempts.isdecimal() and int(raw_attempts) > 0 else None)
        raw_fallback = response.header("X-Mesh-Routing-Fallback")
        fallback = {"true": True, "false": False}.get(raw_fallback)
        warnings = []
        if attempts is None:
            warnings.append("gateway_attempts_unverified")
        elif attempts > 1:
            warnings.append("gateway_multiple_attempts_reported")
        if fallback is None:
            warnings.append("gateway_fallback_unverified")
        elif fallback:
            warnings.append("gateway_fallback_reported")
        if version is None:
            warnings.append("mesh_version_not_checked")
        diagnostics = {"gateway_attempts_reported": attempts, "gateway_fallback_reported": fallback,
                       "version_matches": version_matches, "warnings": warnings,
                       "validation_checks": {"response_schema_compatible": True,
                                             "model_matches": data.get("model") == MODEL,
                                             "expected_version_matches": version_matches}}
        usage = data.get("usage")
        names = ("prompt_tokens", "completion_tokens", "total_tokens")
        if not isinstance(usage, dict) or any(
            type(usage.get(name)) is not int or not 0 <= usage[name] <= 1_000_000_000 for name in names
        ):
            return compatible, None, diagnostics
        if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
            return compatible, None, diagnostics
        return compatible, {name: usage[name] for name in names}, diagnostics


def _result() -> dict:
    return {"status": "not_run", "http_status": None, "attempts": 0,
            "response_compatible": None, "token_usage": None,
            "estimated_cost_usd": None, "cost_status": "not_run", "diagnostics": {},
            "failure_category": None, "failure_stage": None}


async def run_connectivity(
    options: TestOptions, *, execute: bool = False, environment: Mapping[str, str] | None = None,
    brave_transport: HTTPTransport | None = None, mesh_transport: HTTPTransport | None = None,
) -> dict:
    """Preflight by default. Callers must explicitly opt in to any external action."""
    values = os.environ if environment is None else environment
    scope = "mesh_only" if options.mesh_only else "brave_only" if options.brave_only else "both"
    report = {"mode": "live" if execute else "preflight", "scope": scope,
              "ready": False, "blockers": [],
              "brave": _result(), "mesh": _result()}
    blockers = report["blockers"]
    if not options.brave_only:
        try:
            mesh_settings = MeshSettings.from_environment(values, policy=_policy(15), max_output_tokens=64)
        except Exception:
            blockers.append("mesh_credential_missing_or_invalid")
    if not options.mesh_only:
        try:
            brave_settings = BraveSearchSettings.from_environment(values, policy=_policy(10))
        except Exception:
            blockers.append("brave_credential_missing_or_invalid")
    if not options.brave_only and not options.gateway_risk_accepted:
        blockers.append("gateway_uncertainty_not_accepted")
    if not options.spending_confirmed:
        blockers.append("approved_spend_controls_not_confirmed")
    required_rates = ((options.mesh_input_rate, options.mesh_output_rate) if options.mesh_only else
                      (options.brave_request_rate,) if options.brave_only else
                      (options.mesh_input_rate, options.mesh_output_rate, options.brave_request_rate))
    if any(rate is None for rate in required_rates):
        blockers.append("approved_account_prices_required")
    report["ready"] = not blockers
    if blockers or not execute:
        return report

    brave = None if options.mesh_only else _Observation(brave_transport if brave_transport is not None else PublicHTTPTransport())
    mesh = None if options.brave_only else _Observation(mesh_transport if mesh_transport is not None else PublicHTTPTransport())
    active = report["mesh"] if options.mesh_only else report["brave"]
    observer = mesh if options.mesh_only else brave
    try:
        async with asyncio.timeout(30):
            if not options.mesh_only:
                active.update(status="failed", cost_status="unknown_failure_may_be_billable")
                await BraveSearchDiscovery(brave_settings, brave).discover(SearchRequest("Python documentation", 1))
                active.update(status="passed", response_compatible=True,
                              estimated_cost_usd=format(options.brave_request_rate, "f"),
                              cost_status="nominal_account_rate_not_invoice")
                active.update(http_status=brave.status, attempts=brave.attempts)

                if options.brave_only:
                    return report

            active = report["mesh"]
            observer = mesh
            active.update(status="failed", cost_status="unknown_failure_may_be_billable")
            response = await MeshAPIClient(mesh_settings, mesh).complete(MeshRequest(MODEL, "Reply exactly OK.", 64))
            compatible, usage, diagnostics = mesh.mesh_metadata(options.mesh_version)
            # Evaluate every check, even after a mismatch. Only fixed names and
            # booleans leave this boundary; never retain the compared values.
            checks = diagnostics["validation_checks"]
            checks["reply_matches"] = response.text.strip() == "OK"
            checks["output_limit_satisfied"] = None if usage is None else usage["completion_tokens"] <= 64
            diagnostics["failed_checks"] = [name for name, passed in checks.items() if passed is False]
            compatible = compatible and checks["reply_matches"]
            # A provider violating the requested output cap is not a passing test.
            compatible = compatible and checks["output_limit_satisfied"] is not False
            active.update(response_compatible=compatible, token_usage=usage, diagnostics=diagnostics)
            if not compatible:
                active["failure_category"] = FailureCategory.VALIDATION.value
            if compatible:
                active["status"] = "passed"
                active["cost_status"] = "usage_unavailable" if usage is None else "response_usage_estimate_not_total_bill"
                if usage is not None:
                    cost = (usage["prompt_tokens"] * options.mesh_input_rate
                            + usage["completion_tokens"] * options.mesh_output_rate) / Decimal(1_000_000)
                    active["estimated_cost_usd"] = format(cost, "f")
    except Exception as error:
        # Never format exceptions: even injected transports may contain secrets.
        active["status"] = "failed"
        active["response_compatible"] = False
        category = (error.category if isinstance(error, HTTPError) else
                    FailureCategory.TIMEOUT if isinstance(error, TimeoutError) else FailureCategory.INTERNAL)
        if category == FailureCategory.PROTOCOL and observer.status is not None and observer.status != 200:
            category = status_category(observer.status)
        active["failure_category"] = category.value
        active["failure_stage"] = error.stage.value if isinstance(error, HTTPError) and error.stage is not None else None
        if isinstance(error, HTTPError):
            active["http_status"] = error.http_status
    finally:
        active.update(http_status=observer.status if observer.status is not None else active["http_status"],
                      attempts=observer.attempts)
    return report


def _rate(value: str) -> Decimal:
    try:
        if len(value) > 32:
            raise ValueError
        rate = Decimal(value)
        TestOptions(mesh_input_rate=rate)
        return rate
    except Exception:
        raise argparse.ArgumentTypeError("Expected a finite nonnegative account price with at most 12 decimal places") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Make up to one request per provider; may incur charges")
    providers = parser.add_mutually_exclusive_group()
    providers.add_argument("--brave-only", action="store_true", help="Test Brave only; never read Mesh credentials or invoke Mesh")
    providers.add_argument("--mesh-only", action="store_true", help="Test Mesh only; never read Brave credentials or invoke Brave")
    parser.add_argument("--accept-gateway-uncertainty", action="store_true",
                        help="Accept that client limits cannot bound gateway attempts, latency or billing")
    parser.add_argument("--confirm-spend-controls", action="store_true",
                        help="Attest account prices, approved spend exposure and provider spending controls were reviewed")
    parser.add_argument("--mesh-version", help="Optional expected effective version, YYYY-MM; validation only, not authentication")
    parser.add_argument("--mesh-input-usd-per-million", type=_rate)
    parser.add_argument("--mesh-output-usd-per-million", type=_rate)
    parser.add_argument("--brave-usd-per-request", type=_rate)
    args = parser.parse_args()
    try:
        options = TestOptions(args.accept_gateway_uncertainty, args.mesh_version, args.mesh_input_usd_per_million,
                              args.mesh_output_usd_per_million, args.brave_usd_per_request, args.confirm_spend_controls,
                              args.brave_only, args.mesh_only)
        report = asyncio.run(run_connectivity(options, execute=args.execute))
    except KeyboardInterrupt:
        print('{"status":"interrupted","cost_status":"unknown_may_be_billable"}')
        return 130
    except Exception:
        print('{"status":"configuration_or_runtime_failure"}')
        return 1
    print(json.dumps(report, sort_keys=True))
    if not report["ready"]:
        return 2
    providers = ("mesh",) if args.mesh_only else ("brave",) if args.brave_only else ("brave", "mesh")
    return 0 if not args.execute or all(report[p]["status"] == "passed" for p in providers) else 1


if __name__ == "__main__":
    raise SystemExit(main())
