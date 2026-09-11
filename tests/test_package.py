"""Submission-package handling: discovery, the review view, resolution, PDF submissions."""
import shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manuscript_agent.package import Package, PackageError

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp") / "ma-test-package"


def build():
    shutil.rmtree(ROOT, ignore_errors=True)
    (ROOT / "sections").mkdir(parents=True); (ROOT / "figures").mkdir()
    (ROOT / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        "\\input{sections/intro}\n\\input{sections/results}\n"
        "% \\input{sections/scrapped}\n\\bibliography{refs}\n\\end{document}\n")
    (ROOT / "sections/intro.tex").write_text("\\section{Introduction}\nWe study triage.\n")
    (ROOT / "sections/results.tex").write_text(
        "\\section{Results}\nMacro-F1 is 0.74 \\cite{smith2019}.\n"
        "\\begin{figure}\n\\includegraphics{figures/arch}\n"
        "\\caption{Architecture of the triage model.}\n\\end{figure}\n")
    (ROOT / "sections/scrapped.tex").write_text("SHOULD NOT APPEAR\n")
    (ROOT / "refs.bib").write_text("@article{smith2019,t={A}}\n@inproceedings{jones2021,t={B}}\n")
    (ROOT / "figures/arch.pdf").write_bytes(b"%PDF fake")
    return Package.load(ROOT)


# --- discovery -----------------------------------------------------------
pkg = build()
assert pkg.rel(pkg.main) == "main.tex"
assert [pkg.rel(s) for s in pkg.sources] == ["main.tex", "sections/intro.tex",
                                             "sections/results.tex"]
assert "SHOULD NOT APPEAR" not in pkg.text, "commented-out \\input must not be pulled in"
assert pkg.rel(pkg.assets[0]) == "figures/arch.pdf", "extensionless graphic must resolve"
assert pkg.known_citations == {"smith2019", "jones2021"}
assert "Architecture of the triage model." in pkg.manifest(), "caption must reach reviewers"
assert pkg.missing_assets() == []
print("discovery ok:", [pkg.rel(s) for s in pkg.sources])

# --- a PDF with no sources: reviewable, not revisable --------------------
from manuscript_agent.package import PdfSubmission
from manuscript_agent.manuscript import Manuscript

pdf_path = ROOT / "submitted.pdf"
pdf_path.write_bytes(b"%PDF-1.4\n\xd0\xcf binary payload\n%%EOF\n")
sub = PdfSubmission.load(pdf_path)
assert sub.fmt == "PDF" and sub.attachment().media_type == "application/pdf"
assert sub.known_citations == set() and sub.missing_assets() == []
assert not hasattr(sub, "replace"), "nothing may write into a submission"
assert "do not assert" in sub.artifact_manifest().lower()
snap = sub.snapshot(ROOT / "snap", "submitted")
assert snap.exists() and snap.read_bytes() == pdf_path.read_bytes()
try:
    Manuscript.load(pdf_path); raise SystemExit("binary file must not load as a manuscript")
except ValueError as e:
    assert "does not decode as UTF-8" in str(e)
print("pdf submission ok: attachable, honest artifact note, readable error on binary load")

# --- submission resolution order: .pdf -> .tex -> .md, and no Word --------
from manuscript_agent import cli

order = ROOT / "order"
shutil.rmtree(order, ignore_errors=True); (order / "sections").mkdir(parents=True)
(order / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n")
(order / "main.md").write_text("# Fallback\n")

# sources present -> a package, whether or not a stale PDF sits beside them
assert type(cli._open(str(order))).__name__ == "Package"
(order / "main.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
assert type(cli._open(str(order))).__name__ == "Package", "sources win over a PDF beside them"
assert type(cli._open(str(order), main=str(order / "main.tex"))).__name__ == "Package"

# markdown only
md_only = ROOT / "mdonly"; md_only.mkdir(parents=True, exist_ok=True)
(md_only / "paper.md").write_text("# Paper\n")
assert type(cli._open(str(md_only))).__name__ == "Package"

# pdf only
pdf_only = ROOT / "pdfonly"; pdf_only.mkdir(parents=True, exist_ok=True)
(pdf_only / "main.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
assert type(cli._open(str(pdf_only))).__name__ == "PdfSubmission"

for ext in (".docx", ".doc", ".odt", ".rtf"):
    doc = ROOT / f"paper{ext}"; doc.write_bytes(b"PK\x03\x04")
    try:
        cli._open(str(doc)); raise SystemExit(f"{ext} must be refused")
    except SystemExit as e:
        assert "not supported" in str(e), e
print("resolution ok: sources win, a lone PDF is reviewed as is, Word refused")

print("PACKAGE OK")
