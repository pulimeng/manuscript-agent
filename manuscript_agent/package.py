"""A submission package: a main file plus the sections, bibliography and figures it needs.

Real manuscripts are not one file. This resolves `\\input`/`\\include` into a single view of
the sources, inventories the assets (figures, data), and reads the bibliography so citations
can be checked. Nothing here writes into the package: revision is the author's job.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from .build import BUILD_DIR
from .llm import Attachment
from .manuscript import strip_fence

TEXT_SUFFIXES = {".tex", ".md", ".markdown", ".txt", ".rst", ".cls", ".sty"}
# everything that counts as a source when diffing one version against the next
SOURCE_SUFFIXES = TEXT_SUFFIXES | {".bib"}
ASSET_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg", ".gif", ".tif", ".tiff",
                  ".csv", ".tsv", ".xlsx", ".json"}

INCLUDE = re.compile(r"\\(?:input|include|subfile|import|subimport)\s*\{([^}]+)\}")
GRAPHIC = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\s*\{([^}]+)\}")
BIB_DECL = re.compile(r"\\(?:bibliography|addbibresource)\s*\{([^}]+)\}")
GRAPHICSPATH = re.compile(r"\\graphicspath\s*\{(.+?)\}\s*(?:%|$)", re.MULTILINE)
PATH_GROUP = re.compile(r"\{([^{}]*)\}")
BIB_ENTRY = re.compile(r"@\w+\s*\{\s*([^,\s}]+)")
CAPTION = re.compile(r"\\caption\s*\{")
ARTIFACT_URL = re.compile(
    r"https?://(?:www\.)?(?:github\.com|gitlab\.com|zenodo\.org|osf\.io|"
    r"huggingface\.co|figshare\.com|dataverse\.[\w.]+|codeocean\.com)/[^\s}\\,)]+"
)
CODE_SUFFIXES = {".py", ".ipynb", ".R", ".r", ".sh", ".jl", ".m", ".cpp", ".java"}
DATA_SUFFIXES = {".csv", ".tsv", ".json", ".jsonl", ".parquet", ".zip", ".tar", ".gz",
                 ".npz", ".h5", ".xlsx"}
DOCUMENTCLASS = re.compile(r"^\s*\\documentclass", re.MULTILINE)
COMMENT = re.compile(r"(?<!\\)%.*$", re.MULTILINE)

class PackageError(RuntimeError):
    """The author's output could not be applied to the package."""


@dataclass
class Package:
    """A manuscript made of several files, edited in place, reviewed as one document."""

    root: Path
    main: Path
    sources: List[Path] = field(default_factory=list)   # text files, in include order
    assets: List[Path] = field(default_factory=list)    # figures and data
    bib_files: List[Path] = field(default_factory=list)
    captions: Dict[str, str] = field(default_factory=dict)
    graphics_paths: List[str] = field(default_factory=list)  # from \graphicspath

    # -- loading ---------------------------------------------------------

    @staticmethod
    def load(target: str | Path, main: Optional[str | Path] = None) -> "Package":
        target = Path(target)
        root = target if target.is_dir() else target.parent
        main_path = Path(main) if main else (None if target.is_dir() else target)
        if main_path is None:
            main_path = _find_main(root)
        if not main_path.exists():
            raise FileNotFoundError(main_path)
        pkg = Package(root=root.resolve(), main=main_path.resolve())
        pkg._discover()
        return pkg

    def _discover(self) -> None:
        self.sources = _resolve_includes(self.main, self.root)
        self.graphics_paths = _graphics_paths(self.sources)
        seen_assets: List[Path] = []
        bibs: List[Path] = []
        for src in self.sources:
            body = src.read_text(errors="replace")
            clean = COMMENT.sub("", body)
            for raw in GRAPHIC.findall(clean):
                for candidate in _asset_candidates(self.root, src, raw, self.graphics_paths):
                    if candidate.exists() and candidate not in seen_assets:
                        seen_assets.append(candidate)
                        break
                else:
                    p = _asset_candidates(self.root, src, raw, self.graphics_paths)[0]
                    if p not in seen_assets:
                        seen_assets.append(p)  # recorded even when missing
                caption = _caption_near(body, raw)
                if caption:
                    self.captions[raw] = caption
            for raw in BIB_DECL.findall(clean):
                for name in raw.split(","):
                    p = _resolve(self.root, src, name.strip(), default_suffix=".bib")
                    if p.exists() and p not in bibs:
                        bibs.append(p)
        # an undeclared .bib sitting beside a source file still counts as available.
        # Scoped to the source directories on purpose: a recursive scan would sweep up the
        # copies inside round snapshots when the run directory lives in the package.
        for directory in sorted({src.parent for src in self.sources}):
            for p in sorted(directory.glob("*.bib")):
                if p not in bibs:
                    bibs.append(p)
        self.assets = seen_assets
        self.bib_files = bibs

    # -- the reviewer's view ---------------------------------------------

    @property
    def path(self) -> Path:
        return self.main

    @property
    def fmt(self) -> str:
        return "LaTeX" if self.main.suffix.lower() == ".tex" else "Markdown"

    @property
    def known_citations(self) -> Set[str]:
        keys: Set[str] = set()
        for bib in self.bib_files:
            keys.update(BIB_ENTRY.findall(bib.read_text(errors="replace")))
        return keys

    def artifacts(self) -> List[str]:
        """Code, data and repository links the manuscript actually ships or points to.

        Reviewers see only the PDF, so without this they cannot tell 'the authors released
        nothing' from 'I cannot open it from here'.
        """
        found: List[str] = []
        for src in self.sources:
            for url in ARTIFACT_URL.findall(src.read_text(errors="replace")):
                entry = f"link: {url.rstrip('.')}"
                if entry not in found:
                    found.append(entry)
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or any(x.startswith(".") for x in path.parts):
                continue
            suffix = path.suffix.lower()
            if suffix in CODE_SUFFIXES:
                found.append(f"code in package: {self.rel(path)}")
            elif suffix in DATA_SUFFIXES:
                found.append(f"data in package: {self.rel(path)}")
        return found

    def artifact_manifest(self) -> str:
        items = self.artifacts()
        if not items:
            return (
                "No code, data or repository link is visible in the review package or named "
                "in the text you were given. That is a fact about this package, not about "
                "what the authors hold: artifacts are often submitted through a separate "
                "channel, withheld for anonymity, or promised on acceptance. If the "
                "manuscript states an availability plan, judge that statement. Do not assert "
                "that the authors have no artifact."
            )
        return (
            "The submission ships or names the following artifacts. You are reading the PDF "
            "only, so you cannot open them from here — that is a limit of your access, not a "
            "failure by the authors:\n"
            + "\n".join(f"  - {i}" for i in items)
        )

    def missing_assets(self) -> List[str]:
        """Figures and data files referenced by the text that do not exist on disk."""
        return [self.rel(a) for a in self.assets if not a.exists()]

    def rel(self, p: Path) -> str:
        try:
            return str(p.resolve().relative_to(self.root))
        except ValueError:
            return str(p)

    def manifest(self) -> str:
        lines = [f"main: {self.rel(self.main)}"]
        others = [self.rel(s) for s in self.sources if s != self.main]
        if others:
            lines.append(f"source files ({len(others)}): {', '.join(others)}")
        if self.bib_files:
            n = len(self.known_citations)
            lines.append(
                f"bibliography: {', '.join(self.rel(b) for b in self.bib_files)} "
                f"({n} entries available to cite)"
            )
        if self.assets:
            lines.append("figures and data (binary — their contents are not shown):")
            for a in self.assets:
                name = self.rel(a)
                mark = "" if a.exists() else "  [MISSING FROM PACKAGE]"
                cap = next(
                    (c for raw, c in self.captions.items() if raw in name or name.endswith(raw)),
                    "",
                )
                lines.append(f"  - {name}{mark}" + (f" — caption: {cap}" if cap else ""))
        return "\n".join(lines)

    @property
    def text(self) -> str:
        """The whole package as one document: manifest, then every source file in order."""
        parts = [f"<package_manifest>\n{self.manifest()}\n</package_manifest>", ""]
        for src in self.sources:
            rel = self.rel(src)
            parts.append(f"%%% FILE: {rel} %%%")
            parts.append(src.read_text(errors="replace").rstrip("\n"))
            parts.append(f"%%% END FILE: {rel} %%%")
            parts.append("")
        return "\n".join(parts)

    def save(self) -> None:
        """Files are written by `replace`; kept for interface parity with Manuscript."""

    NOISE = {".git", ".venv", "__pycache__", ".DS_Store", BUILD_DIR}

    def snapshot(self, directory: Path, label: str) -> Path:
        """Copy the whole package. The run directory often lives inside it — skip it, or
        copytree recurses into its own destination."""
        out = (directory / label).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            shutil.rmtree(out)

        def ignore(src: str, names: List[str]) -> Set[str]:
            base = Path(src).resolve()
            skip = set()
            for name in names:
                candidate = base / name
                if name in self.NOISE or candidate == out or candidate in out.parents:
                    skip.add(name)
            return skip

        shutil.copytree(self.root, out, ignore=ignore)
        return out


# -- helpers --------------------------------------------------------------


def _visible(root: Path, pattern: str) -> List[Path]:
    """Files matching `pattern`, ignoring dot-directories.

    Frozen versions live under `.manuscript-agent/versions/`, and each is a complete copy of
    the package — including its own main.tex. Without this, discovery finds them.
    """
    return sorted(
        p for p in root.rglob(pattern)
        if not any(part.startswith(".") for part in p.relative_to(root).parts)
    )


def _find_main(root: Path) -> Path:
    tex = _visible(root, "*.tex")
    with_class = [p for p in tex if DOCUMENTCLASS.search(p.read_text(errors="replace"))]
    if len(with_class) == 1:
        return with_class[0]
    if len(with_class) > 1:
        named = [p for p in with_class if p.stem.lower() in ("main", "paper", "manuscript")]
        if named:
            return named[0]
        raise PackageError(
            "several files declare \\documentclass; name the main one explicitly: "
            + ", ".join(str(p.relative_to(root)) for p in with_class)
        )
    for name in ("main.md", "paper.md", "manuscript.md"):
        if (root / name).exists():
            return root / name
    md = _visible(root, "*.md")
    if len(md) == 1:
        return md[0]
    raise PackageError(f"no main manuscript file found in {root}")


def _resolve(root: Path, src: Path, raw: str, default_suffix: str = "") -> Path:
    raw = raw.strip().strip('"')
    p = Path(raw)
    if default_suffix and not p.suffix:
        p = p.with_suffix(default_suffix)
    if p.is_absolute():
        return p
    local = (src.parent / p).resolve()
    return local if local.exists() else (root / p).resolve()


def _graphics_paths(sources: List[Path]) -> List[str]:
    """Directories declared with \\graphicspath{{a/}{b/}}, searched before the file's own."""
    out: List[str] = []
    for src in sources:
        body = COMMENT.sub("", src.read_text(errors="replace"))
        for decl in GRAPHICSPATH.findall(body):
            for group in PATH_GROUP.findall(decl):
                cleaned = group.strip()
                if cleaned and cleaned not in out:
                    out.append(cleaned)
    return out


def _asset_candidates(root: Path, src: Path, raw: str,
                      graphics_paths: Optional[List[str]] = None) -> List[Path]:
    """Every place LaTeX would look for this graphic, in the order it would look."""
    prefixes = [""] + list(graphics_paths or [])
    bases: List[Path] = []
    for prefix in prefixes:
        joined = f"{prefix}{raw.strip()}" if prefix else raw.strip()
        candidate = _resolve(root, src, joined)
        if candidate not in bases:
            bases.append(candidate)
    out: List[Path] = []
    for base in bases:
        if base.suffix:
            out.append(base)
        else:
            out += [base.with_suffix(s) for s in (".pdf", ".png", ".jpg", ".jpeg", ".eps")]
    return out


def _resolve_includes(main: Path, root: Path) -> List[Path]:
    """Depth-first include order, each file once."""
    ordered: List[Path] = []
    seen: Set[Path] = set()

    def walk(path: Path) -> None:
        path = path.resolve()
        if path in seen or not path.exists():
            return
        seen.add(path)
        ordered.append(path)
        body = COMMENT.sub("", path.read_text(errors="replace"))
        for raw in INCLUDE.findall(body):
            child = _resolve(root, path, raw, default_suffix=".tex")
            if child.suffix.lower() in TEXT_SUFFIXES:
                walk(child)

    walk(main)
    return ordered


def _caption_near(body: str, graphic_raw: str) -> str:
    """The caption of the figure environment containing this graphic, if any."""
    idx = body.find(graphic_raw)
    if idx < 0:
        return ""
    start = body.rfind("\\begin{figure", 0, idx)
    end = body.find("\\end{figure", idx)
    scope = body[start if start >= 0 else max(0, idx - 800): end if end > 0 else idx + 800]
    m = CAPTION.search(scope)
    if not m:
        return ""
    depth, out = 1, []
    for ch in scope[m.end():]:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        out.append(ch)
    return " ".join("".join(out).split())[:300]


@dataclass
class PdfSubmission:
    """A submitted PDF with no sources behind it: it can be reviewed, not revised."""

    path: Path

    @staticmethod
    def load(target: str | Path) -> "PdfSubmission":
        p = Path(target).resolve()
        if not p.exists():
            raise FileNotFoundError(p)
        return PdfSubmission(path=p)

    @property
    def root(self) -> Path:
        return self.path.parent

    @property
    def main(self) -> Path:
        return self.path

    @property
    def fmt(self) -> str:
        return "PDF"

    @property
    def text(self) -> str:
        return f"(The submission is the attached PDF: {self.path.name}. There are no sources.)"

    @property
    def known_citations(self) -> Set[str]:
        return set()

    def artifacts(self) -> List[str]:
        """A bare PDF ships nothing the reviewers can be pointed at."""
        return []

    def artifact_manifest(self) -> str:
        return (
            "You were given a PDF and nothing else. Whether the authors provide code or "
            "data is whatever the manuscript itself states; do not assert that they have "
            "no artifact because none accompanies this file."
        )

    def missing_assets(self) -> List[str]:
        return []

    def attachment(self) -> Attachment:
        return Attachment.from_path(self.path)

    def save(self) -> None:
        pass

    def snapshot(self, directory: Path, label: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        out = directory / f"{label}.pdf"
        shutil.copyfile(self.path, out)
        return out

