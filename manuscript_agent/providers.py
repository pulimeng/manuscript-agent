"""Per-role model selection across Claude and OpenAI.

Every agent talks to an object with the same two methods — `text()` for long-form output and
`parse()` for a Pydantic schema — so which provider backs which role is a configuration
choice. Mixing them is the point: a review panel drawn from one model shares that model's
blind spots.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Type, TypeVar

from pydantic import BaseModel

from .llm import (
    Attachment,
    DEFAULT_MODEL,
    LLM as AnthropicLLM,
    RefusalError,
    TruncatedError,
)

T = TypeVar("T", bound=BaseModel)

CLAUDE = "claude"
OPENAI = "openai"

DEFAULT_OPENAI_MODEL = "gpt-5.1"

_CLAUDE_NAME = re.compile(r"^(claude|anthropic)", re.IGNORECASE)
_OPENAI_NAME = re.compile(r"^(gpt|o[1-9]|chatgpt|text-|davinci)", re.IGNORECASE)
# models that accept a reasoning effort on the Responses API
_OPENAI_REASONING = re.compile(r"^(o[1-9]|gpt-5)", re.IGNORECASE)

# Anthropic exposes five effort levels; OpenAI's reasoning effort tops out at "high".
_OPENAI_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high",
                  "max": "high"}


@dataclass(frozen=True)
class ModelSpec:
    """Which model plays a role. Written as `provider:model`, e.g. `openai:gpt-5.1`."""

    provider: str
    model: str
    effort: str = "high"

    @staticmethod
    def parse(text: str, effort: str = "high") -> "ModelSpec":
        raw = text.strip()
        if ":" in raw:
            provider, _, model = raw.partition(":")
            provider = provider.strip().lower()
            model = model.strip()
            if provider in ("anthropic", "claude"):
                provider = CLAUDE
            elif provider in ("openai", "oai"):
                provider = OPENAI
            else:
                raise ValueError(f"unknown provider {provider!r} in {text!r}")
            if not model:
                model = DEFAULT_MODEL if provider == CLAUDE else DEFAULT_OPENAI_MODEL
            return ModelSpec(provider, model, effort)

        if _OPENAI_NAME.match(raw):
            return ModelSpec(OPENAI, raw, effort)
        if _CLAUDE_NAME.match(raw):
            return ModelSpec(CLAUDE, raw, effort)
        raise ValueError(
            f"cannot infer a provider from {text!r}; write it as 'claude:{raw}' or "
            f"'openai:{raw}'"
        )

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


class OpenAILLM:
    """OpenAI Responses API, same surface as the Anthropic client."""

    def __init__(
        self,
        model: str = DEFAULT_OPENAI_MODEL,
        effort: str = "high",
        max_tokens: int = 64000,
        client=None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - depends on the install
                raise ImportError(
                    "the openai package is required to use an openai: model — "
                    "pip install openai"
                ) from exc
            client = OpenAI()
        self.client = client

    @property
    def label(self) -> str:
        return f"{OPENAI}:{self.model}"

    def _reasoning(self) -> dict:
        if _OPENAI_REASONING.match(self.model):
            return {"reasoning": {"effort": _OPENAI_EFFORT.get(self.effort, "high")}}
        return {}

    @staticmethod
    def _check(response, max_tokens: int) -> None:
        if getattr(response, "status", None) == "incomplete":
            detail = getattr(response, "incomplete_details", None)
            reason = getattr(detail, "reason", detail)
            if reason == "max_output_tokens":
                raise TruncatedError(
                    f"the model hit its {max_tokens}-token output limit before finishing. "
                    "Reasoning tokens share this budget — raise max_tokens, or lower --effort."
                )
            raise RuntimeError(f"OpenAI response incomplete: {reason}")

    @staticmethod
    def _input(prompt: str, documents: Optional[Sequence[Attachment]]):
        if not documents:
            return prompt
        content = [
            {
                "type": "input_file",
                "filename": doc.filename,
                "file_data": f"data:{doc.media_type};base64,{doc.b64}",
            }
            for doc in documents
        ]
        content.append({"type": "input_text", "text": prompt})
        return [{"role": "user", "content": content}]

    def text(
        self,
        system: str,
        prompt: str,
        max_tokens: Optional[int] = None,
        documents: Optional[Sequence[Attachment]] = None,
    ) -> str:
        response = self.client.responses.create(
            model=self.model,
            instructions=system,
            input=self._input(prompt, documents),
            max_output_tokens=max_tokens or self.max_tokens,
            **self._reasoning(),
        )
        self._check(response, max_tokens or self.max_tokens)
        return (response.output_text or "").strip()

    def parse(
        self,
        system: str,
        prompt: str,
        schema: Type[T],
        max_tokens: int = 32000,
        documents: Optional[Sequence[Attachment]] = None,
    ) -> T:
        response = self.client.responses.parse(
            model=self.model,
            instructions=system,
            input=self._input(prompt, documents),
            text_format=schema,
            max_output_tokens=max_tokens,
            **self._reasoning(),
        )
        self._check(response, max_tokens)
        parsed = response.output_parsed
        if parsed is None:
            raise RefusalError(
                f"{self.label} returned no parsed output (refusal or empty response)"
            )
        return parsed


def build(spec: ModelSpec, max_tokens: int = 64000):
    """Instantiate the client for a role."""
    if spec.provider == CLAUDE:
        return AnthropicLLM(model=spec.model, effort=spec.effort, max_tokens=max_tokens)
    if spec.provider == OPENAI:
        return OpenAILLM(model=spec.model, effort=spec.effort, max_tokens=max_tokens)
    raise ValueError(f"unknown provider {spec.provider!r}")


def cycle(specs: List[ModelSpec], count: int) -> List[ModelSpec]:
    """Spread a list of specs over `count` roles, repeating if fewer were given."""
    if not specs:
        raise ValueError("at least one model spec is required")
    return [specs[i % len(specs)] for i in range(count)]
