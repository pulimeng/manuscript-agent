"""Manual workflow: review, revise by hand, review again — reviewers carry over."""
import os, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# the model is stubbed below, but the preflight still asks the SDK for a credential
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from manuscript_agent import cli
from manuscript_agent.history import SubmissionHistory
from manuscript_agent.schemas import (Review, ReviewPoint, PriorPointVerdict, MetaReview)

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp") / "ma-test-manual"
os.chdir(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp"))   # runs/ is relative to cwd
shutil.rmtree(Path("runs") / "ma-test-manual", ignore_errors=True)
shutil.rmtree(Path("runs") / "fresh", ignore_errors=True)
for stale in Path("runs").glob("*.archived-*"):
    shutil.rmtree(stale, ignore_errors=True)
shutil.rmtree(ROOT, ignore_errors=True); (ROOT / "sections").mkdir(parents=True)
(ROOT / "main.md").write_text("# Paper\n\nNo ablation reported.\n")

PROMPTS = []


class Stub:
    def parse(self, system, prompt, schema, max_tokens=16000, documents=None):
        if schema is Review:
            PROMPTS.append(prompt)
            first = "You reviewed an earlier version" not in prompt
            return Review(
                version_reviewed="v1" if first else "v2", summary="s", decision_critical=["W1"],
                prior_points=[] if first else [PriorPointVerdict(
                    label="W1", verdict="resolved", evidence="the ablation is now in §2")],
                score_change="n/a" if first else "the ablation landed",
                points=[ReviewPoint(label="W1", kind="weakness", version="v1", page=1,
                                    artifact_status="not_applicable", section="§2",
                                    comment="NO ABLATION", ask="revision", evidence="p.2", verification="verified_in_manuscript", resolvable_by_rewording=False)] if first else [],
                soundness=3, novelty=3, clarity=3, overall=4 if first else 7,
                confidence=4, recommendation="major_revision" if first else "accept")
        return MetaReview(summary="m", consensus_strengths=["a"],
                          critical_issues=["add the ablation"], optional_issues=[],
                          decision="major_revision", rationale="r", guidance_to_authors=["g"])

    def text(self, *a, **k):
        raise AssertionError("the manual workflow must never call the author")


cli.build = lambda spec, **k: Stub()          # every role is the stub
args = ["review", str(ROOT), "--no-compile", "--reviewers", "2"]

# --- round 1 -------------------------------------------------------------
assert cli.main(args) == 0
HIST = Path("runs") / "ma-test-manual"          # keyed by package name, outside the package
hist = SubmissionHistory.load(HIST)
assert len(hist.rounds) == 1 and hist.rounds[0].vid == "v1"
assert (HIST / "round-1/reviews.md").exists()
assert (HIST / "versions/v1/main.md").exists()
assert not (ROOT / ".manuscript-agent").exists(), "nothing may be written into the package"
print("round 1 recorded:", hist.rounds[0].vid, hist.rounds[0].decision)

# --- you revise by hand --------------------------------------------------
(ROOT / "main.md").write_text("# Paper\n\nAblation reported in this section.\n")
(ROOT / "letter.md").write_text("We added the ablation, as R1 asked.")

# --- round 2 -------------------------------------------------------------
assert cli.main(args + ["--letter", str(ROOT / "letter.md")]) == 0
hist = SubmissionHistory.load(HIST)
assert len(hist.rounds) == 2 and hist.rounds[1].vid == "v2", hist.rounds
assert hist.rounds[0].source_hash != hist.rounds[1].source_hash, "v2 must differ from v1"

r2 = PROMPTS[2]
for needle, what in (
    ("You reviewed an earlier version", "continuation framing"),
    ("NO ABLATION", "its own round-1 review"),
    ("We added the ablation", "the response letter you wrote"),
    ("<changes_since_your_review>", "the diff of your manual edits"),
    ("Ablation reported in this section", "the actual edit"),
):
    assert needle in r2, f"round-2 prompt lacks {what}"
print("round 2 carries: prior review, your letter, your diff")

assert (HIST / "round-2/changes-since-last-round.diff").exists()
assert (HIST / "round-2/response-letter.md").exists()
second = hist.rounds[1].reviews[0].review
assert second.prior_points[0].verdict == "resolved" and second.overall == 7
table = (HIST / "summary.md").read_text()
assert "| 1 |" in table and "| 2 |" in table and "4/10" in table and "7/10" in table
print("history table:\n" + table.strip())

# --- history written under an older schema still opens -------------------
import json
from manuscript_agent.history import HistoryError

legacy = ROOT / "legacy" / ".manuscript-agent"
legacy.mkdir(parents=True)
(legacy / "state.json").write_text(json.dumps({"rounds": [{
    "number": 1, "vid": "v1", "source_hash": "abc", "pdf_hash": "def", "pages": 19,
    "decision": "major_revision", "created_at": "2026-08-30T10:00:00", "letter": "",
    "reviews": [{"reviewer_id": "R1", "persona": "the methodologist", "review": {
        "version_reviewed": "v1", "summary": "s",
        "points": [{"label": "W1", "kind": "weakness", "version": "v1", "section": "§3",
                    "page": 4, "comment": "c", "severity": "blocking",
                    "artifact_status": "not_applicable"},
                   {"label": "W2", "kind": "weakness", "version": "v1", "section": "§4",
                    "page": 5, "comment": "c2", "severity": "minor",
                    "artifact_status": "not_applicable"}],
        "soundness": 3, "novelty": 3, "clarity": 3, "overall": 4, "confidence": 4,
        "recommendation": "major_revision"}}]}]}))

old = SubmissionHistory.load(legacy)
assert len(old.rounds) == 1 and old.rounds[0].digest_algo == 1, "old digest must be marked"
pts = old.rounds[0].reviews[0].review.points
assert [p.ask for p in pts] == ["fatal", "clarification"], [p.ask for p in pts]
assert all(p.verification == "inferred" for p in pts), "backfilled fields must not claim proof"
assert old.rounds[0].reviews[0].review.decision_critical == []
print("legacy history migrated: severity -> ask, backfills marked inferred")

(legacy / "state.json").write_text("{ not json")
try:
    SubmissionHistory.load(legacy); raise SystemExit("a corrupt history must not be silent")
except HistoryError as e:
    assert "--fresh" in str(e)
print("corrupt history reports how to recover")

# --- --fresh archives rather than destroys -------------------------------
shutil.rmtree(ROOT / "fresh", ignore_errors=True)
FR = ROOT / "fresh"; FR.mkdir(parents=True)
(FR / "main.md").write_text("# Paper\n\nFirst version.\n")
fresh_args = ["review", str(FR), "--no-compile", "--reviewers", "2"]
assert cli.main(fresh_args) == 0
FRH = Path("runs") / "fresh"
before = (FRH / "round-1/reviews.md").read_text()

assert cli.main(fresh_args + ["--fresh"]) == 0
archives = sorted(Path("runs").glob("fresh.archived-*"))
assert len(archives) == 1, archives
assert (archives[0] / "round-1/reviews.md").read_text() == before, "old reviews must survive"

restarted = SubmissionHistory.load(FRH)
assert len(restarted.rounds) == 1 and restarted.rounds[0].vid == "v1", restarted.rounds
assert not restarted.rounds[0].reviews[0].review.prior_points, \
    "a fresh start must not carry prior points"
print("--fresh archived the old history and restarted at v1")

# --- the panel is drawn at round 1 and kept for every later round --------
import json as _json
from manuscript_agent import config as _config

CAST = ROOT / "cast"; shutil.rmtree(CAST, ignore_errors=True); CAST.mkdir(parents=True)
(CAST / "main.md").write_text("# Paper\n\nv1 text.\n")
CASTH = Path("runs") / "cast"; shutil.rmtree(CASTH, ignore_errors=True)
cast_args = ["review", str(CAST), "--no-compile", "--reviewers", "3", "--adversarial",
             "--seed", "5"]

# round 1: a random draw from the pool, recorded
assert cli.main(cast_args) == 0
state = _json.loads((CASTH / "state.json").read_text())
c1 = state["casting"]
assert set(c1["reviewers"]) == {"R1", "R2", "R3", "R4"} and c1["editor"], c1
assert c1["reviewer_count"] == 3 and c1["adversarial"] is True
assert all(v in _config.MODEL_POOL for v in list(c1["reviewers"].values()) + [c1["editor"]])
print("round 1 drew:", c1["editor"], "|", list(c1["reviewers"].values()))

# round 2: the same panel returns, even when the flags would draw a different one
(CAST / "main.md").write_text("# Paper\n\nv2 text.\n")
assert cli.main(["review", str(CAST), "--no-compile", "--reviewers", "2", "--seed", "99",
                 "--model", "claude-sonnet-5"]) == 0
c2 = _json.loads((CASTH / "state.json").read_text())["casting"]
assert c2 == c1, "round 2 must keep round 1's panel regardless of flags"
hist = SubmissionHistory.load(CASTH)
assert len(hist.rounds) == 2
assert {r.reviewer_id for r in hist.rounds[1].reviews} == {"R1", "R2", "R3", "R4"}, \
    "the panel size is part of the casting, so --reviewers 2 must not shrink it"
print("round 2 kept the same panel; flags ignored")

# --recast draws again; --fresh starts a new manuscript history with a new draw
assert cli.main(cast_args + ["--recast", "--seed", "6"]) == 0
c3 = _json.loads((CASTH / "state.json").read_text())["casting"]
assert "seed 6" in c3["how"] and "seed 5" in c1["how"], "--recast must record a new draw"
assert cli.main(cast_args + ["--fresh", "--seed", "6"]) == 0
c4 = _json.loads((CASTH / "state.json").read_text())["casting"]
assert len(SubmissionHistory.load(CASTH).rounds) == 1, "--fresh restarts at round 1"
print("--recast redraws; --fresh restarts with a new panel")

# --- a bare PDF gets the same continuity: same panel, prior points carried ----
PDFD = ROOT / "pdfrounds"; shutil.rmtree(PDFD, ignore_errors=True); PDFD.mkdir(parents=True)
PDF = PDFD / "draft.pdf"
PDF.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n")
PDFH = Path("runs") / "draft"; shutil.rmtree(PDFH, ignore_errors=True)
PROMPTS.clear()
pdf_args = ["review", str(PDF), "--reviewers", "2", "--seed", "3"]

assert cli.main(pdf_args) == 0
h1 = SubmissionHistory.load(PDFH)
assert len(h1.rounds) == 1 and h1.casting and h1.rounds[0].pdf_hash, "a PDF round is recorded"
assert (PDFH / "versions/v1/v1.pdf").exists() and (PDFH / "round-1/submitted.pdf").exists()
assert not (PDFH / "round-1/checks.md").exists(), "no sources, no checks"
assert not (ROOT / "pdfrounds" / ".manuscript-agent").exists()
print("pdf round 1 recorded:", h1.rounds[0].vid, "| panel:", h1.casting["editor"])

PDF.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n% revised\n%%EOF\n")
assert cli.main(pdf_args + ["--letter", str(ROOT / "letter.md")]) == 0
h2 = SubmissionHistory.load(PDFH)
assert len(h2.rounds) == 2 and h2.casting == h1.casting, "same panel returns for a PDF"
assert h2.rounds[0].pdf_hash != h2.rounds[1].pdf_hash
r2 = PROMPTS[-1]
assert "You reviewed an earlier version" in r2 and "NO ABLATION" in r2, \
    "a PDF resubmission must carry the reviewer's own prior review"
assert "<changes_since_your_review>" not in r2, "no sources, so no diff is claimed"
assert not (PDFH / "round-2/changes-since-last-round.diff").exists()
print("pdf round 2: same panel, prior review carried, no diff claimed")
print("MANUAL ROUNDS OK")
