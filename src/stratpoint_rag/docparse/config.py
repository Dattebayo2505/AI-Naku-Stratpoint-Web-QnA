"""docparse config — env-switched model, upload paths, and parse limits.

Read at call time (not import) so tests can monkeypatch the environment.
Mirrors ``rag/config.py``; that idiom is load-bearing for the test suite.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Imported, not duplicated — one URL, one timeout, one text model to change.
# Hop 2 runs on the same LLM_MODEL as the rest of the chat path; a second
# knob would let the extractor silently drift onto a different model than the
# one every latency decision in rag/config.py was made for.
from stratpoint_rag.rag.config import (
    llm_model,
    llm_timeout,
    nvidia_api_key,
    nvidia_base_url,
)

load_dotenv()

__all__ = [
    "EXTRACTION_MAX_TOKENS",
    "MAX_TOKENS",
    "TEMPERATURE",
    "VISION_TIMEOUT",
    "concurrency",
    "extraction_group_pages",
    "extraction_token_budget",
    "llm_model",
    "llm_timeout",
    "max_pages",
    "nvidia_api_key",
    "nvidia_base_url",
    "nvidia_vision_api_key",
    "text_layer_min_chars",
    "upload_dir",
    "upload_max_bytes",
    "upload_ttl_seconds",
    "vision_model",
]


# Tuning decisions belong in version control with a comment, not in .env.
#
# TEMPERATURE matches rag/answer.py — transcription is the least creative task
# in the system.
#
# MAX_TOKENS is deliberately 2048, not the 512 the endpoint probe used. 512 was
# never hit only because the probe page was sparse; a dense RFP page hits it,
# truncates mid-sentence, and looks like a successful parse. A low ceiling also
# actively encourages this model's characteristic failure mode — summarizing
# instead of transcribing.
TEMPERATURE = 0.1
MAX_TOKENS = 2048

# Per-page ceiling on one vision call, deliberately far below LLM_TIMEOUT (300s).
#
# A page normally returns in ~1.5-5s. But under rate limiting this endpoint
# throttles by DELAYING rather than returning 429 — one measured page took 173s
# against a 1.5s baseline, and tenacity never fired because the response was a
# perfectly good 200 that simply arrived late. At 300s a 20-page brief could
# block for the better part of an hour, long past any client timeout.
#
# 90s is ~4.5x the documented p95 (20s), so it does not clip a merely slow page,
# but it converts an indefinite stall into one recorded entry in pages_failed —
# the same soft degradation the rest of the page loop already assumes.
VISION_TIMEOUT = 90

# Hop-2 reply ceiling. An extraction over a 20-page RFP is a long JSON object —
# a dozen features, a dozen constraints — and truncation here does not look like
# a failure, it looks like a brief with fewer requirements than it has.
EXTRACTION_MAX_TOKENS = 2048


def _int_env(var: str, default: int) -> int:
    """Read an int from the environment, falling back on anything unparseable.

    A typo'd .env must not raise deep inside a page loop (cf. rag/config.py's
    llm_timeout).
    """
    val = os.getenv(var)
    try:
        return int(val) if val else default
    except ValueError:
        return default


def vision_model() -> str:
    val = os.getenv("VISION_MODEL")
    return val if val else "meta/llama-3.2-11b-vision-instruct"


def nvidia_vision_api_key() -> str:
    """The vision key, falling back to the text key.

    One NIM key works for any NIM model, so requiring a second one would 401 a
    contributor deep inside a page loop. A second account stays available as a
    quota lever (40 RPM per model is tight) but is not required.
    """
    val = os.getenv("NVIDIA_VISION_API_KEY") or os.getenv("NVIDIA_API_KEY")
    return val.strip() if val else ""


def upload_dir() -> str:
    val = os.getenv("UPLOAD_DIR")
    return val if val else "data/uploads"


def upload_ttl_seconds() -> int:
    return _int_env("UPLOAD_TTL_SECONDS", 3600)


def upload_max_bytes() -> int:
    return _int_env("UPLOAD_MAX_BYTES", 25_000_000)


def max_pages() -> int:
    """Hard cap on pages parsed per upload — abuse guard and latency guard.

    40 rather than 20 because real RFPs routinely run past 20 pages and the
    truncation is invisible in the answer: the brief is simply missing its back
    half. The cost is bounded by the text-layer route — a digital RFP of any
    length costs zero vision calls — so 40 only bites on a fully scanned
    document, where it is exactly the case worth covering.

    Sizing at the ceiling: 40 pages / concurrency 4 x ~5s is ~50s typical, and
    the worst case (every page stalling into VISION_TIMEOUT) is 10 x 90s = 900s,
    past the 300s parse timeout. A wholly-stalled parse was already going to
    fail at 20 pages (450s); raising the cap widens the band in which a *partly*
    slow scan times out client-side. Lower DOCPARSE_MAX_PAGES if that shows up.
    """
    return _int_env("DOCPARSE_MAX_PAGES", 40)


def concurrency() -> int:
    """Page-level worker count.

    Bounded by NIM's 40 requests/min per model: max_pages() x concurrency() is
    the exposure, and with a 40-page cap a *single* fully-scanned upload now
    spends the whole minute's quota. Raising this multiplies the burst, it does
    not shorten the queue.
    """
    return _int_env("DOCPARSE_CONCURRENCY", 4)


def extraction_token_budget() -> int:
    """Above this estimated prompt size, hop 2 switches to map-reduce.

    Llama 3.1 8B nominally handles 128k, but extraction quality collapses long
    before that (lost-in-the-middle) and the failure is SILENT: constraints
    buried on page 22 are dropped and the result is a clean, well-formed object
    that is simply missing half the brief. Nothing in the contract can express
    "I only read the first 12 pages."

    ~12k tokens is roughly 8-10 transcribed pages, which covers most real briefs
    outright; the 40-page cap from hop 1 bounds the worst case at 8 groups.
    """
    return _int_env("DOCPARSE_EXTRACTION_TOKEN_BUDGET", 12_000)


def extraction_group_pages() -> int:
    """Pages per map-reduce group."""
    return _int_env("DOCPARSE_EXTRACTION_GROUP_PAGES", 5)


def text_layer_min_chars() -> int:
    """Below this many extracted chars, a PDF page is rasterized for vision.

    Echoes the crawler's existing 200-char ``thin_content`` heuristic.
    """
    return _int_env("DOCPARSE_TEXT_LAYER_MIN_CHARS", 100)
