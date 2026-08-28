"""Subfield review standards.

Each community accepts a different kind of evidence. A systems reviewer who does not ask
for p99 latency, or an ML reviewer who does not ask how many seeds, is not reviewing.
These profiles carry that expectation into the reviewer and editor prompts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass
class Topic:
    id: str
    name: str
    expertise: str
    standards: List[str] = field(default_factory=list)
    red_flags: List[str] = field(default_factory=list)

    @staticmethod
    def load(path: str | Path) -> "Topic":
        return Topic(**json.loads(Path(path).read_text()))

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def brief(self) -> str:
        if self.id == "general":
            return ""
        lines = [
            f"Evidentiary standards in {self.name}. Apply these; do not recite them back.",
            "A manuscript in this area is expected to supply:",
        ]
        lines += [f"- {s}" for s in self.standards]
        lines += ["", "Failure modes that are common here and that you must actively check for:"]
        lines += [f"- {f}" for f in self.red_flags]
        lines += [
            "",
            "Absence of one of these is a real weakness, but weigh it against what the paper "
            "claims: do not demand evidence for a claim the authors did not make.",
        ]
        return "\n".join(lines)


TOPICS: Dict[str, Topic] = {
    "general": Topic(
        id="general",
        name="computer science (unspecified subfield)",
        expertise="a broadly read computer scientist",
    ),
    "ml": Topic(
        id="ml",
        name="machine learning and deep learning",
        expertise=(
            "a machine learning researcher fluent in optimization, training dynamics, "
            "benchmark methodology and statistical comparison of learned models"
        ),
        standards=[
            "multiple random seeds with dispersion reported, not a single run",
            "baselines tuned under the same budget as the proposed method, and re-run rather "
            "than copied from another paper's table",
            "an ablation that isolates the mechanism the paper credits for the gain",
            "the test split used once, with model selection done on validation",
            "compute, parameter count and training data scale reported",
            "a check for train/test contamination where pretrained models are involved",
        ],
        red_flags=[
            "a headline improvement inside the seed-to-seed variance",
            "an untuned or differently-sized baseline",
            "'SOTA' claimed on a saturated benchmark where the delta is noise",
            "hyperparameters selected on the test set",
            "gains that vanish once compute is matched",
        ],
    ),
    "nlp": Topic(
        id="nlp",
        name="natural language processing and language models",
        expertise=(
            "an NLP researcher experienced in evaluation design, human annotation protocols "
            "and the pitfalls of benchmarking large language models"
        ),
        standards=[
            "human evaluation with a stated protocol, annotator count and inter-annotator "
            "agreement, wherever quality is claimed",
            "an explicit pretraining-contamination check for any benchmark result",
            "prompts given verbatim, with sensitivity to prompt phrasing addressed",
            "decoding parameters, model versions and API dates reported",
            "claims scoped to the languages and domains actually evaluated",
        ],
        red_flags=[
            "LLM-as-judge used as the sole quality measure with no human validation",
            "cherry-picked qualitative examples standing in for aggregate results",
            "prompt engineering performed against the test set",
            "English-only evaluation supporting a general claim about language",
        ],
    ),
    "cv": Topic(
        id="cv",
        name="computer vision",
        expertise=(
            "a computer vision researcher familiar with standard benchmarks, backbone "
            "comparability and the effect of training schedules on reported accuracy"
        ),
        standards=[
            "standard splits and metrics for the benchmark in question",
            "backbone, input resolution, augmentation and schedule matched across methods",
            "FLOPs, parameters and inference latency alongside accuracy",
            "test-time augmentation and ensembling disclosed",
            "failure cases shown, not only successes",
        ],
        red_flags=[
            "a stronger backbone or longer schedule doing the work attributed to the method",
            "undisclosed test-time augmentation",
            "qualitative figures selected to flatter the method",
        ],
    ),
    "systems": Topic(
        id="systems",
        name="computer systems (operating systems, distributed systems, architecture)",
        expertise=(
            "a systems researcher who builds and measures real systems and is practised at "
            "spotting unrepresentative benchmarks and untuned baselines"
        ),
        standards=[
            "tail latency (p95/p99) reported, not only means or throughput",
            "hardware, kernel, and configuration fully described",
            "warm-up excluded and steady state measured, with run-to-run variance",
            "comparison against a real, competently tuned system rather than a strawman",
            "scalability curves accompanied by an explanation of the bottleneck",
            "workload justified as representative, ideally with a production trace",
            "artifact available, or a reason it is not",
        ],
        red_flags=[
            "mean-only latency hiding a long tail",
            "a baseline left at default configuration",
            "microbenchmarks generalized into an end-to-end claim",
            "speedups that shrink once the baseline is given the same hardware",
        ],
    ),
    "security": Topic(
        id="security",
        name="computer security and privacy",
        expertise=(
            "a security researcher who evaluates defenses against adaptive adversaries and "
            "is sceptical of threat models written after the evaluation"
        ),
        standards=[
            "a threat model stated up front: adversary goals, capabilities and knowledge",
            "defenses evaluated against an adaptive attacker aware of the defense",
            "false-positive rate measured on realistic benign traffic or samples",
            "ethics and responsible disclosure addressed for any real-world component",
            "performance and deployability cost of the defense quantified",
            "attacks evaluated under realistic constraints, not idealized access",
        ],
        red_flags=[
            "a defense tested only against static, non-adaptive attacks",
            "a threat model that excludes exactly the attacks the defense cannot stop",
            "malicious-sample detection reported without a benign baseline",
            "claims of security absent a formal argument or adversarial evaluation",
        ],
    ),
    "pl": Topic(
        id="pl",
        name="programming languages and formal methods",
        expertise=(
            "a programming languages researcher who reads proofs carefully and checks that "
            "the implementation corresponds to the formalism"
        ),
        standards=[
            "formal statements with proofs, at minimum in an appendix",
            "soundness and completeness claims explicitly scoped, with assumptions listed",
            "mechanization named and available if the work claims to be verified",
            "the implementation shown to correspond to the formal system it is based on",
            "evaluation on an established benchmark suite for the class of programs",
        ],
        red_flags=[
            "a theorem stated with only a proof sketch for the difficult case",
            "an unstated side condition carrying the proof",
            "'verified' used for a system whose formal model omits the interesting behaviour",
            "an implementation whose optimizations are outside the proved model",
        ],
    ),
    "theory": Topic(
        id="theory",
        name="theoretical computer science",
        expertise=(
            "a theoretician who checks proofs line by line and compares bounds against the "
            "prior state of the art in the same model"
        ),
        standards=[
            "complete proofs, with the model of computation and all assumptions explicit",
            "bounds compared against prior work in the same model and regime",
            "hidden constants and lower-order terms disclosed where they matter",
            "matching lower bounds discussed, or the gap acknowledged",
        ],
        red_flags=[
            "an improvement that holds only in a parameter regime of no interest",
            "an assumption that trivializes the problem",
            "a proof whose key step is asserted rather than argued",
        ],
    ),
    "db": Topic(
        id="db",
        name="databases and data management",
        expertise=(
            "a database researcher experienced in benchmarking query engines and storage "
            "systems, and in the ways such benchmarks are made to flatter a system"
        ),
        standards=[
            "standard workloads (TPC-C/H/DS, YCSB) alongside any custom workload",
            "full configuration disclosure: indexes, buffer pool, isolation level, tuning",
            "cold- and warm-cache numbers distinguished",
            "concurrency and contention evaluated, not only single-client throughput",
            "durability, recovery and correctness under failure addressed",
        ],
        red_flags=[
            "a comparison DBMS left at default configuration",
            "single-threaded numbers supporting a claim about a concurrent system",
            "a custom workload with no argument for its representativeness",
        ],
    ),
    "hci": Topic(
        id="hci",
        name="human-computer interaction",
        expertise=(
            "an HCI researcher trained in study design, mixed methods and the limits of "
            "what a given sample can support"
        ),
        standards=[
            "study design, sample size, recruitment and compensation reported",
            "ethics/IRB approval stated",
            "statistical tests appropriate to the design, with effect sizes and CIs",
            "qualitative coding described, with the coding process and reliability",
            "generalizability limits stated explicitly for the population studied",
        ],
        red_flags=[
            "a sample too small or too narrow for the generality of the claim",
            "p-values with no effect size",
            "a within-subjects design ignoring order effects",
            "a system evaluation with no control or baseline condition",
        ],
    ),
    "se": Topic(
        id="se",
        name="software engineering",
        expertise=(
            "a software engineering researcher familiar with mining studies, developer "
            "experiments and construct validity"
        ),
        standards=[
            "dataset construction and sampling strategy justified, not convenience-sampled",
            "a threats-to-validity section covering construct, internal and external validity",
            "a replication package with data and scripts",
            "developer studies with a stated protocol and appropriate statistics",
            "evaluation on projects independent of the tool's authors",
        ],
        red_flags=[
            "repositories mined with no sampling rationale or quality filter",
            "a tool evaluated only on the projects it was developed against",
            "absent threats-to-validity discussion",
        ],
    ),
    "networks": Topic(
        id="networks",
        name="computer networking",
        expertise=(
            "a networking researcher who distinguishes simulation fidelity from deployment "
            "reality and checks fairness and measurement methodology"
        ),
        standards=[
            "topology, traffic model and link parameters justified as representative",
            "simulation results supported by testbed or real-deployment evidence for any "
            "claim about practice",
            "fairness and interaction with existing congestion control evaluated",
            "measurement vantage points, duration and ethics described",
        ],
        red_flags=[
            "a deployment claim resting on simulation alone",
            "a new congestion control evaluated without competing flows",
            "a measurement study generalizing from a single vantage point",
        ],
    ),
}


def get(topic_id: str) -> Topic:
    return TOPICS[topic_id]
