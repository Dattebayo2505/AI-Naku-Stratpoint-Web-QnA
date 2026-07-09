"""Lightweight, toggleable latency instrumentation.

Set TIMING=0 in the environment to silence all of it. On by default so the
E2E/Docker logs show per-stage timings without extra flags.

Two things are logged:
  * one-time cold-build costs (NeMo LLMRails, the embedder) via note() — these
    are what the process-wide caches save on every request after the first;
  * per-request stage breakdowns via timed()/log_total() in run_with_guardrails.

A dedicated logger with its own StreamHandler is used so timings are visible
even when uvicorn/pytest hasn't configured the root logger.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager

_LOG = logging.getLogger("stratpoint_rag.timing")


def enabled() -> bool:
    return os.getenv("TIMING", "1").lower() not in ("0", "false", "no", "")


# Guarantee the timing lines actually surface (docker/uvicorn/pytest all differ
# in how they configure logging). Attach one handler, don't propagate to root.
if enabled() and not _LOG.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    _LOG.addHandler(_h)
    _LOG.setLevel(logging.INFO)
    _LOG.propagate = False


def note(msg: str) -> None:
    """Log a one-off timing note (e.g. a cold build cost)."""
    if enabled():
        _LOG.info("⏱  %s", msg)


@contextmanager
def timed(stage: str, store: dict[str, float] | None = None):
    """Time a block; record ms into `store[stage]` and log a line."""
    if not enabled():
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - t0) * 1000
        if store is not None:
            store[stage] = ms
        _LOG.info("⏱  %-22s %8.0f ms", stage, ms)


def log_total(stages: dict[str, float], total_ms: float) -> None:
    if not enabled():
        return
    breakdown = "  ".join(f"{k}={v:.0f}" for k, v in stages.items())
    _LOG.info("⏱  %-22s %8.0f ms   [%s]", "TOTAL /chat", total_ms, breakdown)
