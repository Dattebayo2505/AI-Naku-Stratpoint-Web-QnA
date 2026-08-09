"""pdf_gen config — storage paths, render timeouts, browser flags, branding.

Read at call time (not import) so tests can monkeypatch the environment.
Mirrors ``docparse/config.py``; that idiom is load-bearing for the test suite.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

__all__ = [
    "browser_args",
    "chromium_executable",
    "company_email",
    "company_name",
    "company_website",
    "logo_path",
    "payment_terms",
    "pdf_max_concurrency",
    "pdf_timeout_ms",
    "proposal_dir",
    "proposal_ttl_seconds",
    "quote_valid_days",
    "tax_rate_percent",
]


def _int_env(var: str, default: int) -> int:
    """Read an int from the environment, falling back on anything unparseable.

    A typo'd .env must not raise in the middle of rendering a client's quote.
    """
    val = os.getenv(var)
    try:
        return int(val) if val else default
    except ValueError:
        return default


def proposal_dir() -> str:
    return os.getenv("PROPOSAL_DIR") or "data/proposals"


def proposal_ttl_seconds() -> int:
    """Older proposals are swept on the next render.

    Longer than the 1h upload TTL: a visitor may reasonably come back to a
    download link after the brief that produced it has been cleaned up, and the
    PDF is a few hundred KB against the brief's tens of MB.
    """
    return _int_env("PROPOSAL_TTL_SECONDS", 86_400)


def pdf_timeout_ms() -> int:
    """Ceiling on one HTML->PDF render.

    Sized like ``docparse``'s VISION_TIMEOUT and for the same reason: the
    failure mode of a headless browser is a stall, not an exception. Chromium
    waiting on a font it can never fetch never returns, and without a ceiling
    that becomes an indefinitely hung /chat turn. A two-page quote with no
    external assets renders in well under 2s; 30s is roughly 15x that.
    """
    return _int_env("PDF_TIMEOUT_MS", 30_000)


def pdf_max_concurrency() -> int:
    """Concurrent Chromium instances allowed across the process.

    Each render launches its own browser — the sync Playwright API is not
    shareable across threads, and FastAPI runs sync endpoints in a threadpool,
    so a shared instance is not on the table. A Chromium is ~120MB resident, and
    the deployment target is a 6GB LXC also hosting the model, so this is a
    memory guard rather than a throughput knob. Raising it does not make one
    render faster.
    """
    return max(1, _int_env("PDF_MAX_CONCURRENCY", 2))


def browser_args() -> list[str]:
    """Chromium launch flags.

    ``--no-sandbox`` because the deployment target is an unprivileged LXC, where
    Chromium's setuid sandbox cannot initialise and the browser exits at launch.
    ``--disable-dev-shm-usage`` because containers default to a 64MB /dev/shm,
    which Chromium exhausts and then crashes mid-render — a failure that looks
    like a corrupt PDF rather than an out-of-space error.
    """
    extra = os.getenv("PDF_BROWSER_ARGS")
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    return args + [a for a in (extra or "").split() if a]


def chromium_executable() -> str | None:
    """Explicit Chromium path, for a container with a system browser installed
    instead of ``playwright install chromium``. None = Playwright's own."""
    return os.getenv("PDF_CHROMIUM_PATH") or None


# ── branding defaults (overridable per render) ──────────────────────────────


def company_name() -> str:
    return os.getenv("PROPOSAL_COMPANY_NAME") or "Stratpoint Technologies"


def company_email() -> str | None:
    return os.getenv("PROPOSAL_COMPANY_EMAIL") or None


def company_website() -> str | None:
    return os.getenv("PROPOSAL_COMPANY_WEBSITE") or "stratpoint.com"


def logo_path() -> str | None:
    """Local image inlined as a data: URI at render time.

    A *path*, never a URL: the renderer blocks the network, so an http(s) logo
    would silently vanish from the PDF instead of failing loudly.
    """
    return os.getenv("PROPOSAL_LOGO_PATH") or None


def quote_valid_days() -> int:
    return _int_env("PROPOSAL_VALID_DAYS", 30)


def tax_rate_percent() -> str:
    """Kept as a string so ``Decimal`` parses it exactly (see schema.money)."""
    return os.getenv("PROPOSAL_TAX_RATE_PERCENT") or "0"


def payment_terms() -> str:
    return (
        os.getenv("PROPOSAL_PAYMENT_TERMS")
        or "30% on signing, 40% at mid-project acceptance, 30% on final delivery. "
        "Invoices are due 15 days from issue."
    )
