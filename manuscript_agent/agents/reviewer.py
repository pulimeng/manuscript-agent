"""The reviewer agent: reads a manuscript and returns a structured review."""

from __future__ import annotations

from ..config import Persona, Venue
from ..topics import TOPICS, Topic
from ..llm import LLM
from ..llm import Attachment
from ..manuscript import Manuscript
from ..schemas import Review, ScoredReview

SYSTEM = """You are {name}, serving as Reviewer {rid} for the following venue.

{venue}

Your review lens: {focus}
Your disposition: {disposition}
Your background: {expertise}
{standards}

Rules of engagement:
- Read the whole manuscript before judging it. Quote or name the specific section, figure,
  equation or sentence each point refers to; a critique that could apply to any paper is
  worthless.
- Distinguish what the manuscript demonstrates from what it asserts. Score soundness on the
  former only.
- Mark a point 'blocking' only if the central claim fails without it.
- Do not reward or punish the writing style beyond its effect on clarity.
- You are reviewing for {vname}. Calibrate to that acceptance bar, not to a general
  impression of effort.
- Do not invent facts about prior work. If you suspect a related paper exists but are not
  certain, phrase it as a question to the authors, not as a factual claim."""

FIRST = """Review the manuscript below.

<manuscript format="{fmt}">
{text}
</manuscript>

Produce your review."""

ANCHOR = """You are reviewing this exact version:

  {stamp}

Every point you raise must carry that version id verbatim in its `version` field, and the
page of the attached PDF where the issue appears. A criticism the authors cannot locate is
not actionable, and the editor will discard it.

<available_artifacts>
{artifacts}
</available_artifacts>

When a point concerns code, data or supplementary material, set `artifact_status` honestly.
If the artifact is listed above, you simply cannot open it from a PDF — record that as
'provided_but_i_could_not_access' and do not hold it against the authors or let it lower your
soundness score. Reserve 'authors_did_not_provide' for material that is genuinely absent.

"""

FIRST_PDF = """The submitted manuscript is attached as a PDF — the compiled article exactly
as the authors submitted it, with its figures, tables, captions and layout.

Judge the display items as well as the prose: whether each figure shows what its caption
claims, whether axes and units are labelled, whether tables report dispersion, and whether
anything essential is missing from the presentation.

Produce your review."""

REREVIEW_PDF = """You reviewed an earlier version of this manuscript. Your previous review and
the authors' response letter are below; the revised manuscript is attached as a PDF.

<your_previous_review>
{previous}
</your_previous_review>

<author_response>
{response}
</author_response>

Re-review the revised manuscript. For each of your previous points, judge whether it was
actually addressed in the article — not merely promised in the response letter. Verify claimed
changes against the attached PDF, figures included. Raise your scores where the revision
genuinely fixed something, hold them where it did not, and lower them if the revision
introduced new problems. Do not re-raise resolved points, and do not manufacture new blocking
points to justify a low score."""

REREVIEW = """You reviewed an earlier version of this manuscript. Here is your previous
review, the authors' response letter, and the revised manuscript.

<your_previous_review>
{previous}
</your_previous_review>

<author_response>
{response}
</author_response>

<revised_manuscript format="{fmt}">
{text}
</revised_manuscript>

Re-review the revised manuscript. For each of your previous points, judge whether it was
actually addressed in the text — not merely promised in the response letter. Verify claimed
edits against the manuscript. Raise your scores where the revision genuinely fixed something,
hold them where it did not, and lower them if the revision introduced new problems. Do not
re-raise points that are now resolved, and do not manufacture new blocking points to justify
a low score."""


class ReviewerAgent:
    def __init__(self, llm: LLM, venue: Venue, topic: Topic | None = None) -> None:
        self.llm = llm
        self.venue = venue
        self.topic = topic or TOPICS["general"]

    def _system(self, persona: Persona) -> str:
        standards = self.topic.brief()
        return SYSTEM.format(
            standards=f"\n{standards}\n" if standards else "",
            name=persona.name,
            rid=persona.id,
            venue=self.venue.brief(),
            vname=self.venue.name,
            focus=persona.focus,
            disposition=persona.disposition,
            expertise=persona.expertise,
        )

    def review(
        self,
        manuscript: Manuscript,
        persona: Persona,
        previous_review_md: str | None = None,
        response_letter: str | None = None,
        pdf: Attachment | None = None,
        stamp: str = "",
        artifacts: str = "",
    ) -> ScoredReview:
        """Read the submitted PDF when there is one; fall back to the sources otherwise."""
        resubmission = bool(previous_review_md and response_letter)
        if pdf:
            prompt = (
                REREVIEW_PDF.format(previous=previous_review_md, response=response_letter)
                if resubmission
                else FIRST_PDF
            )
        elif resubmission:
            prompt = REREVIEW.format(
                previous=previous_review_md,
                response=response_letter,
                fmt=manuscript.fmt,
                text=manuscript.text,
            )
        else:
            prompt = FIRST.format(fmt=manuscript.fmt, text=manuscript.text)
        if stamp:
            prompt = ANCHOR.format(stamp=stamp, artifacts=artifacts or "none") + prompt
        extra = {"documents": [pdf]} if pdf else {}
        review = self.llm.parse(self._system(persona), prompt, Review, **extra)
        return ScoredReview(reviewer_id=persona.id, persona=persona.name, review=review)
