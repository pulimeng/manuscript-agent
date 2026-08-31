"""The editor agent: adjudicates the reviews and issues the decision."""

from __future__ import annotations

from typing import List, Optional

from ..config import Venue
from ..llm import LLM
from ..llm import Attachment
from ..manuscript import Manuscript
from ..render import reviews_md
from ..schemas import MetaReview, ScoredReview

SYSTEM = """You are the handling editor (area chair) for the following venue.

{venue}

You have read the manuscript yourself and you have the reviews in front of you. Your job is
to adjudicate, not to average.

Rules of engagement:
- Weigh reviews by the evidence in them. A specific, verifiable critique outweighs a
  confident but unsupported one, whatever the reviewer's stated confidence.
- Where reviewers disagree, say which side you find correct and why. Do not paper over it.
- Discard reviewer points that are factually wrong about the manuscript, and say so
  explicitly so the authors are not asked to fix a non-problem.
- 'critical_issues' is a contract: it must be the complete set of things that, if fixed,
  would move this manuscript to acceptance. Keep it short and ordered by importance.
- Mechanical checks (build, page limit, undefined citations and references, missing
  figures, unresolved drafting markers) run automatically on every version. Their result is
  given to you. Do not spend a critical issue on anything the checks already cover, and do
  not accept a reviewer point that the checks contradict.
- A reviewer point marked 'provided_but_i_could_not_access' is a limit of the reviewer's
  access to a PDF, not a deficiency of the manuscript. Never turn one into a critical issue.
  A point marked 'authors_did_not_provide' is a real gap and may be one.
- On a resubmission, each review carries `prior_points`: that reviewer's own verdict on
  every point it raised last round. Weigh those over the fresh `points` list — a reviewer
  who marks its own blocking point resolved has said more than one who restates it. A point
  listed below as not revisited was dropped without a verdict; do not treat it as resolved.
- Points listed as misanchored named a version other than the one under review. Treat them
  as unverified and say so rather than binding the authors to them.
- If an author response is included, read it before deciding. Where the authors state
  that a request cannot be met — an experiment they cannot run, data they do not hold —
  judge that claim on its merits. If you accept it, you have two honest options: drop the
  demand and require instead that the claim be scoped and the gap stated in the limitations,
  or decide the manuscript cannot be accepted here. Re-issuing a demand you have accepted is
  impossible wastes the authors' round and is not a decision.
- If an integrity report is present, it lists values in the current manuscript that an
  automated check could not trace to the previously reviewed version and that the authors
  did not remove or source when asked. Unexplained quantitative claims are a serious
  concern: require the authors to state the provenance of each, and if the manuscript's
  central claim depends on one, that is grounds for rejection rather than revision.
- If unaddressed critical issues from your previous round are listed, establish which they
  are: silently ignored (hold the decision), or declined with a reason you find acceptable
  (drop them from critical_issues so they stop blocking).
- Choose 'reject' when the flaw is unfixable without work beyond a revision cycle
  (new data, a different method, a claim the evidence cannot support). Choose
  'major_revision' when the work is sound but the manuscript is not yet convincing.
  Choose 'accept' only when no critical issue remains.
- This is round {round} of at most {max_rounds}. {round_note}"""

FINAL_NOTE = (
    "This is the final round: your decision is terminal, so choose 'accept' or 'reject' "
    "unless the remaining issues are purely editorial."
)
NORMAL_NOTE = "Further revision rounds are available if the work warrants them."

PROMPT = """<manuscript format="{fmt}">
{text}
</manuscript>

<reviews>
{reviews}
</reviews>
{author_response}{history}
Adjudicate and issue your decision."""

PROMPT_PDF = """The submitted manuscript is attached as a PDF, exactly as the authors
submitted it.

<reviews>
{reviews}
</reviews>
{author_response}{history}
Adjudicate and issue your decision."""

AUTHOR_RESPONSE = """
<author_response_to_previous_round>
{letter}
</author_response_to_previous_round>
"""

DECLINED = """
<declared_out_of_scope>
The authors stated these requests cannot be met with the evidence available to them:
{items}
</declared_out_of_scope>
"""

CHECKS = """
<automated_checks>
Run against the version under review:
{report}
</automated_checks>
"""

MISANCHORED = """
<misanchored_points>
These reviewer points cite a version other than the one under review:
{items}
</misanchored_points>
"""

INTEGRITY = """
<integrity_report>
An automated check flagged these values as introduced during the last revision with no
antecedent in the previously reviewed version, and they survived a correction pass:
{items}
</integrity_report>
"""

UNADDRESSED = """
<unaddressed_critical_issues>
These critical issues from your previous decision were not mapped to any edit in the
authors' revision plan:
{items}
</unaddressed_critical_issues>
"""

HISTORY = """
<previous_decisions>
{history}
</previous_decisions>
"""


def _bullets(items: List[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


class EditorAgent:
    def __init__(self, llm: LLM, venue: Venue) -> None:
        self.llm = llm
        self.venue = venue

    def decide(
        self,
        manuscript: Manuscript,
        reviews: List[ScoredReview],
        round_no: int,
        max_rounds: int,
        history: str = "",
        response_letter: str = "",
        out_of_scope: Optional[List[str]] = None,
        unaddressed: Optional[List[str]] = None,
        integrity: Optional[List[str]] = None,
        pdf: Optional[Attachment] = None,
        checks: str = "",
        misanchored: Optional[List[str]] = None,
    ) -> MetaReview:
        system = SYSTEM.format(
            venue=self.venue.brief(),
            round=round_no,
            max_rounds=max_rounds,
            round_note=FINAL_NOTE if round_no >= max_rounds else NORMAL_NOTE,
        )
        response = ""
        if response_letter:
            response += AUTHOR_RESPONSE.format(letter=response_letter)
        if out_of_scope:
            response += DECLINED.format(items=_bullets(out_of_scope))
        if unaddressed:
            response += UNADDRESSED.format(items=_bullets(unaddressed))
        if integrity:
            response += INTEGRITY.format(items=_bullets(integrity))
        if checks:
            response += CHECKS.format(report=checks)
        if misanchored:
            response += MISANCHORED.format(items=_bullets(misanchored))
        fields = dict(
            reviews=reviews_md(reviews),
            author_response=response,
            history=HISTORY.format(history=history) if history else "",
        )
        prompt = (
            PROMPT_PDF.format(**fields)
            if pdf
            else PROMPT.format(fmt=manuscript.fmt, text=manuscript.text, **fields)
        )
        extra = {"documents": [pdf]} if pdf else {}
        return self.llm.parse(system, prompt, MetaReview, max_tokens=32000, **extra)
