"""Provider-neutral request/response types and the ``LlmProvider`` protocol (ADR-011, ADR-040).

Every adapter takes an ``LlmRequest`` (a system prompt, a user prompt, the JSON schema the answer
must follow) and returns an ``LlmResponse`` (raw text plus measured usage). Parsing and validating
the text against the schema is the caller's job (``app.answering.generate``), so a provider can
never make an invalid answer look valid.

Prompts carry patient evidence: they are never logged here. Errors carry a fixed ``kind`` and the
adapter's exception class name, never the provider's message (it can echo the prompt).
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.core.errors import MediMindError


class LlmError(MediMindError):
    """A provider call failed. ``kind`` is a stable code; the message never contains prompts.

    Kinds: ``not_configured``, ``external_not_allowed``, ``not_synthetic``, ``unreachable``,
    ``timeout``, ``http_error``, ``bad_response``, ``rate_limited``.
    """

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class LlmRequest:
    system: str = field(repr=False)
    user: str = field(repr=False)
    json_schema: dict[str, Any] = field(repr=False)
    schema_name: str = "answer"
    max_output_tokens: int = 800
    temperature: float = 0.0


@dataclass(frozen=True)
class LlmResponse:
    text: str = field(repr=False)
    model: str
    input_tokens: int | None  # None when the provider does not report usage
    output_tokens: int | None
    latency_ms: int


@runtime_checkable
class LlmProvider(Protocol):
    """One configured model. ``is_local`` is True only when no data leaves this machine."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def is_local(self) -> bool: ...

    def complete(self, request: LlmRequest) -> LlmResponse: ...
