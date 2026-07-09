"""Regression tests for handoff defects F1, F2, F3.

Offline, deterministic, no network (no NVIDIA_API_KEY needed — the classifier's
LLM fallback returns None without a key, so routing is heuristic-only here).
"""
from __future__ import annotations

from stratpoint_rag.rag.models import Chunk


def _chunk(text: str) -> Chunk:
    return Chunk(id="1", slug="p", url="https://stratpoint.com/p/", title="P", text=text)


# ── F1: PII phone-regex must not eat legitimate numeric facts ────────────────

def test_f1_numeric_fact_present_in_source_is_not_redacted():
    from stratpoint_rag.guardrails.output_guardrails import OutputPIIChecker

    answer = "The AWS migration keeps the website at 99.99% operational."
    source = [_chunk("Solaire runs at 99.99% uptime after the AWS migration.")]
    result = OutputPIIChecker().check(answer, source)
    # Present in source → must serve the answer verbatim, not a [PHONE]-mangled copy.
    assert result.modified_output is None
    assert "[PHONE]" not in (result.modified_output or answer)


def test_f1_phone_regex_ignores_percentages_versions_years():
    from stratpoint_rag.guardrails.input_guardrails import PIIRedactor

    r = PIIRedactor()
    for benign in ("99.99%", "MySQL 5.5", "2020/09/30", "version 1.2.3.4"):
        redacted, matched = r.redact(benign)
        assert "[PHONE]" not in redacted, f"{benign!r} wrongly redacted"


def test_f1_real_phone_still_redacted():
    from stratpoint_rag.guardrails.input_guardrails import PIIRedactor

    redacted, _ = PIIRedactor().redact("call me at +63 917 123 4567 please")
    assert "[PHONE]" in redacted


def test_f1_output_only_pii_still_redacted():
    from stratpoint_rag.guardrails.output_guardrails import OutputPIIChecker

    answer = "Reach me at +63 917 123 4567."
    source = [_chunk("Stratpoint builds software.")]  # phone NOT in source
    result = OutputPIIChecker().check(answer, source)
    assert result.modified_output is not None
    assert "[PHONE]" in result.modified_output


# ── F2: injection block must never leak the internal category string ─────────

def test_f2_system_prompt_request_does_not_leak_category():
    from stratpoint_rag.agent.guardrail_agent import _user_facing_block, _INJECTION_BLOCK

    leaked = "Blocked: matched 'system_prompt_request'"
    assert _user_facing_block(leaked) == _INJECTION_BLOCK


def test_f2_no_blocked_category_leaks_raw_string():
    from stratpoint_rag.agent.guardrail_agent import (
        _user_facing_block, _INJECTION_BLOCK, _HARMFUL_BLOCK,
    )
    from stratpoint_rag.guardrails.input_guardrails import BLOCKED_PATTERNS

    friendly = {_INJECTION_BLOCK, _HARMFUL_BLOCK}
    for _, category in BLOCKED_PATTERNS:
        msg = _user_facing_block(f"Blocked: matched '{category}'")
        assert msg in friendly, f"category {category!r} leaked: {msg!r}"


# ── F3: consecutive un-answerable turns must eventually escalate, not loop ───

def test_f3_escalates_on_fourth_consecutive_unanswered_turn():
    from stratpoint_rag.agent.guardrail_agent import _escalate_or_count, _MAX_CLARIFY_ROUNDS
    from stratpoint_rag.guardrails.memory import ConversationMemory

    mem = ConversationMemory(session_id="t")
    # First _MAX rounds only count up — no escalation yet.
    for _ in range(_MAX_CLARIFY_ROUNDS):
        assert _escalate_or_count(mem) is False
    # The next un-answerable turn escalates and resets the counter.
    assert _escalate_or_count(mem) is True
    assert mem.clarify_streak == 0


def test_f3_streak_resets_so_it_only_fires_on_consecutive_runs():
    from stratpoint_rag.agent.guardrail_agent import _escalate_or_count
    from stratpoint_rag.guardrails.memory import ConversationMemory

    mem = ConversationMemory(session_id="t")
    _escalate_or_count(mem)
    _escalate_or_count(mem)
    mem.clarify_streak = 0  # a grounded answer intervened
    # Two more un-answerable turns must NOT escalate — the run was broken.
    assert _escalate_or_count(mem) is False
    assert _escalate_or_count(mem) is False


def test_f3_answer_turn_only_counts_explicit_ungrounded():
    """None (resource delivery / parse-fallback) must NOT advance the streak;
    only an explicit is_grounded=False counts. Grounded resets."""
    from stratpoint_rag.agent.guardrail_agent import _escalation_for_answer, _ESCALATION_RESPONSE
    from stratpoint_rag.guardrails.memory import ConversationMemory

    mem = ConversationMemory(session_id="t")
    # None: ambiguous (e.g. a successful resource delivery) — streak untouched.
    for _ in range(10):
        assert _escalation_for_answer(mem, None) is None
    assert mem.clarify_streak == 0

    # Explicit ungrounded runs count and eventually escalate.
    assert _escalation_for_answer(mem, False) is None  # 1
    assert _escalation_for_answer(mem, False) is None  # 2
    assert _escalation_for_answer(mem, False) is None  # 3
    assert _escalation_for_answer(mem, False) == _ESCALATION_RESPONSE  # 4 → hand-off

    # A grounded answer resets.
    mem.clarify_streak = 2
    assert _escalation_for_answer(mem, True) is None
    assert mem.clarify_streak == 0
