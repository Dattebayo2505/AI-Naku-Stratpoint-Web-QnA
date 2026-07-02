from stratpoint_crawl.cli import build_parser, settings_from_args


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.out == "./data"
    assert args.concurrency == 4
    assert args.incremental is False
    assert args.save_html is False
    assert args.force is False


def test_settings_from_args_overrides():
    args = build_parser().parse_args(
        ["--concurrency", "2", "--delay-min", "0.1", "--delay-max", "0.2", "--save-html"])
    s = settings_from_args(args)
    assert s.concurrency == 2
    assert (s.delay_min, s.delay_max) == (0.1, 0.2)
    assert s.save_html is True


def test_force_flag_parses():
    args = build_parser().parse_args(["--incremental", "--force"])
    assert args.incremental is True and args.force is True


def test_write_report_counts(tmp_path):
    from stratpoint_crawl.cli import _write_report
    from stratpoint_crawl.models import CrawlSummary, PageContent, PageResult

    thin_c = PageContent(url="https://x/t/", title="T", markdown="hi",
                         text_len=2, content_hash="sha256:t", thin=True)
    summary = CrawlSummary(
        results=[
            PageResult(url="https://x/ok/", slug="ok", status="ok",
                       content=PageContent(url="https://x/ok/", title="O", markdown="body",
                                           text_len=4, content_hash="sha256:o")),
            PageResult(url="https://x/t/", slug="t", status="ok", content=thin_c),
            PageResult(url="https://x/d/", slug="d", status="failed", error="boom"),
        ],
        skipped=5,
        removed=["https://x/gone/"],
    )
    report = _write_report(tmp_path, summary, 1.23, "2026-06-26T00:00:00Z")
    assert report["crawled"] == 3
    assert report["succeeded"] == 2
    assert report["skipped"] == 5
    assert report["removed"] == ["https://x/gone/"]
    assert len(report["failed"]) == 1
    assert report["thin_content"] == ["https://x/t/"]


def _ok_summary():
    from stratpoint_crawl.models import CrawlSummary, PageContent, PageResult

    return CrawlSummary(results=[
        PageResult(url="https://x/ok/", slug="ok", status="ok",
                   content=PageContent(url="https://x/ok/", title="O", markdown="body",
                                       text_len=4, content_hash="sha256:o")),
    ])


def _all_skip_summary():
    from stratpoint_crawl.models import CrawlSummary

    return CrawlSummary(results=[], skipped=3)


def test_write_report_stamps_run_time_and_last_successful_scrape(tmp_path):
    from stratpoint_crawl.cli import _write_report

    report = _write_report(tmp_path, _ok_summary(), 1.0, "2026-06-26T00:00:00Z")
    assert report["run_finished_at"] == "2026-06-26T00:00:00Z"
    assert report["last_successful_scrape"] == "2026-06-26T00:00:00Z"


def test_all_skip_run_carries_last_successful_forward(tmp_path):
    from stratpoint_crawl.cli import _write_report

    # A real scrape happened first...
    _write_report(tmp_path, _ok_summary(), 1.0, "2026-06-26T00:00:00Z")
    # ...then a later all-skip run records its own finish time but does NOT
    # advance last_successful_scrape.
    report = _write_report(tmp_path, _all_skip_summary(), 0.2, "2026-06-27T09:00:00Z")
    assert report["run_finished_at"] == "2026-06-27T09:00:00Z"
    assert report["last_successful_scrape"] == "2026-06-26T00:00:00Z"
