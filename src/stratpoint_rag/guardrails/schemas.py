from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RedactionRule(BaseModel):
    pattern: str
    replacement: str = "[REDACTED]"
    entity_type: str = "unknown"


class GuardrailResult(BaseModel):
    passed: bool = True
    action: Literal["allow", "block", "redact", "escalate"] = "allow"
    message: str = ""
    modified_input: str | None = None
    modified_output: str | None = None


class GuardrailConfig(BaseModel):
    mode: Literal["fail_open", "fail_closed"] = "fail_closed"
    redact_pii: bool = True
    filter_topic: bool = True
    block_keywords: bool = True
    check_hallucination: bool = True
    check_advice: bool = True
