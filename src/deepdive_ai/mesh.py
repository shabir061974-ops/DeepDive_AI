"""Exclusive application LLM boundary, using Mesh's configured HTTPS origin."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping, Protocol

from .http_transport import (
    BoundedHTTPClient, HTTPError, HTTPTransport, PublicHTTPTransport,
    RequestLimitExceeded, RequestPolicy, positive_int,
)

from .models import nonempty
from .configuration import provider_key


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


class MeshError(HTTPError):
    """Sanitized Mesh failure; upstream bodies and credentials are never echoed."""


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
            raise MeshError(f"Mesh request failed with HTTP status {response.status}")
        media_type = (response.header("content-type") or "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise MeshError("Mesh returned an unsupported response type")
        try:
            data = json.loads(response.body.decode("utf-8"), object_pairs_hook=_json_object,
                              parse_constant=_invalid_constant)
            if not isinstance(data, dict) or not isinstance(data.get("id"), str):
                raise ValueError
            choices = data.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ValueError
            choice = choices[0]
            message = choice.get("message")
            if (choice.get("finish_reason") != "stop" or not isinstance(message, dict)
                    or message.get("role") != "assistant" or message.get("tool_calls")
                    or not isinstance(message.get("content"), str)):
                raise ValueError
            return MeshResponse(message["content"], data["id"])
        except (ValueError, TypeError, KeyError, RecursionError):
            raise MeshError("Mesh returned a malformed or incomplete completion") from None
