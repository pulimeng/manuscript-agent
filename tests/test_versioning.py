"""Versions are frozen and hashed; the diff between rounds is real; checks read the sources."""
import shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manuscript_agent.checks import CheckReport, _math_balance, _scan_sources, error_context, run_checks
from manuscript_agent.package import Package
from manuscript_agent.patches import tree_patch
from manuscript_agent.versions import DIGEST_ALGO, VersionStore, sha

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/package"
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp") / "ma-test-versions"
shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
WORK = ROOT / "work"
shutil.copytree(EXAMPLE, WORK, ignore=shutil.ignore_patterns(".manuscript-build"))


def variant(name, results_body):
    """A copy of the example with sections/results.tex replaced — a hand-made revision."""
    dst = ROOT / name
    shutil.copytree(WORK, dst, ignore=shutil.ignore_patterns(".manuscript-build"))
    (dst / "sections/results.tex").write_text(results_body)
    return dst


store = VersionStore(ROOT / "versions")
pkg = Package.load(WORK)
v1 = store.freeze(pkg.root, pkg.main, "v1")

# --- a version is sealed and identified ---------------------------------
assert v1.source_hash and v1.pdf_hash and v1.pages, v1.stamp()
assert v1.digest_algo == DIGEST_ALGO and v1.digest_files, "the digest must say what it covers"
assert "v1" in v1.stamp() and "sha256" in v1.stamp()
before = v1.source_hash
(WORK / "sections/results.tex").write_text("\\section{Results}\nEdited after freezing.\n")
assert store.freeze(pkg.root, pkg.main, "vX").source_hash != before, "hash must track content"
assert v1.source_hash == before, "a frozen version must not change when the work tree does"
assert sha(v1.pdf.read_bytes()) == v1.pdf_hash, "pdf hash must match the pdf on disk"
print("freeze ok:", v1.stamp())

# --- the diff reviewers see is the real change --------------------------
rev = variant("revised", "\\section{Results}\nMacro-F1 is 0.81.\n")
patch = tree_patch(v1.root, rev, v1.vid, v1.source_hash)
assert patch and patch.files == ["sections/results.tex"], patch.files
assert "+Macro-F1 is 0.81." in patch.text and "diff --git" in patch.text
assert patch.applies_to(v1.root), "the diff must apply to the version it was taken against"
assert not tree_patch(v1.root, v1.root, "v1", "h"), "no change, no diff"
print(f"diff ok: {patch.files}, +{patch.added} -{patch.removed}")

# --- checks catch what a submission portal would ------------------------
bad = variant("bad", "\\section{Results}\n[TODO: numbers]\nSee Section 4 \\cite{ghost}.\n")
trial = store.evaluate(bad, bad / "main.tex", "v2")
report = run_checks(trial, trial.package)
kinds = {f.check for f in report.blocking}
assert not report.passed() and {"stale-wording", "citations"} <= kinds, report.render()
assert any(f.check == "stale-wording" and f.severity == "warning" for f in report.findings)
print("checks ok:", report.summary(), "->", sorted(kinds))

good = variant("good", "\\section{Results}\nMacro-F1 is 0.81.\n")
gt = store.evaluate(good, good / "main.tex", "v2")
assert run_checks(gt, gt.package).passed(), run_checks(gt, gt.package).render()
print("a clean revision passes")

# --- a length breach warns by default; blocks only when asked -----------
from dataclasses import replace as _replace
over = _replace(gt, pages=19)
warned = run_checks(over, over.package, page_limit=10)
assert warned.passed() and any(f.check == "pages" and f.severity == "warning"
                               for f in warned.findings), warned.render()
assert "pages" in {f.check for f in
                   run_checks(over, over.package, page_limit=10, enforce_pages=True).blocking}
assert run_checks(over, over.package).passed(), "no limit set means no page check"
print("pages: warns at 19>10, blocks only when enforced, silent when unset")

# --- unbalanced math is caught with the offending source line -----------
mb = variant("mathbad", "\\section{Results}\nAccuracy rose to ($93.5\\%).\n\nLater.\n")
rep = CheckReport(); _math_balance(rep, Package.load(mb))
assert any(f.check == "math" for f in rep.blocking) and "results.tex" in rep.blocking[0].where
assert "93.5" in error_context(Package.load(mb), rep)
ok = variant("mathok", "\\section{Results}\n($93.5\\%$). Escaped \\$5 and \\(x\\).\n")
rep2 = CheckReport(); _math_balance(rep2, Package.load(ok))
assert not rep2.blocking, rep2.render()
print("math check ok:", rep.blocking[0].where)

# --- a marker macro's definition is not an unresolved marker -------------
defs = variant("defs", "\\newcommand{\\todo}[1]{{\\color{red}[TODO: #1]}}\n\\section{R}\nClean.\n")
r_defs = CheckReport(); _scan_sources(r_defs, Package.load(defs))
assert not r_defs.blocking, r_defs.render()
uses = variant("uses", "\\newcommand{\\todo}[1]{[TODO: #1]}\n\\todo{run it}\nAnd [TODO: n].\n")
r_uses = CheckReport(); _scan_sources(r_uses, Package.load(uses))
assert len(r_uses.blocking) == 2, r_uses.render()
print("stale-wording: definitions skipped, uses still caught")
print("VERSIONING OK")
