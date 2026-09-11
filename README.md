# ManuscriptAgent

Peer review for a manuscript you are still writing. A panel of reviewers and an editor read
the compiled PDF and hand you a decision. You revise. Run it again and the same panel picks up
where it left off.

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
- **Editor** — adjudicates rather than averages, merges duplicate concerns, discards
  reviewer points that are wrong about the manuscript, and issues `accept` /
  `minor_revision` / `major_revision` / `reject`.

## Requirements

| | |
| --- | --- |
| Python | 3.9 or newer |
| Packages | `anthropic`, `openai`, `pydantic` — declared in `pyproject.toml`, pinned in `requirements.txt` |
| LaTeX | `latexmk` + `pdflatex` (TeX Live / MacTeX) to compile the PDF the reviewers read. Without it, or with `--no-compile`, they read the sources. |
| Keys | `ANTHROPIC_API_KEY`; `OPENAI_API_KEY` too if you want OpenAI models in the pool |

## Install

```bash
conda create -n manuscript-agent python=3.11 -y
conda activate manuscript-agent
pip install -e .

cp keys.txt.example keys.txt            # then fill in your keys
python tests/test_manual_rounds.py      # check the install; no API key needed
```

### Keys

Keys are read from the environment; nothing in the tool takes a key as an argument or writes
one to disk. Three ways to set them, in the order they win:

1. **Your shell** — `export ANTHROPIC_API_KEY=...`, or `source load_keys.sh`, which reads
   `keys.txt` and exports what it finds (useful when other tools need the same keys).
2. **A key file, read at startup** — either `keys.txt` (`Provider:key` lines, template in
   `keys.txt.example`) or `.env` (`KEY=VALUE` lines). Looked for in the directory you run in,
   then the project root, then `~/.manuscript-agent-keys.txt` / `~/.manuscript-agent.env`.
   Never overrides a value already in your shell. Both files are gitignored.
3. **An `ant auth login` profile** — the Anthropic SDK finds it on its own when neither of the
   above sets `ANTHROPIC_API_KEY`.

```
# keys.txt
Anthropic:sk-ant-...
OpenAI:sk-proj-...
```

`OPENAI_API_KEY` is optional: without it, the panel is drawn from the Claude models in
`MODEL_POOL` only.

## How a round works

```
your sources ──freeze──► v2 (hashed, compiled)
                          ├── checks: build, citations, refs, figures, math, stale wording
                          ├── R1..R4 read v2.pdf — with their own v1 reviews,
                          │   the diff of your edits, and your response letter
                          └── editor adjudicates ──► decision + critical issues
```

Rounds are kept in `runs/<package-name>/` — versions, reviews, decisions and a running score
table. Nothing is written into your package. The directory is named after the manuscript
rather than timestamped, which is what lets the second command be days later, in a new shell,
and still be round 2, carrying:

- each reviewer's **own** previous review, and a required verdict on every point it raised
  (`resolved` / `partially_resolved` / `unresolved` / `withdrawn`) with where it checked
- **the diff of your manual edits**, so claims can be checked against what you actually did
- your response letter, if you wrote one (`--letter`); without it the reviewers judge the
  diff on its own and are told so

`runs/<name>/summary.md` is the running record:

| Round | Version | Pages | R1 | R2 | R3 | R4 | Editor |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | v1 | 19 | 4/10 major_revision | 5/10 major_revision | 4/10 major_revision | 4/10 reject | major_revision |
| 2 | v2 | 19 | 6/10 minor_revision | 7/10 accept | 6/10 minor_revision | 5/10 major_revision | minor_revision |

The mechanical checks run on every round, so a broken citation, a surviving `TODO`, an
unbalanced `$` or a reference to a missing figure reaches you before it reaches the reviewers.

`--fresh` starts over at v1 with a new panel — it **archives** the existing history to
`runs/<name>.archived-<timestamp>` rather than overwriting it. Use it after changing the
venue or the reviewer instructions: rounds judged under different rules should not be
carried forward as though they were comparable. `--history DIR` puts the record elsewhere.

## The panel

Round 1 draws the editor and each reviewer **at random** from `MODEL_POOL` in
[`config.py`](manuscript_agent/config.py), restricted to the providers you hold a key for.
Every later round of that manuscript keeps the same casting, so the reviewers who return are
the ones who reviewed before. A new manuscript draws again.

```python
MODEL_POOL = [
    "claude:claude-opus-5",
    "claude:claude-sonnet-5",
    "openai:gpt-5.4",
    "openai:gpt-5.2",
]
```

Reviewers are drawn without replacement while the pool lasts, so a four-seat panel over a
four-model pool is four different models. The draw is recorded in `state.json`; `--seed N`
makes it reproducible.

```bash
manuscript-agent review ./paper                      # random panel, kept for this manuscript
manuscript-agent review ./paper --seed 7             # the same random panel every time
manuscript-agent review ./paper --recast             # draw a new panel for a manuscript that has one
manuscript-agent review ./paper --model claude-opus-5                 # pin every role
manuscript-agent review ./paper --editor-model openai:gpt-5.4 \
                                --reviewer-model claude-opus-5 --reviewer-model openai:gpt-5.2
```

Pins apply on round 1 (or with `--recast`). On a later round they are ignored with a
message, because a resubmission goes back to the reviewers who read it.

**Panel correlation is stated to the editor.** Four reviewers on one model are correlated
samples, not four opinions, and the editor is told so in as many words; a same-family panel
gets a weaker warning. Keep both providers in the pool if you want real independence.

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

**Reviewer access is not an author failing.** Reviewers see only the PDF, so they are given a
manifest of what the submission actually ships. Each point records `artifact_status`:
`authors_did_not_provide` is a real gap, `provided_but_i_could_not_access` is the reviewer's
own limit and the editor may not turn it into a critical issue.

## Venues

The venue profile is what the panel calibrates against — the acceptance bar, the review form,
and the length rule. Pick one with `--venue`:

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

## Accepted formats

| given | treated as |
| --- | --- |
| a directory | the manuscript inside it: the `.tex` declaring `\documentclass` (preferring `main`/`paper`/`manuscript` if several do), else `main.md` / `paper.md` / a lone `.md` |
| `.tex` | LaTeX sources; compiled to the PDF the reviewers read |
| `.md` | Markdown sources; reviewed as text, since there is nothing to compile |
| `.pdf` | a finished submission — reviewed as it is, one round, no history (there are no sources to version) |
| `.docx`, `.doc`, `.odt`, `.rtf`, `.pages` | **not supported** — convert with pandoc, or export a PDF |

**Sources win over a PDF sitting beside them.** A package holding both `main.tex` and
`main.pdf` is resolved to the sources: the PDF is rebuilt from them each round, so reviewers
never read a stale export, and there is a version to hash and diff against next time. Point
the command at the `.pdf` explicitly if you want the one-shot path. `--main` overrides the
search either way.

### Submission packages

A directory is a submission bundle: the main file plus everything it pulls in.

| part | handling |
| --- | --- |
| `\input` / `\include` / `\subfile` | resolved recursively, depth-first, each file once; commented-out includes ignored |
| `.bib` | keys parsed so citations can be checked |
| figures / data | drawn into the compiled PDF, so reviewers see them directly; `\graphicspath` honoured; a reference to a file the package lacks is reported |
| `\includegraphics{fig}` | extensionless refs resolved against `.pdf/.png/.jpg/.jpeg/.eps` |

Each round seals a complete, compilable copy of the package under `runs/<name>/versions/`,
with the PDF built from exactly those sources and SHA-256 over both. The version stamp —
`v2 | source sha256:… | pdf sha256:… | 19 pages` — goes into every review prompt, so a
criticism can always be traced to the bytes it was made against. Points naming a different
version are collected in `misanchored.md` and the editor is told to treat them as unverified.

## What you get

```
runs/<name>/
  state.json                  the machine-readable history, casting included
  summary.md                  the running score table
  versions/v1/ v2/ …          sealed sources + the PDF built from them
  round-1/
    version.txt               the stamp the reviewers were given
    submitted.pdf             the compiled article they read
    reviews.md  reviews.json  the reviews, human- and machine-readable
    meta-review.md            the editor's adjudication and decision
    checks.md                 what the mechanical checks found
  round-2/
    … the same, plus:
    changes-since-last-round.diff   your manual edits, as the reviewers saw them
    response-letter.md              your letter, if you passed --letter
    dropped-points.md               prior points a reviewer did not revisit
```

## Library use

```python
from manuscript_agent import SubmissionHistory

hist = SubmissionHistory.load("runs/paper")
print(hist.casting["editor"], hist.casting["reviewers"])
for rnd in hist.rounds:
    print(rnd.number, rnd.vid, rnd.decision, [r.review.overall for r in rnd.reviews])

for sr in hist.last.reviews:                  # what each reviewer still holds against you
    for point in sr.review.points:
        if point.label in sr.review.decision_critical:
            print(f"{sr.reviewer_id}-{point.label} [{point.ask}] p.{point.page}: {point.comment}")
```

## Tuning it

- `manuscript_agent/config.py` — `MODEL_POOL`, venue profiles (`VENUES`), and the reviewer
  personas (lens and disposition).
- `manuscript_agent/agents/reviewer.py` and `editor.py` — the system prompts and the rules
  of engagement described above.

## Tests

Six offline suites, no API key needed — they stub the model and exercise the control flow:

```bash
python tests/test_manual_rounds.py # review, revise by hand, review again; the panel sticks
python tests/test_calibration.py   # the ask taxonomy, decision-critical cap, panel correlation
python tests/test_providers.py     # spec parsing, request shapes, random casting
python tests/test_versioning.py    # freeze and hash, the between-round diff, the checks
python tests/test_build.py         # compile, page count, attach the PDF, non-UTF-8 output
python tests/test_package.py       # package discovery, resolution, PDF submissions
```

`for t in tests/test_*.py; do python "$t" || echo "FAILED $t"; done` runs the lot.

## Licence

MIT — see [LICENSE](LICENSE).

## Design notes and limits

- **Reviewers have no literature access.** They see the manuscript and nothing else, so
  novelty and prior-art judgments are the weakest part of the output; the prompt tells them to
  phrase suspected prior work as a question rather than a claim.
- **Reviews are structured outputs**, so scores, recommendations and per-point verdicts are
  enumerated and machine-usable rather than parsed out of prose. `state.json` is the whole
  history in that form.
- **A page count is total pages**, references and appendices included, which is not how venues
  count main text. No limit is set by default — pass `--page-limit` deliberately.
- **No cost accounting.** Nothing reads `usage`; spend shows up only on the provider
  dashboards.
- **No `.docx`.** Sources must be LaTeX or Markdown.
- Refusals surface as `RefusalError`, truncated output as `TruncatedError`.
- `--effort medium` cuts cost; reviews get noticeably shallower below that.
