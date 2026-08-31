"""A resubmission is reviewed by the same reviewer, continuing its own assessment."""
import shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manuscript_agent.config import RunConfig, VENUES
from manuscript_agent.manuscript import Manuscript
from manuscript_agent.pipeline import SubmissionPipeline, dropped_prior_points
from manuscript_agent.schemas import (Review, ReviewPoint, PriorPointVerdict, MetaReview,
                                      RevisionPlan, RevisionItem, ScoredReview)

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp") / "ma-test-continuity"


def point(label, comment, page=2):
    return ReviewPoint(label=label, kind="weakness", version="v1", page=page,
                       artifact_status="not_applicable", section="§3",
                       comment=comment, ask="revision", evidence="p.2", verification="verified_in_manuscript", resolvable_by_rewording=False)


class Stub:
    """Round 2 accounts for W1 and silently forgets W2."""

    def __init__(self, account=True):
        self.account, self.prompts, self.round = account, [], 0

    def parse(self, system, prompt, schema, max_tokens=16000, documents=None):
        if schema is Review:
            self.prompts.append(prompt)
            first = "You reviewed an earlier version" not in prompt
            prior = []
            if not first and self.account:
                prior = [PriorPointVerdict(label="W1", verdict="resolved",
                                           evidence="§3 now reports the ablation")]
            return Review(
                version_reviewed="v1" if first else "v2",
                summary="s", decision_critical=["W1"],
                prior_points=prior,
                score_change="n/a" if first else "the ablation landed",
                points=[point("W1", "THE ABLATION IS MISSING"),
                        point("W2", "no variance reported")] if first else [],
                soundness=3, novelty=3, clarity=3, overall=5 if first else 7,
                confidence=4, recommendation="major_revision")
        if schema is MetaReview:
            self.round += 1
            return MetaReview(summary="m", consensus_strengths=["a"],
                              critical_issues=["add the ablation"], optional_issues=[],
                              decision="major_revision", rationale="r",
                              guidance_to_authors=["g"])
        return RevisionPlan(strategy="s", items=[RevisionItem(refs=["R1-W1"],
                            critical_issues=[1], section="§3", action="add ablation",
                            stance="accept")], out_of_scope=[])

    def text(self, system, prompt, max_tokens=None, documents=None):
        if "<diff_of_changes>" in prompt:
            return "WE ADDED THE ABLATION IN SECTION 3."
        # deliberately introduces no new numbers, so the fabrication gate stays out of it
        return ("%%% FILE: paper.md %%%\n# T\n\nAn ablation is now reported.\n"
                "%%% END FILE: paper.md %%%\n")


def run(account):
    shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
    (ROOT / "paper.md").write_text("# T\n\nNo ablation here.\n")
    cfg = RunConfig(venue=VENUES["workshop"], rounds=2, reviewer_count=2, compile_pdf=False)
    llm = Stub(account)
    res = SubmissionPipeline(cfg, llm=llm, on_event=lambda m: None).run(
        Manuscript.load(ROOT / "paper.md"), ROOT / "runs")
    return res, llm


# --- the round-2 prompt continues the same reviewer ----------------------
res, llm = run(account=True)
r2 = llm.prompts[2]
for needle, what in (
    ("You reviewed an earlier version", "framed as a continuation"),
    ("THE ABLATION IS MISSING", "its own previous review"),
    ("WE ADDED THE ABLATION", "the author's response letter"),
    ("<changes_since_your_review>", "the diff of what actually changed"),
    ("not the response letter", "told to verify, not trust"),
):
    assert needle in r2, f"round-2 prompt lacks {what}"
print("round-2 prompt carries: prior review, response letter, diff, verify instruction")

# the diff must be the real patch, not a description of it
assert "An ablation is now reported" in r2 and "diff --git" in r2, \
    "the actual patch must be shown"
print("the diff shown is the real patch")

# --- resolved verdicts are recorded and rendered -------------------------
second = res.rounds[1].reviews[0].review
assert second.prior_points and second.prior_points[0].verdict == "resolved"
assert second.overall == 7 and "ablation landed" in second.score_change
report = (res.rounds[1].directory / "reviews.md").read_text()
assert "Verdict on my previous points" in report and "**resolved**" in report
print("prior-point verdicts recorded and rendered; score movement explained")

# --- a reviewer that forgets its own point is caught --------------------
assert res.rounds[1].misanchored, "W2 was dropped and should be flagged"
assert any("W2" in m for m in res.rounds[1].misanchored), res.rounds[1].misanchored
assert (res.rounds[1].directory / "dropped-points.md").exists()
print("dropped prior point flagged:", [m for m in res.rounds[1].misanchored if "W2" in m][0])

# --- and the detector itself ---------------------------------------------
sr = ScoredReview(reviewer_id="R1", persona="p", review=Review(
    version_reviewed="v2", summary="s", decision_critical=[],
    prior_points=[PriorPointVerdict(label="W1", verdict="unresolved", evidence="§3")],
    score_change="held", points=[], soundness=3, novelty=3, clarity=3, overall=4,
    confidence=4, recommendation="major_revision"))
assert dropped_prior_points([sr], {"R1": ["W1", "W2"]}) == [
    "R1-W2 was raised last round and not revisited"]
assert dropped_prior_points([sr], {}) == [], "a first review has nothing to drop"
print("CONTINUITY OK")
