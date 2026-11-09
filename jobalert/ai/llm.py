"""Thin wrapper around the Anthropic SDK for one-shot structured (JSON) calls."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic

log = logging.getLogger(__name__)


class TransientAIError(RuntimeError):
    """Network / rate limit / server error: leave the job pending and retry next run."""


@dataclass
class LLMResult:
    data: dict[str, Any] | None          # parsed JSON, None if refused or unparseable
    refused: bool = False
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""


class LLM:
    def __init__(self, api_key: str) -> None:
        # The SDK already retries 429/5xx/connection errors with backoff.
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=180)

    def structured(
        self,
        *,
        model: str,
        system_blocks: list[str],
        user_text: str,
        schema: dict[str, Any],
        effort: str,
        max_tokens: int = 8000,
        use_fallback: bool = False,
    ) -> LLMResult:
        """One request with a JSON-schema constrained answer.

        The last system block gets a cache breakpoint, so instructions + profile
        (identical on every call) are billed at the cache-read rate after the first call.
        """
        system = [{"type": "text", "text": text} for text in system_blocks]
        system[-1]["cache_control"] = {"type": "ephemeral"}
        params: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_text}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        try:
            if use_fallback:
                # Server-side fallback: if the model declines (e.g. a cyber-security job
                # description trips a classifier), the API retries on a recommended model.
                response = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params
                )
            else:
                response = self.client.messages.create(**params)
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as exc:
            raise TransientAIError(f"{type(exc).__name__}: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise TransientAIError(f"HTTP {exc.status_code}: {exc.message}") from exc
            raise

        usage = {
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens,
            "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        }
        if response.stop_reason == "refusal":
            log.warning("%s declined (%s)", model, getattr(response.stop_details, "category", None))
            return LLMResult(None, refused=True, usage=usage, model=response.model)
        if response.stop_reason == "max_tokens":
            log.warning("%s hit max_tokens", model)
            return LLMResult(None, usage=usage, model=response.model)
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("%s returned invalid JSON", model)
            return LLMResult(None, usage=usage, model=response.model)
        return LLMResult(data, usage=usage, model=response.model)
