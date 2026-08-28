"""The author agent: drafts the manuscript, plans the revision, rewrites it, replies."""

from __future__ import annotations

from typing import List

from ..config import Venue
from ..llm import LLM
from ..manuscript import Manuscript
from ..render import meta_md, plan_md, reviews_md
from ..schemas import MetaReview, RevisionPlan, ScoredReview

SYSTEM = """You are the corresponding author of a manuscript under submission to:

{venue}

You write like a working researcher, not like a press release. You state what you did, what
it shows, and what it does not show. You do not pad, you do not hedge into meaninglessness,
and you never claim results you do not have.

The manuscript is a {fmt} document. Follow exactly the output format each task specifies,
and emit nothing besides it — no preamble, no commentary, no surrounding code fence. What you
emit must be valid {fmt}."""

DRAFT = """Write the first full draft of the manuscript from this brief.

<brief>
{brief}
</brief>

Include every section the venue expects (title, abstract, introduction, related work,
methods, results, discussion, limitations, conclusion — adapted to the venue's conventions).
{length}

Where the brief does not supply a number, a citation or a result, do NOT invent one: leave an
explicit placeholder of the form [TODO: ...] describing exactly what is needed. Placeholders
are acceptable in a first draft; fabricated evidence is not.

Emit the complete {fmt} file."""

PLAN = """The reviews and the editor's decision on your manuscript are below.

<manuscript format="{fmt}">
{text}
</manuscript>

<reviews>
{reviews}
</reviews>

<editor_decision>
{meta}
</editor_decision>

Plan the revision before writing it.

Every critical issue the editor listed must map to at least one plan item, recorded in
that item's 'critical_issues' field by its 1-based index in the editor's list. If you cannot
address one, it must appear in 'out_of_scope' with the reason — silently skipping it is the
one thing you must not do. For each item say
what edit you will actually make — 'clarify the contribution' is not an action, 'move the
three contribution bullets from §4 into the end of §1 and drop the third, which the
experiments do not support' is. Use stance 'rebut' only where the reviewer is factually wrong
or the request is out of scope, and be honest in 'out_of_scope' about what would need new
data or experiments.

Where a request cannot be met with the evidence you have — a reviewer asks for an experiment
you cannot run, data you do not hold, or a study of a scale you have not done — do not
pretend otherwise and do not weaken the manuscript to dodge it. Put it in 'out_of_scope',
state precisely what would be required, and plan the honest alternative: scope the claim to
what you did show, and add the gap to the limitations section. That is a legitimate outcome
of a revision round, not a failure."""

REVISE = """Execute this revision plan on the manuscript.

<manuscript format="{fmt}">
{text}
</manuscript>

<revision_plan>
{plan}
</revision_plan>

<editor_decision>
{meta}
</editor_decision>

Rules:
- Make every edit the plan calls for, at the places the plan names.
- Leave everything the plan does not touch byte-for-byte unchanged. Do not restructure,
  re-word or re-format sections you were not asked to change.
- Do not add results, numbers, citations or experiments that do not exist. If a reviewer asks
  for evidence you do not have, say so in the text (limitations) and rebut in the letter —
  never fabricate it to satisfy a reviewer.
- Keep the document compiling: balanced environments, intact references and labels.

{emit}"""

LETTER = """Write the response letter accompanying your revised manuscript.

<reviews>
{reviews}
</reviews>

<editor_decision>
{meta}
</editor_decision>

<revision_plan>
{plan}
</revision_plan>

<diff_of_changes>
{diff}
</diff_of_changes>

Write it in the standard form: a short paragraph to the editor, then each reviewer in turn,
each of their points quoted by its label, followed by your response. State concretely what
changed and where (section, figure, table). Where you are rebutting, argue the substance
courteously and without evasion. Where you could not comply, say so plainly and explain why.
Claim nothing the diff does not show. Markdown."""


FIX = """An automated check compared your revised manuscript against the version the
reviewers read. The values below appear in your revision but cannot be traced to the earlier
text, meaning they were introduced during this revision round.

<manuscript format="{fmt}">
{text}
</manuscript>

<unsourced_values>
{report}
</unsourced_values>

For each one, do exactly one of the following:

- If it was copied or derived from data already in the manuscript, leave it and make its
  provenance explicit in the text so the derivation is visible.
- If it is a real result you hold but had not previously written down, leave it and state
  where it comes from (which experiment, which table).
- If it is not a value you actually have — a number produced to satisfy a reviewer, a
  citation you cannot verify exists — remove it. Replace the claim with what you can support,
  or with an explicit [TODO: ...] naming the evidence that would be needed. Removing an
  unsupported number and admitting the gap is always the correct action; keeping it is not.

Structural numbering (section, figure and table numbers) is not a finding — ignore any of
those that appear in the list.

{emit}"""


BUILD_FIX = """Your revision no longer compiles. The submission is the compiled PDF, so
until this builds there is nothing to submit.

<manuscript format="{fmt}">
{text}
</manuscript>

<compiler_output>
{log}
</compiler_output>

Fix the build. Work from the first error — later ones are usually consequences of it. Do not
change the substance of the manuscript while doing so: this is a repair, not a revision.
Common causes are an unbalanced environment, a stray brace, a command used without its
package, or a reference to a file that is not in the package.

{emit}"""


class AuthorAgent:
    def __init__(self, llm: LLM, venue: Venue) -> None:
        self.llm = llm
        self.venue = venue

    def _system(self, fmt: str) -> str:
        return SYSTEM.format(venue=self.venue.brief(), fmt=fmt)

    def draft(self, brief: str, fmt: str = "Markdown") -> str:
        prompt = DRAFT.format(brief=brief, fmt=fmt, length=self.venue.length_guidance)
        return self.llm.text(self._system(fmt), prompt)

    def plan(
        self, manuscript: Manuscript, reviews: List[ScoredReview], meta: MetaReview
    ) -> RevisionPlan:
        prompt = PLAN.format(
            fmt=manuscript.fmt,
            text=manuscript.text,
            reviews=reviews_md(reviews),
            meta=meta_md(meta),
        )
        return self.llm.parse(self._system(manuscript.fmt), prompt, RevisionPlan)

    def revise(self, manuscript: Manuscript, plan: RevisionPlan, meta: MetaReview) -> str:
        prompt = REVISE.format(
            fmt=manuscript.fmt,
            text=manuscript.text,
            plan=plan_md(plan),
            meta=meta_md(meta),
            emit=manuscript.emit_instructions,
        )
        return self.llm.text(self._system(manuscript.fmt), prompt)

    def fix_unsourced(self, manuscript: Manuscript, report: str) -> str:
        prompt = FIX.format(
            fmt=manuscript.fmt,
            text=manuscript.text,
            report=report,
            emit=manuscript.emit_instructions,
        )
        return self.llm.text(self._system(manuscript.fmt), prompt)

    def fix_build(self, manuscript, log: str) -> str:
        prompt = BUILD_FIX.format(
            fmt=manuscript.fmt,
            text=manuscript.text,
            log=log,
            emit=manuscript.emit_instructions,
        )
        return self.llm.text(self._system(manuscript.fmt), prompt)

    def response_letter(
        self,
        reviews: List[ScoredReview],
        meta: MetaReview,
        plan: RevisionPlan,
        diff: str,
        fmt: str = "Markdown",
    ) -> str:
        prompt = LETTER.format(
            reviews=reviews_md(reviews),
            meta=meta_md(meta),
            plan=plan_md(plan),
            diff=diff or "(no textual changes)",
        )
        return self.llm.text(self._system(fmt), prompt, max_tokens=32000)
