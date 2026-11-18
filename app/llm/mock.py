"""Deterministic scripted provider for tests and offline evaluation (ADR-040).

``ScriptedProvider`` returns whatever its ``script`` function returns for a request: a fixed
string, a faithful answer built from the prompt, or a deliberately adversarial one (fabricated
citations, another patient's source, an altered value). No network, no randomness, no model.
Token counts are a whitespace word count, labelled as an estimate by the evaluation.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.llm.provider import LlmRequest, LlmResponse


@dataclass
class ScriptedProvider:
    script: Callable[[LlmRequest], str]
    model_name: str = "scripted-mock"
    calls: int = 0

    @property
    def name(self) -> str:
        return "mock"

    @property
    def model(self) -> str:
        return self.model_name

    @property
    def is_local(self) -> bool:
        return True

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        text = self.script(request)
        return LlmResponse(
            text=text,
            model=self.model_name,
            input_tokens=len(request.system.split()) + len(request.user.split()),
            output_tokens=len(text.split()),
            latency_ms=0,
        )
