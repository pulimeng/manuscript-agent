"""Agentic peer review: reviewers and an editor read your compiled manuscript; you revise."""

from .build import BuildResult, compile_pdf
from .config import MODEL_POOL, VENUES, Persona, RunConfig, Venue
from .history import SubmissionHistory
from .llm import LLM, Attachment
from .manuscript import Manuscript
from .package import Package, PdfSubmission
from .providers import ModelSpec, OpenAILLM
from .versions import Version, VersionStore

__all__ = [
    "Attachment",
    "BuildResult",
    "LLM",
    "MODEL_POOL",
    "Manuscript",
    "ModelSpec",
    "OpenAILLM",
    "Package",
    "PdfSubmission",
    "Persona",
    "RunConfig",
    "SubmissionHistory",
    "VENUES",
    "Venue",
    "Version",
    "VersionStore",
    "compile_pdf",
]
__version__ = "0.4.0"
