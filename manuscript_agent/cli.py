"""Command line entry point.

One command, `review`: freeze the sources, compile, have the panel read the PDF, have the
editor decide. You revise. Run it again and the same panel picks up where it left off.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .agents import EditorAgent, ReviewerAgent
from .build import BuildError, available as tex_available, compile_pdf
from .checks import run_checks
from .config import MODEL_POOL, VENUES, RunConfig, Venue
from .history import HistoryError, SubmissionHistory
from .llm import Attachment, RefusalError, TruncatedError
from .manuscript import BinaryManuscriptError, Manuscript
from .package import Package, PackageError, PdfSubmission
from .panel import (
    dropped_prior_points,
    misanchored_points,
    overweighted_reviews,
    panel_correlation,
    slugify,
)
from .patches import tree_patch
from .providers import ModelSpec, build
from .render import meta_md, reviews_md
from .versions import DIGEST_ALGO, VersionStore

ENV_VAR = {"openai": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY"}
WORD_SUFFIXES = {".docx", ".doc", ".odt", ".rtf", ".pages"}
LEGACY_HISTORY = ".manuscript-agent"


def _log(msg: str) -> None:
    print(msg, flush=True)


def load_dotenv(paths=(".env",)) -> List[str]:
    """Read KEY=VALUE lines from a .env into the environment, without overriding anything
    already set. No dependency, no interpolation; quotes around the value are stripped."""
    loaded: List[str] = []
    for candidate in paths:
        path = Path(candidate).expanduser()
        if not path.is_file():
            continue
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
                loaded.append(key)
    return loaded


# -- configuration ----------------------------------------------------------


def _venue(args) -> Venue:
    if args.venue_file:
        return Venue.load(args.venue_file)
    return VENUES[args.venue]


def _config(args) -> RunConfig:
    reviewer_models = [ModelSpec.parse(v, args.effort) for v in (args.reviewer_model or [])]
    return RunConfig(
        venue=_venue(args),
        editor_model=ModelSpec.parse(args.editor_model, args.effort) if args.editor_model else None,
        reviewer_models=reviewer_models,
        page_limit=args.page_limit,
        enforce_page_limit=args.enforce_page_limit,
        compile_pdf=not args.no_compile,
        engine=args.engine,
        reviewer_count=args.reviewers,
        adversarial=args.adversarial,
        model=args.model,
        effort=args.effort,
    )


def _available_pool() -> List[str]:
    """MODEL_POOL restricted to providers you can actually call.

    OpenAI needs its key in the environment. The Anthropic SDK can also find credentials in
    an `ant auth login` profile, so Claude models stay in the pool regardless and any
    problem surfaces at the first request.
    """
    pool = [m for m in MODEL_POOL
            if not m.startswith("openai:") or os.environ.get("OPENAI_API_KEY")]
    if not pool:
        raise SystemExit(
            "no model in MODEL_POOL is usable: set OPENAI_API_KEY and/or ANTHROPIC_API_KEY, "
            "or edit MODEL_POOL in manuscript_agent/config.py"
        )
    return pool


def _cast(cfg: RunConfig, history: SubmissionHistory, args) -> None:
    """Round 1 draws the panel; every later round of the manuscript keeps it.

    The point of coming back to the same reviewers is that their second opinion is a
    continuation of their first. A new draw each round would make continuity a fiction.
    """
    stored = history.casting
    explicit = bool(args.model or args.editor_model or args.reviewer_model)

    if stored and not args.recast:
        if explicit:
            _log("Casting flags ignored: this manuscript already has a panel, and a "
                 "resubmission goes back to the reviewers who read it. Use --recast to "
                 "draw a new one (or --fresh to start the history over).")
        cfg.reviewer_count = stored.get("reviewer_count", cfg.reviewer_count)
        cfg.adversarial = stored.get("adversarial", cfg.adversarial)
        from .config import personas
        cfg.personas = personas(cfg.reviewer_count, cfg.adversarial)
        cfg.editor_model = ModelSpec.parse(stored["editor"], cfg.effort)
        cfg.reviewer_models = [
            ModelSpec.parse(stored["reviewers"][p.id], cfg.effort) for p in cfg.personas
        ]
        _log(f"Panel restored from round 1 (the same reviewers and editor return)")
        return

    if not cfg.cast_complete:
        cfg.cast(pool=_available_pool(), seed=args.seed)
        how = "drawn at random from MODEL_POOL" + (f" (seed {args.seed})" if args.seed is not None else "")
    else:
        how = "pinned by flags"
    history.casting = {
        **cfg.casting(),
        "reviewer_count": cfg.reviewer_count,
        "adversarial": cfg.adversarial,
        "drawn_at": datetime.now().isoformat(timespec="seconds"),
        "how": how,
    }
    _log(f"Panel {how}; it will be kept for every round of this manuscript")


def _preflight(cfg: RunConfig) -> None:
    """Report the casting, and fail legibly on a missing key."""
    roles = [("editor", cfg.editor_model)]
    roles += [(p.id, spec) for p, spec in zip(cfg.personas, cfg.reviewer_models)]
    _log("Casting: " + ", ".join(f"{name}={spec}" for name, spec in roles))

    missing = {}
    for name, spec in roles:
        var = ENV_VAR[spec.provider]
        if spec.provider == "openai" and not os.environ.get(var):
            missing.setdefault(var, []).append(name)
    for var, names in missing.items():
        raise SystemExit(
            f"{var} is not set, and it is needed for: {', '.join(names)}.\n"
            f"  export {var}=...    (or pin the panel to another provider, e.g. "
            "--model claude-opus-5)"
        )


# -- what to review ------------------------------------------------------------


def _history_dir(ms, args) -> Path:
    """Where this manuscript's rounds live: outside the package, under a visible
    `runs/<name>/`, keyed by name rather than timestamped so continuity across separate
    invocations works."""
    if args.history:
        return Path(args.history)
    chosen = Path(args.outdir) / slugify(Path(ms.root).name or Path(ms.path).stem)

    legacy = Path(ms.root) / LEGACY_HISTORY
    if legacy.exists() and not (chosen / "state.json").exists():
        chosen.parent.mkdir(parents=True, exist_ok=True)
        if chosen.exists():
            shutil.rmtree(chosen)
        shutil.move(str(legacy), str(chosen))
        _log("Moved the review history out of your package:")
        _log(f"  {legacy}  ->  {chosen}")
    for old in sorted(Path(ms.root).glob(f"{LEGACY_HISTORY}.archived-*")):
        target = chosen.with_name(f"{chosen.name}{old.name[len(LEGACY_HISTORY):]}")
        if not target.exists():
            chosen.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(target))
            _log(f"  {old.name}  ->  {target}")
    return chosen


def _open(target: str, main: Optional[str] = None):
    """A PDF is reviewed as submitted; a directory or .tex/.md is a source package."""
    path = Path(target)
    if path.suffix.lower() in WORD_SUFFIXES:
        raise SystemExit(
            f"{path.name}: Word and rich-text documents are not supported.\n"
            "  Accepted: .pdf (reviewed as is), .tex or .md sources, or a directory holding "
            "them. Convert with pandoc, or export a PDF."
        )
    if path.suffix.lower() == ".pdf":
        return PdfSubmission.load(path)
    if path.is_dir():
        try:
            return Package.load(path, main)
        except PackageError as exc:
            pdfs = sorted(path.glob("*.pdf"))
            if pdfs:
                return PdfSubmission.load(pdfs[0])
            raise SystemExit(str(exc))
    if main:
        return Package.load(path, main)
    pkg = Package.load(path)
    if len(pkg.sources) > 1:
        _log(f"{target} pulls in {len(pkg.sources) - 1} more source file(s); "
             "treating the directory as a submission package")
        return pkg
    return Manuscript.load(path)


def _compile(ms, cfg: RunConfig):
    if isinstance(ms, PdfSubmission):
        _log(f"Reviewing {ms.path.name} as submitted ({ms.path.stat().st_size // 1000} kB)")
        return ms.attachment()
    if not cfg.compile_pdf or Path(ms.main).suffix.lower() != ".tex" or not tex_available():
        return None
    result = compile_pdf(ms.root, ms.main, cfg.engine)
    if not result.ok:
        _log("Build failed — reviewing the sources instead:")
        for line in result.errors[:5]:
            _log(f"  {line}")
        return None
    _log(f"Compiled {result.pdf.name} ({result.pdf.stat().st_size // 1000} kB)")
    return Attachment.from_path(result.pdf)


# -- the round ------------------------------------------------------------------


def cmd_review(args) -> int:
    """One round: freeze, compile, review, adjudicate. You revise; you run it again."""
    cfg = _config(args)
    ms = _open(args.manuscript, args.main)

    if isinstance(ms, PdfSubmission):
        return _review_pdf(cfg, ms, args)

    history_dir = _history_dir(ms, args)
    if args.fresh and (history_dir / "state.json").exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        archived = history_dir.with_name(f"{history_dir.name}.archived-{stamp}")
        history_dir.rename(archived)
        _log(f"Archived the previous history to {archived}")
        _log("  starting again at v1 with a new panel; the old rounds stay readable")
    history = SubmissionHistory.load(history_dir)
    _cast(cfg, history, args)
    _preflight(cfg)

    store = VersionStore(history.directory / "versions", cfg.compile_pdf, cfg.engine)
    version = store.freeze(ms.root, Path(ms.main), history.next_vid())
    _log(f"Frozen {version.stamp()}")
    if version.build_attempted and version.pdf is None:
        raise BuildError(
            "the manuscript does not compile, so there is nothing to submit:\n"
            + "\n".join(version.build_errors[:8])
        )

    pkg = version.package
    checks = run_checks(version, pkg, cfg.page_limit or cfg.venue.page_limit,
                        cfg.enforce_page_limit)
    if checks.findings:
        _log(f"Checks: {checks.summary()}")
        for f in checks.findings[:8]:
            _log(f"  {f.render()[2:]}")

    previous = history.previous_reviews()
    letter = Path(args.letter).read_text() if args.letter else ""
    changes = ""
    if history.last and previous:
        prior_root = history.directory / "versions" / history.last.vid
        if prior_root.exists():
            changes = tree_patch(prior_root, version.root, history.last.vid,
                                 history.last.source_hash).text
            lines = len(changes.splitlines())
            _log(f"Continuing round {history.next_number()}: "
                 f"{history.last.vid} -> {version.vid}, "
                 f"{lines} diff lines since your last submission")
            if not lines:
                _log("  the sources are unchanged since the last round — the reviewers will "
                     "be re-reading the same paper")
            if getattr(history.last, "digest_algo", 1) != DIGEST_ALGO:
                _log(f"  ({history.last.vid} was hashed under an older digest definition; "
                     "comparison above is by file contents, not by hash)")
        if not letter:
            _log("  (no --letter given; reviewers will judge the diff on its own)")

    pdf = Attachment.from_path(version.pdf) if version.pdf else None
    artifacts = pkg.artifact_manifest()
    reviews = []
    for persona, spec in zip(cfg.personas, cfg.reviewer_models):
        _log(f"{persona.id} ({persona.name}) reading... [{spec}]")
        sr = ReviewerAgent(build(spec), cfg.venue).review(
            pkg, persona,
            previous_review_md=previous.get(persona.id),
            response_letter=letter or (previous and "(no response letter was provided)"),
            pdf=pdf, stamp=version.stamp(), artifacts=artifacts, changes=changes,
        )
        r = sr.review
        critical = ", ".join(r.decision_critical) or "none named"
        carried = ""
        if r.prior_points:
            done = sum(1 for x in r.prior_points if x.verdict == "resolved")
            carried = f", {done}/{len(r.prior_points)} prior points resolved"
        _log(f"  -> {r.recommendation} (overall {r.overall}/10, "
             f"{len(r.points)} points{carried})")
        _log(f"     decision-critical: {critical}")
        reviews.append(sr)

    misanchored = misanchored_points(reviews, version.vid) + overweighted_reviews(reviews)
    dropped = dropped_prior_points(reviews, history.previous_labels())
    correlation = panel_correlation(cfg.reviewer_models)
    _log("editor adjudicating...")
    meta = EditorAgent(build(cfg.editor_model), cfg.venue).decide(
        pkg, reviews, history.next_number(), history.next_number(),
        history=history.decision_history(), response_letter=letter,
        pdf=pdf, checks=checks.render(), misanchored=misanchored + dropped,
        correlation=correlation,
    )
    _log(f"editor -> {meta.decision.upper()}")

    rd = history.directory / f"round-{history.next_number()}"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "version.txt").write_text(version.stamp() + "\n")
    (rd / "reviews.md").write_text(reviews_md(reviews))
    (rd / "reviews.json").write_text(json.dumps([r.model_dump() for r in reviews], indent=2))
    (rd / "meta-review.md").write_text(meta_md(meta))
    (rd / "checks.md").write_text(checks.render())
    if changes:
        (rd / "changes-since-last-round.diff").write_text(changes)
    if dropped:
        (rd / "dropped-points.md").write_text("\n".join(dropped) + "\n")
    if letter:
        (rd / "response-letter.md").write_text(letter)
    if pdf:
        shutil.copyfile(version.pdf, rd / "submitted.pdf")

    history.record(version, reviews, meta, letter)
    (history.directory / "summary.md").write_text(history.summary())

    if args.out:
        Path(args.out).write_text(reviews_md(reviews) + "\n" + meta_md(meta))
        _log(f"Wrote {args.out}")

    def show(path: Path) -> str:
        try:
            return str(path.relative_to(Path.cwd()))
        except ValueError:
            return str(path)

    _log(f"\nRound {history.next_number() - 1} complete: {meta.decision.upper()}")
    _log(f"  reviews    {show(rd / 'reviews.md')}")
    _log(f"  decision   {show(rd / 'meta-review.md')}")
    _log(f"  history    {show(history.directory / 'summary.md')}")
    _log("\nRevise your sources, then run the same command for round "
         f"{history.next_number()}. Add --letter response.md to say what you changed.")
    return 0


def _review_pdf(cfg: RunConfig, ms: PdfSubmission, args) -> int:
    """A PDF with no sources: one round, no history, a fresh draw."""
    if not cfg.cast_complete:
        cfg.cast(pool=_available_pool(), seed=args.seed)
    _preflight(cfg)
    pdf = _compile(ms, cfg)
    reviews = []
    for persona, spec in zip(cfg.personas, cfg.reviewer_models):
        _log(f"{persona.id} ({persona.name}) reading... [{spec}]")
        reviews.append(ReviewerAgent(build(spec), cfg.venue).review(ms, persona, pdf=pdf))
    _log(f"editor adjudicating... [{cfg.editor_model}]")
    meta = EditorAgent(build(cfg.editor_model), cfg.venue).decide(
        ms, reviews, 1, 1, pdf=pdf, correlation=panel_correlation(cfg.reviewer_models),
    )
    report = reviews_md(reviews) + "\n" + meta_md(meta)
    if args.out:
        Path(args.out).write_text(report)
        _log(f"Wrote {args.out}")
    else:
        print(report)
    return 0


# -- argument parsing -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="manuscript-agent",
        description="Peer review for a manuscript you are still writing.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("review", help="one review round; revise, then run it again")
    r.add_argument("manuscript", help="a .tex/.md file, a .pdf, or a directory holding the package")
    r.add_argument("--main", help="main source file, when several declare \\documentclass")
    r.add_argument("--letter", help="your response letter for this round")
    r.add_argument("-o", "--out", help="also write the round's report to this file")

    where = r.add_argument_group("where rounds are kept")
    where.add_argument("--outdir", default="runs", help="parent directory (default: runs)")
    where.add_argument("--history", help="this manuscript's directory outright (default: <outdir>/<name>)")
    where.add_argument("--fresh", action="store_true",
                       help="start over at v1 with a new panel; the old history is archived alongside")

    venue = r.add_argument_group("venue")
    venue.add_argument("--venue", choices=sorted(VENUES), default="cs-conference")
    venue.add_argument("--venue-file", help="JSON file overriding the built-in venue profile")
    venue.add_argument("--page-limit", type=int,
                       help="total PDF pages to check against (references and appendices included)")
    venue.add_argument("--enforce-page-limit", action="store_true",
                       help="treat a length breach as blocking rather than a warning")
    venue.add_argument("--no-compile", action="store_true",
                       help="skip the build; reviewers read the sources instead of a PDF")
    venue.add_argument("--engine", default="pdflatex", choices=["pdflatex", "xelatex", "lualatex"])

    panel = r.add_argument_group(
        "panel",
        "Round 1 draws the editor and reviewers at random from MODEL_POOL; later rounds of "
        "the same manuscript keep that panel. Pin roles here to override the draw.",
    )
    panel.add_argument("--reviewers", type=int, default=3)
    panel.add_argument("--adversarial", action="store_true", help="add a hostile extra reviewer")
    panel.add_argument("--seed", type=int, help="make the random draw reproducible")
    panel.add_argument("--recast", action="store_true",
                       help="draw a new panel for a manuscript that already has one")
    panel.add_argument("--model", help="one model in every role, e.g. claude-opus-5")
    panel.add_argument("--editor-model", help="pin the editor, e.g. openai:gpt-5.4")
    panel.add_argument("--reviewer-model", action="append", metavar="SPEC",
                       help="pin a reviewer; repeat for a mixed panel, fewer than --reviewers cycles")
    panel.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default="high")
    r.set_defaults(func=cmd_review)
    return p


def main(argv=None) -> int:
    # .env in the working directory, then in the project, then ~/.manuscript-agent.env
    loaded = load_dotenv((".env", Path(__file__).resolve().parents[1] / ".env",
                          "~/.manuscript-agent.env"))
    if loaded:
        _log(f"Loaded from .env: {', '.join(loaded)}")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _log("interrupted")
        return 130
    except (BuildError, PackageError, HistoryError, FileNotFoundError,
            TruncatedError, RefusalError, BinaryManuscriptError) as exc:
        _log(f"\n{type(exc).__name__}: {exc}")
        return 1
    except Exception as exc:  # credentials, rate limits, transport
        name = type(exc).__name__
        if "OpenAIError" in name or "Authentication" in name or "Permission" in name:
            _log(f"\n{name}: {exc}")
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
