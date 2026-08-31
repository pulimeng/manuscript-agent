"""Mechanical checks a candidate must pass before it is promoted to the next version.

None of these are judgements about the science. They are the things a copy editor or a
submission portal would catch, and they are checked automatically so no reviewer has to spend
a point on them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

COMMENT_TAIL = re.compile(r"(?<!\\)%.*$")
STALE_MARKERS = re.compile(
    r"(\[?\bTODO\b[^\]\n]*\]?|\bTBD\b|\bFIXME\b|\bXXX\b|\bplaceholder\b|lorem ipsum"
    r"|\\todo\{[^}]*\}|\?\?\?)",
    re.IGNORECASE,
)
# "Section 3" written as a literal instead of \ref — goes stale the moment sections move
HARDCODED_XREF = re.compile(
    r"(?<!\\)\b(Section|Sections|Figure|Figures|Table|Tables|Appendix)~?\s+(\d+(?:\.\d+)*)\b"
)
ESCAPED = re.compile(r"\\[$()\[\]]")
VERB = re.compile(r"\\verb\|[^|]*\||\\url\{[^}]*\}")
UNDEFINED_CITATION = re.compile(r"Citation [`'\"]([^'\"`]+)['\"`]")
UNDEFINED_REFERENCE = re.compile(r"Reference [`'\"]([^'\"`]+)['\"`]")

BLOCKING = "blocking"
WARNING = "warning"


@dataclass
class Finding:
    check: str
    severity: str
    message: str
    where: str = ""

    def render(self) -> str:
        loc = f" ({self.where})" if self.where else ""
        return f"- [{self.severity}] {self.check}: {self.message}{loc}"


@dataclass
class CheckReport:
    findings: List[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == BLOCKING]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == WARNING]

    def passed(self) -> bool:
        return not self.blocking

    def render(self) -> str:
        if not self.findings:
            return "All checks passed.\n"
        lines = [f.render() for f in self.findings]
        return "\n".join(lines) + "\n"

    def summary(self) -> str:
        if not self.findings:
            return "checks passed"
        return f"{len(self.blocking)} blocking, {len(self.warnings)} warning"


def _text_only_checks(report: "CheckReport", package) -> "CheckReport":
    for asset in package.missing_assets():
        report.findings.append(
            Finding("figures", BLOCKING, f"{asset} is referenced but not in the package")
        )
    _scan_sources(report, package)
    return report


def run_checks(version, package, page_limit: Optional[int] = None,
               enforce_pages: bool = False) -> CheckReport:
    """Check a freshly built version against the things a submission portal would reject.

    `version` is a versions.Version (already compiled); `package` is its Package view.
    """
    report = CheckReport()

    # Checks that depend on a build only apply when one was attempted: a Markdown
    # manuscript, or a run with --no-compile, has no PDF and that is not a defect.
    if not getattr(version, "build_attempted", True):
        return _text_only_checks(report, package)

    # 1. it has to build
    if version.pdf is None:
        report.findings.append(
            Finding("build", BLOCKING, "no PDF was produced from these sources")
        )
        for err in version.build_errors[:5]:
            report.findings.append(Finding("build", BLOCKING, err))
    elif not version.build_ok:
        report.findings.append(
            Finding("build", BLOCKING, f"compiled with {len(version.build_errors)} error(s)")
        )
        for err in version.build_errors[:8]:
            report.findings.append(Finding("build", BLOCKING, err))

    # 2. length
    if page_limit and version.pages:
        if version.pages > page_limit:
            report.findings.append(
                Finding(
                    "pages", BLOCKING if enforce_pages else WARNING,
                    f"{version.pages} pages in the PDF (references and appendices included) "
                    f"exceeds the {page_limit}-page limit set for this run",
                )
            )
        elif version.pages == page_limit:
            report.findings.append(
                Finding("pages", WARNING, f"exactly at the {page_limit}-page limit")
            )

    # 3. citations and cross-references that do not resolve
    for warning in version.build_warnings:
        key = UNDEFINED_CITATION.search(warning)
        if key:
            report.findings.append(
                Finding("citations", BLOCKING, f"undefined citation {key.group(1)}")
            )
            continue
        ref = UNDEFINED_REFERENCE.search(warning)
        if ref:
            report.findings.append(
                Finding("references", BLOCKING, f"undefined reference {ref.group(1)}")
            )

    # 4. figures the text points at but the package does not contain
    for asset in package.missing_assets():
        report.findings.append(
            Finding("figures", BLOCKING, f"{asset} is referenced but not in the package")
        )

    # 5. drafting scars and cross-references that will go stale
    _scan_sources(report, package)
    return report


def _math_balance(report: CheckReport, package) -> None:
    """An odd number of `$`, or mismatched \\( \\), in a file. TeX blames a later line."""
    for src in package.sources:
        rel = package.rel(src)
        dollars = 0
        opens = closes = 0
        first_odd_line = None
        for line_no, raw in enumerate(src.read_text(errors="replace").splitlines(), 1):
            line = COMMENT_TAIL.sub("", raw)
            line = VERB.sub("", line)
            line = ESCAPED.sub("", line)
            dollars += line.count("$")
            opens += line.count("\\(")
            closes += line.count("\\)")
            if dollars % 2 and first_odd_line is None:
                first_odd_line = line_no
            elif dollars % 2 == 0:
                first_odd_line = None
        if dollars % 2:
            report.findings.append(
                Finding("math", BLOCKING,
                        "unbalanced '$' — a math delimiter is not closed",
                        f"{rel}:{first_odd_line or '?'}")
            )
        if opens != closes:
            report.findings.append(
                Finding("math", BLOCKING,
                        f"{opens} '\\(' but {closes} '\\)'", rel)
            )


def _scan_sources(report: CheckReport, package) -> None:
    _math_balance(report, package)
    for src in package.sources:
        rel = package.rel(src)
        body = src.read_text(errors="replace")
        for line_no, line in enumerate(body.splitlines(), 1):
            if line.lstrip().startswith("%"):
                continue
            marker = STALE_MARKERS.search(line)
            if marker:
                report.findings.append(
                    Finding("stale-wording", BLOCKING,
                            f"unresolved marker {marker.group(0)[:40]!r}", f"{rel}:{line_no}")
                )
            xref = HARDCODED_XREF.search(line)
            if xref:
                report.findings.append(
                    Finding("stale-wording", WARNING,
                            f"hardcoded cross-reference {xref.group(0)!r} — use \\\\ref",
                            f"{rel}:{line_no}")
                )


LOCATED = re.compile(r"([\w./-]+\.(?:tex|bib|sty|cls|md)):(\d+)")


def error_context(package, report: "CheckReport", window: int = 4) -> str:
    """The offending source lines for every finding that names a file and line.

    TeX habitually reports an unbalanced delimiter several lines after the real one, so the
    author needs to see the text, not a line number.
    """
    seen = set()
    out = []
    for finding in report.blocking:
        for blob in (finding.where, finding.message):
            m = LOCATED.search(blob or "")
            if not m:
                continue
            rel, line_no = m.group(1), int(m.group(2))
            key = (rel, line_no)
            if key in seen:
                continue
            seen.add(key)
            path = next((s for s in package.sources if package.rel(s).endswith(rel.lstrip("./"))),
                        None)
            if path is None:
                continue
            lines = path.read_text(errors="replace").splitlines()
            lo, hi = max(0, line_no - window - 1), min(len(lines), line_no + window)
            body = "\n".join(
                f"{i + 1:>5}{'>' if i + 1 == line_no else ' '} {lines[i]}" for i in range(lo, hi)
            )
            out.append(f"--- {package.rel(path)} around line {line_no} ---\n{body}")
    return "\n\n".join(out)
