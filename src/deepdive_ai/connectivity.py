"""Explicit, bounded adapter smoke test. Default invocation never uses the network."""

import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from .http_transport import HTTPRequest, HTTPResponse, HTTPTransport, PublicHTTPTransport, RequestPolicy
from .mesh import MeshAPIClient, MeshRequest, MeshSettings
from .retrieval import SearchRequest
from .search import BraveSearchDiscovery, BraveSearchSettings


MODEL = "google/gemini-2.5-flash"


@dataclass(frozen=True)
class TestOptions:
    routing_confirmed: bool = False
    mesh_version: str | None = None
    mesh_input_rate: Decimal | None = None
    mesh_output_rate: Decimal | None = None
    brave_request_rate: Decimal | None = None

    def __post_init__(self) -> None:
        if type(self.routing_confirmed) is not bool:
            raise ValueError("Invalid routing confirmation")
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
        self.attempts = 0

    async def send(self, request: HTTPRequest) -> HTTPResponse:
        if self.attempts:
            raise RuntimeError("Single-attempt budget exhausted")
        self.attempts += 1
        response = await self._transport.send(request)
        self._response = response
        return response

    @property
    def status(self) -> int | None:
        return self._response.status if self._response is not None else None

    def mesh_metadata(self, version: str) -> tuple[bool, dict[str, int] | None]:
        response = self._response
        if response is None:
            return False, None
        # Called only after MeshAPIClient has validated the JSON and completion.
        data = json.loads(response.body.decode("utf-8"))
        compatible = (
            data.get("model") == MODEL
            and response.header("X-Mesh-Routing-Attempts") == "1"
            and response.header("X-Mesh-Routing-Fallback") == "false"
            and response.header("X-Mesh-Version") == version
        )
        usage = data.get("usage")
        names = ("prompt_tokens", "completion_tokens", "total_tokens")
        if not isinstance(usage, dict) or any(
            type(usage.get(name)) is not int or not 0 <= usage[name] <= 1_000_000_000 for name in names
        ):
            return compatible, None
        if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
            return compatible, None
        return compatible, {name: usage[name] for name in names}


def _result() -> dict:
    return {"status": "not_run", "http_status": None, "attempts": 0,
            "response_compatible": None, "token_usage": None,
            "estimated_cost_usd": None, "cost_status": "not_run"}


async def run_connectivity(
    options: TestOptions, *, execute: bool = False, environment: Mapping[str, str] | None = None,
    brave_transport: HTTPTransport | None = None, mesh_transport: HTTPTransport | None = None,
) -> dict:
    """Preflight by default. Callers must explicitly opt in to any external action."""
    values = os.environ if environment is None else environment
    report = {"mode": "live" if execute else "preflight", "ready": False, "blockers": [],
              "brave": _result(), "mesh": _result()}
    blockers = report["blockers"]
    try:
        mesh_settings = MeshSettings.from_environment(values, policy=_policy(15), max_output_tokens=64)
    except Exception:
        blockers.append("mesh_credential_missing_or_invalid")
    try:
        brave_settings = BraveSearchSettings.from_environment(values, policy=_policy(10))
    except Exception:
        blockers.append("brave_credential_missing_or_invalid")
    if not options.routing_confirmed:
        blockers.append("mesh_single_attempt_no_fallback_policy_not_confirmed")
    if options.mesh_version is None:
        blockers.append("approved_mesh_version_required")
    if any(rate is None for rate in (options.mesh_input_rate, options.mesh_output_rate, options.brave_request_rate)):
        blockers.append("approved_account_prices_required")
    report["ready"] = not blockers
    if blockers or not execute:
        return report

    brave = _Observation(brave_transport if brave_transport is not None else PublicHTTPTransport())
    mesh = _Observation(mesh_transport if mesh_transport is not None else PublicHTTPTransport())
    active = report["brave"]
    observer = brave
    try:
        async with asyncio.timeout(30):
            active.update(status="failed", cost_status="unknown_failure_may_be_billable")
            await BraveSearchDiscovery(brave_settings, brave).discover(SearchRequest("Python documentation", 1))
            active.update(status="passed", response_compatible=True,
                          estimated_cost_usd=format(options.brave_request_rate, "f"),
                          cost_status="nominal_account_rate_not_invoice")
            active.update(http_status=brave.status, attempts=brave.attempts)

            active = report["mesh"]
            observer = mesh
            active.update(status="failed", cost_status="unknown_failure_may_be_billable")
            response = await MeshAPIClient(mesh_settings, mesh).complete(MeshRequest(MODEL, "Reply exactly OK.", 64))
            compatible, usage = mesh.mesh_metadata(options.mesh_version)
            compatible = compatible and response.text.strip() == "OK"
            # A provider violating the requested output cap is not a passing test.
            compatible = compatible and (usage is None or usage["completion_tokens"] <= 64)
            active.update(response_compatible=compatible, token_usage=usage)
            if compatible:
                active["status"] = "passed"
                active["cost_status"] = "usage_unavailable" if usage is None else "estimate_not_invoice"
                if usage is not None:
                    cost = (usage["prompt_tokens"] * options.mesh_input_rate
                            + usage["completion_tokens"] * options.mesh_output_rate) / Decimal(1_000_000)
                    active["estimated_cost_usd"] = format(cost, "f")
    except Exception:
        # Never format exceptions: even injected transports may contain secrets.
        active["status"] = "failed"
        active["response_compatible"] = False
    finally:
        active.update(http_status=observer.status, attempts=observer.attempts)
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
    parser.add_argument("--confirm-routing", action="store_true",
                        help="Attest key policy: one upstream attempt, no fallback, gateway latency at most 10 seconds")
    parser.add_argument("--mesh-version", help="Approved effective key version, YYYY-MM")
    parser.add_argument("--mesh-input-usd-per-million", type=_rate)
    parser.add_argument("--mesh-output-usd-per-million", type=_rate)
    parser.add_argument("--brave-usd-per-request", type=_rate)
    args = parser.parse_args()
    try:
        options = TestOptions(args.confirm_routing, args.mesh_version, args.mesh_input_usd_per_million,
                              args.mesh_output_usd_per_million, args.brave_usd_per_request)
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
    return 0 if not args.execute or all(report[p]["status"] == "passed" for p in ("brave", "mesh")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
