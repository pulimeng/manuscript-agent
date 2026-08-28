"""Thin wrapper over the Anthropic SDK shared by every agent."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Type, TypeVar

import anthropic
from anthropic.lib._parse._transform import transform_schema
from pydantic import BaseModel, TypeAdapter

DEFAULT_MODEL = "claude-opus-5"

T = TypeVar("T", bound=BaseModel)


@dataclass
class Attachment:
    """A file sent to the model as-is — the compiled PDF the reviewers actually read."""

    filename: str
    data: bytes
    media_type: str = "application/pdf"

    @staticmethod
    def from_path(path: str | Path, media_type: str = "application/pdf") -> "Attachment":
        p = Path(path)
        return Attachment(p.name, p.read_bytes(), media_type)

    @property
    def b64(self) -> str:
        return base64.standard_b64encode(self.data).decode("utf-8")

    @property
    def size_mb(self) -> float:
        return len(self.data) / 1_000_000


class RefusalError(RuntimeError):
    """The model declined the request (stop_reason == 'refusal')."""


class TruncatedError(RuntimeError):
    """The model ran out of output budget mid-answer, so the result is unusable."""


def _text_of(message) -> str:
    return "\n".join(b.text for b in message.content if b.type == "text").strip()


def _check(message, max_tokens: int) -> None:
    if message.stop_reason == "refusal":
        detail = getattr(message, "stop_details", None)
        category = getattr(detail, "category", None)
        raise RefusalError(f"model refused the request (category={category})")
    if message.stop_reason == "max_tokens":
        raise TruncatedError(
            f"the model hit its {max_tokens}-token output limit before finishing. "
            "Thinking tokens share this budget, so a long document can exhaust it — "
            "raise max_tokens, or lower --effort."
        )


class LLM:
    """One client, reused across agents. Thread-safe, so reviewers can run in parallel."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: str = "high",
        max_tokens: int = 64000,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.client = client or anthropic.Anthropic()

    @property
    def label(self) -> str:
        return f"claude:{self.model}"

    def _content(self, prompt: str, documents: Optional[Sequence[Attachment]]):
        if not documents:
            return prompt
        blocks: List[dict] = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": doc.media_type,
                    "data": doc.b64,
                },
                "title": doc.filename,
            }
            for doc in documents
        ]
        blocks.append({"type": "text", "text": prompt})
        return blocks

    def text(
        self,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
        documents: Optional[Sequence[Attachment]] = None,
    ) -> str:
        """Long-form generation (drafts, revisions, response letters). Streamed."""
        with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            messages=[{"role": "user", "content": self._content(prompt, documents)}],
        ) as stream:
            message = stream.get_final_message()
        _check(message, max_tokens or self.max_tokens)
        return _text_of(message)

    def parse(
        self,
        system: str,
        prompt: str,
        schema: Type[T],
        max_tokens: int = 32000,
        documents: Optional[Sequence[Attachment]] = None,
    ) -> T:
        """Structured generation (reviews, decisions, revision plans).

        Streamed rather than using `messages.parse`, for two reasons: a large output budget
        needs streaming to stay under the HTTP timeout, and streaming lets us read
        `stop_reason` before parsing — a truncated answer becomes a clear TruncatedError
        instead of a JSON syntax error from half a review.
        """
        fmt = {"type": "json_schema", "schema": transform_schema(TypeAdapter(schema).json_schema())}
        with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort, "format": fmt},
            messages=[{"role": "user", "content": self._content(prompt, documents)}],
        ) as stream:
            message = stream.get_final_message()
        _check(message, max_tokens)
        text = _text_of(message)
        try:
            return schema.model_validate(json.loads(text))
        except json.JSONDecodeError as exc:
            raise TruncatedError(
                f"the model returned malformed JSON ({exc}); "
                f"stop_reason was {message.stop_reason}"
            ) from exc
