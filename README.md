# ManuscriptAgent

Peer review for a manuscript you are still writing. Four reviewers and an editor read the
compiled PDF, argue about it, and hand you a decision — then you revise, and they pick up
where they left off.

```bash
manuscript-agent review ./paper --venue iclr --adversarial            # round 1
#   ... you revise the sources yourself ...
manuscript-agent review ./paper --venue iclr --letter response.md     # round 2
```

Nothing writes to your manuscript. The agents produce critique; the writing stays yours.

- **Reviewers** — independent personas (methodologist, domain expert, careful generalist,
  and an adversarial skeptic under `--adversarial`). Each names **at most two**
  decision-critical weaknesses and labels every point with what it actually asks for —
  `fatal`, `revision`, `clarification` or `optional_experiment` — plus the evidence it
  checked and whether the point is resolvable by rewording.
- **Editor** — adjudicates rather than averages, discards reviewer points that are wrong
  about the manuscript, and issues `accept` / `minor_revision` / `major_revision` / `reject`.
- **Author** *(optional)* — only in `submit`, the fully automatic loop, where it proposes
  revisions as patches behind a promotion gate. The manual loop never calls it.

## Requirements

| | |
| --- | --- |
| Python | 3.9 or newer |
| Packages | `anthropic>=0.125`, `openai>=2.0`, `pydantic>=2.0` — declared in `pyproject.toml`, pinned to tested versions in `requirements.txt` |
| LaTeX | `latexmk` + `pdflatex` (TeX Live / MacTeX) — needed to compile the PDF that gets submitted. Without it the reviewers read the sources instead; `--no-compile` forces that path. |
| Keys | `ANTHROPIC_API_KEY`. `OPENAI_API_KEY` too if you use `submit` or `write`, whose author role is an OpenAI model by default |

Both provider SDKs are required rather than optional, because the default casting puts the
author on OpenAI and the reviewers and editor on Claude. `review` never casts an author, so it
needs only the Anthropic key; the preflight checks the roles a command actually uses and tells
you which is missing. Cast every role on one provider with `--model` if you prefer.

## Accepted formats

| given | treated as |
| --- | --- |
| a directory | the manuscript inside it: the `.tex` declaring `\documentclass` (preferring `main`/`paper`/`manuscript` if several do), else `main.md` / `paper.md` / a lone `.md` |
| `.tex` | LaTeX sources; compiled to the PDF the reviewers read |
| `.md` | Markdown sources; reviewed as text, since there is nothing to compile |
| `.pdf` | a finished submission — reviewable, not revisable, and no history is kept (there are no sources to version) |
| `.docx`, `.doc`, `.odt`, `.rtf`, `.pages` | **not supported** — convert with pandoc, or export a PDF to review it as submitted |

`.txt` and `.rst` also load, as plain text.

**Sources win over a PDF sitting beside them.** A package holding both `main.tex` and
`main.pdf` is resolved to the sources: the PDF is rebuilt from them each round, so reviewers
never read a stale export, and there is a version to hash and diff against next time. Point
the command at the `.pdf` explicitly if you want the one-shot path. `--main` overrides the
search either way.

## Install

```bash
conda create -n manuscript-agent python=3.11 -y
conda activate manuscript-agent
pip install -e .                       # or: pip install -r requirements.txt for pinned versions

export ANTHROPIC_API_KEY=sk-ant-...    # or run `ant auth login`
export OPENAI_API_KEY=sk-...

python tests/test_loop.py              # check the install; no API key needed
manuscript-agent --help
```

Put the two `export` lines in `~/.zshrc` so they survive a new shell. Anything below assumes
the environment is active; with a plain virtualenv instead, the equivalent is
`python3 -m venv .venv && .venv/bin/pip install -e .` and an explicit `.venv/bin/` prefix on
each command.

## Use

```bash
# review a round — this is the main workflow
manuscript-agent review ./paper --adversarial
manuscript-agent review ./paper --adversarial --letter response.md   # after you revise

# review a PDF you already have, with no sources behind it
manuscript-agent review ./paper/main.pdf

# draft a first version from a brief
manuscript-agent write examples/brief.md -o paper.md --venue biomed-journal

# the fully automatic loop, if you want it (see "Automatic revision" below)
manuscript-agent submit ./paper --rounds 3 --adversarial
```

## How a round works

One round is: freeze the sources as a version, compile it, four reviewers read the PDF, the
editor adjudicates. Then you revise and run it again.

```
your sources ──freeze──► v2 (hashed, compiled)
                          ├── checks: build, citations, refs, figures, math, stale wording
                          ├── R1..R4 read v2.pdf — with their own v1 reviews,
                          │   your diff, and your response letter
                          └── editor adjudicates ──► decision + critical issues
```

Rounds are kept in `runs/<package-name>/` — versions, reviews, decisions and a running score
table. The directory is named after the manuscript rather than timestamped, which is what lets
the second command be days later, in a new shell, and still be round 2. Nothing is written
into your package: the history is a record of the review, not part of the paper, and it should
not travel with the sources you upload.

- each reviewer's **own** previous review, and a required verdict on every point it raised
  (`resolved` / `partially_resolved` / `unresolved` / `withdrawn`) with where it checked
- **the diff of your manual edits**, so claims can be checked against what you actually did
- your response letter, if you wrote one (`--letter`); without it the reviewers judge the
  diff on its own and are told so

`.manuscript-agent/summary.md` is the running record:

| Round | Version | Pages | R1 | R2 | Editor |
| --- | --- | --- | --- | --- | --- |
| 1 | v1 | 19 | 4/10 major_revision | 4/10 major_revision | major_revision |
| 2 | v2 | 19 | 6/10 major_revision | 7/10 accept | major_revision |

The mechanical checks run on every round, so if your revision broke a citation, left a `TODO`,
unbalanced a `$` or referenced a missing figure, you hear about it before the reviewers do.
`--history DIR` puts the record somewhere else. `--fresh` starts over at v1 — it **archives**
the existing history to `runs/<name>.archived-<timestamp>` beside it rather than
overwriting the artifacts, which is what you want after changing the venue, the panel, or the
reviewer instructions: rounds judged under different rules should not be carried forward as
though they were comparable.

A history left inside a package by an earlier version (`<package>/.manuscript-agent/`) is
moved out to `runs/` the next time you run `review`, archives included. History written under
an older schema is migrated when it is read, so upgrading needs no action. Fields that did not exist then are backfilled conservatively — a point recorded before
`verification` existed is marked `inferred`, never `verified_in_manuscript`.

## Submission packages

Point `submit` or `review` at a **directory** and it is treated as a submission bundle: the
main file plus everything it pulls in. A single `.tex` that `\input`s other files is promoted
to a package automatically.

```bash
manuscript-agent submit examples/package --rounds 3
manuscript-agent submit paper/ --main paper/manuscript.tex   # when several files declare \documentclass
```

What the package does with each part:

| part | handling |
| --- | --- |
| `\input` / `\include` / `\subfile` | resolved recursively, depth-first, each file once; commented-out includes ignored |
| section files | concatenated into one document for reviewing, each inside a `%%% FILE: path %%%` marker |
| `.bib` | keys parsed and treated as available to cite, so citing an existing entry is not mistaken for fabrication |
| figures / data | drawn into the compiled PDF, so reviewers see them directly; also inventoried in a manifest with captions for the `--no-compile` path |
| `\includegraphics{fig}` with no extension | resolved against `.pdf`, `.png`, `.jpg`, `.jpeg`, `.eps` |

### The submission is the compiled PDF

Reviewers do not read your sources — they read the article, exactly as a venue would receive
it. Each round compiles the package with `latexmk` and sends the resulting PDF to the
reviewers and the editor as an attachment, so they see the figures, the tables, the captions
and the layout, not a description of them.

```bash
manuscript-agent submit examples/package            # compiles, submits round-1/submitted.pdf
manuscript-agent submit paper/ --engine xelatex
manuscript-agent submit paper/ --no-compile         # review the sources instead
```

`--no-compile` falls back to source review, and so does any package whose main file is not
`.tex` or when no LaTeX toolchain is installed — the reviewers then read the marked-up
sources as before.

**A revision that does not build has not been made.** After each round the package is
recompiled; if the author broke it, the compiler errors go back to the author as a repair
task (substance unchanged — it is a repair, not a revision) and the build is retried once.
If it still fails, the run stops with `BuildError` and the full log, because there is nothing
to submit. `round-N/build-failure.log` holds the log whenever this happens. Unresolved
citations and references are reported as warnings rather than failures, so a paper with a
dangling `\ref` still reaches the reviewers with the problem noted.

Aux files are kept in `.manuscript-build/` inside the package, out of your sources and out of
the snapshots.

**The author edits per file.** It emits only the files it changed, each wrapped in the same
`%%% FILE: ... %%%` markers, and those blocks replace those files. Everything else is
untouched byte-for-byte as a property of the mechanism rather than an instruction the model
is asked to follow. New source files may be created inside the package; writes outside the
package root, writes to binary files, and output with no FILE blocks are all rejected as
`PackageError`.

**The fabrication check extends to assets.** The author cannot create a figure, so a revision
that answers "we need an ROC curve" by writing `\includegraphics{figures/roc.pdf}` for a file
that does not exist is reported exactly like an invented number — and, under the default
`retry`, sent back to be removed or admitted as a gap.

Each round seals a complete, compilable copy of the package under `versions/`, not just the
changed files.

## What you get

`review` writes into `runs/<package-name>/` (`--outdir` moves the parent, `--history` names
this manuscript's directory outright):

```
versions/v1/           the sealed sources, plus v1.pdf built from exactly them
versions/v2/           ... one per round
round-1/
  version.txt          the stamp the reviewers were given
  submitted.pdf        the compiled article they read
  reviews.md           the reviews, human-readable
  reviews.json         the same, structured
  meta-review.md       the editor's adjudication and decision
  checks.md            what the mechanical checks found
round-2/
  ... the same, plus:
  changes-since-last-round.diff   your manual edits, as the reviewers saw them
  response-letter.md              your letter, if you passed --letter
  dropped-points.md               prior points a reviewer did not revisit
state.json             the machine-readable history
summary.md             the running score table
```

`submit` instead writes a timestamped `runs/<paper>-<timestamp>/` containing the same
per-round material plus the author's `revision-plan.md`, `revision.patch`, `candidate/`,
`candidate-checks.md`, `integrity.md`, `out-of-scope.md`, `unaddressed.md`, `MERGE_STATUS`
and a `final.patch` for the whole run.

## Casting models in roles

Every agent talks to the same two-method interface, so which provider plays which role is
configuration. Roles are named with `provider:model`; the provider is inferred when the model
name is unambiguous (`gpt-5.5` → OpenAI, `claude-opus-5` → Claude).

**Default casting** — the author writes on OpenAI, the panel and the editor judge on Claude:

| role | model |
| --- | --- |
| author | `openai:gpt-5.5` |
| reviewers (all) | `claude:claude-opus-5` |
| editor | `claude:claude-opus-5` |

Both `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are therefore needed for a default run.

```bash
# default: gpt-5.5 writes, Opus reviews and decides
manuscript-agent submit paper.md

# cast one model in every role instead
manuscript-agent submit paper.md --model claude-opus-5

# override a single role; it wins over --model and over the defaults
manuscript-agent submit paper.md --author-model openai:gpt-5.4

# a mixed panel — fewer specs than reviewers cycles them
manuscript-agent submit paper.md --reviewers 4 \
  --reviewer-model claude-opus-5 --reviewer-model openai:gpt-5.4
```

Precedence is `--author-model`/`--editor-model`/`--reviewer-model` → `--model` → the defaults
above. Change the defaults in `DEFAULT_AUTHOR_MODEL` / `DEFAULT_REVIEWER_MODEL` /
`DEFAULT_EDITOR_MODEL` at the top of [`config.py`](manuscript_agent/config.py).

```python
from manuscript_agent import ModelSpec, RunConfig, VENUES

cfg = RunConfig(venue=VENUES["cs-conference"])              # the default casting
cfg = RunConfig(                                            # or name the roles yourself
    venue=VENUES["cs-conference"],
    author_model=ModelSpec.parse("openai:gpt-5.5"),
    editor_model=ModelSpec.parse("claude-opus-5"),
    reviewer_models=[ModelSpec.parse("claude-opus-5"), ModelSpec.parse("openai:gpt-5.4")],
)
```

Why bother: a panel drawn from one model shares that model's blind spots, and its author
shares them too — the author is then reviewed by something that fails the same way it does.
Splitting the panel across providers, and putting the editor on a different model from the
author, is the cheapest available defence against that. `summary.md` records which model
played each role, and the run log tags every review with its provider.

Anthropic effort levels map onto OpenAI reasoning effort, with `xhigh`/`max` clamped to
`high`; non-reasoning OpenAI models omit the parameter entirely.

## Library use

Reading a submission's history:

```python
from manuscript_agent.history import SubmissionHistory

hist = SubmissionHistory.load("paper/.manuscript-agent")
for rnd in hist.rounds:
    scores = [r.review.overall for r in rnd.reviews]
    print(rnd.number, rnd.vid, rnd.decision, scores)

last = hist.last
for sr in last.reviews:                       # what each reviewer still holds against you
    for point in sr.review.points:
        if point.severity == "blocking":
            print(f"{sr.reviewer_id}-{point.label} p.{point.page}: {point.comment}")
```

Driving the automatic loop:

```python
from pathlib import Path
from manuscript_agent import VENUES, Manuscript, RunConfig, SubmissionPipeline

cfg = RunConfig(venue=VENUES["cs-conference"], rounds=3, reviewer_count=4, adversarial=True)
result = SubmissionPipeline(cfg, on_event=print).run(
    Manuscript.load("paper.tex"), Path("runs")
)
print(result.decision, result.rounds[-1].meta.critical_issues)
```

## Venues

The venue profile is what the reviewers and editor calibrate against — the acceptance bar,
the review form, and the length rule. Pick one with `--venue`:

| `--venue` | for | length rule |
| --- | --- | --- |
| `iclr` | a top-tier ML conference; the acceptance bar says plainly that papers with real weaknesses are routinely accepted | 9 pages of main text, references and appendices excluded |
| `cs-conference` | a generic selective CS conference (~20% acceptance) | 8 pages of main text plus unlimited appendix |
| `biomed-journal` | clinical and translational work; statistics and reproducibility weighted heavily | ~4000 words, up to 6 display items |
| `workshop` | early-stage and position work; a lenient bar | 4 pages |

Nothing enforces these page rules mechanically — they are what the reviewers are told the
venue expects. `--page-limit N` adds a real check against the compiled PDF's total page count
(references and appendices included, so set it deliberately), and `--enforce-page-limit` makes
a breach blocking rather than a warning.

For anything else, write the four strings as JSON and pass `--venue-file`
([example](examples/venue-custom.json)), or add a `Venue` to `VENUES` in `config.py`.

## Tuning it

Almost everything you'll want to change is prompt text or config, in two places:

- `manuscript_agent/config.py` — venue profiles (`VENUES`) and reviewer personas. A venue
  profile is scope, acceptance bar, review form and length. Add your own, or pass one as
  JSON with `--venue-file`.
- `manuscript_agent/agents/*.py` — the system prompts and task prompts for each role. The
  guardrails that matter live here: reviewers are told not to invent prior work, the author
  is told never to fabricate a result to satisfy a reviewer and to leave untouched sections
  byte-for-byte alone, the editor is told to name which reviewer is wrong.

## Automatic revision (`submit`)

`submit` runs the loop without you: the author agent proposes each revision and a promotion
gate decides whether it is merged. The manual loop is the better default — you keep the
writing — but everything below is available if you want the machine to try.

It never edits the manuscript under review either. Each round works on a sealed version, and
a revision is a proposal until it earns its way in.

```
v1 (frozen, hashed, compiled)
 ├── reviewers comment on v1's PDF — they cannot edit anything
 ├── editor adjudicates
 ├── author proposes round-1/revision.patch against v1
 ├── candidate tree assembled and compiled in round-1/candidate/
 ├── checks: build, page limit, citations, references, figures, stale wording, fabrication
 └── passes ──► promoted to v2 (frozen, hashed, compiled)   MERGE_STATUS: MERGED
     fails   ──► run stops, patch kept                      MERGE_STATUS: UNMERGED
```

**Versions are immutable.** `runs/<run>/versions/v1/`, `v2/` … are complete, compilable
packages, each with the PDF built from exactly those sources and a SHA-256 over both. The
version stamp — `v2 | source sha256:… | pdf sha256:… | 9 pages` — goes into every review
prompt, so a review can always be traced to the bytes it was made against.

**A resubmission keeps its reviewers.** On round 2+ each reviewer receives its own previous
review, the response letter, and **the actual diff** the authors applied — and must return
`prior_points`: one verdict per point it raised last time (`resolved` / `partially_resolved` /
`unresolved` / `withdrawn`) with the place in the current version where it checked. It is told
to judge the manuscript rather than the letter, so a change promised but not made is
`unresolved`. A point raised last round and not revisited is recorded in `dropped-points.md`
and reported to the editor, who is told not to read silence as resolution. `score_change`
records what moved the overall score, or why it held.

**Reviewers comment, they never edit.** Their only output is structured critique. Every point
must carry the version id it refers to and the page it appears on; points naming a different
version are collected in `misanchored.md` and the editor is told to treat them as unverified
rather than binding the authors to them.

**Revisions are patches.** The author's output is assembled in a candidate tree and diffed
against the version — `revision.patch` is a plain `git apply`-compatible file you can read,
apply by hand, or discard. The manuscript itself is only written when a candidate is promoted.

**Promotion is gated** by mechanical checks, run on the candidate before anything merges:

| check | blocks promotion when |
| --- | --- |
| build | the candidate does not compile |
| pages | over `--page-limit`, and only with `--enforce-page-limit` — otherwise it warns |
| citations | a `\cite` key does not resolve |
| references | a `\ref` is undefined |
| figures | the text points at a file the package does not contain |
| stale wording | `TODO`, `TBD`, `FIXME`, `XXX`, `placeholder` survive into the submission |
| math | an unclosed `$`, or mismatched `\(` `\)` — caught before the compiler, which blames a later line |
| fabrication | numbers, citations or figures with no antecedent in the reviewed version |

A hardcoded cross-reference (`Section 4` rather than `\ref`) is a warning, not a block —
it is what goes stale when sections move. Length is a warning too, and no page limit is set
by default: the count is total PDF pages, which cannot separate main text from references and
appendices, and asking the author to cut pages is a rewrite rather than a repair — the gate
will not attempt one. The author gets `--repair-attempts` tries (default 2) to fix a failing candidate, and is
given the failing checks, the compiler output **and the offending source lines** — TeX
routinely reports an unbalanced delimiter several lines after the real one, so a line number
alone is not enough to act on. If the candidate still fails, it is not promoted and the run
ends with the patch on disk and unmerged: a version nobody can compile is not a submission.
`--promote manual` writes the patch and merges nothing, for when you want to read every
change yourself.

**The outcome is a patch against your project.** `final.patch` is the whole run as one diff
from v1 to the last version, and `MERGE_STATUS` records the decision, both hashes, whether
it applies cleanly to the directory you started from, and the command to apply it:

```
MERGED
decision: major_revision
base: v1 (source sha256:f45b439b4dc835e3)
final: v3 (source sha256:2c81aa04e91be773)
project: /Users/you/paper
applies cleanly to the project: True
apply with: git apply -p1 runs/paper-.../final.patch
```

**Reviewer access is not an author failing.** Reviewers see only the PDF, so they are given a
manifest of what the submission actually ships — repository links, code and data files found
in the package. Each point records `artifact_status`: `authors_did_not_provide` is a real gap,
`provided_but_i_could_not_access` is the reviewer's own limit and the editor is instructed
never to turn one into a critical issue. Those points are listed in `artifact-access.md`.

Everything above stays readable: versions are real packages, patches are standard unified
diffs, and every report is markdown or JSON.

## Review calibration

A reviewer that files twenty equally weighted objections has told the editor nothing. The
review schema is built to prevent that:

| | |
| --- | --- |
| `decision_critical` | at most two labels — the points that actually drive the recommendation. More than two, or a label with no matching point, is reported to the editor. |
| `ask` | `fatal` (no revision repairs it) · `revision` (rewriting or re-analysis fixes it) · `clarification` (could not tell from the text) · `optional_experiment` (would strengthen it; not required) |
| `evidence` + `verification` | what was checked and whether it was `verified_in_manuscript`, `not_verifiable_from_pdf`, or `inferred`. An unverified allegation may not be `fatal`. |
| `resolvable_by_rewording` | set when scoping the claim to its evidence would resolve the objection, so the remedy is a sentence rather than a new study |

Reviewers are told to judge each claim **at its stated scope** rather than the broadest
reading the wording admits, and to ask whether a narrower claim, an existing number or a
limitation would settle the concern before demanding an experiment.

The editor is told to **merge duplicates** — two reviewers raising one concern is one
concern — to weigh points by verification rather than by force, to treat `revision` and
`clarification` points as by definition repairable, and to reject only for a verified flaw
that cannot be repaired within a revision cycle at the scope the authors claim.

**Panel correlation is stated explicitly.** Four reviewers on one model are correlated
samples, not four opinions, and the editor is told so in as many words. Split the panel to
get real independence:

```bash
manuscript-agent review ./paper --reviewers 4 \
  --reviewer-model claude-opus-5 --reviewer-model openai:gpt-5.4
```

## Guardrails in the automatic loop

These govern `submit`, where an agent does the revising. The case they are built around is the
reviewer asking for something the authors cannot produce — a new experiment, data they do not
hold, a study they have not run.

1. **The author can refuse.** `RevisionItem.stance` includes `rebut`, and `RevisionPlan`
   has an `out_of_scope` list. The revise prompt forbids the alternative outright: never add
   a result, number or citation that does not exist to satisfy a reviewer. The instructed
   response to an impossible request is to scope the claim to what was shown, add the gap to
   the limitations, and argue it in the letter.
2. **The editor rules on the refusal.** The editor receives the response letter and the
   `out_of_scope` declarations, and is instructed that if it accepts a request is impossible
   it has two honest options — drop the demand and require the limitation instead, or decide
   the manuscript cannot be accepted here. Re-issuing a demand it has accepted is impossible
   is explicitly called out as not a decision. This is what stops the loop from deadlocking
   on an unmeetable requirement.
3. **Silent drops are caught in code, not by a prompt.** Every plan item records which of the
   editor's `critical_issues` it addresses (by index). `unaddressed_issues()` in
   `pipeline.py` computes the set difference: an issue that is neither planned nor declined
   is written to `round-N/unaddressed.md`, logged as a WARNING, and handed to the editor next
   round as `<unaddressed_critical_issues>` to rule on. This is deterministic — it does not
   depend on the editor noticing.
4. **Compliance is verified against the text.** Round 2+ reviewers get their own prior review
   and the response letter, and are told to check claimed edits against the manuscript rather
   than trust the letter.

5. **Invented evidence is caught mechanically.** After every revision,
   [`integrity.py`](manuscript_agent/integrity.py) diffs the revised manuscript against the
   version the reviewers actually read and reports every numeric literal and citation key
   that is newly asserted with no antecedent in the earlier text. Structural numbering
   (`Section 4`, `Fig. 3`, `Table 7`, `\ref{}`, list markers, URLs) is excluded, so what
   surfaces is quantitative claims and citations — exactly the things a model under pressure
   from a reviewer will invent. A new value that looks like a re-rounding of an existing one
   is flagged with that note attached.

   `--on-fabrication` decides what happens next:

   | | behaviour |
   | --- | --- |
   | `retry` *(default)* | the author gets one correction pass: source each value, or remove it and replace the claim with `[TODO: ...]` naming the evidence that would be needed. Anything that survives is escalated to the editor as `<integrity_report>`, where unexplained quantitative claims are grounds for rejection rather than revision. |
   | `warn` | recorded to `integrity.md` and escalated, no correction pass |
   | `fail` | raises `FabricationError` and stops the run |

   `--ignore-integers-below N` suppresses bare integers under `N` if counts like "3 datasets"
   prove noisy in your documents; the default of `0` shows everything.

   The check is conservative by construction — it cannot tell a real unrecorded result from
   an invented one, only that the author did not have it in writing before the reviewers
   asked. That is the property worth enforcing.

## Tests

Nine offline suites, no API key needed — they stub the model and exercise the control flow:

```bash
python tests/test_manual_rounds.py # review, revise by hand, review again — with continuity
python tests/test_continuity.py    # a resubmission is judged by the same reviewer
python tests/test_loop.py          # the automatic loop end to end
python tests/test_build.py         # compile, attach the PDF, repair a broken build
python tests/test_providers.py     # spec parsing, request shapes, panel routing
python tests/test_guardrails.py    # impossible request -> declined -> editor rules
python tests/test_fabrication.py   # invented result -> detect, repair, escalate, fail
python tests/test_package.py       # package discovery, per-file write-back, PDF submission
python tests/test_versioning.py    # freeze and hash, patch proposals, the promotion gate
```

`for t in tests/test_*.py; do python "$t" || echo "FAILED $t"; done` runs the lot.

## Licence

MIT — see [LICENSE](LICENSE).

## Design notes and limits

- **Reviewers have no literature access.** They see the manuscript and nothing else, so
  novelty and prior-art judgments are the weakest part of the output; the prompt tells them to
  phrase suspected prior work as a question rather than a claim. Wire the `web_search` server
  tool into `llm.py` if you want real citation checking.
- **Reviews are structured outputs**, so scores, recommendations and per-point verdicts are
  enumerated and machine-usable rather than parsed out of prose. `state.json` is the whole
  history in that form.
- **Reviewers run concurrently** in `submit`; `review` runs them in sequence so the log stays
  readable.
- **A page count is total pages**, references and appendices included, which is not how venues
  count main text. No limit is set by default — pass `--page-limit` deliberately.
- **No cost accounting.** Nothing reads `usage`; spend shows up only on the provider
  dashboards.
- **No `.docx`.** Sources must be LaTeX or Markdown.
- Refusals surface as `manuscript_agent.llm.RefusalError`, truncated output as
  `TruncatedError`. If you want automatic server-side fallback, switch `llm.py` to
  `client.beta.messages.*` with the `server-side-fallback-2026-07-01` beta and
  `fallbacks="default"`.
- Reviewers and editor default to `claude-opus-5` at `effort=high`. `--effort medium` cuts
  cost; reviews get noticeably shallower below that.
- In `submit` only: revision is a whole-file rewrite per changed file, so output tokens scale
  with the sections touched, and there is no convergence detection — it runs its round budget.
