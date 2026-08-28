"""The submit -> review -> decide -> revise -> resubmit loop."""

from __future__ import annotations

import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .agents import AuthorAgent, EditorAgent, ReviewerAgent
from .config import RunConfig
from .build import BuildError, available as tex_available, compile_pdf
from .integrity import IntegrityReport, Violation, check
from .llm import LLM
from .llm import Attachment
from .providers import build
from .manuscript import Manuscript
from .manuscript import diff as text_diff
from .package import Package
from .render import meta_md, plan_md, review_md, reviews_md
from .schemas import MetaReview, RevisionPlan, ScoredReview

TERMINAL = {"accept", "reject"}


@dataclass
class RoundResult:
    number: int
    reviews: List[ScoredReview]
    meta: MetaReview
    plan: Optional[RevisionPlan] = None
    response_letter: Optional[str] = None
    diff: str = ""
    unaddressed: List[str] = field(default_factory=list)
    integrity: List[str] = field(default_factory=list)
    pdf: Optional[Path] = None
    directory: Optional[Path] = None


@dataclass
class RunResult:
    manuscript: Manuscript
    rounds: List[RoundResult] = field(default_factory=list)
    directory: Optional[Path] = None

    @property
    def decision(self) -> str:
        return self.rounds[-1].meta.decision if self.rounds else "not_submitted"


def _label(llm) -> str:
    """Providers expose `.label`; test doubles need not."""
    return getattr(llm, "label", type(llm).__name__)


def unaddressed_issues(meta: MetaReview, plan: RevisionPlan) -> List[str]:
    """Critical issues the revision plan maps to no edit and does not decline.

    A deterministic check, deliberately not left to the model: an issue that is neither
    planned nor declared out of scope has been dropped on the floor.
    """
    covered = {i for item in plan.items for i in item.critical_issues}
    declined = " ".join(plan.out_of_scope).lower()
    missing = []
    for i, issue in enumerate(meta.critical_issues, 1):
        if i in covered:
            continue
        # a short lexical overlap test is enough to spot an explicit decline
        words = {w for w in issue.lower().split() if len(w) > 4}
        if words and len(words & set(declined.split())) >= max(2, len(words) // 4):
            continue
        missing.append(f"[{i}] {issue}")
    return missing


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "manuscript"


class FabricationError(RuntimeError):
    """Unsourced values survived the correction pass and the policy is 'fail'."""


class SubmissionPipeline:
    """Runs a manuscript through as many review rounds as the editor allows."""

    def __init__(
        self,
        config: RunConfig,
        llm: Optional[LLM] = None,
        on_event: Callable[[str], None] = lambda msg: None,
    ) -> None:
        """`llm`, if given, plays every role — otherwise each role gets its own client."""
        self.config = config
        self.author = AuthorAgent(llm or build(config.author_model), config.venue)
        self.editor = EditorAgent(
            llm or build(config.editor_model), config.venue, config.topic
        )
        # one reviewer agent per persona, so the panel can span providers
        self.reviewers = [
            ReviewerAgent(llm or build(spec), config.venue, config.topic)
            for spec in config.reviewer_models
        ]
        self.on_event = on_event

    # -- review round ----------------------------------------------------

    def _collect_reviews(
        self,
        manuscript: Manuscript,
        previous: Dict[str, str],
        response_letter: Optional[str],
        pdf: Optional[Attachment] = None,
    ) -> List[ScoredReview]:
        def one(pair):
            persona, agent = pair
            self.on_event(f"  {persona.id} ({persona.name}) reading... [{_label(agent.llm)}]")
            sr = agent.review(
                manuscript,
                persona,
                previous_review_md=previous.get(persona.id),
                response_letter=response_letter,
                pdf=pdf,
            )
            r = sr.review
            self.on_event(
                f"  {persona.id} -> {r.recommendation} "
                f"(overall {r.overall}/10, {len(r.points)} points)"
            )
            return sr

        pairs = list(zip(self.config.personas, self.reviewers))
        with ThreadPoolExecutor(max_workers=len(pairs)) as pool:
            return list(pool.map(one, pairs))

    # -- the build -------------------------------------------------------

    def _buildable(self, manuscript) -> bool:
        return (
            self.config.compile_pdf
            and Path(manuscript.main).suffix.lower() == ".tex"
            and tex_available()
        )

    def _build(self, manuscript, rd: Path, allow_repair: bool) -> Optional[Attachment]:
        """Compile the submission. A revision that will not build has not been made."""
        if not self._buildable(manuscript):
            return None

        result = compile_pdf(manuscript.root, manuscript.main, self.config.engine)
        if not result.ok and allow_repair:
            self.on_event("  BUILD FAILED — author repairing:")
            for line in result.errors[:3]:
                self.on_event(f"    {line}")
            (rd / "build-failure.log").write_text(result.log)
            fixed = self.author.fix_build(manuscript, result.summary())
            manuscript.replace(fixed)
            manuscript.save()
            result = compile_pdf(manuscript.root, manuscript.main, self.config.engine)

        if not result.ok:
            (rd / "build-failure.log").write_text(result.log)
            raise BuildError(
                "the submission does not compile; nothing can be submitted.\n"
                + result.summary()
                + f"\nFull log: {rd / 'build-failure.log'}"
            )

        submitted = rd / "submitted.pdf"
        shutil.copyfile(result.pdf, submitted)
        note = f" ({result.summary()})" if result.warnings else ""
        self.on_event(
            f"  compiled {submitted.name} — {submitted.stat().st_size / 1000:.0f} kB" + note
        )
        return Attachment.from_path(submitted)

    # -- integrity -------------------------------------------------------

    def _integrity_report(self, manuscript, before: str) -> IntegrityReport:
        """Unsourced values, plus figures and data the text now cites but the package lacks."""
        report = check(
            before,
            manuscript.text,
            self.config.ignore_integers_below,
            known_citations=manuscript.known_citations,
        )
        for asset in manuscript.missing_assets():
            report.violations.append(
                Violation(
                    "asset",
                    asset,
                    f"referenced by the manuscript",
                    "file is not in the submission package",
                )
            )
        return report

    def _verify(self, manuscript: Manuscript, before: str, rd: Path) -> List[str]:
        """Flag values with no antecedent in the reviewed version; try once to repair."""
        report = self._integrity_report(manuscript, before)
        if not report:
            return []

        self.on_event(
            f"  INTEGRITY: {len(report.violations)} value(s) with no antecedent in the "
            f"reviewed version: {', '.join(report.values[:6])}"
            + (" ..." if len(report.values) > 6 else "")
        )
        (rd / "integrity.md").write_text(report.render())

        if self.config.on_fabrication == "warn":
            return report.values

        if self.config.on_fabrication == "retry":
            self.on_event("  author correcting unsourced values...")
            corrected = self.author.fix_unsourced(manuscript, report.render())
            manuscript.replace(corrected)
            report = self._integrity_report(manuscript, before)
            (rd / "integrity.md").write_text(report.render())
            if not report:
                self.on_event("  INTEGRITY: cleared after correction")
                return []
            self.on_event(
                f"  INTEGRITY: {len(report.violations)} value(s) survived correction — "
                "reported to the editor"
            )
            return report.values

        raise FabricationError(
            f"{len(report.violations)} unsourced value(s) in {manuscript.path}: "
            f"{', '.join(report.values)}"
        )

    # -- main loop -------------------------------------------------------

    def run(self, manuscript: Manuscript, outdir: Path) -> RunResult:
        run_dir = outdir / f"{slugify(manuscript.path.stem)}-{datetime.now():%Y%m%d-%H%M%S}"
        run_dir.mkdir(parents=True, exist_ok=True)
        result = RunResult(manuscript=manuscript, directory=run_dir)

        previous_reviews: Dict[str, str] = {}
        response_letter: Optional[str] = None
        out_of_scope: List[str] = []
        unaddressed: List[str] = []
        integrity: List[str] = []
        decision_history = ""

        for n in range(1, self.config.rounds + 1):
            rd = run_dir / f"round-{n}"
            rd.mkdir(exist_ok=True)
            manuscript.snapshot(rd, "submitted")
            self.on_event(f"\n=== Round {n}: under review ===")
            pdf = self._build(manuscript, rd, allow_repair=n > 1)
            round_pdf = rd / "submitted.pdf" if pdf else None

            reviews = self._collect_reviews(manuscript, previous_reviews, response_letter, pdf)
            (rd / "reviews.md").write_text(reviews_md(reviews))
            (rd / "reviews.json").write_text(
                json.dumps([r.model_dump() for r in reviews], indent=2)
            )

            self.on_event("  editor adjudicating...")
            meta = self.editor.decide(
                manuscript,
                reviews,
                n,
                self.config.rounds,
                history=decision_history,
                response_letter=response_letter or "",
                out_of_scope=out_of_scope,
                unaddressed=unaddressed,
                integrity=integrity,
                pdf=pdf,
            )
            (rd / "meta-review.md").write_text(meta_md(meta))
            (rd / "meta-review.json").write_text(json.dumps(meta.model_dump(), indent=2))
            self.on_event(f"  editor -> {meta.decision.upper()}")

            round_result = RoundResult(
                number=n, reviews=reviews, meta=meta, directory=rd, pdf=round_pdf
            )
            result.rounds.append(round_result)
            decision_history += f"\nRound {n} decision: {meta.decision}\n{meta.rationale}\n"

            if meta.decision in TERMINAL:
                break
            if n == self.config.rounds:
                self.on_event("  round budget exhausted without a terminal decision")
                break

            self.on_event("  author planning revision...")
            plan = self.author.plan(manuscript, reviews, meta)
            (rd / "revision-plan.md").write_text(plan_md(plan))

            unaddressed = unaddressed_issues(meta, plan)
            if plan.out_of_scope:
                (rd / "out-of-scope.md").write_text(
                    "\n".join(f"- {x}" for x in plan.out_of_scope) + "\n"
                )
                self.on_event(
                    f"  author declined {len(plan.out_of_scope)} request(s) as out of scope; "
                    "the editor will rule on them next round"
                )
            if unaddressed:
                (rd / "unaddressed.md").write_text("\n".join(unaddressed) + "\n")
                self.on_event(
                    f"  WARNING: {len(unaddressed)} critical issue(s) neither planned nor "
                    "declined — flagged to the editor"
                )

            self.on_event(f"  author revising ({len(plan.items)} planned edits)...")
            before = manuscript.text
            revised = self.author.revise(manuscript, plan, meta)
            manuscript.replace(revised)
            integrity = self._verify(manuscript, before, rd)
            # after any correction pass, so the response letter cites the text that shipped
            diff = text_diff(before, manuscript.text, manuscript.path.name)
            manuscript.save()
            manuscript.snapshot(rd, "revised")
            (rd / "changes.diff").write_text(diff)

            self.on_event("  author writing response letter...")
            letter = self.author.response_letter(reviews, meta, plan, diff, manuscript.fmt)
            (rd / "response-letter.md").write_text(letter)

            round_result.plan = plan
            round_result.diff = diff
            round_result.response_letter = letter
            round_result.unaddressed = unaddressed
            round_result.integrity = integrity
            out_of_scope = list(plan.out_of_scope)

            previous_reviews = {r.reviewer_id: review_md(r) for r in reviews}
            response_letter = letter

        (run_dir / "summary.md").write_text(summarize(result, self.config))
        return result


def summarize(result: RunResult, config: RunConfig) -> str:
    lines = [
        f"# Submission run — {result.manuscript.path.name}",
        "",
        f"Venue: {config.venue.name}",
        f"Topic: {config.topic.name}",
        f"Author model: {config.author_model} (effort={config.effort})",
        f"Editor model: {config.editor_model}",
        "Reviewers: " + "; ".join(config.panel()),
        f"Rounds run: {len(result.rounds)} of {config.rounds}",
        f"**Final decision: {result.decision}**",
        "",
        "| Round | " + " | ".join(p.id for p in config.personas) + " | Editor |",
        "| --- | " + " | ".join("---" for _ in config.personas) + " | --- |",
    ]
    for r in result.rounds:
        by_id = {sr.reviewer_id: sr.review for sr in r.reviews}
        cells = []
        for p in config.personas:
            rv = by_id.get(p.id)
            cells.append(f"{rv.overall}/10 {rv.recommendation}" if rv else "—")
        lines.append(f"| {r.number} | " + " | ".join(cells) + f" | {r.meta.decision} |")
    lines += ["", "## Final meta-review", ""]
    if result.rounds:
        lines.append(meta_md(result.rounds[-1].meta))
    return "\n".join(lines) + "\n"
