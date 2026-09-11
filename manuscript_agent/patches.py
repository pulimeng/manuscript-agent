"""Diffs between manuscript versions.

Round 2's reviewers are shown exactly what changed since the version they reviewed, as a
plain `git apply`-compatible unified diff.
"""

from __future__ import annotations

import difflib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set

from .package import SOURCE_SUFFIXES


@dataclass
class Patch:
    """A proposed change from `base` to `candidate`, as a unified diff."""

    base_vid: str
    base_source_hash: str
    text: str
    files: List[str] = field(default_factory=list)
    added: int = 0
    removed: int = 0

    def __bool__(self) -> bool:
        return bool(self.text.strip())

    def header(self) -> str:
        return (
            f"# Patch against {self.base_vid} (source sha256:{self.base_source_hash[:16]})\n"
            f"# {len(self.files)} file(s), +{self.added} -{self.removed}\n"
            f"# files: {', '.join(self.files) or '(none)'}\n"
        )

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.header() + self.text)
        return path

    def applies_to(self, root: Path) -> bool:
        """True if `git apply --check` accepts this patch against `root`."""
        if not self.text.strip():
            return True
        proc = subprocess.run(
            ["git", "apply", "--check", "-p1", "-"],
            input=self.text, text=True, errors="replace", cwd=root, capture_output=True,
        )
        return proc.returncode == 0



def _text_files(root: Path) -> Set[str]:
    return {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SOURCE_SUFFIXES
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
    }


def _read(path: Path) -> List[str]:
    return path.read_text(errors="replace").splitlines(keepends=True)


def tree_patch(base: Path, candidate: Path, base_vid: str, base_hash: str) -> Patch:
    """Unified diff of every source file that differs between two trees."""
    names = sorted(_text_files(base) | _text_files(candidate))
    chunks: List[str] = []
    changed: List[str] = []
    added = removed = 0

    for name in names:
        old_path, new_path = base / name, candidate / name
        old = _read(old_path) if old_path.exists() else []
        new = _read(new_path) if new_path.exists() else []
        if old == new:
            continue

        body = list(
            difflib.unified_diff(
                old, new,
                fromfile=f"a/{name}" if old else "/dev/null",
                tofile=f"b/{name}" if new else "/dev/null",
                n=3,
            )
        )
        if not body:
            continue
        head = [f"diff --git a/{name} b/{name}\n"]
        if not old:
            head.append("new file mode 100644\n")
        elif not new:
            head.append("deleted file mode 100644\n")
        chunks.extend(head + body)
        if body and not body[-1].endswith("\n"):
            chunks.append("\n")
        changed.append(name)
        added += sum(1 for ln in body if ln.startswith("+") and not ln.startswith("+++"))
        removed += sum(1 for ln in body if ln.startswith("-") and not ln.startswith("---"))

    return Patch(base_vid, base_hash, "".join(chunks), changed, added, removed)
