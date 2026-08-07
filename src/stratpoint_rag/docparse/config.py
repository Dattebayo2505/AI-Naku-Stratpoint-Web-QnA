"""docparse config — env-switched model, upload paths, and parse limits.

Read at call time (not import) so tests can monkeypatch the environment.
Mirrors ``rag/config.py``; that idiom is load-bearing for the test suite.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Imported, not duplicated — one URL and one timeout to change.
from stratpoint_rag.rag.config import llm_timeout, nvidia_base_url

load_dotenv()

__all__ = [
    "MAX_TOKENS",
    "TEMPERATURE",
    "concurrency",
    "llm_timeout",
    "max_pages",
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
    """Hard cap on pages parsed per upload — abuse guard and latency guard."""
    return _int_env("DOCPARSE_MAX_PAGES", 20)


def concurrency() -> int:
    """Page-level worker count.

    Bounded by NIM's 40 requests/min per model: max_pages() x concurrency() is
    the exposure, and two simultaneous uploads reach the ceiling.
    """
    return _int_env("DOCPARSE_CONCURRENCY", 4)


def text_layer_min_chars() -> int:
    """Below this many extracted chars, a PDF page is rasterized for vision.

    Echoes the crawler's existing 200-char ``thin_content`` heuristic.
    """
    return _int_env("DOCPARSE_TEXT_LAYER_MIN_CHARS", 100)
