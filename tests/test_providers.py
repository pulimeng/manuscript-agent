"""Per-role provider selection: spec parsing, request construction, panel routing."""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import anthropic, openai
from manuscript_agent.config import RunConfig, VENUES
from manuscript_agent.providers import ModelSpec, OpenAILLM, build, cycle
from manuscript_agent.pipeline import SubmissionPipeline
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

# --- panel routing -------------------------------------------------------
cfg = RunConfig(
    venue=VENUES["cs-conference"], reviewer_count=3,
    author_model=ModelSpec.parse("claude-opus-5"),
    editor_model=ModelSpec.parse("openai:gpt-5.1"),
    reviewer_models=[ModelSpec.parse("openai:gpt-5.1"), ModelSpec.parse("claude-opus-5")],
)
pipe = SubmissionPipeline(cfg)
assert pipe.author.llm.label == "claude:claude-opus-5"
assert pipe.editor.llm.label == "openai:gpt-5.1"
labels = [r.llm.label for r in pipe.reviewers]
assert labels == ["openai:gpt-5.1", "claude:claude-opus-5", "openai:gpt-5.1"], labels
assert isinstance(pipe.author.llm.client, anthropic.Anthropic)
assert isinstance(pipe.editor.llm.client, openai.OpenAI)
print("panel routing ok:", labels)

# a single shared llm still overrides every role (what the other tests rely on)
class Stub: pass
shared = SubmissionPipeline(cfg, llm=Stub())
assert shared.author.llm is shared.editor.llm is shared.reviewers[0].llm
print("shared-llm override ok")

# default casting: OpenAI author, Claude reviewers and editor
d = RunConfig(venue=VENUES["workshop"])
assert str(d.author_model) == "openai:gpt-5.5", d.author_model
assert str(d.editor_model) == "claude:claude-opus-5", d.editor_model
assert [str(x) for x in d.reviewer_models] == ["claude:claude-opus-5"] * 3
print("default casting:", d.author_model, "author /", d.editor_model, "editor+panel")

# --model casts one model everywhere
one = RunConfig(venue=VENUES["workshop"], model="claude-opus-5")
assert str(one.author_model) == "claude:claude-opus-5"
assert [str(x) for x in one.reviewer_models] == ["claude:claude-opus-5"] * 3
print("--model override ok")

# an explicit role wins over both the override and the default
mixed = RunConfig(venue=VENUES["workshop"], model="claude-opus-5",
                  author_model=ModelSpec.parse("openai:gpt-5.4"))
assert str(mixed.author_model) == "openai:gpt-5.4"
assert str(mixed.editor_model) == "claude:claude-opus-5"
print("explicit role beats override ok")

# the SDK passes unknown model strings through rather than validating them
probe = OpenAILLM(model="gpt-5.5", client=openai.OpenAI(
    api_key="t", base_url="http://127.0.0.1:9", max_retries=0))
try:
    probe.text("s", "p", max_tokens=50); raise SystemExit("expected a connection error")
except openai.APIConnectionError:
    print("gpt-5.5 accepted client-side (no local model-name validation)")
print("PROVIDERS OK")
