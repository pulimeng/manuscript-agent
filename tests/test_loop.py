"""Full-loop offline test: stubbed model, 2 rounds, accept, artifacts + fence stripping."""
import sys, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manuscript_agent.config import RunConfig, VENUES
from manuscript_agent.manuscript import Manuscript
from manuscript_agent.pipeline import SubmissionPipeline
from manuscript_agent.schemas import (Review, ReviewPoint, MetaReview, RevisionPlan,
                                      RevisionItem)
VID = {"v": "v1"}
ROUND = {"n": 0}

class StubLLM:
    def parse(self, system, prompt, schema, max_tokens=16000):
        assert any(t in prompt for t in ("<manuscript", "<revised_manuscript", "<reviews>"))
        if schema is Review:
            return Review(version_reviewed=VID["v"], summary="s", prior_points=[],
                          score_change="n/a", points=[ReviewPoint(label="W1", kind="weakness", version=VID["v"], page=1,
                          artifact_status="not_applicable", section="§3", comment="c", severity="major")], soundness=3,
                          novelty=3, clarity=3, overall=5, confidence=4,
                          recommendation="major_revision")
        if schema is MetaReview:
            ROUND["n"] += 1
            return MetaReview(version_reviewed=VID["v"], summary="m", consensus_strengths=["a"],
                              critical_issues=["fix §3"], optional_issues=[],
                              decision="accept" if ROUND["n"] >= 2 else "major_revision",
                              rationale="r", guidance_to_authors=["g"])
        if schema is RevisionPlan:
            return RevisionPlan(strategy="st", items=[RevisionItem(refs=["R1-W1"],
                                critical_issues=[1], section="§3", action="do x",
                                stance="accept")], out_of_scope=[])
        raise AssertionError(schema)
    def text(self, system, prompt, max_tokens=None, documents=None):
        if "<diff_of_changes>" in prompt:
            return "letter"
        return ("%%% FILE: paper.md %%%\n# Title\n\nRevised body.\n"
                "%%% END FILE: paper.md %%%\n")

tmp = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp") / "ma-test-loop"; shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True)
src = tmp / "paper.md"; src.write_text("# Title\n\nOriginal body.\n")
cfg = RunConfig(venue=VENUES["cs-conference"], rounds=3, reviewer_count=3)
res = SubmissionPipeline(cfg, llm=StubLLM(), on_event=lambda m: None).run(
    Manuscript.load(src), tmp / "runs")
assert res.decision == "accept" and len(res.rounds) == 2, (res.decision, len(res.rounds))
assert src.read_text() == "# Title\n\nRevised body.\n"
assert not res.rounds[0].unaddressed
print("SMOKE OK — decision", res.decision, "in", len(res.rounds), "rounds")
