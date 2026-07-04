"""Builds the system and user prompts consistently (plan §5.4).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .schema import GroundedAnswer
from .system_prompts import (
    SYSTEM_PROMPT_V0_ZEROSHOT,
    SYSTEM_PROMPT_V1_FEWSHOT,
    SYSTEM_PROMPT_V2_COT,
    SYSTEM_PROMPT_V3_ROLE_STRUCTURED,
    SYSTEM_PROMPT_V4_COMBINED,
)

if TYPE_CHECKING:
    from ..rag.models import Chunk


def build_prompt(query: str, chunks: list[Chunk], variant: str) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt) for the given variant.

    The user_prompt layout is held identical across all variants to isolate
    the system prompt as the sole independent variable.
    """
    # 1. Select the raw system prompt based on the variant
    if variant == "v0_zeroshot":
        sys_tmpl = SYSTEM_PROMPT_V0_ZEROSHOT
    elif variant == "v1_fewshot":
        sys_tmpl = SYSTEM_PROMPT_V1_FEWSHOT
    elif variant == "v2_cot":
        schema_json = json.dumps(GroundedAnswer.model_json_schema(), indent=2)
        sys_tmpl = SYSTEM_PROMPT_V2_COT.replace("{schema_format}", schema_json)
    elif variant == "v3_role_structured":
        schema_json = json.dumps(GroundedAnswer.model_json_schema(), indent=2)
        sys_tmpl = SYSTEM_PROMPT_V3_ROLE_STRUCTURED.replace("{schema_format}", schema_json)
    elif variant in ("v4_combined_lowtemp", "v4_combined_hightemp"):
        schema_json = json.dumps(GroundedAnswer.model_json_schema(), indent=2)
        sys_tmpl = SYSTEM_PROMPT_V4_COMBINED.replace("{schema_format}", schema_json)
    else:
        raise ValueError(f"Unknown prompt variant: {variant!r}")

    # 2. Format the user context and query identically across all variants
    context_blocks = []
    for c in chunks:
        # Title defaults to 'Stratpoint' if missing
        title = c.title if c.title else "Stratpoint"
        context_blocks.append(f"[Source: {title}] ({c.url})\n{c.text}")

    context_str = "\n---\n".join(context_blocks)

    user_prompt = f"""User Context:
---
{context_str}
---
Question: {query}
"""

    return sys_tmpl, user_prompt
