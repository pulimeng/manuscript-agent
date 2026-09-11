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
    # Total pages of the compiled PDF. Left unset by default: venue limits are usually on
    # main text, excluding references and appendices, which a page count cannot distinguish.
    # Set it deliberately (--page-limit) if you want the check.
    page_limit: Optional[int] = None

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


# Models a panel may be drawn from. Round 1 of a manuscript draws its editor and reviewers
# at random from here (restricted to the providers you hold a key for), and every later
# round of that manuscript keeps the same casting, so the reviewers who return are the ones
# who reviewed before. A new manuscript draws again. Edit freely; entries are provider:model.
MODEL_POOL = [
    "claude:claude-opus-5",
    "claude:claude-sonnet-5",
    "openai:gpt-5.4",
    "openai:gpt-5.2",
]


VENUES: dict[str, Venue] = {
    "iclr": Venue(
        name="ICLR (a top-tier machine learning conference)",
        scope="Representation learning and machine learning broadly, including empirical, "
              "methodological and benchmark contributions.",
        acceptance_bar=(
            "A contribution the community can build on, supported by evidence at the scope "
            "the authors claim. Papers with real weaknesses are routinely accepted when the "
            "central claim holds; rejection is for claims the evidence cannot support."
        ),
        review_form=(
            "Summary, strengths, weaknesses, questions to the authors, per-axis scores "
            "(soundness, novelty, clarity), overall rating and confidence."
        ),
        length_guidance="9 pages of main text; references and appendices do not count "
                        "toward it.",
    ),
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
    reviewer_count: int = 3
    adversarial: bool = False
    effort: str = "high"
    page_limit: Optional[int] = None   # overrides the venue's
    enforce_page_limit: bool = False   # a length breach warns unless you ask for a block
    compile_pdf: bool = True           # reviewers read the compiled PDF, as a venue would
    engine: str = "pdflatex"
    personas: List[Persona] = field(default_factory=list)
    # Casting. Left empty, both are drawn at random from MODEL_POOL by `cast()`; set them to
    # pin a panel, or to restore the panel a manuscript was first reviewed by.
    editor_model: Optional[ModelSpec] = None
    reviewer_models: List[ModelSpec] = field(default_factory=list)
    model: Optional[str] = None        # one model in every role, overriding the draw

    def __post_init__(self) -> None:
        if not self.personas:
            self.personas = personas(self.reviewer_count, self.adversarial)
        if self.model:
            pinned = ModelSpec.parse(self.model, self.effort)
            self.editor_model = self.editor_model or pinned
            self.reviewer_models = self.reviewer_models or [pinned]
        if self.reviewer_models:
            self.reviewer_models = cycle(self.reviewer_models, len(self.personas))

    @property
    def cast_complete(self) -> bool:
        return self.editor_model is not None and len(self.reviewer_models) == len(self.personas)

    def cast(self, pool: Optional[List[str]] = None, seed: Optional[int] = None) -> "RunConfig":
        """Fill any role that is not pinned by drawing from the pool.

        Reviewers are drawn without replacement while the pool allows, so a panel is as
        diverse as the pool permits; the editor is drawn independently. The draw is
        recorded by the caller and reused for every later round of the same manuscript.
        """
        import random

        rng = random.Random(seed)
        candidates = [ModelSpec.parse(m, self.effort) for m in (pool or MODEL_POOL)]
        if not candidates:
            raise ValueError("the model pool is empty")
        if self.editor_model is None:
            self.editor_model = rng.choice(candidates)
        if not self.reviewer_models:
            need = len(self.personas)
            drawn: List[ModelSpec] = []
            while len(drawn) < need:
                batch = list(candidates)
                rng.shuffle(batch)
                drawn += batch[: need - len(drawn)]
            self.reviewer_models = drawn
        return self

    def panel(self) -> List[str]:
        """One line per reviewer: who they are and which model plays them."""
        return [
            f"{p.id} {p.name} [{spec}]"
            for p, spec in zip(self.personas, self.reviewer_models)
        ]

    def casting(self) -> dict:
        """The casting in a form that survives a trip through state.json."""
        return {
            "editor": str(self.editor_model),
            "reviewers": {
                p.id: str(spec) for p, spec in zip(self.personas, self.reviewer_models)
            },
        }
