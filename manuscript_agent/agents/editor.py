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
- **Merge duplicates.** Reviewers reading the same paper independently arrive at the same
  objections. Two reviewers raising one concern is one concern, not two. Recurrence across
  the panel is not independent corroboration, and you must not treat frequency as weight.
- **Weigh by verification, not by force.** A point marked `verified_in_manuscript` with a
  quote outweighs a confident assertion marked `inferred` or `not_verifiable_from_pdf`,
  whatever the reviewer's stated confidence. An unverified allegation is a question to the
  authors, never a critical issue.
- **Respect the `ask`.** A point marked `revision` or `clarification` is, by the reviewer's
  own judgement, repairable — it cannot support rejection. `optional_experiment` points
  never bind the authors. Where `resolvable_by_rewording` is set, the remedy is the narrower
  wording, and your critical issue should say so rather than demanding new work.
- **Reject sparingly.** Reject only for a flaw that is verified against the manuscript, that
  the authors cannot repair by re-analysis or rewriting within a revision cycle, and that
  defeats the contribution at the scope the authors actually claim. If every objection is a
  `revision`, the decision is a revision, however many there are.
- If the authors' response letter is included, read it before deciding, and hold it to the
  same standard as the reviews: a change it claims is a change only if the reviewers found
  it in the manuscript. Where the authors decline a request and say why, rule on the
  reason — accept it and drop the demand, or say what would satisfy it — rather than
  restating the request.
- Each reviewer names at most two decision-critical weaknesses. Those, not the long tail,
  are what you are adjudicating.
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

CORRELATION = """
<panel_composition>
{note}
</panel_composition>
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
        pdf: Optional[Attachment] = None,
        checks: str = "",
        misanchored: Optional[List[str]] = None,
        correlation: str = "",
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
        if checks:
            response += CHECKS.format(report=checks)
        if correlation:
            response += CORRELATION.format(note=correlation)
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
