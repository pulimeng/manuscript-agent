"""Reviews should read like a referee's, not a checklist: few decisive points, honest asks."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manuscript_agent.agents.editor import EditorAgent
from manuscript_agent.agents.reviewer import ReviewerAgent
from manuscript_agent.config import VENUES, personas
from manuscript_agent.pipeline import overweighted_reviews, panel_correlation
from manuscript_agent.providers import ModelSpec as M
from manuscript_agent.render import review_md
from manuscript_agent.schemas import Review, ReviewPoint, ScoredReview


def point(label, ask, verification="verified_in_manuscript", reword=False):
    return ReviewPoint(label=label, kind="weakness", version="v1", section="§3", page=4,
                       comment=f"comment for {label}", ask=ask, evidence="p.4, Table 2",
                       verification=verification, resolvable_by_rewording=reword,
                       artifact_status="not_applicable")


def review(points, critical):
    return ScoredReview(reviewer_id="R1", persona="p", review=Review(
        version_reviewed="v1", summary="s", decision_critical=critical, prior_points=[],
        score_change="n/a", points=points, soundness=3, novelty=3, clarity=3, overall=5,
        confidence=4, recommendation="major_revision"))


# --- the taxonomy exists and the report is organised by it ---------------
sr = review([point("W1", "fatal"), point("W2", "revision", reword=True),
             point("W3", "clarification", "not_verifiable_from_pdf"),
             point("W4", "optional_experiment")], ["W1"])
md = review_md(sr)
for heading in ("Decision-critical", "Fatal", "Revision required", "Clarifications",
                "Optional experiments"):
    assert heading in md, f"report lacks {heading}"
assert "resolvable by rewording" in md and "not verifiable from pdf" in md
assert "checked: p.4, Table 2" in md
print("report groups by ask, flags unverified and reword-resolvable points")

# --- more than two decision-critical points is caught --------------------
assert overweighted_reviews([review([point("W1", "fatal")], ["W1"])]) == []
over = review([point(f"W{i}", "revision") for i in range(1, 5)], ["W1", "W2", "W3"])
flags = overweighted_reviews([over])
assert flags and "3 decision-critical" in flags[0], flags
ghost = review([point("W1", "revision")], ["W9"])
assert any("raised no such point" in f for f in overweighted_reviews([ghost]))
print("decision-critical cap enforced:", flags[0])

# --- a same-model panel is labelled correlated ---------------------------
same = panel_correlation([M.parse("claude-opus-5")] * 4)
assert "correlated samples" in same and "not three times as likely" in same
family = panel_correlation([M.parse("claude-opus-5"), M.parse("claude-sonnet-5")] * 2)
assert "one model family" in family, family
mixed = panel_correlation([M.parse("claude-opus-5"), M.parse("openai:gpt-5.4")] * 2)
assert "spans more than one model family" in mixed, mixed
print("panel correlation labelled:", same.split(".")[0])

# --- the instructions the models actually receive ------------------------
rsys = ReviewerAgent(None, VENUES["cs-conference"])._system(personas(1)[0])
for needle in ("at most two decision-critical", "Verify before you allege",
               "at its stated scope", "a sentence would fix",
               "do not invent venue requirements"):
    assert needle.lower() in rsys.lower(), f"reviewer prompt lacks: {needle}"

esys = EditorAgent(None, VENUES["cs-conference"]).__class__
from manuscript_agent.agents.editor import SYSTEM as ESYS
for needle in ("Merge duplicates", "not independent corroboration",
               "Weigh by verification", "Reject sparingly", "cannot repair"):
    assert needle.lower() in ESYS.lower(), f"editor prompt lacks: {needle}"
print("reviewer and editor prompts carry the calibration rules")
print("CALIBRATION OK")
