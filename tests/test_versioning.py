"""Versions are frozen and hashed; patches are proposals; promotion is gated."""
import shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manuscript_agent.checks import run_checks
from manuscript_agent.package import Package
from manuscript_agent.patches import materialise, tree_patch
from manuscript_agent.versions import VersionStore

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/package"
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp") / "ma-test-versions"
shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
WORK = ROOT / "work"; shutil.copytree(EXAMPLE, WORK, ignore=shutil.ignore_patterns(".manuscript-build"))

store = VersionStore(ROOT / "versions")
pkg = Package.load(WORK)
v1 = store.freeze(pkg.root, pkg.main, "v1")

# --- a version is sealed and identified ---------------------------------
assert v1.source_hash and v1.pdf_hash and v1.pages, v1.stamp()
assert "v1" in v1.stamp() and "sha256" in v1.stamp()
before = v1.source_hash
(WORK / "sections/results.tex").write_text("\\section{Results}\nEdited after freezing.\n")
assert store.freeze(pkg.root, pkg.main, "vX").source_hash != before, "hash must track content"
assert v1.source_hash == before, "a frozen version must not change when the work tree does"
print("freeze ok:", v1.stamp())

# --- the reviewed PDF belongs to the reviewed sources -------------------
from manuscript_agent.versions import sha
assert sha(v1.pdf.read_bytes()) == v1.pdf_hash, "pdf hash must match the pdf on disk"
print("pdf hash matches its sources")

# --- a proposal is a patch, and does not touch the version --------------
blocks = {"sections/results.tex": "\\section{Results}\nMacro-F1 is 0.81.\n"}
cand = materialise(v1.root, "main.tex", blocks, ROOT / "candidate")
patch = tree_patch(v1.root, cand, v1.vid, v1.source_hash)
assert patch and patch.files == ["sections/results.tex"], patch.files
assert patch.applies_to(v1.root), "the patch must apply to the version it was made against"
assert "Edited after freezing" not in (v1.root / "sections/results.tex").read_text()
assert store.freeze(v1.root, v1.main, "vY").source_hash == v1.source_hash, \
    "the frozen tree is unchanged by proposing against it"
print(f"patch ok: {patch.files}, +{patch.added} -{patch.removed}, applies cleanly")

# --- checks gate what may be promoted -----------------------------------
bad = materialise(v1.root, "main.tex",
                  {"sections/results.tex": "\\section{Results}\n[TODO: numbers]\n"
                                           "See Section 4 \\cite{ghost}.\n"},
                  ROOT / "bad")
trial = store.evaluate(bad, bad / "main.tex", "v2-candidate")
report = run_checks(trial, trial.package, page_limit=1)
kinds = {f.check for f in report.blocking}
assert not report.passed() and {"stale-wording", "citations"} <= kinds, report.render()
assert any(f.check == "stale-wording" and f.severity == "warning" for f in report.findings)
print("checks ok:", report.summary(), "->", sorted(kinds))

good = materialise(v1.root, "main.tex", blocks, ROOT / "good")
gt = store.evaluate(good, good / "main.tex", "v2-candidate")
assert run_checks(gt, gt.package, page_limit=10).passed(), run_checks(gt, gt.package).render()
print("a clean candidate passes")
print("VERSIONING OK")
