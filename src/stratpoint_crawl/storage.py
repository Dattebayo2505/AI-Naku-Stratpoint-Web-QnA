import json
import os
from pathlib import Path
from urllib.parse import urlparse

from .models import PageContent, PageResult


def slugify(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if not path:
        return "index"
    return path.replace("/", "__")


def _frontmatter(content: PageContent, crawled_at: str, lastmod: str | None) -> str:
    lines = [
        "---",
        f"url: {content.url}",
        f"title: {content.title}",
        f"crawled_at: {crawled_at}",
        f"lastmod: {lastmod or ''}",
        f"content_hash: {content.content_hash}",
        "---",
        "",
    ]
    return "\n".join(lines)


class Writer:
    def __init__(self, out_dir: Path, crawled_at: str, save_html: bool):
        self.out_dir = Path(out_dir)
        self.crawled_at = crawled_at
        self.save_html = save_html
        self.pages_dir = self.out_dir / "pages"
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        if save_html:
            (self.out_dir / "raw_html").mkdir(parents=True, exist_ok=True)

    def write_page(self, slug: str, content: PageContent,
                   raw_html: str | None = None, lastmod: str | None = None) -> None:
        body = _frontmatter(content, self.crawled_at, lastmod) + content.markdown + "\n"
        (self.pages_dir / f"{slug}.md").write_text(body, encoding="utf-8")
        if self.save_html and raw_html is not None:
            (self.out_dir / "raw_html" / f"{slug}.html").write_text(raw_html, encoding="utf-8")

    def write_index(self, results: list[PageResult],
                    carried_records: list[dict] | None = None) -> None:
        carried_records = carried_records or []
        rows = []
        for r in results:
            c = r.content
            rows.append(json.dumps({
                "url": r.url,
                "title": c.title if c else None,
                "slug": r.slug,
                "lastmod": r.lastmod,
                "crawled_at": self.crawled_at,
                "content_hash": c.content_hash if c else None,
                "text_len": c.text_len if c else 0,
                "status": r.status,
                "error": r.error,
            }))
        for rec in carried_records:
            carried = dict(rec)
            carried["status"] = "skipped"
            rows.append(json.dumps(carried))
        tmp = self.out_dir / "index.jsonl.tmp"
        tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
        os.replace(tmp, self.out_dir / "index.jsonl")
