"""Prompt engineering module: schemas, few-shot examples, system prompts, prompt builder, registry, and ablation runner.
"""

from __future__ import annotations

from .builder import build_prompt
from .schema import Citation, GroundedAnswer

__all__ = ["build_prompt", "Citation", "GroundedAnswer"]
