import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .crawler import PlaywrightFetcher, crawl
from .sitemap import EmptySitemapError, discover_page_refs
from .state import load_last_successful_scrape, resolve_last_successful_scrape


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stratpoint-crawler",
        description="Crawl stratpoint.com into Markdown for RAG.",
    )
    p.add_argument("--out", default="./data", help="Output directory (default ./data)")
    p.add_argument("--limit", type=int, default=None, help="Crawl only first N URLs")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--delay-min", type=float, default=0.5)
    p.add_argument("--delay-max", type=float, default=1.5)
    p.add_argument("--save-html", action="store_true", help="Archive raw HTML")
    p.add_argument("--incremental", action="store_true",
                   help="Skip pages whose sitemap lastmod is unchanged since the last run")
    p.add_argument("--force", action="store_true",
                   help="Recrawl every page even with --incremental")
    p.add_argument("--verbose", action="store_true")
    return p


def settings_from_args(args: argparse.Namespace) -> Settings:
    return Settings(
        concurrency=args.concurrency,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        save_html=args.save_html,
    )


def _write_report(out_dir: Path, summary, elapsed: float, crawled_at: str) -> dict:
    results = summary.results
    ok = [r for r in results if r.status == "ok"]
    failed = [r for r in results if r.status == "failed"]
    thin = [r for r in ok if r.content and r.content.thin]
    report_path = out_dir / "run_report.json"
    last_successful = resolve_last_successful_scrape(
        succeeded=len(ok), crawled_at=crawled_at,
        previous=load_last_successful_scrape(report_path),
    )
    report = {
        "run_finished_at": crawled_at,
        "last_successful_scrape": last_successful,
        "crawled": len(results),
        "succeeded": len(ok),
        "skipped": summary.skipped,
        "removed": summary.removed,
        "failed": [{"url": r.url, "error": r.error} for r in failed],
        "thin_content": [r.url for r in thin],
        "elapsed_seconds": round(elapsed, 2),
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


async def _run(args: argparse.Namespace) -> int:
    settings = settings_from_args(args)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    crawled_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    refs = await discover_page_refs(settings)
    print(f"Discovered {len(refs)} URLs")

    start = time.monotonic()
    async with PlaywrightFetcher(settings) as fetcher:
        summary = await crawl(
            refs, settings=settings, fetcher=fetcher, out_dir=out_dir,
            crawled_at=crawled_at, limit=args.limit,
            incremental=args.incremental, force=args.force,
        )
    report = _write_report(out_dir, summary, time.monotonic() - start, crawled_at)
    print(f"Done: {report['succeeded']} ok, {report['skipped']} skipped, "
          f"{len(report['removed'])} removed, {len(report['failed'])} failed")
    print(f"Last successful scrape: {report['last_successful_scrape']}")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(_run(args))
    except EmptySitemapError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
