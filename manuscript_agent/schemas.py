"""Structured payloads exchanged between the author, reviewer and editor agents."""

from typing import List, Literal

from pydantic import BaseModel, Field

Recommendation = Literal["accept", "minor_revision", "major_revision", "reject"]

# What the point actually asks of the authors. A real reviewer distinguishes these; a
# checklist does not, which is why 'major' ends up meaning nothing.
Ask = Literal[
    "fatal",                # the contribution does not survive this, and no revision repairs it
    "revision",             # fixable by rewriting, re-analysis, or reporting what already exists
    "clarification",        # you could not tell from the text; the authors may already have it
    "optional_experiment",  # would strengthen the paper; not required for this claim to stand
]

Verification = Literal[
    "verified_in_manuscript",     # you found it and can quote it
    "not_verifiable_from_pdf",    # it may be true; you cannot check it from what you were given
    "inferred",                   # your reading, not something the text states
]
Score5 = Literal[1, 2, 3, 4, 5]
Score10 = Literal[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]


ArtifactStatus = Literal[
    "not_applicable",
    "authors_did_not_provide",
    "provided_but_i_could_not_access",
]


class ReviewPoint(BaseModel):
    """One numbered critique, anchored to a version and a place in it."""

    label: str = Field(description="Short stable id for this point, e.g. 'W1', 'Q2'.")
    kind: Literal["strength", "weakness", "question", "minor"]
    version: str = Field(
        description="The manuscript version this point refers to, copied exactly from the "
        "version stamp you were given, e.g. 'v2'."
    )
    section: str = Field(description="Section/figure/table the point is about, or 'general'.")
    page: int = Field(
        description="Page of the submitted PDF where this appears; 0 only if the point "
        "genuinely applies to the whole manuscript."
    )
    comment: str = Field(description="The critique itself, specific and actionable.")
    ask: Ask = Field(
        description="What you are actually asking for. Use 'fatal' only when no revision or "
        "re-analysis of existing material could repair it. If narrower wording of the claim "
        "would resolve your objection, it is a 'revision', not a 'fatal'."
    )
    evidence: str = Field(
        description="What you checked, quoted or located precisely — 'p.5, Table 2 reports "
        "n=104' — or, if you could not check it, say so plainly."
    )
    verification: Verification = Field(
        description="Whether you verified this against the manuscript, could not verify it "
        "from the PDF, or are inferring it. An unverified allegation may not be 'fatal'."
    )
    resolvable_by_rewording: bool = Field(
        description="True if scoping the claim to what the evidence supports would resolve "
        "this, without new experiments."
    )
    artifact_status: ArtifactStatus = Field(
        description=(
            "'authors_did_not_provide' if the manuscript never offers the code, data or "
            "supplementary material in question; 'provided_but_i_could_not_access' if it is "
            "listed in the available artifacts but you could not inspect it from the PDF "
            "alone — that is a limit of your access, not a fault of the authors, and must "
            "not be scored as one. 'not_applicable' for every point that is not about "
            "artifacts."
        )
    )


class PriorPointVerdict(BaseModel):
    """What became of one point from your own previous review."""

    label: str = Field(description="The label from your previous review, e.g. 'W2'.")
    verdict: Literal["resolved", "partially_resolved", "unresolved", "withdrawn"] = Field(
        description="'withdrawn' only if you now believe the point was mistaken."
    )
    evidence: str = Field(
        description="Where in the current version you checked, and what you found there. "
        "Cite the text, not the response letter."
    )


class Review(BaseModel):
    version_reviewed: str = Field(
        description="The version id you were given, copied exactly, e.g. 'v2'."
    )
    summary: str = Field(description="Neutral summary of what the manuscript claims and does.")
    decision_critical: List[str] = Field(
        description="At most TWO labels from your points: the ones that actually determine "
        "your recommendation. If everything matters, nothing does — choose."
    )
    prior_points: List[PriorPointVerdict] = Field(
        description="One entry for every point in your previous review, if you were given "
        "one. Empty list on a first review."
    )
    score_change: str = Field(
        description="If you reviewed an earlier version, say what moved your overall score "
        "since then, or why it held. 'n/a' on a first review."
    )
    points: List[ReviewPoint]
    soundness: Score5
    novelty: Score5
    clarity: Score5
    overall: Score10
    confidence: Score5
    recommendation: Recommendation


class ScoredReview(BaseModel):
    """A Review plus the identity of the reviewer that produced it."""

    reviewer_id: str
    persona: str
    review: Review


class MetaReview(BaseModel):
    summary: str = Field(description="Where the reviewers agree and where they conflict.")
    consensus_strengths: List[str]
    critical_issues: List[str] = Field(
        description="Must-fix items, in priority order. These bind the revision."
    )
    optional_issues: List[str] = Field(description="Nice-to-have items the authors may decline.")
    decision: Recommendation
    rationale: str
    guidance_to_authors: List[str]


class RevisionItem(BaseModel):
    refs: List[str] = Field(description="Review point labels this addresses, e.g. ['R1-W2'].")
    critical_issues: List[int] = Field(
        description=(
            "1-based indices of the editor's critical_issues this item addresses. "
            "Empty only if the item addresses none of them."
        )
    )
    section: str
    action: str = Field(description="Concrete edit to make, not a restatement of the critique.")
    stance: Literal["accept", "partially_accept", "rebut"] = Field(
        description="'rebut' means argue the point instead of editing."
    )


class RevisionPlan(BaseModel):
    strategy: str = Field(description="How this revision round is framed overall.")
    items: List[RevisionItem]
    out_of_scope: List[str] = Field(
        description="Requests that cannot be satisfied without new data/experiments."
    )
