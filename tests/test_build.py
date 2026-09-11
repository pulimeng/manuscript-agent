"""The submitted artefact is a compiled PDF: build it, read its page count, attach it."""
import shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anthropic, openai
from manuscript_agent.build import available, compile_pdf
from manuscript_agent.llm import LLM, Attachment
from manuscript_agent.package import Package
from manuscript_agent.patches import tree_patch
from manuscript_agent.providers import OpenAILLM
from manuscript_agent.schemas import Review
from manuscript_agent.versions import VersionStore

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

# --- freezing records the page count and hashes the PDF -----------------
store = VersionStore(ROOT / "versions")
v = store.freeze(pkg.root, pkg.main, "v1")
assert v.build_ok and v.pages == 1 and v.pdf_hash, v.stamp()
print("frozen:", v.stamp())

# --- the PDF reaches both providers as an attachment ---------------------
pdf = Attachment.from_path(v.pdf)
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

# --- TeX echoes raw source bytes; its output is not necessarily UTF-8 ----
pkg = fresh()
(ROOT / "sections/latin1.tex").write_bytes(
    "\\section{Notes}\nSee \xa7 4 for the protocol.\n".encode("latin-1"))
main = ROOT / "main.tex"
main.write_text(main.read_text().replace("\\input{sections/results}",
                                         "\\input{sections/results}\n\\input{sections/latin1}"))
result = compile_pdf(ROOT, ROOT / "main.tex")
assert isinstance(result.log, str), "the log must decode without raising"
assert tree_patch(ROOT, ROOT, "v1", "hash").applies_to(ROOT), "git apply must tolerate it"
print("non-UTF-8 compiler output and git apply both survive")
print("BUILD OK")
