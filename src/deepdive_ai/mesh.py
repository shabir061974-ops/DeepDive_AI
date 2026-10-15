"""Single boundary for future LLM calls. No network transport in Phase 1."""

import os
from dataclasses import dataclass, field
from typing import Mapping, Protocol

from .models import nonempty


@dataclass(frozen=True)
class MeshSettings:
    api_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise ValueError("MESH_API_KEY must be configured")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "MeshSettings":
        values = os.environ if environment is None else environment
        return cls(api_key=values.get("MESH_API_KEY", ""))


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

        Provider URL, wire format, timeouts and transport await official Mesh specs.
        Implementations must not log credentials or prompt/response bodies.
        """
        ...


class DisabledMeshClient:
    async def complete(self, request: MeshRequest) -> MeshResponse:
        raise RuntimeError("Mesh transport is disabled in Phase 1")
