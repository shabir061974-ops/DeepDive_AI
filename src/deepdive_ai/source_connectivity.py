"""One fixed public-source diagnostic; offline preflight unless --execute is given."""

import argparse
import asyncio
import json
from dataclasses import replace
from urllib.parse import urlsplit

from .http_transport import (
    BoundedHTTPClient, FailureCategory, HTTPError, HTTPRequest, HTTPResponse,
    HTTPTransport, PublicHTTPTransport, RequestPolicy, TransportProgress,
    TransportStage, status_category, validate_url,
)


SOURCE_URL = "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/404"
TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 256_000


class _RecordingProgress(TransportProgress):
    """Record fixed stage enums only, preserving the bounded client's snapshot."""

    def __init__(self, parent: TransportProgress | None):
        super().__init__()
        self.parent = parent
        self.reached: set[TransportStage] = set()

    def update(self, stage: TransportStage, status: int | None = None) -> None:
        super().update(stage, status)
        with self._lock:
            self.reached.add(stage)
        if self.parent is not None:
            self.parent.update(stage, status)

    def stages(self) -> dict[str, bool]:
        with self._lock:
            return {stage.value: stage in self.reached for stage in TransportStage}


class _DiagnosticTransport:
    def __init__(self, transport: HTTPTransport):
        self.transport = transport
        self.attempts = 0
        self.progress = _RecordingProgress(None)

    async def send(self, request: HTTPRequest) -> HTTPResponse:
        self.attempts += 1
        self.progress = _RecordingProgress(request.progress)
        return await self.transport.send(replace(request, progress=self.progress))


async def run_source_diagnostic(*, execute: bool = False,
                                transport: HTTPTransport | None = None) -> dict:
    """No credentials, providers, custom destinations, redirects or research workflow."""
    if type(execute) is not bool:
        raise ValueError("Execution requires an explicit boolean")
    url = validate_url(SOURCE_URL)
    result = {"mode": "execute" if execute else "preflight", "status": "ready",
              "source_hostname": urlsplit(url).hostname, "attempts": 0,
              "max_requests": 1, "max_retries": 0, "timeout_seconds": TIMEOUT_SECONDS,
              "max_response_bytes": MAX_RESPONSE_BYTES, "http_status": None,
              "failure_category": None, "failure_stage": None,
              "stages_reached": {stage.value: False for stage in TransportStage}}
    if not execute:
        return result
    observed = _DiagnosticTransport(transport if transport is not None else PublicHTTPTransport())
    client = BoundedHTTPClient(observed, RequestPolicy(timeout_seconds=TIMEOUT_SECONDS,
        max_retries=0, max_requests=1, max_concurrency=1, max_response_bytes=MAX_RESPONSE_BYTES))
    try:
        response = await client.exchange("GET", url, (
            ("Accept-Encoding", "identity"), ("Accept", "text/html"),
            ("User-Agent", "DeepDive-AI/0.1 (source connectivity diagnostic)")))
        if type(response.status) is not int or not 100 <= response.status <= 599:
            raise HTTPError("Invalid HTTP status", category=FailureCategory.PROTOCOL)
        result["http_status"] = response.status
        result["status"] = "passed" if response.status == 200 else "failed"
        if response.status != 200:
            result["failure_category"] = status_category(response.status).value
            stage = observed.progress.snapshot()[0]
            result["failure_stage"] = stage.value if stage else None
    except HTTPError as error:
        result.update(status="failed", failure_category=error.category.value,
                      failure_stage=error.stage.value if error.stage else None,
                      http_status=error.http_status)
        if error.category == FailureCategory.UNSAFE_DESTINATION:
            result.pop("source_hostname", None)
    except Exception:
        # Never serialize exception text, request data or upstream response data.
        result.update(status="failed", failure_category=FailureCategory.INTERNAL.value)
    result["attempts"] = observed.attempts
    result["stages_reached"] = observed.progress.stages()
    return result


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse normally echoes unknown arguments, which could contain a URL
        # or a mistakenly supplied credential. Never repeat those values.
        self.exit(2, "Invalid arguments. Only --execute and --help are supported.\n")


def main() -> int:
    parser = _SafeParser(prog="python -m deepdive_ai.source_connectivity", description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Authorize one public-source HTTP attempt")
    args = parser.parse_args()
    try:
        result = asyncio.run(run_source_diagnostic(execute=args.execute))
    except KeyboardInterrupt:
        print('{"status":"interrupted","attempts":null}')
        return 130
    except Exception:
        print('{"status":"failed","failure_category":"internal_error"}')
        return 1
    print(json.dumps(result))
    return 0 if result["status"] in {"ready", "passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
