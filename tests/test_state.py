import json

from stratpoint_crawl.models import PageRef
from stratpoint_crawl.state import (
    load_last_successful_scrape,
    load_previous,
    resolve_last_successful_scrape,
    should_recrawl,
)


def _rec(url, lastmod="2025-01-01", status="ok"):
    return {"url": url, "lastmod": lastmod, "status": status, "content_hash": "sha256:1"}


def test_load_previous_returns_full_records_keyed_by_url(tmp_path):
    index = tmp_path / "index.jsonl"
    index.write_text(json.dumps(_rec("https://stratpoint.com/a/")) + "\n", encoding="utf-8")
    prev = load_previous(index)
    assert prev["https://stratpoint.com/a/"]["lastmod"] == "2025-01-01"
    assert prev["https://stratpoint.com/a/"]["status"] == "ok"


def test_load_previous_missing_file_returns_empty(tmp_path):
    assert load_previous(tmp_path / "nope.jsonl") == {}


def test_recrawl_new_page():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")
    assert should_recrawl(ref, None) is True


def test_skip_when_lastmod_unchanged():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")
    assert should_recrawl(ref, _rec(ref.url, "2025-01-01")) is False


def test_recrawl_when_lastmod_changed():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod="2025-06-01")
    assert should_recrawl(ref, _rec(ref.url, "2025-01-01")) is True


def test_recrawl_when_prev_lastmod_missing():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")
    assert should_recrawl(ref, _rec(ref.url, lastmod=None)) is True


def test_recrawl_when_sitemap_lastmod_missing():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod=None)
    assert should_recrawl(ref, _rec(ref.url, "2025-01-01")) is True


def test_recrawl_when_prev_failed():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")
    assert should_recrawl(ref, _rec(ref.url, "2025-01-01", status="failed")) is True


def test_force_always_recrawls():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")
    assert should_recrawl(ref, _rec(ref.url, "2025-01-01"), force=True) is True


# --- last successful scrape tracking ------------------------------------

def test_resolve_advances_when_a_page_succeeded():
    assert resolve_last_successful_scrape(
        succeeded=1, crawled_at="2026-06-26T00:00:00Z", previous="2026-01-01T00:00:00Z"
    ) == "2026-06-26T00:00:00Z"


def test_resolve_carries_previous_forward_when_nothing_succeeded():
    # An all-skip (or all-fail) run is not a new scrape.
    assert resolve_last_successful_scrape(
        succeeded=0, crawled_at="2026-06-26T00:00:00Z", previous="2026-01-01T00:00:00Z"
    ) == "2026-01-01T00:00:00Z"


def test_resolve_is_none_when_first_run_scrapes_nothing():
    assert resolve_last_successful_scrape(
        succeeded=0, crawled_at="2026-06-26T00:00:00Z", previous=None
    ) is None


def test_load_last_successful_reads_prior_report(tmp_path):
    report = tmp_path / "run_report.json"
    report.write_text(
        json.dumps({"last_successful_scrape": "2026-01-01T00:00:00Z"}), encoding="utf-8")
    assert load_last_successful_scrape(report) == "2026-01-01T00:00:00Z"


def test_load_last_successful_missing_file_returns_none(tmp_path):
    assert load_last_successful_scrape(tmp_path / "nope.json") is None


def test_load_last_successful_absent_field_returns_none(tmp_path):
    report = tmp_path / "run_report.json"
    report.write_text(json.dumps({"crawled": 0}), encoding="utf-8")
    assert load_last_successful_scrape(report) is None
