"""Agentic manuscript writing, submission, peer review and revision."""

from .config import VENUES, Persona, RunConfig, Venue
from .build import BuildResult, compile_pdf
from .integrity import IntegrityReport, Violation
from .integrity import check as check_integrity
from .llm import LLM, Attachment
from .manuscript import Manuscript
from .pipeline import RunResult, SubmissionPipeline
from .providers import ModelSpec, OpenAILLM

__all__ = [
    "VENUES",
    "Attachment",
    "BuildResult",
    "IntegrityReport",
    "compile_pdf",
    "LLM",
    "ModelSpec",
    "OpenAILLM",
    "Violation",
    "check_integrity",
    "Manuscript",
    "Persona",
    "RunConfig",
    "RunResult",
    "SubmissionPipeline",
    "Venue",
]
__version__ = "0.1.0"
