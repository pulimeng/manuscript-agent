"""The manuscript itself: a single text file the author agent edits in place."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*)\n```\s*$", re.DOTALL)

FORMATS = {
    ".tex": "LaTeX",
    ".md": "Markdown",
    ".markdown": "Markdown",
    ".txt": "plain text",
    ".rst": "reStructuredText",
}


@dataclass
class Manuscript:
    path: Path
    text: str

    @staticmethod
    def load(path: str | Path) -> "Manuscript":
        p = Path(path)
        try:
            return Manuscript(path=p, text=p.read_text())
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"{p} is not a text manuscript (it does not decode as UTF-8). "
                "A PDF can be reviewed with `manuscript-agent review`; to revise it, point "
                "the command at its sources."
            ) from exc

    @property
    def fmt(self) -> str:
        return FORMATS.get(self.path.suffix.lower(), "plain text")

    @property
    def root(self) -> Path:
        return self.path.parent

    @property
    def main(self) -> Path:
        return self.path

    @property
    def emit_instructions(self) -> str:
        return f"Emit the complete revised {self.fmt} file and nothing else."

    @property
    def known_citations(self) -> set:
        return set()

    def missing_assets(self) -> list:
        return []

    def save(self) -> None:
        self.path.write_text(self.text)

    def snapshot(self, directory: Path, label: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        out = directory / f"{label}{self.path.suffix}"
        out.write_text(self.text)
        return out

    def proposed_blocks(self, emitted: str) -> dict:
        """A one-file manuscript emits the whole file, so the proposal is that one file."""
        return {self.path.name: strip_fence(emitted)}

    def replace(self, new_text: str) -> str:
        """Swap in a new full text, returning a unified diff against the old one."""
        old = self.text
        self.text = strip_fence(new_text)
        return diff(old, self.text, self.path.name)


def strip_fence(text: str) -> str:
    """Models like to wrap a whole document in a code fence. Undo that."""
    text = text.strip()
    m = FENCE.match(text)
    return (m.group(1) if m else text).strip() + "\n"


def diff(old: str, new: str, name: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            n=2,
        )
    )
