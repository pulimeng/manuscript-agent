"""The submission loop.

One manuscript version is frozen per round. Reviewers comment on it; they never edit it. The
author proposes a patch against that version, which is assembled in a candidate tree, checked
mechanically, and promoted into the next version only if it passes. Whatever ends the run is
returned as a patch against the project you started from, marked merged or unmerged.
"""

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
from .build import BuildError
from .checks import CheckReport, error_context, run_checks
from .config import RunConfig
from .integrity import IntegrityReport, Violation, check
from .llm import LLM, Attachment
from .manuscript import Manuscript
from .package import Package
from .patches import Patch, materialise, overlay, tree_patch
from .providers import build
from .render import meta_md, plan_md, review_md, reviews_md
from .schemas import MetaReview, RevisionPlan, ScoredReview
from .versions import Version, VersionStore

TERMINAL = {"accept", "reject"}


class FabricationError(RuntimeError):
    """Unsourced values survived the correction pass and the policy is 'fail'."""


class PromotionRefused(RuntimeError):
    """A proposed patch failed the checks and was not merged."""


@dataclass
class RoundResult:
    number: int
    version: Version
    reviews: List[ScoredReview]
    meta: MetaReview
    plan: Optional[RevisionPlan] = None
    patch: Optional[Patch] = None
    checks: Optional[CheckReport] = None
    promoted: bool = False
    response_letter: Optional[str] = None
    unaddressed: List[str] = field(default_factory=list)
    integrity: List[str] = field(default_factory=list)
    misanchored: List[str] = field(default_factory=list)
    directory: Optional[Path] = None


@dataclass
class RunResult:
    manuscript: object
    rounds: List[RoundResult] = field(default_factory=list)
    directory: Optional[Path] = None
    versions: List[Version] = field(default_factory=list)
    final_patch: Optional[Path] = None
    merged: bool = False

    @property
    def decision(self) -> str:
        return self.rounds[-1].meta.decision if self.rounds else "not_submitted"


def _label(llm) -> str:
    return getattr(llm, "label", type(llm).__name__)


def unaddressed_issues(meta: MetaReview, plan: RevisionPlan) -> List[str]:
    """Critical issues the revision plan maps to no edit and does not decline."""
    covered = {i for item in plan.items for i in item.critical_issues}
    declined = " ".join(plan.out_of_scope).lower()
    missing = []
    for i, issue in enumerate(meta.critical_issues, 1):
        if i in covered:
            continue
        words = {w for w in issue.lower().split() if len(w) > 4}
        if words and len(words & set(declined.split())) >= max(2, len(words) // 4):
            continue
        missing.append(f"[{i}] {issue}")
    return missing


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


def panel_correlation(specs) -> str:
    """Say plainly when the panel is one model wearing four hats.

    Four samples from the same model are correlated draws, not four opinions. The editor is
    told so it cannot read agreement as corroboration.
    """
    names = [str(s) for s in specs]
    families = {n.split(":")[0] for n in names}   # provider is the family here
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


def overweighted_reviews(reviews: List[ScoredReview], limit: int = 2) -> List[str]:
    """Reviewers that named more than `limit` decision-critical weaknesses, or none."""
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
                out.append(f"{sr.reviewer_id} marked {label} decision-critical but raised no "
                           f"such point")
    return out


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


def inaccessible_artifact_points(reviews: List[ScoredReview]) -> List[str]:
    """Points the reviewer itself marked as its own access limit, not an author failing."""
    return [
        f"{sr.reviewer_id}-{p.label}: {p.comment[:80]}"
        for sr in reviews
        for p in sr.review.points
        if p.artifact_status == "provided_but_i_could_not_access"
    ]


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "manuscript"


class SubmissionPipeline:
    def __init__(
        self,
        config: RunConfig,
        llm: Optional[LLM] = None,
        on_event: Callable[[str], None] = lambda msg: None,
    ) -> None:
        self.config = config
        self.author = AuthorAgent(llm or build(config.author_model), config.venue)
        self.editor = EditorAgent(llm or build(config.editor_model), config.venue)
        self.reviewers = [
            ReviewerAgent(llm or build(spec), config.venue)
            for spec in config.reviewer_models
        ]
        self.on_event = on_event

    # -- review ----------------------------------------------------------

    def _collect_reviews(
        self,
        version: Version,
        previous: Dict[str, str],
        response_letter: Optional[str],
        pdf: Optional[Attachment],
        artifacts: str,
        changes: str = "",
    ) -> List[ScoredReview]:
        def one(pair):
            persona, agent = pair
            self.on_event(f"  {persona.id} ({persona.name}) reading... [{_label(agent.llm)}]")
            sr = agent.review(
                version.package,
                persona,
                previous_review_md=previous.get(persona.id),
                response_letter=response_letter,
                pdf=pdf,
                stamp=version.stamp(),
                artifacts=artifacts,
                changes=changes,
            )
            r = sr.review
            asks = ", ".join(
                f"{n}x {a}" for a, n in sorted(
                    __import__("collections").Counter(p.ask for p in r.points).items()
                )
            )
            carried = ""
            if r.prior_points:
                resolved = sum(1 for p in r.prior_points if p.verdict == "resolved")
                carried = f", {resolved}/{len(r.prior_points)} prior points resolved"
            self.on_event(
                f"  {persona.id} -> {r.recommendation} (overall {r.overall}/10, "
                f"{len(r.points)} points{carried})"
                + (f"\n      {asks}" if asks else "")
            )
            return sr

        pairs = list(zip(self.config.personas, self.reviewers))
        with ThreadPoolExecutor(max_workers=len(pairs)) as pool:
            return list(pool.map(one, pairs))

    # -- integrity -------------------------------------------------------

    def _integrity_report(self, before: str, after: str, package, citations) -> IntegrityReport:
        report = check(
            before, after, self.config.ignore_integers_below, known_citations=citations
        )
        for asset in package.missing_assets():
            report.violations.append(
                Violation("asset", asset, "referenced by the manuscript",
                          "file is not in the submission package")
            )
        return report

    # -- propose, check, promote -----------------------------------------

    def _propose(
        self,
        version: Version,
        plan: RevisionPlan,
        meta: MetaReview,
        rd: Path,
    ) -> tuple:
        """Assemble the author's revision as a candidate tree and a patch. Nothing is merged."""
        pkg = version.package
        main_rel = pkg.rel(pkg.main)
        emitted = self.author.revise(pkg, plan, meta)
        blocks = pkg.proposed_blocks(emitted)

        candidate = materialise(version.root, main_rel, blocks, rd / "candidate")
        patch = tree_patch(version.root, candidate, version.vid, version.source_hash)
        return candidate, main_rel, patch

    def _page_limit(self) -> Optional[int]:
        return self.config.page_limit or self.config.venue.page_limit

    def _check_candidate(
        self, store: VersionStore, candidate: Path, main_rel: str, vid: str,
        version: Version, citations,
    ) -> tuple:
        trial = store.evaluate(candidate, candidate / main_rel, vid)
        report = run_checks(
            trial, trial.package, self._page_limit(), self.config.enforce_page_limit
        )
        integrity = self._integrity_report(
            version.package.text, trial.package.text, trial.package, citations
        )
        return trial, report, integrity

    # -- main loop -------------------------------------------------------

    def run(self, manuscript, outdir: Path) -> RunResult:
        origin = Path(manuscript.root).resolve()
        run_dir = outdir / f"{slugify(Path(manuscript.path).stem)}-{datetime.now():%Y%m%d-%H%M%S}"
        run_dir.mkdir(parents=True, exist_ok=True)
        store = VersionStore(
            run_dir / "versions", self.config.compile_pdf, self.config.engine
        )
        result = RunResult(manuscript=manuscript, directory=run_dir)

        version = store.freeze(manuscript.root, Path(manuscript.main), "v1")
        self.on_event(f"Frozen {version.stamp()}")
        if version.build_attempted and not version.build_ok and version.pdf is None:
            (run_dir / "build-failure.log").write_text("\n".join(version.build_errors))
            raise BuildError(
                "the manuscript does not compile, so there is nothing to submit:\n"
                + "\n".join(version.build_errors[:8])
            )

        previous_reviews: Dict[str, str] = {}
        previous_labels: Dict[str, List[str]] = {}
        last_patch = ""
        response_letter: Optional[str] = None
        out_of_scope: List[str] = []
        unaddressed: List[str] = []
        integrity_carry: List[str] = []
        decision_history = ""

        for n in range(1, self.config.rounds + 1):
            rd = run_dir / f"round-{n}"
            rd.mkdir(exist_ok=True)
            self.on_event(f"\n=== Round {n}: {version.vid} under review ===")
            (rd / "version.txt").write_text(version.stamp() + "\n")

            pkg = version.package
            artifacts = pkg.artifact_manifest()
            baseline = run_checks(version, pkg, self._page_limit(), self.config.enforce_page_limit)
            (rd / "checks.md").write_text(baseline.render())
            pdf = Attachment.from_path(version.pdf) if version.pdf else None
            if pdf:
                shutil.copyfile(version.pdf, rd / "submitted.pdf")

            reviews = self._collect_reviews(
                version, previous_reviews, response_letter, pdf, artifacts, last_patch
            )
            (rd / "reviews.md").write_text(reviews_md(reviews))
            (rd / "reviews.json").write_text(
                json.dumps([r.model_dump() for r in reviews], indent=2)
            )
            misanchored = misanchored_points(reviews, version.vid)
            misanchored += overweighted_reviews(reviews)
            dropped = dropped_prior_points(reviews, previous_labels)
            if dropped:
                (rd / "dropped-points.md").write_text("\n".join(dropped) + "\n")
                self.on_event(f"  {len(dropped)} prior point(s) were not revisited")
                misanchored = misanchored + dropped
            if misanchored:
                (rd / "misanchored.md").write_text("\n".join(misanchored) + "\n")
                self.on_event(f"  {len(misanchored)} point(s) cite the wrong version")
            access = inaccessible_artifact_points(reviews)
            if access:
                (rd / "artifact-access.md").write_text("\n".join(access) + "\n")
                self.on_event(
                    f"  {len(access)} point(s) are reviewer access limits, not author gaps"
                )

            self.on_event("  editor adjudicating...")
            meta = self.editor.decide(
                pkg, reviews, n, self.config.rounds,
                history=decision_history,
                response_letter=response_letter or "",
                out_of_scope=out_of_scope,
                unaddressed=unaddressed,
                integrity=integrity_carry,
                pdf=pdf,
                checks=baseline.render(),
                misanchored=misanchored,
                correlation=panel_correlation(self.config.reviewer_models),
            )
            (rd / "meta-review.md").write_text(meta_md(meta))
            (rd / "meta-review.json").write_text(json.dumps(meta.model_dump(), indent=2))
            self.on_event(f"  editor -> {meta.decision.upper()}")

            round_result = RoundResult(
                number=n, version=version, reviews=reviews, meta=meta,
                checks=baseline, directory=rd, misanchored=misanchored,
            )
            result.rounds.append(round_result)
            decision_history += f"\nRound {n} on {version.vid}: {meta.decision}\n{meta.rationale}\n"

            if meta.decision in TERMINAL:
                break
            if n == self.config.rounds:
                self.on_event("  round budget exhausted without a terminal decision")
                break

            self.on_event("  author planning revision...")
            plan = self.author.plan(pkg, reviews, meta)
            (rd / "revision-plan.md").write_text(plan_md(plan))
            unaddressed = unaddressed_issues(meta, plan)
            round_result.plan = plan
            round_result.unaddressed = unaddressed
            out_of_scope = list(plan.out_of_scope)
            if out_of_scope:
                (rd / "out-of-scope.md").write_text(
                    "\n".join(f"- {x}" for x in out_of_scope) + "\n"
                )
                self.on_event(f"  author declined {len(out_of_scope)} request(s) as out of scope")
            if unaddressed:
                (rd / "unaddressed.md").write_text("\n".join(unaddressed) + "\n")
                self.on_event(f"  WARNING: {len(unaddressed)} critical issue(s) unaddressed")

            self.on_event(f"  author proposing a patch ({len(plan.items)} planned edits)...")
            citations = set(pkg.known_citations)
            candidate, main_rel, patch = self._propose(version, plan, meta, rd)
            patch.write(rd / "revision.patch")
            self.on_event(
                f"  patch against {version.vid}: {len(patch.files)} file(s), "
                f"+{patch.added} -{patch.removed}"
            )
            round_result.patch = patch

            next_vid = f"v{n + 1}"
            trial, report, integrity = self._check_candidate(
                store, candidate, main_rel, next_vid, version, citations
            )
            promoted, report, integrity = self._gate(
                store, candidate, main_rel, next_vid, version, citations,
                trial, report, integrity, rd,
            )
            (rd / "candidate-checks.md").write_text(report.render())
            (rd / "integrity.md").write_text(integrity.render())
            integrity_carry = integrity.values
            round_result.integrity = integrity.values
            round_result.checks = report
            round_result.promoted = promoted

            if not promoted:
                (rd / "MERGE_STATUS").write_text(
                    f"UNMERGED\npatch: {rd / 'revision.patch'}\nreason:\n{report.render()}"
                )
                self.on_event("  patch NOT promoted — the run stops with it unmerged:")
                for f in (report.blocking or [])[:5]:
                    where = f" ({f.where})" if f.where else ""
                    self.on_event(f"    {f.check}: {f.message}{where}")
                for value in integrity.values[:5]:
                    self.on_event(f"    unsourced: {value}")
                break

            self.on_event("  author writing response letter...")
            letter = self.author.response_letter(reviews, meta, plan, patch.text, pkg.fmt)
            (rd / "response-letter.md").write_text(letter)
            round_result.response_letter = letter

            # promote: the candidate becomes the working manuscript and the next version
            self._merge(candidate, manuscript)
            version = store.freeze(manuscript.root, Path(manuscript.main), next_vid)
            (rd / "MERGE_STATUS").write_text(f"MERGED into {version.vid}\n{version.stamp()}\n")
            self.on_event(f"  promoted -> {version.stamp()}")

            previous_reviews = {r.reviewer_id: review_md(r) for r in reviews}
            previous_labels = {
                r.reviewer_id: [p.label for p in r.review.points] for r in reviews
            }
            last_patch = patch.text
            response_letter = letter

        result.versions = list(store.versions)
        self._finalise(result, origin, store, run_dir)
        (run_dir / "summary.md").write_text(summarize(result, self.config))
        return result

    # -- promotion gate --------------------------------------------------

    def _gate(self, store, candidate, main_rel, vid, version, citations,
              trial, report, integrity, rd):
        """Promote only a candidate that builds, passes the checks, and invents nothing."""
        if self.config.promote == "manual":
            self.on_event("  --promote manual: patch written, nothing merged")
            return False, report, integrity

        for attempt in range(self.config.repair_attempts + 1):
            blocking = report.blocking
            unsourced = integrity.violations if self.config.on_fabrication != "warn" else []
            if not blocking and not unsourced:
                return True, report, integrity
            if attempt == self.config.repair_attempts:
                break
            if self.config.on_fabrication == "fail" and unsourced:
                raise FabricationError(integrity.render())

            label = f"repair {attempt + 1}/{self.config.repair_attempts}"
            if blocking and all(f.check == "pages" for f in blocking):
                # asking the author to cut pages is a rewrite, not a repair, and inviting one
                # is how a length breach turns into invented results
                self.on_event(
                    "  candidate is over the page limit; that is a rewrite, not a repair"
                )
                break
            if blocking:
                self.on_event(f"  candidate fails {len(blocking)} check(s) — author {label}:")
                for f in blocking[:4]:
                    where = f" ({f.where})" if f.where else ""
                    self.on_event(f"    {f.check}: {f.message}{where}")
                context = error_context(trial.package, report)
                detail = report.render()
                if trial.build_errors:
                    detail += "\nCompiler output:\n" + "\n".join(trial.build_errors[:20])
                if context:
                    detail += "\n\nThe source at those locations:\n" + context
                fixed = self.author.fix_build(trial.package, detail)
            else:
                self.on_event(
                    f"  candidate introduces {len(unsourced)} unsourced value(s) — "
                    f"author {label}"
                )
                fixed = self.author.fix_unsourced(trial.package, integrity.render())

            try:
                blocks = trial.package.proposed_blocks(fixed)
            except Exception as exc:
                self.on_event(f"  repair rejected: {exc}")
                break
            overlay(candidate, main_rel, blocks)
            trial, report, integrity = self._check_candidate(
                store, candidate, main_rel, vid, version, citations
            )
        return False, report, integrity

    @staticmethod
    def _merge(candidate: Path, manuscript) -> None:
        """Copy the approved candidate over the working manuscript."""
        root = Path(manuscript.root)
        for src in candidate.rglob("*"):
            if not src.is_file() or any(p.startswith(".") for p in src.relative_to(candidate).parts):
                continue
            dst = root / src.relative_to(candidate)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)

    # -- final patch -----------------------------------------------------

    def _finalise(self, result: RunResult, origin: Path, store: VersionStore, run_dir: Path):
        """Return the outcome as a patch against the project the run started from."""
        final = store.latest
        if final is None:
            return
        first = store.versions[0]
        patch = tree_patch(first.root, final.root, first.vid, first.source_hash)
        merged = any(r.promoted for r in result.rounds)
        path = run_dir / "final.patch"
        patch.write(path)
        result.final_patch = path
        result.merged = merged

        status = "MERGED" if merged else "UNMERGED"
        applies = patch.applies_to(origin) if patch else True
        (run_dir / "MERGE_STATUS").write_text(
            f"{status}\n"
            f"decision: {result.decision}\n"
            f"base: {first.vid} (source sha256:{first.source_hash[:16]})\n"
            f"final: {final.vid} (source sha256:{final.source_hash[:16]})\n"
            f"project: {origin}\n"
            f"final patch: {path}\n"
            f"applies cleanly to the project: {applies}\n"
            f"apply with: git apply -p1 {path}\n"
        )
        self.on_event(
            f"\nFinal patch: {path} ({len(patch.files)} file(s), +{patch.added} -{patch.removed}) "
            f"[{status}, applies to project: {applies}]"
        )


def summarize(result: RunResult, config: RunConfig) -> str:
    lines = [
        f"# Submission run — {Path(result.manuscript.path).name}",
        "",
        f"Venue: {config.venue.name}",
        f"Author model: {config.author_model} (effort={config.effort})",
        f"Editor model: {config.editor_model}",
        "Reviewers: " + "; ".join(config.panel()),
        f"Rounds run: {len(result.rounds)} of {config.rounds}",
        f"**Final decision: {result.decision}** — "
        f"{'merged' if result.merged else 'unmerged'}",
        "",
        "## Versions",
        "",
    ]
    lines += [v.summary() for v in result.versions]
    lines += [
        "",
        "## Scores",
        "",
        "| Round | Version | " + " | ".join(p.id for p in config.personas) + " | Editor | Promoted |",
        "| --- | --- | " + " | ".join("---" for _ in config.personas) + " | --- | --- |",
    ]
    for r in result.rounds:
        by_id = {sr.reviewer_id: sr.review for sr in r.reviews}
        cells = []
        for p in config.personas:
            rv = by_id.get(p.id)
            cells.append(f"{rv.overall}/10 {rv.recommendation}" if rv else "—")
        promoted = "yes" if r.promoted else "no"
        lines.append(
            f"| {r.number} | {r.version.vid} | " + " | ".join(cells)
            + f" | {r.meta.decision} | {promoted} |"
        )
    if result.final_patch:
        lines += ["", f"Final patch: `{result.final_patch}`"]
    lines += ["", "## Final meta-review", ""]
    if result.rounds:
        lines.append(meta_md(result.rounds[-1].meta))
    return "\n".join(lines) + "\n"
