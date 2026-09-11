"""Per-role provider selection: spec parsing, request construction, panel routing."""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import anthropic, openai
from manuscript_agent.config import RunConfig, VENUES
from manuscript_agent.providers import ModelSpec, OpenAILLM, build, cycle
from manuscript_agent.schemas import Review

# --- spec parsing --------------------------------------------------------
assert str(ModelSpec.parse("gpt-5.1")) == "openai:gpt-5.1"
assert str(ModelSpec.parse("claude-opus-5")) == "claude:claude-opus-5"
assert str(ModelSpec.parse("anthropic:claude-sonnet-5")) == "claude:claude-sonnet-5"
for bad in ("llama3", "grok:x"):
    try:
        ModelSpec.parse(bad); raise SystemExit(f"{bad} should not parse")
    except ValueError:
        pass
print("spec parsing ok")

# --- OpenAI request construction (no network) ----------------------------
oai = OpenAILLM(model="gpt-5.1", client=openai.OpenAI(
    api_key="t", base_url="http://127.0.0.1:9", max_retries=0))
assert oai._reasoning() == {"reasoning": {"effort": "high"}}
assert OpenAILLM(model="gpt-4.1")._reasoning() == {}, "non-reasoning model must omit it"
assert OpenAILLM(model="gpt-5.1", effort="max")._reasoning()["reasoning"]["effort"] == "high"
for call in (lambda: oai.text("s", "p", max_tokens=500),
             lambda: oai.parse("s", "p", Review)):
    try:
        call(); raise SystemExit("expected a connection error")
    except openai.APIConnectionError:
        pass
print("openai text() + parse() requests build; effort mapped")

# --- casting: random from the pool, reproducible, and pins win ----------
from manuscript_agent.config import MODEL_POOL

cfg = RunConfig(venue=VENUES["cs-conference"], reviewer_count=3, adversarial=True)
assert not cfg.cast_complete
cfg.cast(seed=11)
assert cfg.cast_complete and len(cfg.reviewer_models) == 4
assert all(str(m) in MODEL_POOL for m in cfg.reviewer_models + [cfg.editor_model])
again = RunConfig(venue=VENUES["cs-conference"], reviewer_count=3, adversarial=True).cast(seed=11)
assert again.casting() == cfg.casting(), "the same seed must reproduce the draw"
other = RunConfig(venue=VENUES["cs-conference"], reviewer_count=3, adversarial=True).cast(seed=12)
print("cast:", cfg.casting()["editor"], "|", list(cfg.casting()["reviewers"].values()))
print("reproducible with a seed; a different seed differs:", other.casting() != cfg.casting())

# the pool is used without replacement while it lasts, so a 4-seat panel over a 4-model
# pool is four different models
assert len({str(m) for m in cfg.reviewer_models}) == min(4, len(MODEL_POOL))
print("reviewers drawn without replacement")

# a restricted pool (no OpenAI key) never draws an OpenAI model
claude_only = [m for m in MODEL_POOL if m.startswith("claude:")]
c2 = RunConfig(venue=VENUES["workshop"], reviewer_count=3).cast(pool=claude_only, seed=1)
assert all(m.provider == "claude" for m in c2.reviewer_models + [c2.editor_model])
print("restricted pool respected")

# pins beat the draw, and --model pins every role
pinned = RunConfig(venue=VENUES["workshop"], reviewer_count=3,
                   editor_model=ModelSpec.parse("openai:gpt-5.4"),
                   reviewer_models=[ModelSpec.parse("claude-opus-5")])
assert pinned.cast_complete and str(pinned.editor_model) == "openai:gpt-5.4"
assert [str(m) for m in pinned.reviewer_models] == ["claude:claude-opus-5"] * 3
one = RunConfig(venue=VENUES["workshop"], model="claude-sonnet-5")
assert one.cast_complete and {str(m) for m in one.reviewer_models + [one.editor_model]} \
    == {"claude:claude-sonnet-5"}
print("pinned roles and --model override the draw")

# each spec builds the right client
import anthropic
c = build(ModelSpec.parse("claude-opus-5")); assert isinstance(c.client, anthropic.Anthropic)
o = build(ModelSpec.parse("openai:gpt-5.4")); assert isinstance(o.client, openai.OpenAI)
print("build() routes each spec to its provider")
print("PROVIDERS OK")
