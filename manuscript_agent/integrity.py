"""Fabrication check: quantitative claims must not appear from nowhere.

The no-fabrication rule in the author prompt is an instruction; this module is the check.
It compares the revised manuscript against the version that was reviewed and reports every
number and citation key that is newly asserted and cannot be traced to the previous text.

Deliberately mechanical. It does not judge whether a value is plausible — only whether the
author had it before the reviewers asked for it.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Set

# A numeric literal: 5, -3.2, 1,024, 0.81, 12%, 1.5e-3
NUMBER = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?%?")

# LaTeX / Markdown citation keys
CITE_KEYS = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}")
BIB_KEYS = re.compile(r"@\w+\{([^,}\s]+)")

# Numbers that are structure, not evidence: "Section 3", "Fig. 2", "Table 4b", "Eq. (7)"
STRUCTURAL_CUE = re.compile(
    r"(?i)\b(?:section|sec|subsection|figure|fig|table|tab|equation|eq|algorithm|alg|"
    r"appendix|chapter|line|lines|step|listing|item|page|footnote|column|row|panel|"
    r"ref|label|autoref|cref|eqref|pageref|part)\b\.?\s*[~(\[]?\s*$"
)
# Markdown/LaTeX numbering at the start of a line: "## 3.2 Results", "\section{3}"
LINE_NUMBERING = re.compile(r"^\s*(?:#{1,6}\s*|\\\w+\{?\s*|[-*+]\s+|\(?\d+[.)]\s)")
URL = re.compile(r"https?://\S+|doi:\S+|arXiv:\S+", re.IGNORECASE)


@dataclass
class Violation:
    kind: str  # "number" | "citation"
    value: str
    line: str
    note: str = ""

    def render(self) -> str:
        note = f" — {self.note}" if self.note else ""
        return f"- **{self.value}** ({self.kind}){note}\n  > {self.line.strip()[:200]}"


@dataclass
class IntegrityReport:
    violations: List[Violation]

    def __bool__(self) -> bool:
        return bool(self.violations)

    @property
    def values(self) -> List[str]:
        return [v.value for v in self.violations]

    def render(self) -> str:
        if not self.violations:
            return "No unsourced values introduced in this revision.\n"
        lines = [
            "The following appear in the revised manuscript but cannot be traced to the "
            "version that was reviewed:",
            "",
        ]
        lines += [v.render() for v in self.violations]
        return "\n".join(lines) + "\n"


def _normalize(token: str) -> str:
    return token.replace(",", "").rstrip("%").lstrip("+")


def _numbers_in(text: str) -> Set[str]:
    return {_normalize(m.group(0)) for m in NUMBER.finditer(text)}


def _is_structural(line: str, start: int) -> bool:
    """True if this numeric occurrence is document structure rather than a claim."""
    prefix = line[:start]
    if STRUCTURAL_CUE.search(prefix):
        return True
    if not prefix.strip() or LINE_NUMBERING.match(line) and start <= len(prefix.rstrip()) + 1:
        # a heading number or list marker at the head of the line
        return not prefix.strip() or prefix.strip().startswith(("#", "\\", "-", "*"))
    for m in URL.finditer(line):
        if m.start() <= start < m.end():
            return True
    return False


def _rounding_note(value: str, old_numbers: Set[str]) -> str:
    """A new value that is a rounding of an existing one is still new, but worth labelling."""
    for old in old_numbers:
        if old != value and (old.startswith(value) or value.startswith(old)):
            return f"looks like a re-rounding of {old} in the previous version"
    return ""


def added_lines(old: str, new: str) -> List[str]:
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(), n=0, lineterm="")
    return [ln[1:] for ln in diff if ln.startswith("+") and not ln.startswith("+++")]


def check(
    old: str,
    new: str,
    ignore_below: int = 0,
    known_citations: Optional[Iterable[str]] = None,
) -> IntegrityReport:
    """Report values asserted in `new` that have no antecedent in `old`.

    `ignore_below` suppresses bare integers strictly below this magnitude (counts like
    "3 datasets" churn constantly); set it to 0 to see everything. `known_citations` are
    keys the authors already hold — a package's .bib entries — which are therefore citable
    without being a new claim.
    """
    old_numbers = _numbers_in(old)
    old_cites = set(_flatten(CITE_KEYS.findall(old))) | set(BIB_KEYS.findall(old))
    old_cites |= set(known_citations or ())

    violations: List[Violation] = []
    seen: Set[str] = set()

    for line in added_lines(old, new):
        for m in NUMBER.finditer(line):
            value = _normalize(m.group(0))
            if value in old_numbers or value in seen:
                continue
            if _is_structural(line, m.start()):
                continue
            if ignore_below and _is_small_integer(value, ignore_below):
                continue
            seen.add(value)
            violations.append(
                Violation("number", m.group(0), line, _rounding_note(value, old_numbers))
            )
        for key in _flatten(CITE_KEYS.findall(line)):
            if key and key not in old_cites and key not in seen:
                seen.add(key)
                violations.append(
                    Violation("citation", key, line, "citation key not present before revision")
                )
    return IntegrityReport(violations)


def _flatten(groups) -> List[str]:
    out: List[str] = []
    for g in groups:
        out.extend(k.strip() for k in g.split(","))
    return out


def _is_small_integer(value: str, threshold: int) -> bool:
    try:
        n = float(value)
    except ValueError:
        return False
    return n == int(n) and abs(n) < threshold
