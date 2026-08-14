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
    "figure_pass_max_pages",
    "figure_pass_min_text_chars",
    "figure_pass_novelty",
    "llm_model",
    "llm_timeout",
    "max_pages",
    "nvidia_api_key",
    "nvidia_base_url",
    "nvidia_vision_api_key",
    "soffice_binary",
    "soffice_timeout",
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

    That second application was silently discarding correct work until
    2026-08-09, and the threshold was not at fault — ``_content_words`` was.
    Numbers were not counted, so page 5's maps ("Civic Park - 2023", "Tower Park
    - 2025"), whose place names the page's prose already uses, scored 0.048 and
    were dropped 16 times out of 16. See ``transcribe._WORD_RE``. Do not read
    the four percentages above as a defence of 0.10 against that failure; they
    were measured with the same blind spot, and only the ranking survives it.
    """
    val = os.getenv("DOCPARSE_FIGURE_PASS_NOVELTY")
    try:
        return float(val) if val else 0.10
    except ValueError:
        return 0.10


def figure_pass_min_text_chars() -> int:
    """Novelty is only meaningful against a baseline worth comparing to.

    A scanned page has no text layer, so *everything* the model returns is
    "novel" and the ratio says nothing about whether the figure was read. Below
    this many characters the NOVELTY trigger is skipped rather than guessed at.

    Corrected 2026-08-09: this used to gate the whole figure pass, and its
    justification ran "the transcription pass is doing the whole job there
    anyway". It is not. Measured on the same 10-page RFP supplied digitally and
    fully rasterized, the figure pass could fire on 4 of 10 digital pages and
    **0 of 10** scanned ones, so a full-page cover photo and two site plans the
    digital route described went undescribed on the scan. Only the novelty
    trigger needs a baseline; "did a figure block come back" needs nothing. See
    ``transcribe._render_page``.

    On a page with no text layer the baseline is the transcription pass's own
    reply instead — measured at 1.000 content-word recall against the digital
    copy's text layer, so it is the closest thing to ground truth available and
    it keeps the caption-echo check alive where it would otherwise be lost.
    """
    return _int_env("DOCPARSE_FIGURE_PASS_MIN_TEXT_CHARS", 200)


def figure_pass_min_novel_words() -> int:
    """Novel content words that keep a figure-pass reply whatever its ratio.

    ``figure_pass_novelty`` is a proportion, so a reply that reads the picture
    correctly and *then* restates the page's prose around it is punished for the
    padding. Traced live on RFP page 5: of two replies that recovered "Civic Park
    - 2023" and "Tower Park - 2025" off the map, one scored 0.154 and was kept
    and the other 0.065 and was dropped, the only difference being how much of
    the page body it repeated back.

    "Did this reply bring anything back" is a count, not a proportion. Measured
    on the corpus: a reply that read the maps carries 4 novel content words, a
    table misread as a picture carries 1 ("employer's"), and a pure caption echo
    carries 0. 3 sits in that gap with margin on both sides, the same way 0.10
    sits between 1.6% and 17.7%.

    Raising this re-opens the dilution hole; lowering it to 1 lets a single
    stray token pass a re-typed caption through.
    """
    return _int_env("DOCPARSE_FIGURE_PASS_MIN_NOVEL_WORDS", 3)


def figure_pass_max_pages() -> int:
    """Ceiling on second calls per document. Cost guard, not a quality knob.

    Without a text layer the novelty trigger is unavailable, so the gate rests
    on "no figure block came back" — which is also true of every ordinary text
    page of a scanned brief. Measured on the 10-page scan: 9 of 10 pages carried
    no figure block, so an uncapped rule takes that parse from 10 vision calls
    to 19, and a 40-page scan from 40 to 80.

    12 covers a typical brief outright — the failure this exists to bound is the
    40-page ceiling, not the 10-page case. Note honestly that 40 + 12 = 52 calls
    still exceeds NIM's 40 requests/min: a *fully scanned* 40-page upload was
    already spending the whole minute's quota before this cap existed (see
    ``concurrency``), and the cap bounds the overrun rather than removing it.
    Lower it, or ``DOCPARSE_MAX_PAGES``, if throttling shows up as timeouts.

    Exhausting the budget is stamped into the page's provenance comment. A
    silent cap would read as "this page had no figure" when it means "nobody
    looked" — the same distinction ``pages_failed`` exists to preserve.
    """
    return _int_env("DOCPARSE_FIGURE_PASS_MAX_PAGES", 12)


def text_layer_min_chars() -> int:
    """Below this many extracted chars, a PDF page is rasterized for vision.

    Echoes the crawler's existing 200-char ``thin_content`` heuristic.
    """
    return _int_env("DOCPARSE_TEXT_LAYER_MIN_CHARS", 100)


def soffice_binary() -> str:
    """Explicit path to the LibreOffice binary; '' means auto-discover.

    LibreOffice is a hard dependency of the deck path but it is a system
    package, not a Python one, so no installer we ship puts it on PATH. It
    routinely is not on PATH on Windows at all. This is the escape hatch;
    ``slides.find_soffice`` owns the fallback search.
    """
    val = os.getenv("SOFFICE_BINARY")
    return val.strip() if val else ""


def soffice_timeout() -> int:
    """Seconds a single deck conversion may take before the child is killed.

    Same reasoning as VISION_TIMEOUT: a hung soffice would otherwise block an
    upload request indefinitely, and this converts that into one clean
    ConversionFailed. 120s is generous — a 40-slide deck converts in a few
    seconds — because the cost of clipping a merely slow conversion is a
    rejected upload, while the cost of the ceiling being loose is bounded.
    """
    return _int_env("SOFFICE_TIMEOUT", 120)
