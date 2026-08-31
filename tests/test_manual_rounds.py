"""Manual workflow: review, revise by hand, review again — reviewers carry over."""
import shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manuscript_agent import cli
from manuscript_agent.history import SubmissionHistory
from manuscript_agent.schemas import (Review, ReviewPoint, PriorPointVerdict, MetaReview)

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp") / "ma-test-manual"
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
hist = SubmissionHistory.load(ROOT / ".manuscript-agent")
assert len(hist.rounds) == 1 and hist.rounds[0].vid == "v1"
assert (ROOT / ".manuscript-agent/round-1/reviews.md").exists()
assert (ROOT / ".manuscript-agent/versions/v1/main.md").exists()
print("round 1 recorded:", hist.rounds[0].vid, hist.rounds[0].decision)

# --- you revise by hand --------------------------------------------------
(ROOT / "main.md").write_text("# Paper\n\nAblation reported in this section.\n")
(ROOT / "letter.md").write_text("We added the ablation, as R1 asked.")

# --- round 2 -------------------------------------------------------------
assert cli.main(args + ["--letter", str(ROOT / "letter.md")]) == 0
hist = SubmissionHistory.load(ROOT / ".manuscript-agent")
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

assert (ROOT / ".manuscript-agent/round-2/changes-since-last-round.diff").exists()
assert (ROOT / ".manuscript-agent/round-2/response-letter.md").exists()
second = hist.rounds[1].reviews[0].review
assert second.prior_points[0].verdict == "resolved" and second.overall == 7
table = (ROOT / ".manuscript-agent/summary.md").read_text()
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
before = (FR / ".manuscript-agent/round-1/reviews.md").read_text()

assert cli.main(fresh_args + ["--fresh"]) == 0
archives = sorted(FR.glob(".manuscript-agent.archived-*"))
assert len(archives) == 1, archives
assert (archives[0] / "round-1/reviews.md").read_text() == before, "old reviews must survive"

restarted = SubmissionHistory.load(FR / ".manuscript-agent")
assert len(restarted.rounds) == 1 and restarted.rounds[0].vid == "v1", restarted.rounds
assert not restarted.rounds[0].reviews[0].review.prior_points, \
    "a fresh start must not carry prior points"
print("--fresh archived the old history and restarted at v1")
print("MANUAL ROUNDS OK")
