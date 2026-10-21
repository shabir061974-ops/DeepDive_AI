"""Exclusive application LLM boundary, using Mesh's configured HTTPS origin."""

import asyncio
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Awaitable, Callable, Mapping, Protocol

from .http_transport import (
    BoundedHTTPClient, FailureCategory, HTTPError, HTTPResponse, HTTPTransport, PublicHTTPTransport,
    RequestLimitExceeded, RequestPolicy, positive_int,
)

from .models import nonempty
from .configuration import provider_key
from .audit import MeshResponseClassification, RoutingRejectionCode


@dataclass(frozen=True)
class MeshSettings:
    api_key: str = field(repr=False)
    base_url: str = "https://api.meshapi.ai/v1"
    max_output_tokens: int = 4096
    policy: RequestPolicy = field(default_factory=RequestPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise ValueError("MESH_API_KEY must be configured")
        if any(ord(c) <= 32 or ord(c) >= 127 for c in self.api_key):
            raise ValueError("MESH_API_KEY contains invalid header characters")
        if self.base_url not in ("https://api.meshapi.ai/v1", "https://api.meshapi.ai/v1/"):
            raise ValueError("Mesh base URL must use the approved Mesh HTTPS origin and v1 path")
        positive_int(self.max_output_tokens, "max_output_tokens")
        if not isinstance(self.policy, RequestPolicy):
            raise ValueError("Mesh policy must be a RequestPolicy")

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None, *,
        base_url: str = "https://api.meshapi.ai/v1", max_output_tokens: int = 4096,
        policy: RequestPolicy = RequestPolicy(),
    ) -> "MeshSettings":
        return cls(api_key=provider_key("MESH_API_KEY", environment), base_url=base_url,
                   max_output_tokens=max_output_tokens, policy=policy)


@dataclass(frozen=True)
class MeshRequest:
    model: str
    prompt: str = field(repr=False)
    max_output_tokens: int = 1024

    def __post_init__(self) -> None:
        nonempty(self.model, "model")
        nonempty(self.prompt, "prompt")
        if type(self.max_output_tokens) is not int or self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")


@dataclass(frozen=True)
class MeshResponse:
    text: str = field(repr=False)
    request_id: str

    def __post_init__(self) -> None:
        nonempty(self.text, "response text")
        nonempty(self.request_id, "request id")


class MeshClient(Protocol):
    async def complete(self, request: MeshRequest) -> MeshResponse:
        """All future application LLM calls must pass through this interface.

        Application agents must never instantiate another provider's LLM client.
        Implementations must not log credentials or prompt/response bodies.
        """
        ...


class DisabledMeshClient:
    async def complete(self, request: MeshRequest) -> MeshResponse:
        raise RuntimeError("Mesh transport is disabled in Phase 1")


class MeshCompletionCheck(StrEnum):
    """Fixed name of the completion check a Mesh response failed; never a raw value."""

    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    ENVELOPE_INVALID = "envelope_invalid"
    CHOICES_INVALID = "choices_invalid"
    FINISH_REASON_LENGTH = "finish_reason_length"
    FINISH_REASON_CONTENT_FILTER = "finish_reason_content_filter"
    FINISH_REASON_TOOL_CALLS = "finish_reason_tool_calls"
    FINISH_REASON_OTHER = "finish_reason_other"
    FINISH_REASON_MISSING = "finish_reason_missing"
    MESSAGE_INVALID = "message_invalid"
    TOOL_CALLS_PRESENT = "tool_calls_present"
    CONTENT_NOT_TEXT = "content_not_text"
    CONTENT_EMPTY = "content_empty"


def _finish_reason_check(value: object) -> MeshCompletionCheck:
    if value is None:
        return MeshCompletionCheck.FINISH_REASON_MISSING
    return {"length": MeshCompletionCheck.FINISH_REASON_LENGTH,
            "content_filter": MeshCompletionCheck.FINISH_REASON_CONTENT_FILTER,
            "tool_calls": MeshCompletionCheck.FINISH_REASON_TOOL_CALLS,
            "function_call": MeshCompletionCheck.FINISH_REASON_TOOL_CALLS,
            }.get(value, MeshCompletionCheck.FINISH_REASON_OTHER) if isinstance(value, str) \
        else MeshCompletionCheck.FINISH_REASON_OTHER


class MeshError(HTTPError):
    """Sanitized Mesh failure; upstream bodies and credentials are never echoed."""

    def __init__(self, message: str = "", *, completion_check: MeshCompletionCheck | None = None, **kwargs):
        super().__init__(message, **kwargs)
        self.completion_check = completion_check if isinstance(completion_check, MeshCompletionCheck) else None


class MeshIdentityError(MeshError):
    """Completion schema passed, but model/routing acceptance failed."""

    default_category = FailureCategory.VALIDATION


@dataclass(frozen=True)
class MeshResponseClassificationResult:
    classification: MeshResponseClassification
    rejection_codes: tuple[RoutingRejectionCode, ...] = ()


def classify_mesh_response(response: HTTPResponse) -> MeshResponseClassificationResult:
    """Classify headers without retaining values or establishing routing trust.

    Support clarification supplied October 20, 2026: explicit cache hits may
    omit routing headers. They remain unverified, never accepted as no-fallback.
    This non-streaming client does not accept server-sent event responses.
    """
    def values(name: str) -> list:
        return [value for key, value in response.headers
                if isinstance(key, str) and key.lower() == name]

    cache = values("x-cache")
    content_types = values("content-type")
    codes = []
    if cache and (len(cache) != 1 or type(cache[0]) is not str or cache[0] not in ("HIT", "MISS")):
        codes.append(RoutingRejectionCode.INVALID_CACHE_METADATA)
    if len(content_types) > 1 or any(not (type(value) is str and value.isascii()
            and 0 < len(value) <= 120 and all(32 <= ord(char) <= 126 for char in value))
            for value in content_types):
        codes.append(RoutingRejectionCode.INVALID_METADATA_VALUE)
    streaming = any(type(value) is str and value.split(";", 1)[0].strip().lower() == "text/event-stream"
                    for value in content_types)
    hit = len(cache) == 1 and type(cache[0]) is str and cache[0] == "HIT"
    routing_present = any(values(name) for name in (
        "x-mesh-routing-model", "x-mesh-routing-fallback", "x-mesh-routing-attempts"))
    if hit:
        codes.append(RoutingRejectionCode.CACHED_RESPONSE_UNVERIFIED)
        if routing_present or streaming:
            codes.append(RoutingRejectionCode.CONTRADICTORY_METADATA)
    if streaming:
        codes.append(RoutingRejectionCode.UNEXPECTED_STREAMING)
    invalid = any(code in codes for code in (
        RoutingRejectionCode.INVALID_CACHE_METADATA, RoutingRejectionCode.INVALID_METADATA_VALUE,
        RoutingRejectionCode.CONTRADICTORY_METADATA))
    classification = (MeshResponseClassification.INVALID if invalid else
                      MeshResponseClassification.STREAMING if streaming else
                      MeshResponseClassification.CACHED if hit else
                      MeshResponseClassification.NORMAL if values("x-mesh-routing-fallback") else
                      MeshResponseClassification.UNKNOWN)
    return MeshResponseClassificationResult(classification, tuple(codes))


def mesh_identity_checks(response: HTTPResponse, requested: str, returned: object) -> dict[str, bool]:
    """Support-confirmed non-streaming policy; never return raw header values.

    Only the exact Gemini pair is equivalent, starting at API version 2026-08.
    A routing-model header signals another model, even if its text looks familiar.
    Missing fallback metadata cannot establish that no backup provider answered.
    """
    names = ("x-mesh-version", "x-mesh-routing-model", "x-mesh-routing-fallback", "x-mesh-routing-attempts")
    values = {name: [] for name in names}
    for key, value in response.headers:
        if isinstance(key, str) and key.lower() in values:
            values[key.lower()].append(value)
    valid = all(len(items) <= 1 and all(type(v) is str and v.isascii() and len(v) <= 120
                and all(33 <= ord(c) <= 126 for c in v) for v in items) for items in values.values())

    def one(name: str) -> str | None:
        items = values[name]
        return items[0] if len(items) == 1 and type(items[0]) is str else None

    version = one("x-mesh-version")
    version_valid = (version is not None and re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", version) is not None
                     and int(version[:4]) > 0)
    if values["x-mesh-version"] and not version_valid:
        valid = False
    attempts = one("x-mesh-routing-attempts")
    if values["x-mesh-routing-attempts"] and (attempts is None or not attempts.isascii()
            or not attempts.isdecimal() or len(attempts) > 6 or int(attempts) < 1):
        valid = False
    valid = (valid and not values["x-mesh-routing-model"] and one("x-mesh-routing-fallback") == "false"
             and not classify_mesh_response(response).rejection_codes)
    exact = type(returned) is str and returned == requested
    pair = frozenset({"google/gemini-2.5-flash", "gemini-2.5-flash"})
    normalized = (type(returned) is str and requested in pair and returned in pair
                  and version_valid and version >= "2026-08" and len(values["x-mesh-version"]) == 1)
    return {"model_matches": bool(exact or normalized), "routing_metadata_valid": bool(valid)}


def mesh_routing_rejection_codes(response: HTTPResponse) -> tuple[RoutingRejectionCode, ...]:
    """Diagnostics only. Do not use these codes to make acceptance decisions."""
    names = ("x-mesh-version", "x-mesh-routing-model", "x-mesh-routing-fallback", "x-mesh-routing-attempts")
    values = {name: [] for name in names}
    for key, value in response.headers:
        if isinstance(key, str) and key.lower() in values:
            values[key.lower()].append(value)

    def one(name: str) -> str | None:
        items = values[name]
        return items[0] if len(items) == 1 and type(items[0]) is str else None

    found = set(classify_mesh_response(response).rejection_codes)
    if any(len(items) > 1 for items in values.values()):
        found.add(RoutingRejectionCode.DUPLICATE_METADATA)
    if any(not (type(v) is str and v.isascii() and len(v) <= 120
                and all(33 <= ord(c) <= 126 for c in v)) for items in values.values() for v in items):
        found.add(RoutingRejectionCode.INVALID_METADATA_VALUE)
    fallback = values["x-mesh-routing-fallback"]
    if not fallback:
        found.add(RoutingRejectionCode.FALLBACK_MISSING)
    else:
        if any(type(v) is str and v == "true" for v in fallback):
            found.add(RoutingRejectionCode.FALLBACK_TRUE)
        if one("x-mesh-routing-fallback") not in ("true", "false"):
            found.add(RoutingRejectionCode.FALLBACK_INVALID)
    if values["x-mesh-routing-model"]:
        found.add(RoutingRejectionCode.ROUTING_MODEL_PRESENT)
    version = one("x-mesh-version")
    if values["x-mesh-version"] and not (version is not None
            and re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", version) is not None and int(version[:4]) > 0):
        found.add(RoutingRejectionCode.INVALID_VERSION)
    attempts = one("x-mesh-routing-attempts")
    if values["x-mesh-routing-attempts"] and (attempts is None or not attempts.isascii()
            or not attempts.isdecimal() or len(attempts) > 6 or int(attempts) < 1):
        found.add(RoutingRejectionCode.INVALID_ATTEMPTS)
    return tuple(code for code in RoutingRejectionCode if code in found)


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("invalid JSON constant")


class MeshAPIClient:
    """Non-streaming chat-completions adapter; protocol compatibility is unverified.

    Only api.meshapi.ai is allowed, and redirects are never followed. No fallback
    provider exists. No requests occur at import, construction or application startup.
    """

    def __init__(self, settings: MeshSettings, transport: HTTPTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._settings = settings
        self._http = BoundedHTTPClient(transport if transport is not None else PublicHTTPTransport(),
                                       settings.policy, sleep)

    @classmethod
    def from_environment(cls) -> "MeshAPIClient":
        return cls(MeshSettings.from_environment())

    async def complete(self, request: MeshRequest) -> MeshResponse:
        if not isinstance(request, MeshRequest):
            raise ValueError("request must be a MeshRequest")
        if request.max_output_tokens > self._settings.max_output_tokens:
            raise MeshError("Requested output token limit exceeds Mesh configuration")
        if len(request.prompt) + len(request.model) > self._settings.policy.max_request_bytes:
            raise RequestLimitExceeded("Mesh request exceeds byte limit")
        try:
            payload = json.dumps({
                "model": request.model,
                "messages": [{"role": "user", "content": request.prompt}],
                "max_tokens": request.max_output_tokens,
                "stream": False,
            }, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except UnicodeError:
            raise MeshError("Mesh request contains invalid Unicode") from None
        response = await self._http.exchange(
            "POST", self._settings.base_url.rstrip("/") + "/chat/completions",
            (("Authorization", "Bearer " + self._settings.api_key),
             ("Content-Type", "application/json"), ("Accept", "application/json"),
             ("Accept-Encoding", "identity")), payload)
        if response.status != 200:
            raise MeshError(f"Mesh request failed with HTTP status {response.status}", http_status=response.status)
        if classify_mesh_response(response).rejection_codes:
            raise MeshError("Mesh response classification rejected", category=FailureCategory.VALIDATION,
                            http_status=200)
        media_type = (response.header("content-type") or "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise MeshError("Mesh returned an unsupported response type", http_status=200,
                            completion_check=MeshCompletionCheck.UNSUPPORTED_CONTENT_TYPE)
        # Every check below must pass, exactly as before. `check` only names the first
        # one that failed, as a fixed code; no response value is retained.
        check = MeshCompletionCheck.ENVELOPE_INVALID
        try:
            data = json.loads(response.body.decode("utf-8"), object_pairs_hook=_json_object,
                              parse_constant=_invalid_constant)
            if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"].strip():
                raise ValueError
            check = MeshCompletionCheck.CHOICES_INVALID
            choices = data.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ValueError
            choice = choices[0]
            message = choice.get("message")
            if choice.get("finish_reason") != "stop":
                check = _finish_reason_check(choice.get("finish_reason"))
                raise ValueError
            check = MeshCompletionCheck.MESSAGE_INVALID
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError
            check = MeshCompletionCheck.TOOL_CALLS_PRESENT
            if message.get("tool_calls"):
                raise ValueError
            check = MeshCompletionCheck.CONTENT_NOT_TEXT
            if not isinstance(message.get("content"), str):
                raise ValueError
            check = MeshCompletionCheck.CONTENT_EMPTY
            completion = MeshResponse(message["content"], data["id"])
            if not all(mesh_identity_checks(response, request.model, data.get("model")).values()):
                raise MeshIdentityError("Mesh model or routing metadata rejected", http_status=200)
            return completion
        except (ValueError, TypeError, KeyError, RecursionError):
            raise MeshError("Mesh returned a malformed or incomplete completion", http_status=200,
                            completion_check=check) from None
