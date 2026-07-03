"""Load the crawled corpus, honoring the corpus invariant (plan §2).

A page is present when status is ``ok`` OR ``skipped``. We never import from
``stratpoint_crawl`` — this reads the on-disk contract only.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
PRESENT = {"ok", "skipped"}


@dataclass(frozen=True)
class Page:
    slug: str
    url: str
    title: str
    content_hash: str
    body: str  # markdown body, frontmatter stripped


def strip_frontmatter(text: str) -> str:
    """Drop a leading ``---`` YAML frontmatter block, if present."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            return text[nl + 1 :] if nl != -1 else ""
    return text


def load_manifest(index_path: Path) -> list[dict]:
    rows = []
    for line in Path(index_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return [r for r in rows if r.get("status") in PRESENT]


def load_pages(data_dir: str | Path = DEFAULT_DATA_DIR) -> list[Page]:
    data_dir = Path(data_dir)
    pages_dir = data_dir / "pages"
    pages: list[Page] = []
    for r in load_manifest(data_dir / "index.jsonl"):
        md = pages_dir / f"{r['slug']}.md"
        if not md.exists():  # skip-and-warn: a broken corpus shouldn't kill the whole run
            log.warning("skipping %s: page file missing (%s)", r["slug"], md)
            continue
        raw = md.read_text(encoding="utf-8")
        pages.append(
            Page(
                slug=r["slug"],
                url=r["url"],
                title=r["title"],
                content_hash=r["content_hash"],
                body=strip_frontmatter(raw),
            )
        )
    return pages
