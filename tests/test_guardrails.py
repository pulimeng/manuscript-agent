"""Guardrail test: author declares an experiment impossible; check the editor is told."""
import sys, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manuscript_agent.config import RunConfig, VENUES
from manuscript_agent.manuscript import Manuscript
from manuscript_agent.pipeline import SubmissionPipeline, unaddressed_issues
from manuscript_agent.schemas import (Review, ReviewPoint, MetaReview, RevisionPlan,
                                      RevisionItem)

SEEN = {"editor_prompts": []}

VID = {"v": "v1"}


class StubLLM:
    def parse(self, system, prompt, schema, max_tokens=16000):
        if schema is Review:
            return Review(version_reviewed=VID["v"], summary="s", prior_points=[],
                          score_change="n/a", points=[ReviewPoint(label="W1", kind="weakness", version=VID["v"], page=1,
                          artifact_status="not_applicable", section="§4", comment="run a prospective trial", severity="blocking")],
                          soundness=2, novelty=4, clarity=4, overall=4, confidence=5,
                          recommendation="major_revision")
        if schema is MetaReview:
            SEEN["editor_prompts"].append(prompt)
            return MetaReview(version_reviewed=VID["v"], summary="m", consensus_strengths=["a"],
                              critical_issues=["run a prospective clinical trial",
                                               "report calibration curves",
                                               "discuss site B vendor bias"],
                              optional_issues=[], decision="major_revision", rationale="r",
                              guidance_to_authors=["g"])
        if schema is RevisionPlan:
            # addresses CI2 only; declines CI1; silently drops CI3
            return RevisionPlan(
                strategy="scope the claim",
                items=[RevisionItem(refs=["R1-W1"], section="§5", action="add calibration",
                                    stance="accept", critical_issues=[2])],
                out_of_scope=["a prospective clinical trial requires new patient recruitment "
                              "and ethics approval; we scope the claim to retrospective data"],
            )
        raise AssertionError(schema)
    def text(self, system, prompt, max_tokens=None, documents=None):
        if "<diff_of_changes>" in prompt:
            return "letter"
        return ("%%% FILE: paper.md %%%\n# T\n\nrevised with limitations section.\n"
                "%%% END FILE: paper.md %%%\n")

tmp = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp") / "ma-test-guardrails"
shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True)
src = tmp / "paper.md"; src.write_text("# T\n\nbody\n")

cfg = RunConfig(venue=VENUES["biomed-journal"], rounds=2, reviewer_count=2)
res = SubmissionPipeline(cfg, llm=StubLLM(), on_event=print).run(Manuscript.load(src), tmp / "runs")

r1 = res.rounds[0]
print("\nunaddressed detected:", r1.unaddressed)
assert r1.unaddressed == ["[3] discuss site B vendor bias"], r1.unaddressed

p2 = SEEN["editor_prompts"][1]
for tag in ("<author_response_to_previous_round>", "<declared_out_of_scope>",
            "<unaddressed_critical_issues>"):
    assert tag in p2, f"round-2 editor prompt missing {tag}"
    print("round-2 editor sees", tag)
assert "prospective clinical trial requires new patient recruitment" in p2
assert "site B vendor bias" in p2.split("<unaddressed_critical_issues>")[1]

files = sorted(x.name for x in (res.directory / "round-1").iterdir())
print("\nround-1 artifacts:", files)
assert "out-of-scope.md" in files and "unaddressed.md" in files
print("\nGUARDRAIL OK")
