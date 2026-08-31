"""Submission history across separate invocations.

The automatic loop kept its rounds in memory. When you revise by hand, each round is its own
command, possibly days apart, so the history has to live on disk: which versions have been
submitted, what each reviewer said, and what you told them you changed. That is what lets
round 2 be the same reviewers continuing, rather than four strangers reading a new paper.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .render import review_md
from .schemas import MetaReview, ScoredReview

STATE = "state.json"


@dataclass
class Round:
    number: int
    vid: str
    source_hash: str
    pdf_hash: Optional[str]
    pages: Optional[int]
    decision: str
    created_at: str
    reviews: List[ScoredReview] = field(default_factory=list)
    letter: str = ""

    def to_json(self) -> dict:
        return {
            "number": self.number,
            "vid": self.vid,
            "source_hash": self.source_hash,
            "pdf_hash": self.pdf_hash,
            "pages": self.pages,
            "decision": self.decision,
            "created_at": self.created_at,
            "letter": self.letter,
            "reviews": [r.model_dump() for r in self.reviews],
        }

    @staticmethod
    def from_json(data: dict) -> "Round":
        return Round(
            number=data["number"],
            vid=data["vid"],
            source_hash=data["source_hash"],
            pdf_hash=data.get("pdf_hash"),
            pages=data.get("pages"),
            decision=data["decision"],
            created_at=data["created_at"],
            letter=data.get("letter", ""),
            reviews=[ScoredReview.model_validate(r) for r in data.get("reviews", [])],
        )


@dataclass
class SubmissionHistory:
    """Everything that has happened to one manuscript, round by round."""

    directory: Path
    rounds: List[Round] = field(default_factory=list)

    @staticmethod
    def load(directory: str | Path) -> "SubmissionHistory":
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        state = directory / STATE
        rounds = []
        if state.exists():
            rounds = [Round.from_json(r) for r in json.loads(state.read_text())["rounds"]]
        return SubmissionHistory(directory, rounds)

    def save(self) -> None:
        (self.directory / STATE).write_text(
            json.dumps({"rounds": [r.to_json() for r in self.rounds]}, indent=2)
        )

    # -- what the next round needs to know -------------------------------

    @property
    def last(self) -> Optional[Round]:
        return self.rounds[-1] if self.rounds else None

    def next_number(self) -> int:
        return len(self.rounds) + 1

    def next_vid(self) -> str:
        return f"v{self.next_number()}"

    def previous_reviews(self) -> Dict[str, str]:
        """Each reviewer's own last review, rendered, keyed by reviewer id."""
        if not self.last:
            return {}
        return {r.reviewer_id: review_md(r) for r in self.last.reviews}

    def previous_labels(self) -> Dict[str, List[str]]:
        if not self.last:
            return {}
        return {r.reviewer_id: [p.label for p in r.review.points] for r in self.last.reviews}

    def decision_history(self) -> str:
        return "".join(
            f"\nRound {r.number} on {r.vid}: {r.decision}\n" for r in self.rounds
        )

    def record(
        self,
        version,
        reviews: List[ScoredReview],
        meta: MetaReview,
        letter: str = "",
    ) -> Round:
        rnd = Round(
            number=self.next_number(),
            vid=version.vid,
            source_hash=version.source_hash,
            pdf_hash=version.pdf_hash,
            pages=version.pages,
            decision=meta.decision,
            created_at=datetime.now().isoformat(timespec="seconds"),
            reviews=reviews,
            letter=letter,
        )
        self.rounds.append(rnd)
        self.save()
        return rnd

    def summary(self) -> str:
        if not self.rounds:
            return "No rounds recorded yet.\n"
        lines = ["| Round | Version | " ]
        ids = [r.reviewer_id for r in self.rounds[0].reviews]
        lines = [
            "| Round | Version | Pages | " + " | ".join(ids) + " | Editor |",
            "| --- | --- | --- | " + " | ".join("---" for _ in ids) + " | --- |",
        ]
        for rnd in self.rounds:
            by_id = {r.reviewer_id: r.review for r in rnd.reviews}
            cells = [
                f"{by_id[i].overall}/10 {by_id[i].recommendation}" if i in by_id else "—"
                for i in ids
            ]
            lines.append(
                f"| {rnd.number} | {rnd.vid} | {rnd.pages or '—'} | "
                + " | ".join(cells)
                + f" | {rnd.decision} |"
            )
        return "\n".join(lines) + "\n"
