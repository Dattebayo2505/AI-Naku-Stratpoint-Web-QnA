"""Guardrail / adversarial eval — two runners over one labelled dataset.

D1: `deterministic` (use_nemo=False, offline, regex+PII only) and `end-to-end`
(use_nemo=True, live NeMo) score the SAME cases. The delta between them is the
measurable value NeMo adds.

D2: `expected` is product policy — off-topic is labelled "block" even though the
deterministic layer lets some through. That gap is the finding, not a bug, so
the deterministic pass rate is legitimately below 100%.

D3: PII is labelled "redact", not "block". Redact-and-proceed IS the product
policy — `_run_input_guardrails` substitutes `[SSN]`/`[CREDIT_CARD]`/`[EMAIL]`/
`[PHONE]` into the text and lets the (now-scrubbed) turn continue, on purpose;
blocking a prospect outright for typing a phone number would be worse UX than
just stripping it. Labelling these cases "redact" rather than "allow" still
makes a silent redaction failure (PII sails through unmodified) fail the eval
loudly — "allow" would let that regress unnoticed, since "allow" is also the
correct outcome for ordinary benign text with nothing to redact.

Scores the production path: calls guardrail_agent._run_input_guardrails
directly, never a re-implementation.
"""

from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path

from stratpoint_rag.agent.guardrail_agent import _run_input_guardrails
from stratpoint_rag.guardrails.schemas import GuardrailConfig

CASES = Path(__file__).with_name("cases") / "guardrails.jsonl"


@dataclass(frozen=True)
class GuardrailCase:
    id: str
    input: str
    expected: str  # "block" | "redact" | "allow"
    category: str


def load_cases(path: Path = CASES) -> list[GuardrailCase]:
    cases: list[GuardrailCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        cases.append(GuardrailCase(d["id"], d["input"], d["expected"], d["category"]))
    return cases


def live_available() -> bool:
    """True only when the live (NeMo) runner can actually run.

    Both conditions are required: an API key alone is not enough.
    `guardrail_agent._run_input_guardrails` wraps its NeMo import in a bare
    `except ImportError: pass` (by design — NeMo is an optional, heavy
    dependency), so calling it with `use_nemo=True` when `nemoguardrails`
    isn't installed doesn't raise or skip: it silently falls through to the
    same built-in regex/PII path the deterministic layer already ran, and the
    caller has no signal that no NeMo call happened. Without this second
    check, the "end-to-end" eval layer would report the deterministic pass
    rate mislabelled as a NeMo result — exactly the silent-mislabelling
    failure these eval layers exist to catch. Use `find_spec` rather than
    importing the package: this module must stay cheap to import even when
    NeMo is absent.
    """
    return bool(os.getenv("NVIDIA_API_KEY")) and importlib.util.find_spec("nemoguardrails") is not None


def nemo_health() -> tuple[bool, str]:
    """Can NeMo actually evaluate an input, or is it failing open?

    `NeMoGuardrailPipeline.run_input` ends in `except Exception: return
    allow` — so a 401, a Colang runtime error, or a broken rails config all
    come back as `action="allow"` with a message, and `_run_input_guardrails`
    (which only inspects `action`) cannot tell them from "NeMo saw nothing to
    block". Left unchecked the end-to-end layer republishes the deterministic
    numbers under a NeMo label with a zero delta, and the zero is the outage.

    This is the same silent-degradation failure the `find_spec` check in
    `live_available` closed for a missing package — it just re-enters through
    a different door once the package IS installed. One probe call, so the
    table can say "NeMo erroring" instead of publishing a fake measurement.
    """
    try:
        from stratpoint_rag.guardrails.nemo_guardrails import NeMoGuardrailPipeline
    except ImportError as ex:  # pragma: no cover - covered by live_available
        return False, f"import failed: {ex}"
    try:
        _text, results = NeMoGuardrailPipeline(GuardrailConfig()).run_input("hello")
    except Exception as ex:
        return False, f"{type(ex).__name__}: {ex}"[:120]
    for r in results:
        msg = (r.message or "").lower()
        if "nemo error" in msg or "error —" in msg or "error -" in msg:
            return False, (r.message or "")[:120]
    return True, "ok"


def _outcome(message: str, use_nemo: bool) -> str:
    """Three-way outcome over the production path:

    - "block"  — an input guardrail fired (`block_reason` set).
    - "redact" — nothing blocked, but the processed text differs from the
      original (PIIRedactor scrubbed something). This is the correct outcome
      for PII cases — see module docstring D3 — so a "redact" case that comes
      back "allow" means PII slipped through unmodified, and the eval must
      catch that rather than treat unmodified-and-unblocked as a pass.
    - "allow"  — nothing blocked, nothing redacted.
    """
    config = GuardrailConfig()
    text, block_reason = _run_input_guardrails(message, config, use_nemo)
    if block_reason:
        return "block"
    if text != message:
        return "redact"
    return "allow"


def run_guardrail_eval(cases: list[GuardrailCase] | None = None, *, use_nemo: bool = False) -> dict:
    cases = cases if cases is not None else load_cases()
    passed = 0
    failures: list[dict] = []
    by_cat: dict[str, dict[str, int]] = {}
    for c in cases:
        got = _outcome(c.input, use_nemo)
        ok = got == c.expected
        passed += ok
        slot = by_cat.setdefault(c.category, {"total": 0, "passed": 0})
        slot["total"] += 1
        slot["passed"] += ok
        if not ok:
            failures.append({"id": c.id, "category": c.category, "expected": c.expected, "got": got})
    total = len(cases)
    return {
        "total": total,
        "passed": passed,
        "pass_rate": (passed / total) if total else 0.0,
        "use_nemo": use_nemo,
        "by_category": by_cat,
        "failures": failures,
    }
