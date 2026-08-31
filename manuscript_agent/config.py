"""Venue profiles and reviewer personas. Everything here is meant to be edited."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import List, Optional

from .providers import ModelSpec, cycle


@dataclass
class Venue:
    name: str
    scope: str
    acceptance_bar: str
    review_form: str
    length_guidance: str = "No hard limit; match the norms of the venue."
    page_limit: Optional[int] = None  # enforced mechanically on every candidate

    @staticmethod
    def load(path: str | Path) -> "Venue":
        data = json.loads(Path(path).read_text())
        return Venue(**data)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def brief(self) -> str:
        return (
            f"Venue: {self.name}\n"
            f"Scope: {self.scope}\n"
            f"Acceptance bar: {self.acceptance_bar}\n"
            f"Review form: {self.review_form}\n"
            f"Length: {self.length_guidance}"
            + (f"\nHard page limit: {self.page_limit}" if self.page_limit else "")
        )


@dataclass
class Persona:
    id: str
    name: str
    focus: str
    disposition: str
    expertise: str = "expert in the paper's subfield"


# Default casting. The author drafts and rewrites prose; the reviewers and the editor
# judge evidence, and are deliberately kept on a different provider from the author so the
# work is not assessed by the model that produced it.
DEFAULT_AUTHOR_MODEL = "openai:gpt-5.5"
DEFAULT_REVIEWER_MODEL = "claude:claude-opus-5"
DEFAULT_EDITOR_MODEL = "claude:claude-opus-5"


VENUES: dict[str, Venue] = {
    "cs-conference": Venue(
        name="A selective computer science conference (~20% acceptance)",
        scope="Empirical and methodological work in computer science and machine learning.",
        acceptance_bar=(
            "A clear, novel contribution supported by experiments that isolate the claimed "
            "effect, with honest baselines and ablations. Incremental deltas and unsupported "
            "claims are rejected."
        ),
        review_form=(
            "Summary, strengths, weaknesses, questions to the authors, per-axis scores "
            "(soundness, novelty, clarity), overall rating and confidence."
        ),
        length_guidance="8 pages of main text plus unlimited appendix.",
        page_limit=10,
    ),
    "biomed-journal": Venue(
        name="A mid-to-high tier biomedical journal",
        scope="Clinical, translational and computational biomedical research.",
        acceptance_bar=(
            "Methodological rigour, adequate sample size and statistics, reproducible "
            "protocol, explicit limitations, and clinical or biological relevance. "
            "Overstated causal language from observational data is a rejection trigger."
        ),
        review_form=(
            "Summary, major compulsory revisions, minor essential revisions, discretionary "
            "revisions, statistical review, recommendation to the editor."
        ),
        length_guidance="~4000 words main text, structured abstract, up to 6 display items.",
    ),
    "workshop": Venue(
        name="A workshop with a lenient bar",
        scope="Early-stage and position work.",
        acceptance_bar=(
            "A defensible idea with preliminary evidence. Rough edges are acceptable; "
            "unclear claims and missing evidence are not."
        ),
        review_form="Summary, strengths, weaknesses, recommendation.",
        length_guidance="4 pages.",
        page_limit=5,
    ),
}

DEFAULT_PERSONAS: List[Persona] = [
    Persona(
        id="R1",
        name="the methodologist",
        focus=(
            "experimental design, statistics, baselines, ablations, data leakage, "
            "reproducibility, whether the evidence actually supports each claim"
        ),
        disposition=(
            "rigorous and unsentimental; you separate what was shown from what was asserted, "
            "and you say plainly when an experiment cannot support its conclusion"
        ),
    ),
    Persona(
        id="R2",
        name="the domain expert",
        focus=(
            "novelty and positioning against prior work, whether the framing is honest, "
            "missing related work, whether the problem matters to this community"
        ),
        disposition=(
            "well read and slightly territorial about prior art; you notice when a "
            "contribution has been made before under a different name"
        ),
    ),
    Persona(
        id="R3",
        name="the careful generalist",
        focus=(
            "clarity, structure, whether the abstract matches the results, figures and "
            "tables, notation, reproducible description of the method, limitations section"
        ),
        disposition=(
            "constructive and concrete; you point at specific sentences rather than giving "
            "vague impressions, and you weight readability heavily"
        ),
    ),
]

ADVERSARIAL_PERSONA = Persona(
    id="R4",
    name="the skeptic",
    focus=(
        "overclaiming, cherry-picked results, unfair baselines, hidden assumptions, "
        "and whether the headline number would survive an independent reimplementation"
    ),
    disposition=(
        "adversarial but fair; you actively try to break the paper's central claim and you "
        "recommend rejection when the core evidence does not hold up"
    ),
)


def personas(count: int, adversarial: bool = False) -> List[Persona]:
    """Build the reviewer panel.

    `adversarial` *adds* the skeptic to the panel rather than displacing a reviewer, so
    `--reviewers 3 --adversarial` seats four.
    """
    pool = list(DEFAULT_PERSONAS)
    for i in range(len(pool) + 1, count + 1):
        pool.append(
            Persona(
                id=f"R{i}",
                name="an additional independent reviewer",
                focus="the overall contribution, evidence and presentation",
                disposition="balanced",
            )
        )
    selected = pool[:count]
    if adversarial:
        selected.append(replace(ADVERSARIAL_PERSONA, id=f"R{count + 1}"))
    return selected


@dataclass
class RunConfig:
    venue: Venue
    rounds: int = 3
    reviewer_count: int = 3
    adversarial: bool = False
    model: Optional[str] = None  # set to cast one model in every role
    effort: str = "high"
    on_fabrication: str = "retry"  # "warn" | "retry" | "fail"
    repair_attempts: int = 2           # tries the author gets to fix a candidate
    promote: str = "auto"              # "auto" (checks gate) | "manual" (patch only)
    compile_pdf: bool = True           # submit the compiled PDF, as a venue would receive it
    engine: str = "pdflatex"
    ignore_integers_below: int = 0
    personas: List[Persona] = field(default_factory=list)
    # Per-role models. Unset roles fall back to `model`/`effort`.
    author_model: Optional[ModelSpec] = None
    editor_model: Optional[ModelSpec] = None
    reviewer_models: List[ModelSpec] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.personas:
            self.personas = personas(self.reviewer_count, self.adversarial)
        override = ModelSpec.parse(self.model, self.effort) if self.model else None
        self.author_model = self.author_model or override or ModelSpec.parse(
            DEFAULT_AUTHOR_MODEL, self.effort
        )
        self.editor_model = self.editor_model or override or ModelSpec.parse(
            DEFAULT_EDITOR_MODEL, self.effort
        )
        self.reviewer_models = cycle(
            self.reviewer_models
            or [override or ModelSpec.parse(DEFAULT_REVIEWER_MODEL, self.effort)],
            len(self.personas),
        )

    def panel(self) -> List[str]:
        """One line per reviewer: who they are and which model plays them."""
        return [
            f"{p.id} {p.name} [{spec}]"
            for p, spec in zip(self.personas, self.reviewer_models)
        ]
