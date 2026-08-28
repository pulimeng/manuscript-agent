"""Submission-package handling: discovery, review view, per-file write-back, guards."""
import shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manuscript_agent.config import RunConfig, VENUES
from manuscript_agent.package import Package, PackageError
from manuscript_agent.pipeline import SubmissionPipeline
from manuscript_agent.schemas import (Review, ReviewPoint, MetaReview, RevisionPlan,
                                      RevisionItem)

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

# --- write-back ----------------------------------------------------------
intro_before = (ROOT / "sections/intro.tex").read_text()
d = pkg.replace("""%%% FILE: sections/results.tex %%%
\\section{Results}
Macro-F1 is 0.74 \\cite{smith2019,jones2021}.
%%% END FILE: sections/results.tex %%%
%%% FILE: sections/ablation.tex %%%
\\section{Ablation}
[TODO: run it]
%%% END FILE: sections/ablation.tex %%%
""")
assert (ROOT / "sections/intro.tex").read_text() == intro_before, "untouched file changed"
assert (ROOT / "sections/ablation.tex").exists(), "new file not created"
assert (ROOT / "figures/arch.pdf").exists(), "asset clobbered"
assert "sections/results.tex" in d and "sections/ablation.tex" in d
print("write-back ok, unchanged files byte-identical")

for bad, why in (
    ("%%% FILE: ../escape.tex %%%\nx\n%%% END FILE: ../escape.tex %%%", "path escape"),
    ("%%% FILE: figures/f.pdf %%%\nx\n%%% END FILE: figures/f.pdf %%%", "binary write"),
    ("no markers", "no FILE blocks"),
):
    try:
        pkg.replace(bad); raise SystemExit(f"should have rejected: {why}")
    except PackageError:
        pass
print("guards ok: path escape, binary write, missing markers all rejected")

# --- through the pipeline ------------------------------------------------
class StubLLM:
    """Cites a real .bib key, invents a number, and references a figure it cannot create."""
    def parse(self, system, prompt, schema, max_tokens=16000):
        if schema is Review:
            assert "<package_manifest>" in prompt and "%%% FILE: sections/" in prompt
            return Review(summary="s", points=[ReviewPoint(label="W1", kind="weakness",
                          section="§2", comment="no ROC", severity="major")], soundness=3,
                          novelty=3, clarity=3, overall=5, confidence=4,
                          recommendation="major_revision")
        if schema is MetaReview:
            return MetaReview(summary="m", consensus_strengths=["a"],
                              critical_issues=["add ROC analysis"], optional_issues=[],
                              decision="major_revision", rationale="r", guidance_to_authors=["g"])
        if schema is RevisionPlan:
            return RevisionPlan(strategy="s", items=[RevisionItem(refs=["R1-W1"],
                                critical_issues=[1], section="§2", action="add ROC",
                                stance="accept")], out_of_scope=[])
        raise AssertionError(schema)
    def text(self, system, prompt, max_tokens=None):
        if "<unsourced_values>" in prompt or "<revision_plan>" in prompt:
            return ("%%% FILE: sections/results.tex %%%\n\\section{Results}\n"
                    "Macro-F1 is 0.74 \\cite{smith2019,jones2021}; AUC is 0.93.\n"
                    "\\begin{figure}\\includegraphics{figures/roc.pdf}"
                    "\\caption{ROC.}\\end{figure}\n"
                    "%%% END FILE: sections/results.tex %%%\n")
        return "letter"

pkg = build()
cfg = RunConfig(venue=VENUES["cs-conference"], rounds=2, reviewer_count=2,
                on_fabrication="warn", compile_pdf=False)
res = SubmissionPipeline(cfg, llm=StubLLM(), on_event=print).run(pkg, ROOT / "runs")
flags = res.rounds[0].integrity
assert flags == ["0.93", "figures/roc.pdf"], flags  # nothing but the two real findings
assert "jones2021" not in flags, flags             # real .bib key not accused
# the run directory lives inside the package here: its snapshots must not leak into the view
assert not any(f.startswith("2026") or f.isdigit() and len(f) > 5 for f in flags), flags
snap = res.rounds[0].directory / "submitted"
assert (snap / "sections/intro.tex").exists() and (snap / "figures/arch.pdf").exists()
print("\npipeline ok — flagged:", flags)

# --- a PDF with no sources: reviewable, not revisable --------------------
from manuscript_agent.package import PdfSubmission
from manuscript_agent.manuscript import Manuscript

pdf_path = ROOT / "submitted.pdf"
pdf_path.write_bytes(b"%PDF-1.4\n\xd0\xcf binary payload\n%%EOF\n")
sub = PdfSubmission.load(pdf_path)
assert sub.fmt == "PDF" and sub.attachment().media_type == "application/pdf"
assert sub.known_citations == set() and sub.missing_assets() == []
for call, why in ((lambda: sub.replace("x"), "replace"),
                  (lambda: sub.emit_instructions, "emit_instructions")):
    try:
        call(); raise SystemExit(f"{why} should refuse on a source-less PDF")
    except PackageError as e:
        assert "cannot be revised" in str(e)
snap = sub.snapshot(ROOT / "snap", "submitted")
assert snap.exists() and snap.read_bytes() == pdf_path.read_bytes()
try:
    Manuscript.load(pdf_path); raise SystemExit("binary file must not load as a manuscript")
except ValueError as e:
    assert "does not decode as UTF-8" in str(e)
print("pdf submission ok: attachable, refuses revision, readable error on binary text load")
print("PACKAGE OK")
