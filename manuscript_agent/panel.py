"""Checks on the reviews themselves: anchoring, continuity, focus, and panel composition."""

from __future__ import annotations

import re
from typing import Dict, List

from .schemas import ScoredReview


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "manuscript"


def misanchored_points(reviews: List[ScoredReview], vid: str) -> List[str]:
    """Criticisms that name a version other than the one under review."""
    out = []
    for sr in reviews:
        for point in sr.review.points:
            if point.version and point.version.strip().lower() != vid.lower():
                out.append(
                    f"{sr.reviewer_id}-{point.label} cites {point.version!r}, "
                    f"not {vid}: {point.comment[:80]}"
                )
    return out


def dropped_prior_points(
    reviews: List[ScoredReview], previous: Dict[str, List[str]]
) -> List[str]:
    """Points a reviewer raised last round and did not account for this round.

    A re-review that quietly forgets its own criticism is indistinguishable from a fresh
    reviewer, which is exactly what a resubmission is not.
    """
    out = []
    for sr in reviews:
        before = set(previous.get(sr.reviewer_id, []))
        if not before:
            continue
        accounted = {p.label for p in sr.review.prior_points}
        for label in sorted(before - accounted):
            out.append(f"{sr.reviewer_id}-{label} was raised last round and not revisited")
    return out


def overweighted_reviews(reviews: List[ScoredReview], limit: int = 2) -> List[str]:
    """Reviewers that named more than `limit` decision-critical weaknesses, or phantom ones."""
    out = []
    for sr in reviews:
        chosen = sr.review.decision_critical
        labels = {p.label for p in sr.review.points}
        if len(chosen) > limit:
            out.append(
                f"{sr.reviewer_id} named {len(chosen)} decision-critical points "
                f"({', '.join(chosen)}); at most {limit} were asked for"
            )
        for label in chosen:
            if label not in labels:
                out.append(
                    f"{sr.reviewer_id} marked {label} decision-critical but raised no such point"
                )
    return out


def inaccessible_artifact_points(reviews: List[ScoredReview]) -> List[str]:
    """Points the reviewer itself marked as its own access limit, not an author failing."""
    return [
        f"{sr.reviewer_id}-{p.label}: {p.comment[:80]}"
        for sr in reviews
        for p in sr.review.points
        if p.artifact_status == "provided_but_i_could_not_access"
    ]


def panel_correlation(specs) -> str:
    """Say plainly when the panel is one model wearing four hats.

    Four samples from the same model are correlated draws, not four opinions. The editor is
    told so it cannot read agreement as corroboration.
    """
    names = [str(s) for s in specs]
    families = {n.split(":")[0] for n in names}  # provider is the family here
    if len(set(names)) == 1:
        return (
            f"All {len(names)} reviewers are the same model ({names[0]}) under different "
            "persona prompts. Their reviews are correlated samples, not independent "
            "opinions: agreement between them is weak evidence, and a point raised by three "
            "of them is not three times as likely to be right."
        )
    if len(families) == 1:
        return (
            f"All {len(names)} reviewers come from one model family "
            f"({', '.join(sorted(families))}): {', '.join(sorted(set(names)))}. Treat "
            "agreement between them as partially correlated rather than independent."
        )
    return (
        "The panel spans more than one model family "
        f"({', '.join(sorted(families))}), so agreement between reviewers from different "
        "families carries more weight than agreement within one."
    )
