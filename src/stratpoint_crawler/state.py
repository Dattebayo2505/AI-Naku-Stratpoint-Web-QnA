import json
from pathlib import Path

from .models import PageRef


def load_previous(index_path: Path) -> dict[str, dict]:
    """Map url -> the full prior index.jsonl record. Empty if the file is absent."""
    index_path = Path(index_path)
    if not index_path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        url = row.get("url")
        if url:
            out[url] = row
    return out


def load_last_successful_scrape(report_path: Path) -> str | None:
    """Read ``last_successful_scrape`` from a prior run_report.json, or None."""
    report_path = Path(report_path)
    if not report_path.exists():
        return None
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    value = data.get("last_successful_scrape")
    return value if isinstance(value, str) else None


def resolve_last_successful_scrape(*, succeeded: int, crawled_at: str,
                                   previous: str | None) -> str | None:
    """When was the corpus last actually scraped?

    A run counts as the latest scrape only if it fetched at least one page
    (``succeeded > 0``). An all-skip or all-fail run is not a new scrape, so
    the previous value is carried forward unchanged.
    """
    return crawled_at if succeeded > 0 else previous


def should_recrawl(ref: PageRef, prev_record: dict | None, force: bool = False) -> bool:
    """Decide whether a page must be fetched this run.

    Skip (return False) only for a page that previously succeeded and whose
    sitemap lastmod is unchanged. Anything else — forced, new, previously
    failed, or missing a lastmod on either side — is recrawled, because we
    must never skip on an absent or unreliable signal.
    """
    if force or prev_record is None:
        return True
    if prev_record.get("status") not in ("ok", "skipped"):
        return True
    prev_lastmod = prev_record.get("lastmod")
    if not prev_lastmod or not ref.lastmod:
        return True
    return ref.lastmod != prev_lastmod
