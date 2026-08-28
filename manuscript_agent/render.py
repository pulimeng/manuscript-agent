"""Human-readable rendering of the structured review objects."""

from __future__ import annotations

from typing import Iterable

from .schemas import MetaReview, RevisionPlan, ScoredReview


def review_md(sr: ScoredReview) -> str:
    r = sr.review
    lines = [
        f"### Reviewer {sr.reviewer_id} — {sr.persona}",
        "",
        f"**Recommendation:** {r.recommendation}  ",
        f"**Scores:** soundness {r.soundness}/5 · novelty {r.novelty}/5 · "
        f"clarity {r.clarity}/5 · overall {r.overall}/10 · confidence {r.confidence}/5",
        "",
        "**Summary**",
        "",
        r.summary,
        "",
    ]
    for kind, title in (
        ("strength", "Strengths"),
        ("weakness", "Weaknesses"),
        ("question", "Questions to the authors"),
        ("minor", "Minor points"),
    ):
        pts = [p for p in r.points if p.kind == kind]
        if not pts:
            continue
        lines += [f"**{title}**", ""]
        for p in pts:
            lines.append(
                f"- `{sr.reviewer_id}-{p.label}` ({p.severity}, {p.section}) — {p.comment}"
            )
        lines.append("")
    return "\n".join(lines)


def reviews_md(reviews: Iterable[ScoredReview]) -> str:
    return "\n".join(review_md(r) for r in reviews)


def meta_md(m: MetaReview) -> str:
    lines = [
        "### Meta-review",
        "",
        f"**Decision:** {m.decision}",
        "",
        m.summary,
        "",
        "**Consensus strengths**",
        "",
    ]
    lines += [f"- {s}" for s in m.consensus_strengths] or ["- (none recorded)"]
    lines += ["", "**Critical issues (must fix)**", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(m.critical_issues, 1)] or ["(none)"]
    if m.optional_issues:
        lines += ["", "**Optional issues**", ""]
        lines += [f"- {s}" for s in m.optional_issues]
    lines += ["", "**Guidance to authors**", ""]
    lines += [f"- {s}" for s in m.guidance_to_authors] or ["- (none)"]
    lines += ["", "**Rationale**", "", m.rationale, ""]
    return "\n".join(lines)


def plan_md(p: RevisionPlan) -> str:
    lines = ["### Revision plan", "", p.strategy, ""]
    for i, item in enumerate(p.items, 1):
        refs = ", ".join(item.refs) or "—"
        ci = ", ".join(f"CI{n}" for n in item.critical_issues)
        tag = f"{refs}; {ci}" if ci else refs
        lines.append(f"{i}. [{item.stance}] ({item.section}; addresses {tag}) {item.action}")
    if p.out_of_scope:
        lines += ["", "**Out of scope for this revision**", ""]
        lines += [f"- {s}" for s in p.out_of_scope]
    return "\n".join(lines) + "\n"
