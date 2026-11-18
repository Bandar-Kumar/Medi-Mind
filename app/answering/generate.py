"""Answer generators behind one interface (Phase 5; ADR-040).

- ``ExtractiveGenerator``: the deterministic composer; no model (the default).
- ``LlmGenerator``: any ``LlmProvider``; the reply must be a JSON object that validates as
  ``DraftAnswer`` (Pydantic, ``extra="forbid"``). Anything else is ``generation_failed``; the text
  is never shown, repaired or logged.

Both return a ``Generation``; the citation validator then runs on either in exactly the same way.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from app.answering.compose import COMPOSER_VERSION, compose_answer
from app.answering.context import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    answer_json_schema,
    build_user_prompt,
)
from app.answering.model import DraftAnswer, EvidenceItem, RoutePlan
from app.llm.provider import LlmError, LlmProvider, LlmRequest


@dataclass(frozen=True)
class Generation:
    draft: DraftAnswer | None
    generator: str  # "extractive" or "llm:<provider>:<model>"
    version: str  # composer or prompt version
    error_kind: str | None = None  # generation_failed reason (fixed code)
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0


class AnswerGenerator(Protocol):
    @property
    def label(self) -> str: ...

    def generate(
        self,
        question: str,
        plan: RoutePlan,
        items: Sequence[EvidenceItem],
        *,
        missing: Sequence[str],
        terms: Sequence[str],
    ) -> Generation: ...


class ExtractiveGenerator:
    label = "extractive"

    def generate(
        self,
        question: str,
        plan: RoutePlan,
        items: Sequence[EvidenceItem],
        *,
        missing: Sequence[str],
        terms: Sequence[str],
    ) -> Generation:
        draft = compose_answer(plan, items, missing=missing, terms=terms)
        return Generation(draft, self.label, COMPOSER_VERSION)


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_draft(text: str) -> DraftAnswer | None:
    """The reply as a ``DraftAnswer``, or None. A Markdown code fence around the JSON is allowed."""
    match = _FENCE.match(text)
    body = match.group(1) if match else text
    try:
        return DraftAnswer.model_validate(json.loads(body))
    except (ValueError, ValidationError, TypeError):
        return None


class LlmGenerator:
    def __init__(self, provider: LlmProvider, *, max_output_tokens: int = 800) -> None:
        self.provider = provider
        self.max_output_tokens = max_output_tokens

    @property
    def label(self) -> str:
        return f"llm:{self.provider.name}:{self.provider.model}"

    def generate(
        self,
        question: str,
        plan: RoutePlan,
        items: Sequence[EvidenceItem],
        *,
        missing: Sequence[str],
        terms: Sequence[str],
    ) -> Generation:
        if not items:
            # Nothing to ground an answer in: no model call at all.
            return Generation(
                DraftAnswer(status="insufficient_evidence"), self.label, PROMPT_VERSION
            )
        request = LlmRequest(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(question, plan, items),
            json_schema=answer_json_schema(),
            max_output_tokens=self.max_output_tokens,
        )
        try:
            response = self.provider.complete(request)
        except LlmError as exc:
            return Generation(None, self.label, PROMPT_VERSION, error_kind=f"llm_{exc.kind}")
        draft = parse_draft(response.text)
        return Generation(
            draft,
            self.label,
            PROMPT_VERSION,
            error_kind=None if draft is not None else "invalid_structured_output",
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=response.latency_ms,
        )
