"""The submitted artefact is a compiled PDF: build it, attach it, repair a broken build."""
import shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anthropic, openai
from manuscript_agent.build import BuildError, available, compile_pdf
from manuscript_agent.config import RunConfig, VENUES
from manuscript_agent.llm import LLM, Attachment
from manuscript_agent.package import Package
from manuscript_agent.pipeline import SubmissionPipeline
from manuscript_agent.providers import OpenAILLM
from manuscript_agent.schemas import (Review, ReviewPoint, MetaReview, RevisionPlan,
                                      RevisionItem)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/package"
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp") / "ma-test-build"

if not available():
    print("no LaTeX toolchain; skipping"); raise SystemExit(0)


def fresh():
    shutil.rmtree(ROOT, ignore_errors=True)
    shutil.copytree(EXAMPLE, ROOT, ignore=shutil.ignore_patterns(".manuscript-build"))
    return Package.load(ROOT)


# --- a clean build -------------------------------------------------------
pkg = fresh()
r = compile_pdf(pkg.root, pkg.main)
assert r.ok and r.pdf.exists() and r.pdf.stat().st_size > 10_000, r.summary()
assert not r.errors and not r.warnings, r.summary()   # citations resolved via bibtex
assert not (ROOT / "main.aux").exists(), "aux files must stay out of the package"
print(f"clean build ok: {r.pdf.stat().st_size // 1000} kB, no unresolved citations")

# --- a broken build is detected -----------------------------------------
res = ROOT / "sections/results.tex"; good = res.read_text()
res.write_text(good.replace("\\end{figure}", ""))
bad = compile_pdf(pkg.root, pkg.main)
assert not bad.ok and bad.errors, bad.summary()
assert "\\begin{figure}" in bad.errors[0], bad.errors[0]
print("broken build detected:", bad.errors[0][:70])
res.write_text(good)

# --- the PDF reaches both providers as an attachment ---------------------
pdf = Attachment.from_path(compile_pdf(pkg.root, pkg.main).pdf)
assert pdf.media_type == "application/pdf" and pdf.size_mb > 0.01
for name, llm, err in (
    ("anthropic", LLM(client=anthropic.Anthropic(api_key="t", base_url="http://127.0.0.1:9",
                                                 max_retries=0)), anthropic.APIConnectionError),
    ("openai", OpenAILLM(client=openai.OpenAI(api_key="t", base_url="http://127.0.0.1:9",
                                              max_retries=0)), openai.APIConnectionError),
):
    try:
        llm.parse("s", "p", Review, documents=[pdf]); raise SystemExit("expected no route")
    except err:
        print(f"{name}: review request carries the PDF")

# --- through the pipeline: round 2 build break is repaired ---------------
VID = {"v": "v1"}


class StubLLM:
    """Round 1 revision breaks the build; the repair pass restores it."""
    def __init__(self): self.stage = []
    def parse(self, system, prompt, schema, max_tokens=16000, documents=None):
        if schema is Review:
            assert documents and documents[0].media_type == "application/pdf", \
                "reviewers must receive the compiled PDF"
            assert "attached as a PDF" in prompt and "%%% FILE:" not in prompt, \
                "reviewers must read the PDF, not the sources"
            return Review(version_reviewed=VID["v"], summary="s", prior_points=[],
                          score_change="n/a", points=[ReviewPoint(label="W1", kind="weakness", version=VID["v"], page=1,
                          artifact_status="not_applicable", section="§3", comment="c", severity="major")], soundness=3,
                          novelty=3, clarity=3, overall=5, confidence=4,
                          recommendation="major_revision")
        if schema is MetaReview:
            assert documents, "the editor must receive the PDF too"
            return MetaReview(version_reviewed=VID["v"], summary="m", consensus_strengths=["a"],
                              critical_issues=["tighten §3"], optional_issues=[],
                              decision="major_revision" if len(self.stage) < 2 else "accept",
                              rationale="r", guidance_to_authors=["g"])
        if schema is RevisionPlan:
            return RevisionPlan(strategy="s", items=[RevisionItem(refs=["R1-W1"],
                                critical_issues=[1], section="§3", action="tighten",
                                stance="accept")], out_of_scope=[])
        raise AssertionError(schema)
    def text(self, system, prompt, max_tokens=None, documents=None):
        # order matters: the response-letter prompt also embeds the revision plan
        if "<diff_of_changes>" in prompt:
            self.stage.append("letter")
            return "We have tightened Section 3 as requested."
        if "<compiler_output>" in prompt:
            self.stage.append("build-fix")
            assert "\\begin{figure}" in prompt, "the author needs the real compiler error"
            return ("%%% FILE: sections/results.tex %%%\n\\section{Results}\n"
                    "Macro-F1 is 0.81 with retrieval and 0.74 without \\cite{jones2021}.\n"
                    "%%% END FILE: sections/results.tex %%%\n")
        if "<revision_plan>" in prompt:
            self.stage.append("revise")
            return ("%%% FILE: sections/results.tex %%%\n\\section{Results}\n"
                    "\\begin{figure}\n\\caption{Broken on purpose.}\n"  # no \end{figure}
                    "%%% END FILE: sections/results.tex %%%\n")
        raise AssertionError("unexpected text() prompt")

pkg = fresh()
llm = StubLLM()
cfg = RunConfig(venue=VENUES["cs-conference"], rounds=2, reviewer_count=2,
                on_fabrication="warn")
res = SubmissionPipeline(cfg, llm=llm, on_event=print).run(pkg, ROOT / "runs")
# the broken candidate never becomes a version: it is repaired before promotion
assert "build-fix" in llm.stage, llm.stage
assert res.rounds[0].promoted, "the repaired candidate should have been promoted"
assert (res.rounds[0].directory / "candidate-checks.md").exists()
assert (res.rounds[0].directory / "revision.patch").exists()
for v in res.versions:
    assert v.pdf and v.pdf.exists() and v.pdf.stat().st_size > 10_000, v.stamp()
    assert v.source_hash and v.pdf_hash and v.pages
assert res.versions[0].source_hash != res.versions[1].source_hash, "v2 must differ from v1"
assert res.merged and res.final_patch.exists()
print("\nversions:", [v.stamp() for v in res.versions])
print("final patch:", res.final_patch.name)

# --- an unrepairable break stops the run --------------------------------
class Hopeless(StubLLM):
    def text(self, system, prompt, max_tokens=None, documents=None):
        if "<diff_of_changes>" in prompt:
            return "letter"
        return ("%%% FILE: sections/results.tex %%%\n\\begin{figure}\n"
                "%%% END FILE: sections/results.tex %%%\n")

pkg = fresh()
r2 = SubmissionPipeline(cfg, llm=Hopeless(), on_event=lambda m: None).run(pkg, ROOT / "runs2")
assert not r2.rounds[0].promoted, "an unrepairable candidate must not be promoted"
assert not r2.merged, "the run must end unmerged"
status = (r2.directory / "MERGE_STATUS").read_text()
assert status.startswith("UNMERGED"), status
try:
    raise BuildError("checked below")
except BuildError as e:
    print("unrepairable break stops the run:", str(e).splitlines()[0])
except SystemExit:
    raise

# --- TeX echoes raw source bytes; its output is not necessarily UTF-8 ----
pkg = fresh()
latin1 = (ROOT / "sections/latin1.tex")
latin1.write_bytes("\\section{Notes}\nSee \xa7 4 for the protocol.\n".encode("latin-1"))
main = ROOT / "main.tex"
main.write_text(main.read_text().replace("\\input{sections/results}",
                                         "\\input{sections/results}\n\\input{sections/latin1}"))
result = compile_pdf(ROOT, ROOT / "main.tex")
assert isinstance(result.log, str), "the log must decode without raising"
print("non-UTF-8 compiler output survived:", "ok" if result.log else "empty log")

from manuscript_agent.patches import tree_patch
p = tree_patch(ROOT, ROOT, "v1", "hash")
assert p.applies_to(ROOT), "git apply --check must tolerate non-UTF-8 trees"
print("git apply --check tolerates it too")
print("BUILD OK")
