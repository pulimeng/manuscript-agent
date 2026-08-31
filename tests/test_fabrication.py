"""Fabrication-check test: the author invents a result; verify detect -> repair -> escalate."""
import sys, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manuscript_agent.config import RunConfig, VENUES
from manuscript_agent.manuscript import Manuscript
from manuscript_agent.pipeline import SubmissionPipeline, FabricationError
from manuscript_agent.schemas import (Review, ReviewPoint, MetaReview, RevisionPlan,
                                      RevisionItem)

def block(body):
    return f"%%% FILE: paper.md %%%\n{body}%%% END FILE: paper.md %%%\n"


ORIGINAL = "# Paper\n\nMacro-F1 is 0.74 on the held-out set (Table 2).\n"
# the revision invents a number, a p-value and a citation
FABRICATED = ("# Paper\n\nMacro-F1 is 0.74 on the held-out set (Table 2), rising to 0.91 "
              "with retrieval (p < 0.001) \\cite{ghost2024}.\n")
HONEST = ("# Paper\n\nMacro-F1 is 0.74 on the held-out set (Table 2). Retrieval was not "
          "evaluated in this version.\n")

VID = {"v": "v1"}


class StubLLM:
    def __init__(self, repair): self.repair, self.editor_prompts, self.calls = repair, [], []
    def parse(self, system, prompt, schema, max_tokens=16000):
        if schema is Review:
            return Review(version_reviewed=VID["v"], summary="s", prior_points=[],
                          score_change="n/a", points=[ReviewPoint(label="W1", kind="weakness", version=VID["v"], page=1,
                          artifact_status="not_applicable", section="§3", comment="no retrieval ablation", severity="blocking")],
                          soundness=2, novelty=3, clarity=4, overall=4, confidence=4,
                          recommendation="major_revision")
        if schema is MetaReview:
            self.editor_prompts.append(prompt)
            return MetaReview(version_reviewed=VID["v"], summary="m", consensus_strengths=["a"],
                              critical_issues=["report a retrieval ablation"],
                              optional_issues=[], decision="major_revision", rationale="r",
                              guidance_to_authors=["g"])
        if schema is RevisionPlan:
            return RevisionPlan(strategy="st", items=[RevisionItem(refs=["R1-W1"],
                                critical_issues=[1], section="§2", action="add ablation",
                                stance="accept")], out_of_scope=[])
        raise AssertionError(schema)
    def text(self, system, prompt, max_tokens=None, documents=None):
        # the response-letter prompt embeds the revision plan, so test it first
        self.calls.append("letter" if "<diff_of_changes>" in prompt else
                          "fix" if "<unsourced_values>" in prompt else "revise")
        if self.calls[-1] == "revise":
            return block(FABRICATED)
        if self.calls[-1] == "fix":
            return block(HONEST if self.repair else FABRICATED)
        return "letter"

def run(policy, repair, tag):
    tmp = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp") / f"ma-test-fab-{tag}"
    shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True)
    src = tmp / "paper.md"; src.write_text(ORIGINAL)
    cfg = RunConfig(venue=VENUES["cs-conference"], rounds=2,
                    reviewer_count=2, on_fabrication=policy)
    llm = StubLLM(repair)
    res = SubmissionPipeline(cfg, llm=llm, on_event=print).run(Manuscript.load(src), tmp/"runs")
    return res, llm, src

print("### policy=retry, author repairs")
res, llm, src = run("retry", True, "repair")
r1 = res.rounds[0]
assert llm.calls[:2] == ["revise", "fix"], llm.calls
assert r1.integrity == [], r1.integrity
assert "0.91" not in src.read_text() and "not evaluated" in src.read_text()
assert "No unsourced" in (r1.directory / "integrity.md").read_text()
assert "<integrity_report>" not in llm.editor_prompts[1]
print("-> repaired, nothing escalated\n")

print("### policy=retry, author refuses to repair")
res, llm, src = run("retry", False, "stubborn")
r1 = res.rounds[0]
assert set(r1.integrity) == {"0.91", "0.001", "ghost2024"}, r1.integrity
assert not r1.promoted, "a candidate that invents values must not be promoted"
assert not res.merged and (res.directory / "MERGE_STATUS").read_text().startswith("UNMERGED")
assert "0.91" in (r1.directory / "integrity.md").read_text()
assert src.read_text() == ORIGINAL, "the manuscript must be untouched by a refused patch"
print("-> refused promotion, manuscript untouched, run ends unmerged\n")

print("### policy=fail")
try:
    run("fail", False, "fail")
    raise SystemExit("expected FabricationError")
except FabricationError as e:
    print("-> aborted:", e, "\n")

print("### policy=warn (no repair attempted)")
res, llm, src = run("warn", False, "warn")
assert "fix" not in llm.calls, llm.calls
assert set(res.rounds[0].integrity) == {"0.91", "0.001", "ghost2024"}
assert res.rounds[0].promoted, "warn records the finding but still merges"
print("-> recorded and merged anyway\n")
print("FABRICATION CHECK OK")
