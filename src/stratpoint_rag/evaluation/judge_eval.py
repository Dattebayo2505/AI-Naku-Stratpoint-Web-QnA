"""LLM-as-judge (Component #13) — absolute scoring of proposal quality.

Absolute, not pairwise: there is one system under test, so there is no second
proposal to compare against. Per Week 11, the prompt carries clear criteria, a
scoring rubric, chain-of-thought BEFORE the verdict, and structured JSON out.

Bias handling, disclosed rather than claimed solved:
  - positional: N/A to absolute scoring (no A/B order to flip).
  - length: the rubric scores named criteria; report proposal length alongside
    the score so a length effect stays visible.
  - self-preference: the judge runs on the SAME NIM endpoint as the system under
    test. This is a real, UNMITIGATED limitation — stated in the write-up.

Reuses rag.config for endpoint/key/model — same client shape as rag/answer.py.
"""

from __future__ import annotations

import html as html_mod
import json
import re
from typing import TYPE_CHECKING

import httpx

from stratpoint_rag.rag import config

if TYPE_CHECKING:
    from stratpoint_rag.evaluation.harness import LayerResult

JUDGE_SYSTEM = (
    "You are a strict evaluator of software project PROPOSALS. Score the proposal "
    "from 1 to 5 on these criteria, weighted equally:\n"
    "  1. Scope clarity — are the requirements and deliverables concrete?\n"
    "  2. Pricing sanity — are roles, rates, and totals internally consistent?\n"
    "  3. Timeline realism — do phases and durations fit the scope?\n"
    "  4. Professionalism — is it client-ready and free of placeholders?\n\n"
    "First reason step by step about each criterion. THEN output a single JSON "
    'object on the last line: {"score": <int 1-5>, "rationale": "<one sentence>"}.\n'
    "The score is the rounded average of the four criteria."
)


def build_judge_prompt(proposal_text: str) -> str:
    # Names "score" and "json" explicitly in the user turn (not just the system
    # prompt) — build_judge_prompt is exercised on its own by the offline test,
    # so the instruction has to survive being read in isolation from JUDGE_SYSTEM.
    return (
        "Evaluate this proposal. Score it 1-5 per the rubric and answer with JSON "
        f"on the last line.\n\n---\n{proposal_text}\n---"
    )


def live_available() -> bool:
    try:
        return bool(config.nvidia_api_key())
    except Exception:
        return False


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    return s.strip()


def _last_json_object(text: str) -> dict:
    # Balanced-JSON extraction, scanning candidate '{' positions from the END
    # backwards: a naive `\{[^{}]*\}` regex excludes braces from its own match,
    # so a rationale containing a literal brace (e.g. "{placeholder}") splits
    # into multiple fragments and picks the wrong one. raw_decode lets the
    # stdlib json parser find the real matching close-brace instead.
    dec = json.JSONDecoder()
    for i in range(len(text) - 1, -1, -1):
        if text[i] != "{":
            continue
        try:
            obj, _ = dec.raw_decode(text[i:])
        except ValueError:
            continue
        if isinstance(obj, dict) and "score" in obj:
            return obj
    raise ValueError(f"no JSON object with a score in judge reply: {text[:120]!r}")


def parse_verdict(raw: str) -> dict:
    """Extract the last JSON object with a 1-5 integer score. Raises ValueError."""
    text = _strip_fence(raw)
    obj = _last_json_object(text)
    score = obj.get("score")
    if not isinstance(score, int) or not (1 <= score <= 5):
        raise ValueError(f"score out of range: {score!r}")
    return {"score": score, "rationale": str(obj.get("rationale", ""))}


def judge_proposal(proposal_text: str) -> dict:
    body = {
        "model": config.llm_model(),
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": build_judge_prompt(proposal_text)},
        ],
        "max_tokens": 512,
        "temperature": 0.0,  # a judge should be as deterministic as the endpoint allows
        "stream": False,
    }
    resp = httpx.post(
        f"{config.nvidia_base_url()}/chat/completions",
        headers={"Authorization": f"Bearer {config.nvidia_api_key()}"},
        json=body,
        timeout=config.llm_timeout(),
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"].get("content") or ""
    v = parse_verdict(raw)
    v["length"] = len(proposal_text)  # keep the length signal visible (bias note)
    return v


def _html_to_text(html: str) -> str:
    """Strip script/style blocks and tags, collapse whitespace. The judge must
    see the proposal, not the stylesheet: this template's <style> block runs to
    character 13,673, so slicing raw HTML to 6,000 chars fed the model pure CSS.
    """
    txt = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html_mod.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


# ponytail: sample proposals for the judge come from generated HTML twins under
# data/proposals/. The layer() below scores those if present; wiring a curated
# proposal set is a follow-up, not needed for the layer to exist.
def _proposal_root():
    """Where the app actually writes proposals.

    Resolved through pdf_gen's own config rather than reconstructed from
    __file__: the container sets PROPOSAL_DIR=/app/proposals, so a path built
    from the source tree pointed at /app/data/proposals — a directory nothing
    writes to. The judge reported SKIP there while a stale copy under the
    read-only ./data mount could make it score proposals the running app never
    produced. One source of truth for the path is the fix.
    """
    from pathlib import Path

    from stratpoint_rag.pdf_gen import config as pdf_config

    return Path(pdf_config.proposal_dir())


def _sample_proposals() -> list[str]:
    root = _proposal_root()
    if not root.exists():
        return []
    samples: list[str] = []
    for p in root.rglob("*.html"):
        if len(samples) >= 10:
            break
        samples.append(_html_to_text(p.read_text(encoding="utf-8"))[:6000])
    return samples


def layer() -> LayerResult:
    # Deferred, not top-level: harness imports this module's `layer` to build
    # REGISTRY, so a module-level `from harness import LayerResult` here would
    # be a circular import (harness <-> judge_eval) that fails at import time
    # depending on which module is entered first.
    from stratpoint_rag.evaluation.harness import LayerResult

    if not live_available():
        return LayerResult("judge", "judge/proposal-quality", 0, 0,
                           detail="no NVIDIA_API_KEY", skipped=True)
    samples = _sample_proposals()
    if not samples:
        return LayerResult("judge", "judge/proposal-quality", 0, 0,
                           detail=f"no proposals in {_proposal_root()} — seed first",
                           skipped=True)
    # "passed" = score >= 3 (client-acceptable). Report count; the mean score is
    # the headline number for the slide. Failed calls are tracked, not just
    # dropped: a shrinking denominator can make a near-total measurement
    # failure (e.g. 9 of 10 timing out) read as a perfect 1/1 run.
    scores = []
    failed = 0
    for text in samples:
        try:
            scores.append(judge_proposal(text)["score"])
        except (httpx.HTTPError, ValueError):
            failed += 1
            continue
    if not scores:
        return LayerResult("judge", "judge/proposal-quality", 0, 0, detail="all judge calls failed", skipped=True)
    passed = sum(1 for s in scores if s >= 3)
    mean = sum(scores) / len(scores)
    detail = f"mean {mean:.2f}/5"
    if failed:
        detail += f", {failed} of {len(samples)} calls failed"
    return LayerResult("judge", "judge/proposal-quality", len(scores), passed,
                       detail=detail)
