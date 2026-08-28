"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from .agents import AuthorAgent, EditorAgent, ReviewerAgent
from .config import VENUES, RunConfig, Venue, personas
from .llm import LLM
from .manuscript import Manuscript, strip_fence
from .build import BuildError, available as tex_available, compile_pdf
from .llm import Attachment, RefusalError, TruncatedError
from .package import Package, PackageError, PdfSubmission
from .pipeline import FabricationError
from .pipeline import SubmissionPipeline
from .render import meta_md, reviews_md
from .providers import ModelSpec, build
from .topics import TOPICS, Topic


def _venue(args) -> Venue:
    if args.venue_file:
        return Venue.load(args.venue_file)
    return VENUES[args.venue]


def _topic(args) -> Topic:
    if args.topic_file:
        return Topic.load(args.topic_file)
    return TOPICS[args.topic]


def _spec(value, effort):
    return ModelSpec.parse(value, effort) if value else None


def _config(args) -> RunConfig:
    reviewer_models = [ModelSpec.parse(v, args.effort) for v in (args.reviewer_model or [])]
    return RunConfig(
        author_model=_spec(args.author_model, args.effort),
        editor_model=_spec(args.editor_model, args.effort),
        reviewer_models=reviewer_models,
        venue=_venue(args),
        topic=_topic(args),
        on_fabrication=getattr(args, "on_fabrication", "retry"),
        compile_pdf=not getattr(args, "no_compile", False),
        engine=getattr(args, "engine", "pdflatex"),
        ignore_integers_below=getattr(args, "ignore_integers_below", 0),
        rounds=getattr(args, "rounds", 1),
        reviewer_count=args.reviewers,
        adversarial=args.adversarial,
        model=args.model,
        effort=args.effort,
    )


ENV_VAR = {"openai": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY"}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _preflight(cfg, needs=("author", "editor", "reviewers")) -> None:
    """Report the casting of the roles this command uses, and fail legibly on a missing key."""
    roles = []
    if "author" in needs:
        roles.append(("author", cfg.author_model))
    if "editor" in needs:
        roles.append(("editor", cfg.editor_model))
    if "reviewers" in needs:
        roles += [(p.id, spec) for p, spec in zip(cfg.personas, cfg.reviewer_models)]
    _log("Casting: " + ", ".join(f"{name}={spec}" for name, spec in roles))

    missing = {}
    for name, spec in roles:
        var = ENV_VAR[spec.provider]
        # an Anthropic key may also come from an `ant auth login` profile, so only OpenAI
        # is a hard requirement here; Anthropic surfaces its own error at request time.
        if spec.provider == "openai" and not os.environ.get(var):
            missing.setdefault(var, []).append(name)
    for var, names in missing.items():
        raise SystemExit(
            f"{var} is not set, and it is needed for: {', '.join(names)}.\n"
            f"  export {var}=...    (or cast those roles on another provider, "
            f"e.g. --author-model claude-opus-5)"
        )


def _open(target: str, main: str = None):
    """A PDF is a finished submission; a directory (or a file with \\input) is a package;
    a lone text file is a manuscript."""
    path = Path(target)
    if path.suffix.lower() == ".pdf":
        return PdfSubmission.load(path)
    if path.is_dir() or main:
        return Package.load(path, main)
    pkg = Package.load(path)
    if len(pkg.sources) > 1:
        _log(f"{target} pulls in {len(pkg.sources) - 1} more source file(s); "
             "treating the directory as a submission package")
        return pkg
    return Manuscript.load(path)


def cmd_write(args) -> int:
    brief = Path(args.brief).read_text() if Path(args.brief).exists() else args.brief
    out = Path(args.out)
    cfg = _config(args)
    _preflight(cfg, needs=("author",))
    author = AuthorAgent(build(cfg.author_model), cfg.venue)
    fmt = Manuscript(path=out, text="").fmt
    _log(
        f"Drafting {out} as {fmt} for: {cfg.venue.name} ({cfg.topic.name}) "
        f"[{cfg.author_model}]"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(strip_fence(author.draft(brief, fmt)))
    _log(f"Wrote {out}")
    return 0


def cmd_review(args) -> int:
    cfg = _config(args)
    _preflight(cfg, needs=("editor", "reviewers"))
    ms = _open(args.manuscript, args.main)
    pdf = _compile(ms, cfg)
    reviews = []
    for persona, spec in zip(cfg.personas, cfg.reviewer_models):
        _log(f"{persona.id} ({persona.name}) reading... [{spec}]")
        reviews.append(
            ReviewerAgent(build(spec), cfg.venue, cfg.topic).review(ms, persona, pdf=pdf)
        )
    _log(f"editor adjudicating... [{cfg.editor_model}]")
    editor = EditorAgent(build(cfg.editor_model), cfg.venue, cfg.topic)
    meta = editor.decide(ms, reviews, 1, 1, pdf=pdf)
    report = reviews_md(reviews) + "\n" + meta_md(meta)
    if args.out:
        Path(args.out).write_text(report)
        _log(f"Wrote {args.out}")
    else:
        print(report)
    return 0


def cmd_submit(args) -> int:
    cfg = _config(args)
    ms = _open(args.manuscript, args.main)
    if isinstance(ms, PdfSubmission):
        raise SystemExit(
            f"{ms.path.name} is a PDF with no sources, so there is nothing to revise.\n"
            f"  manuscript-agent review {args.manuscript} ...   review it as submitted\n"
            "  manuscript-agent submit <dir>                   revise it, given its sources"
        )
    _preflight(cfg)
    if not args.in_place:
        ms = _working_copy(ms)
        _log(f"Revising a copy: {ms.path} (use --in-place to edit the original)")
    pipeline = SubmissionPipeline(cfg, on_event=_log)
    result = pipeline.run(ms, Path(args.outdir))
    _log(f"\nFinal decision: {result.decision.upper()}")
    _log(f"Artifacts: {result.directory}")
    return 0 if result.decision != "reject" else 1


def _compile(ms, cfg):
    """`review` submits the same artefact `submit` does: the compiled PDF."""
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


def _working_copy(ms):
    """Duplicate the manuscript — the whole package, for a package — and work on the copy."""
    if isinstance(ms, Package):
        target = ms.root.with_name(ms.root.name + ".revised")
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(ms.root, target, ignore=shutil.ignore_patterns("runs", ".git", ".venv"))
        return Package.load(target, target / ms.rel(ms.main))
    src = Path(ms.path)
    target = src.with_name(src.stem + ".revised" + src.suffix)
    target.write_text(ms.text)
    return Manuscript.load(target)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="manuscript-agent", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--venue", choices=sorted(VENUES), default="cs-conference")
        sp.add_argument("--venue-file", help="JSON file overriding the built-in venue profile")
        sp.add_argument(
            "--topic",
            choices=sorted(TOPICS),
            default="general",
            help="CS subfield whose evidentiary standards the reviewers apply",
        )
        sp.add_argument("--topic-file", help="JSON file defining a custom topic profile")
        sp.add_argument(
            "--no-compile",
            action="store_true",
            help="skip the build and let the reviewers read the sources instead of the PDF",
        )
        sp.add_argument(
            "--engine",
            default="pdflatex",
            choices=["pdflatex", "xelatex", "lualatex"],
            help="LaTeX engine used to produce the submitted PDF",
        )
        sp.add_argument("--reviewers", type=int, default=3)
        sp.add_argument(
            "--adversarial", action="store_true", help="add a hostile fourth reviewer"
        )
        sp.add_argument(
            "--model",
            help=(
                "cast one model in every role, overriding the per-role defaults "
                "(author openai:gpt-5.5, reviewers and editor claude:claude-opus-5)"
            ),
        )
        sp.add_argument("--author-model", help="model playing the author, e.g. claude-opus-5")
        sp.add_argument("--editor-model", help="model playing the editor")
        sp.add_argument(
            "--reviewer-model",
            action="append",
            metavar="SPEC",
            help=(
                "model playing a reviewer; repeat to build a mixed panel "
                "(e.g. --reviewer-model claude-opus-5 --reviewer-model openai:gpt-5.1). "
                "Fewer specs than reviewers cycles them."
            ),
        )
        sp.add_argument(
            "--effort", choices=["low", "medium", "high", "xhigh", "max"], default="high"
        )

    w = sub.add_parser("write", help="draft a manuscript from a brief")
    w.add_argument("brief", help="brief text, or a path to a file containing it")
    w.add_argument("-o", "--out", default="manuscript.md")
    common(w)
    w.set_defaults(func=cmd_write)

    r = sub.add_parser("review", help="one review round, no revision")
    r.add_argument("manuscript", help="a file, or a directory holding the submission package")
    r.add_argument("--main", help="main source file, when a package has more than one")
    r.add_argument("-o", "--out", help="write the report here instead of stdout")
    common(r)
    r.set_defaults(func=cmd_review)

    s = sub.add_parser("submit", help="full review/revise/resubmit loop")
    s.add_argument("manuscript", help="a file, or a directory holding the submission package")
    s.add_argument("--main", help="main source file, when a package has more than one")
    s.add_argument("--rounds", type=int, default=3)
    s.add_argument("--outdir", default="runs")
    s.add_argument(
        "--in-place", action="store_true", help="revise the file itself instead of a copy"
    )
    s.add_argument(
        "--on-fabrication",
        choices=["warn", "retry", "fail"],
        default="retry",
        help=(
            "what to do when a revision introduces values absent from the reviewed version: "
            "warn (record and continue), retry (ask the author to source or remove them, "
            "default), fail (abort the run)"
        ),
    )
    s.add_argument(
        "--ignore-integers-below",
        type=int,
        default=0,
        help="suppress bare integers below this magnitude in the fabrication check",
    )
    common(s)
    s.set_defaults(func=cmd_submit)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _log("interrupted")
        return 130
    except (BuildError, PackageError, FabricationError, FileNotFoundError,
            TruncatedError, RefusalError, ValueError) as exc:
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
