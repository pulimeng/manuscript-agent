"""Compile the source package into the PDF that actually gets submitted.

Reviewers read the PDF, not your sources — so the build is part of the submission, and a
revision that breaks the build has not been made. A failed compile is a finding, handed back
to the author with the log the same way an unsourced number is.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

BUILD_DIR = ".manuscript-build"

# `file:line: message` and the plain `! TeX error` form
TEX_ERROR = re.compile(r"^(?:.*?:\d+:.*|! .*)$", re.MULTILINE)
UNDEFINED = re.compile(
    r"^(?:LaTeX|Package \w+) Warning: (?:Citation|Reference) .*$", re.MULTILINE
)


class BuildError(RuntimeError):
    pass


@dataclass
class BuildResult:
    ok: bool
    pdf: Optional[Path]
    log: str
    errors: List[str]
    warnings: List[str]

    def summary(self, limit: int = 25) -> str:
        if self.ok and not self.warnings:
            return "Compiled cleanly."
        lines: List[str] = []
        if self.errors:
            lines.append("Errors:")
            lines += [f"  {e}" for e in self.errors[:limit]]
        if self.warnings:
            lines.append("Unresolved references or citations:")
            lines += [f"  {w}" for w in self.warnings[:limit]]
        return "\n".join(lines) or "Build failed with no parsable error; see the full log."


def _dedupe(items) -> List[str]:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def available() -> bool:
    return shutil.which("latexmk") is not None or shutil.which("pdflatex") is not None


def compile_pdf(root: Path, main: Path, engine: str = "pdflatex",
                timeout: int = 300) -> BuildResult:
    """Build `main` inside `root`. Aux files stay in a build directory, out of the package."""
    root, main = Path(root).resolve(), Path(main).resolve()
    outdir = root / BUILD_DIR
    outdir.mkdir(exist_ok=True)

    if shutil.which("latexmk"):
        cmd = ["latexmk", f"-{engine}", "-bibtex", "-interaction=nonstopmode",
               "-file-line-error", f"-outdir={BUILD_DIR}", str(main.relative_to(root))]
    elif shutil.which(engine):
        cmd = [engine, "-interaction=nonstopmode", "-file-line-error",
               f"-output-directory={BUILD_DIR}", str(main.relative_to(root))]
    else:
        raise BuildError(
            "no LaTeX toolchain found (looked for latexmk and pdflatex) — install TeX Live "
            "or MacTeX, or run with --no-compile to review the sources instead"
        )

    # bibtex and graphics search run from the build directory; point them back at the package
    env = dict(os.environ)
    for var in ("BIBINPUTS", "TEXINPUTS", "BSTINPUTS"):
        env[var] = f"{root}:{env.get(var, '')}"

    try:
        proc = subprocess.run(
            cmd, cwd=root, capture_output=True, text=True, errors="replace",
            timeout=timeout, env=env,
        )
        output = proc.stdout + proc.stderr
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        return BuildResult(False, None, f"compile timed out after {timeout}s", 
                           [f"compile timed out after {timeout}s"], [])

    log_file = outdir / (main.stem + ".log")
    log = log_file.read_text(errors="replace") if log_file.exists() else output
    pdf = outdir / (main.stem + ".pdf")

    errors = _dedupe(m.strip() for m in TEX_ERROR.findall(log) if m.strip())
    warnings = sorted({m.strip() for m in UNDEFINED.findall(log)})
    # latexmk exits non-zero for unresolved references alone; judge on hard errors and the
    # artefact instead, so a paper with a dangling \ref still reaches the reviewers.
    ok = pdf.exists() and not errors
    if not pdf.exists() and not errors:
        errors = [f"no PDF produced (exit code {returncode})"]
    return BuildResult(ok, pdf if pdf.exists() else None, log, errors, warnings)
