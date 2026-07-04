"""Variant configuration registry (plan §5.5).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VariantConfig:
    use_schema: bool
    temperature: float
    top_p: float = 0.95


PROMPT_VARIANTS: dict[str, VariantConfig] = {
    "v0_zeroshot": VariantConfig(use_schema=False, temperature=0.7),
    "v1_fewshot": VariantConfig(use_schema=False, temperature=0.7),
    "v2_cot": VariantConfig(use_schema=True, temperature=0.3),
    "v3_role_structured": VariantConfig(use_schema=True, temperature=0.3),
    "v4_combined_lowtemp": VariantConfig(use_schema=True, temperature=0.1),
    "v4_combined_hightemp": VariantConfig(use_schema=True, temperature=0.8),
}
