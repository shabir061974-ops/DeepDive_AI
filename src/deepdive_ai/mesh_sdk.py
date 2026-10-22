"""Opt-in Mesh SDK compatibility diagnostic; the research pipeline never imports it.

Production research keeps using the bounded HTTP adapter in ``mesh.py``. This module
shows that the OpenAI-compatible Python client reaches the same Mesh endpoint and
that its responses pass the same routing, cache and model-identity classification.
Without ``--execute`` it makes no request. It reads ``MESH_API_KEY`` from the process
environment only, never from the private credentials file, and prints no secrets,
prompts or completions.
"""

import argparse
import json
import os
from typing import Callable, Mapping

from .http_transport import HTTPResponse
from .mesh import classify_mesh_response, mesh_identity_checks, mesh_routing_rejection_codes


MESH_SDK_BASE_URL = "https://api.meshapi.ai/v1"
DEFAULT_MODEL = "google/gemini-2.5-flash"
PROMPT = "Reply with the single word OK."
MAX_TOKENS = 16
TIMEOUT_SECONDS = 20
FINISH_REASONS = frozenset({"stop", "length", "content_filter", "tool_calls", "function_call"})


class MeshSDKError(RuntimeError):
    """Fixed safe message only; never include keys, responses or upstream errors."""


def mesh_api_key(environment: Mapping[str, str] | None = None) -> str:
    source = os.environ if environment is None else environment
    key = source.get("MESH_API_KEY", "")
    if (not isinstance(key, str) or not 1 <= len(key) <= 4096
            or any(not 33 <= ord(char) <= 126 for char in key)):
        raise MeshSDKError("MESH_API_KEY must be set in the environment")
    return key


def build_client(environment: Mapping[str, str] | None = None, *,
                 client_factory: Callable[..., object] | None = None):
    """Construct a client bound to the approved Mesh endpoint; sends nothing."""
    key = mesh_api_key(environment)
    if client_factory is None:
        try:
            from openai import OpenAI
        except ImportError:
            raise MeshSDKError("Install the optional SDK with: pip install -e '.[mesh-sdk]'") from None
        client_factory = OpenAI
    # Explicit values keep OPENAI_* environment settings (endpoint, organization,
    # project) from redirecting the request or adding identifiers to it.
    return client_factory(api_key=key, base_url=MESH_SDK_BASE_URL, max_retries=0,
                          timeout=TIMEOUT_SECONDS, organization="", project="")


def _token_count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


def run_diagnostic(model: str = DEFAULT_MODEL, *, execute: bool = False,
                   environment: Mapping[str, str] | None = None,
                   client_factory: Callable[..., object] | None = None) -> dict:
    if (not isinstance(model, str) or not 1 <= len(model) <= 120 or not model.isascii()
            or any(not (char.isalnum() or char in "/-._") for char in model)):
        raise ValueError("Invalid model identifier")
    result = {"mode": "sdk_diagnostic" if execute else "preflight", "base_url": MESH_SDK_BASE_URL,
              "model": model, "max_tokens": MAX_TOKENS, "ready": False, "status": "not_run"}
    try:
        mesh_api_key(environment)
    except MeshSDKError:
        result["blockers"] = ["mesh_api_key_missing_or_invalid"]
        return result
    result["ready"] = True
    if not execute:
        return result
    try:
        client = build_client(environment, client_factory=client_factory)
        raw = client.chat.completions.with_raw_response.create(
            model=model, messages=[{"role": "user", "content": PROMPT}],
            max_tokens=MAX_TOKENS, stream=False)
        headers = raw.headers
        pairs = headers.multi_items() if hasattr(headers, "multi_items") else headers.items()
        response = HTTPResponse(status=raw.status_code, headers=tuple((str(k), str(v)) for k, v in pairs))
        completion = raw.parse()
    except MeshSDKError as error:
        result.update(status="failed", failure=str(error))
        return result
    except Exception:
        result.update(status="failed", failure="sdk_request_failed")
        return result
    classification = classify_mesh_response(response)
    checks = mesh_identity_checks(response, model, getattr(completion, "model", None))
    usage = getattr(completion, "usage", None)
    choices = getattr(completion, "choices", None)
    finish = getattr(choices[0], "finish_reason", None) if isinstance(choices, list) and choices else None
    result.update(
        status="completed" if all(checks.values()) else "rejected",
        http_status=response.status, classification=classification.classification.value,
        routing_rejection_codes=list(mesh_routing_rejection_codes(response)), checks=checks,
        finish_reason=finish if finish in FINISH_REASONS else "other" if finish is not None else "missing",
        prompt_tokens=_token_count(getattr(usage, "prompt_tokens", None)),
        completion_tokens=_token_count(getattr(usage, "completion_tokens", None)))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--execute", action="store_true",
                        help="Send one small billable request through the OpenAI-compatible client")
    args = parser.parse_args()
    try:
        result = run_diagnostic(args.model, execute=args.execute)
    except ValueError:
        print('{"status":"invalid_arguments"}')
        return 1
    print(json.dumps(result, sort_keys=True))
    if not result["ready"]:
        return 2
    return 0 if result["status"] in ("not_run", "completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
