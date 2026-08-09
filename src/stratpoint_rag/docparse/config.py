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
    "figure_pass_min_text_chars",
    "figure_pass_novelty",
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
# truncates mid-sentence, and looks like a successful parse.
#
# On nemotron this ceiling is now pure insurance rather than an active guard:
# across six runs of a 10-page scan the longest completion was 448 tokens and
# every call finished with finish_reason="stop". Do not lower it on that basis —
# it only ever bites during a failure, and a truncated dense page is
# indistinguishable from a successful parse downstream.
TEMPERATURE = 0.1
MAX_TOKENS = 2048

# Per-page ceiling on one vision call, deliberately far below LLM_TIMEOUT (300s).
#
# The reason for having a ceiling is an ENDPOINT behaviour and did not change
# with the model: under rate limiting NVIDIA throttles by DELAYING rather than
# returning 429, so tenacity never fires — the response is a perfectly good 200
# that simply arrives late. A 4-image probe call was measured stalling past
# 300s. At LLM_TIMEOUT a 40-page scan could block for the better part of an
# hour, long past any client timeout.
#
# 45s is sized for nemotron: pages return in 3.5-19s at 1120px across six runs
# of a 10-page scan, so this is ~2.4x the slowest page observed and does not
# clip a merely slow one. It converts an indefinite stall into one recorded
# entry in pages_failed — the soft degradation the rest of the page loop
# already assumes.
VISION_TIMEOUT = 45

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
    """The hop-1 vision model.

    Nemotron rather than meta/llama-3.2-11b-vision-instruct since 2026-08-09.
    Measured on a 10-page RFP supplied both digitally and fully rasterized:
    1.000 content-word recall on every scanned page against the digital file's
    own text layer, at 3,755 prompt tokens per page against meta's 6,431, with
    no degeneration loops and no refusals in six runs.
    """
    val = os.getenv("VISION_MODEL")
    return val if val else "nvidia/nemotron-nano-12b-v2-vl"


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
    the worst case (every page stalling into VISION_TIMEOUT) is 10 x 45s = 450s,
    past the 300s parse timeout. A wholly-stalled parse was already going to
    fail at 20 pages; raising the cap widens the band in which a *partly* slow
    scan times out client-side. Lower DOCPARSE_MAX_PAGES if that shows up.
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


def figure_pass_novelty() -> float:
    """Below this novelty, a page's vision call is judged to have bought nothing.

    "Novelty" is the share of content words in the transcription that the page's
    embedded text layer did not already contain. A figure page whose vision call
    scores near zero returned only what a free text extraction would have — the
    ~6,400 tokens bought no information — which is exactly the case the figure
    pass exists to repair.

    Measured on a real RFP, transcription-pass novelty by page:

        page 1 (photo)         52.2%   worked
        page 6 (site plan)     32.1%   worked
        page 3 (two maps)      17.7%   worked, weakest success
        page 5 (two maps)       1.6%   returned the text layer and one word

    0.10 sits with margin on both sides of the gap between 1.6% and 17.7%.
    Raising it past ~0.15 starts paying for pages that did not need help.

    Since 2026-08-09 this is the *second* of two triggers, not the only one --
    ``transcribe._render_page`` fires the pass whenever no figure block came back
    at all, whatever the novelty. Page 6 above is why: its 32.1% is real, but all
    of it came from a colour legend the model read off the site plan into a
    Markdown table, and reading a legend is not describing a picture. Novelty
    answers "did the reply add words", which stops proxying for "did the model
    look at the picture" as soon as a legend or table sits beside the figure.

    The threshold is also applied to the figure pass's OWN reply, against the
    same text layer: the model returns the page's printed captions often enough
    that an unchecked block puts a re-typed caption in the artifact dressed as a
    description of a picture nothing looked at.
    """
    val = os.getenv("DOCPARSE_FIGURE_PASS_NOVELTY")
    try:
        return float(val) if val else 0.10
    except ValueError:
        return 0.10


def figure_pass_min_text_chars() -> int:
    """Novelty is only meaningful against a text layer worth comparing to.

    A scanned page has no text layer, so *everything* the model returns is
    "novel" and the ratio says nothing about whether the figure was read. Below
    this many characters the figure pass is skipped rather than guessed at —
    the transcription pass is doing the whole job there anyway.
    """
    return _int_env("DOCPARSE_FIGURE_PASS_MIN_TEXT_CHARS", 200)


def text_layer_min_chars() -> int:
    """Below this many extracted chars, a PDF page is rasterized for vision.

    Echoes the crawler's existing 200-char ``thin_content`` heuristic.
    """
    return _int_env("DOCPARSE_TEXT_LAYER_MIN_CHARS", 100)
