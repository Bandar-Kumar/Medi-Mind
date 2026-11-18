"""OpenAI-compatible Chat Completions adapter, standard library only (ADR-024, ADR-040).

Works with any server that implements ``POST <base_url>/chat/completions`` (for example a local
Ollama or llama.cpp server, or a hosted free tier). No SDK and no new dependency: ``urllib``.

Privacy guard (ARCHITECTURE §12): the base URL must point at this machine (loopback) unless
``allow_external`` is set explicitly. The service layer additionally refuses any patient not
marked ``is_synthetic`` for an external provider (ADR-005). The API key is a ``SecretStr`` and is
only ever placed in the ``Authorization`` header.

**Status:** written and unit-tested against a fake transport only. No live provider was reachable
when Phase 5 was built (no local model server, no credentials), so it has never been exercised
against a real model; see ADR-040.
"""

import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr

from app.llm.provider import LlmError, LlmRequest, LlmResponse

Transport = Callable[[urllib.request.Request, float], bytes]

_LOCAL_HOSTS = frozenset({"localhost"})


def is_loopback_url(base_url: str) -> bool:
    """True when the URL's host is this machine (``localhost`` or a loopback address)."""
    host = urllib.parse.urlsplit(base_url).hostname or ""
    if host in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _urlopen(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (checked URL)
        return response.read()


@dataclass
class OpenAICompatibleProvider:
    base_url: str
    model_name: str
    api_key: SecretStr | None = field(default=None, repr=False)
    allow_external: bool = False
    timeout_seconds: float = 60.0
    transport: Transport = field(default=_urlopen, repr=False)

    def __post_init__(self) -> None:
        scheme = urllib.parse.urlsplit(self.base_url).scheme
        if scheme not in ("http", "https"):
            raise LlmError("The LLM base URL must use http or https", kind="not_configured")
        if not self.model_name.strip():
            raise LlmError("No LLM model is configured", kind="not_configured")
        if not self.allow_external and not is_loopback_url(self.base_url):
            raise LlmError(
                "External LLM endpoints are disabled (MEDIMIND_LLM_ALLOW_EXTERNAL)",
                kind="external_not_allowed",
            )

    @property
    def name(self) -> str:
        return "openai_compatible"

    @property
    def model(self) -> str:
        return self.model_name

    @property
    def is_local(self) -> bool:
        return is_loopback_url(self.base_url)

    def _payload(self, request: LlmRequest) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "response_format": {"type": "json_object"},
        }

    def complete(self, request: LlmRequest) -> LlmResponse:
        headers = {"Content-Type": "application/json"}
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key.get_secret_value()}"
        http_request = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(self._payload(request)).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started = time.perf_counter()
        error: LlmError | None = None
        body = b""
        try:
            body = self.transport(http_request, self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            kind = "rate_limited" if exc.code == 429 else "http_error"
            error = LlmError(f"The LLM endpoint returned HTTP {exc.code}", kind=kind)
        except TimeoutError:
            error = LlmError("The LLM call timed out", kind="timeout")
        except (urllib.error.URLError, OSError) as exc:
            error = LlmError(
                f"The LLM endpoint is unreachable ({type(exc).__name__})", kind="unreachable"
            )
        if error is not None:
            raise error  # outside ``except``: no chained provider message (it may echo the prompt)
        latency = round((time.perf_counter() - started) * 1000)
        try:
            data = json.loads(body)
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
        except (ValueError, KeyError, IndexError, TypeError):
            text = None
        if not isinstance(text, str):
            raise LlmError("The LLM response has no message content", kind="bad_response")
        return LlmResponse(
            text=text,
            model=str(data.get("model") or self.model_name),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=latency,
        )
