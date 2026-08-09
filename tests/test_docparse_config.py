"""docparse config: env read at call time, with the documented defaults.

Mirrors tests/test_config.py's treatment of rag/config.py. Every test clears
the relevant vars first because config.py calls load_dotenv() at import, so a
developer's real .env is already in os.environ by the time these run.
"""

from __future__ import annotations

import pytest

from stratpoint_rag.docparse import config


_ALL_VARS = (
    "VISION_MODEL",
    "NVIDIA_VISION_API_KEY",
    "NVIDIA_API_KEY",
    "UPLOAD_DIR",
    "UPLOAD_TTL_SECONDS",
    "UPLOAD_MAX_BYTES",
    "DOCPARSE_MAX_PAGES",
    "DOCPARSE_CONCURRENCY",
    "DOCPARSE_TEXT_LAYER_MIN_CHARS",
)


@pytest.fixture
def clean_env(monkeypatch):
    for var in _ALL_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.mark.parametrize(
    "fn_name, expected",
    [
        ("vision_model", "nvidia/nemotron-nano-12b-v2-vl"),
        ("upload_dir", "data/uploads"),
        ("upload_ttl_seconds", 3600),
        ("upload_max_bytes", 25_000_000),
        ("max_pages", 40),
        ("concurrency", 4),
        ("text_layer_min_chars", 100),
    ],
)
def test_defaults_when_unset(clean_env, fn_name, expected):
    assert getattr(config, fn_name)() == expected


@pytest.mark.parametrize(
    "fn_name, var, raw, expected",
    [
        ("vision_model", "VISION_MODEL", "some/other-vlm", "some/other-vlm"),
        ("upload_dir", "UPLOAD_DIR", "/srv/uploads", "/srv/uploads"),
        ("upload_ttl_seconds", "UPLOAD_TTL_SECONDS", "60", 60),
        ("upload_max_bytes", "UPLOAD_MAX_BYTES", "500", 500),
        ("max_pages", "DOCPARSE_MAX_PAGES", "5", 5),
        ("concurrency", "DOCPARSE_CONCURRENCY", "1", 1),
        ("text_layer_min_chars", "DOCPARSE_TEXT_LAYER_MIN_CHARS", "0", 0),
    ],
)
def test_env_overrides_default(clean_env, fn_name, var, raw, expected):
    clean_env.setenv(var, raw)
    assert getattr(config, fn_name)() == expected


def test_env_is_read_at_call_time_not_import(clean_env):
    """The load-bearing idiom: rag/config.py:3 says so, and the suite relies on it."""
    assert config.max_pages() == 40
    clean_env.setenv("DOCPARSE_MAX_PAGES", "7")
    assert config.max_pages() == 7


@pytest.mark.parametrize(
    "fn_name, var, default",
    [
        ("upload_ttl_seconds", "UPLOAD_TTL_SECONDS", 3600),
        ("upload_max_bytes", "UPLOAD_MAX_BYTES", 25_000_000),
        ("max_pages", "DOCPARSE_MAX_PAGES", 40),
        ("concurrency", "DOCPARSE_CONCURRENCY", 4),
        ("text_layer_min_chars", "DOCPARSE_TEXT_LAYER_MIN_CHARS", 100),
    ],
)
def test_unparseable_int_falls_back_to_default(clean_env, fn_name, var, default):
    """A typo'd .env must not crash a request mid-page-loop (rag/config.py:57-62)."""
    clean_env.setenv(var, "not-a-number")
    assert getattr(config, fn_name)() == default


# ── the vision key fallback ─────────────────────────────────────────────────


def test_vision_key_prefers_the_dedicated_var(clean_env):
    clean_env.setenv("NVIDIA_VISION_API_KEY", "nvapi-vision")
    clean_env.setenv("NVIDIA_API_KEY", "nvapi-text")
    assert config.nvidia_vision_api_key() == "nvapi-vision"


def test_vision_key_falls_back_to_the_text_key(clean_env):
    """One key works for any NIM model, so a second key must not be required."""
    clean_env.setenv("NVIDIA_API_KEY", "nvapi-text")
    assert config.nvidia_vision_api_key() == "nvapi-text"


def test_vision_key_falls_back_when_dedicated_var_is_blank(clean_env):
    """.envexample ships `NVIDIA_VISION_API_KEY=` — blank must not shadow the fallback."""
    clean_env.setenv("NVIDIA_VISION_API_KEY", "")
    clean_env.setenv("NVIDIA_API_KEY", "nvapi-text")
    assert config.nvidia_vision_api_key() == "nvapi-text"


def test_vision_key_is_stripped(clean_env):
    clean_env.setenv("NVIDIA_VISION_API_KEY", "  nvapi-vision\n")
    assert config.nvidia_vision_api_key() == "nvapi-vision"


def test_vision_key_is_empty_when_neither_is_set(clean_env):
    assert config.nvidia_vision_api_key() == ""


# ── shared with rag ─────────────────────────────────────────────────────────


def test_base_url_is_reexported_from_rag_config():
    """Imported, not duplicated — one URL to change."""
    from stratpoint_rag.rag import config as rag_config

    assert config.nvidia_base_url is rag_config.nvidia_base_url


# ── tuning constants live in version control, not env ───────────────────────


def test_tuning_constants_are_module_constants():
    assert config.TEMPERATURE == 0.1
    assert config.MAX_TOKENS == 2048
