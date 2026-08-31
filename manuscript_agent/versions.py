"""Immutable manuscript versions.

One version is frozen per review round: a complete copy of the sources plus the PDF built
from exactly those sources, hashed so that every review, every patch and every check can name
the version it applies to. Nothing writes into a frozen version after it is sealed.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .build import BUILD_DIR, available as tex_available, compile_pdf
from .package import Package

PAGES = re.compile(r"Output written on .*?\((\d+) pages?", re.IGNORECASE)
NOISE = {".git", ".venv", "__pycache__", ".DS_Store", BUILD_DIR}


def _ignore_for(destination: Path):
    """Skip noise, and skip the destination itself — the run directory usually lives
    inside the package, and copytree will otherwise recurse into its own output."""
    out = destination.resolve()

    def ignore(src, names):
        base = Path(src).resolve()
        return {
            name for name in names
            if name in NOISE or (base / name) == out or (base / name) in out.parents
        }

    return ignore


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Version:
    """A sealed manuscript version. Read-only by contract."""

    vid: str
    root: Path
    main: Path
    source_hash: str
    created_at: str
    pdf: Optional[Path] = None
    pdf_hash: Optional[str] = None
    pages: Optional[int] = None
    build_attempted: bool = False
    build_ok: bool = False
    build_errors: List[str] = field(default_factory=list)
    build_warnings: List[str] = field(default_factory=list)

    @property
    def package(self) -> Package:
        return Package.load(self.root, self.main)

    def stamp(self) -> str:
        """The identity a reviewer must quote back."""
        bits = [f"{self.vid}", f"source sha256:{self.source_hash[:16]}"]
        if self.pdf_hash:
            bits.append(f"pdf sha256:{self.pdf_hash[:16]}")
        if self.pages:
            bits.append(f"{self.pages} pages")
        return " | ".join(bits)

    def summary(self) -> str:
        lines = [f"- {self.stamp()}", f"  frozen at {self.created_at}", f"  sources {self.root}"]
        if self.build_warnings:
            lines.append(f"  build warnings: {len(self.build_warnings)}")
        return "\n".join(lines)


def source_digest(pkg: Package) -> str:
    """Hash over every source file, path included, so a rename is a change."""
    h = hashlib.sha256()
    for src in sorted(pkg.sources, key=lambda p: pkg.rel(p)):
        h.update(pkg.rel(src).encode())
        h.update(b"\0")
        h.update(src.read_bytes())
        h.update(b"\0")
    for bib in sorted(pkg.bib_files, key=lambda p: pkg.rel(p)):
        h.update(pkg.rel(bib).encode())
        h.update(b"\0")
        h.update(bib.read_bytes())
    return h.hexdigest()


class VersionStore:
    """Freezes versions under `<run>/versions/` and never edits one afterwards."""

    def __init__(self, directory: Path, compile_pdfs: bool = True, engine: str = "pdflatex"):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.compile_pdfs = compile_pdfs
        self.engine = engine
        self.versions: List[Version] = []

    @property
    def latest(self) -> Optional[Version]:
        return self.versions[-1] if self.versions else None

    def freeze(self, source: Path, main: Path, vid: Optional[str] = None) -> Version:
        """Copy the working sources into an immutable version and build its PDF."""
        vid = vid or f"v{len(self.versions) + 1}"
        root = self.directory / vid
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(source, root, ignore=_ignore_for(root))

        frozen_main = root / main.resolve().relative_to(Path(source).resolve())
        pkg = Package.load(root, frozen_main)
        version = Version(
            vid=vid,
            root=root,
            main=frozen_main,
            source_hash=source_digest(pkg),
            created_at=datetime.now().isoformat(timespec="seconds"),
        )

        if self.compile_pdfs and frozen_main.suffix.lower() == ".tex" and tex_available():
            version.build_attempted = True
            result = compile_pdf(root, frozen_main, self.engine)
            version.build_ok = result.ok
            version.build_errors = result.errors
            version.build_warnings = result.warnings
            if result.pdf and result.pdf.exists():
                pdf = root / f"{vid}.pdf"
                shutil.copyfile(result.pdf, pdf)
                version.pdf = pdf
                version.pdf_hash = sha(pdf.read_bytes())
                m = PAGES.search(result.log)
                version.pages = int(m.group(1)) if m else None

        self.versions.append(version)
        return version

    def evaluate(self, root: Path, main: Path, vid: str) -> Version:
        """Build a candidate in place so it can be checked. Not registered as a version:
        a candidate only becomes one by being promoted."""
        pkg = Package.load(root, main)
        version = Version(
            vid=vid,
            root=root,
            main=main,
            source_hash=source_digest(pkg),
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        if self.compile_pdfs and main.suffix.lower() == ".tex" and tex_available():
            version.build_attempted = True
            result = compile_pdf(root, main, self.engine)
            version.build_ok = result.ok
            version.build_errors = result.errors
            version.build_warnings = result.warnings
            if result.pdf and result.pdf.exists():
                version.pdf = result.pdf
                version.pdf_hash = sha(result.pdf.read_bytes())
                m = PAGES.search(result.log)
                version.pages = int(m.group(1)) if m else None
        return version

    def manifest(self) -> str:
        return "\n".join(v.summary() for v in self.versions)
